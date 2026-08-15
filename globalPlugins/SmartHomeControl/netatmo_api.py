# -*- coding: utf-8 -*-
"""
Netatmo cloud API handler
Communicates with the Netatmo cloud API via OAuth2 (REST).

Supported devices:
  - weather station (NAMain, NAModule1-4)
  - thermostats (NATherm1, NRV, NAPlug)
  - Aircare / indoor air (NHC)

"""

import os
import base64
import html
import re
import threading
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
from logHandler import log

# requests is already bundled in the add-on's lib/ folder
import requests

import addonHandler
# Makes sure the gettext `_` builtin is available even when this module is
# imported directly (e.g. without __init__.py having called initTranslation()
# first). Throws None on error, so harmless.
try:
    addonHandler.initTranslation()
except Exception as e:
    log.debug(f"Ignored error during translation setup: {e}")
if "_" not in globals():  # fallback if initTranslation() fails
    # Without this fallback `_` stays undefined and the first `_()` call
    # raises a NameError mid-dialog instead of at import time.
    def _(s):
        return s

from .constants import (
    NETATMO_AUTH_URL, NETATMO_TOKEN_URL, NETATMO_API_BASE,
    NETATMO_REDIRECT_HOST, NETATMO_REDIRECT_PORT, netatmo_redirect_uri,
    NETATMO_DEFAULT_SCOPES, NETATMO_MODE_NAMES, HOMESDATA_CACHE_SECONDS,
    NETATMO_FULL_REFRESH_SECONDS,
)

# Local aliases for backward compatibility
DEFAULT_SCOPES = NETATMO_DEFAULT_SCOPES.split()


