# -*- coding: utf-8 -*-
"""
Smart Home Control - NVDA add-on
Controls Meross, Netatmo, VeSync/Levoit and Cozytouch/Atlantic devices
via their cloud APIs.

"""

import globalPluginHandler
import ui
import gui
import wx
import scriptHandler
import inputCore
from keyboardHandler import KeyboardInputGesture
import config
import addonHandler
import threading
import sys
import os
import time
from logHandler import log

# Determine the add-on path and add the lib folder to the Python path.
# Append instead of insert(0, ...): this keeps NVDA's own modules and other
# add-ons taking precedence and prevents our bundled versions (requests,
# urllib3, idna, certifi, aiohttp, ...) from overriding anything already
# loaded.
#
# Dual-arch bundling: pure Python packages live directly in lib/. Packages with
# compiled C extensions (aiohttp, multidict, yarl, frozenlist, propcache,
# charset_normalizer, Cryptodome) live architecture-specific under
# lib/_arch/<tag>/, because a .pyd must match the interpreter's Python version
# AND bitness exactly:
# - NVDA 2025.x : 32-bit, Python 3.11  -> lib/_arch/cp311-win32
# - NVDA 2026.1 : 64-bit, Python 3.13  -> lib/_arch/cp313-amd64
#
# WICHTIG: Es gibt KEINEN Pure-Python-Fallback für diese Pakete. Frühere
# Fassungen dieses Kommentars versprachen einen ("slower, but functional") -
# im gebauten Paket liegen aiohttp, multidict, yarl, frozenlist, propcache,
# charset_normalizer und Cryptodome aber ausschließlich unter lib/_arch/.
# Passt keine Architektur, steht damit nicht ein langsamerer Weg zur
# Verfügung, sondern gar keiner: die Meross-Unterstützung fällt aus. Die
# beiden gebündelten Architekturen decken NVDA 2025.1 bis 2026.1 vollständig
# ab; ein dritter Fall ist hypothetisch, soll dann aber ehrlich gemeldet
# werden statt als "Fallback" durchzugehen.
# Selection is primarily by bitness; the Python version is only used for the
# warning.
import struct as _struct

def _select_arch_dir(arch_root):
    """Selects the matching _arch subfolder for the running interpreter."""
    is_64bit = _struct.calcsize("P") == 8
    expected = "cp313-amd64" if is_64bit else "cp311-win32"
    candidate = os.path.join(arch_root, expected)
    if os.path.isdir(candidate):
        py = sys.version_info
        exp_minor = 13 if is_64bit else 11
        if py.minor != exp_minor:
            log.warning(
                f"Smart Home Control: Python {py.major}.{py.minor} weicht von der "
                f"für {expected} gebauten Version ab – die kompilierten Extensions "
                f"lassen sich möglicherweise nicht laden."
            )
        return candidate
    log.error(
        f"Smart Home Control: Kein passender _arch-Ordner ({expected}) gefunden. "
        f"Die Meross-Unterstützung steht auf dieser NVDA-/Python-Version NICHT "
        f"zur Verfügung (es gibt keinen Pure-Python-Ersatz für aiohttp und "
        f"Cryptodome). Die übrigen Plattformen – Netatmo, VeSync und Cozytouch – "
        f"funktionieren unverändert."
    )
    return None

addon_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
lib_path = os.path.join(addon_path, "lib")
_arch_dir = _select_arch_dir(os.path.join(lib_path, "_arch"))
# Append the arch folder BEFORE lib so the compiled packages are found there;
# pure Python packages (requests, idna, meross_iot, ...) come from lib/.
if _arch_dir and _arch_dir not in sys.path:
    sys.path.append(_arch_dir)
    log.debug(f"Smart Home Control: arch-Pfad angehängt: {_arch_dir}")
if lib_path not in sys.path:
    sys.path.append(lib_path)
    log.debug(f"Smart Home Control: lib-Pfad angehängt: {lib_path}")

# Initialize the add-on.
# Guarded like every other module in this package: an unguarded failure here
# would abort the import of the WHOLE add-on instead of just losing the
# translations.
try:
    addonHandler.initTranslation()
except Exception as e:
    log.debug(f"initTranslation fehlgeschlagen: {e}")
if "_" not in globals():  # fallback outside of NVDA
    def _(s):
        return s

from .meross_api import MerossAPI
from .netatmo_api import NetatmoAPI
from .vesync_api import VeSyncAPI
from .cozytouch_api import CozytouchAPI
from .device_dialog import SmartHomeControlDialog
from .settings_panel import SmartHomeSettingsDialog
from .security import encrypt_dpapi, decrypt_dpapi, is_encrypted
from .credentials import _CredentialsMixin
from .scheduler import _SchedulerMixin
from .change_detection import _ChangeDetectionMixin
from .platform_utils import split_by_platform, PLATFORM_LABELS
from .dialog_helpers import _beep
from .constants import (
    CONFSPEC, BEEP_ERROR, BEEP_SUCCESS, BEEP_LOADING, BEEP_ACTION,
    NETATMO_REDIRECT_PORT,
)


def _safe_log_error(message, exception):
    """Logs an error WITHOUT exc_info so no sensitive request data
    (tokens, bodies, headers) ends up in the NVDA log.
    """
    log.error(f"{message}: {type(exception).__name__}: {exception}")

# Config section for this add-on
config.conf.spec["smartHomeControl"] = CONFSPEC


