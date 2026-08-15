# -*- coding: utf-8 -*-
"""
Smart Home Control - Cozytouch/Atlantic device wrappers.

Currently supported: hot water heat pump (e.g. Austria Email Revolution Evo 3,
modelId 2376) via the Atlantic Cozytouch cloud. The cloud's control model is
"capability ID (numeric) + value" - the meanings are kept centrally in CAP_*
and thus easy to adjust if another model deviates.

Deliberately follows the same structure as vesync_devices.py: shared flags
(is_cozytouch, is_offline, is_on), uuid/unique_id, get_channels(),
get_status_summary(), get_type_display() and setters with per-field
protection windows.

"""

import threading
import time
import json
import datetime
from logHandler import log

import addonHandler
try:
    addonHandler.initTranslation()
except Exception as e:
    log.debug(f"Ignored error during translation setup: {e}")
if "_" not in globals():  # fallback outside of NVDA
    def _(s):
        return s

# ============================================================
# Capability IDs (derived from gduteil/cozytouch capability.py + spike dump)
# Keep CENTRAL: if another Atlantic model deviates, adjust only here.
# ============================================================
# WARNING: capability 22 is NOT the measured water temperature but a
# MODE-DEPENDENT target value (Eco+ -> ~53, manual -> ~58). Confirmed by
# observation (jumps on mode change). Therefore NOT shown in the UI as the
# measured temperature. The real measured temperature may not be exposed
# at all by the cloud for this model (2376) via the mapped capabilities.
CAP_EFFECTIVE_SETPOINT = 22  # mode base value (without boost) - informational only
# Capability 312 = the actually targeted heating goal INCLUDING the Eco
# reduction and boost override. Observed: Eco+ 53.2 / manual 58 / boost 62.
# Shown as "current heating target" when it differs from the setpoint (231).
CAP_ACTIVE_TARGET = 312
CAP_TARGET_TEMP = 231       # target temperature (writable)
CAP_TARGET_TEMP_MIN = 105301  # lower bound for the target temperature
CAP_TARGET_TEMP_MAX = 105300  # upper bound for the target temperature (alternatively 252)
CAP_DHW_ON = 86             # hot water production on/off (switch)
CAP_HEATING_MODE = 87       # operating mode (select): 0=manual, 3=Eco+, 4=program
CAP_BOOST = 165             # boost mode (switch)
CAP_AWAY = 152              # away mode switch: "0"=off, "1"=on, "2"=pending
CAP_AWAY_TS = 222           # away window "[startTs,endTs]" ("[0,0]" = none)
CAP_HOT_WATER_PCT = 271     # available hot water in %
CAP_RESISTANCE = 99         # electric heating element active (binary)
CAP_ENERGY_TOTAL = 59       # cumulative energy consumption, raw value in Wh (/1000 = kWh)
CAP_WIFI_SIGNAL = 179       # Wi-Fi signal (dBm)
CAP_WIFI_SSID = 219         # Wi-Fi network name (SSID)
CAP_VERSION = 121           # firmware version
CAP_OFFPEAK = 283           # off-peak/night tariff active (binary)
CAP_BOOST_TOTAL_TIME = 232  # boost duration (type "time")
CAP_SCHEDULE_BASE = 245     # time programs: 245=Monday ... 251=Sunday
                            # value: [[start,end],[start,end],[start,end]] in
                            # minutes since 0:00

# Fallback bounds if the min/max capabilities are missing
DEFAULT_TEMP_MIN = 50.0
DEFAULT_TEMP_MAX = 62.0

# Operating mode labels. Key = capability value of CAP_HEATING_MODE.
COZYTOUCH_HEATING_MODE_NAMES = {
    # Translators: Cozytouch operating mode (manual).
    "0": _("Manual"),
    # Translators: Cozytouch operating mode (Eco+).
    "3": _("Eco+"),
    # Translators: Cozytouch operating mode (time program).
    "4": _("Program"),
}