# ============================================================
# Netatmo device wrapper
# ============================================================
class NetatmoDevice:
    """Wrapper for a Netatmo device (weather, thermostat, etc.)"""

    def __init__(self, module_data, station_name=None, home_id=None):
        self.raw_data = module_data
        self._id = module_data.get('_id', module_data.get('id', ''))
        self.name = module_data.get(
            'module_name',
            module_data.get('station_name',
                            module_data.get('name', 'Unbekannt')))
        self.type = module_data.get('type', 'Unknown')
        self.home_id = home_id
        self.room_id = module_data.get('room_id', None)
        # Room name from homesdata (set in get_devices). Empty for devices
        # without a room (e.g. NAPlug, weather stations).
        self.room_name = ''
        self.station_name = station_name

        # Marker
        self.is_netatmo = True
        self.is_offline = not module_data.get('reachable', True)

        # Device type flags
        self.is_weather_station = self.type in ('NAMain',)
        self.is_outdoor_module = self.type in ('NAModule1',)
        self.is_wind_module = self.type in ('NAModule2',)
        self.is_rain_module = self.type in ('NAModule3',)
        self.is_indoor_module = self.type in ('NAModule4',)
        self.is_thermostat = self.type in ('NATherm1', 'NRV')
        self.is_valve = self.type in ('NRV',)
        self.is_relay = self.type in ('NAPlug',)
        self.is_aircare = self.type in ('NHC',)

        # Compatibility flags (for shared use with the Meross code)
        self.is_sensor = (self.is_weather_station or self.is_outdoor_module
                          or self.is_indoor_module or self.is_aircare)
        self.is_temperature_sensor = self.is_sensor or self.is_thermostat
        self.is_water_sensor = False
        self.is_plug = False
        self.is_light = False
        self.is_diffuser = False
        self.is_hub = False
        self.is_multi_channel = False
        self.has_power_meter = False
        self._is_on = False
        self._channels = []

        # Measurement data
        self._dashboard_data = module_data.get('dashboard_data', {})

        # Thermostat-specific
        self._therm_setpoint = None
        self._therm_measured = None
        self._therm_setpoint_mode = None  # 'schedule', 'manual', 'away', 'hg', 'max'
        self._therm_setpoint_end_time = None  # Unix timestamp when the manual setpoint ends
        self._boiler_status = None  # True/False
        self._schedule_zone_name = None  # active schedule zone name (e.g. 'Komfort', 'Eco', 'Nacht')
        self._therm_setpoint_default_duration = None  # default duration of manual setpoints in minutes
        self._anticipating = None  # True/False - pre-heating active (heats early to reach the target temp in time)
        self._open_window = None  # True/False - open window detected (heating paused)
        self._next_schedule_change = None  # dict: {'time': unix_ts, 'zone_name': str, 'temp': float} or None
        self._active_schedule_name = None  # name of the active heating schedule (cached)

        # Derive the category type (for tree assignment)
        self.device_type = self._derive_device_type()

    def _derive_device_type(self):
        """Derives the device type string (for categorization)"""
        if self.type in ('NATherm1', 'NRV'):
            return 'thermostat'
        elif self.type == 'NAPlug':
            return 'gateway'
        elif self.type == 'NHC':
            return 'aircare'
        elif self.type in ('NAMain', 'NAModule1', 'NAModule2', 'NAModule3', 'NAModule4'):
            return 'weather'
        return 'unknown'

    # ------ Identification ------
    @property
    def uuid(self):
        return f"netatmo_{self._id}"

    @property
    def unique_id(self):
        """Unique ID - identical to uuid for Netatmo devices."""
        return self.uuid

    @property
    def is_on(self):
        return self._is_on

    def get_channels(self):
        return []

    # ------ Sensor data ------
    def get_temperature(self):
        if 'Temperature' in self._dashboard_data:
            return round(self._dashboard_data['Temperature'], 1)
        if self._therm_measured is not None:
            return round(self._therm_measured, 1)
        return None

    def get_humidity(self):
        val = self._dashboard_data.get('Humidity')
        return round(val, 1) if val is not None else None

    def get_co2(self):
        return self._dashboard_data.get('CO2')

    def get_noise(self):
        return self._dashboard_data.get('Noise')

    def get_pressure(self):
        val = self._dashboard_data.get('Pressure')
        return round(val, 1) if val is not None else None

    def get_rain(self):
        return self._dashboard_data.get('Rain')

    def get_rain_1h(self):
        return self._dashboard_data.get('sum_rain_1')

    def get_rain_24h(self):
        return self._dashboard_data.get('sum_rain_24')

    def get_wind_strength(self):
        return self._dashboard_data.get('WindStrength')

    def get_wind_angle(self):
        return self._dashboard_data.get('WindAngle')

    def get_gust_strength(self):
        return self._dashboard_data.get('GustStrength')

    def get_gust_angle(self):
        return self._dashboard_data.get('GustAngle')

    # ------ Thermostat ------
    def get_setpoint_temp(self):
        return self._therm_setpoint

    def get_setpoint_end_time(self):
        """Returns the Unix timestamp when the manual setpoint ends (0 or None = permanent)"""
        return self._therm_setpoint_end_time

    def get_setpoint_mode(self):
        """Returns the current thermostat setpoint mode"""
        return self._therm_setpoint_mode

    def get_schedule_zone_name(self):
        """Returns the name of the current schedule zone (e.g. 'Komfort', 'Eco', 'Nacht')"""
        return self._schedule_zone_name

    def get_boiler_status(self):
        """Returns the boiler status (True=heating, False=off, None=unknown)"""
        return self._boiler_status

    def is_anticipating(self):
        """Returns whether pre-heating is active (True/False/None=unknown).

        Anticipation starts the heating early so the target temperature is
        already reached at the scheduled time.
        """
        return self._anticipating

    def is_open_window(self):
        """Returns whether an open window was detected (True/False/None=unknown).

        With an open window the heating is paused automatically.
        """
        return self._open_window

    def get_next_schedule_change(self):
        """Returns the next schedule change.

        Returns:
            dict with 'time' (Unix timestamp), 'zone_name' (str), 'temp' (float) or None
        """
        return self._next_schedule_change

    # ------ Diagnostics ------
    def get_battery_percent(self):
        # The weather station provides a real percentage
        pct = self.raw_data.get('battery_percent')
        if pct is not None:
            return pct
        # Energy devices (NATherm1, NRV) provide battery_state as a text level
        # instead - convert to an approximate percentage.
        state_map = {'full': 100, 'high': 75, 'medium': 50, 'low': 25, 'very_low': 10}
        return state_map.get(self.raw_data.get('battery_state'))

    def get_wifi_status(self):
        return self.raw_data.get('wifi_status')

    def get_rf_status(self):
        return self.raw_data.get('rf_status')

    # ------ Display ------
    def get_type_display(self):
        # Translators: Device type displays for Netatmo modules.
        type_map = {
            'NAMain': _("Weather station (indoor)"),
            'NAModule1': _("Outdoor module"),
            'NAModule2': _("Wind gauge"),
            'NAModule3': _("Rain gauge"),
            'NAModule4': _("Additional indoor module"),
            'NATherm1': _("Thermostat"),
            'NRV': _("Radiator valve"),
            'NAPlug': _("Thermostat relay"),
            'NHC': _("Indoor air quality monitor"),
        }
        return type_map.get(self.type, self.type)

    def get_status_summary(self):
        """Accessible summary of all measurements"""
        parts = []

        temp = self.get_temperature()
        if temp is not None:
            parts.append(f"{temp}°C")

        humidity = self.get_humidity()
        if humidity is not None:
            # Translators: Status announcement: relative humidity.
            parts.append(_("{value}% humidity").format(value=humidity))

        co2 = self.get_co2()
        if co2 is not None:
            parts.append(f"{co2} ppm CO2")

        noise = self.get_noise()
        if noise is not None:
            parts.append(f"{noise} dB")

        pressure = self.get_pressure()
        if pressure is not None:
            parts.append(f"{pressure} mbar")

        rain = self.get_rain()
        if rain is not None:
            # Translators: Status announcement: rain amount.
            parts.append(_("{value} mm rain").format(value=rain))

        wind = self.get_wind_strength()
        if wind is not None:
            # Translators: Status announcement: wind speed.
            parts.append(_("{value} km/h wind").format(value=wind))

        gust = self.get_gust_strength()
        if gust is not None:
            # Translators: Status announcement: gust speed.
            parts.append(_("{value} km/h gusts").format(value=gust))

        setpoint = self.get_setpoint_temp()
        if setpoint is not None:
            # Translators: Status announcement: target temperature of a
            # thermostat.
            parts.append(_("Target: {value:.1f}°C").format(value=setpoint))
        # Show the heating mode (thermostats only)
        mode = self.get_setpoint_mode()
        if mode:
            mode_text = NETATMO_MODE_NAMES.get(mode, mode)
            # For schedule mode also show the active zone name (e.g. comfort,
            # eco, night)
            if mode == 'schedule' and self._schedule_zone_name:
                mode_text += f" ({self._schedule_zone_name})"
            # For manual mode show the end time
            if mode == 'manual' and self._therm_setpoint_end_time:
                try:
                    end_local = time.localtime(self._therm_setpoint_end_time)
                    end_str = time.strftime("%H:%M", end_local)
                    # Translators: Suffix after the heating mode: until when
                    # the manual temperature applies (HH:MM).
                    mode_text += _(" (until {time})").format(time=end_str)
                except Exception as e:
                    log.debug(f"Ignored error in get_status_summary: {e}")
            # Translators: Status announcement: current heating mode.
            parts.append(_("Mode: {mode}").format(mode=mode_text))
        elif setpoint is not None:
            # If a setpoint exists but no mode, it was probably set manually
            # Translators: Status announcement: heating mode manual (no mode
            # reported).
            parts.append(_("Mode: manual"))

        # Boiler status
        boiler = self.get_boiler_status()
        if boiler is not None:
            # Translators: Status announcement: boiler active/off.
            parts.append(_("Heating: active") if boiler else _("Heating: off"))

        # Pre-heating (anticipation)
        anticipating = self.is_anticipating()
        if anticipating:
            # Translators: Status announcement: thermostat is pre-heating.
            parts.append(_("Pre-heating active"))

        # Open window
        open_window = self.is_open_window()
        if open_window:
            # Translators: Status announcement: open window detected.
            parts.append(_("Window open"))

        # Next schedule change
        next_change = self.get_next_schedule_change()
        if next_change:
            try:
                change_time = time.localtime(next_change['time'])
                change_str = time.strftime("%H:%M", change_time)
                # temp can be present with value None (zone without a
                # matching room) - {temp:.1f} would raise TypeError and
                # silently swallow the announcement.
                temp = next_change.get('temp')
                if temp is not None:
                    # Translators: Status announcement: next schedule change
                    # (zone, temperature, time).
                    nc_text = _("Next change: {zone} ({temp:.1f}°C) at {time}").format(
                        zone=next_change.get('zone_name', ''),
                        temp=temp,
                        time=change_str)
                else:
                    # Translators: Status announcement: next schedule change
                    # without a known temperature (zone, time).
                    nc_text = _("Next change: {zone} at {time}").format(
                        zone=next_change.get('zone_name', ''),
                        time=change_str)
                parts.append(nc_text)
            except Exception as e:
                log.debug(f"Ignored error in get_status_summary: {e}")

        battery = self.get_battery_percent()
        if battery is not None:
            # Translators: Status announcement: battery level in percent.
            parts.append(_("Battery: {value}%").format(value=battery))

        if not parts:
            # Translators: Status announcement without data or device offline.
            return _("offline") if self.is_offline else _("no data")

        return ", ".join(parts)

    def _update_status(self):
        """Compatibility stub (Netatmo needs no local status update)"""

    @staticmethod
    def _translate_zone_name(name):
        """Translates default Netatmo zone names.

        The Netatmo API returns English/French default names such as
        'Comfort', 'Confort', 'Comfort+', 'Night', 'Eco' etc.
        Custom zone names are returned unchanged.
        """
        if not name:
            return name
        # Translators: Translations of Netatmo's default zone names.
        translations = {
            'Comfort': _("Comfort"),
            'Confort': _("Comfort"),
            'Comfort+': _("Comfort+"),
            'Comfort +': _("Comfort+"),
            'Confort+': _("Comfort+"),
            'Confort +': _("Comfort+"),
            'Night': _("Night"),
            'Eco': _("Eco"),
            'Away': _("Away"),
            'Frost Guard': _("Frost guard"),
        }
        return translations.get(name, name)


