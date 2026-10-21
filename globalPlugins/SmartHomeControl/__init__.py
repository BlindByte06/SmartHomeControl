# -*- coding: utf-8 -*-
"""
Smart Home Control - NVDA add-on
Controls Meross, Netatmo, VeSync/Levoit, Cozytouch/Atlantic and Petlibro
devices via their cloud APIs.

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
        f"other platforms - Netatmo, VeSync, Cozytouch and Petlibro - work "
        f"unchanged."
    )
    return None

addon_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
lib_path = os.path.join(addon_path, "lib")
_arch_dir = _select_arch_dir(os.path.join(lib_path, "_arch"))
# Append the arch folder BEFORE lib so the compiled packages are found there;
# pure Python packages (meross_iot, paho, ...) come from lib/. APPENDED, so
# NVDA's own library.zip comes first: requests, urllib3, certifi, idna and
# charset_normalizer are NVDA's copies at runtime, whatever lib/ holds -
# replacing them in NVDA's process could break NVDA itself. Which copy ran is
# logged after the login (_log_library_origins).
if _arch_dir and _arch_dir not in sys.path:
    sys.path.append(_arch_dir)
    log.debug(f"Smart Home Control: arch path appended: {_arch_dir}")
if lib_path not in sys.path:
    sys.path.append(lib_path)
    log.debug(f"Smart Home Control: lib path appended: {lib_path}")

def _log_library_origins():
    """Logs which copy of the network libraries runs - NVDA's or the add-on's.

    Once per login, after it, so aiohttp is loaded when Meross is on. A
    report then says which versions ran, and a fix bundled in lib/ that
    never takes effect because NVDA ships the package too shows up here.
    """
    own = os.path.normcase(lib_path)
    parts = []
    for name in ("requests", "urllib3", "certifi", "aiohttp"):
        module = sys.modules.get(name)
        if module is None:
            continue
        where = os.path.normcase(getattr(module, "__file__", "") or "")
        origin = "add-on" if where.startswith(own) else "NVDA"
        parts.append(f"{name} {getattr(module, '__version__', '?')} ({origin})")
    if parts:
        log.info("Libraries in use: " + ", ".join(parts))


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
from .cozytouch_devices import CozytouchPending
from .petlibro_api import PetlibroAPI, PetlibroError
from .device_dialog import SmartHomeControlDialog
from .settings_panel import (
    SmartHomeSettingsDialog, is_credentials_error, login_error_message,
    offer_credential_reentry,
)
from .security import encrypt_dpapi, decrypt_dpapi_checked, is_encrypted
from .credentials import _CredentialsMixin
from .scheduler import _SchedulerMixin
from .platform_health import PlatformHealth
from . import platform_health
from .change_detection import _ChangeDetectionMixin
from .platform_utils import (
    split_by_platform, platform_of, PLATFORMS, PLATFORM_LABELS,
    PASSWORD_PLATFORMS,
)
from . import credential_store
from .meross_devices import carry_over_hub_subdevices


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
from .errors import log_failure, is_transient
from .formatting import fmt_decimal, fmt_number
from .constants import (
    CONFSPEC, BEEP_ERROR, BEEP_SUCCESS, BEEP_LOADING, BEEP_ACTION,
    BEEP_ON, BEEP_OFF, NETATMO_REDIRECT_PORT,
)


def _safe_log_error(message, exception):
    """Logs a failure WITHOUT exc_info so no sensitive request data
    (tokens, bodies, headers) ends up in the NVDA log.

    At the level it deserves (see errors.log_failure): a login that failed
    because the network was not up yet is a WARNING. It used to be an ERROR,
    and a laptop starting before its Wi-Fi played NVDA's error sound once
    per platform.
    """
    log_failure(message, exception)

# Config section for this add-on
config.conf.spec["smartHomeControl"] = CONFSPEC


class _AddonSettings:
    """The add-on's section of NVDA's configuration - written to nvda.ini.

    Reading goes through config.conf, as everywhere. Writing through it puts a
    value into the profile activated last: saved while a triggered profile
    was active (an application of its own, say all), the settings of the
    add-on landed in that profile and overrode the base whenever it was
    active again - a notification switched off in one place stayed on in
    another. Platforms, notifications and the like are not meant to differ
    from profile to profile, so a value written here goes to the base
    configuration, and the same key is taken out of every other active
    profile.

    Falls back to the ordinary write if NVDA's configuration ever looks
    different from what this expects.
    """

    SECTION = "smartHomeControl"

    def __init__(self):
        self._aggregated = config.conf[self.SECTION]

    def get(self, key, default=None):
        return self._aggregated.get(key, default)

    def __getitem__(self, key):
        return self._aggregated[key]

    def __setitem__(self, key, value):
        try:
            profiles = config.conf.profiles
            base = profiles[0]
            if self.SECTION not in base:
                base[self.SECTION] = {}
            base[self.SECTION][key] = value
            for profile in profiles[1:]:
                section = profile.get(self.SECTION)
                if section is not None and key in section:
                    del section[key]
            # The aggregated view caches what it read; without this it
            # would go on answering with the value from before.
            cache = getattr(self._aggregated, "_cache", None)
            if isinstance(cache, dict):
                cache.pop(key, None)
        except Exception as e:
            log.debug(f"Writing {key} to the base configuration failed "
                      f"({type(e).__name__}: {e}) - written the ordinary way")
            self._aggregated[key] = value


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
        self.petlibro_api = None
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
        # Platforms waiting for a new login attempt, and the event that ends
        # those waits when NVDA shuts down (see _schedule_login_retry).
        self._login_retry_pending = set()
        self._login_retry_stop = threading.Event()
        # save_settings() runs from several login threads at once since the
        # platforms log in in parallel.
        self._save_lock = threading.RLock()
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
        self.use_petlibro = False

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

        # Petlibro credentials (feeders and water fountains)
        self.petlibro_email = ''
        self._encrypted_petlibro_password = ''
        self.petlibro_token = ''
        self.petlibro_last_portions = 1
        
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
        self._petlibro_connected = None

        # The same knowledge in a form that can be looked up: which platform
        # is answering, since when it is not, and which part of it is missing.
        # Written by the scheduler, read by the device tree, the status line
        # and the connection diagnostics.
        self.platform_health = PlatformHealth()
        
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
        # Changes the cloud accepted that the appliance had not shown yet.
        self._cozytouch_expected = {}

        # Petlibro: the same pair, plus the work records already seen per
        # feeder - a feeding is noticed by a record id that is new.
        self._previous_petlibro_states = {}
        self._recent_petlibro_actions = {}
        self._petlibro_seen_records = {}
        # Changes sent from here that the device has not confirmed yet.
        self._petlibro_expected = {}
        # "Feed now" running or waiting for a late confirmation, per device.
        self._petlibro_feed_wait = {}
        # Faults announced lately, per device - one event comes twice.
        self._petlibro_recent_faults = {}

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
        if self.auto_login and self._platforms_to_login():
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
        # Stop the unified scheduler thread and any login retry waiting.
        self._login_retry_stop.set()
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

        # The logouts run on a thread of their own, waited for a few seconds
        # at most. On NVDA's main thread they added up to some seventeen
        # seconds on a bad network - the Meross manager close, the HTTP
        # logout and two joins of its event loop thread one after the other -
        # and NVDA stood still while it was quitting.
        apis = [a for a in (self.api, self.netatmo_api, self.vesync_api,
                            self.cozytouch_api, self.petlibro_api) if a]

        def _logout_all():
            for api in apis:
                try:
                    api.logout()
                except Exception as e:
                    log.debug(f"Ignored error in terminate: {e}")

        if apis:
            worker = threading.Thread(target=_logout_all, daemon=True,
                                      name="SmartHomeControl logout")
            worker.start()
            worker.join(timeout=self._LOGOUT_WAIT)
            if worker.is_alive():
                log.debug(f"Logout not finished after {self._LOGOUT_WAIT}s - "
                          f"NVDA goes on shutting down")
        super().terminate()

    # Seconds terminate() waits for the platforms to log out.
    _LOGOUT_WAIT = 3.0
    
    def load_settings(self):
        """Load the settings from the NVDA config"""
        try:
            # Read as usual; anything written while loading (the one-time
            # move of the credentials) goes to nvda.ini itself.
            conf = _AddonSettings()
            # Credentials do not live in nvda.ini: NVDA writes the whole
            # configuration into the log at startup, and the email address
            # stood there in plain text. credential_store keeps them in a
            # file of its own and moves them out of the configuration the
            # first time it runs.
            secrets = credential_store.load(conf)
            # An early version wrote to a section of its own, the Netatmo
            # password in plain text among it. It goes once, and is written
            # out at once: NVDA logs the configuration at the next start.
            removed = credential_store.drop_legacy_sections(config.conf.profiles[0])
            if removed:
                log.info("Removed the configuration of an early version: "
                         + ", ".join(f"[{name}]" for name in removed))
                self._flush_config_to_disk()
            # A value that cannot be decrypted here keeps its ciphertext,
            # and save_settings writes THAT back instead of the empty string
            # the decryption returned. DPAPI fails whenever the Windows
            # account or the machine is another one - a portable NVDA on a
            # stick reaches a second machine that way, and the credential
            # file travels beside the add-on folder. Without this the first
            # save on that machine would overwrite values that were still
            # good on the first one, the Netatmo client id and secret among
            # them, which are typed in by hand and cannot be fetched again.
            self._unreadable_secrets = set()

            def _secret(key):
                """Decrypted value; notes the key when it cannot be read."""
                plaintext, ok = decrypt_dpapi_checked(secrets.get(key, ""))
                if not ok:
                    self._unreadable_secrets.add(key)
                return plaintext

            def _maybe_secret(key):
                """An email address: encrypted since 26.10.1, plain before."""
                value = secrets.get(key, "")
                return _secret(key) if is_encrypted(value) else value

            self.email = _maybe_secret("email")

            # Keep the password encrypted in memory (never as plain text). It
            # is only decrypted on demand at login time via the password
            # property. set_encrypted_password() checks the format cleanly (no
            # blind prefix heuristic).
            self.set_encrypted_password(secrets.get("password", ""))
            
            self.auto_login = conf.get("autoLogin", True)
            self.announce_external_changes = conf.get("announceExternalChanges", True)
            self.notify_faults = conf.get("notifyFaults", True)
            self.notify_fault_unreachable = conf.get("notifyFaultUnreachable", True)
            self.notify_fault_partial = conf.get("notifyFaultPartial", True)
            self.notify_fault_recovered = conf.get("notifyFaultRecovered", True)
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
                self.netatmo_client_id = _secret("netatmoClientId")
            else:
                # Legacy: plain text, will be encrypted on the next save
                self.netatmo_client_id = raw_client_id
            self.netatmo_client_secret = _secret("netatmoClientSecret")

            self.netatmo_access_token = _secret("netatmoAccessToken")
            self.netatmo_refresh_token = _secret("netatmoRefreshToken")
            self.netatmo_token_expiry = conf.get("netatmoTokenExpiry", 0)
            self.netatmo_redirect_port = conf.get("netatmoRedirectPort", NETATMO_REDIRECT_PORT)

            # VeSync credentials (password and token encrypted)
            self.vesync_email = _maybe_secret("vesyncEmail")
            self.set_encrypted_vesync_password(secrets.get("vesyncPassword", ""))
            self.vesync_country_code = conf.get("vesyncCountryCode", "DE") or "DE"
            self.vesync_token = _secret("vesyncToken")
            self.vesync_account_id = _secret("vesyncAccountId")
            self.vesync_region = conf.get("vesyncRegion", "")
            self.vesync_filter_threshold = conf.get("vesyncFilterThreshold", 15)
            from .constants import FAV_LAYER_SWITCH_WINDOW_DEFAULT
            self.fav_layer_switch_window = conf.get(
                "favLayerSwitchWindow", FAV_LAYER_SWITCH_WINDOW_DEFAULT)

            # Cozytouch credentials (password and token encrypted)
            self.use_cozytouch = conf.get("useCozytouch", False)
            self.cozytouch_email = _maybe_secret("cozytouchEmail")
            self.set_encrypted_cozytouch_password(secrets.get("cozytouchPassword", ""))
            self.cozytouch_token = _secret("cozytouchToken")
            self.cozytouch_capacity_liters = conf.get("cozytouchCapacityLiters", 0)
            self.notify_cozytouch_mode = conf.get("notifyCozytouchMode", True)
            self.notify_cozytouch_temp = conf.get("notifyCozytouchTemp", True)
            self.notify_cozytouch_boost = conf.get("notifyCozytouchBoost", True)
            self.notify_cozytouch_power = conf.get("notifyCozytouchPower", True)
            self.notify_cozytouch_away = conf.get("notifyCozytouchAway", True)

            # Petlibro credentials (password and token encrypted)
            self.use_petlibro = conf.get("usePetlibro", False)
            self.petlibro_email = _maybe_secret("petlibroEmail")
            self.set_encrypted_petlibro_password(secrets.get("petlibroPassword", ""))
            self.petlibro_token = _secret("petlibroToken")
            self.petlibro_last_portions = conf.get("petlibroLastPortions", 1)
            self.notify_petlibro_feed_failed = conf.get("notifyPetlibroFeedFailed", True)
            self.notify_petlibro_feed_external = conf.get("notifyPetlibroFeedExternal", True)
            self.notify_petlibro_feed_planned = conf.get("notifyPetlibroFeedPlanned", False)
            self.notify_petlibro_food_low = conf.get("notifyPetlibroFoodLow", True)
            self.notify_petlibro_water_low = conf.get("notifyPetlibroWaterLow", True)
            self.notify_petlibro_maintenance = conf.get("notifyPetlibroMaintenance", True)
            self.notify_petlibro_battery = conf.get("notifyPetlibroBattery", True)
            self.notify_petlibro_connection = conf.get("notifyPetlibroConnection", True)
            self.notify_petlibro_settings = conf.get("notifyPetlibroSettings", True)

            if self._unreadable_secrets:
                # Names only, never values. The list is what tells a reader
                # of the log apart the two cases that look alike from
                # outside: nothing stored, or stored on another machine.
                names = ", ".join(sorted(self._unreadable_secrets))
                log.warning(
                    f"Stored credentials cannot be decrypted on this Windows "
                    f"account or machine and are kept untouched: {names}")

            log.debug(f"Settings loaded: Meross={self.use_meross}, Netatmo={self.use_netatmo}, VeSync={self.use_vesync}, Cozytouch={self.use_cozytouch}, auto login={self.auto_login}")
        except Exception as e:
            log.error(f"Failed to load the settings: {e}")
            # Nothing is known about the stored values after a failed load,
            # so nothing may be overwritten either: save_settings keeps
            # every secret key as it stands (see _unreadable_secrets).
            self._unreadable_secrets = set(credential_store.SECRET_KEYS)
            self.email = ""
            self._encrypted_password = ""
            self.auto_login = True
            self.announce_external_changes = True
            self.notify_faults = True
            self.notify_fault_unreachable = True
            self.notify_fault_partial = True
            self.notify_fault_recovered = True
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
            self.use_petlibro = False
            self.petlibro_email = ""
            self._encrypted_petlibro_password = ""
            self.petlibro_token = ""
            self.petlibro_last_portions = 1
            self.notify_petlibro_feed_failed = True
            self.notify_petlibro_feed_external = True
            self.notify_petlibro_feed_planned = False
            self.notify_petlibro_food_low = True
            self.notify_petlibro_water_low = True
            self.notify_petlibro_maintenance = True
            self.notify_petlibro_battery = True
            self.notify_petlibro_connection = True
            self.notify_petlibro_settings = True

    def save_settings(self, flush=False):
        """Save the settings to the NVDA config (passwords encrypted with DPAPI)

        Serialised: the platforms log in in parallel, and three of them save
        a fresh token as soon as they have it.

        ``flush`` writes nvda.ini to disk at once - the settings dialog asks
        for it when its OK is pressed. Everything else only updates the
        configuration in memory: the credentials and tokens go to their own
        file, which is written at once anyway, and nvda.ini used to be
        written out on every token renewal - every three hours for Netatmo -
        with every change made in NVDA's own settings since, which defeated
        "Save configuration when exiting NVDA" being switched off.
        """
        with self._save_lock:
            self._save_settings_locked(flush)

    @staticmethod
    def _save_on_exit_wanted():
        """Whether NVDA saves its configuration when it exits."""
        try:
            return bool(config.conf["general"]["saveConfigurationOnExit"])
        except Exception:
            return True

    @staticmethod
    def _secrets_in_conf(conf):
        """Whether a credential still stands in nvda.ini (before migration)."""
        for key in credential_store.SECRET_KEYS:
            try:
                if conf.get(key, ""):
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _encrypted(key, plaintext, stored):
        """The ciphertext to store for ``plaintext`` under ``key``.

        The stored one while it still decrypts to the same text, a fresh one
        otherwise. DPAPI gives a different ciphertext for the same text on
        every call, so encrypting afresh made every save a change, and the
        credential file was rewritten - with an icacls child process for its
        rights - for a feeder's last portion count.
        """
        if not plaintext:
            return ""
        old = stored.get(key, "")
        if old and is_encrypted(old):
            try:
                if decrypt_dpapi_checked(old) == (plaintext, True):
                    return old
            except Exception:
                pass
        return encrypt_dpapi(plaintext)

    def _save_settings_locked(self, flush=False):
        try:
            # Written to nvda.ini itself, never into the profile that happens
            # to be active (see _AddonSettings).
            conf = _AddonSettings()
            # Checked before the save below clears them: a credential that
            # was still in nvda.ini has to leave the disk too, not only the
            # memory - NVDA writes nvda.ini into its log at every start.
            secrets_were_in_conf = self._secrets_in_conf(conf)
            # The secrets go into the credential store, not into the
            # configuration - see load_settings. The dictionary is filled
            # here and written in one go further down.
            secrets = {}
            # What the file holds now: an unchanged value keeps its
            # ciphertext (see _encrypted), and a save that changes nothing
            # does not rewrite the file.
            stored = credential_store.read_stored()
            # The email addresses are encrypted too since 26.10.1. The file
            # is the user's alone (security._restrict_file_acl), but it
            # stays behind wherever NVDA's configuration is copied, and an
            # address says who owns the devices.
            secrets["email"] = self._encrypted("email", self.email, stored)
            
            # Password (already encrypted in memory - store directly)
            secrets["password"] = self._encrypted_password if self._encrypted_password else ""
            
            conf["autoLogin"] = self.auto_login
            conf["announceExternalChanges"] = self.announce_external_changes
            conf["notifyFaults"] = self.notify_faults
            conf["notifyFaultUnreachable"] = self.notify_fault_unreachable
            conf["notifyFaultPartial"] = self.notify_fault_partial
            conf["notifyFaultRecovered"] = self.notify_fault_recovered
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
            secrets["netatmoClientId"] = self._encrypted("netatmoClientId", self.netatmo_client_id, stored)
            secrets["netatmoClientSecret"] = self._encrypted("netatmoClientSecret", self.netatmo_client_secret, stored)
            secrets["netatmoAccessToken"] = self._encrypted("netatmoAccessToken", self.netatmo_access_token, stored)
            secrets["netatmoRefreshToken"] = self._encrypted("netatmoRefreshToken", self.netatmo_refresh_token, stored)
            conf["netatmoTokenExpiry"] = self.netatmo_token_expiry
            conf["netatmoRedirectPort"] = self.netatmo_redirect_port

            # VeSync credentials (password and token encrypted)
            secrets["vesyncEmail"] = self._encrypted("vesyncEmail", self.vesync_email, stored)
            secrets["vesyncPassword"] = self._encrypted_vesync_password if self._encrypted_vesync_password else ""
            conf["vesyncCountryCode"] = self.vesync_country_code or "DE"
            secrets["vesyncToken"] = self._encrypted("vesyncToken", self.vesync_token, stored)
            secrets["vesyncAccountId"] = self._encrypted("vesyncAccountId", self.vesync_account_id, stored)
            conf["vesyncRegion"] = self.vesync_region or ""
            conf["vesyncFilterThreshold"] = self.vesync_filter_threshold
            conf["favLayerSwitchWindow"] = self.fav_layer_switch_window

            # Cozytouch credentials (password and token encrypted)
            conf["useCozytouch"] = self.use_cozytouch
            secrets["cozytouchEmail"] = self._encrypted("cozytouchEmail", self.cozytouch_email, stored)
            secrets["cozytouchPassword"] = self._encrypted_cozytouch_password if self._encrypted_cozytouch_password else ""
            secrets["cozytouchToken"] = self._encrypted("cozytouchToken", self.cozytouch_token, stored)
            conf["cozytouchCapacityLiters"] = self.cozytouch_capacity_liters

            # Petlibro credentials (password and token encrypted)
            conf["usePetlibro"] = self.use_petlibro
            secrets["petlibroEmail"] = self._encrypted("petlibroEmail", self.petlibro_email, stored)
            secrets["petlibroPassword"] = self._encrypted_petlibro_password if self._encrypted_petlibro_password else ""
            secrets["petlibroToken"] = self._encrypted("petlibroToken", self.petlibro_token, stored)
            conf["petlibroLastPortions"] = int(self.petlibro_last_portions or 1)
            conf["notifyPetlibroFeedFailed"] = self.notify_petlibro_feed_failed
            conf["notifyPetlibroFeedExternal"] = self.notify_petlibro_feed_external
            conf["notifyPetlibroFeedPlanned"] = self.notify_petlibro_feed_planned
            conf["notifyPetlibroFoodLow"] = self.notify_petlibro_food_low
            conf["notifyPetlibroWaterLow"] = self.notify_petlibro_water_low
            conf["notifyPetlibroMaintenance"] = self.notify_petlibro_maintenance
            conf["notifyPetlibroBattery"] = self.notify_petlibro_battery
            conf["notifyPetlibroConnection"] = self.notify_petlibro_connection
            conf["notifyPetlibroSettings"] = self.notify_petlibro_settings
            # A secret this machine could not decrypt is dropped from the
            # dictionary rather than written: credential_store.save leaves a
            # key it is not given exactly as it stands. Writing the empty
            # string the decryption returned would destroy a ciphertext that
            # is still good on the machine it was made on.
            #
            # Only while it is still empty. Anything that has since put a
            # real value there - the settings dialog, a re-auth - wins, and
            # the value gets written. What this does not cover is emptying
            # such a key on purpose: the old ciphertext stays. It is inert,
            # since it never decrypts here, and the alternative is a table
            # of which key belongs to which platform, kept in step by hand.
            for key in getattr(self, '_unreadable_secrets', ()):
                if not secrets.get(key):
                    secrets.pop(key, None)

            if not secrets:
                # Nothing left to contribute - the load failed and no field
                # has been filled in since. Saving anyway would clear the
                # configuration (that is the second half of the call) while
                # putting nothing in its place, and a migration that had not
                # run yet would lose everything it was about to move.
                log.warning("No credentials to save - the stored file and the "
                            "configuration are both left as they are")
            # Everything collected above goes into the credential store, and
            # the same call clears whatever is still standing in nvda.ini.
            # A failure only means the file stays as it was - the values are
            # in memory and the next save tries again. It is said out loud
            # nonetheless: the one value that gets lost this way is a token
            # the cloud rotated, and a silent debug line is not enough to
            # explain a sign-in that asks for the password again.
            elif not credential_store.save(conf, secrets):
                log.error("Credentials were NOT written - the stored file "
                          "keeps its previous contents")
            conf["notifyCozytouchMode"] = self.notify_cozytouch_mode
            conf["notifyCozytouchTemp"] = self.notify_cozytouch_temp
            conf["notifyCozytouchBoost"] = self.notify_cozytouch_boost
            conf["notifyCozytouchPower"] = self.notify_cozytouch_power
            conf["notifyCozytouchAway"] = self.notify_cozytouch_away

            # Flush atomically to disk when the settings dialog was confirmed
            # - a crash with restartUnsafely (see the Vocalizer/WASAPI crash)
            # would otherwise discard what was set there. Not when NVDA is
            # told not to save its configuration on exit: then nothing is
            # saved without NVDA+Ctrl+C, and writing nvda.ini here would save
            # NVDA's own unsaved changes along with it. Credentials do not
            # need it any more - they are in their own file, on disk already.
            if secrets_were_in_conf or (flush and self._save_on_exit_wanted()):
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

    def _petlibro_reauth(self, api):
        """Reauth callback for Petlibro when the cloud answers code 1009.

        Called by ``PetlibroAPI._post``. A token ends when anything else logs
        in to the same account - the phone app, for instance - so this is
        the normal way back, not an error.
        """
        if not (self.petlibro_email and self._encrypted_petlibro_password):
            log.warning("Petlibro re-auth not possible - no credentials stored")
            return False
        try:
            pw = self.petlibro_password
            try:
                api.login(self.petlibro_email, pw)
            finally:
                pw = None
                del pw
            creds = api.get_credentials()
            if creds.get("token"):
                self.petlibro_token = creds["token"]
                self.save_settings()
            log.info("Petlibro re-auth successful")
            return True
        except Exception as e:
            _safe_log_error("Petlibro re-auth failed", e)
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
        with self._devices_lock:
            others = [d for d in self.devices if platform_of(d) != platform]
            self.devices = others + list(devices)
        return len(devices)

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------
    # Seconds before a platform whose login failed for a reason other than
    # its credentials is tried again; the last value repeats. Without this a
    # laptop that started NVDA before its Wi-Fi stayed without that platform
    # until somebody opened the settings and saved them.
    _LOGIN_RETRY_DELAYS = (30, 60, 120, 300)

    def _platform_has_credentials(self, platform):
        """Whether ``platform`` has what its login needs."""
        if platform == 'meross':
            return bool(self.email and self._encrypted_password)
        if platform == 'netatmo':
            return bool(self.netatmo_client_id and self.netatmo_refresh_token)
        if platform == 'vesync':
            return bool((self.vesync_email and self._encrypted_vesync_password)
                        or (self.vesync_token and self.vesync_account_id))
        if platform == 'cozytouch':
            return bool(self.cozytouch_email and self._encrypted_cozytouch_password)
        if platform == 'petlibro':
            return bool(self.petlibro_email and self._encrypted_petlibro_password)
        return False

    def _platforms_to_login(self, platforms=None):
        """The platforms out of ``platforms`` (None = all) that can log in.

        Switched on and with credentials, in the order of PLATFORMS.
        """
        return [p for p in PLATFORMS
                if (platforms is None or p in platforms)
                and self._platform_enabled(p)
                and self._platform_has_credentials(p)]

    def _platform_api(self, platform):
        """The API object of a platform, or None while it is not logged in."""
        return {
            'meross': self.api,
            'netatmo': self.netatmo_api,
            'vesync': self.vesync_api,
            'cozytouch': self.cozytouch_api,
            'petlibro': self.petlibro_api,
        }.get(platform)

    def _start_auto_login(self, interactive=False, platforms=None):
        """Starts the login in the background.

        Args:
            interactive: True when the login follows an action by the user
                (settings saved). Only then may a refused login ask for the
                credentials again - at NVDA start a dialog would pop up
                unasked, typically because the network is not up yet.
            platforms: the platforms to log in to; None means every
                platform that is switched on. Saving the settings passes
                only the ones whose credentials changed or that have no
                session yet - it used to log in to all of them again, a
                Meross session of twenty seconds included.
        """
        wanted = self._platforms_to_login(platforms)
        if not wanted:
            return
        log.info(f"Starting the login: {', '.join(wanted)}")
        # Translators: Spoken while the add-on signs in to the platforms at
        # startup.
        ui.message(_("Logging in to {platforms}...").format(
            platforms=", ".join(PLATFORM_LABELS[p] for p in wanted)))
        threading.Thread(
            target=self._do_login, args=(interactive, wanted),
            daemon=True).start()

    def _do_login(self, interactive=False, platforms=None, retry_attempt=None):
        """Logs in to ``platforms`` - all at once, each on a thread of its own.

        One after the other they took thirty seconds (measured 2026-10-01:
        Meross 20, Cozytouch 3.4, Petlibro 3.4, Netatmo 2, VeSync 1), and the
        device menu stayed closed for all of it. In parallel the slowest one
        decides.

        Only the platforms that logged in replace their devices. The list
        used to be replaced as a whole, so a platform that failed a re-login
        - one 503 at the wrong moment - lost its devices while its old
        session went on running.

        A failure that is not a refused credential is tried again later
        (_LOGIN_RETRY_DELAYS). ``retry_attempt`` counts those retries; a
        retry does not announce its failure again.
        """
        platforms = self._platforms_to_login(platforms)
        if not platforms:
            return
        quiet = retry_attempt is not None
        results = {}
        failures = {}
        # The menu waits only while there is nothing to show at all.
        self.is_loading = not self.is_logged_in
        try:
            log.info(f"_do_login: logging in to {', '.join(platforms)}")
            claimed = [p for p in platforms if self.begin_platform_login(p)]
            for p in set(platforms) - set(claimed):
                log.info(f"{PLATFORM_LABELS[p]} login already running - not "
                         f"starting a second one")
            # The sessions running now - see _close_replaced_session.
            before = {p: self._platform_api(p) for p in claimed}
            threads = [threading.Thread(
                target=self._login_one, args=(p, results, failures),
                daemon=True, name=f"SmartHomeControl login {p}")
                for p in claimed]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            for p in PLATFORMS:
                if p in results:
                    if self._take_platform_devices(p, results[p]):
                        self._close_replaced_session(p, before.get(p))

            with self._devices_lock:
                have_devices = bool(self.devices)
            if have_devices:
                self.is_logged_in = True
                self._last_refresh_time = time.time()
                self._start_background_refresh()
            if results:
                # Before the announcement below: the menu's own logins
                # rebuilt their tree first and spoke afterwards.
                self._notify_menu_of_login()

            # Platform names are brand names -> do not translate.
            parts = [f"{len(results[p])} {PLATFORM_LABELS[p]}"
                     for p in PLATFORMS if results.get(p)]
            if parts:
                total = sum(len(results[p]) for p in PLATFORMS if results.get(p))
                # Translators: Success message after login. {total} = number of
                # devices, {detail} = breakdown per platform (e.g. "3 Meross, 1
                # Netatmo").
                wx.CallAfter(ui.message, _(
                    "{total} devices ready ({detail})").format(
                    total=total, detail=", ".join(parts)))
            elif results and not failures and not have_devices:
                # Translators: Announced when the login succeeded but there are
                # no devices in the account.
                wx.CallAfter(ui.message, _("No devices found"))

            refused = []
            retry = []
            # Retries already made. A failure of the network or a cloud is
            # tried again for as long as it lasts; anything else only as many
            # times as there are delays. Without the limit a Meross account
            # with two-factor sign-in (MissingMFA), too many open sessions or
            # an API error code was tried every five minutes for good, with
            # an ERROR - and NVDA's error sound - each time.
            tries = 0 if retry_attempt is None else retry_attempt + 1
            for p in PLATFORMS:
                error = failures.get(p)
                if error is None:
                    continue
                credentials = (p in PASSWORD_PLATFORMS
                               and is_credentials_error(error))
                if credentials:
                    refused.append((p, error))
                elif is_transient(error) or tries < len(self._LOGIN_RETRY_DELAYS):
                    retry.append(p)
                else:
                    log.warning(f"{PLATFORM_LABELS[p]} login keeps failing "
                                f"({type(error).__name__}) - not tried again "
                                f"until the settings are saved or NVDA restarts")
                # The first refusal after a save is spoken by the offer to
                # enter it again (_ask_credential_reentry) - said here as
                # well, it came twice.
                offered = interactive and credentials and len(refused) == 1
                if (not quiet or credentials) and not offered:
                    wx.CallAfter(ui.message, login_error_message(p, error))
            if retry:
                attempt = 0 if retry_attempt is None else retry_attempt
                delay = self._schedule_login_retry(retry, attempt)
                if delay and not quiet:
                    # Translators: Spoken after a login failed because of the
                    # network or the server. {seconds} = when it is tried again.
                    wx.CallAfter(ui.message, _(
                        "New attempt in {seconds} seconds").format(seconds=delay))
            if interactive and refused:
                platform, error = refused[0]
                wx.CallAfter(self._ask_credential_reentry, platform, error)
        except Exception as e:
            _safe_log_error("Login failed", e)
        finally:
            self.is_loading = False
            try:
                _log_library_origins()
            except Exception as e:
                log.debug(f"Library origins not logged: {e}")

    def _login_one(self, platform, results, failures):
        """Thread body: logs in to one platform and notes the outcome."""
        try:
            results[platform] = self._LOGIN_STEPS[platform](self) or []
            log.info(f"{PLATFORM_LABELS[platform]}: "
                     f"{len(results[platform])} device(s) found")
        except Exception as e:
            failures[platform] = e
            _safe_log_error(f"{PLATFORM_LABELS[platform]} login failed", e)
        finally:
            self.end_platform_login(platform)

    def _take_platform_devices(self, platform, devices):
        """Puts the devices of a fresh login into the list - that platform's only.

        An empty answer from a platform that has devices in the list keeps
        them: a short network outage during the login is likelier than an
        account that was emptied in the meantime.

        Returns True when the list now holds the new devices, False when the
        known ones stayed.
        """
        devices = list(devices)
        with self._devices_lock:
            previous = [d for d in self.devices if platform_of(d) == platform]
        if not devices and previous:
            log.warning(f"{PLATFORM_LABELS[platform]} login returned no devices "
                        f"- the {len(previous)} known ones stay")
            return False
        if platform == 'meross':
            # A hub that did not answer lists no sensors - without this its
            # sensors were gone for the rest of the session, and their
            # favourites with them.
            carried = carry_over_hub_subdevices(previous, devices)
            if carried:
                log.warning(f"{len(carried)} hub sensor(s) kept: their hub "
                            "did not list them this time")
                devices = devices + carried
                if self.api is not None:
                    self.api.set_wrapped_devices(devices)
        self.replace_platform_devices(platform, devices)
        # The state the login found is the baseline - not an "external
        # change" on the first poll.
        if platform == 'vesync':
            for vd in devices:
                self._previous_vesync_states[vd.uuid] = self._snapshot_vesync_state(vd)
        elif platform == 'cozytouch':
            for cd in devices:
                self._previous_cozytouch_states[cd.uuid] = self._snapshot_cozytouch_state(cd)
        elif platform == 'petlibro':
            for pd in devices:
                self._seed_petlibro_state(pd)
        return True

    # Platforms whose session of before _do_login closes once the new devices
    # are in the list. Meross closes its own in _login_meross; Netatmo's was
    # closed by neither of the two login paths and still is not.
    _CLOSE_REPLACED_SESSIONS = ('vesync', 'cozytouch', 'petlibro')

    def _close_replaced_session(self, platform, old_api):
        """Closes the session a fresh login of ``platform`` replaced.

        The device menu's own logins did that, the login here did not: every
        save of the settings left a VeSync, Cozytouch or Petlibro session
        open behind it. Only after the devices are replaced - until then the
        devices in the list still work through the old session.
        """
        if (platform not in self._CLOSE_REPLACED_SESSIONS or old_api is None
                or old_api is self._platform_api(platform)):
            return
        try:
            old_api.logout()
        except Exception as e:
            log.debug(f"Closing the old {PLATFORM_LABELS[platform]} session "
                      f"failed: {e}")

    def _notify_menu_of_login(self):
        """Lets an open device menu rebuild its tree after a login.

        The tree holds the device objects it was built from. After a login
        those of that platform are no longer the ones in the list: they are
        not polled any more, and their session may be closed. A retry that
        logged in while the menu was open used to leave them there.
        """
        dialog = getattr(self, '_active_dialog', None)
        if dialog is None or getattr(dialog, '_is_destroyed', False):
            return
        wx.CallAfter(dialog._rebuild_after_login)

    def _schedule_login_retry(self, platforms, attempt):
        """Tries the login of ``platforms`` again after a while.

        Returns the delay in seconds, or 0 when a retry for them is already
        waiting.
        """
        pending = self._login_retry_pending
        # Under the login lock: two login threads failing at the same moment
        # both saw the platform as not pending and scheduled two retries.
        with self._login_lock:
            platforms = [p for p in platforms if p not in pending]
            if not platforms:
                return 0
            pending.update(platforms)
        delays = self._LOGIN_RETRY_DELAYS
        delay = delays[min(attempt, len(delays) - 1)]
        log.info(f"Login of {', '.join(platforms)} is tried again in {delay}s "
                 f"(attempt {attempt + 1})")

        def wait_and_retry():
            if self._login_retry_stop.wait(delay):
                return  # NVDA is shutting down
            with self._login_lock:
                pending.difference_update(platforms)
            # Still wanted, and still without a session? The settings or the
            # device menu may have logged in meanwhile, or switched it off.
            still = [p for p in self._platforms_to_login(platforms)
                     if self._platform_api(p) is None]
            if still:
                self._do_login(False, still, retry_attempt=attempt + 1)

        threading.Thread(target=wait_and_retry, daemon=True,
                         name="SmartHomeControl login retry").start()
        return delay

    # ---- One login per platform. Each returns the platform's devices and
    # sets its API object; an exception means the login failed. ----------
    def _login_meross(self):
        # Take over the new instance only AFTER a successful login: if a
        # re-login fails, the previous (still working) instance incl. MQTT
        # push stays untouched.
        new_api = MerossAPI()
        new_api.set_device_state_changed_callback(self._on_external_device_change)
        new_api.set_throttle_callback(self._on_meross_throttled)
        # Decrypt the password only at login time. login() passes it through
        # as a coroutine argument and deletes it itself, so no permanent
        # plain-text copy remains here or in MerossAPI.
        _tmp_password = self.password
        try:
            new_api.login(self.email, _tmp_password)
        finally:
            _tmp_password = None
            del _tmp_password
        log.info("Meross login successful")
        devices = new_api.get_devices()
        new_api.set_wrapped_devices(devices)
        old_api, self.api = self.api, new_api
        if old_api is not None:
            try:
                old_api.logout()
            except Exception as e:
                log.debug(f"Logout of the old Meross instance failed: {e}")
        return devices

    def _login_netatmo(self):
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
        # EVERY later renewal must arrive here as well - Netatmo rotates
        # refresh tokens, so without this the config kept the ones from the
        # login and a restart hours later restored an already invalidated
        # refresh token.
        new_netatmo.set_token_update_callback(self._on_netatmo_tokens_renewed)
        # Renew the token if necessary
        new_netatmo._ensure_valid_token()
        # Tokens possibly renewed - save them
        tokens = new_netatmo.get_tokens()
        if tokens['access_token'] != self.netatmo_access_token:
            self._on_netatmo_tokens_renewed(tokens)
        devices = new_netatmo.get_devices()
        # get_devices() does not raise: without the network it comes back
        # empty. And with a token still valid nothing else in this login
        # needs the network - so a start without Wi-Fi (2026-10-03, 18:09)
        # counted as a login to an account without devices. The other four
        # platforms failed and were tried again 30 seconds later; Netatmo
        # was left out of that retry and did not come back.
        if not getattr(new_netatmo, 'last_poll_ok', True):
            # Translators: Why a login failed: the cloud did not answer
            # (no network, or the manufacturer's server is down). Spoken
            # after "Netatmo login failed:".
            raise ConnectionError(_("no connection to the cloud"))
        self.netatmo_api = new_netatmo
        return devices

    def _login_vesync(self):
        vs_api = VeSyncAPI(country_code=self.vesync_country_code or "DE")
        # Register the reauth callback: on an expired token (e.g. after days
        # of inactivity) an automatic re-login with the stored password
        # happens - instead of the user having to go into the settings.
        if hasattr(vs_api, 'set_reauth_callback'):
            vs_api.set_reauth_callback(self._vesync_reauth)

        # Preferred: reuse existing tokens, otherwise email/password
        have_tokens = bool(self.vesync_token and self.vesync_account_id)
        if have_tokens:
            vs_api.set_credentials(
                token=self.vesync_token,
                account_id=self.vesync_account_id,
                country_code=self.vesync_country_code or "DE",
                region=self.vesync_region or None,
            )
        elif self.vesync_email and self._encrypted_vesync_password:
            _vs_password = self.vesync_password
            try:
                vs_api.login(self.vesync_email, _vs_password)
            finally:
                _vs_password = None
                del _vs_password
        else:
            # Translators: Error message when VeSync was enabled but neither
            # a token nor email/password are configured.
            raise RuntimeError(_("No VeSync credentials configured"))

        try:
            devices = vs_api.get_devices()
        except RuntimeError as e:
            # Token possibly expired -> retry with the password login
            if not (have_tokens and self.vesync_email
                    and self._encrypted_vesync_password):
                raise
            log.info(f"VeSync: token login failed ({e}) - trying the password login")
            _vs_password = self.vesync_password
            try:
                vs_api.login(self.vesync_email, _vs_password)
            finally:
                _vs_password = None
                del _vs_password
            devices = vs_api.get_devices()

        self.vesync_api = vs_api
        # Save the current tokens (they can change due to cross-region)
        creds = vs_api.get_credentials()
        if creds["token"] and creds["account_id"]:
            self.vesync_token = creds["token"]
            self.vesync_account_id = creds["account_id"]
            self.vesync_country_code = creds["country_code"]
            self.vesync_region = creds["region"]
            self.save_settings()
        return devices

    def _login_cozytouch(self):
        ct_api = CozytouchAPI()
        if hasattr(ct_api, 'set_reauth_callback'):
            ct_api.set_reauth_callback(self._cozytouch_reauth)
        _ct_password = self.cozytouch_password
        try:
            ct_api.login(self.cozytouch_email, _ct_password)
        finally:
            _ct_password = None
            del _ct_password
        devices = ct_api.get_devices()
        self.cozytouch_api = ct_api
        # Save the fresh token
        creds = ct_api.get_credentials()
        if creds.get("token"):
            self.cozytouch_token = creds["token"]
            self.save_settings()
        return devices

    def _login_petlibro(self):
        pl_api = PetlibroAPI()
        pl_api.set_reauth_callback(self._petlibro_reauth)
        if self.petlibro_token:
            # The stored token first: every password login signs the account
            # out everywhere else once more. When it has expired, the first
            # request logs in again through the reauth callback.
            pl_api.set_credentials(self.petlibro_token)
        else:
            _pl_password = self.petlibro_password
            try:
                pl_api.login(self.petlibro_email, _pl_password)
            finally:
                _pl_password = None
                del _pl_password
        try:
            devices = pl_api.get_devices()
        except PetlibroError as e:
            # The re-login behind an expired token swallows the reason;
            # logging in here again brings it back, so a refused password is
            # asked for instead of reported as a vague cloud error.
            if e.code != 1009 or not self.petlibro_token:
                raise
            _pl_password = self.petlibro_password
            try:
                pl_api.login(self.petlibro_email, _pl_password)
            finally:
                _pl_password = None
                del _pl_password
            devices = pl_api.get_devices()
        self.petlibro_api = pl_api
        creds = pl_api.get_credentials()
        if creds.get("token") and creds["token"] != self.petlibro_token:
            self.petlibro_token = creds["token"]
            self.save_settings()
        return devices

    _LOGIN_STEPS = {
        'meross': _login_meross,
        'netatmo': _login_netatmo,
        'vesync': _login_vesync,
        'cozytouch': _login_cozytouch,
        'petlibro': _login_petlibro,
    }

    def _ask_credential_reentry(self, platform, error):
        """Offers to enter the refused credentials again (main thread).

        With the device menu open, the menu makes the offer, as it did when
        it ran logins of its own: the dialogs belong to the menu, and a save
        there goes through its _apply_settings_change.
        """
        dialog = getattr(self, '_active_dialog', None)
        if dialog is not None and not getattr(dialog, '_is_destroyed', False):
            dialog._offer_login_reentry(platform, error)
            return

        def _after_save(changed):
            if changed:
                self._start_auto_login(interactive=True, platforms=changed)

        if getattr(self, '_settings_open', False):
            # The settings are open already - the reason is enough.
            ui.message(login_error_message(platform, error))
            return
        # prePopup/postPopup as for every dialog of this add-on: NVDA needs
        # them to hand the focus over and give it back afterwards.
        self._settings_open = True
        gui.mainFrame.prePopup()
        try:
            offer_credential_reentry(
                gui.mainFrame, self, platform, error, on_saved=_after_save)
        except Exception as e:
            log.error(f"Credential re-entry failed: {type(e).__name__}: {e}")
        finally:
            self._settings_open = False
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
        # postPopup in a finally: a constructor that raised used to leave
        # NVDA's popup count raised, and the focus was not handed back.
        gui.mainFrame.prePopup()
        try:
            dlg = SmartHomeControlDialog(gui.mainFrame, self)
            self._active_dialog = dlg  # store the reference for live updates
            # Trigger an immediate poll at the foreground rate so external
            # changes (Levoit app or physical controls) arrive in the dialog
            # quickly. The scheduler detects the open dialog itself and polls
            # at the shorter foreground interval from now on.
            # request_immediate_poll() also wakes the scheduler: it sleeps
            # until the next due poll and would otherwise see the flag only
            # with a delay.
            self.request_immediate_poll()
            try:
                dlg.ShowModal()
            finally:
                self._active_dialog = None  # clear the reference when the dialog is closed
                dlg.Destroy()
        finally:
            gui.mainFrame.postPopup()
    
    def _show_settings(self):
        """Shows the settings dialog"""
        # One at a time, like the device menu: two of them saved over each
        # other, the second one with the values it was opened with.
        if getattr(self, '_settings_open', False):
            # Translators: Spoken when the settings are opened while they are
            # open already.
            ui.message(_("The settings are already open"))
            return
        self._settings_open = True
        gui.mainFrame.prePopup()
        try:
            dlg = SmartHomeSettingsDialog(gui.mainFrame, self)
            try:
                saved = dlg.ShowModal() == wx.ID_OK
                changed = set(getattr(dlg, 'changed_platforms', ()))
            finally:
                dlg.Destroy()
            if saved:
                self._apply_saved_settings(changed)
        finally:
            self._settings_open = False
            gui.mainFrame.postPopup()

    def _apply_saved_settings(self, changed):
        """Logs in what the saved settings call for - and nothing else.

        Every Save used to log in to every platform again: a new Meross
        session with its whole device discovery for a changed notification
        switch, and a platform whose re-login hit a network error lost its
        devices. Now only platforms with new credentials, and switched-on
        platforms without a session, log in; a switched-off one loses its
        devices.

        The device menu saves through here as well. It used to run a login
        of its own per platform - five copies of the ones below, without the
        retry after a network error, and Netatmo there was taken without
        asking the cloud at all.

        Returns how many devices of switched-off platforms were dropped -
        the device menu rebuilds its tree for that.
        """
        dropped = self.drop_disabled_platform_devices()
        # The settings clear the Netatmo tokens when the client ID changes -
        # the authorisation belongs to the old app registration then.
        if (self.use_netatmo and 'netatmo' in changed
                and self.netatmo_client_id and not self.netatmo_refresh_token):
            # Translators: Note after new Netatmo client data: the OAuth2
            # authorisation in the browser has to be granted again.
            ui.message(_("Netatmo: authorise again with the Connect button in "
                         "the settings"))
        if self.is_logged_in:
            platforms = [p for p in PLATFORMS if self._platform_enabled(p)
                         and (p in changed or self._platform_api(p) is None)]
        else:
            platforms = None
        # interactive: the login follows the save, so a refused password may
        # ask to be entered again.
        self._start_auto_login(interactive=True, platforms=platforms)
        return dropped

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
                            today=fmt_decimal(kwh_today, 2),
                            week=fmt_decimal(kwh_week, 2),
                            watt=fmt_number(watt)))
                    else:
                        # Translators: Energy summary from the device's own
                        # meter without a current power value.
                        parts.append(_(
                            "{name}: today {today} kilowatt hours, last 7 "
                            "days {week} kilowatt hours").format(
                            name=dev.name,
                            today=fmt_decimal(kwh_today, 2),
                            week=fmt_decimal(kwh_week, 2)))
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
                        today=fmt_decimal(kwh_today, 2),
                        week=fmt_decimal(kwh_week, 2),
                        watt=fmt_number(last_watt)))
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
            ('meross', 'Meross', self.use_meross, self._meross_connected),
            ('netatmo', 'Netatmo', self.use_netatmo, self._netatmo_connected),
            ('vesync', 'VeSync', self.use_vesync, self._vesync_connected),
            ('cozytouch', 'Cozytouch', self.use_cozytouch, self._cozytouch_connected),
            ('petlibro', 'Petlibro', self.use_petlibro, self._petlibro_connected),
        )
        for name, label, active, connected in platform_states:
            if not active:
                continue
            # The record knows more than the flag: since when a platform has
            # been silent, and whether only a part of it is missing.
            entry = self.platform_health.get(name)
            detail = platform_health.status_suffix(entry)
            if detail:
                state = detail
            elif connected is True:
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
            # refresh of any platform. (It used to say "Meross" although
            # every platform sets this time.)
            parts.append(_("Last refresh {seconds} seconds ago").format(
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
            # A shortcut with Alt, Control, Windows or NVDA (Alt+Tab,
            # Windows+D, Control+C ...) ends the layer and goes on to where it
            # was meant for. It used to be swallowed with the error tone like
            # a stray letter - the one Alt+Tab was lost. Shift alone does not
            # count: Shift+letter is typing, and that stays swallowed.
            held = {name.lower() for name in
                    (getattr(gesture, 'modifierNames', None) or ())}
            if held & {'alt', 'control', 'windows', 'nvda'}:
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
        if (getattr(device, 'is_netatmo', False)
                or getattr(device, 'is_sensor', False)
                or getattr(device, 'is_petlibro', False)):
            # Devices that cannot be switched: sensors (Meross MS100/MS400
            # ...) and all Netatmo devices - thermostats can be adjusted but
            # not switched on/off, weather stations only display. A Petlibro
            # feeder has no on/off either, and a digit pressed twice must
            # never give a meal. This branch
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
        if getattr(device, 'is_opener', False):
            # A garage door or a roller shutter: never from two digits.
            _beep(BEEP_ERROR)
            # Translators: Error when a device is recognised but cannot be
            # operated yet. {name} = device name, {type} = model designation.
            ui.message(_("{name} is shown but cannot be operated yet "
                         "({type})").format(
                name=device.name, type=getattr(device, 'type', '?')))
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
                parts.append(_("{temp}°C").format(temp=fmt_decimal(temp, 1)))
            hum = device.get_humidity() if hasattr(device, 'get_humidity') else None
            if hum is not None:
                # Translators: Relative humidity in the status announcement.
                parts.append(_("{value}% humidity").format(value=fmt_number(hum)))
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
                parts.append(_("{watt} watts").format(watt=fmt_number(power)))
        battery = (device.get_battery_percent()
                   if hasattr(device, 'get_battery_percent') else None)
        if battery is not None:
            # Translators: Battery level in the status announcement.
            parts.append(_("battery {percent}%").format(percent=battery))
        return parts

    @scriptHandler.script(
        # Translators: Description of the script in the NVDA input gestures
        # dialog.
        description=_("Announces a summary of all smart home devices; pressed "
                      "twice, shows every device in a list"),
        gesture="kb:NVDA+control+shift+p",
    )
    def script_announceStatus(self, gesture):
        """A short summary; pressed twice, every device in a readable list.

        It used to speak every device, one after the other, as a single
        utterance - for an account of fifty devices two minutes of speech
        that could neither be navigated nor stopped at the one device that
        mattered. The summary says what needs attention; the list is a
        browse mode document, read line by line and on the braille display.
        """
        as_list = scriptHandler.getLastScriptRepeatCount() >= 1
        log.debug(f"script_announceStatus called (list={as_list})")

        if not self.is_logged_in:
            ui.message(_("Not logged in"))
            return

        # With a fresh cache: at once, without waiting.
        with self._devices_lock:
            cached_devices = list(self.devices)
        if self.is_cache_fresh() and cached_devices:
            self._announce_devices_status(cached_devices, as_list)
            return

        # Cache not fresh - short update in the background
        log.debug("Cache not fresh - refreshing in the background")
        self._start_status_beep()

        def task():
            try:
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
                    for api, enabled in (
                            (self.netatmo_api, self.use_netatmo),
                            (self.vesync_api, self.use_vesync),
                            (self.cozytouch_api, self.use_cozytouch),
                            (self.petlibro_api, self.use_petlibro)):
                        if api and enabled:
                            try:
                                all_devs.extend(api.get_devices())
                            except Exception as e:
                                log.debug(f"Devices could not be loaded: {e}")
                    # Assign under the lock - the scheduler thread reads the
                    # same list in parallel (consistent with all other write
                    # sites).
                    with self._devices_lock:
                        all_devs = all_devs + carry_over_hub_subdevices(
                            self.devices, all_devs)
                        self.devices = all_devs
                    self._last_refresh_time = time.time()
                else:
                    with self._devices_lock:
                        by_platform = split_by_platform(self.devices)
                    for platform, api in (
                            ('meross', self.api),
                            ('netatmo', self.netatmo_api),
                            ('vesync', self.vesync_api),
                            ('cozytouch', self.cozytouch_api),
                            ('petlibro', self.petlibro_api)):
                        devs = by_platform[platform]
                        if not (devs and api):
                            continue
                        try:
                            api.update_device_status(devs)
                        except Exception as e:
                            # Cached data is better than nothing.
                            log.debug(f"{PLATFORM_LABELS[platform]} status "
                                      f"update failed: {e}")
                    self._last_refresh_time = time.time()

                with self._devices_lock:
                    devs_for_status = list(self.devices)
                self._stop_status_beep()
                if not devs_for_status:
                    wx.CallAfter(ui.message, _("No devices found"))
                    return
                wx.CallAfter(_beep, BEEP_SUCCESS)
                wx.CallAfter(self._announce_devices_status, devs_for_status,
                             as_list)
            except Exception as e:
                _safe_log_error("Failed to fetch the status", e)
                self._stop_status_beep()
                wx.CallAfter(_beep, BEEP_ERROR)
                error_msg = str(e)
                if len(error_msg) > 50:
                    error_msg = error_msg[:50] + "..."
                # Translators: Generic error message with detail text.
                wx.CallAfter(ui.message, _("Error: {error}").format(error=error_msg))

        threading.Thread(target=task, daemon=True).start()

    def _announce_devices_status(self, devices, as_list=False):
        """Speaks the summary, or shows every device as a readable list."""
        if as_list:
            # Translators: Title of the window that lists every device with
            # its status (the status command pressed twice).
            ui.browseableMessage("\n".join(self._device_status_lines(devices)),
                                 _("Smart home status"))
            return
        ui.message(self._status_summary(devices))

    def _status_summary(self, devices):
        """One short sentence or two: how many, how many on, what needs care."""
        by_platform = split_by_platform(devices)
        detail = ", ".join(f"{len(devs)} {PLATFORM_LABELS[name]}"
                           for name, devs in by_platform.items() if devs)
        offline = sorted((d for d in devices
                          if getattr(d, 'is_offline', False)),
                         key=lambda d: d.name.lower())
        switched_on = sum(1 for d in devices
                          if not getattr(d, 'is_offline', False)
                          and self._counts_as_switched_on(d))
        # Translators: Summary of the status command. {count} = devices,
        # {detail} = per platform (e.g. "3 Meross, 1 Netatmo"), {on} =
        # devices switched on, {offline} = devices without connection.
        parts = [_("{count} devices ({detail}), {on} switched on, {offline} "
                   "offline.").format(count=len(devices), detail=detail,
                                      on=switched_on, offline=len(offline))]
        if 0 < len(offline) <= 5:
            # Translators: Names of the offline devices in the status
            # summary.
            parts.append(_("Offline: {names}.").format(
                names=", ".join(d.name for d in offline)))
        for device in devices:
            if (getattr(device, 'is_water_sensor', False)
                    and not getattr(device, 'is_offline', False)):
                try:
                    wet = device.is_water_detected()
                except Exception:
                    wet = False
                if wet:
                    # Translators: Alarm of a Meross water sensor. {name} = device.
                    parts.append(_("Water alarm: {name}").format(name=device.name) + ".")
        return " ".join(parts)

    @staticmethod
    def _counts_as_switched_on(device):
        """Whether a device is "on" in the sense of the summary.

        Only devices that can be switched: sensors, hubs, Netatmo modules and
        Petlibro devices have no on/off, and their is_on says nothing.
        """
        if (getattr(device, 'is_netatmo', False)
                or getattr(device, 'is_petlibro', False)
                or getattr(device, 'is_sensor', False)
                or getattr(device, 'is_hub', False)):
            return False
        try:
            return bool(device.is_on)
        except Exception:
            return False

    def _device_status_lines(self, devices):
        """One line per device, alphabetical: "Name: status"."""
        # Translators: Announced when a device currently provides no
        # sensor/status data.
        no_data = _("No data")
        # Translators: Announced when a device is currently unreachable.
        offline_text = _("offline")
        lines = []
        for device in sorted(devices, key=lambda d: d.name.lower()):
            try:
                if hasattr(device, 'get_status_summary'):
                    # Netatmo, VeSync, Cozytouch and Petlibro bring their own
                    # summary - the one the favourites announce too.
                    summary = device.get_status_summary()
                    lines.append(f"{device.name}: {summary or no_data}")
                    continue
                if getattr(device, 'is_offline', False):
                    lines.append(f"{device.name}: {offline_text}")
                    continue
                status = ", ".join(str(p) for p in
                                   self._meross_status_parts(device) if p)
                lines.append(f"{device.name}: {status or no_data}")
            except Exception as e:
                log.warning(f"Failed to fetch the data of {device.name}: {e}")
                # Translators: Generic fallback when the status query for a
                # single device fails.
                lines.append(f"{device.name}: {_('Error')}")
        return lines

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
            'petlibro': self.use_petlibro,
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
        from .history import _device_key
        try:
            with self._devices_lock:
                by_platform = split_by_platform(self.devices)
                meross_devs = by_platform['meross']
                netatmo_devs = by_platform['netatmo']
                vesync_devs = by_platform['vesync']
                cozytouch_devs = by_platform['cozytouch']
                petlibro_devs = by_platform['petlibro']

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

            # Update Netatmo - a full pass, since the user asked for it, but
            # IN PLACE. It used to replace the device objects with fresh
            # ones; the open tree kept the old objects, which nothing updated
            # any more, so after F5 its Netatmo values stood still.
            if self.netatmo_api and self.use_netatmo:
                log.debug("Updating the Netatmo devices...")
                try:
                    added = self.netatmo_api.update_device_status(
                        netatmo_devs, full=True) or []
                    netatmo_devs = netatmo_devs + list(added)
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
                        # done at the user's explicit request) - with the
                        # boost bookkeeping kept in step: skipping it, a boost
                        # that came on before F5 had no start, and its end
                        # counted as somebody's doing.
                        for cd in cozytouch_devs:
                            snapshot = self._snapshot_cozytouch_state(cd)
                            self._note_cozytouch_boost(
                                cd.uuid,
                                self._previous_cozytouch_states.get(cd.uuid),
                                snapshot, cd.boost_total_time)
                            self._previous_cozytouch_states[cd.uuid] = snapshot
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

            # Update Petlibro (status of the existing devices; discovery if
            # needed)
            if self.petlibro_api and self.use_petlibro:
                try:
                    if petlibro_devs:
                        self.petlibro_api.update_device_status(petlibro_devs)
                    else:
                        petlibro_devs = self.petlibro_api.get_devices()
                    # The user asked for this refresh: take the new state as
                    # the baseline instead of announcing it.
                    for pd in petlibro_devs:
                        self._seed_petlibro_state(pd)
                    self._mark_platform_refreshed('petlibro')
                except Exception as e:
                    log.debug(f"Petlibro status refresh failed: {e}")

            with self._devices_lock:
                new_list = (meross_devs + netatmo_devs + vesync_devs
                            + cozytouch_devs + petlibro_devs)
                # Keep devices that a scheduler poll running in parallel
                # discovered/appended between the snapshot and the reassignment
                # (otherwise a lost update: they would silently drop out).
                # _device_key and not uuid: every sensor on a Meross hub
                # carries the hub's uuid, so a single sensor in the new list
                # made all its siblings count as known, and a second sensor
                # the parallel poll had found dropped out silently.
                known_keys = {_device_key(d) for d in new_list}
                for d in self.devices:
                    if _device_key(d) not in known_keys:
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
            log_failure("Failed to update the devices", e)
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
                # Marked before sending, as in the dialog: the poll runs on a
                # thread of its own and can read the new state while the
                # command is still on its way.
                self._record_local_vesync_action(device_uuid)
                device.toggle_switch(new_state)
                status = _("on") if new_state else _("off")
                self._record_local_toggle(device_uuid, new_state)
                # Also mark the local action for the push detection so the next
                # background refresh does not announce the user's own switching
                # as an external change.
                self._record_local_vesync_action(device_uuid)
                self._accept_local_change('vesync', device)
                self._log_toggle(device, new_state)
                return _("{name}: {status}").format(name=device.name, status=status)

            # Petlibro: nothing to switch. Refused here as well, so no path can
            # turn a "toggle" into a feeding.
            if getattr(device, 'is_petlibro', False):
                # Translators: Message when the user tries to switch a device
                # that cannot be switched on/off (sensors, Netatmo devices).
                raise ValueError(_("{name}: cannot be switched – adjustable in "
                                   "the device menu").format(name=device.name))

            # Cozytouch devices: hot water production on/off (analogous to the
            # dialog mixin)
            if getattr(device, 'is_cozytouch', False):
                new_state = not device.is_on
                # Marked before sending, as in the dialog: set_dhw waits up
                # to half a minute for the appliance, and a poll in between
                # read the new state as a change made elsewhere.
                self._record_local_cozytouch_action(device_uuid)
                status = _("on") if new_state else _("off")
                # Translators: Error message when toggling hot water
                # production fails.
                failure = _("Hot water could not be toggled")
                try:
                    ok = device.set_dhw(new_state)
                except CozytouchPending as pending:
                    # Accepted, not shown yet - followed by the polls, as in
                    # the dialog (see _run_cozytouch_cloud_action).
                    self._record_local_cozytouch_action(device_uuid)
                    self._expect_cozytouch(
                        device, pending.field, pending.expected,
                        # Translators: Confirmation after switching hot water
                        # production on/off.
                        confirm=_("{name}: operation {status}").format(
                            name=device.name, status=status),
                        fail=f"{device.name}: {failure}",
                        history_action='toggle_on' if new_state else 'toggle_off')
                    # Translators: A change was sent to a device that has not
                    # answered the cloud yet.
                    return _("{name}: sent, but the device has not answered "
                             "yet - the change is announced as soon as the "
                             "device reports it.").format(name=device.name)
                if not ok:
                    raise RuntimeError(failure)
                self._record_local_toggle(device_uuid, new_state)
                self._record_local_cozytouch_action(device_uuid)
                self._accept_local_change('cozytouch', device)
                self._log_toggle(device, new_state)
                return _("{name}: {status}").format(name=device.name, status=status)

            # Garage door openers and roller shutters are shown, not
            # switched (see meross_devices._is_opener).
            if getattr(device, 'is_opener', False):
                # Translators: Error when a device is recognised but cannot be
                # operated yet. {name} = device name, {type} = model designation.
                raise ValueError(_("{name} is shown but cannot be operated yet "
                                   "({type})").format(
                    name=device.name, type=getattr(device, 'type', '?')))

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
            # Marked before sending: the device's push of its new state can
            # arrive before this call returns (see _begin_local_toggle).
            self._begin_local_toggle(device_uuid, new_state)
            try:
                self.api.set_device_state(device.uuid, new_state, channel=channel)
            except Exception:
                self._forget_local_toggle(device_uuid)
                raise
            
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
            log_failure("Toggling failed", e)
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
            log_failure("Failed to set the diffuser mode", e)
            raise



# Note: the former 18 individual scripts ("toggle favorite N" / "announce
# the status of favorite N") are replaced by the favorites layer
# (script_favoritesLayer in the class): one gesture, then a digit 1-9.
# Orphaned gestures.ini entries pointing at the old script names are
# harmless - NVDA ignores bindings to scripts that do not exist.