# Known model IDs -> exact model name. Shown in the device tree like the
# model aliases of the other platforms. Unknown IDs fall back to the generic
# type plus the numeric model ID (helps users report new models).
COZYTOUCH_MODEL_NAMES = {
    2376: "Austria Email Revolution Evo 3",
}

# Protection window (seconds): for this long after a local action, a possibly
# still stale cloud value for the SAME field is not written back.
PROTECT_WINDOW = 30.0


def _to_float(value, default=None):
    """Safely converts a Cozytouch value (often '53.2000000...') to float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_temp(value):
    """Formats a temperature accessibly: whole number when possible."""
    f = _to_float(value)
    if f is None:
        return "?"
    if abs(f - round(f)) < 0.05:
        return str(int(round(f)))
    return f"{f:.1f}".replace(".", ",")


def _min_to_hhmm(minutes):
    """Converts minutes since midnight to 'HH:MM'."""
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return "?"
    return f"{m // 60:02d}:{m % 60:02d}"


def _format_schedule_windows(raw):
    """Formats a time program value like '[[420,1020],[0,0],[0,0]]'.

    Returns e.g. '07:00-17:00' or 'none' (all windows [0,0]).
    """
    try:
        windows = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None
    if not isinstance(windows, list):
        return None
    parts = []
    for w in windows:
        if isinstance(w, list) and len(w) == 2 and not (w[0] == 0 and w[1] == 0):
            parts.append(f"{_min_to_hhmm(w[0])}–{_min_to_hhmm(w[1])}")
    # Translators: Time program without active heating windows.
    return ", ".join(parts) if parts else _("none")


class CozytouchWaterHeater:
    """Wrapper for an Atlantic/Cozytouch hot water heat pump."""

    def __init__(self, raw_device, api):
        self.raw_data = raw_device
        self._api = api

        self.device_id = raw_device.get("deviceId")
        # Translators: Fallback device name for a Cozytouch hot water heat
        # pump.
        self.device_name = raw_device.get("name", _("Hot water heat pump"))
        self.model_id = raw_device.get("modelId")
        self.product_id = raw_device.get("productId")
        self.gateway_serial = raw_device.get("gatewaySerialNumber", "")

        # Shared flags (as with Meross/Netatmo/VeSync)
        self.is_cozytouch = True
        self.is_meross = False
        self.is_netatmo = False
        self.is_vesync = False
        self.is_offline = False
        self.is_sensor = False
        self.is_water_sensor = False
        self.is_temperature_sensor = False
        self.is_plug = False
        self.is_light = False
        self.is_diffuser = False
        self.is_hub = False
        self.is_multi_channel = False
        self.has_power_meter = False

        # Display name + type field (as with the other platforms)
        self.name = self.device_name
        self.type = "water_heater"

        # Capability cache: {capabilityId(int): value(str)}
        self._caps = {}
        # Per-field protection window (logical field -> timestamp of last local
        # action)
        self._last_field_action_ts = {}
        self._lock = threading.RLock()

        # Take over initial capabilities from the device list (if present)
        self.apply_capabilities(raw_device.get("capabilities", []))

    # ---------------- Identity ----------------
    @property
    def uuid(self):
        return f"cozytouch_{self.device_id}"

    @property
    def unique_id(self):
        return self.uuid

    def get_channels(self):
        return []

    def get_type_display(self):
        """Device type incl. exact model (like the other platforms).

        Known model IDs show the real model name; unknown ones show the
        generic type so nothing breaks with new hardware.
        """
        model = COZYTOUCH_MODEL_NAMES.get(self.model_id)
        if model:
            return model
        # Translators: Device type display for a Cozytouch hot water heat pump.
        return _("Hot water heat pump")

    @property
    def model_display(self):
        """Exact model name, or None if the model ID is unknown."""
        return COZYTOUCH_MODEL_NAMES.get(self.model_id)

    # ---------------- Capability access ----------------
    def apply_capabilities(self, caps_list):
        """Takes over a list of {capabilityId, value} into the cache.

        Fields with an active local protection window are NOT overwritten -
        analogous to the VeSync per-field logic. The value just set stays
        stable in the display while external changes to other fields arrive
        immediately.
        """
        if not isinstance(caps_list, list):
            return
        with self._lock:
            for c in caps_list:
                if not isinstance(c, dict):
                    continue
                cid = c.get("capabilityId")
                if cid is None:
                    continue
                field = self._field_for_cap(cid)
                if field and self._is_field_protected(field):
                    continue
                self._caps[int(cid)] = c.get("value")

    def _cap(self, cap_id, default=None):
        with self._lock:
            return self._caps.get(int(cap_id), default)

    @staticmethod
    def _field_for_cap(cap_id):
        """Maps a capability ID to a logical protection field (or None)."""
        return {
            CAP_TARGET_TEMP: "target_temp",
            CAP_HEATING_MODE: "mode",
            CAP_BOOST: "boost",
            CAP_DHW_ON: "dhw",
            CAP_AWAY: "away",
            CAP_AWAY_TS: "away",
        }.get(int(cap_id))

    def _protect(self, field):
        self._last_field_action_ts[field] = time.time()

    def _is_field_protected(self, field, window=PROTECT_WINDOW):
        ts = self._last_field_action_ts.get(field, 0.0)
        return (time.time() - ts) < window

    # ---------------- Derived properties ----------------
    @property
    def is_on(self):
        """Hot water production active (CAP_DHW_ON)."""
        return str(self._cap(CAP_DHW_ON, "0")) == "1"

    @property
    def effective_setpoint(self):
        """Mode base value (CAP 22) - informational, not editable."""
        return _to_float(self._cap(CAP_EFFECTIVE_SETPOINT))

    @property
    def active_target(self):
        """Actually targeted heating goal incl. Eco/boost (CAP 312)."""
        return _to_float(self._cap(CAP_ACTIVE_TARGET))

    @property
    def target_temperature(self):
        return _to_float(self._cap(CAP_TARGET_TEMP))

    @property
    def target_temp_min(self):
        return _to_float(self._cap(CAP_TARGET_TEMP_MIN), DEFAULT_TEMP_MIN)

    @property
    def target_temp_max(self):
        return _to_float(self._cap(CAP_TARGET_TEMP_MAX), DEFAULT_TEMP_MAX)

    @property
    def mode_value(self):
        v = self._cap(CAP_HEATING_MODE)
        return str(v) if v is not None else None

    @property
    def mode_name(self):
        return COZYTOUCH_HEATING_MODE_NAMES.get(self.mode_value, self.mode_value or "?")

    @property
    def boost_on(self):
        return str(self._cap(CAP_BOOST, "0")) == "1"

    @property
    def away_on(self):
        # "1" = active, "2" = pending (scheduled, startDate not reached yet).
        # Both count as ON - matching the reference implementation, which
        # treats every value != "0" as on. Reading only == "1" made a freshly
        # enabled away mode (reported as "2" for the first minute) look off.
        return str(self._cap(CAP_AWAY, "0")) != "0"

    @property
    def away_pending(self):
        """True while away mode is scheduled but not yet active (value "2")."""
        return str(self._cap(CAP_AWAY, "0")) == "2"

    @property
    def away_window(self):
        """Public (start, end) of the away period - see _away_window."""
        return self._away_window()

    def _away_window(self):
        """Returns the (start, end) away timestamps from CAP 222, or (None, None)."""
        raw = self._cap(CAP_AWAY_TS)
        try:
            window = json.loads(raw) if isinstance(raw, str) else raw
            if (isinstance(window, list) and len(window) == 2
                    and window[0] and window[1] and window[0] <= window[1]):
                return int(window[0]), int(window[1])
        except (ValueError, TypeError):
            pass
        return None, None

    @property
    def hot_water_percent(self):
        v = self._cap(CAP_HOT_WATER_PCT)
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    @property
    def resistance_on(self):
        return str(self._cap(CAP_RESISTANCE, "0")) == "1"

    @property
    def energy_total_kwh(self):
        """Cumulative energy consumption in kWh (raw value cap 59 is Wh)."""
        wh = _to_float(self._cap(CAP_ENERGY_TOTAL))
        return wh / 1000.0 if wh is not None else None

    @property
    def offpeak_active(self):
        """Off-peak/night tariff currently detected (CAP 283)."""
        return str(self._cap(CAP_OFFPEAK, "0")) == "1"

    @property
    def wifi_signal(self):
        """Wi-Fi signal in dBm (CAP 179, e.g. -42)."""
        v = self._cap(CAP_WIFI_SIGNAL)
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    @property
    def wifi_ssid(self):
        """Wi-Fi network name/SSID (CAP 219)."""
        v = self._cap(CAP_WIFI_SSID)
        return str(v) if v else None

    @property
    def boost_total_time(self):
        """Boost duration (CAP 232). Unit unconfirmed; 0 = not set."""
        v = _to_float(self._cap(CAP_BOOST_TOTAL_TIME))
        return v if v else None

    @property
    def today_schedule_text(self):
        """Heating windows for TODAY from the time program (CAP 245=Mon .. 251=Sun)."""
        weekday = datetime.date.today().weekday()  # 0=Monday .. 6=Sunday
        raw = self._cap(CAP_SCHEDULE_BASE + weekday)
        if raw is None:
            return None
        return _format_schedule_windows(raw)

    @property
    def firmware(self):
        v = self._cap(CAP_VERSION)
        return str(v) if v is not None else None

    # ---------------- Accessible summary ----------------
    def get_status_summary(self):
        if self.is_offline:
            # Translators: Device is not reachable.
            return _("offline")
        parts = []
        tt = self.target_temperature
        if tt is not None:
            # Translators: Cozytouch status announcement: configured target
            # temperature.
            parts.append(_("Target temperature: {temp} degrees").format(temp=_fmt_temp(tt)))
        at = self.active_target
        if at is not None and tt is not None and abs(at - tt) >= 0.5:
            # Translators: Cozytouch status announcement: actually targeted
            # heating goal (incl. Eco/boost adjustment) if it differs from the
            # target temperature.
            parts.append(_("current heating target: {temp} degrees").format(temp=_fmt_temp(at)))
        hw = self.hot_water_percent
        if hw is not None:
            # Translators: Cozytouch status announcement: remaining hot water
            # supply.
            parts.append(_("Hot water supply: {percent} percent").format(percent=hw))
        if self.mode_value is not None:
            # Translators: Cozytouch status announcement: current operating
            # mode.
            parts.append(_("Mode: {mode}").format(mode=self.mode_name))
        # Translators: Cozytouch status announcement: hot water production
        # on/off.
        parts.append(_("Operation on") if self.is_on else _("Operation off"))
        if self.boost_on:
            # Translators: Cozytouch status announcement: boost mode is
            # running.
            parts.append(_("Boost active"))
        if self.away_on:
            if self.away_pending:
                # Translators: Cozytouch status announcement: away mode is
                # scheduled but its start time has not been reached yet.
                parts.append(_("Away mode scheduled"))
            else:
                # Translators: Cozytouch status announcement: away mode active.
                parts.append(_("Away mode active"))
        if self.resistance_on:
            # Translators: Cozytouch status announcement: electric backup
            # heating element active.
            parts.append(_("Electric heating element active"))
        return ", ".join(parts)

    # ---------------- Setters (write + optimistically set locally)
    # ----------------
    def set_target_temperature(self, temp):
        """Sets the target temperature (degrees Celsius)."""
        lo, hi = self.target_temp_min, self.target_temp_max
        temp = max(lo, min(hi, float(temp)))
        value = str(temp)
        ok = self._api.set_capability(self.device_id, CAP_TARGET_TEMP, value)
        if ok:
            with self._lock:
                self._caps[CAP_TARGET_TEMP] = value
            self._protect("target_temp")
        return ok

    def set_mode(self, mode_value):
        """Sets the operating mode (capability value as string, e.g. '3')."""
        value = str(mode_value)
        ok = self._api.set_capability(self.device_id, CAP_HEATING_MODE, value)
        if ok:
            with self._lock:
                self._caps[CAP_HEATING_MODE] = value
            self._protect("mode")
        return ok

    def set_boost(self, on):
        value = "1" if on else "0"
        ok = self._api.set_capability(self.device_id, CAP_BOOST, value)
        if ok:
            with self._lock:
                self._caps[CAP_BOOST] = value
            self._protect("boost")
        return ok

    def set_boost_duration(self, minutes):
        """Sets the boost duration (CAP 232) in minutes.

        EXPERIMENTAL: the reference implementation treats this capability as
        read-only (diagnostics). Whether the cloud accepts the write is
        checked by the value verification in set_capability, so a rejection
        produces an honest error instead of a silent failure.
        """
        value = str(int(minutes))
        ok = self._api.set_capability(self.device_id, CAP_BOOST_TOTAL_TIME, value)
        if ok:
            with self._lock:
                self._caps[CAP_BOOST_TOTAL_TIME] = value
            self._protect("boost")
        return ok

    def set_dhw(self, on):
        value = "1" if on else "0"
        ok = self._api.set_capability(self.device_id, CAP_DHW_ON, value)
        if ok:
            with self._lock:
                self._caps[CAP_DHW_ON] = value
            self._protect("dhw")
        return ok

    def set_away(self, on, start_ts=None, end_ts=None):
        """Toggles away mode via the coordinated API call.

        The backend requires an absence date range; a bare switch write is
        silently ineffective. ``start_ts``/``end_ts`` allow scheduling an
        explicit period (from the dialog). Without an explicit period, a
        valid range stored on the device is reused; otherwise the same
        default as the reference implementation applies:
        start = now + 60 s, end = start + 2 days.
        """
        if on:
            now = time.time()
            if not start_ts or not end_ts:
                start_ts, end_ts = self._away_window()
            if not start_ts or not end_ts or end_ts <= now:
                start_ts = int(now) + 60
                end_ts = start_ts + 2 * 24 * 60 * 60
            start_ts, end_ts = int(start_ts), int(end_ts)
        else:
            start_ts, end_ts = (None, None)
        ok = self._api.set_away_mode(
            self.device_id, bool(on), start_ts, end_ts,
            ts_capability_id=CAP_AWAY_TS, mode_capability_id=CAP_AWAY,
        )
        if ok:
            with self._lock:
                # Optimistic: freshly enabled mode starts as "pending" ("2")
                # because startDate lies ~1 min in the future.
                self._caps[CAP_AWAY] = "2" if on else "0"
                self._caps[CAP_AWAY_TS] = (
                    "[" + str(start_ts) + "," + str(end_ts) + "]"
                    if on else "[0,0]")
            self._protect("away")
        return ok

    def _update_status(self):
        """Updates the capabilities of this single device (on demand).

        Safety net for generic refresh paths that expect
        ``device._update_status()`` (like Meross/Netatmo/VeSync). The regular
        background refresh uses ``CozytouchAPI.update_device_status`` instead.
        """
        try:
            caps = self._api._fetch_capabilities(self.device_id)
            if caps is not None:
                self.apply_capabilities(caps)
                self.is_offline = False
        except Exception as e:
            log.debug(f"Cozytouch: _update_status failed for {self.name}: {e}")