# ============================================================
# OAuth2 state container (instance-based instead of a class variable)
# ============================================================
class _OAuthState:
    """Instance-based container for OAuth2 callback data.

    Each OAuth flow gets its own state container to avoid race
    conditions with parallel calls.
    """
    def __init__(self):
        self.auth_code = None
        self.auth_state = None
        self.auth_error = None


# ============================================================
# OAuth2 callback handler
# ============================================================
class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Local HTTP handler for the OAuth2 redirect callback.

    State is accessed via self.server.oauth_state (instance-based).
    """

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if 'code' in params:
            self.server.oauth_state.auth_code = params['code'][0]
            self.server.oauth_state.auth_state = params.get('state', [None])[0]
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            # HTML texts: intentionally English-only and not passed through _():
            # they are only shown in the browser during the one-time OAuth flow,
            # and the browser does not necessarily use the NVDA language.
            # NVDA announces the result through its own UI in parallel.
            self.wfile.write(
                '<html><body style="font-family:sans-serif;text-align:center;padding:40px">'
                '<h1>Authorization successful</h1>'
                '<p>You can close this window now and return to NVDA.</p>'
                '</body></html>'.encode('utf-8')
            )
        elif 'error' in params:
            err_raw = params['error'][0]
            self.server.oauth_state.auth_error = err_raw
            self.send_response(400)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            # html.escape() against reflected XSS - even though the browser is
            # local, a crafted redirect link could otherwise inject HTML/script.
            err_safe = html.escape(err_raw, quote=True)
            self.wfile.write(
                (
                    '<html><body style="font-family:sans-serif;text-align:center;padding:40px">'
                    '<h1>Error</h1><p>'
                    + err_safe
                    + '</p></body></html>'
                ).encode('utf-8')
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress HTTP server logs (they would otherwise appear in the NVDA log)"""


