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
# IMPORTANT: there is NO pure Python fallback for these packages. Earlier
# versions of this comment promised one ("slower, but functional") - but in
# the built package aiohttp, multidict, yarl, frozenlist, propcache,
# charset_normalizer and Cryptodome exist only under lib/_arch/. If no
# architecture matches, there is not a slower way but none at all: Meross
# support drops out. The two bundled architectures cover NVDA 2025.1 through
# 2026.1 completely; a third case is hypothetical but should then be reported
# honestly instead of passing as a "fallback".
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
                f"Smart Home Control: Python {py.major}.{py.minor} differs from the "
                f"version built for {expected} - the compiled extensions may "
                f"fail to load."
            )
        return candidate
    log.error(
        f"Smart Home Control: no matching _arch folder ({expected}) found. "
        f"Meross support is NOT available on this NVDA/Python version (there "
        f"is no pure Python replacement for aiohttp and Cryptodome). The "
        f"other platforms - Netatmo, VeSync and Cozytouch - work unchanged."
    )
    return None

addon_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
lib_path = os.path.join(addon_path, "lib")
_arch_dir = _select_arch_dir(os.path.join(lib_path, "_arch"))
# Append the arch folder BEFORE lib so the compiled packages are found there;
# pure Python packages (requests, idna, meross_iot, ...) come from lib/.
if _arch_dir and _arch_dir not in sys.path:
    sys.path.append(_arch_dir)
    log.debug(f"Smart Home Control: arch path appended: {_arch_dir}")
if lib_path not in sys.path:
    sys.path.append(lib_path)
    log.debug(f"Smart Home Control: lib path appended: {lib_path}")

# Initialize the add-on.
# Guarded like every other module in this package: an unguarded failure here
# would abort the import of the WHOLE add-on instead of just losing the
# translations.
try:
    addonHandler.initTranslation()
except Exception as e:
    log.debug(f"initTranslation failed: {e}")
if "_" not in globals():  # fallback outside of NVDA
    def _(s):
        return s

from .meross_api import MerossAPI
from .netatmo_api import NetatmoAPI
from .vesync_api import VeSyncAPI
from .cozytouch_api import CozytouchAPI
from .device_dialog import SmartHomeControlDialog
from .settings_panel import (
    SmartHomeSettingsDialog, is_credentials_error, login_error_message,
    offer_credential_reentry,
)
from .security import encrypt_dpapi, decrypt_dpapi, is_encrypted
from .credentials import _CredentialsMixin
from .scheduler import _SchedulerMixin
from .change_detection import _ChangeDetectionMixin
from .platform_utils import (
    split_by_platform, platform_of, PLATFORM_LABELS, PASSWORD_PLATFORMS,
)
from . import credential_store