class GlobalPlugin(_CredentialsMixin, _SchedulerMixin, _ChangeDetectionMixin,
                   globalPluginHandler.GlobalPlugin):
    """Smart Home Control global plugin.

    Aufgeteilt in Mixins (Verhalten unverändert):
      - _CredentialsMixin    (credentials.py): verschlüsselte Passwort-Properties
      - _SchedulerMixin      (scheduler.py): Polling-Scheduler + Plattform-Refresh
      - _ChangeDetectionMixin (change_detection.py): externe Änderungserkennung
    """

    # Translators: Category name in the NVDA input gestures dialog. Brand name,
    # do not translate.
    scriptCategory = _("Smart Home Control")

    # The default shortcuts are defined via the @script decorators of the
    # respective scripts (gesture=...). An additional __gestures dict would be
    # a redundant duplicate definition of the same binding.

    def __init__(self):
        super().__init__()
        self.api = None
        self.netatmo_api = None
        self.vesync_api = None
        self.cozytouch_api = None
        # Thread safety: self.devices is read and written from several threads
        # (background refresh, VeSync fast poll, login thread, UI thread).
        # Read/write accesses should hold the lock or work with a snapshot
        # copy.
        self._devices_lock = threading.RLock()
        self.devices = []  # all devices (Meross + Netatmo + VeSync mixed)
        self.is_logged_in = False
        self.is_loading = False
        self._status_beep_active = False  # flag for the periodic beep during the status query

        # Platform flags - ALL off by default. The user chooses in the settings
        # which platform(s) to use; nothing is preselected (not even Meross).
        self.use_meross = False
        self.use_netatmo = False
        self.use_vesync = False
        self.use_cozytouch = False

        # Encrypted password storage (backing field for the password property)
        self._encrypted_password = ""

        # Netatmo credentials
        self.netatmo_client_id = ''
        self.netatmo_client_secret = ''
        self.netatmo_access_token = ''
        self.netatmo_refresh_token = ''
        self.netatmo_token_expiry = 0
        # Port of the local OAuth2 callback server (must match the redirect URI
        # registered at dev.netatmo.com). Configurable per user.
        self.netatmo_redirect_port = NETATMO_REDIRECT_PORT

        # VeSync credentials
        self.vesync_email = ''
        self._encrypted_vesync_password = ''
        self.vesync_country_code = 'DE'
        self.vesync_token = ''
        self.vesync_account_id = ''
        self.vesync_region = ''

        # Cozytouch credentials (Atlantic / Austria Email)
        self.cozytouch_email = ''
        self._encrypted_cozytouch_password = ''
        self.cozytouch_token = ''
        self.cozytouch_capacity_liters = 0  # rated capacity (liters), 0 = off
        
        # Background refresh system
        self._last_refresh_time = 0  # timestamp of the last successful refresh
        self._background_refresh_thread = None
        self._background_refresh_running = False
        self._stop_event = threading.Event()  # interruptible waiting for a fast shutdown

        # Network offline detection for the background refresh
        self._consecutive_refresh_failures = 0  # counts consecutive failures
        self._network_offline = False  # True when the network is detected as offline

        # Per-platform connection status for the one-time announcement on
        # status change. None = not initialized yet (no announcement on the
        # first refresh after login)
        self._meross_connected = None
        self._netatmo_connected = None
        self._vesync_connected = None
        self._cozytouch_connected = None
        
        # Push notification system for external changes (Alexa, app, etc.)
        self._last_announced_change = None  # prevents duplicate announcements
        self._last_announced_time = 0

        # Local toggle actions for suppressing duplicate feedback
        self._recent_local_toggles = {}
        
        # Reference to the open dialog for live updates
        self._active_dialog = None
        
        # Netatmo thermostat state tracking for external change detection
        self._previous_netatmo_therm_states = {}
        self._last_boiler_announce_time = {}  # cooldown for boiler notifications per device

        # Cozytouch state tracking for external change detection
        self._previous_cozytouch_states = {}
        # Per Cozytouch device: timestamp of the last local action (dialog).
        # Prevents the user's own change from being announced again as an
        # external change on the next poll (analogous to VeSync).
        self._recent_cozytouch_actions = {}

        # VeSync state tracking for external change detection
        self._previous_vesync_states = {}
        # Per VeSync device: timestamp of the last local action (dialog).
        # Checked in _detect_vesync_changes so the upcoming push confirmation
        # of the user's own action is not announced twice.
        self._recent_vesync_actions = {}
        # Forces an immediate poll of all platforms at the foreground rate on
        # the next scheduler tick (set when the dialog is opened).
        self._force_poll = False
        
        log.info("=" * 50)
        log.info("Smart Home Control: Add-on gestartet")
        log.info(f"Python-Version: {sys.version}")
        log.info(f"Addon-Pfad: {os.path.dirname(__file__)}")
        log.info("=" * 50)

        # Load the settings
        self.load_settings()
        
        # Auto login if enabled
        if self.auto_login:
            has_meross = self.use_meross and self.email and self._encrypted_password
            has_netatmo = self.use_netatmo and self.netatmo_refresh_token
            has_vesync = self.use_vesync and (
                (self.vesync_email and self._encrypted_vesync_password)
                or (self.vesync_token and self.vesync_account_id)
            )
            has_cozytouch = self.use_cozytouch and (
                self.cozytouch_email and self._encrypted_cozytouch_password
            )
            if has_meross or has_netatmo or has_vesync or has_cozytouch:
                self._start_auto_login()
        
        log.info("Smart Home Control: Initialisierung abgeschlossen")
    
    def terminate(self):
        """Cleanup when NVDA exits"""
        log.info("Smart Home Control: Add-on wird beendet")
        # Eine noch aktive Favoriten-Ebene abräumen - sonst bliebe die
        # Abfang-Funktion in inputCore über das Add-on-Ende hinaus
        # installiert (z.B. beim Neustart des Add-ons).
        try:
            self._fav_layer_exit()
        except Exception as e:
            log.debug(f"Favoriten-Ebene beim Beenden: {e}")
        # Save unsaved history entries from the debounce window.
        try:
            from .history import flush_pending
            flush_pending()
        except Exception as e:
            log.debug(f"History-Flush beim Beenden fehlgeschlagen: {e}")
        # Save unsaved energy samples as well.
        try:
            from .energy import flush_pending as flush_energy
            flush_energy()
        except Exception as e:
            log.debug(f"Energie-Flush beim Beenden fehlgeschlagen: {e}")
        # Stop the unified scheduler thread.
        self._stop_background_refresh()

        # Wait for the thread to end cleanly (best effort).
        t = getattr(self, '_background_refresh_thread', None)
        if t is not None and t.is_alive():
            try:
                t.join(timeout=2.0)
                if t.is_alive():
                    log.debug("Scheduler-Thread konnte nicht binnen 2s beendet werden")
            except Exception as e:
                log.debug(f"Join für Scheduler-Thread fehlgeschlagen: {e}")

        if self.api:
            try:
                self.api.logout()
            except Exception as e:
                log.debug(f"Ignorierter Fehler in terminate: {e}")
        if self.netatmo_api:
            try:
                self.netatmo_api.logout()
            except Exception as e:
                log.debug(f"Ignorierter Fehler in terminate: {e}")
        if self.vesync_api:
            try:
                self.vesync_api.logout()
            except Exception as e:
                log.debug(f"Ignorierter Fehler in terminate: {e}")
        if self.cozytouch_api:
            try:
                self.cozytouch_api.logout()
            except Exception as e:
                log.debug(f"Ignorierter Fehler in terminate: {e}")
        super().terminate()
    
    def load_settings(self):
        """Load the settings from the NVDA config"""
        try:
            conf = config.conf["smartHomeControl"]
            self.email = conf.get("email", "")
            
            # Keep the password encrypted in memory (never as plain text). It
            # is only decrypted on demand at login time via the password
            # property. set_encrypted_password() checks the format cleanly (no
            # blind prefix heuristic).
            self.set_encrypted_password(conf.get("password", ""))
            
            self.auto_login = conf.get("autoLogin", True)
            self.announce_external_changes = conf.get("announceExternalChanges", True)
            self.start_tab = conf.get("startTab", "devices")

            # Platform flags
            self.use_meross = conf.get("useMeross", False)
            self.use_netatmo = conf.get("useNetatmo", False)
            self.use_vesync = conf.get("useVesync", False)

            # Fine-grained notification settings ("Notifications" tab)
            self.notify_meross_toggle = conf.get("notifyMerossToggle", True)
            self.notify_netatmo_mode = conf.get("notifyNetatmoMode", True)
            self.notify_netatmo_setpoint = conf.get("notifyNetatmoSetpoint", True)
            self.notify_netatmo_boiler = conf.get("notifyNetatmoBoiler", True)
            self.notify_netatmo_open_window = conf.get("notifyNetatmoOpenWindow", True)
            self.notify_netatmo_anticipation = conf.get("notifyNetatmoAnticipation", False)
            self.notify_vesync_toggle = conf.get("notifyVesyncToggle", True)
            self.notify_vesync_mode = conf.get("notifyVesyncMode", True)
            self.notify_vesync_fan_speed = conf.get("notifyVesyncFanSpeed", True)
            self.notify_vesync_air_quality = conf.get("notifyVesyncAirQuality", True)
            self.notify_vesync_filter = conf.get("notifyVesyncFilter", True)
            
            # Netatmo credentials (all secrets encrypted)
            raw_client_id = conf.get("netatmoClientId", "")
            if raw_client_id and is_encrypted(raw_client_id):
                self.netatmo_client_id = decrypt_dpapi(raw_client_id)
            else:
                # Legacy: plain text, will be encrypted on the next save
                self.netatmo_client_id = raw_client_id
            encrypted_secret = conf.get("netatmoClientSecret", "")
            self.netatmo_client_secret = decrypt_dpapi(encrypted_secret) if encrypted_secret else ""
            
            encrypted_access = conf.get("netatmoAccessToken", "")
            self.netatmo_access_token = decrypt_dpapi(encrypted_access) if encrypted_access else ""
            encrypted_refresh = conf.get("netatmoRefreshToken", "")
            self.netatmo_refresh_token = decrypt_dpapi(encrypted_refresh) if encrypted_refresh else ""
            self.netatmo_token_expiry = conf.get("netatmoTokenExpiry", 0)
            self.netatmo_redirect_port = conf.get("netatmoRedirectPort", NETATMO_REDIRECT_PORT)

            # VeSync credentials (password and token encrypted)
            self.vesync_email = conf.get("vesyncEmail", "")
            self.set_encrypted_vesync_password(conf.get("vesyncPassword", ""))
            self.vesync_country_code = conf.get("vesyncCountryCode", "DE") or "DE"
            encrypted_vs_token = conf.get("vesyncToken", "")
            self.vesync_token = decrypt_dpapi(encrypted_vs_token) if encrypted_vs_token else ""
            encrypted_vs_account = conf.get("vesyncAccountId", "")
            self.vesync_account_id = decrypt_dpapi(encrypted_vs_account) if encrypted_vs_account else ""
            self.vesync_region = conf.get("vesyncRegion", "")
            self.vesync_filter_threshold = conf.get("vesyncFilterThreshold", 15)

            # Cozytouch credentials (password and token encrypted)
            self.use_cozytouch = conf.get("useCozytouch", False)
            self.cozytouch_email = conf.get("cozytouchEmail", "")
            self.set_encrypted_cozytouch_password(conf.get("cozytouchPassword", ""))
            encrypted_ct_token = conf.get("cozytouchToken", "")
            self.cozytouch_token = decrypt_dpapi(encrypted_ct_token) if encrypted_ct_token else ""
            self.cozytouch_capacity_liters = conf.get("cozytouchCapacityLiters", 0)
            self.notify_cozytouch_mode = conf.get("notifyCozytouchMode", True)
            self.notify_cozytouch_temp = conf.get("notifyCozytouchTemp", True)
            self.notify_cozytouch_boost = conf.get("notifyCozytouchBoost", True)
            self.notify_cozytouch_power = conf.get("notifyCozytouchPower", True)
            self.notify_cozytouch_away = conf.get("notifyCozytouchAway", True)

            log.debug(f"Einstellungen geladen: Meross={self.use_meross}, Netatmo={self.use_netatmo}, VeSync={self.use_vesync}, Cozytouch={self.use_cozytouch}, Auto-Login={self.auto_login}")
        except Exception as e:
            log.error(f"Fehler beim Laden der Einstellungen: {e}")
            self.email = ""
            self._encrypted_password = ""
            self.auto_login = True
            self.announce_external_changes = True
            self.start_tab = "devices"
            self.use_meross = False
            self.use_netatmo = False
            self.use_vesync = False
            # Defaults for the notification settings
            self.notify_meross_toggle = True
            self.notify_netatmo_mode = True
            self.notify_netatmo_setpoint = True
            self.notify_netatmo_boiler = True
            self.notify_netatmo_open_window = True
            self.notify_netatmo_anticipation = False
            self.notify_vesync_toggle = True
            self.notify_vesync_mode = True
            self.notify_vesync_fan_speed = True
            self.notify_vesync_air_quality = True
            self.notify_vesync_filter = True
            self.netatmo_client_id = ""
            self.netatmo_client_secret = ""
            self.netatmo_access_token = ""
            self.netatmo_refresh_token = ""
            self.netatmo_token_expiry = 0
            self.netatmo_redirect_port = NETATMO_REDIRECT_PORT
            self.vesync_email = ""
            self._encrypted_vesync_password = ""
            self.vesync_country_code = "DE"
            self.vesync_token = ""
            self.vesync_account_id = ""
            self.vesync_region = ""
            self.vesync_filter_threshold = 15
            self.use_cozytouch = False
            self.cozytouch_email = ""
            self._encrypted_cozytouch_password = ""
            self.cozytouch_token = ""
            self.cozytouch_capacity_liters = 0
            self.notify_cozytouch_mode = True
            self.notify_cozytouch_temp = True
            self.notify_cozytouch_boost = True
            self.notify_cozytouch_power = True
            self.notify_cozytouch_away = True

    def save_settings(self):
        """Save the settings to the NVDA config (passwords encrypted with DPAPI)"""
        try:
            conf = config.conf["smartHomeControl"]
            conf["email"] = self.email
            
            # Password (already encrypted in memory - store directly)
            conf["password"] = self._encrypted_password if self._encrypted_password else ""
            
            conf["autoLogin"] = self.auto_login
            conf["announceExternalChanges"] = self.announce_external_changes
            conf["startTab"] = getattr(self, 'start_tab', 'devices')

            # Platform flags
            conf["useMeross"] = self.use_meross
            conf["useNetatmo"] = self.use_netatmo
            conf["useVesync"] = self.use_vesync

            # Fine-grained notification settings
            conf["notifyMerossToggle"] = self.notify_meross_toggle
            conf["notifyNetatmoMode"] = self.notify_netatmo_mode
            conf["notifyNetatmoSetpoint"] = self.notify_netatmo_setpoint
            conf["notifyNetatmoBoiler"] = self.notify_netatmo_boiler
            conf["notifyNetatmoOpenWindow"] = self.notify_netatmo_open_window
            conf["notifyNetatmoAnticipation"] = self.notify_netatmo_anticipation
            conf["notifyVesyncToggle"] = self.notify_vesync_toggle
            conf["notifyVesyncMode"] = self.notify_vesync_mode
            conf["notifyVesyncFanSpeed"] = self.notify_vesync_fan_speed
            conf["notifyVesyncAirQuality"] = self.notify_vesync_air_quality
            conf["notifyVesyncFilter"] = self.notify_vesync_filter
            
            # Netatmo credentials (all IDs/secrets/tokens encrypted)
            conf["netatmoClientId"] = encrypt_dpapi(self.netatmo_client_id) if self.netatmo_client_id else ""
            conf["netatmoClientSecret"] = encrypt_dpapi(self.netatmo_client_secret) if self.netatmo_client_secret else ""
            conf["netatmoAccessToken"] = encrypt_dpapi(self.netatmo_access_token) if self.netatmo_access_token else ""
            conf["netatmoRefreshToken"] = encrypt_dpapi(self.netatmo_refresh_token) if self.netatmo_refresh_token else ""
            conf["netatmoTokenExpiry"] = self.netatmo_token_expiry
            conf["netatmoRedirectPort"] = self.netatmo_redirect_port

            # VeSync credentials (password and token encrypted)
            conf["vesyncEmail"] = self.vesync_email
            conf["vesyncPassword"] = self._encrypted_vesync_password if self._encrypted_vesync_password else ""
            conf["vesyncCountryCode"] = self.vesync_country_code or "DE"
            conf["vesyncToken"] = encrypt_dpapi(self.vesync_token) if self.vesync_token else ""
            conf["vesyncAccountId"] = encrypt_dpapi(self.vesync_account_id) if self.vesync_account_id else ""
            conf["vesyncRegion"] = self.vesync_region or ""
            conf["vesyncFilterThreshold"] = self.vesync_filter_threshold

            # Cozytouch credentials (password and token encrypted)
            conf["useCozytouch"] = self.use_cozytouch
            conf["cozytouchEmail"] = self.cozytouch_email
            conf["cozytouchPassword"] = self._encrypted_cozytouch_password if self._encrypted_cozytouch_password else ""
            conf["cozytouchToken"] = encrypt_dpapi(self.cozytouch_token) if self.cozytouch_token else ""
            conf["cozytouchCapacityLiters"] = self.cozytouch_capacity_liters
            conf["notifyCozytouchMode"] = self.notify_cozytouch_mode
            conf["notifyCozytouchTemp"] = self.notify_cozytouch_temp
            conf["notifyCozytouchBoost"] = self.notify_cozytouch_boost
            conf["notifyCozytouchPower"] = self.notify_cozytouch_power
            conf["notifyCozytouchAway"] = self.notify_cozytouch_away

            # Flush atomically to disk immediately. Without this call the
            # config only lives in RAM and is only written on a clean shutdown
            # - a crash with restartUnsafely (see the Vocalizer/WASAPI crash)
            # then discards all credentials entered since.
            self._flush_config_to_disk()
            log.debug("Einstellungen gespeichert (Credentials DPAPI-verschlüsselt)")
        except Exception as e:
            log.error(f"Fehler beim Speichern der Einstellungen: {e}")

    def _flush_config_to_disk(self):
        """Writes NVDA's configuration atomically to disk.

        ``config.conf.save()`` writes ``nvda.ini`` via a temp file + rename,
        so even a crash during writing leaves no corrupt file - either the
        old or the new state stays intact.

        The write runs on the main thread. ``save_settings()`` is also
        called from background threads (auto login, token reauth); from
        there we dispatch the flush to the main thread via ``wx.CallAfter``.
        """
        def _do_save():
            try:
                config.conf.save()
            except Exception as e:
                log.error(f"Fehler beim Schreiben der Konfiguration: {e}")

        if wx.IsMainThread():
            _do_save()
        else:
            wx.CallAfter(_do_save)

    def _vesync_reauth(self, api):
        """Reauth callback for VeSync on token expiry.

        Called by ``VeSyncAPI._post`` when the API reports a token-expired
        error code. Attempts a password login with the stored credentials.
        On success the API updates the tokens internally; we additionally
        save them in the NVDA configuration.
        """
        if not (self.vesync_email and self._encrypted_vesync_password):
            log.warning("VeSync Re-Auth nicht möglich – keine Zugangsdaten gespeichert")
            return False
        try:
            pw = self.vesync_password
            try:
                api.login(self.vesync_email, pw)
            finally:
                pw = None
                del pw
            creds = api.get_credentials()
            if creds["token"] and creds["account_id"]:
                self.vesync_token = creds["token"]
                self.vesync_account_id = creds["account_id"]
                self.vesync_country_code = creds["country_code"]
                self.vesync_region = creds["region"]
                self.save_settings()
            log.info("VeSync Re-Auth erfolgreich")
            return True
        except Exception as e:
            _safe_log_error("VeSync Re-Auth fehlgeschlagen", e)
            return False

    def _cozytouch_reauth(self, api):
        """Reauth callback for Cozytouch on an expired token.

        Called by ``CozytouchAPI._request`` on HTTP 401/403. Attempts a
        password login with the stored credentials and saves the fresh token
        in the NVDA configuration.
        """
        if not (self.cozytouch_email and self._encrypted_cozytouch_password):
            log.warning("Cozytouch Re-Auth nicht möglich – keine Zugangsdaten gespeichert")
            return False
        try:
            pw = self.cozytouch_password
            try:
                api.login(self.cozytouch_email, pw)
            finally:
                pw = None
                del pw
            creds = api.get_credentials()
            if creds.get("token"):
                self.cozytouch_token = creds["token"]
                self.save_settings()
            log.info("Cozytouch Re-Auth erfolgreich")
            return True
        except Exception as e:
            _safe_log_error("Cozytouch Re-Auth fehlgeschlagen", e)
            return False

    def _start_auto_login(self):
        """Starts the automatic login in the background"""
        log.info("Starte Auto-Login...")
        platforms = []
        if self.use_meross:
            platforms.append("Meross")
        if self.use_netatmo:
            platforms.append("Netatmo")
        if self.use_vesync:
            platforms.append("VeSync")
        if self.use_cozytouch:
            platforms.append("Cozytouch")
        ui.message(_("Anmeldung bei {platforms}...").format(platforms=", ".join(platforms)))
        threading.Thread(target=self._do_login, daemon=True).start()
    
    def _do_login(self):
        """Performs the login (in a separate thread) - Meross and/or Netatmo and/or VeSync"""
        try:
            log.info("_do_login: Login-Prozess startet...")
            self.is_loading = True
            meross_devices = []
            netatmo_devices = []
            vesync_devices = []
            cozytouch_devices = []

            # ---- Meross login ----
            if self.use_meross and self.email and self._encrypted_password:
                try:
                    wx.CallAfter(ui.message, _("Meross: Verbinde..."))
                    log.info("Verbinde mit Meross-Server...")
                    
                    # Take over the new instance only AFTER a successful login:
                    # if a re-login fails, the previous (still working)
                    # instance incl. MQTT push stays untouched.
                    new_api = MerossAPI()
                    new_api.set_device_state_changed_callback(self._on_external_device_change)
                    new_api.set_throttle_callback(self._on_meross_throttled)
                    # Decrypt the password only at login time. login() passes
                    # it through as a coroutine argument and deletes it itself,
                    # so no permanent plain-text copy remains here or in
                    # MerossAPI.
                    _tmp_password = self.password
                    try:
                        new_api.login(self.email, _tmp_password)
                    finally:
                        _tmp_password = None
                        del _tmp_password
                    log.info("Meross Login erfolgreich!")
                    
                    wx.CallAfter(ui.message, _("Meross: Lade Geräte..."))
                    meross_devices = new_api.get_devices()
                    new_api.set_wrapped_devices(meross_devices)
                    old_api, self.api = self.api, new_api
                    if old_api is not None:
                        try:
                            old_api.logout()
                        except Exception as e:
                            log.debug(f"Logout der alten Meross-Instanz fehlgeschlagen: {e}")
                    log.info(f"Meross: {len(meross_devices)} Gerät(e) gefunden")
                    
                except Exception as e:
                    # No exc_info -> avoids token/header leaks in the log.
                    _safe_log_error("Meross Login fehlgeschlagen", e)
                    error_msg = str(e)[:80]
                    # Translators: Error message for a failed Meross login.
                    wx.CallAfter(ui.message, _("Meross Fehler: {error}").format(error=error_msg))
            
            # ---- Netatmo login ----
            if self.use_netatmo and self.netatmo_client_id and self.netatmo_refresh_token:
                try:
                    wx.CallAfter(ui.message, _("Netatmo: Verbinde..."))
                    log.info("Verbinde mit Netatmo...")
                    
                    new_netatmo = NetatmoAPI(
                        self.netatmo_client_id,
                        self.netatmo_client_secret,
                        redirect_port=self.netatmo_redirect_port,
                    )
                    new_netatmo.set_tokens(
                        self.netatmo_access_token,
                        self.netatmo_refresh_token,
                        self.netatmo_token_expiry
                    )
                    
                    # Renew the token if necessary
                    new_netatmo._ensure_valid_token()
                    
                    # Tokens possibly renewed - save them
                    tokens = new_netatmo.get_tokens()
                    if tokens['access_token'] != self.netatmo_access_token:
                        self.netatmo_access_token = tokens['access_token']
                        self.netatmo_refresh_token = tokens['refresh_token']
                        self.netatmo_token_expiry = tokens['token_expiry']
                        self.save_settings()
                    
                    netatmo_devices = new_netatmo.get_devices()
                    self.netatmo_api = new_netatmo
                    log.info(f"Netatmo: {len(netatmo_devices)} Gerät(e) gefunden")
                    
                except Exception as e:
                    _safe_log_error("Netatmo Login fehlgeschlagen", e)
                    error_msg = str(e)[:80]
                    # Translators: Error message for a failed Netatmo login.
                    wx.CallAfter(ui.message, _("Netatmo Fehler: {error}").format(error=error_msg))
            
            # ---- VeSync login ----
            if self.use_vesync:
                try:
                    wx.CallAfter(ui.message, _("VeSync: Verbinde..."))
                    log.info("Verbinde mit VeSync...")

                    vs_api = VeSyncAPI(country_code=self.vesync_country_code or "DE")

                    # Register the reauth callback: on an expired token (e.g.
                    # after days of inactivity) an automatic re-login with the
                    # stored password happens - instead of the user having to
                    # go into the settings manually.
                    if hasattr(vs_api, 'set_reauth_callback'):
                        vs_api.set_reauth_callback(self._vesync_reauth)

                    # Preferred: reuse existing tokens, otherwise
                    # email/password
                    have_tokens = bool(self.vesync_token and self.vesync_account_id)
                    if have_tokens:
                        vs_api.set_credentials(
                            token=self.vesync_token,
                            account_id=self.vesync_account_id,
                            country_code=self.vesync_country_code or "DE",
                            region=self.vesync_region or None,
                        )
                        # On a token error fall back to the password login
                        # later
                    elif self.vesync_email and self._encrypted_vesync_password:
                        _vs_password = self.vesync_password
                        try:
                            vs_api.login(self.vesync_email, _vs_password)
                        finally:
                            _vs_password = None
                            del _vs_password
                    else:
                        # Translators: Error message when VeSync was enabled
                        # but neither a token nor email/password are
                        # configured.
                        raise RuntimeError(_("Keine VeSync-Zugangsdaten konfiguriert"))

                    wx.CallAfter(ui.message, _("VeSync: Lade Geräte..."))
                    try:
                        vesync_devices = vs_api.get_devices()
                    except RuntimeError as e:
                        # Token possibly expired -> retry with the password
                        # login
                        if (have_tokens and self.vesync_email
                                and self._encrypted_vesync_password):
                            log.info(f"VeSync: Token-Login fehlgeschlagen ({e}) – versuche Passwort-Login")
                            _vs_password = self.vesync_password
                            try:
                                vs_api.login(self.vesync_email, _vs_password)
                            finally:
                                _vs_password = None
                                del _vs_password
                            vesync_devices = vs_api.get_devices()
                        else:
                            raise

                    self.vesync_api = vs_api

                    # Save the current tokens (they can change due to cross-
                    # region)
                    creds = vs_api.get_credentials()
                    if creds["token"] and creds["account_id"]:
                        self.vesync_token = creds["token"]
                        self.vesync_account_id = creds["account_id"]
                        self.vesync_country_code = creds["country_code"]
                        self.vesync_region = creds["region"]
                        self.save_settings()
                    log.info(f"VeSync: {len(vesync_devices)} Gerät(e) gefunden")

                except Exception as e:
                    _safe_log_error("VeSync Login fehlgeschlagen", e)
                    error_msg = str(e)[:80]
                    # Translators: Error message for a failed VeSync login.
                    wx.CallAfter(ui.message, _("VeSync Fehler: {error}").format(error=error_msg))

            # ---- Cozytouch login (Atlantic / Austria Email) ----
            if self.use_cozytouch and self.cozytouch_email and self._encrypted_cozytouch_password:
                try:
                    wx.CallAfter(ui.message, _("Cozytouch: Verbinde..."))
                    log.info("Verbinde mit Cozytouch...")

                    ct_api = CozytouchAPI()
                    if hasattr(ct_api, 'set_reauth_callback'):
                        ct_api.set_reauth_callback(self._cozytouch_reauth)

                    _ct_password = self.cozytouch_password
                    try:
                        ct_api.login(self.cozytouch_email, _ct_password)
                    finally:
                        _ct_password = None
                        del _ct_password

                    wx.CallAfter(ui.message, _("Cozytouch: Lade Geräte..."))
                    cozytouch_devices = ct_api.get_devices()
                    self.cozytouch_api = ct_api

                    # Save the fresh token
                    creds = ct_api.get_credentials()
                    if creds.get("token"):
                        self.cozytouch_token = creds["token"]
                        self.save_settings()
                    log.info(f"Cozytouch: {len(cozytouch_devices)} Gerät(e) gefunden")

                except Exception as e:
                    _safe_log_error("Cozytouch Login fehlgeschlagen", e)
                    error_msg = str(e)[:80]
                    # Translators: Error message for a failed Cozytouch login.
                    wx.CallAfter(ui.message, _("Cozytouch Fehler: {error}").format(error=error_msg))

            # ---- Merge the device lists (atomically under the lock) ----
            new_devices = meross_devices + netatmo_devices + vesync_devices + cozytouch_devices

            # Session protection: if a RE-login returns no devices at all (e.g.
            # a short network outage during login) while the existing session
            # already has some, the working session is NOT discarded -
            # otherwise the user would be left without devices until the NVDA
            # restart.
            if not new_devices and self.is_logged_in:
                with self._devices_lock:
                    has_existing = bool(self.devices)
                if has_existing:
                    log.warning("Re-Login lieferte keine Geräte – bestehende Sitzung bleibt aktiv")
                    # Translators: Announcement when a renewed login fails but
                    # the previous device list remains in use.
                    wx.CallAfter(ui.message, _("Anmeldung fehlgeschlagen – bisherige Geräte bleiben verfügbar"))
                    return

            with self._devices_lock:
                self.devices = new_devices

            # Create the initial VeSync snapshot so the first refresh does not
            # report the existing state as an "external change".
            for vd in vesync_devices:
                self._previous_vesync_states[vd.uuid] = self._snapshot_vesync_state(vd)
            for cd in cozytouch_devices:
                self._previous_cozytouch_states[cd.uuid] = self._snapshot_cozytouch_state(cd)

            # Über die lokale Liste statt self.devices: die Referenz wurde
            # gerade unter dem Lock zugewiesen, so entfällt der ungeschützte
            # Lesezugriff.
            if new_devices:
                self.is_logged_in = True
                self._last_refresh_time = time.time()
                self._start_background_refresh()

                total = len(new_devices)
                parts = []
                # Platform names are brand names -> do not translate.
                if meross_devices:
                    parts.append(f"{len(meross_devices)} Meross")
                if netatmo_devices:
                    parts.append(f"{len(netatmo_devices)} Netatmo")
                if vesync_devices:
                    parts.append(f"{len(vesync_devices)} VeSync")
                if cozytouch_devices:
                    parts.append(f"{len(cozytouch_devices)} Cozytouch")
                detail = ", ".join(parts)

                # Translators: Success message after login. {total} = number of
                # devices, {detail} = breakdown per platform (e.g. "3 Meross, 1
                # Netatmo").
                wx.CallAfter(ui.message, _(
                    "{total} Geräte bereit ({detail})").format(
                    total=total, detail=detail))
            else:
                self.is_logged_in = False
                # Translators: Announced when the login succeeded but there are
                # no devices in the account.
                wx.CallAfter(ui.message, _("Keine Geräte gefunden"))

        except Exception as e:
            _safe_log_error("Login fehlgeschlagen", e)
            with self._devices_lock:
                has_existing = bool(self.devices)
            if self.is_logged_in and has_existing:
                # Do not discard the existing session because of a failed re-
                # login (session protection, see above).
                log.warning("Re-Login fehlgeschlagen – bestehende Sitzung bleibt aktiv")
            else:
                self.is_logged_in = False
        finally:
            self.is_loading = False
    
    @scriptHandler.script(
        # Translators: Description of the script in the NVDA input gestures
        # dialog.
        description=_("Öffnet das Smart Home Control Menü"),
        gesture="kb:NVDA+shift+h",
    )
    def script_openSmartMenu(self, gesture):
        """Opens the main menu with the device controls"""
        
        if self.is_loading:
            ui.message(_("Anmeldung läuft..."))
            return
        
        if not self.is_logged_in:
            # Not logged in yet - open the settings
            ui.message(_("Nicht angemeldet – öffne Einstellungen"))
            wx.CallAfter(self._show_settings)
            return
        
        # Open the device dialog
        wx.CallAfter(self._show_device_dialog)
    
    def _show_device_dialog(self):
        """Shows the device control dialog"""
        ui.message(_("Öffne Geräteübersicht..."))
        gui.mainFrame.prePopup()
        dlg = SmartHomeControlDialog(gui.mainFrame, self)
        self._active_dialog = dlg  # store the reference for live updates
        # Trigger an immediate poll at the foreground rate so external changes
        # (Levoit app or physical controls) arrive in the dialog quickly. The
        # scheduler detects the open dialog itself and polls at the shorter
        # foreground interval from now on. request_immediate_poll() weckt den
        # Scheduler zusätzlich auf - er schläft bis zur nächsten fälligen
        # Abfrage und würde das Flag sonst erst verzögert sehen.
        self.request_immediate_poll()
        try:
            dlg.ShowModal()
        finally:
            self._active_dialog = None  # clear the reference when the dialog is closed
        dlg.Destroy()
        gui.mainFrame.postPopup()
    
    def _show_settings(self):
        """Shows the settings dialog"""
        gui.mainFrame.prePopup()
        dlg = SmartHomeSettingsDialog(gui.mainFrame, self)
        if dlg.ShowModal() == wx.ID_OK:
            # Settings were saved, try to log in
            should_login = False
            if self.use_meross and self.email and self._encrypted_password:
                should_login = True
            if self.use_netatmo and self.netatmo_client_id and self.netatmo_refresh_token:
                should_login = True
            if self.use_vesync and (
                (self.vesync_email and self._encrypted_vesync_password)
                or (self.vesync_token and self.vesync_account_id)
            ):
                should_login = True
            if self.use_cozytouch and self.cozytouch_email and self._encrypted_cozytouch_password:
                should_login = True
            if should_login:
                self._start_auto_login()
        dlg.Destroy()
        gui.mainFrame.postPopup()
    
    @scriptHandler.script(
        # Translators: Description of the script in the NVDA input gestures
        # dialog.
        description=_("Öffnet die Smart Home Einstellungen")
    )
    def script_openSettings(self, gesture):
        """Opens the settings dialog"""
        wx.CallAfter(self._show_settings)
    
    @scriptHandler.script(
        # Translators: Description of the script in the NVDA input gestures
        # dialog. No default gesture - the user assigns one if needed.
        description=_("Sagt den Energieverbrauch der Messsteckdosen an (heute und letzte 7 Tage)")
    )
    def script_announceEnergy(self, gesture):
        """Announces today's and last week's energy per metering plug.

        Bevorzugte Quelle ist der GERÄTE-eigene Verbrauchszähler
        (consumptionX): Der zählt auch weiter, wenn NVDA nicht läuft.
        Nur wenn ein Gerät diese Abfrage nicht unterstützt, kommen die im
        Hintergrund gesammelten Leistungs-Stichproben zum Einsatz - dann
        als "geschätzt" gekennzeichnet, weil sie nur die NVDA-Laufzeit
        abdecken.
        """
        # Translators: Announced while the energy data is being fetched.
        ui.message(_("Energiedaten werden abgerufen..."))

        def task():
            parts = []
            covered_uuids = set()
            # 1. Geräte-Zähler (vollständig, unabhängig von NVDA-Laufzeit).
            # get_daily_consumption ist cloud-schonend gecacht (15 min TTL) -
            # wiederholte Ansagen kosten keine zusätzlichen Cloud-Nachrichten.
            if self.api and self.use_meross:
                with self._devices_lock:
                    meters = [d for d in self.devices
                              if getattr(d, 'has_power_meter', False)]
                for dev in meters:
                    data = self.api.get_daily_consumption(dev.uuid)
                    if not data:
                        continue
                    kwh_today, kwh_week = self.api.summarize_daily_consumption(data)
                    watt = dev.get_power()
                    covered_uuids.add(dev.unique_id)
                    for ch in (dev.get_channels() or []):
                        covered_uuids.add(ch.unique_id)
                    if watt is not None:
                        # Translators: Energy summary from the device's own
                        # meter. {name} = device, {today}/{week} = kWh,
                        # {watt} = current watts.
                        parts.append(_(
                            "{name}: heute {today} Kilowattstunden, letzte 7 Tage "
                            "{week} Kilowattstunden, aktuell {watt} Watt").format(
                            name=dev.name,
                            today=f"{kwh_today:.2f}".replace(".", ","),
                            week=f"{kwh_week:.2f}".replace(".", ","),
                            watt=f"{watt:g}".replace(".", ",")))
                    else:
                        # Translators: Energy summary from the device's own
                        # meter without a current power value.
                        parts.append(_(
                            "{name}: heute {today} Kilowattstunden, letzte 7 Tage "
                            "{week} Kilowattstunden").format(
                            name=dev.name,
                            today=f"{kwh_today:.2f}".replace(".", ","),
                            week=f"{kwh_week:.2f}".replace(".", ",")))
            # 2. Fallback: gesammelte Stichproben für Geräte ohne Zähler-Antwort
            try:
                from .energy import get_energy_log
                for uuid, name, kwh_today, kwh_week, last_watt in get_energy_log().summary():
                    if uuid in covered_uuids:
                        continue
                    # Translators: Energy summary estimated from background
                    # samples (only covers the time NVDA was running).
                    parts.append(_(
                        "{name}: heute {today} Kilowattstunden, letzte 7 Tage "
                        "{week} Kilowattstunden, aktuell {watt} Watt "
                        "(geschätzt, erfasst nur solange NVDA lief)").format(
                        name=name,
                        today=f"{kwh_today:.2f}".replace(".", ","),
                        week=f"{kwh_week:.2f}".replace(".", ","),
                        watt=f"{last_watt:g}".replace(".", ",")))
            except Exception as e:
                log.debug(f"Energie-Fallback fehlgeschlagen: {e}")
            if not parts:
                # Translators: Message when no energy data is available.
                parts.append(_("Keine Energiedaten verfügbar. Messsteckdosen "
                               "müssen verbunden sein."))
            else:
                # Alphabetisch nach Gerätename (jeder Eintrag beginnt mit dem
                # Namen) - gleiche Ordnung wie beim Übersichts-Befehl.
                parts.sort(key=str.casefold)
            wx.CallAfter(ui.message, "; ".join(parts))

        threading.Thread(target=task, daemon=True).start()

    @scriptHandler.script(
        # Translators: Description of the script in the NVDA input gestures
        # dialog. No default gesture - the user assigns one if needed.
        description=_("Verbindungsdiagnose: Status aller Smart Home Plattformen ansagen")
    )
    def script_connectionDiagnostics(self, gesture):
        """Announces per-platform connection state, network state and token info."""
        parts = []
        if not self.is_logged_in:
            # Translators: Diagnostics: not logged in at all.
            parts.append(_("Nicht angemeldet"))
        platform_states = (
            ('Meross', self.use_meross, self._meross_connected),
            ('Netatmo', self.use_netatmo, self._netatmo_connected),
            ('VeSync', self.use_vesync, self._vesync_connected),
            ('Cozytouch', self.use_cozytouch, self._cozytouch_connected),
        )
        for label, active, connected in platform_states:
            if not active:
                continue
            if connected is True:
                # Translators: Diagnostics: platform is connected.
                state = _("verbunden")
            elif connected is False:
                # Translators: Diagnostics: platform is disconnected.
                state = _("getrennt")
            else:
                # Translators: Diagnostics: platform has not polled yet.
                state = _("noch keine Abfrage")
            parts.append(f"{label}: {state}")
        if self._network_offline:
            # Translators: Diagnostics: network considered offline.
            parts.append(_("Netzwerk: offline (Fehlversuche: {count})").format(
                count=self._consecutive_refresh_failures))
        if self.use_netatmo and self.netatmo_token_expiry:
            remaining = int(self.netatmo_token_expiry - time.time())
            if remaining > 0:
                # Translators: Diagnostics: Netatmo token remaining lifetime.
                parts.append(_("Netatmo-Token: noch {minutes} Minuten gültig").format(
                    minutes=remaining // 60))
            else:
                # Translators: Diagnostics: Netatmo token expired (auto-renewal
                # happens on the next request).
                parts.append(_("Netatmo-Token: abgelaufen, wird bei nächster "
                               "Abfrage erneuert"))
        if self._last_refresh_time:
            age = int(time.time() - self._last_refresh_time)
            # Translators: Diagnostics: seconds since the last successful
            # Meross refresh.
            parts.append(_("Letzte Meross-Aktualisierung vor {seconds} Sekunden").format(
                seconds=age))
        if not parts:
            # Translators: Diagnostics: no platform is enabled.
            parts.append(_("Keine Plattform aktiviert"))
        ui.message("; ".join(parts))

    # ------------------------------------------------------------------
    # Favoriten-Ebene: EIN frei belegbarer Befehl statt 18 einzeln zu
    # belegender.
    #
    # Ablauf: Geste -> Ansage "Favoriten" -> nächste Ziffer 1-9 sagt den
    # Status des Favoriten mit diesem festen Platz an. Dieselbe Ziffer
    # zweimal kurz hintereinander schaltet ihn (Fenster = NVDA-Einstellung
    # "multiPressTimeout", wie bei NVDAs eigenen Doppeldruck-Befehlen).
    # 0 liest die Belegung vor, Escape bricht ab.
    #
    # Die Aufteilung ist Absicht: die harmlose Auskunft kommt sofort und
    # ohne Umweg, das folgenreiche Schalten verlangt den bewussten zweiten
    # Druck. Ein Vertippen sagt also nur etwas an, statt ein Gerät zu
    # schalten.
    #
    # Technik: inputCore.manager._captureFunc fängt die nächste Eingabe ab,
    # bevor NVDA sie als Befehl auflöst; False als Rückgabe schluckt die
    # Taste, damit sie nicht in die fokussierte Anwendung durchrutscht.
    # Dasselbe Muster nutzt z.B. der SPL Assistant (StationPlaylist).
    # ------------------------------------------------------------------
    _FAV_LAYER_IDLE_MS = 15000  # Sicherheitsnetz: Ebene ohne Eingabe beenden

    @scriptHandler.script(
        # Translators: Description of the favorites layer script in the
        # NVDA input gestures dialog.
        # Kurz halten: Der Dialog Tastenbefehle zeigt eine Liste, in der
        # jeder Eintrag am Stück vorgelesen wird. Details stehen im
        # Handbuch und sagt in der Ebene die Taste 0 an.
        description=_("Favorit per Ziffer wählen (einmal drücken sagt den "
                      "Status an, zweimal drücken schaltet)"),
        # BEWUSST OHNE Standard-Belegung - wie alle frei belegbaren Befehle
        # dieser Erweiterung. Eine mitgelieferte Vorgabe kann man nicht
        # verlässlich kollisionsfrei wählen: NVDAs eigene Quelltexte sind nur
        # die halbe Wahrheit, dazu kommen Tastaturlayout (Desktop/Laptop),
        # andere Add-ons und eigene Zuweisungen des Nutzers. Ein Kürzel, das
        # eine bestehende Belegung überschreibt, ist schlimmer als gar keins.
        # Der Nutzer vergibt es unter NVDA-Menü -> Optionen -> Tastenbefehle
        # -> Kategorie "Smart Home Control".
    )
    def script_favoritesLayer(self, gesture):
        """Öffnet die Favoriten-Ebene (nächste Ziffer wählt den Favoriten)."""
        if not self.is_logged_in:
            ui.message(_("Nicht angemeldet"))
            return
        from .favorites import get_favorites
        if not get_favorites().get_count():
            # Translators (bestehende msgid aus dem Favoriten-Tab)
            ui.message(_("Noch keine Favoriten – im Geräte-Tab mit Strg+B hinzufügen"))
            return
        self._fav_layer_active = True
        self._fav_layer_pending = None  # (Platz, wx.CallLater) während des Doppeldruck-Fensters
        inputCore.manager._captureFunc = self._fav_layer_capture
        self._fav_layer_watchdog = wx.CallLater(
            self._FAV_LAYER_IDLE_MS, self._fav_layer_idle_timeout)
        _beep(BEEP_ACTION)
        # "Favoriten" allein war irreführend - es klang nach einer
        # erledigten Aktion statt nach einer Rückfrage. Der Text sagt
        # jetzt, dass die Erweiterung wartet und was sie erwartet.
        # Translators: Announced when the favorites layer opens and waits
        # for a digit. Keep it short - it is spoken on every use.
        ui.message(_("Favorit wählen: Ziffer 1 bis 9"))

    def _fav_layer_exit(self):
        """Verlässt die Ebene und räumt Abfang-Funktion und Timer ab."""
        self._fav_layer_active = False
        if inputCore.manager._captureFunc == self._fav_layer_capture:
            inputCore.manager._captureFunc = None
        # getattr: terminate() ruft auch auf, wenn die Ebene nie offen war
        pending = getattr(self, '_fav_layer_pending', None)
        self._fav_layer_pending = None
        if pending:
            pending[1].Stop()
        watchdog = getattr(self, '_fav_layer_watchdog', None)
        self._fav_layer_watchdog = None
        if watchdog:
            watchdog.Stop()

    def _fav_layer_capture(self, gesture):
        """Wertet die nächste Eingabe innerhalb der Ebene aus.

        ACHTUNG - LÄUFT AUF NVDAs EINGABE-THREAD, NICHT im wx-Hauptthread:
        inputCore ruft die Abfang-Funktion direkt aus ``executeGesture``
        auf. wx-Timer dürfen dort nicht angefasst werden; ``Start()`` wirft
        sonst "wxAssertionError: timer can only be started from the main
        thread" und inputCore schaltet die Abfang-Funktion daraufhin ab -
        die Ebene wäre mitten in der Bedienung tot.

        Deshalb entscheidet diese Funktion nur das, was sie SOFORT
        entscheiden muss - ob die Taste geschluckt wird - und schiebt jede
        weitere Arbeit per ``wx.CallAfter`` (thread-sicher) in den
        Hauptthread. Angenehmer Nebeneffekt: der gesamte Zustand der Ebene
        wird damit ausschließlich im Hauptthread verändert, Sperren sind
        nicht nötig.

        Rückgabe False schluckt die Geste (inputCore bricht die
        Verarbeitung ab), True lässt sie normal weiterlaufen.
        """
        try:
            # Modifier-Tasten (Umschalt, NVDA, ...) durchlassen und in der
            # Ebene bleiben - sie kommen als eigene "Gesten" an.
            if getattr(gesture, 'isModifier', False):
                return True
            if not isinstance(gesture, KeyboardInputGesture):
                # Braille-/Touch-Eingabe o.ä.: Ebene beenden, normal
                # weiterreichen.
                wx.CallAfter(self._fav_layer_exit)
                return True
            key = gesture.mainKeyName
            # Nummernblock-Ziffern gleichbehandeln (Desktop-Layout)
            if key.startswith('numpad') and key[6:].isdigit():
                key = key[6:]
            if key == 'escape':
                wx.CallAfter(self._fav_layer_cancel)
                return False
            if key == '0':
                wx.CallAfter(self._fav_layer_announce_overview)
                return False
            if len(key) == 1 and key.isdigit():  # '1'..'9'
                wx.CallAfter(self._fav_layer_digit, int(key))
                return False
            # Jede andere Taste: Ebene beenden, Fehlerton. Die Taste wird
            # geschluckt, damit z.B. kein Buchstabe in ein Eingabefeld der
            # fokussierten Anwendung rutscht.
            wx.CallAfter(self._fav_layer_reject)
            return False
        except Exception:
            # inputCore würde die Abfang-Funktion bei einer Ausnahme selbst
            # deaktivieren - unsere Timer/Flags müssen trotzdem weg.
            wx.CallAfter(self._fav_layer_exit)
            raise

    def _fav_layer_cancel(self):
        """Escape: Ebene verlassen und das ansagen (im Hauptthread)."""
        if not getattr(self, '_fav_layer_active', False):
            return
        self._fav_layer_exit()
        ui.message(_("Abgebrochen"))

    def _fav_layer_reject(self):
        """Unerwartete Taste: Ebene verlassen, Fehlerton (im Hauptthread)."""
        if not getattr(self, '_fav_layer_active', False):
            return
        self._fav_layer_exit()
        _beep(BEEP_ERROR)

    def _fav_layer_digit(self, number):
        """Ziffer 1-9 in der Ebene: Status ansagen, bei Doppeldruck schalten.

        Einmal drücken ist die harmlose Auskunft und passiert SOFORT -
        dasselbe Muster wie NVDAs eigene Doppeldruck-Befehle (NVDA+T sagt
        den Titel sofort, zweimal buchstabiert ihn). Das folgenreiche
        Schalten verlangt bewusst den zweiten Druck; es gibt deshalb auch
        keine Rücknahme-Frist mehr - der Doppeldruck IST die Bestätigung.

        Läuft immer im wx-Hauptthread (per CallAfter aus der
        Abfang-Funktion) - nur dort dürfen die Timer angefasst werden.
        """
        if not getattr(self, '_fav_layer_active', False):
            return  # Ebene wurde zwischenzeitlich verlassen
        watchdog = getattr(self, '_fav_layer_watchdog', None)
        if watchdog:
            watchdog.Start(self._FAV_LAYER_IDLE_MS)

        pending = self._fav_layer_pending
        if pending and pending[0] == number:
            # Zweiter Druck derselben Ziffer: schalten. Die laufende
            # Status-Ansage wird dabei von der Schalt-Rückmeldung abgelöst.
            pending[1].Stop()
            self._fav_layer_pending = None
            self._fav_layer_exit()
            self._favorite_toggle(number)
            return
        if pending:
            # Andere Ziffer: das Doppeldruck-Fenster der vorherigen gilt
            # nicht mehr (sonst schaltete "1, 2, 1" versehentlich Favorit 1).
            pending[1].Stop()
            self._fav_layer_pending = None

        from .favorites import get_favorites
        if get_favorites().get_by_slot(number) is None:
            # Translators (bestehende msgid)
            ui.message(_("Favorit {number} ist nicht belegt").format(number=number))
            return  # in der Ebene bleiben - der Nutzer kann neu wählen
        # Erster Druck: Status sofort ansagen, dann auf einen möglichen
        # zweiten Druck warten.
        self._favorite_status(number)
        timer = wx.CallLater(
            int(config.conf["keyboard"]["multiPressTimeout"]),
            self._fav_layer_window_expired)
        self._fav_layer_pending = (number, timer)

    def _fav_layer_window_expired(self):
        """Kein zweiter Druck: Der Status ist angesagt, die Ebene endet.

        Bewusst ohne Ton oder Ansage - die Auskunft war die Aktion, ein
        zusätzliches "Ebene beendet" wäre nur Lärm.
        """
        if not getattr(self, '_fav_layer_active', False):
            return  # bereits verlassen (Escape, andere Taste, Beenden)
        self._fav_layer_pending = None
        self._fav_layer_exit()

    def _fav_layer_announce_overview(self):
        """Sagt an, welche Ziffer welchen Favoriten schaltet (Taste 0).

        Läuft im wx-Hauptthread (per CallAfter aus der Abfang-Funktion).
        """
        if not getattr(self, '_fav_layer_active', False):
            return
        watchdog = getattr(self, '_fav_layer_watchdog', None)
        if watchdog:
            watchdog.Start(self._FAV_LAYER_IDLE_MS)
        from .favorites import get_favorites
        favs = get_favorites()
        parts = []
        for n in range(1, 10):
            fav = favs.get_by_slot(n)
            if fav:
                parts.append(f"{n}: {fav.get('name', '')}")
        # Translators: Usage hint at the end of the favorites layer
        # overview (announced after pressing 0 in the layer).
        parts.append(_("Ziffer sagt den Status an, zweimal drücken schaltet, "
                       "Escape bricht ab"))
        ui.message(". ".join(parts))

    def _fav_layer_idle_timeout(self):
        """Sicherheitsnetz: Ebene nach längerer Untätigkeit beenden.

        Ohne dieses Netz bliebe die Abfang-Funktion beliebig lange aktiv
        und würde Minuten später einen völlig zusammenhanglosen
        Tastendruck verschlucken.
        """
        if not getattr(self, '_fav_layer_active', False):
            return
        self._fav_layer_exit()
        ui.message(_("Abgebrochen"))

    def _get_favorite_device(self, number):
        """Liefert (favorit, device) für den Ebenen-Platz ``number`` (1-9).

        device kann None sein (z.B. noch nicht geladen/offline entfernt).
        Der Platz ist die feste, in der Favoriten-Datei gespeicherte Nummer
        des Geräts (favorites._assign_slots) - nicht seine Listenposition.
        """
        from .favorites import get_favorites
        fav = get_favorites().get_by_slot(number)
        if fav is None:
            return None, None
        uid = fav.get('uuid', '')
        with self._devices_lock:
            for d in self.devices:
                if getattr(d, 'unique_id', d.uuid) == uid or d.uuid == uid:
                    return fav, d
                for ch in (d.get_channels() or []):
                    if ch.unique_id == uid or ch.uuid == uid:
                        return fav, ch
        return fav, None

    def _favorite_toggle(self, number):
        """Schaltet Favorit Nr. ``number`` um (für die Direktgesten)."""
        if not self.is_logged_in:
            ui.message(_("Nicht angemeldet"))
            return
        fav, device = self._get_favorite_device(number)
        if fav is None:
            # Translators: Message when the favorite slot is empty.
            ui.message(_("Favorit {number} ist nicht belegt").format(number=number))
            return
        if device is None:
            # Translators: Message when the favorite's device is not loaded.
            ui.message(_("{name}: Gerät nicht verfügbar").format(
                name=fav.get('name', '?')))
            return
        if getattr(device, 'is_netatmo', False) or getattr(device, 'is_sensor', False):
            # Nicht schaltbare Geräte: Sensoren (Meross MS100/MS400 ...) und
            # alle Netatmo-Geräte - Thermostate lassen sich zwar verstellen,
            # aber nicht ein-/ausschalten; Wetterstationen sind reine Anzeige.
            # Früher sagte dieser Zweig einfach nochmal den Status an. Nach
            # dem Doppeldruck wirkte das, als sei nichts passiert. Jetzt
            # nennt die Ansage den Grund; der Status kam beim ersten Druck
            # ohnehin schon.
            _beep(BEEP_ERROR)
            # Translators: Message when the user tries to switch a device
            # that cannot be switched on/off (sensors, Netatmo devices).
            ui.message(_("{name}: nicht schaltbar – im Geräte-Menü einstellbar").format(
                name=device.name))
            return

        def task():
            try:
                result = self.toggle_device(device.uuid)
                _beep(BEEP_SUCCESS)
                wx.CallAfter(ui.message, result)
            except Exception as e:
                _beep(BEEP_ERROR)
                _safe_log_error("Favoriten-Toggle fehlgeschlagen", e)
                # Translators: Error message when toggling a favorite fails.
                wx.CallAfter(ui.message, _("Schalten fehlgeschlagen: {error}").format(
                    error=str(e)[:80]))
        threading.Thread(target=task, daemon=True).start()

    def _favorite_status(self, number):
        """Sagt den Status von Favorit Nr. ``number`` an."""
        if not self.is_logged_in:
            ui.message(_("Nicht angemeldet"))
            return
        fav, device = self._get_favorite_device(number)
        if fav is None:
            ui.message(_("Favorit {number} ist nicht belegt").format(number=number))
            return
        if device is None:
            ui.message(_("{name}: Gerät nicht verfügbar").format(
                name=fav.get('name', '?')))
            return
        parts = [device.name]
        if getattr(device, 'is_offline', False):
            # Translators: Status announcement for an offline device.
            parts.append(_("offline"))
        elif getattr(device, 'is_netatmo', False) and hasattr(device, 'get_status_summary'):
            parts.append(device.get_status_summary())
        else:
            if hasattr(device, 'is_on'):
                parts.append(_("ein") if device.is_on else _("aus"))
            power = device.get_power() if hasattr(device, 'get_power') else None
            if power is not None:
                # Translators: Current power draw in the status announcement.
                parts.append(_("{watt} Watt").format(
                    watt=f"{power:g}".replace(".", ",")))
            if getattr(device, 'is_cozytouch', False):
                tt = device.target_temperature
                if tt is not None:
                    # Translators: Target temperature in the status
                    # announcement.
                    parts.append(_("Ziel {temp} Grad").format(
                        temp=f"{tt:g}".replace(".", ",")))
                parts.append(device.mode_name)
        ui.message(", ".join(str(p) for p in parts if p))

    @scriptHandler.script(
        # Translators: Description of the script in the NVDA input gestures
        # dialog.
        description=_("Sagt den Status aller Smart Home Geräte an"),
        gesture="kb:NVDA+control+shift+p",
    )
    def script_announceStatus(self, gesture):
        """Announces the status of all devices - FAST, from the cache"""
        
        log.debug("script_announceStatus aufgerufen")
        
        if not self.is_logged_in:
            ui.message(_("Nicht angemeldet"))
            log.debug("Nicht angemeldet - Abbruch")
            return
        
        # OPTIMIZED: with a fresh cache, announce immediately WITHOUT waiting
        # (Snapshot unter dem Lock - Konvention aus __init__, Z. 149 ff.)
        with self._devices_lock:
            cached_devices = list(self.devices)
        if self.is_cache_fresh() and cached_devices:
            log.debug(f"Cache frisch - sofortige Ansage von {len(cached_devices)} Geräten")
            # No beep needed - immediate output
            self._announce_devices_status(cached_devices)
            return
        
        # Cache not fresh - short update in the background
        log.debug("Cache nicht frisch - aktualisiere im Hintergrund")
        
        # Start the periodic beep
        self._start_status_beep()
        
        def task():
            try:
                log.debug("Starte Statusaktualisierung...")

                # Use the cached devices, only update the status (faster!)
                # (Leseschnappschuss unter dem Lock)
                with self._devices_lock:
                    have_devices = bool(self.devices)
                if not have_devices:
                    log.debug("Keine gecachten Geräte - hole neue Geräteliste")
                    wx.CallAfter(ui.message, _("Lade Geräte..."))
                    all_devs = []
                    if self.api and self.use_meross:
                        meross_devs = self.api.get_devices()
                        self.api.set_wrapped_devices(meross_devs)
                        all_devs.extend(meross_devs)
                    if self.netatmo_api and self.use_netatmo:
                        all_devs.extend(self.netatmo_api.get_devices())
                    if self.vesync_api and self.use_vesync:
                        try:
                            all_devs.extend(self.vesync_api.get_devices())
                        except Exception as e:
                            log.debug(f"VeSync Geräte konnten nicht geladen werden: {e}")
                    if self.cozytouch_api and self.use_cozytouch:
                        try:
                            all_devs.extend(self.cozytouch_api.get_devices())
                        except Exception as e:
                            log.debug(f"Cozytouch Geräte konnten nicht geladen werden: {e}")
                    # Assign under the lock - the scheduler thread reads the
                    # same list in parallel (consistent with all other write
                    # sites).
                    with self._devices_lock:
                        self.devices = all_devs
                    self._last_refresh_time = time.time()
                else:
                    log.debug(f"Verwende {len(self.devices)} gecachte Geräte - aktualisiere nur Status")
                    # Update Meross, VeSync and Cozytouch status (Netatmo has
                    # rate limits)
                    try:
                        with self._devices_lock:
                            by_platform = split_by_platform(self.devices)
                        meross_devs = by_platform['meross']
                        if meross_devs and self.api:
                            self.api.update_device_status(meross_devs)
                        vesync_devs = by_platform['vesync']
                        if vesync_devs and self.vesync_api:
                            try:
                                self.vesync_api.update_device_status(vesync_devs)
                            except Exception as e:
                                log.debug(f"VeSync Status-Update fehlgeschlagen: {e}")
                        cozytouch_devs = by_platform['cozytouch']
                        if cozytouch_devs and self.cozytouch_api:
                            try:
                                self.cozytouch_api.update_device_status(cozytouch_devs)
                            except Exception as e:
                                log.debug(f"Cozytouch Status-Update fehlgeschlagen: {e}")
                        self._last_refresh_time = time.time()
                    except TimeoutError:
                        log.warning("Status-Update Timeout - verwende gecachte Daten")
                        # No abort - cached data is better than nothing
                
                with self._devices_lock:
                    devs_for_status = list(self.devices)
                if not devs_for_status:
                    log.warning("Keine Geräte gefunden")
                    self._stop_status_beep()
                    wx.CallAfter(ui.message, _("Keine Geräte gefunden"))
                    return

                # Stop the beep and play the success sound
                self._stop_status_beep()
                wx.CallAfter(_beep, BEEP_SUCCESS)
                wx.CallAfter(self._announce_devices_status, devs_for_status)

            except Exception as e:
                _safe_log_error("Fehler beim Abrufen des Status", e)
                self._stop_status_beep()
                wx.CallAfter(_beep, BEEP_ERROR)
                error_msg = str(e)
                if len(error_msg) > 50:
                    error_msg = error_msg[:50] + "..."
                # Translators: Generic error message with detail text.
                wx.CallAfter(ui.message, _("Fehler: {error}").format(error=error_msg))
        
        log.debug("Starte Thread für Statusabfrage")
        threading.Thread(target=task, daemon=True).start()
    
    def _announce_devices_status(self, devices):
        """Announces the status of all devices via speech"""
        # Sort the devices alphabetically by name
        sorted_devices = sorted(devices, key=lambda d: d.name.lower())

        # Count the devices per platform (central mapping, incl. Cozytouch)
        by_platform = split_by_platform(sorted_devices)

        # Assemble the status message (platform brand names untranslated)
        parts = [
            f"{len(devs)} {PLATFORM_LABELS[name]}"
            for name, devs in by_platform.items() if devs
        ]
        # Translators: Placeholder when no devices are listed per platform.
        detail = ", ".join(parts) if parts else _("keine")
        # Translators: Introduction of the status announcement. {count} =
        # total, {detail} = breakdown per platform.
        msg = _("{count} Geräte ({detail}). ").format(
            count=len(sorted_devices),
            detail=detail,
        )
        log.debug(f"Erstelle Statusnachricht für {len(sorted_devices)} Geräte")
        
        # Translators: Announced when a device currently provides no
        # sensor/status data.
        no_data = _("Keine Daten")
        # Translators: Announced when a device is currently unreachable.
        offline_text = _("offline")

        for device in sorted_devices:
            try:
                # Netatmo devices
                if getattr(device, 'is_netatmo', False):
                    summary = device.get_status_summary()
                    msg += f"{device.name}: {summary if summary else no_data}. "
                    continue

                # VeSync devices
                if getattr(device, 'is_vesync', False):
                    summary = device.get_status_summary()
                    msg += f"{device.name}: {summary if summary else no_data}. "
                    continue

                # Cozytouch devices (hot water heat pump): announce via their
                # own status summary like Netatmo/VeSync.
                if getattr(device, 'is_cozytouch', False):
                    summary = device.get_status_summary()
                    msg += f"{device.name}: {summary if summary else no_data}. "
                    continue

                # Offline devices
                if hasattr(device, 'is_offline') and device.is_offline:
                    msg += f"{device.name}: {offline_text}. "
                    continue

                # Temperature sensor
                if device.is_temperature_sensor:
                    log.debug(f"Temperatursensor: {device.name} ({device.type})")
                    temp = device.get_temperature()
                    humidity = device.get_humidity()
                    log.debug(f"  -> Temp: {temp}, Humidity: {humidity}")

                    if temp is not None:
                        msg += f"{device.name}: {temp:.1f}°C"
                        if humidity is not None:
                            # Translators: Relative humidity in percent.
                            msg += _(", {humidity:.1f}% Luftfeuchtigkeit").format(humidity=humidity)
                        msg += ". "
                    else:
                        msg += f"{device.name}: {no_data}. "

                # Water sensor
                elif device.is_water_sensor:
                    alarm = device.is_water_detected()
                    # Translators: Meross water sensor: water was detected
                    # (alarm).
                    status = _("Wasseralarm!") if alarm else _("kein Wasser erkannt")
                    msg += f"{device.name}: {status}. "

                # Hub (MSH300, MSH450)
                elif "msh" in device.type.lower():
                    # Hubs have no on/off state, show the online status
                    if hasattr(device._device, 'online_status'):
                        online = device._device.online_status
                    elif hasattr(device._device, '_online'):
                        online = device._device._online
                    elif hasattr(device._device, 'online'):
                        online = device._device.online
                    else:
                        online = False

                    # Translators: Meross hub connection status.
                    status = _("online") if online else offline_text
                    msg += f"{device.name}: {status}. "

                # Normal devices (plugs, lamps)
                else:
                    status = _("ein") if device.is_on else _("aus")
                    msg += f"{device.name}: {status}"

                    # Power consumption for MSS310/MSS315 (with voltage and
                    # amperage)
                    if device.has_power_meter:
                        try:
                            power = device.get_power()
                            voltage = device.get_voltage()
                            current = device.get_current()

                            if power is not None:
                                # Translators: Current power consumption in
                                # watts.
                                msg += _(", {power} Watt").format(power=power)
                            if voltage is not None:
                                # Translators: Current voltage in volts.
                                msg += _(", {voltage} Volt").format(voltage=voltage)
                            if current is not None:
                                # Translators: Current amperage in amps.
                                msg += _(", {current} Ampere").format(current=current)
                        except Exception as e:
                            log.debug(f"Strommessung nicht verfügbar für {device.name}: {e}")

                    msg += ". "

            except Exception as e:
                log.warning(f"Fehler beim Abrufen der Daten für {device.name}: {e}")
                # Translators: Generic fallback when the status query for a
                # single device fails.
                msg += f"{device.name}: {_('Fehler')}. "
        
        log.debug(f"Statusnachricht fertig: {msg[:100]}...")
        ui.message(msg)
    
    def _start_status_beep(self):
        """Starts the periodic beep during the status query"""
        self._status_beep_active = True
        # First beep immediately
        _beep(BEEP_LOADING)

        def beep_loop():
            while self._status_beep_active:
                time.sleep(1)
                if self._status_beep_active:
                    wx.CallAfter(_beep, BEEP_LOADING)

        threading.Thread(target=beep_loop, daemon=True).start()
    
    def _stop_status_beep(self):
        """Stops the periodic beep"""
        self._status_beep_active = False
    
    def refresh_devices(self):
        """Refreshes the device list (for the dialog) - status update ONLY, no discovery!"""
        try:
            with self._devices_lock:
                by_platform = split_by_platform(self.devices)
                meross_devs = by_platform['meross']
                netatmo_devs = by_platform['netatmo']
                vesync_devs = by_platform['vesync']
                cozytouch_devs = by_platform['cozytouch']

            # Update the Meross status
            if self.api and self.is_logged_in and self.use_meross:
                if meross_devs:
                    log.debug(f"Aktualisiere Status von {len(meross_devs)} Meross-Geräten...")
                    self.api.update_device_status(meross_devs)
                else:
                    log.debug("Keine Meross-Geräte vorhanden - führe Discovery aus...")
                    meross_devs = self.api.get_devices()
                    self.api.set_wrapped_devices(meross_devs)

            # Update Netatmo (fetch new data)
            if self.netatmo_api and self.use_netatmo:
                log.debug("Aktualisiere Netatmo-Geräte...")
                try:
                    netatmo_devs = self.netatmo_api.get_devices()
                except Exception as e:
                    # Do not escalate Netatmo errors - use the last known
                    # devices
                    log.debug(f"Netatmo Refresh fehlgeschlagen: {e}")

            # Update VeSync (status of the existing devices; discovery if
            # needed)
            if self.vesync_api and self.use_vesync:
                if vesync_devs:
                    log.debug(f"Aktualisiere Status von {len(vesync_devs)} VeSync-Geräten...")
                    try:
                        self.vesync_api.update_device_status(vesync_devs)
                        # Update the snapshots (no "external" trigger, since
                        # done at the user's explicit request)
                        for vd in vesync_devs:
                            self._previous_vesync_states[vd.uuid] = self._snapshot_vesync_state(vd)
                    except Exception as e:
                        log.debug(f"VeSync Status-Refresh fehlgeschlagen: {e}")
                else:
                    log.debug("Keine VeSync-Geräte vorhanden - führe Discovery aus...")
                    try:
                        vesync_devs = self.vesync_api.get_devices()
                        for vd in vesync_devs:
                            self._previous_vesync_states[vd.uuid] = self._snapshot_vesync_state(vd)
                    except Exception as e:
                        log.debug(f"VeSync Discovery fehlgeschlagen: {e}")

            # Update Cozytouch (status of the existing devices; discovery if
            # needed)
            if self.cozytouch_api and self.use_cozytouch:
                if cozytouch_devs:
                    log.debug(f"Aktualisiere Status von {len(cozytouch_devs)} Cozytouch-Geräten...")
                    try:
                        self.cozytouch_api.update_device_status(cozytouch_devs)
                        # Update the snapshots (no "external" trigger, since
                        # done at the user's explicit request)
                        for cd in cozytouch_devs:
                            self._previous_cozytouch_states[cd.uuid] = self._snapshot_cozytouch_state(cd)
                    except Exception as e:
                        log.debug(f"Cozytouch Status-Refresh fehlgeschlagen: {e}")
                else:
                    log.debug("Keine Cozytouch-Geräte vorhanden - führe Discovery aus...")
                    try:
                        cozytouch_devs = self.cozytouch_api.get_devices()
                        for cd in cozytouch_devs:
                            self._previous_cozytouch_states[cd.uuid] = self._snapshot_cozytouch_state(cd)
                    except Exception as e:
                        log.debug(f"Cozytouch Discovery fehlgeschlagen: {e}")

            with self._devices_lock:
                new_list = meross_devs + netatmo_devs + vesync_devs + cozytouch_devs
                # Keep devices that a scheduler poll running in parallel
                # discovered/appended between the snapshot and the reassignment
                # (otherwise a lost update: they would silently drop out).
                known_uuids = {d.uuid for d in new_list}
                for d in self.devices:
                    if d.uuid not in known_uuids:
                        new_list.append(d)
                self.devices = new_list
            self._last_refresh_time = time.time()
            log.debug(f"Geräte-Refresh abgeschlossen: {len(self.devices)} Geräte")
            return self.devices
        except Exception as e:
            log.error(f"Fehler beim Aktualisieren der Geräte: {e}")
            raise
    
    def _log_toggle(self, target, new_state):
        """Schreibt einen Schaltvorgang in den Verlauf.

        Bewusst hier und nicht in den aufrufenden Oberflächen: toggle_device()
        ist der gemeinsame Engpass BEIDER Bedienwege (Geräte-Dialog und
        Favoriten-Direktgesten). Früher protokollierte nur der Dialog - über
        eine Favoriten-Geste geschaltete Geräte tauchten im Verlauf gar nicht
        auf, obwohl dasselbe Gerät über das Menü geschaltet dort erschien.
        """
        try:
            from .history import get_history, SOURCE_LOCAL
            get_history().log_action(
                target,
                'toggle_on' if new_state else 'toggle_off',
                # Translators: Detail column of a switch action in the history.
                _('Ein') if new_state else _('Aus'),
                source=SOURCE_LOCAL,
            )
        except Exception as e:
            # Der Verlauf darf das Schalten nie verhindern.
            log.debug(f"Verlaufseintrag fehlgeschlagen: {e}")

    def toggle_device(self, device_uuid, channel=None):
        """
        Toggles a device or channel

        Args:
            device_uuid: UUID of the device (or "uuid_chX" for channels)
            channel: channel index (optional, extracted from the UUID if not given)
        """
        try:
            # Lookup under the lock (the list can be reassigned during a
            # refresh)
            with self._devices_lock:
                # Check whether it is a channel UUID (format: "uuid_chX").
                # Nur als Kanal behandeln, wenn nach "_ch" wirklich eine Zahl
                # folgt - eine Geräte-UUID kann selbst "_ch" enthalten.
                parts = device_uuid.rsplit("_ch", 1) if channel is None else None
                if parts and len(parts) == 2 and parts[1].isdigit():
                    parent_uuid = parts[0]
                    channel = int(parts[1])
                    device = next((d for d in self.devices if d.uuid == parent_uuid), None)
                else:
                    device = next((d for d in self.devices if d.uuid == device_uuid), None)

            if not device:
                # Translators: Error message when a device can no longer be
                # found in the device list via its UUID (e.g. after a reload).
                raise ValueError(_("Gerät nicht gefunden"))

            # VeSync devices: their own toggle logic
            if getattr(device, 'is_vesync', False):
                new_state = not device.is_on
                device.toggle_switch(new_state)
                status = _("ein") if new_state else _("aus")
                self._record_local_toggle(device_uuid, new_state)
                # Also mark the local action for the push detection so the next
                # background refresh does not announce the user's own switching
                # as an external change.
                self._record_local_vesync_action(device_uuid)
                self._log_toggle(device, new_state)
                return _("{name}: {status}").format(name=device.name, status=status)

            # Cozytouch devices: hot water production on/off (analogous to the
            # dialog mixin)
            if getattr(device, 'is_cozytouch', False):
                new_state = not device.is_on
                if not device.set_dhw(new_state):
                    # Translators: Error message when toggling hot water
                    # production fails.
                    raise RuntimeError(_("Warmwasser konnte nicht umgeschaltet werden"))
                status = _("ein") if new_state else _("aus")
                self._record_local_toggle(device_uuid, new_state)
                self._record_local_cozytouch_action(device_uuid)
                self._log_toggle(device, new_state)
                return _("{name}: {status}").format(name=device.name, status=status)

            # Determine the current status
            if channel is not None:
                # Channel mode
                current_state = device._device.is_on(channel=channel) if hasattr(device._device, 'is_on') else False
            else:
                current_state = device.is_on

            new_state = not current_state
            # self.api kann None sein (Meross deaktiviert, Gerät aber noch in
            # der Liste) - dann saubere Meldung statt AttributeError.
            if not self.api:
                raise RuntimeError(_("Nicht angemeldet"))
            self.api.set_device_state(device.uuid, new_state, channel=channel)
            
            # Update the status
            log_target = device
            if channel is not None:
                # For channels: find the channel object and update it
                channels = device.get_channels()
                for ch in channels:
                    if ch.channel_index == channel:
                        ch._is_on = new_state
                        device_name = ch.name
                        log_target = ch
                        break
                else:
                    # Translators: Fallback display name for a device channel.
                    device_name = _("{name} Kanal {number}").format(
                        name=device.name, number=channel)
            else:
                device._is_on = new_state
                device_name = device.name

            status = _("ein") if new_state else _("aus")
            self._record_local_toggle(device_uuid, new_state)
            # Bei Kanälen das Kanal-Objekt protokollieren, damit im Verlauf
            # "Garten: Ausgang Pumpe" steht und nicht nur "Garten".
            self._log_toggle(log_target if channel is not None else device,
                             new_state)
            return _("{name}: {status}").format(name=device_name, status=status)

        except (TimeoutError, ConnectionError, OSError) as e:
            # Network/timeout errors: only WARNING (not ERROR) - the dialog
            # shows a message
            log.warning(f"Fehler beim Umschalten: {e}")
            raise
        except Exception as e:
            log.error(f"Fehler beim Umschalten: {e}")
            raise
    
    def set_diffuser_mode(self, device_uuid, mode_action):
        """
        Sets the spray mode of a diffuser

        Args:
            device_uuid: UUID of the diffuser
            mode_action: 'diffuser_light', 'diffuser_strong', or 'diffuser_off'
        """
        try:
            with self._devices_lock:
                device = next((d for d in self.devices if d.uuid == device_uuid), None)

            if not device:
                raise ValueError(_("Gerät nicht gefunden"))

            if not device.is_diffuser:
                # Translators: Error message when a diffuser action is
                # accidentally executed on another device type.
                raise ValueError(_("Gerät ist kein Diffuser"))

            # Convert the action to a spray mode
            from meross_iot.model.enums import DiffuserSprayMode
            mode_map = {
                'diffuser_light': DiffuserSprayMode.LIGHT,
                'diffuser_strong': DiffuserSprayMode.STRONG,
                'diffuser_off': DiffuserSprayMode.OFF
            }

            if mode_action not in mode_map:
                # Translators: Error message for an unknown diffuser action.
                raise ValueError(_("Ungültige Diffuser-Aktion: {action}").format(action=mode_action))

            spray_mode = mode_map[mode_action]

            # Set the mode via the API
            self.api.set_diffuser_spray_mode(device.uuid, spray_mode)

            # Update the status
            device._update_status()

            # Verlauf: wie beim Schalten am gemeinsamen Engpass, damit auch
            # ein Aufruf außerhalb des Dialogs protokolliert wird.
            try:
                from .history import get_history, SOURCE_LOCAL
                from .constants import DIFFUSER_MODE_NAMES
                get_history().log_action(
                    device, mode_action,
                    DIFFUSER_MODE_NAMES.get(mode_action, mode_action),
                    source=SOURCE_LOCAL)
            except Exception as e:
                log.debug(f"Verlaufseintrag fehlgeschlagen: {e}")

            # Translators: Success feedback after a diffuser mode change.
            return _("{name}: Modus gesetzt").format(name=device.name)
            
        except Exception as e:
            log.error(f"Fehler beim Setzen des Diffuser-Modus: {e}")
            raise



# Hinweis: Die früheren 18 Einzel-Skripte ("Favorit N umschalten" /
# "Status von Favorit N ansagen") sind durch die Favoriten-Ebene ersetzt
# (script_favoritesLayer in der Klasse): eine Geste, dann Ziffer 1-9.
# Verwaiste gestures.ini-Einträge auf die alten Skriptnamen sind harmlos -
# NVDA ignoriert Bindungen an nicht existierende Skripte.