# ============================================================
# Netatmo API class
# ============================================================
class NetatmoAPI:
    """Netatmo cloud API handler with the OAuth2 authorization code flow"""

    def __init__(self, client_id, client_secret, redirect_port=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = None
        self.refresh_token = None
        self.token_expiry = 0
        self._devices = []
        # Notified on every token renewal so the caller can persist the
        # rotated tokens (see set_token_update_callback).
        self._token_update_callback = None

        # Lock for the token refresh: Netatmo ROTATES refresh tokens (each
        # refresh issues a new one and invalidates the old). Without the lock
        # the scheduler thread and a UI action could refresh in parallel; the
        # second refresh then fails with the already invalidated token and at
        # worst discards the whole login.
        # (Same idea as _reauth_lock in VeSyncAPI/CozytouchAPI.)
        self._token_refresh_lock = threading.Lock()

        # Redirect target of the OAuth2 flow. The port is configurable (must
        # match the URI registered at dev.netatmo.com); host and path are
        # fixed. redirect_uri is derived centrally from the port so the local
        # callback server, the auth request and the token exchange are
        # guaranteed to be identical.
        self.redirect_host = NETATMO_REDIRECT_HOST
        try:
            self.redirect_port = int(redirect_port) if redirect_port else NETATMO_REDIRECT_PORT
        except (TypeError, ValueError):
            self.redirect_port = NETATMO_REDIRECT_PORT
        self.redirect_uri = netatmo_redirect_uri(self.redirect_port)

        # Short-lived cache for /homesdata (topology + heating schedules).
        # This data practically never changes - schedules are created once.
        # Without the cache, expanding a thermostat in the device menu
        # triggered a full homesdata request just to show the name of the
        # active schedule. The background poll fetches it once for all
        # thermostats anyway (see update_device_status); with the cache the
        # dialog benefits from that.
        self._homesdata_cache = {}       # params key -> (timestamp, data)
        self._homesdata_cache_lock = threading.Lock()

        # Timestamp of the last FULL status pass (get_devices with
        # getstationsdata + homesdata). In between, update_device_status only
        # polls /homestatus - see there.
        self._last_full_status_refresh = 0.0

        # Network error deduplication: prevents ERROR log spam during network
        # outages
        self._last_network_error_time = 0  # timestamp of the last ERROR log
        self._network_error_count = 0  # number of consecutive network errors
        self._NETWORK_ERROR_LOG_INTERVAL = 300  # only one ERROR log every 5 min during a persistent outage

    def _is_network_error(self, exception):
        """Checks whether an error is transient (DNS, connection, timeout, HTTP 5xx)"""
        error_msg = str(exception).lower()
        network_indicators = [
            'failed to resolve', 'getaddrinfo failed', 'name resolution',
            'connection refused', 'connection reset', 'connection aborted',
            'no route to host', 'network is unreachable', 'nicht erreichbar',
            'max retries exceeded', 'connectionerror', 'timeout',
            'winerror 10065', 'winerror 10060', 'winerror 10061',
        ]
        if any(indicator in error_msg for indicator in network_indicators):
            return True
        # HTTP 5xx (bad gateway, service unavailable, etc.) are transient
        # server/CDN errors. Detects both "(502)" and "(HTTP 502)"
        return bool(re.search(r'\((?:http\s)?5\d{2}\)', error_msg))

    def _log_network_error(self, context, exception):
        """Logs network errors with deduplication (ERROR only the first time, then DEBUG)"""
        now = time.time()
        if self._is_network_error(exception):
            self._network_error_count += 1
            # First occurrence or after a long pause: log as ERROR
            if (now - self._last_network_error_time) > self._NETWORK_ERROR_LOG_INTERVAL:
                log.error(f"{context}: {exception}")
                self._last_network_error_time = now
            else:
                # Repeated error: only DEBUG
                log.debug(f"{context} (retry #{self._network_error_count}): {exception}")
        else:
            # Not a network error: always log as ERROR
            log.error(f"{context}: {exception}")

    @staticmethod
    def _transient_hint(exception):
        """Plain-text hint for temporary API states, otherwise None.

        Classified by HTTP status because that is unambiguous. Netatmo's own
        error numbers (``code`` in the JSON) are nowhere fully documented and
        have changed before - their ``message`` is therefore passed through
        unchanged instead of being mapped against a homemade table.
        """
        text = str(exception)
        match = re.search(r'\((?:HTTP\s)?(\d{3})\)', text)
        if not match:
            return None
        status = int(match.group(1))
        if status == 429:
            # Translators: Explanation for HTTP 429 from the Netatmo API.
            return _("Netatmo request limit reached – the add-on will try "
                     "again later")
        if 500 <= status < 600:
            # Translators: Explanation for HTTP 5xx from the Netatmo API.
            # Shown/logged when Netatmo's own servers are unavailable.
            return _("Netatmo servers temporarily unavailable – not a problem "
                     "with the add-on or your settings; the next update will "
                     "try again")
        return None

    def _log_api_exception(self, context, exception):
        """Logs an API error at the severity it deserves.

        An HTTP 503 from Netatmo is not a fault of this add-on but a state on
        their servers. Logged as ERROR it sends whoever reads the NVDA log
        hunting for a bug that does not exist - hence WARNING plus plain text
        on what the state means. Anything unexpected stays ERROR.
        """
        hint = self._transient_hint(exception)
        if hint:
            self._network_error_count += 1
            now = time.time()
            if (now - self._last_network_error_time) > self._NETWORK_ERROR_LOG_INTERVAL:
                log.warning(f"{context}: {hint}. Response: {exception}")
                self._last_network_error_time = now
            else:
                log.debug(f"{context} (retry "
                          f"#{self._network_error_count}): {exception}")
            return
        if self._is_network_error(exception):
            self._log_network_error(context, exception)
            return
        log.error(f"{context}: {exception}")

    def _reset_network_error_state(self):
        """Resets the network error counters (after a successful API call)"""
        if self._network_error_count > 0:
            log.info(f"Netatmo: network available again (after {self._network_error_count} failed attempts)")
        self._network_error_count = 0

    # ----------------------------------------------------------
    # Token management
    # ----------------------------------------------------------
    def is_authenticated(self):
        """Checks whether tokens are present (not whether they are valid)"""
        return bool(self.access_token and self.refresh_token)

    def set_tokens(self, access_token, refresh_token, expiry=0):
        """Sets tokens directly (e.g. from the saved config)"""
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_expiry = expiry

    def get_tokens(self):
        """Returns the current tokens (for saving)"""
        return {
            'access_token': self.access_token or '',
            'refresh_token': self.refresh_token or '',
            'token_expiry': self.token_expiry,
        }

    def set_token_update_callback(self, callback):
        """Registers a callback for every token renewal.

        Netatmo ROTATES refresh tokens: each refresh issues a new one and
        invalidates the old. Without this callback the renewed tokens only
        lived inside this object - the caller kept saving the ones from the
        login, so after a restart an already invalidated refresh token was
        restored and the authorization had to be repeated.

        ``callback(tokens_dict)`` runs on the thread that triggered the
        refresh (usually the scheduler), so it must not touch the UI directly.
        """
        self._token_update_callback = callback

    def _notify_token_update(self):
        """Passes freshly issued tokens to the callback (never raises)."""
        cb = self._token_update_callback
        if not cb:
            return
        try:
            cb(self.get_tokens())
        except Exception as e:
            log.debug(f"Ignored error in the token update callback: {e}")

    # ----------------------------------------------------------
    # OAuth2 flow
    # ----------------------------------------------------------
    def start_oauth_flow(self, scopes=None, timeout=120):
        """
        Starts the OAuth2 authorization code flow.

        1. Local HTTP server on self.redirect_port
        2. Browser opens the Netatmo authorization page
        3. User authorizes -> redirect to self.redirect_uri
        4. The code is exchanged for tokens

        Important: the redirect URI MUST match the URI registered at
        dev.netatmo.com exactly (host + port + path).

        Args:
            scopes: list of scopes (default: read_station, read/write_thermostat)
            timeout: max. wait time for the browser callback in seconds

        Returns:
            True on success

        Raises:
            RuntimeError: on error or timeout
        """
        if scopes is None:
            scopes = DEFAULT_SCOPES

        state = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b'=').decode('ascii')

        # Start the local HTTP server (host/port of the instance - must match
        # the redirect URI registered at dev.netatmo.com).
        try:
            server = HTTPServer(
                (self.redirect_host, self.redirect_port),
                _OAuthCallbackHandler,
            )
        except OSError as e:
            # Translators: Error when starting the local OAuth callback server
            # because the port is in use by another program. {port} = port
            # number.
            raise RuntimeError(_(
                "Port {port} is in use. Choose a different Netatmo port in "
                "the settings (and register it at dev.netatmo.com as well), "
                "or close the program blocking it. ({error})"
            ).format(port=self.redirect_port, error=e))
        server.timeout = 5  # short timeout per handle_request() pass

        # Instance-based OAuth state (safe with parallel calls)
        server.oauth_state = _OAuthState()

        # Assemble the auth URL
        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'scope': ' '.join(scopes),
            'state': state,
            'response_type': 'code',
        }
        auth_url = f"{NETATMO_AUTH_URL}?{urlencode(params)}"

        log.info("Netatmo OAuth: opening the browser for authorisation...")
        webbrowser.open(auth_url)

        # Wait for the OAuth callback (a loop instead of a single
        # handle_request so e.g. favicon.ico requests do not block the
        # callback)
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                server.handle_request()
                # Check whether a code or an error was received
                if server.oauth_state.auth_code or server.oauth_state.auth_error:
                    break
        finally:
            server.server_close()

        # Check the result
        if server.oauth_state.auth_error:
            # Translators: Error message when the user declines the Netatmo
            # authorization in the browser or Netatmo returns an error.
            raise RuntimeError(_(
                "Netatmo authorization denied: {error}"
            ).format(error=server.oauth_state.auth_error))

        if not server.oauth_state.auth_code:
            # Translators: Error message when the browser callback does not
            # return within the timeout (e.g. browser window closed by the
            # user).
            raise RuntimeError(_(
                "No response received from Netatmo. Timeout or browser window "
                "closed."))

        if server.oauth_state.auth_state != state:
            # Translators: Security error message when the OAuth2 CSRF state
            # parameter does not match. Should only happen on attack attempts.
            raise RuntimeError(_(
                "Security error: state parameter does not match (CSRF "
                "protection)"))

        # Exchange the code for tokens
        self._exchange_code_for_token(server.oauth_state.auth_code, scopes)
        log.info("Netatmo OAuth: authenticated successfully")
        return True

    def _exchange_code_for_token(self, code, scopes=None):
        """Exchanges the authorization code for access/refresh tokens"""
        data = {
            'grant_type': 'authorization_code',
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'code': code,
            'redirect_uri': self.redirect_uri,
        }
        if scopes:
            data['scope'] = ' '.join(scopes)

        resp = requests.post(NETATMO_TOKEN_URL, data=data, timeout=30)

        if resp.status_code != 200:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text[:200]
            raise RuntimeError(f"Token request failed ({resp.status_code}): {detail}")

        token_data = resp.json()
        self.access_token = token_data['access_token']
        self.refresh_token = token_data['refresh_token']
        self.token_expiry = time.time() + token_data.get('expires_in', 10800)

        log.info(f"Netatmo: token received, valid until {time.ctime(self.token_expiry)}")
        self._notify_token_update()

    def refresh_access_token(self):
        """Renews the access token with the refresh token.

        Thread-safe: Only ONE thread refreshes at a time (Netatmo rotates
        refresh tokens – parallel refreshes would invalidate each other).
        Threads that were waiting on the lock while another thread already
        renewed the token detect that via the changed access token and
        return without a second refresh. The 403-forced refresh path also
        works: there the waiting thread has seen the same (rejected) token
        and correctly refreshes again if the first refresh did not happen.

        Distinguishes between:
          - 4xx (refresh token really invalid) -> discard tokens, user must reconnect
          - 5xx / network error (transient) -> keep tokens, retry later
        """
        stale_token = self.access_token
        with self._token_refresh_lock:
            if self.access_token != stale_token:
                # Another thread refreshed while we waited on the lock.
                log.debug("Netatmo: token was already refreshed in parallel - no second refresh")
                return
            self._refresh_access_token_locked()

    def _refresh_access_token_locked(self):
        """Actual refresh logic – call only while holding _token_refresh_lock."""
        if not self.refresh_token:
            # Translators: Error message when no OAuth refresh token is stored.
            raise RuntimeError(_("No refresh token available – please "
                                 "authorize again"))

        data = {
            'grant_type': 'refresh_token',
            'refresh_token': self.refresh_token,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
        }

        resp = self._http_with_retry('POST', NETATMO_TOKEN_URL, data=data)

        if resp.status_code != 200:
            # 4xx = refresh token really invalid -> discard the tokens
            # 5xx = transient server error -> keep the tokens, retry later
            if 400 <= resp.status_code < 500:
                detail = self._format_api_error(resp)
                log.error(f"Netatmo login expired ({detail}). Tokens are discarded.")
                self.access_token = None
                self.refresh_token = None
                self.token_expiry = 0
                self._notify_token_update()
                # Translators: Error message: Netatmo authorization expired.
                raise RuntimeError(_(
                    "Netatmo login is no longer valid. Please reconnect in "
                    "the settings."))
            else:
                # Transient - the tokens stay valid
                # Translators: Error message: Netatmo server unreachable.
                msg = _("Netatmo server temporarily unreachable (HTTP {code})").format(
                    code=resp.status_code)
                log.warning(f"Netatmo token refresh deferred: {msg}")
                raise RuntimeError(msg)

        token_data = resp.json()
        self.access_token = token_data['access_token']
        self.refresh_token = token_data['refresh_token']
        self.token_expiry = time.time() + token_data.get('expires_in', 10800)

        log.debug(f"Netatmo: token refreshed, valid until {time.ctime(self.token_expiry)}")
        self._notify_token_update()

    # ----------------------------------------------------------
    # Internal HTTP methods
    # ----------------------------------------------------------
    def _ensure_valid_token(self):
        """Makes sure a valid token is present"""
        if not self.access_token or not self.refresh_token:
            # Translators: Error message: no valid Netatmo access configured.
            raise RuntimeError(_("Not authorized – please connect to Netatmo "
                                 "in the settings"))

        # Auto-refresh when the token expires in < 60s
        if time.time() >= self.token_expiry - 60:
            log.debug("Netatmo: token is expiring, refreshing...")
            self.refresh_access_token()

    def _http_with_retry(self, method, url, **kwargs):
        """HTTP request with auto-retry on 5xx and 429.

        Retry strategy:
          - 5xx: up to 2 retries with exponential backoff (2s, 5s).
          - 429 (rate limit): respects the ``Retry-After`` header up to 30s;
            otherwise a fixed 10s pause.
          - Other status codes/network errors: return immediately.
        """
        kwargs.setdefault('timeout', 30)
        func = requests.get if method == 'GET' else requests.post

        backoff_5xx = [2, 5]  # wait times in seconds before each retry
        attempt = 0
        max_5xx_retries = len(backoff_5xx)
        had_429 = False

        while True:
            resp = func(url, **kwargs)

            # 429: rate limit - respect Retry-After, but only once
            if resp.status_code == 429 and not had_429:
                had_429 = True
                retry_after = resp.headers.get('Retry-After', '')
                wait = 10
                try:
                    wait = min(30, max(1, int(retry_after)))
                except (TypeError, ValueError):
                    pass
                log.warning(f"Netatmo: HTTP 429 (rate limit), waiting {wait}s...")
                time.sleep(wait)
                continue

            # 5xx: up to 2 retries
            if 500 <= resp.status_code < 600 and attempt < max_5xx_retries:
                wait = backoff_5xx[attempt]
                attempt += 1
                log.debug(f"Netatmo: HTTP {resp.status_code}, Retry {attempt}/{max_5xx_retries} in {wait}s...")
                time.sleep(wait)
                continue

            return resp

    @staticmethod
    def _format_api_error(resp):
        """Produces a compact error message. Detects HTML error pages so the
        HTML body is not spammed into the log.
        """
        code = resp.status_code
        body = (resp.text or '').lstrip()
        is_html = body[:9].lower().startswith('<!doctype') or body[:5].lower().startswith('<html')
        if is_html:
            if 500 <= code < 600:
                # Translators: Short hint for HTTP 5xx errors of the Netatmo
                # API.
                hint = _("Netatmo server temporarily unreachable")
            else:
                # Translators: Short hint for an unexpected response of the
                # Netatmo API.
                hint = _("Unexpected response from the Netatmo server")
            return f"Netatmo API ({code}): {hint}"
        # JSON / plain text: only show the first 200 characters
        return f"Netatmo API ({code}): {body[:200]}"

    def _api_get(self, endpoint, params=None):
        """Authenticated GET request"""
        self._ensure_valid_token()

        url = f"{NETATMO_API_BASE}/{endpoint}"
        headers = {"Authorization": f"Bearer {self.access_token}"}

        resp = self._http_with_retry('GET', url, headers=headers, params=params)

        # 403 is Netatmo's usual "token invalid"; some endpoints answer 401
        # instead - both get exactly one refresh attempt.
        if resp.status_code in (401, 403):
            # Token possibly invalid - try one refresh
            self.refresh_access_token()
            headers = {"Authorization": f"Bearer {self.access_token}"}
            resp = self._http_with_retry('GET', url, headers=headers, params=params)

        if resp.status_code != 200:
            raise RuntimeError(self._format_api_error(resp))

        return resp.json()

    def _api_post(self, endpoint, data=None):
        """Authenticated POST request"""
        self._ensure_valid_token()

        url = f"{NETATMO_API_BASE}/{endpoint}"
        headers = {"Authorization": f"Bearer {self.access_token}"}

        resp = self._http_with_retry('POST', url, headers=headers, data=data)

        # 401/403: see _api_get
        if resp.status_code in (401, 403):
            self.refresh_access_token()
            headers = {"Authorization": f"Bearer {self.access_token}"}
            resp = self._http_with_retry('POST', url, headers=headers, data=data)

        if resp.status_code != 200:
            raise RuntimeError(self._format_api_error(resp))

        return resp.json()

    # ----------------------------------------------------------
    # Public API methods
    # ----------------------------------------------------------
    def get_stations_data(self):
        """Fetches weather station data (/getstationsdata)"""
        return self._api_get("getstationsdata")

    def get_home_status(self, home_id=None):
        """Fetches the current home status (/homestatus)"""
        params = {}
        if home_id:
            params['home_id'] = home_id
        return self._api_get("homestatus", params=params)

    def get_homes_data(self, gateway_types=None, max_age=None,
                       cached_only=False):
        """Fetches homes and topology (/homesdata), cached.

        Args:
            gateway_types: e.g. 'NAPlug' to get energy-specific data incl. schedules
            max_age: maximum age of the cached value in seconds.
                None = ``HOMESDATA_CACHE_SECONDS``. 0 forces a fresh request.
            cached_only: True = NEVER issue a network request. Returns the
                cached value or None. For callers on the UI thread that must
                not block.

        Returns:
            dict of the API response, or None if ``cached_only`` is set and
            nothing (still) sits in the cache.
        """
        key = gateway_types or ''
        limit = HOMESDATA_CACHE_SECONDS if max_age is None else max_age

        with self._homesdata_cache_lock:
            entry = self._homesdata_cache.get(key)
        if entry is not None:
            age = time.time() - entry[0]
            if age < limit:
                log.debug(f"Netatmo: homesdata from the cache ({age:.0f}s old)")
                return entry[1]
            if cached_only:
                # Slightly stale beats blocking the UI thread: the caller
                # shows the old value and has it refreshed in the
                # background.
                log.debug(f"Netatmo: homesdata stale ({age:.0f}s), "
                          f"but cached_only - returning the old value")
                return entry[1]
        elif cached_only:
            return None

        params = {}
        if gateway_types:
            params['gateway_types'] = gateway_types
        data = self._api_get("homesdata", params=params if params else None)
        with self._homesdata_cache_lock:
            self._homesdata_cache[key] = (time.time(), data)
        return data

    def invalidate_homesdata_cache(self):
        """Drops the homesdata cache.

        To be called after switching the heating schedule, otherwise the menu
        keeps showing the old one for up to ``HOMESDATA_CACHE_SECONDS``.
        """
        with self._homesdata_cache_lock:
            self._homesdata_cache.clear()

    def set_room_thermpoint(self, home_id, room_id, mode="manual", temp=None, endtime=None):
        """Sets the temperature of a room (/setroomthermpoint)"""
        data = {
            'home_id': home_id,
            'room_id': room_id,
            'mode': mode,
        }
        if temp is not None:
            data['temp'] = temp
        if endtime is not None:
            data['endtime'] = endtime

        return self._api_post("setroomthermpoint", data=data)

    def set_therm_mode(self, home_id, mode, endtime=None):
        """Sets the heating mode of a home (/setthermmode) - schedule/away/hg"""
        data = {
            'home_id': home_id,
            'mode': mode,
        }
        if endtime is not None:
            data['endtime'] = endtime

        return self._api_post("setthermmode", data=data)

    def switch_home_schedule(self, home_id, schedule_id):
        """Switches the active heating schedule (/switchhomeschedule)"""
        data = {
            'home_id': home_id,
            'schedule_id': schedule_id,
        }
        return self._api_post("switchhomeschedule", data=data)

    def get_schedules(self, home_id, cached_only=False):
        """Returns the available heating schedules for a home.

        Args:
            home_id: the home ID
            cached_only: True = issue no network request. For callers on the
                UI thread. With nothing in the cache an empty list is
                returned.

        Returns:
            list[dict]: list of {id, name, selected} dicts
        """
        try:
            # First try with gateway_types=NAPlug (provides therm_schedules)
            homes_data = self.get_homes_data(gateway_types='NAPlug',
                                             cached_only=cached_only)
            if homes_data is None:
                return []
            body = homes_data.get('body', {})
            for home in body.get('homes', []):
                if home.get('id') == home_id:
                    # The Netatmo API may use 'therm_schedules' or 'schedules'
                    raw_schedules = home.get('therm_schedules', home.get('schedules', []))
                    if not raw_schedules:
                        return []
                    schedules = []
                    for sched in raw_schedules:
                        schedules.append({
                            'id': sched.get('id', ''),
                            'name': sched.get('name', 'Unbenannt'),
                            'selected': sched.get('selected', False),
                        })
                    return schedules
            return []
        except Exception as e:
            # Temporary server states (503/5xx) are NOT a fault of this
            # add-on - logging them as ERROR sends whoever reads the log
            # hunting for a bug that does not exist.
            self._log_api_exception("Netatmo: heating schedules not retrievable", e)
            return []

    # ----------------------------------------------------------
    # Schedule zone resolution
    # ----------------------------------------------------------
    def _get_current_schedule_zone_name(self, home_id, room_id):
        """
        Determines the current schedule zone name (e.g. comfort, eco, night)
        based on the active heating schedule and the current time.

        Args:
            home_id: the home ID
            room_id: the room ID of the thermostat

        Returns:
            str or None: the zone name, or None on error
        """
        result = self._resolve_schedule_info(home_id, room_id)
        return result.get('current_zone_name') if result else None

    def _get_next_schedule_change(self, home_id, room_id):
        """
        Computes the next schedule change for a room.

        Returns:
            dict with {'time': unix_timestamp, 'zone_name': str, 'temp': float} or None
        """
        result = self._resolve_schedule_info(home_id, room_id)
        return result.get('next_change') if result else None

    def _resolve_schedule_info(self, home_id, room_id, homes_data=None):
        """
        Determines both the current schedule zone and the next schedule
        change for a room with a single API call.

        Combines the logic of _get_current_schedule_zone_name and
        _get_next_schedule_change into a single API call.

        Args:
            home_id: the home ID
            room_id: the room ID of the thermostat
            homes_data: optionally an already loaded homesdata response (with
                gateway_types='NAPlug'). If provided, no separate API call is
                made - several thermostats share ONE homesdata fetch instead
                of each triggering their own.

        Returns:
            dict with 'current_zone_name' (str) and 'next_change' (dict) or None
        """
        import datetime
        try:
            if homes_data is None:
                homes_data = self.get_homes_data(gateway_types='NAPlug')
            body = homes_data.get('body', {})

            for home in body.get('homes', []):
                if home.get('id') != home_id:
                    continue

                raw_schedules = home.get('therm_schedules', home.get('schedules', []))
                if not raw_schedules:
                    return None

                # Find the active schedule
                active_schedule = None
                for sched in raw_schedules:
                    if sched.get('selected', False):
                        active_schedule = sched
                        break
                # If none is marked as "selected", take the first one
                if not active_schedule and raw_schedules:
                    active_schedule = raw_schedules[0]

                if not active_schedule:
                    return None

                zones = active_schedule.get('zones', [])
                timetable = active_schedule.get('timetable', [])

                if not zones or not timetable:
                    return None

                # Build the zone maps
                zone_name_map = {z.get('id'): z.get('name', '') for z in zones}
                # Temperature per zone and room
                zone_temp_map = {}
                for z in zones:
                    for room in z.get('rooms', []):
                        if str(room.get('id', '')) == str(room_id):
                            zone_temp_map[z.get('id')] = room.get('therm_setpoint_temperature')
                            break

                # Compute the current minute offset since Monday 00:00
                now = datetime.datetime.now()
                # Python: Monday=0, Sunday=6
                day_of_week = now.weekday()
                current_m_offset = day_of_week * 24 * 60 + now.hour * 60 + now.minute

                result = {}
                # Name of the active schedule
                result['active_schedule_name'] = active_schedule.get('name', '')

                # ---- Determine the current zone ----
                current_zone_id = None
                for entry in timetable:
                    if entry.get('m_offset', 0) <= current_m_offset:
                        current_zone_id = entry.get('zone_id')
                    else:
                        break

                # If no entry matches (e.g. Monday 00:00 before the first
                # entry), take the last entry of the week (wrap-around)
                if current_zone_id is None and timetable:
                    current_zone_id = timetable[-1].get('zone_id')

                zone_name = zone_name_map.get(current_zone_id, None)
                if zone_name:
                    zone_name = NetatmoDevice._translate_zone_name(zone_name)
                    log.debug(f"Netatmo: active zone for room {room_id}: {zone_name}")
                result['current_zone_name'] = zone_name

                # ---- Compute the next schedule change ----
                next_entry = None
                for entry in timetable:
                    if entry.get('m_offset', 0) > current_m_offset:
                        next_entry = entry
                        break

                # If there is no next entry this week, wrap around to the first
                # entry
                days_ahead = 0
                if next_entry is None and timetable:
                    next_entry = timetable[0]
                    days_ahead = 7  # wrap a full week

                if next_entry:
                    next_m_offset = next_entry.get('m_offset', 0)
                    next_zone_id = next_entry.get('zone_id')

                    # Compute the Unix timestamp for the next change
                    if days_ahead == 0:
                        offset_diff = next_m_offset - current_m_offset
                        next_time = now + datetime.timedelta(minutes=offset_diff)
                    else:
                        days_until_monday = 7 - day_of_week
                        next_monday = now.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=days_until_monday)
                        next_time = next_monday + datetime.timedelta(minutes=next_m_offset)

                    next_timestamp = int(next_time.timestamp())

                    next_zone_name = zone_name_map.get(next_zone_id, '')
                    if next_zone_name:
                        next_zone_name = NetatmoDevice._translate_zone_name(next_zone_name)

                    temp = zone_temp_map.get(next_zone_id)

                    next_change = {
                        'time': next_timestamp,
                        'zone_name': next_zone_name,
                        'temp': temp,
                    }
                    result['next_change'] = next_change
                    log.debug(f"Netatmo: next schedule change for room {room_id}: {next_zone_name} ({temp}°C) at {time.strftime('%H:%M', time.localtime(next_timestamp))}")

                return result

            return None
        except Exception as e:
            log.debug(f"Netatmo: failed to determine the schedule info: {e}")
            return None

    # ----------------------------------------------------------
    # Device discovery
    # ----------------------------------------------------------
    def get_devices(self):
        """
        Fetches all Netatmo devices and returns them as a NetatmoDevice list.

        Combines weather station and energy data.

        Returns:
            list[NetatmoDevice]
        """
        devices = []

        # 1. Weather stations
        try:
            stations_data = self.get_stations_data()
            body = stations_data.get('body', {})

            for station in body.get('devices', []):
                station_name = station.get('station_name',
                                           station.get('name', 'Wetterstation'))
                # Main module (NAMain)
                main_dev = NetatmoDevice(station, station_name=station_name)
                main_dev.name = station_name
                devices.append(main_dev)

                # Additional modules (outdoor, wind, rain, indoor)
                for module in station.get('modules', []):
                    mod_dev = NetatmoDevice(module, station_name=station_name)
                    devices.append(mod_dev)

            # debug, not info: this runs on every poll (roughly every 30
            # seconds). At info level it produced 87% of the add-on's whole
            # log output and buried the lines that actually matter.
            log.debug(f"Netatmo: {len(devices)} weather station module(s) found")
            weather_ok = True

        except Exception as e:
            weather_ok = False
            self._log_network_error("Netatmo weather station data error", e)

        # 2. Energy / thermostat
        try:
            # max_age=0 deliberately bypasses the cache. Device discovery
            # doubles as the liveness check of the platform: served from the
            # cache, the scheduler would report "Netatmo connected" while
            # their API does not answer at all, and offline detection would
            # be blind. The cache serves the device menu and the schedule
            # lookup, not this spot. As a side effect this call refills it.
            homes_data = self.get_homes_data(max_age=0)
            body = homes_data.get('body', {})
            energy_count = 0

            for home in body.get('homes', []):
                home_id = home.get('id', '')
                home_name = home.get('name', 'Zuhause')
                modules = home.get('modules', [])
                # Default duration for manual setpoints (in minutes)
                default_duration = home.get('therm_setpoint_default_duration', None)

                # Room mapping: homesdata provides the rooms with names and
                # the modules reference them via room_id. Used for the room
                # display and the room grouping in the dialog.
                rooms_map = {r.get('id'): r.get('name', '')
                             for r in home.get('rooms', [])}

                # Fetch the current status
                status_rooms = {}
                status_modules = {}
                try:
                    status = self.get_home_status(home_id)
                    status_home = status.get('body', {}).get('home', {})
                    status_rooms = {r['id']: r for r in status_home.get('rooms', [])}
                    status_modules = {m['id']: m for m in status_home.get('modules', [])}
                except Exception as e:
                    log.debug(f"Ignored error in get_devices: {e}")

                for module in modules:
                    mod_type = module.get('type', '')
                    if mod_type in ('NATherm1', 'NRV', 'NAPlug'):
                        mod_id = module.get('id', '')
                        room_id = module.get('room_id')

                        # Merge the status data
                        merged = dict(module)
                        if mod_id in status_modules:
                            merged.update(status_modules[mod_id])

                        device = NetatmoDevice(merged, station_name=home_name,
                                               home_id=home_id)
                        # take over the room name (empty if there is none)
                        if room_id and room_id in rooms_map:
                            device.room_name = rooms_map[room_id] or ''
                        # Store the default duration of manual setpoints on the
                        # device
                        if default_duration is not None:
                            device._therm_setpoint_default_duration = default_duration

                        # Temperature from the room status. NOT for the relay
                        # (NAPlug): it is a gateway without a sensor of its
                        # own and would otherwise report the temperature of
                        # the thermostat in the same room - which showed up in
                        # the history as a second, identical series.
                        if room_id and room_id in status_rooms:
                            room_status = status_rooms[room_id]
                            if not device.is_relay:
                                device._therm_measured = room_status.get(
                                    'therm_measured_temperature')
                            device._therm_setpoint = room_status.get(
                                'therm_setpoint_temperature')
                            device._therm_setpoint_mode = room_status.get(
                                'therm_setpoint_mode')  # schedule, manual, away, hg, max
                            device._therm_setpoint_end_time = room_status.get(
                                'therm_setpoint_end_time')
                            # Anticipation and open window
                            anticipating_val = room_status.get('anticipating')
                            if anticipating_val is not None:
                                device._anticipating = bool(anticipating_val)
                            open_window_val = room_status.get('open_window')
                            if open_window_val is not None:
                                device._open_window = bool(open_window_val)

                        # Boiler status from the module status
                        if mod_id in status_modules:
                            boiler_val = status_modules[mod_id].get('boiler_status')
                            if boiler_val is not None:
                                device._boiler_status = bool(boiler_val)

                        devices.append(device)
                        energy_count += 1

            if energy_count:
                log.debug(f"Netatmo: {energy_count} energy device(s) found")
            energy_ok = True

        except Exception as e:
            energy_ok = False
            self._log_network_error("Netatmo energy data error", e)

        # Reset the network status when at least one API call succeeded
        if weather_ok or energy_ok:
            self._reset_network_error_state()

        # 3. Resolve schedule zone names and the next schedule change for
        # thermostats in schedule mode. The homesdata response (incl.
        # schedules) is fetched ONCE for all thermostats and passed along -
        # instead of triggering a separate homesdata call per device. This
        # keeps the load with several thermostats safely below Netatmo's rate
        # limit.
        try:
            schedule_devices = [d for d in devices
                                if getattr(d, 'is_thermostat', False)
                                and d._therm_setpoint_mode == 'schedule'
                                and d.home_id]
            if schedule_devices:
                try:
                    sched_homes_data = self.get_homes_data(gateway_types='NAPlug')
                except Exception as e:
                    log.debug(f"Netatmo: homesdata for the schedules not retrievable: {e}")
                    sched_homes_data = None
                for dev in schedule_devices:
                    schedule_info = self._resolve_schedule_info(
                        dev.home_id, dev.room_id, homes_data=sched_homes_data)
                    if schedule_info:
                        zone_name = schedule_info.get('current_zone_name')
                        if zone_name:
                            dev._schedule_zone_name = zone_name
                        next_change = schedule_info.get('next_change')
                        if next_change:
                            dev._next_schedule_change = next_change
                        active_sched_name = schedule_info.get('active_schedule_name')
                        if active_sched_name:
                            dev._active_schedule_name = active_sched_name
        except Exception as e:
            log.debug(f"Netatmo: failed to resolve the schedule zones: {e}")

        self._devices = devices
        return devices

    def update_device_status(self, devices):
        """Updates the status of all Netatmo devices.

        Light path (the normal case): one /homestatus call per home updates
        the changing thermostat fields. The full pass via get_devices()
        (getstationsdata + homesdata + schedule lookup, >=3 calls) runs at
        most every NETATMO_FULL_REFRESH_SECONDS - weather values and
        schedules rarely change, and with the 15 s fg poll interval the full
        pass would otherwise exceed Netatmo's user limit of ~500 calls/hour.
        """
        now = time.time()
        energy_devs = [d for d in devices
                       if getattr(d, 'is_netatmo', False)
                       and getattr(d, 'home_id', None)]
        if (not energy_devs
                or now - self._last_full_status_refresh >= NETATMO_FULL_REFRESH_SECONDS):
            self._last_full_status_refresh = now
            self._update_device_status_full(devices)
            return

        for home_id in sorted({d.home_id for d in energy_devs}):
            try:
                status = self.get_home_status(home_id)
            except Exception as e:
                self._log_network_error("Netatmo homestatus error", e)
                continue
            home = status.get('body', {}).get('home', {})
            status_rooms = {r.get('id'): r for r in home.get('rooms', [])}
            status_modules = {m.get('id'): m for m in home.get('modules', [])}
            self._reset_network_error_state()

            for device in energy_devs:
                if device.home_id != home_id:
                    continue
                mod = status_modules.get(device._id)
                if mod is not None:
                    if 'reachable' in mod:
                        device.is_offline = not mod.get('reachable', True)
                    boiler_val = mod.get('boiler_status')
                    if boiler_val is not None:
                        device._boiler_status = bool(boiler_val)
                room = status_rooms.get(device.room_id) if device.room_id else None
                if room:
                    # Not for the relay - see the note in get_devices().
                    if not device.is_relay:
                        device._therm_measured = room.get('therm_measured_temperature')
                    device._therm_setpoint = room.get('therm_setpoint_temperature')
                    device._therm_setpoint_mode = room.get('therm_setpoint_mode')
                    device._therm_setpoint_end_time = room.get('therm_setpoint_end_time')
                    anticipating_val = room.get('anticipating')
                    if anticipating_val is not None:
                        device._anticipating = bool(anticipating_val)
                    open_window_val = room.get('open_window')
                    if open_window_val is not None:
                        device._open_window = bool(open_window_val)

    def _update_device_status_full(self, devices):
        """Full status pass: complete get_devices() and field transfer."""
        try:
            fresh_devices = self.get_devices()
            fresh_map = {d.uuid: d for d in fresh_devices}

            for device in devices:
                if hasattr(device, 'is_netatmo') and device.is_netatmo:
                    fresh = fresh_map.get(device.uuid)
                    if fresh:
                        device._dashboard_data = fresh._dashboard_data
                        device._therm_measured = fresh._therm_measured
                        device._therm_setpoint = fresh._therm_setpoint
                        device._therm_setpoint_mode = fresh._therm_setpoint_mode
                        device._therm_setpoint_end_time = fresh._therm_setpoint_end_time
                        device._boiler_status = fresh._boiler_status
                        device._schedule_zone_name = fresh._schedule_zone_name
                        device._therm_setpoint_default_duration = fresh._therm_setpoint_default_duration
                        device._anticipating = fresh._anticipating
                        device._open_window = fresh._open_window
                        device._next_schedule_change = fresh._next_schedule_change
                        device._active_schedule_name = fresh._active_schedule_name
                        device.is_offline = fresh.is_offline
                        device.raw_data = fresh.raw_data
        except Exception as e:
            self._log_network_error("Netatmo status update error", e)

    def logout(self):
        """Discards the tokens"""
        self.access_token = None
        self.refresh_token = None
        self.token_expiry = 0
        log.info("Netatmo: logged out")