def _addon_version():
    """The version from the add-on's own manifest.ini, for the log.

    Read from the file rather than through addonHandler: this also works
    when the add-on runs from a source folder that NVDA never registered,
    which is how it is developed. Any failure gives "unknown" - a missing
    version line must not stop the add-on from starting.
    """
    try:
        manifest = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "manifest.ini")
        with open(manifest, encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith("version"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception as e:
        log.debug(f"Add-on version could not be read: {e}")
    return "unknown"
from .dialog_helpers import _beep
from .constants import (
    CONFSPEC, BEEP_ERROR, BEEP_SUCCESS, BEEP_LOADING, BEEP_ACTION,
    BEEP_ON, BEEP_OFF, NETATMO_REDIRECT_PORT,
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

    Split into mixins (behaviour unchanged):
      - _CredentialsMixin     (credentials.py): encrypted password properties
      - _SchedulerMixin       (scheduler.py): polling scheduler + platform refresh
      - _ChangeDetectionMixin (change_detection.py): external change detection
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
        # Coalesces cloud refreshes: the scheduler pass and refresh_devices()
        # (dialog) do the SAME work. Without this lock, opening the dialog ran
        # both at once - three Meross status rounds within 13 s were visible in
        # the log, which tripled the cloud budget and made the dialog wait for
        # the second round. A second caller now waits for the running refresh
        # and uses its result instead of starting another one.
        self._refresh_lock = threading.Lock()
        # Per platform the time of the last completed refresh - regardless of
        # which path did it. The scheduler uses it to re-schedule instead of
        # repeating a poll the dialog has just made (see _scheduler_body).
        self._platform_last_refresh = {}
        # Which platforms are logging in right now. A Meross login takes
        # fifteen seconds for a large account, and the API only appears when
        # it is through - a second Save in that window used to start a second
        # login, and two sessions then polled the cloud in parallel.
        self._logging_in = set()
        self._login_lock = threading.Lock()
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

        # Water sensors (MS400/MS405): last known state per device, so only
        # the CHANGE is announced and logged - not every poll.
        self._previous_water_states = {}

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
        log.info("Smart Home Control: add-on started")
        # The version, from the add-on's own manifest. NVDA logs one too,
        # but a tester's report is read by searching this block, and a
        # round of testing has already been spent on a package that was
        # not the one meant: two builds carried the same version, and
        # neither the log nor the tester could tell them apart.
        log.info(f"Add-on version: {_addon_version()}")
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
        
        log.info("Smart Home Control: initialisation finished")
    
    def terminate(self):
        """Cleanup when NVDA exits"""
        log.info("Smart Home Control: add-on is shutting down")
        # Tear down a still active favorites layer, otherwise the capture
        # function stays installed in inputCore beyond the add-on's end (for
        # instance when the add-on is restarted).
        try:
            self._fav_layer_exit()
        except Exception as e:
            log.debug(f"Favorites layer during shutdown: {e}")
        # Save unsaved history entries from the debounce window.
        try:
            from .history import flush_pending
            flush_pending()
        except Exception as e:
            log.debug(f"History flush during shutdown failed: {e}")
        # Save unsaved energy samples as well.
        try:
            from .energy import flush_pending as flush_energy
            flush_energy()
        except Exception as e:
            log.debug(f"Energy flush during shutdown failed: {e}")
        # Stop the unified scheduler thread.
        self._stop_background_refresh()

        # Wait for the thread to end cleanly (best effort).
        t = getattr(self, '_background_refresh_thread', None)
        if t is not None and t.is_alive():
            try:
                t.join(timeout=2.0)
                if t.is_alive():
                    log.debug("Scheduler thread could not be stopped within 2s")
            except Exception as e:
                log.debug(f"Join of the scheduler thread failed: {e}")

        if self.api:
            try:
                self.api.logout()
            except Exception as e:
                log.debug(f"Ignored error in terminate: {e}")
        if self.netatmo_api:
            try:
                self.netatmo_api.logout()
            except Exception as e:
                log.debug(f"Ignored error in terminate: {e}")
        if self.vesync_api:
            try:
                self.vesync_api.logout()
            except Exception as e:
                log.debug(f"Ignored error in terminate: {e}")
        if self.cozytouch_api:
            try:
                self.cozytouch_api.logout()
            except Exception as e:
                log.debug(f"Ignored error in terminate: {e}")
        super().terminate()
    
    def load_settings(self):
        """Load the settings from the NVDA config"""
        try:
            conf = config.conf["smartHomeControl"]
            # Credentials do not live in nvda.ini: NVDA writes the whole
            # configuration into the log at startup, and the email address
            # stood there in plain text. credential_store keeps them in a
            # file of its own and moves them out of the configuration the
            # first time it runs.
            secrets = credential_store.load(conf)
            self.email = secrets.get("email", "")
            
            # Keep the password encrypted in memory (never as plain text). It
            # is only decrypted on demand at login time via the password
            # property. set_encrypted_password() checks the format cleanly (no
            # blind prefix heuristic).
            self.set_encrypted_password(secrets.get("password", ""))
            
            self.auto_login = conf.get("autoLogin", True)
            self.announce_external_changes = conf.get("announceExternalChanges", True)
            self.start_tab = conf.get("startTab", "devices")

            # Platform flags
            self.use_meross = conf.get("useMeross", False)
            self.use_netatmo = conf.get("useNetatmo", False)
            self.use_vesync = conf.get("useVesync", False)

            # Fine-grained notification settings ("Notifications" tab)
            self.notify_meross_toggle = conf.get("notifyMerossToggle", True)
            self.notify_meross_water = conf.get("notifyMerossWater", True)
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
            self.notify_vesync_cook = conf.get("notifyVesyncCook", True)
            
            # Netatmo credentials (all secrets encrypted)
            raw_client_id = secrets.get("netatmoClientId", "")
            if raw_client_id and is_encrypted(raw_client_id):
                self.netatmo_client_id = decrypt_dpapi(raw_client_id)
            else:
                # Legacy: plain text, will be encrypted on the next save
                self.netatmo_client_id = raw_client_id
            encrypted_secret = secrets.get("netatmoClientSecret", "")
            self.netatmo_client_secret = decrypt_dpapi(encrypted_secret) if encrypted_secret else ""
            
            encrypted_access = secrets.get("netatmoAccessToken", "")
            self.netatmo_access_token = decrypt_dpapi(encrypted_access) if encrypted_access else ""
            encrypted_refresh = secrets.get("netatmoRefreshToken", "")
            self.netatmo_refresh_token = decrypt_dpapi(encrypted_refresh) if encrypted_refresh else ""
            self.netatmo_token_expiry = conf.get("netatmoTokenExpiry", 0)
            self.netatmo_redirect_port = conf.get("netatmoRedirectPort", NETATMO_REDIRECT_PORT)

            # VeSync credentials (password and token encrypted)
            self.vesync_email = secrets.get("vesyncEmail", "")
            self.set_encrypted_vesync_password(secrets.get("vesyncPassword", ""))
            self.vesync_country_code = conf.get("vesyncCountryCode", "DE") or "DE"
            encrypted_vs_token = secrets.get("vesyncToken", "")
            self.vesync_token = decrypt_dpapi(encrypted_vs_token) if encrypted_vs_token else ""
            encrypted_vs_account = secrets.get("vesyncAccountId", "")
            self.vesync_account_id = decrypt_dpapi(encrypted_vs_account) if encrypted_vs_account else ""
            self.vesync_region = conf.get("vesyncRegion", "")
            self.vesync_filter_threshold = conf.get("vesyncFilterThreshold", 15)
            from .constants import FAV_LAYER_SWITCH_WINDOW_DEFAULT
            self.fav_layer_switch_window = conf.get(
                "favLayerSwitchWindow", FAV_LAYER_SWITCH_WINDOW_DEFAULT)

            # Cozytouch credentials (password and token encrypted)
            self.use_cozytouch = conf.get("useCozytouch", False)
            self.cozytouch_email = secrets.get("cozytouchEmail", "")
            self.set_encrypted_cozytouch_password(secrets.get("cozytouchPassword", ""))
            encrypted_ct_token = secrets.get("cozytouchToken", "")
            self.cozytouch_token = decrypt_dpapi(encrypted_ct_token) if encrypted_ct_token else ""
            self.cozytouch_capacity_liters = conf.get("cozytouchCapacityLiters", 0)
            self.notify_cozytouch_mode = conf.get("notifyCozytouchMode", True)
            self.notify_cozytouch_temp = conf.get("notifyCozytouchTemp", True)
            self.notify_cozytouch_boost = conf.get("notifyCozytouchBoost", True)
            self.notify_cozytouch_power = conf.get("notifyCozytouchPower", True)
            self.notify_cozytouch_away = conf.get("notifyCozytouchAway", True)

            log.debug(f"Settings loaded: Meross={self.use_meross}, Netatmo={self.use_netatmo}, VeSync={self.use_vesync}, Cozytouch={self.use_cozytouch}, auto login={self.auto_login}")
        except Exception as e:
            log.error(f"Failed to load the settings: {e}")
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
            self.notify_meross_water = True
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
            self.notify_vesync_cook = True
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
            from .constants import FAV_LAYER_SWITCH_WINDOW_DEFAULT
            self.fav_layer_switch_window = FAV_LAYER_SWITCH_WINDOW_DEFAULT
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
            # The secrets go into the credential store, not into the
            # configuration - see load_settings. The dictionary is filled
            # here and written in one go further down.
            secrets = {}
            secrets["email"] = self.email
            
            # Password (already encrypted in memory - store directly)
            secrets["password"] = self._encrypted_password if self._encrypted_password else ""
            
            conf["autoLogin"] = self.auto_login
            conf["announceExternalChanges"] = self.announce_external_changes
            conf["startTab"] = getattr(self, 'start_tab', 'devices')

            # Platform flags
            conf["useMeross"] = self.use_meross
            conf["useNetatmo"] = self.use_netatmo
            conf["useVesync"] = self.use_vesync

            # Fine-grained notification settings
            conf["notifyMerossToggle"] = self.notify_meross_toggle
            conf["notifyMerossWater"] = self.notify_meross_water
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
            conf["notifyVesyncCook"] = self.notify_vesync_cook
            
            # Netatmo credentials (all IDs/secrets/tokens encrypted)
            secrets["netatmoClientId"] = encrypt_dpapi(self.netatmo_client_id) if self.netatmo_client_id else ""
            secrets["netatmoClientSecret"] = encrypt_dpapi(self.netatmo_client_secret) if self.netatmo_client_secret else ""
            secrets["netatmoAccessToken"] = encrypt_dpapi(self.netatmo_access_token) if self.netatmo_access_token else ""
            secrets["netatmoRefreshToken"] = encrypt_dpapi(self.netatmo_refresh_token) if self.netatmo_refresh_token else ""
            conf["netatmoTokenExpiry"] = self.netatmo_token_expiry
            conf["netatmoRedirectPort"] = self.netatmo_redirect_port

            # VeSync credentials (password and token encrypted)
            secrets["vesyncEmail"] = self.vesync_email
            secrets["vesyncPassword"] = self._encrypted_vesync_password if self._encrypted_vesync_password else ""
            conf["vesyncCountryCode"] = self.vesync_country_code or "DE"
            secrets["vesyncToken"] = encrypt_dpapi(self.vesync_token) if self.vesync_token else ""
            secrets["vesyncAccountId"] = encrypt_dpapi(self.vesync_account_id) if self.vesync_account_id else ""
            conf["vesyncRegion"] = self.vesync_region or ""
            conf["vesyncFilterThreshold"] = self.vesync_filter_threshold
            conf["favLayerSwitchWindow"] = self.fav_layer_switch_window

            # Cozytouch credentials (password and token encrypted)
            conf["useCozytouch"] = self.use_cozytouch
            secrets["cozytouchEmail"] = self.cozytouch_email
            secrets["cozytouchPassword"] = self._encrypted_cozytouch_password if self._encrypted_cozytouch_password else ""
            secrets["cozytouchToken"] = encrypt_dpapi(self.cozytouch_token) if self.cozytouch_token else ""
            conf["cozytouchCapacityLiters"] = self.cozytouch_capacity_liters
            # Everything collected above goes into the credential store, and
            # the same call clears whatever is still standing in nvda.ini.
            # A failure only means the file stays as it was - the values are
            # in memory and the next save tries again.
            credential_store.save(conf, secrets)
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
            log.debug("Settings saved (credentials DPAPI encrypted)")
        except Exception as e:
            log.error(f"Failed to save the settings: {e}")

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
                log.error(f"Failed to write the configuration: {e}")

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
            log.warning("VeSync re-auth not possible - no credentials stored")
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
            log.info("VeSync re-auth successful")
            return True
        except Exception as e:
            _safe_log_error("VeSync re-auth failed", e)
            return False

    def _cozytouch_reauth(self, api):
        """Reauth callback for Cozytouch on an expired token.

        Called by ``CozytouchAPI._request`` on HTTP 401/403. Attempts a
        password login with the stored credentials and saves the fresh token
        in the NVDA configuration.
        """
        if not (self.cozytouch_email and self._encrypted_cozytouch_password):
            log.warning("Cozytouch re-auth not possible - no credentials stored")
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
            log.info("Cozytouch re-auth successful")
            return True
        except Exception as e:
            _safe_log_error("Cozytouch re-auth failed", e)
            return False

    def begin_platform_login(self, platform):
        """Claims the login slot of a platform.

        Returns False when a login for that platform is already running - the
        caller then does nothing. Without this a second Save during a running
        Meross login started a second one, and both sessions stayed alive
        afterwards: doubled cloud traffic against the hourly budget and every
        push notification arriving twice.
        """
        with self._login_lock:
            if platform in self._logging_in:
                return False
            self._logging_in.add(platform)
            return True

    def end_platform_login(self, platform):
        """Releases the login slot - always, including after a failure."""
        with self._login_lock:
            self._logging_in.discard(platform)

    def replace_platform_devices(self, platform, devices):
        """Swaps the devices of ONE platform in the shared list.

        Used after a login with changed credentials: the devices of the old
        session must go, not just be joined by the new ones - otherwise a
        device that was renamed or removed in the account stays in the tree,
        and every device of a swapped account appears twice.

        Runs under the lock, since the scheduler thread reads and writes the
        list in parallel.

        Returns:
            int: how many devices the platform now contributes.
        """
        from .platform_utils import platform_of
        with self._devices_lock:
            others = [d for d in self.devices if platform_of(d) != platform]
            self.devices = others + list(devices)
        return len(devices)

    def _start_auto_login(self, interactive=False):
        """Starts the automatic login in the background.

        Args:
            interactive: True when the login follows an action by the user
                (settings saved). Only then may a refused login ask for the
                credentials again - at NVDA start a dialog would pop up
                unasked, typically because the network is not up yet.
        """
        log.info("Starting auto login...")
        platforms = []
        if self.use_meross:
            platforms.append("Meross")
        if self.use_netatmo:
            platforms.append("Netatmo")
        if self.use_vesync:
            platforms.append("VeSync")
        if self.use_cozytouch:
            platforms.append("Cozytouch")
        # Translators: Spoken while the add-on signs in to the platforms at
        # startup.
        ui.message(_("Logging in to {platforms}...").format(platforms=", ".join(platforms)))
        threading.Thread(
            target=self._do_login, args=(interactive,), daemon=True).start()

    def _do_login(self, interactive=False):
        """Performs the login (in a separate thread) - Meross and/or Netatmo and/or VeSync"""
        try:
            log.info("_do_login: login process starting...")
            self.is_loading = True
            meross_devices = []
            netatmo_devices = []
            vesync_devices = []
            cozytouch_devices = []
            # Platforms that refused the credentials. Collected instead of
            # asked about immediately: the remaining platforms should still
            # get their chance before a dialog interrupts.
            refused = []

            # ---- Meross login ----
            if self.use_meross and self.email and self._encrypted_password:
                try:
                    # Translators: Spoken while the Meross account is being
                    # signed in to.
                    wx.CallAfter(ui.message, _("Meross: connecting..."))
                    log.info("Connecting to the Meross server...")
                    
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
                    log.info("Meross login successful")
                    
                    # Translators: Spoken while the Meross device list is being
                    # fetched.
                    wx.CallAfter(ui.message, _("Meross: loading devices..."))
                    meross_devices = new_api.get_devices()
                    new_api.set_wrapped_devices(meross_devices)
                    old_api, self.api = self.api, new_api
                    if old_api is not None:
                        try:
                            old_api.logout()
                        except Exception as e:
                            log.debug(f"Logout of the old Meross instance failed: {e}")
                    log.info(f"Meross: {len(meross_devices)} device(s) found")
                    
                except Exception as e:
                    # No exc_info -> avoids token/header leaks in the log.
                    _safe_log_error("Meross login failed", e)
                    self._note_login_failure('meross', e, refused)
            
            # ---- Netatmo login ----
            if self.use_netatmo and self.netatmo_client_id and self.netatmo_refresh_token:
                try:
                    # Translators: Spoken while the Netatmo account is being
                    # signed in to.
                    wx.CallAfter(ui.message, _("Netatmo: connecting..."))
                    log.info("Connecting to Netatmo...")
                    
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
                    # EVERY later renewal must arrive here as well - Netatmo
                    # rotates refresh tokens, so without this the config kept
                    # the ones from the login and a restart hours later
                    # restored an already invalidated refresh token.
                    new_netatmo.set_token_update_callback(
                        self._on_netatmo_tokens_renewed)

                    # Renew the token if necessary
                    new_netatmo._ensure_valid_token()

                    # Tokens possibly renewed - save them
                    tokens = new_netatmo.get_tokens()
                    if tokens['access_token'] != self.netatmo_access_token:
                        self._on_netatmo_tokens_renewed(tokens)

                    netatmo_devices = new_netatmo.get_devices()
                    self.netatmo_api = new_netatmo
                    log.info(f"Netatmo: {len(netatmo_devices)} device(s) found")
                    
                except Exception as e:
                    _safe_log_error("Netatmo login failed", e)
                    self._note_login_failure('netatmo', e, refused)
            
            # ---- VeSync login ----
            if self.use_vesync:
                try:
                    # Translators: Spoken while the VeSync account is being
                    # signed in to.
                    wx.CallAfter(ui.message, _("VeSync: connecting..."))
                    log.info("Connecting to VeSync...")

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
                        raise RuntimeError(_("No VeSync credentials configured"))

                    # Translators: Spoken while the VeSync device list is being
                    # fetched.
                    wx.CallAfter(ui.message, _("VeSync: loading devices..."))
                    try:
                        vesync_devices = vs_api.get_devices()
                    except RuntimeError as e:
                        # Token possibly expired -> retry with the password
                        # login
                        if (have_tokens and self.vesync_email
                                and self._encrypted_vesync_password):
                            log.info(f"VeSync: token login failed ({e}) - trying the password login")
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
                    log.info(f"VeSync: {len(vesync_devices)} device(s) found")

                except Exception as e:
                    _safe_log_error("VeSync login failed", e)
                    self._note_login_failure('vesync', e, refused)

            # ---- Cozytouch login (Atlantic / Austria Email) ----
            if self.use_cozytouch and self.cozytouch_email and self._encrypted_cozytouch_password:
                try:
                    # Translators: Spoken while the Cozytouch account is being
                    # signed in to.
                    wx.CallAfter(ui.message, _("Cozytouch: connecting..."))
                    log.info("Connecting to Cozytouch...")

                    ct_api = CozytouchAPI()
                    if hasattr(ct_api, 'set_reauth_callback'):
                        ct_api.set_reauth_callback(self._cozytouch_reauth)

                    _ct_password = self.cozytouch_password
                    try:
                        ct_api.login(self.cozytouch_email, _ct_password)
                    finally:
                        _ct_password = None
                        del _ct_password

                    # Translators: Spoken while the Cozytouch device list is
                    # being fetched.
                    wx.CallAfter(ui.message, _("Cozytouch: loading devices..."))
                    cozytouch_devices = ct_api.get_devices()
                    self.cozytouch_api = ct_api

                    # Save the fresh token
                    creds = ct_api.get_credentials()
                    if creds.get("token"):
                        self.cozytouch_token = creds["token"]
                        self.save_settings()
                    log.info(f"Cozytouch: {len(cozytouch_devices)} device(s) found")

                except Exception as e:
                    _safe_log_error("Cozytouch login failed", e)
                    self._note_login_failure('cozytouch', e, refused)

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
                    log.warning("Re-login returned no devices - the existing session stays active")
                    # Translators: Announcement when a renewed login fails but
                    # the previous device list remains in use.
                    wx.CallAfter(ui.message, _("Login failed – existing "
                                               "devices remain available"))
                    return

            with self._devices_lock:
                self.devices = new_devices

            # Create the initial VeSync snapshot so the first refresh does not
            # report the existing state as an "external change".
            for vd in vesync_devices:
                self._previous_vesync_states[vd.uuid] = self._snapshot_vesync_state(vd)
            for cd in cozytouch_devices:
                self._previous_cozytouch_states[cd.uuid] = self._snapshot_cozytouch_state(cd)

            # Via the local list instead of self.devices: the reference was
            # just assigned under the lock, which avoids an unguarded read.
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
                    "{total} devices ready ({detail})").format(
                    total=total, detail=detail))
            else:
                self.is_logged_in = False
                # Translators: Announced when the login succeeded but there are
                # no devices in the account.
                wx.CallAfter(ui.message, _("No devices found"))

        except Exception as e:
            _safe_log_error("Login failed", e)
            with self._devices_lock:
                has_existing = bool(self.devices)
            if self.is_logged_in and has_existing:
                # Do not discard the existing session because of a failed re-
                # login (session protection, see above).
                log.warning("Re-login failed - the existing session stays active")
            else:
                self.is_logged_in = False
        finally:
            self.is_loading = False
            # In the finally block on purpose: the session protection above
            # returns early, and a refused password has to be reported from
            # that path too.
            if interactive and refused:
                platform, error = refused[0]
                wx.CallAfter(self._ask_credential_reentry, platform, error)

    def _note_login_failure(self, platform, error, refused):
        """Announces a failed login and notes a refused credential.

        Only a refusal of the credentials themselves is noted; a timeout or a
        missing network must not lead to a question about the password.
        """
        wx.CallAfter(ui.message, login_error_message(platform, error))
        if platform in PASSWORD_PLATFORMS and is_credentials_error(error):
            refused.append((platform, error))

    def _ask_credential_reentry(self, platform, error):
        """Offers to enter the refused credentials again (main thread)."""
        def _after_save(changed):
            if changed:
                self._start_auto_login(interactive=True)

        # prePopup/postPopup as for every dialog of this add-on: NVDA needs
        # them to hand the focus over and give it back afterwards.
        gui.mainFrame.prePopup()
        try:
            offer_credential_reentry(
                gui.mainFrame, self, platform, error, on_saved=_after_save)
        except Exception as e:
            log.error(f"Credential re-entry failed: {type(e).__name__}: {e}")
        finally:
            gui.mainFrame.postPopup()


    @scriptHandler.script(
        # Translators: Description of the script in the NVDA input gestures
        # dialog.
        description=_("Opens the Smart Home Control menu"),
        gesture="kb:NVDA+shift+h",
    )
    def script_openSmartMenu(self, gesture):
        """Opens the main menu with the device controls"""
        
        if self.is_loading:
            # Translators: Spoken when the device menu is opened while the
            # sign-in is still running.
            ui.message(_("Login in progress..."))
            return
        
        if not self.is_logged_in:
            # Not logged in yet - open the settings
            # Translators: Spoken when the device menu is opened while no
            # platform is signed in - the settings open instead.
            ui.message(_("Not logged in – opening settings"))
            wx.CallAfter(self._show_settings)
            return
        
        # Open the device dialog
        wx.CallAfter(self._show_device_dialog)
    
    def _show_device_dialog(self):
        """Shows the device control dialog.

        Opening it twice used to build a second dialog on top of the first.
        The menu is modal, but a global NVDA gesture reaches the add-on
        anyway, so the shortcut kept working while the menu was open. Two of
        them are not only confusing to listen to: the second one takes over
        ``_active_dialog`` and clears it when it closes, and the dialog left
        standing then silently stopped being updated - the tree in front of
        the user was frozen while the devices went on changing.
        """
        open_dialog = self._active_dialog
        if (open_dialog is not None
                and not getattr(open_dialog, "_is_destroyed", False)):
            # Translators: Spoken when the shortcut for the device menu is
            # pressed while the menu is already open.
            ui.message(_("Device menu is already open"))
            try:
                open_dialog.Raise()
                open_dialog.SetFocus()
            except Exception as e:
                log.debug(f"Could not bring the open device menu forward: {e}")
            return
        # Translators: Spoken when the device menu opens.
        ui.message(_("Opening device overview..."))
        gui.mainFrame.prePopup()
        dlg = SmartHomeControlDialog(gui.mainFrame, self)
        self._active_dialog = dlg  # store the reference for live updates
        # Trigger an immediate poll at the foreground rate so external changes
        # (Levoit app or physical controls) arrive in the dialog quickly. The
        # scheduler detects the open dialog itself and polls at the shorter
        # foreground interval from now on. request_immediate_poll() also
        # wakes the scheduler: it sleeps until the next due poll and would
        # otherwise see the flag only with a delay.
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
        try:
            saved = dlg.ShowModal() == wx.ID_OK
        finally:
            dlg.Destroy()
        if saved:
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
                # interactive: the login follows the save, so a refused
                # password may ask to be entered again.
                self._start_auto_login(interactive=True)
        gui.mainFrame.postPopup()
    
    @scriptHandler.script(
        # Translators: Description of the script in the NVDA input gestures
        # dialog.
        description=_("Opens the Smart Home settings")
    )
    def script_openSettings(self, gesture):
        """Opens the settings dialog"""
        wx.CallAfter(self._show_settings)
    
    @scriptHandler.script(
        # Translators: Description of the script in the NVDA input gestures
        # dialog. No default gesture - the user assigns one if needed.
        description=_("Announces the energy consumption of the metering plugs "
                      "(today and last 7 days)")
    )
    def script_announceEnergy(self, gesture):
        """Announces today's and last week's energy per metering plug.

        The preferred source is the DEVICE's own consumption counter
        (consumptionX), which keeps counting while NVDA is not running. Only
        if a device does not support that query are the power samples
        collected in the background used - marked as "estimated", because
        they only cover NVDA's runtime.
        """
        # Translators: Announced while the energy data is being fetched.
        ui.message(_("Fetching energy data..."))

        def task():
            parts = []
            covered_uuids = set()
            # 1. Device counter (complete, independent of NVDA's runtime).
            # get_daily_consumption is cached gently (15 min TTL), so
            # repeated announcements cost no extra cloud messages.
            if self.api and self.use_meross:
                with self._devices_lock:
                    meters = [d for d in self.devices
                              if getattr(d, 'has_power_meter', False)]
                # One bulk query instead of one blocking call per plug: the
                # single-device variant paid the queueing time on the event
                # loop again for every plug, so with several meters the later
                # ones ran into the timeout and were missing from the report.
                bulk = self.api.get_daily_consumption_bulk([d.uuid for d in meters])
                for dev in meters:
                    data = bulk.get(dev.uuid)
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
                            "{name}: today {today} kilowatt hours, last 7 "
                            "days {week} kilowatt hours, currently {watt} "
                            "watts").format(
                            name=dev.name,
                            today=f"{kwh_today:.2f}".replace(".", ","),
                            week=f"{kwh_week:.2f}".replace(".", ","),
                            watt=f"{watt:g}".replace(".", ",")))
                    else:
                        # Translators: Energy summary from the device's own
                        # meter without a current power value.
                        parts.append(_(
                            "{name}: today {today} kilowatt hours, last 7 "
                            "days {week} kilowatt hours").format(
                            name=dev.name,
                            today=f"{kwh_today:.2f}".replace(".", ","),
                            week=f"{kwh_week:.2f}".replace(".", ",")))
            # 2. Fallback: collected samples for devices without a counter
            try:
                from .energy import get_energy_log
                for uuid, name, kwh_today, kwh_week, last_watt in get_energy_log().summary():
                    if uuid in covered_uuids:
                        continue
                    # Translators: Energy summary estimated from background
                    # samples (only covers the time NVDA was running).
                    parts.append(_(
                        "{name}: today {today} kilowatt hours, last 7 days "
                        "{week} kilowatt hours, currently {watt} watts "
                        "(estimated, only recorded while NVDA was running)").format(
                        name=name,
                        today=f"{kwh_today:.2f}".replace(".", ","),
                        week=f"{kwh_week:.2f}".replace(".", ","),
                        watt=f"{last_watt:g}".replace(".", ",")))
            except Exception as e:
                log.debug(f"Energy fallback failed: {e}")
            if not parts:
                # Translators: Message when no energy data is available.
                parts.append(_("No energy data available. Metering plugs must "
                               "be connected."))
            else:
                # Alphabetically by device name (every entry starts with
                # it) - the same order as the overview command.
                parts.sort(key=str.casefold)
            wx.CallAfter(ui.message, "; ".join(parts))

        threading.Thread(target=task, daemon=True).start()

    def _on_netatmo_tokens_renewed(self, tokens):
        """Takes over tokens renewed by NetatmoAPI and persists them.

        Runs on whichever thread triggered the refresh (usually the
        scheduler), so it only touches the config - no UI.
        """
        if tokens.get('access_token') == self.netatmo_access_token and \
                tokens.get('token_expiry') == self.netatmo_token_expiry:
            return
        self.netatmo_access_token = tokens.get('access_token', '')
        self.netatmo_refresh_token = tokens.get('refresh_token', '')
        self.netatmo_token_expiry = tokens.get('token_expiry', 0)
        try:
            self.save_settings()
        except Exception as e:
            log.debug(f"Could not save the renewed Netatmo tokens: {e}")

    @scriptHandler.script(
        # Translators: Description of the script in the NVDA input gestures
        # dialog. No default gesture - the user assigns one if needed.
        description=_("Connection diagnostics: announce the status of all "
                      "smart home platforms")
    )
    def script_connectionDiagnostics(self, gesture):
        """Announces per-platform connection state, network state and token info."""
        parts = []
        if not self.is_logged_in:
            # Translators: Diagnostics: not logged in at all.
            parts.append(_("Not logged in"))
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
                state = _("connected")
            elif connected is False:
                # Translators: Diagnostics: platform is disconnected.
                state = _("disconnected")
            else:
                # Translators: Diagnostics: platform has not polled yet.
                state = _("not polled yet")
            parts.append(f"{label}: {state}")
        if self._network_offline:
            # Translators: Diagnostics: network considered offline.
            parts.append(_("Network: offline (failed attempts: {count})").format(
                count=self._consecutive_refresh_failures))
        if self.use_netatmo:
            # Read the LIVE value from the API object: it renews the token
            # itself roughly every three hours. The plugin's own copy is only
            # the last persisted state and used to be frozen at login time -
            # which is why this line kept claiming "expired" forever.
            api = self.netatmo_api
            expiry = getattr(api, 'token_expiry', 0) if api else self.netatmo_token_expiry
            has_refresh = bool(getattr(api, 'refresh_token', None)) if api \
                else bool(self.netatmo_refresh_token)
            if not has_refresh:
                # Netatmo discarded the tokens (see _refresh_access_token_locked);
                # no request can renew them - only a new authorization can.
                parts.append(_("Netatmo login is no longer valid. Please "
                               "reconnect in the settings."))
            elif expiry:
                remaining = int(expiry - time.time())
                if remaining > 0:
                    # Translators: Diagnostics: Netatmo token remaining lifetime.
                    parts.append(_("Netatmo token: valid for another "
                                   "{minutes} minutes").format(
                        minutes=remaining // 60))
                else:
                    # Translators: Diagnostics: Netatmo token expired (auto-renewal
                    # happens on the next request).
                    parts.append(_("Netatmo token: expired, will be renewed "
                                   "on the next request"))
        if self._last_refresh_time:
            age = int(time.time() - self._last_refresh_time)
            # Translators: Diagnostics: seconds since the last successful
            # Meross refresh.
            parts.append(_("Last Meross refresh {seconds} seconds ago").format(
                seconds=age))
        if not parts:
            # Translators: Diagnostics: no platform is enabled.
            parts.append(_("No platform enabled"))
        ui.message("; ".join(parts))

    # ------------------------------------------------------------------
    # Favorites layer: ONE assignable command instead of 18 to assign
    # individually.
    #
    # Flow: gesture -> announcement -> digit 1-9 announces the status of the
    # favorite in that fixed slot. The layer stays open; pressing the SAME
    # digit again then switches. 0 reads out the assignment, Escape and any
    # other key end the layer, as does the idle timeout.
    #
    # The split is deliberate: the harmless information comes at once, while
    # the consequential switching needs the deliberate second press. A typo
    # therefore only announces something instead of switching a device.
    #
    # Mechanics: inputCore.manager._captureFunc intercepts the next input
    # before NVDA resolves it as a command; returning False swallows the key
    # so it does not slip through to the focused application. The SPL
    # Assistant (StationPlaylist) uses the same pattern.
    # ------------------------------------------------------------------
    _FAV_LAYER_IDLE_MS = 15000  # safety net: end the layer without input

    @scriptHandler.script(
        # Translators: Description of the favorites layer script in the
        # NVDA input gestures dialog.
        # Keep it short: the input gestures dialog shows a list in which
        # every entry is read out in one go. Details are in the manual and
        # are announced in the layer by key 0.
        description=_("Choose a favorite by digit (a digit announces its "
                      "status, the same digit again toggles it)"),
        # DELIBERATELY without a default gesture - like every assignable
        # command of this add-on. A shipped default cannot be chosen free of
        # collisions with any confidence: NVDA's own sources are only half
        # the truth, plus keyboard layout (desktop/laptop), other add-ons and
        # the user's own assignments. A shortcut that overrides an existing
        # binding is worse than none. It is assigned under NVDA menu ->
        # Preferences -> Input gestures -> category "Smart Home Control".
    )
    def script_favoritesLayer(self, gesture):
        """Opens the favorites layer (the next digit picks the favorite)."""
        if not self.is_logged_in:
            ui.message(_("Not logged in"))
            return
        from .favorites import get_favorites
        if not get_favorites().get_count():
            # Translators (existing msgid from the favorites tab)
            ui.message(_("No favorites yet – add them in the devices tab with "
                         "Ctrl+B"))
            return
        self._fav_layer_active = True
        # last chosen digit; pressing it again switches
        self._fav_layer_last_digit = None
        inputCore.manager._captureFunc = self._fav_layer_capture
        self._fav_layer_watchdog = wx.CallLater(
            self._FAV_LAYER_IDLE_MS, self._fav_layer_idle_timeout)
        _beep(BEEP_ACTION)
        # "Favorites" alone was misleading - it sounded like a completed
        # action instead of a prompt. The text now says that the add-on is
        # waiting and what it expects.
        # Translators: Announced when the favorites layer opens and waits
        # for a digit. Keep it short - it is spoken on every use.
        ui.message(_("Choose a favorite: digit 1 to 9"))

    def _fav_layer_exit(self):
        """Leaves the layer and tears down capture function and timer."""
        self._fav_layer_active = False
        if inputCore.manager._captureFunc == self._fav_layer_capture:
            inputCore.manager._captureFunc = None
        self._fav_layer_last_digit = None
        # getattr: terminate() also calls this if the layer was never open
        watchdog = getattr(self, '_fav_layer_watchdog', None)
        self._fav_layer_watchdog = None
        if watchdog:
            watchdog.Stop()

    def _fav_layer_capture(self, gesture):
        """Evaluates the next input inside the layer.

        CAUTION - RUNS ON NVDA's INPUT THREAD, not on the wx main thread:
        inputCore calls the capture function straight from ``executeGesture``.
        wx timers must not be touched there; ``Start()`` would raise
        "wxAssertionError: timer can only be started from the main thread"
        and inputCore would then disable the capture function - leaving the
        layer dead in the middle of use.

        This function therefore only decides what it must decide AT ONCE -
        whether the key is swallowed - and defers all further work to the
        main thread via ``wx.CallAfter`` (thread-safe). Pleasant side effect:
        the layer's whole state is thus only ever changed on the main thread,
        so no locks are needed.

        Returning False swallows the gesture (inputCore aborts processing),
        True lets it continue normally.
        """
        try:
            # Let modifier keys (shift, NVDA, ...) through and stay in the
            # layer - they arrive as gestures of their own.
            if getattr(gesture, 'isModifier', False):
                return True
            if not isinstance(gesture, KeyboardInputGesture):
                # Braille/touch input and the like: end the layer and pass
                # it on normally.
                wx.CallAfter(self._fav_layer_exit)
                return True
            key = gesture.mainKeyName
            # Treat numpad digits the same (desktop layout)
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
            # Any other key: end the layer, error tone. The key is
            # swallowed so no letter slips into an input field of the
            # focused application.
            wx.CallAfter(self._fav_layer_reject)
            return False
        except Exception:
            # inputCore would disable the capture function itself on an
            # exception - our timers/flags still have to go.
            wx.CallAfter(self._fav_layer_exit)
            raise

    def _fav_layer_cancel(self):
        """Escape: leave the layer and announce it (on the main thread)."""
        if not getattr(self, '_fav_layer_active', False):
            return
        self._fav_layer_exit()
        ui.message(_("Cancelled"))

    def _fav_layer_reject(self):
        """Unexpected key: leave the layer, error tone (on the main thread)."""
        if not getattr(self, '_fav_layer_active', False):
            return
        self._fav_layer_exit()
        _beep(BEEP_ERROR)

    def _fav_layer_digit(self, number):
        """Digit 1-9 in the layer: announce the status, switch on a repeat.

        The second press is deliberately NOT bound to NVDA's short
        double-press window. That is exactly where the first version failed
        in practice: press the digit, listen to the status - and while it is
        spoken (a good second) the half-second window expires. Switching was
        effectively unreachable. Unlike NVDA's own double-press commands you
        do not even know beforehand whether you want to switch; you decide
        that only after hearing the status.

        The layer therefore stays open until Escape, another key or the idle
        timeout ends it. While it is open, pressing the SAME digit again
        switches. Another digit announces its status and becomes the
        remembered one, so "1, 2, 1" switches nothing.

        Always runs on the wx main thread (via CallAfter from the capture
        function) - only there may the timers be touched.
        """
        if not getattr(self, '_fav_layer_active', False):
            return  # the layer was left in the meantime
        watchdog = getattr(self, '_fav_layer_watchdog', None)
        if watchdog:
            watchdog.Start(self._FAV_LAYER_IDLE_MS)

        if getattr(self, '_fav_layer_last_digit', None) == number:
            # Same digit pressed again - but only switch if it followed the
            # announcement closely enough. The layer used to stay open
            # indefinitely, so a digit pressed, forgotten, and pressed again
            # minutes later switched a device. On a power strip carrying a
            # computer that is lost work, not a nuisance.
            #
            # An expired window is not a dead end: the digit announces the
            # status again and opens a fresh one, so switching is always
            # two quick presses away.
            since = time.time() - getattr(self, '_fav_layer_last_digit_ts', 0)
            if since <= self._fav_layer_switch_window():
                self._fav_layer_exit()
                self._favorite_toggle(number)
                return
            self._fav_layer_last_digit_ts = time.time()
            self._favorite_status(number)
            return

        from .favorites import get_favorites
        if get_favorites().get_by_slot(number) is None:
            self._fav_layer_last_digit = None
            # Translators (bestehende msgid)
            ui.message(_("Favorite {number} is not assigned").format(number=number))
            return  # stay in the layer - another digit can be chosen
        self._fav_layer_last_digit = number
        self._fav_layer_last_digit_ts = time.time()
        self._favorite_status(number)

    def _fav_layer_switch_window(self):
        """How long after an announcement the same digit still switches.

        In seconds, from the settings. Kept as a method rather than read
        inline so the layer picks up a changed setting without a restart.
        """
        from .constants import (
            FAV_LAYER_SWITCH_WINDOW_DEFAULT, FAV_LAYER_SWITCH_WINDOW_MIN,
            FAV_LAYER_SWITCH_WINDOW_MAX,
        )
        value = getattr(self, 'fav_layer_switch_window',
                        FAV_LAYER_SWITCH_WINDOW_DEFAULT)
        try:
            value = int(value)
        except (TypeError, ValueError):
            return FAV_LAYER_SWITCH_WINDOW_DEFAULT
        return max(FAV_LAYER_SWITCH_WINDOW_MIN,
                   min(FAV_LAYER_SWITCH_WINDOW_MAX, value))

    def _fav_layer_announce_overview(self):
        """Announces which digit switches which favorite (key 0).

        Runs on the wx main thread (via CallAfter from the capture function).
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
        parts.append(_("A digit announces the status, the same digit again "
                       "toggles, Escape cancels"))
        ui.message(". ".join(parts))

    def _fav_layer_idle_timeout(self):
        """Safety net: end the layer after a longer idle period.

        Without it the capture function would stay active indefinitely and
        swallow a completely unrelated key press minutes later.
        """
        if not getattr(self, '_fav_layer_active', False):
            return
        self._fav_layer_exit()
        ui.message(_("Cancelled"))

    def _get_favorite_device(self, number):
        """Returns (favorite, device) for layer slot ``number`` (1-9).

        device can be None (not loaded yet, removed while offline, ...). The
        slot is the device's fixed number stored in the favorites file
        (favorites._assign_slots), not its position in the list.
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
        """Toggles favorite no. ``number`` (for the direct gestures)."""
        if not self.is_logged_in:
            ui.message(_("Not logged in"))
            return
        fav, device = self._get_favorite_device(number)
        if fav is None:
            # Translators: Message when the favorite slot is empty.
            ui.message(_("Favorite {number} is not assigned").format(number=number))
            return
        if device is None:
            # Translators: Message when the favorite's device is not loaded.
            ui.message(_("{name}: device not available").format(
                name=fav.get('name', '?')))
            return
        if getattr(device, 'is_netatmo', False) or getattr(device, 'is_sensor', False):
            # Devices that cannot be switched: sensors (Meross MS100/MS400
            # ...) and all Netatmo devices - thermostats can be adjusted but
            # not switched on/off, weather stations only display. This branch
            # used to simply announce the status again, which after the
            # second press looked as if nothing had happened. The
            # announcement now names the reason; the status already came with
            # the first press.
            _beep(BEEP_ERROR)
            # Translators: Message when the user tries to switch a device
            # that cannot be switched on/off (sensors, Netatmo devices).
            ui.message(_("{name}: cannot be switched – adjustable in the "
                         "device menu").format(
                name=device.name))
            return
        if getattr(device, 'is_offline', False):
            # Without this check the attempt would go to the cloud and come
            # back as a "switching error" - correct but uninformative. The
            # reason is known, so it is named (same wording as in the device
            # menu).
            _beep(BEEP_ERROR)
            # Translators: Message when the device is offline.
            ui.message(_("{name}: offline").format(name=device.name))
            return

        def task():
            try:
                result = self.toggle_device(device.uuid)
                # Pitch as in the device menu: high = switched on, low =
                # switched off. The same success tone used to play in both
                # directions here, so the sound did not tell which way it
                # went - unlike in the dialog. The state is read from the
                # device after switching, not guessed from the return
                # value.
                _beep(BEEP_ON if getattr(device, 'is_on', False) else BEEP_OFF)
                wx.CallAfter(ui.message, result)
            except Exception as e:
                _beep(BEEP_ERROR)
                _safe_log_error("Favorite toggle failed", e)
                # Translators: Error message when toggling a favorite fails.
                wx.CallAfter(ui.message, _("Switching failed: {error}").format(
                    error=str(e)[:80]))
        threading.Thread(target=task, daemon=True).start()

    def _favorite_status(self, number):
        """Announces the status of favorite no. ``number``."""
        if not self.is_logged_in:
            ui.message(_("Not logged in"))
            return
        fav, device = self._get_favorite_device(number)
        if fav is None:
            ui.message(_("Favorite {number} is not assigned").format(number=number))
            return
        if device is None:
            ui.message(_("{name}: device not available").format(
                name=fav.get('name', '?')))
            return
        parts = [device.name]
        if getattr(device, 'is_offline', False):
            # Translators: Status announcement for an offline device.
            parts.append(_("offline"))
        elif hasattr(device, 'get_status_summary'):
            # Netatmo, VeSync and Cozytouch bring their full summary
            # themselves - the same one the device menu shows. It used to be
            # used for Netatmo only, so a Levoit purifier just reported "on"
            # instead of mode, fan level, air quality and filter life, and
            # the Cozytouch heat pump was missing its current heating
            # target.
            parts.append(device.get_status_summary())
        else:
            # Meross has no summary - assemble it here.
            parts.extend(self._meross_status_parts(device))
        ui.message(", ".join(str(p) for p in parts if p))

    @staticmethod
    def _meross_status_parts(device):
        """Status parts of a Meross device for the favorites announcement.

        Sensors have no on/off: their ``is_on`` is always False, which is why
        the announcement used to simply say "off" - wrong information for a
        temperature sensor. They now report their readings.
        """
        parts = []
        if getattr(device, 'is_temperature_sensor', False):
            temp = device.get_temperature() if hasattr(device, 'get_temperature') else None
            if temp is not None:
                # Translators: A temperature with its unit, put together with
                # other pieces into one announcement.
                parts.append(_("{temp}°C").format(temp=f"{temp:.1f}"))
            hum = device.get_humidity() if hasattr(device, 'get_humidity') else None
            if hum is not None:
                # Translators: Relative humidity in the status announcement.
                parts.append(_("{value}% humidity").format(value=f"{hum:g}"))
        elif getattr(device, 'is_water_sensor', False):
            wet = device.is_water_detected() if hasattr(device, 'is_water_detected') else None
            if wet is not None:
                # Translators: Water leak sensor state in the status
                # announcement.
                parts.append(_("water detected") if wet else _("dry"))
        elif getattr(device, 'is_hub', False):
            # A hub itself has no switchable state.
            # Translators: Status announcement for a Meross hub (it only
            # relays its sensors). {count} = number of connected sensors.
            parts.append(_("hub with {count} sensors").format(
                count=len(device.get_channels() or [])))
        else:
            if hasattr(device, 'is_on'):
                parts.append(_("on") if device.is_on else _("off"))
            power = device.get_power() if hasattr(device, 'get_power') else None
            if power is not None:
                # Translators: Current power draw in the status announcement.
                parts.append(_("{watt} watts").format(
                    watt=f"{power:g}".replace(".", ",")))
        battery = (device.get_battery_percent()
                   if hasattr(device, 'get_battery_percent') else None)
        if battery is not None:
            # Translators: Battery level in the status announcement.
            parts.append(_("battery {percent}%").format(percent=battery))
        return parts

    @scriptHandler.script(
        # Translators: Description of the script in the NVDA input gestures
        # dialog.
        description=_("Announces the status of all smart home devices"),
        gesture="kb:NVDA+control+shift+p",
    )
    def script_announceStatus(self, gesture):
        """Announces the status of all devices - FAST, from the cache"""
        
        log.debug("script_announceStatus called")
        
        if not self.is_logged_in:
            ui.message(_("Not logged in"))
            log.debug("Nicht angemeldet - Abbruch")
            return
        
        # OPTIMIZED: with a fresh cache, announce immediately WITHOUT waiting
        # (snapshot under the lock - convention from __init__, line 149 ff.)
        with self._devices_lock:
            cached_devices = list(self.devices)
        if self.is_cache_fresh() and cached_devices:
            log.debug(f"Cache fresh - announcing {len(cached_devices)} devices immediately")
            # No beep needed - immediate output
            self._announce_devices_status(cached_devices)
            return
        
        # Cache not fresh - short update in the background
        log.debug("Cache not fresh - refreshing in the background")
        
        # Start the periodic beep
        self._start_status_beep()
        
        def task():
            try:
                log.debug("Starting status update...")

                # Use the cached devices, only update the status (faster!)
                # (read snapshot under the lock)
                with self._devices_lock:
                    have_devices = bool(self.devices)
                if not have_devices:
                    log.debug("No cached devices - fetching a new device list")
                    wx.CallAfter(ui.message, _("Loading devices..."))
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
                            log.debug(f"VeSync devices could not be loaded: {e}")
                    if self.cozytouch_api and self.use_cozytouch:
                        try:
                            all_devs.extend(self.cozytouch_api.get_devices())
                        except Exception as e:
                            log.debug(f"Cozytouch devices could not be loaded: {e}")
                    # Assign under the lock - the scheduler thread reads the
                    # same list in parallel (consistent with all other write
                    # sites).
                    with self._devices_lock:
                        self.devices = all_devs
                    self._last_refresh_time = time.time()
                else:
                    log.debug(f"Using {len(self.devices)} cached devices - only updating the status")
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
                                log.debug(f"VeSync status update failed: {e}")
                        cozytouch_devs = by_platform['cozytouch']
                        if cozytouch_devs and self.cozytouch_api:
                            try:
                                self.cozytouch_api.update_device_status(cozytouch_devs)
                            except Exception as e:
                                log.debug(f"Cozytouch status update failed: {e}")
                        self._last_refresh_time = time.time()
                    except TimeoutError:
                        log.warning("Status-Update Timeout - verwende gecachte Daten")
                        # No abort - cached data is better than nothing
                
                with self._devices_lock:
                    devs_for_status = list(self.devices)
                if not devs_for_status:
                    log.warning("No devices found")
                    self._stop_status_beep()
                    wx.CallAfter(ui.message, _("No devices found"))
                    return

                # Stop the beep and play the success sound
                self._stop_status_beep()
                wx.CallAfter(_beep, BEEP_SUCCESS)
                wx.CallAfter(self._announce_devices_status, devs_for_status)

            except Exception as e:
                _safe_log_error("Failed to fetch the status", e)
                self._stop_status_beep()
                wx.CallAfter(_beep, BEEP_ERROR)
                error_msg = str(e)
                if len(error_msg) > 50:
                    error_msg = error_msg[:50] + "..."
                # Translators: Generic error message with detail text.
                wx.CallAfter(ui.message, _("Error: {error}").format(error=error_msg))
        
        log.debug("Starting the status query thread")
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
        detail = ", ".join(parts) if parts else _("none")
        # Translators: Introduction of the status announcement. {count} =
        # total, {detail} = breakdown per platform.
        msg = _("{count} devices ({detail}). ").format(
            count=len(sorted_devices),
            detail=detail,
        )
        log.debug(f"Building the status message for {len(sorted_devices)} devices")
        
        # Translators: Announced when a device currently provides no
        # sensor/status data.
        no_data = _("No data")
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
                            msg += _(", {humidity:.1f}% humidity").format(humidity=humidity)
                        msg += ". "
                    else:
                        msg += f"{device.name}: {no_data}. "

                # Water sensor
                elif device.is_water_sensor:
                    alarm = device.is_water_detected()
                    # Translators: Meross water sensor: water was detected
                    # (alarm).
                    status = _("Water alarm!") if alarm else _("no water "
                                                               "detected")
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
                    status = _("on") if device.is_on else _("off")
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
                                msg += _(", {power} watts").format(power=power)
                            if voltage is not None:
                                # Translators: Current voltage in volts.
                                msg += _(", {voltage} volts").format(voltage=voltage)
                            if current is not None:
                                # Translators: Current amperage in amps.
                                msg += _(", {current} amps").format(current=current)
                        except Exception as e:
                            log.debug(f"Power metering not available for {device.name}: {e}")

                    msg += ". "

            except Exception as e:
                log.warning(f"Failed to fetch the data of {device.name}: {e}")
                # Translators: Generic fallback when the status query for a
                # single device fails.
                msg += f"{device.name}: {_('Error')}. "
        
        log.debug(f"Status message ready: {msg[:100]}...")
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
        """Refreshes the device list (for the dialog) - status update ONLY, no discovery!

        Coalesced: if the scheduler (or another caller) is already refreshing,
        this waits for that pass and returns its result instead of sending the
        same cloud queries a second time.
        """
        if not self._refresh_lock.acquire(blocking=False):
            log.debug("Refresh already running - waiting for it instead of polling again")
            self._refresh_lock.acquire()
            self._refresh_lock.release()
            with self._devices_lock:
                return list(self.devices)
        try:
            return self._refresh_devices_impl()
        finally:
            self._refresh_lock.release()

    def _mark_platform_refreshed(self, name):
        """Notes that ``name`` was just refreshed (see _platform_last_refresh)."""
        self._platform_last_refresh[name] = time.time()

    def _platform_enabled(self, name):
        """Whether the platform ``name`` is switched on in the settings."""
        return {
            'meross': self.use_meross,
            'netatmo': self.use_netatmo,
            'vesync': self.use_vesync,
            'cozytouch': self.use_cozytouch,
        }.get(name, True)

    def drop_disabled_platform_devices(self):
        """Removes the devices of switched-off platforms; returns the count.

        Switching a platform off stopped its polling immediately, but the
        devices stayed in the list - and therefore in the tree, offered for
        switching, until the next NVDA start.
        """
        with self._devices_lock:
            keep = [d for d in self.devices
                    if self._platform_enabled(platform_of(d))]
            removed = len(self.devices) - len(keep)
            if removed:
                self.devices = keep
                log.debug(f"{removed} device(s) of switched-off platforms "
                          f"dropped")
        return removed

    def _refresh_devices_impl(self):
        """The actual refresh - only called while holding ``_refresh_lock``."""
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
                    log.debug(f"Updating the status of {len(meross_devs)} Meross devices...")
                    self.api.update_device_status(meross_devs)
                else:
                    log.debug("No Meross devices present - running a discovery...")
                    meross_devs = self.api.get_devices()
                    self.api.set_wrapped_devices(meross_devs)
                self._mark_platform_refreshed('meross')

            # Update Netatmo (fetch new data)
            if self.netatmo_api and self.use_netatmo:
                log.debug("Updating the Netatmo devices...")
                try:
                    netatmo_devs = self.netatmo_api.get_devices()
                    self._mark_platform_refreshed('netatmo')
                except Exception as e:
                    # Do not escalate Netatmo errors - use the last known
                    # devices
                    log.debug(f"Netatmo refresh failed: {e}")

            # Update VeSync (status of the existing devices; discovery if
            # needed)
            if self.vesync_api and self.use_vesync:
                if vesync_devs:
                    log.debug(f"Updating the status of {len(vesync_devs)} VeSync devices...")
                    try:
                        self.vesync_api.update_device_status(vesync_devs)
                        # Update the snapshots (no "external" trigger, since
                        # done at the user's explicit request)
                        for vd in vesync_devs:
                            self._previous_vesync_states[vd.uuid] = self._snapshot_vesync_state(vd)
                        self._mark_platform_refreshed('vesync')
                    except Exception as e:
                        log.debug(f"VeSync status refresh failed: {e}")
                else:
                    log.debug("No VeSync devices present - running a discovery...")
                    try:
                        vesync_devs = self.vesync_api.get_devices()
                        for vd in vesync_devs:
                            self._previous_vesync_states[vd.uuid] = self._snapshot_vesync_state(vd)
                        self._mark_platform_refreshed('vesync')
                    except Exception as e:
                        log.debug(f"VeSync discovery failed: {e}")

            # Update Cozytouch (status of the existing devices; discovery if
            # needed)
            if self.cozytouch_api and self.use_cozytouch:
                if cozytouch_devs:
                    log.debug(f"Updating the status of {len(cozytouch_devs)} Cozytouch devices...")
                    try:
                        self.cozytouch_api.update_device_status(cozytouch_devs)
                        # Update the snapshots (no "external" trigger, since
                        # done at the user's explicit request)
                        for cd in cozytouch_devs:
                            self._previous_cozytouch_states[cd.uuid] = self._snapshot_cozytouch_state(cd)
                        self._mark_platform_refreshed('cozytouch')
                    except Exception as e:
                        log.debug(f"Cozytouch status refresh failed: {e}")
                else:
                    log.debug("No Cozytouch devices present - running a discovery...")
                    try:
                        cozytouch_devs = self.cozytouch_api.get_devices()
                        for cd in cozytouch_devs:
                            self._previous_cozytouch_states[cd.uuid] = self._snapshot_cozytouch_state(cd)
                        self._mark_platform_refreshed('cozytouch')
                    except Exception as e:
                        log.debug(f"Cozytouch discovery failed: {e}")

            with self._devices_lock:
                new_list = meross_devs + netatmo_devs + vesync_devs + cozytouch_devs
                # Keep devices that a scheduler poll running in parallel
                # discovered/appended between the snapshot and the reassignment
                # (otherwise a lost update: they would silently drop out).
                known_uuids = {d.uuid for d in new_list}
                for d in self.devices:
                    if d.uuid not in known_uuids:
                        new_list.append(d)
                # A switched-off platform belongs in neither of the two
                # sources above. Without this filter its devices survived
                # every refresh, because the snapshot at the top brought
                # them back in.
                new_list = [d for d in new_list
                            if self._platform_enabled(platform_of(d))]
                self.devices = new_list
            self._last_refresh_time = time.time()
            log.debug(f"Device refresh finished: {len(self.devices)} devices")
            return self.devices
        except Exception as e:
            log.error(f"Failed to update the devices: {e}")
            raise
    
    def _log_toggle(self, target, new_state):
        """Writes a switching action to the history.

        Deliberately here and not in the calling interfaces: toggle_device()
        is the shared bottleneck of BOTH ways of operating (device dialog and
        favorites gestures). Only the dialog used to log - devices switched
        via a favorites gesture did not appear in the history at all, while
        the same device switched from the menu did.
        """
        try:
            from .history import get_history, SOURCE_LOCAL
            get_history().log_action(
                target,
                'toggle_on' if new_state else 'toggle_off',
                # No detail: the action already says on/off. Anything stored
                # here would be text in the language of the day (see
                # _detail_is_redundant in history.py).
                "",
                source=SOURCE_LOCAL,
            )
        except Exception as e:
            # The history must never prevent the switching.
            log.debug(f"History entry failed: {e}")

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
                # Only treat it as a channel if a number really follows
                # "_ch" - a device UUID can contain "_ch" itself.
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
                raise ValueError(_("Device not found"))

            # VeSync devices: their own toggle logic
            if getattr(device, 'is_vesync', False):
                new_state = not device.is_on
                device.toggle_switch(new_state)
                status = _("on") if new_state else _("off")
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
                    raise RuntimeError(_("Hot water could not be toggled"))
                status = _("on") if new_state else _("off")
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
            # self.api can be None (Meross disabled but the device still in
            # the list) - then report cleanly instead of an AttributeError.
            if not self.api:
                raise RuntimeError(_("Not logged in"))
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
                    device_name = _("{name} channel {number}").format(
                        name=device.name, number=channel)
            else:
                device._is_on = new_state
                device_name = device.name

            status = _("on") if new_state else _("off")
            self._record_local_toggle(device_uuid, new_state)
            # For channels log the channel object so the history shows
            # "garden: pump" and not just "garden".
            self._log_toggle(log_target if channel is not None else device,
                             new_state)
            return _("{name}: {status}").format(name=device_name, status=status)

        except (TimeoutError, ConnectionError, OSError) as e:
            # Network/timeout errors: only WARNING (not ERROR) - the dialog
            # shows a message
            log.warning(f"Toggling failed: {e}")
            raise
        except Exception as e:
            log.error(f"Toggling failed: {e}")
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
                raise ValueError(_("Device not found"))

            if not device.is_diffuser:
                # Translators: Error message when a diffuser action is
                # accidentally executed on another device type.
                raise ValueError(_("Device is not a diffuser"))

            # Convert the action to a spray mode
            from meross_iot.model.enums import DiffuserSprayMode
            mode_map = {
                'diffuser_light': DiffuserSprayMode.LIGHT,
                'diffuser_strong': DiffuserSprayMode.STRONG,
                'diffuser_off': DiffuserSprayMode.OFF
            }

            if mode_action not in mode_map:
                # Translators: Error message for an unknown diffuser action.
                raise ValueError(_("Invalid diffuser action: {action}").format(action=mode_action))

            spray_mode = mode_map[mode_action]

            # Set the mode via the API
            self.api.set_diffuser_spray_mode(device.uuid, spray_mode)

            # Update the status
            device._update_status()

            # History: at the shared bottleneck as with switching, so a
            # call from outside the dialog is logged too.
            try:
                from .history import get_history, SOURCE_LOCAL
                get_history().log_action(
                    # Mode key, not its label - the display translates it.
                    device, mode_action, mode_action,
                    source=SOURCE_LOCAL)
            except Exception as e:
                log.debug(f"History entry failed: {e}")

            # Translators: Success feedback after a diffuser mode change.
            return _("{name}: mode set").format(name=device.name)
            
        except Exception as e:
            log.error(f"Failed to set the diffuser mode: {e}")
            raise



# Note: the former 18 individual scripts ("toggle favorite N" / "announce
# the status of favorite N") are replaced by the favorites layer
# (script_favoritesLayer in the class): one gesture, then a digit 1-9.
# Orphaned gestures.ini entries pointing at the old script names are
# harmless - NVDA ignores bindings to scripts that do not exist.
