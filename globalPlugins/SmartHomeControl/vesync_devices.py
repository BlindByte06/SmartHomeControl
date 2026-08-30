# -*- coding: utf-8 -*-
"""
VeSync device wrappers

Defines wrapper objects for VeSync devices that fit the existing device
architecture (Meross, Netatmo). Each device carries the shared flags
is_vesync, is_offline, is_on etc. and provides device-specific actions.

Implemented following the pyvesync library (BypassV2 API):
  - Levoit Core 200S/300S/400S/500S/600S air purifiers
  - Levoit tower fans (LTF-F422S series)

"""

import threading
import time

from logHandler import log

import addonHandler
try:
    addonHandler.initTranslation()
except Exception as e:
    log.debug(f"initTranslation failed: {e}")
if "_" not in globals():  # fallback outside of NVDA
    def _(s):
        return s


def _plausible_reading(value, minimum=0):
    """A sensor reading that can be believed, else None.

    A particulate concentration cannot be negative. Levoit purifiers send
    -1 for the particulate sensor when it has nothing to report: one Core
    300S did so on roughly every second hourly poll for days on end, while
    an identical unit next to it never did. Without this check the value
    reached the interface as "PM2.5: -1 µg/m³" - announced and put on the
    braille display - and was written to the history as a measurement,
    where 32 of 190 stored readings ended up being dropouts.

    Booleans are rejected on purpose: ``isinstance(True, int)`` is true in
    Python, and a flag that slipped into a numeric field would otherwise
    be stored as 1.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < minimum:
        return None
    return value


# Protection window for user-controlled fields. After a setter,
# ``apply_status_response`` must not overwrite the affected field from the
# (often minutes-long cached) bypassV2 cloud cache for this long.
PROTECT_WINDOW = 60.0
# Protection window for ``_is_on`` from the device list (shorter, because the
# device list becomes fresh faster than bypassV2).
TOGGLE_WINDOW = 8.0


# ============================================================
# Mode constants
# ============================================================
PURIFIER_MODE_AUTO = "auto"
PURIFIER_MODE_MANUAL = "manual"
PURIFIER_MODE_SLEEP = "sleep"

NIGHTLIGHT_MODE_ON = "on"
NIGHTLIGHT_MODE_OFF = "off"
NIGHTLIGHT_MODE_DIM = "dim"

AUTO_PREFERENCE_DEFAULT = "default"
AUTO_PREFERENCE_EFFICIENT = "efficient"
AUTO_PREFERENCE_QUIET = "quiet"

FAN_MODE_NORMAL = "normal"
FAN_MODE_TURBO = "turbo"
FAN_MODE_AUTO = "auto"
FAN_MODE_SLEEP = "advancedSleep"

# Feature flags (analogous to pyvesync.const.PurifierFeatures)
PURIFIER_FEATURE_AIR_QUALITY = "air_quality"  # PM2.5 + level
PURIFIER_FEATURE_NIGHTLIGHT = "nightlight"  # night light (on/off/dim)
PURIFIER_FEATURE_RESET_FILTER = "reset_filter"  # reset filter
PURIFIER_FEATURE_CHILD_LOCK = "child_lock"  # child lock
PURIFIER_FEATURE_DISPLAY = "display"  # display control
PURIFIER_FEATURE_AUTO_PREFERENCE = "auto_preference"  # auto profile
PURIFIER_FEATURE_TIMER = "timer"  # timer
PURIFIER_FEATURE_PM1 = "pm1"  # PM1.0
PURIFIER_FEATURE_PM10 = "pm10"  # PM10
PURIFIER_FEATURE_LIGHT_DETECT = "light_detect"  # light detection


# ============================================================
# Device configuration tables
# ============================================================
# Levoit Core air purifiers (Bypass V2 API with snake_case payload). Modes,
# levels, auto preferences and features match pyvesync.device_map.
def _core_basic_features():
    """Features shared by all Core models."""
    return [
        PURIFIER_FEATURE_CHILD_LOCK,
        PURIFIER_FEATURE_DISPLAY,
        PURIFIER_FEATURE_RESET_FILTER,
        PURIFIER_FEATURE_TIMER,
    ]


def _core_with_air_quality():
    """Features for Core 300S/400S/500S/600S (with PM2.5)."""
    return _core_basic_features() + [
        PURIFIER_FEATURE_AIR_QUALITY,
        PURIFIER_FEATURE_AUTO_PREFERENCE,
    ]


VESYNC_PURIFIER_TYPES = {
    # Core 200S - sleep & manual, night light, no air quality
    "Core200S": {
        "alias": "Core 200S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL],
        "fan_levels": [1, 2, 3],
        "features": _core_basic_features() + [PURIFIER_FEATURE_NIGHTLIGHT],
        "nightlight_modes": [NIGHTLIGHT_MODE_ON, NIGHTLIGHT_MODE_OFF, NIGHTLIGHT_MODE_DIM],
    },
    "LAP-C201S-AUSR": {
        "alias": "Core 200S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL],
        "fan_levels": [1, 2, 3],
        "features": _core_basic_features() + [PURIFIER_FEATURE_NIGHTLIGHT],
        "nightlight_modes": [NIGHTLIGHT_MODE_ON, NIGHTLIGHT_MODE_OFF, NIGHTLIGHT_MODE_DIM],
    },
    "LAP-C202S-WUSR": {
        "alias": "Core 200S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL],
        "fan_levels": [1, 2, 3],
        "features": _core_basic_features() + [PURIFIER_FEATURE_NIGHTLIGHT],
        "nightlight_modes": [NIGHTLIGHT_MODE_ON, NIGHTLIGHT_MODE_OFF, NIGHTLIGHT_MODE_DIM],
    },

    # Core 300S
    "Core300S": {
        "alias": "Core 300S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL, PURIFIER_MODE_AUTO],
        "fan_levels": [1, 2, 3],
        "features": _core_with_air_quality(),
        "auto_preferences": [AUTO_PREFERENCE_DEFAULT, AUTO_PREFERENCE_EFFICIENT, AUTO_PREFERENCE_QUIET],
    },
    "LAP-C301S-WJP": {
        "alias": "Core 300S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL, PURIFIER_MODE_AUTO],
        "fan_levels": [1, 2, 3],
        "features": _core_with_air_quality(),
        "auto_preferences": [AUTO_PREFERENCE_DEFAULT, AUTO_PREFERENCE_EFFICIENT, AUTO_PREFERENCE_QUIET],
    },
    "LAP-C302S-WUSB": {
        "alias": "Core 300S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL, PURIFIER_MODE_AUTO],
        "fan_levels": [1, 2, 3],
        "features": _core_with_air_quality(),
        "auto_preferences": [AUTO_PREFERENCE_DEFAULT, AUTO_PREFERENCE_EFFICIENT, AUTO_PREFERENCE_QUIET],
    },
    "LAP-C301S-WAAA": {
        "alias": "Core 300S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL, PURIFIER_MODE_AUTO],
        "fan_levels": [1, 2, 3],
        "features": _core_with_air_quality(),
        "auto_preferences": [AUTO_PREFERENCE_DEFAULT, AUTO_PREFERENCE_EFFICIENT, AUTO_PREFERENCE_QUIET],
    },
    "LAP-C302S-WGC": {
        "alias": "Core 300S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL, PURIFIER_MODE_AUTO],
        "fan_levels": [1, 2, 3],
        "features": _core_with_air_quality(),
        "auto_preferences": [AUTO_PREFERENCE_DEFAULT, AUTO_PREFERENCE_EFFICIENT, AUTO_PREFERENCE_QUIET],
    },

    # Core 400S
    "Core400S": {
        "alias": "Core 400S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL, PURIFIER_MODE_AUTO],
        "fan_levels": [1, 2, 3, 4],
        "features": _core_with_air_quality(),
        "auto_preferences": [AUTO_PREFERENCE_DEFAULT, AUTO_PREFERENCE_EFFICIENT, AUTO_PREFERENCE_QUIET],
    },
    "LAP-C401S-WJP": {
        "alias": "Core 400S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL, PURIFIER_MODE_AUTO],
        "fan_levels": [1, 2, 3, 4],
        "features": _core_with_air_quality(),
        "auto_preferences": [AUTO_PREFERENCE_DEFAULT, AUTO_PREFERENCE_EFFICIENT, AUTO_PREFERENCE_QUIET],
    },
    "LAP-C401S-WUSR": {
        "alias": "Core 400S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL, PURIFIER_MODE_AUTO],
        "fan_levels": [1, 2, 3, 4],
        "features": _core_with_air_quality(),
        "auto_preferences": [AUTO_PREFERENCE_DEFAULT, AUTO_PREFERENCE_EFFICIENT, AUTO_PREFERENCE_QUIET],
    },
    "LAP-C401S-WAAA": {
        "alias": "Core 400S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL, PURIFIER_MODE_AUTO],
        "fan_levels": [1, 2, 3, 4],
        "features": _core_with_air_quality(),
        "auto_preferences": [AUTO_PREFERENCE_DEFAULT, AUTO_PREFERENCE_EFFICIENT, AUTO_PREFERENCE_QUIET],
    },

    # Core 500S
    "Core500S": {
        "alias": "Core 500S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL, PURIFIER_MODE_AUTO],
        "fan_levels": [1, 2, 3, 4, 5],
        "features": _core_with_air_quality(),
        "auto_preferences": [AUTO_PREFERENCE_DEFAULT, AUTO_PREFERENCE_EFFICIENT, AUTO_PREFERENCE_QUIET],
    },
    "LAP-C501S-WUS": {
        "alias": "Core 500S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL, PURIFIER_MODE_AUTO],
        "fan_levels": [1, 2, 3, 4, 5],
        "features": _core_with_air_quality(),
        "auto_preferences": [AUTO_PREFERENCE_DEFAULT, AUTO_PREFERENCE_EFFICIENT, AUTO_PREFERENCE_QUIET],
    },
    "LAP-C501S-WEU": {
        "alias": "Core 500S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL, PURIFIER_MODE_AUTO],
        "fan_levels": [1, 2, 3, 4, 5],
        "features": _core_with_air_quality(),
        "auto_preferences": [AUTO_PREFERENCE_DEFAULT, AUTO_PREFERENCE_EFFICIENT, AUTO_PREFERENCE_QUIET],
    },

    # Core 600S
    "Core600S": {
        "alias": "Core 600S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL, PURIFIER_MODE_AUTO],
        "fan_levels": [1, 2, 3, 4],
        "features": _core_with_air_quality(),
        "auto_preferences": [AUTO_PREFERENCE_DEFAULT, AUTO_PREFERENCE_EFFICIENT, AUTO_PREFERENCE_QUIET],
    },
    "LAP-C601S-WUS": {
        "alias": "Core 600S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL, PURIFIER_MODE_AUTO],
        "fan_levels": [1, 2, 3, 4],
        "features": _core_with_air_quality(),
        "auto_preferences": [AUTO_PREFERENCE_DEFAULT, AUTO_PREFERENCE_EFFICIENT, AUTO_PREFERENCE_QUIET],
    },
    "LAP-C601S-WUSR": {
        "alias": "Core 600S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL, PURIFIER_MODE_AUTO],
        "fan_levels": [1, 2, 3, 4],
        "features": _core_with_air_quality(),
        "auto_preferences": [AUTO_PREFERENCE_DEFAULT, AUTO_PREFERENCE_EFFICIENT, AUTO_PREFERENCE_QUIET],
    },
    "LAP-C601S-WEU": {
        "alias": "Core 600S",
        "modes": [PURIFIER_MODE_SLEEP, PURIFIER_MODE_MANUAL, PURIFIER_MODE_AUTO],
        "fan_levels": [1, 2, 3, 4],
        "features": _core_with_air_quality(),
        "auto_preferences": [AUTO_PREFERENCE_DEFAULT, AUTO_PREFERENCE_EFFICIENT, AUTO_PREFERENCE_QUIET],
    },
}


# Levoit tower fans (Classic 42-Inch Tower Fan, LTF-F422S series). Mode mapping
# follows pyvesync.device_map.fan_modules
def _tower_fan_config():
    """Shared configuration of all LTF-F422S models.

    ``alias`` contains the official Levoit model name so the user sees the
    same format in the dialog as for the air purifiers (e.g. ``Core 300S``
    <-> ``LTF-F422S``). For several regional variants of the same model the
    exact ``deviceType`` string (KEU/WUSR/WJP/WUS) can still be added in the
    wrapper constructor if it differs from the alias.
    """
    return {
        "alias": "LTF-F422S Tower-Ventilator",
        "modes": {
            FAN_MODE_NORMAL: "normal",
            FAN_MODE_TURBO: "turbo",
            FAN_MODE_AUTO: "auto",
            FAN_MODE_SLEEP: "advancedSleep",
        },
        "fan_levels": list(range(1, 13)),
    }


VESYNC_FAN_TYPES = {
    "LTF-F422S-KEU": _tower_fan_config(),
    "LTF-F422S-WUSR": _tower_fan_config(),
    "LTF-F422S-WJP": _tower_fan_config(),
    "LTF-F422S-WUS": _tower_fan_config(),
}


# ============================================================
# Air fryers (Cosori)
# ============================================================
# Listed by model family without the regional suffix, so every variant
# (-KEU, -KUS, -AEUR, -KUK ...) is covered by resolve_device_config.
#
# These devices are shown, not operated. What they report is settled (see
# VeSyncAirFryer): getAirfryerStatus answers with the cooking state, the
# programme, both temperatures and the remaining time. The open source work
# on VeSync covers only the older single-element Cosori fryers over a
# different protocol, so none of that could be taken over as it stood.
#
# An account holding only such a device used to come out as "no devices",
# which reads exactly like a refused login; showing it with its name and
# state is worth more than the silence, even without a single control.
VESYNC_FRYER_TYPES = {
    "CAF-P583S": {"alias": "Cosori Dual Blaze"},
}


# ============================================================
# Model lookup (regional-variant tolerant)
# ============================================================

def _model_family(device_type):
    """Model family of a VeSync device type, without the regional suffix.

    VeSync reports the full model string including a region code:
        LAP-C201S-AUSR (Australia), LAP-C201S-WEU (Europe),
        LAP-C202S-WUSR (USA), LTF-F422S-KEU, ...
    The first two segments identify the model, the last one only the sales
    region: ``LAP-C201S-WEU`` -> ``LAP-C201S``. Types without a suffix
    (``Core200S``) are returned unchanged.
    """
    parts = (device_type or '').split('-')
    if len(parts) >= 2:
        return '-'.join(parts[:2])
    return device_type or ''


def resolve_device_config(device_type, table):
    """Finds the feature profile for a device type in ``table``.

    Exact match first, then the model family. The family fallback exists
    because the profiles WITHIN one family are identical - all
    ``LAP-C301S-*`` entries carry the same modes, fan levels and features;
    only the sales region differs. Without it, every regional variant would
    have to be listed by hand, and a device whose exact string is missing
    disappears from the menu entirely (``_wrap_device`` returns None) with
    nothing but a DEBUG line in the log. That used to hit the European
    ``-WEU`` variants of the Core 200S/300S/400S, i.e. exactly the devices
    most users of this add-on are likely to own.

    Returns (config, matched_key) or (None, None).
    """
    if not device_type:
        return None, None

    if device_type in table:
        return table[device_type], device_type

    # Case-insensitive exact match (the cloud is not always consistent)
    lowered = device_type.lower()
    for key, cfg in table.items():
        if key.lower() == lowered:
            return cfg, key

    # Same model family, different sales region
    family = _model_family(device_type).lower()
    if family:
        for key, cfg in table.items():
            if _model_family(key).lower() == family:
                # Debug, not info: for the newer entries the tables are
                # keyed by model family on purpose, so this branch is the
                # normal path there and fired on every device list refresh.
                log.debug(
                    f"VeSync: {device_type} not known by name - using "
                    f"the profile of {key} (same model series)")
                return cfg, key

    return None, None


# ============================================================
# Base wrapper for VeSync devices
# ============================================================
class _VeSyncBaseDevice:
    """Shared base for all VeSync wrappers"""

    def __init__(self, raw_data, api, feature_map):
        self.raw_data = raw_data
        self._api = api
        self._feature_map = feature_map

        # Base fields from the device list
        self.device_name = raw_data.get("deviceName", "VeSync device")
        self.cid = raw_data.get("cid", "")
        self.config_module = raw_data.get("configModule", "")
        self.device_type_raw = raw_data.get("deviceType", "")
        self.uuid_raw = raw_data.get("uuid", "")
        self.mac_id = raw_data.get("macID", "")
        self.device_region = (raw_data.get("deviceRegion") or "").upper()
        self.sub_device_no = raw_data.get("subDeviceNo")
        self.current_firm_version = raw_data.get("currentFirmVersion", "")

        # Flags for the shared device list
        self.is_vesync = True
        self.is_meross = False
        self.is_netatmo = False
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
        self._is_on = False
        # Timestamp of the last local switch action (toggle_switch). This lets
        # _refresh_devicelist_state avoid overwriting the optimistically set
        # ``_is_on`` display for a few seconds with a possibly still stale
        # cloud device list.
        self._last_local_toggle_ts = 0.0
        # Per-field protection window: for each logical field (``mode``,
        # ``fan_level``, ``display``, ``child_lock``, ``nightlight``,
        # ``oscillation``, ``mute``, ``auto_preference``, ``filter_life``,
        # ``is_on``) we remember the timestamp of the last local write action.
        # ``apply_status_response`` checks each field separately - so e.g. a
        # ``set_mode`` no longer blocks taking over external ``fan_level``
        # changes, and ``set_fan_speed`` no longer blocks display/child lock.
        # External changes (via the Levoit app) thus become visible as soon as
        # the cloud delivers them at all.
        self._last_field_action_ts = {}
        # The RLock protects apply_status_response() and the setters against
        # concurrent writes from the fast-poll thread and the UI thread (dialog
        # action). Reads happen from the UI thread and can read single
        # attributes atomically thanks to the GIL, lock-free.
        self._lock = threading.RLock()
        # Hash of the last bypassV2 response. For tower fans the cloud response
        # is often cached byte-for-byte - a changed hash is therefore a
        # reliable signal that the cache was re-serialized and ``powerSwitch``
        # is genuinely current.
        self._last_status_hash = None
        # Per-device error counter. Increases with consecutive failures of
        # ``_update_device_details``. After several failures we mark the device
        # as stale so the dialog does not silently show old values.
        self._consecutive_status_failures = 0

        # Take over the online status from the device list
        connection_status = (raw_data.get("connectionStatus") or "").lower()
        if connection_status == "offline":
            self.is_offline = True
        device_status = (raw_data.get("deviceStatus") or "").lower()
        if device_status == "on":
            self._is_on = True

        # Display name (as with Meross/Netatmo)
        self.name = self.device_name
        # The type field is used for display in the dialog
        self.type = self.device_type_raw

    @property
    def uuid(self):
        """Unique UUID within the plugin"""
        return f"vesync_{self.cid}"

    @property
    def unique_id(self):
        """Identical to uuid (for favorites/history)"""
        return self.uuid

    @property
    def is_on(self):
        return self._is_on

    def get_channels(self):
        return []

    # ------ Hooks for status updates (from the API) ------
    def get_status_method(self):
        """Returns the BypassV2 method name for status retrieval"""
        raise NotImplementedError

    def apply_status_response(self, resp):
        """Processes the response of the status retrieval"""
        raise NotImplementedError

    def apply_devicelist_extension(self, raw):
        """Takes over fresh values from the ``extension`` field of the device list.

        Default: no-op. The VeSync device list (``/cloud/v1/deviceManaged/devices``)
        optionally provides an ``extension`` field per device with ``mode``,
        ``fanSpeedLevel``, ``airQuality`` and ``airQualityLevel``. Unlike the
        bypassV2 detail response, the device list is NOT cached server-side -
        external changes (Levoit app) show through there within ~1 s.
        Devices whose mode/level is included in the device list (Core purifiers)
        override this method.
        """

    @staticmethod
    def _bypass_inner_result(resp):
        """Extracts the inner result from a BypassV2 response.

        Bypass V2 nests the actual result in `result.result`. If the outer
        code != 0 or the device is offline, None is returned so the caller
        can react accordingly.
        """
        if not isinstance(resp, dict):
            return None
        if resp.get("code") != 0:
            return None
        outer_result = resp.get("result") or {}
        if not isinstance(outer_result, dict):
            return None
        # Inner code checks the device response (e.g. -11201005 = offline)
        inner_code = outer_result.get("code")
        if inner_code is not None and inner_code != 0:
            return None
        return outer_result.get("result") or {}

    @staticmethod
    def _bypass_call_succeeded(resp):
        """Checks whether a set call was successful (outer + inner code = 0)."""
        if not isinstance(resp, dict):
            return False
        if resp.get("code") != 0:
            return False
        outer_result = resp.get("result")
        if isinstance(outer_result, dict):
            inner_code = outer_result.get("code")
            if inner_code is not None and inner_code != 0:
                return False
        return True

    def _log_rejected_command(self, method, data, resp):
        """Writes down why a command was refused.

        Without this a refusal reaches the log as nothing but the message
        shown to the user - "the change was not accepted" - which says
        that something went wrong and nothing about what. A tester's round
        was spent on exactly that: a temperature change was refused four
        times and the log could not say whether the payload was wrong, the
        field name, or the appliance's own state.

        The response carries the cloud's own code and message, and the
        payload sent is repeated beside it so the two can be read
        together. Neither contains credentials.
        """
        log.info(f"VeSync command {method} refused by {self.name}: {resp}")
        log.info(f"VeSync command {method} payload was: {data}")

    def _log_accepted_command(self, method, data):
        """Writes down a command that went through.

        Logging only the refusals looked economical and cost a round: with
        four calls in a log, two accepted and two refused, the accepted
        ones carried no payload, so what actually differed between them
        had to be reconstructed from the dialogs the reader heard. Both
        halves of the comparison belong in the log.
        """
        log.debug(f"VeSync command {method} accepted by {self.name}: {data}")

    def _update_status(self):
        """Called by the plugin/dialog to fetch a fresh status"""
        try:
            self._api._update_device_details(self)
        except Exception as e:
            log.debug(f"VeSync: _update_status failed for {self.name}: {e}")

    # ------ Per-field protection window ------
    def _protect(self, *fields):
        """Marks one or more fields as just changed locally.

        For ``PROTECT_WINDOW`` seconds ``apply_status_response`` does not
        overwrite the marked fields with values from the bypassV2 response
        (the cloud caches the response for minutes). Other fields are still
        taken over from the cloud immediately - external changes thus stay
        visible promptly.
        """
        now = time.time()
        for field in fields:
            self._last_field_action_ts[field] = now

    def _is_field_protected(self, field, window=PROTECT_WINDOW):
        """True if the field was changed locally within the last ``window`` seconds."""
        ts = self._last_field_action_ts.get(field, 0.0)
        return (time.time() - ts) < window


# ============================================================
# Levoit air purifier (Bypass V2)
# ============================================================
class VeSyncPurifier(_VeSyncBaseDevice):
    """Wrapper for Levoit Core 200S/300S/400S/500S/600S air purifiers.

    Uses the BypassV2 API with snake_case payloads (corresponds to
    pyvesync.devices.vesyncpurifier.VeSyncAirBypass).
    """

    def __init__(self, raw_data, api, feature_map):
        super().__init__(raw_data, api, feature_map)
        self.modes = list(feature_map.get("modes", []))
        self.fan_levels = list(feature_map.get("fan_levels", []))
        self.features = list(feature_map.get("features", []))
        self.auto_preferences = list(feature_map.get("auto_preferences", []))
        self.nightlight_modes = list(feature_map.get("nightlight_modes", []))
        self.alias = feature_map.get("alias", self.device_type_raw)

        # Status fields (subset analogous to pyvesync.PurifierState)
        self.mode = None
        self.fan_level = None
        self.fan_set_level = None
        self.filter_life = None  # 0-100 %
        self.air_quality = None  # level 1=excellent ... 4=poor
        self.air_quality_value = None  # PM2.5 in ug/m3
        # Whether the LAST response carried a usable PM2.5 reading. The
        # displayed value deliberately survives a dropout (see
        # _plausible_reading); the history must not, or it would record a
        # measurement that was never taken.
        self.air_quality_value_fresh = False
        self.pm1 = None  # PM1.0 (V2 / Sprout only)
        self.pm10 = None  # PM10 (V2 / Sprout only)
        self.aq_percent = None  # air quality percent (V2)
        self.voc = None  # VOC (Sprout)
        self.co2 = None  # CO2 (Sprout)
        self.display_on = None  # bool - current display state
        self.display_set_on = None  # bool - desired display state
        self.display_forever = None  # bool - display permanently on
        self.child_lock = None  # bool - child lock
        self.nightlight_status = None  # 'on' / 'off' / 'dim' / None
        self.nightlight_brightness = None  # 0-100 %
        self.fan_rotate_angle = None
        self.auto_preference_type = None  # 'default' / 'efficient' / 'quiet'
        self.auto_room_size = None  # sq ft

    # ------ Readings for the history ------
    def get_pm25(self):
        """The PM2.5 reading, but only while it is a current one.

        Deliberately different from what the tree shows. The displayed
        value survives a sensor dropout, because a line that vanishes and
        returns every hour is worse to navigate than a slightly old
        number. A recorded series must not do that: writing the previous
        reading again would invent a measurement, and the graph would show
        a steady value where the sensor said nothing at all.
        """
        if not self.air_quality_value_fresh:
            return None
        return self.air_quality_value

    # ------ Feature flags ------
    @property
    def supports_air_quality(self):
        return PURIFIER_FEATURE_AIR_QUALITY in self.features

    @property
    def supports_pm1(self):
        return PURIFIER_FEATURE_PM1 in self.features

    @property
    def supports_pm10(self):
        return PURIFIER_FEATURE_PM10 in self.features

    @property
    def supports_nightlight(self):
        return PURIFIER_FEATURE_NIGHTLIGHT in self.features

    @property
    def supports_child_lock(self):
        return PURIFIER_FEATURE_CHILD_LOCK in self.features

    @property
    def supports_display(self):
        return PURIFIER_FEATURE_DISPLAY in self.features

    @property
    def supports_reset_filter(self):
        return PURIFIER_FEATURE_RESET_FILTER in self.features

    @property
    def supports_auto_preference(self):
        return PURIFIER_FEATURE_AUTO_PREFERENCE in self.features

    @property
    def supports_light_detection(self):
        return PURIFIER_FEATURE_LIGHT_DETECT in self.features

    # ------ Display ------
    def get_type_display(self):
        return self.alias

    def get_status_summary(self):
        """Accessible summary for speech output"""
        if self.is_offline:
            # Translators: Device is not reachable.
            return _("offline")

        parts = []
        # Translators: Device state on/off (short).
        parts.append(_("on") if self._is_on else _("off"))

        if self._is_on:
            if self.mode:
                # Translators: Operating mode names of a VeSync air purifier.
                mode_de = {
                    "auto": _("Auto"),
                    "manual": _("Manual"),
                    "sleep": _("Sleep mode"),
                    "turbo": _("Turbo"),
                    "pet": _("Pet"),
                }.get(self.mode, self.mode)
                # Translators: Status announcement: current operating mode.
                parts.append(_("Mode: {mode}").format(mode=mode_de))
            if self.fan_level is not None:
                # Translators: Status announcement: current fan level.
                parts.append(_("Level {level}").format(level=self.fan_level))

        if self.air_quality is not None:
            # Translators: Air quality levels (1=best, 4=worst).
            aq_de = {1: _("excellent"), 2: _("good"), 3: _("moderate"), 4: _("poor")}.get(
                self.air_quality, str(self.air_quality)
            )
            # Translators: Status announcement: air quality level.
            parts.append(_("Air quality: {level}").format(level=aq_de))
        if self.air_quality_value is not None:
            # Translators: Status announcement: particulate matter PM2.5.
            parts.append(_("PM2.5: {value} micrograms per cubic meter").format(
                value=self.air_quality_value))
        if self.pm1 is not None:
            parts.append(f"PM1.0: {self.pm1}")
        if self.pm10 is not None:
            parts.append(f"PM10: {self.pm10}")
        if self.filter_life is not None:
            # Translators: Status announcement: remaining filter life.
            parts.append(_("Filter: {value}%").format(value=self.filter_life))
        if self.child_lock is True:
            # Translators: Status announcement: child lock is active.
            parts.append(_("Child lock active"))
        if self.nightlight_status and self.nightlight_status != "off":
            # Translators: Status announcement: night light state.
            parts.append(_("Night light: {mode}").format(mode=self.nightlight_status))

        return ", ".join(parts)

    # ------ Status retrieval ------
    def get_status_method(self):
        return "getPurifierStatus"

    def apply_status_response(self, resp):
        result = self._bypass_inner_result(resp)
        if result is None:
            # On error, no longer set is_offline - the device list
            # (_refresh_devicelist_state) is the more reliable source for the
            # connection status. Only log here and stop.
            return

        # Build a response hash (as with the tower fan): detects whether the
        # VeSync cloud re-serialized its bypassV2 cache. ``getPurifierStatus``
        # is returned byte-for-byte identical for minutes even though the user
        # switched e.g. from auto to manual-low in the Levoit app. We therefore
        # take mode/level primarily from the (fresh) device list
        # (``apply_devicelist_extension``). So that the CACHED bypassV2
        # response does not immediately overwrite the fresh deviceList value -
        # it runs as the second step after ``_refresh_devicelist_state`` - we
        # take over ``mode``/``fan_level`` from bypassV2 ONLY when the response
        # has actually changed.
        try:
            import json as _json
            current_hash = hash(_json.dumps(result, sort_keys=True, default=str))
        except Exception:
            current_hash = None
        response_changed = (current_hash is not None
                            and current_hash != self._last_status_hash)
        self._last_status_hash = current_hash

        # Per-field protection window (see _VeSyncBaseDevice._protect): for
        # each user-controlled field a timestamp is recorded after a setter.
        # ``apply_status_response`` only skips overwriting those fields that
        # were changed locally within the protection window. All other fields
        # are taken over from the cloud response immediately - external changes
        # thus stay visible promptly instead of being buried for 60 s under a
        # single local action.
        #
        # Core format (snake_case): enabled, mode, level, filter_life,
        # air_quality, air_quality_value, child_lock, display, night_light,
        # display_forever, configuration{...}, levelNew
        # Note: ``_is_on`` and ``is_offline`` are NOT taken over from this
        # bypassV2 response because the VeSync cloud caches it aggressively.
        # External toggles from the Levoit app would otherwise not show through
        # for minutes. Instead ``_refresh_devicelist_state`` (endpoint
        # ``/cloud/v1/deviceManaged/devices``) provides the fresh on/off state.
        with self._lock:
            # ``mode``/``fan_level``: only take over from bypassV2 when the
            # response is fresh (response_changed) - otherwise the cached value
            # would overwrite the fresh deviceList extension value.
            if "mode" in result and response_changed \
                    and not self._is_field_protected('mode'):
                self.mode = result.get("mode")
            if "level" in result and result.get("level") is not None \
                    and response_changed and not self._is_field_protected('fan_level'):
                try:
                    self.fan_level = int(result.get("level"))
                except (TypeError, ValueError):
                    self.fan_level = None
            if "levelNew" in result and result.get("levelNew") is not None \
                    and response_changed and not self._is_field_protected('fan_level'):
                try:
                    self.fan_set_level = int(result.get("levelNew"))
                except (TypeError, ValueError):
                    self.fan_set_level = None
            if "filter_life" in result and not self._is_field_protected('filter_life'):
                self.filter_life = result.get("filter_life")
            if "display" in result and not self._is_field_protected('display'):
                self.display_on = bool(result.get("display"))
                self.display_set_on = bool(result.get("display"))
            if "child_lock" in result and not self._is_field_protected('child_lock'):
                self.child_lock = bool(result.get("child_lock"))
            if self.supports_air_quality:
                # Sensor values: never protected, always go into the wrapper -
                # but only when they are readings at all, see
                # _plausible_reading. A dropout leaves the previous value
                # standing rather than replacing the line with nonsense; the
                # freshness flag is what keeps it out of the history.
                if "air_quality" in result:
                    # Levels run 1 (excellent) to 4 (poor). A level of 0 or
                    # less has not been seen, but it would be displayed as a
                    # bare number, so it is filtered on the same grounds.
                    level = _plausible_reading(result.get("air_quality"), minimum=1)
                    if level is not None:
                        self.air_quality = level
                if "air_quality_value" in result:
                    value = _plausible_reading(result.get("air_quality_value"))
                    self.air_quality_value_fresh = value is not None
                    if value is not None:
                        self.air_quality_value = value
            if "night_light" in result and result.get("night_light") \
                    and not self._is_field_protected('nightlight'):
                self.nightlight_status = result.get("night_light")

            # Configuration (display_forever, auto_preference) - also protected
            # per field: ``set_auto_preference`` only marks the
            # ``auto_preference`` field, ``toggle_display`` only ``display``.
            cfg = result.get("configuration")
            if isinstance(cfg, dict):
                if "display" in cfg and not self._is_field_protected('display'):
                    self.display_set_on = bool(cfg.get("display"))
                if "display_forever" in cfg and not self._is_field_protected('display'):
                    self.display_forever = bool(cfg.get("display_forever"))
                auto_pref = cfg.get("auto_preference")
                if isinstance(auto_pref, dict) \
                        and not self._is_field_protected('auto_preference'):
                    self.auto_preference_type = auto_pref.get("type")
                    self.auto_room_size = auto_pref.get("room_size")

    def apply_devicelist_extension(self, raw):
        """Takes over mode/fan level/air quality from the fresh device list.

        The bypassV2 response (``getPurifierStatus``) is often cached
        server-side for minutes - if the user switches externally (Levoit app)
        e.g. from auto to manual-low, it only shows through in
        ``getPurifierStatus`` after minutes. The ``extension`` field of the
        device list, however, is fresh (~1 s). We therefore take ``mode`` and
        ``fanSpeedLevel`` primarily from here; the per-field protection window
        is still respected so a level the user just set is not overwritten.
        """
        ext = raw.get("extension")
        if not isinstance(ext, dict):
            return
        with self._lock:
            ext_mode = ext.get("mode")
            if ext_mode and not self._is_field_protected('mode'):
                self.mode = ext_mode
            ext_level = ext.get("fanSpeedLevel")
            if ext_level is not None and not self._is_field_protected('fan_level'):
                # ``fanSpeedLevel`` is the current level (controlled by the
                # device in auto mode). Only set ``fan_level``, not
                # ``fan_set_level`` - the latter is the manually set level and
                # comes from ``levelNew`` of the bypassV2 response.
                try:
                    self.fan_level = int(ext_level)
                except (TypeError, ValueError):
                    pass
            if self.supports_air_quality:
                # Sensor values: no protection window, but the same dropout
                # filter as in apply_status_response - the device list
                # carries the identical -1 (see _plausible_reading).
                aq_level = _plausible_reading(ext.get("airQualityLevel"), minimum=1)
                if aq_level is not None:
                    self.air_quality = aq_level
                if "airQuality" in ext:
                    aq_value = _plausible_reading(ext.get("airQuality"))
                    self.air_quality_value_fresh = aq_value is not None
                    if aq_value is not None:
                        self.air_quality_value = aq_value

    # ------ Actions ------
    def toggle_switch(self, on):
        """Switches the device on or off.

        BypassV2 setSwitch expects: {'enabled': bool, 'id': 0}
        """
        data = {"enabled": bool(on), "id": 0}
        resp = self._api.call_bypass_v2(self, "setSwitch", data)
        if not self._bypass_call_succeeded(resp):
            # Translators: Error message after a refused switch command, shown
            # to the user.
            raise RuntimeError(_("VeSync: switch command failed"))
        with self._lock:
            self._is_on = bool(on)
            self._last_local_toggle_ts = time.time()
            self._protect('is_on')
        return True

    def set_mode(self, mode):
        """Sets the operating mode (auto, manual, sleep).

        Manual mode is activated via setLevel (like pyvesync).
        Other modes via setPurifierMode.
        """
        if mode not in self.modes:
            # Translators: Error message when the device does not know the
            # chosen mode.
            raise ValueError(_("Mode '{mode}' is not supported by this device").format(mode=mode))

        if mode == PURIFIER_MODE_MANUAL:
            # In the fallback to set_fan_speed: if both level caches are empty,
            # safely default to 1 - otherwise set_fan_speed would validate
            # against the fan_levels list and abort with a ValueError.
            speed = self.fan_level or self.fan_set_level or (
                self.fan_levels[0] if self.fan_levels else 1
            )
            return self.set_fan_speed(speed)

        data = {"mode": mode}
        resp = self._api.call_bypass_v2(self, "setPurifierMode", data)
        if not self._bypass_call_succeeded(resp):
            # Translators: Error message after a refused mode change, shown to
            # the user.
            raise RuntimeError(_("VeSync: mode '{mode}' could not be set").format(mode=mode))
        with self._lock:
            self.mode = mode
            self._is_on = True
            self._protect('mode', 'is_on')
        return True

    def set_fan_speed(self, speed):
        """Sets the fan level and implicitly switches to manual.

        BypassV2 setLevel (Core): {'id': 0, 'level': int, 'type': 'wind'}
        """
        try:
            speed_int = int(speed)
        except (TypeError, ValueError):
            # Translators: Error message when the fan level is not a number.
            raise ValueError(_("Invalid fan level"))
        if speed_int not in self.fan_levels:
            # Translators: Error message when the device does not have this fan
            # level.
            raise ValueError(_("Fan level {level} is not supported").format(level=speed_int))

        data = {"id": 0, "level": speed_int, "type": "wind"}
        resp = self._api.call_bypass_v2(self, "setLevel", data)
        if not self._bypass_call_succeeded(resp):
            # Translators: Error message after a refused fan level change,
            # shown to the user.
            raise RuntimeError(_("VeSync: fan level {level} could not be set").format(level=speed_int))
        with self._lock:
            self.fan_level = speed_int
            self.fan_set_level = speed_int
            self.mode = PURIFIER_MODE_MANUAL
            self._is_on = True
            self._protect('fan_level', 'mode', 'is_on')
        return True

    def toggle_display(self, on):
        """Switches the display on or off.

        BypassV2 setDisplay: {'state': bool}
        """
        if not self.supports_display:
            # Translators: Error message when the device has no display to
            # switch.
            raise RuntimeError(_("Display control is not supported by this "
                                 "device"))
        data = {"state": bool(on)}
        resp = self._api.call_bypass_v2(self, "setDisplay", data)
        if not self._bypass_call_succeeded(resp):
            # Translators: Error message after a refused display command, shown
            # to the user.
            raise RuntimeError(_("VeSync: display switch command failed"))
        # Optimistically update both the desired and the displayed state so the
        # status line in the dialog is correct immediately.
        with self._lock:
            self.display_set_on = bool(on)
            self.display_on = bool(on)
            self._protect('display')
        return True

    def toggle_child_lock(self, on):
        """Switches the child lock on or off.

        BypassV2 setChildLock: {'child_lock': bool}
        """
        if not self.supports_child_lock:
            # Translators: Error message when the device has no child lock.
            raise RuntimeError(_("Child lock is not supported by this device"))
        data = {"child_lock": bool(on)}
        resp = self._api.call_bypass_v2(self, "setChildLock", data)
        if not self._bypass_call_succeeded(resp):
            # Translators: Error message after a refused child lock command,
            # shown to the user.
            raise RuntimeError(_("VeSync: child lock could not be toggled"))
        with self._lock:
            self.child_lock = bool(on)
            self._protect('child_lock')
        return True

    def set_nightlight_mode(self, mode):
        """Sets the night light mode.

        BypassV2 setNightLight: {'night_light': str}
        Allowed values: 'on', 'off', 'dim'
        """
        if not self.supports_nightlight:
            # Translators: Error message when the device has no night light.
            raise RuntimeError(_("Night light is not supported by this device"))
        if mode not in self.nightlight_modes:
            # Translators: Error message when the device does not know the
            # chosen night light mode.
            raise ValueError(_("Night light mode '{mode}' is not supported").format(mode=mode))
        data = {"night_light": mode}
        resp = self._api.call_bypass_v2(self, "setNightLight", data)
        if not self._bypass_call_succeeded(resp):
            # Translators: Error message after a refused night light command,
            # shown to the user.
            raise RuntimeError(_("VeSync: night light could not be set"))
        with self._lock:
            self.nightlight_status = mode
            self._protect('nightlight')
        return True

    def set_auto_preference(self, preference, room_size=None):
        """Sets the auto profile preference.

        BypassV2 setAutoPreference: {'type': str, 'room_size': int}
        Allowed types: 'default', 'efficient', 'quiet'
        """
        if not self.supports_auto_preference:
            # Translators: Error message when the device has no automatic
            # profile.
            raise RuntimeError(_("Auto profile is not supported by this device"))
        if preference not in self.auto_preferences:
            # Translators: Error message when the device does not know the
            # chosen automatic profile.
            raise ValueError(_("Auto profile '{value}' is not supported").format(value=preference))
        if room_size is None:
            room_size = self.auto_room_size or 600
        data = {"type": preference, "room_size": int(room_size)}
        resp = self._api.call_bypass_v2(self, "setAutoPreference", data)
        if not self._bypass_call_succeeded(resp):
            # Translators: Error message after a refused profile change, shown
            # to the user.
            raise RuntimeError(_("VeSync: auto profile could not be set"))
        with self._lock:
            self.auto_preference_type = preference
            self.auto_room_size = int(room_size)
            self._protect('auto_preference')
        return True

    def reset_filter(self):
        """Resets the filter status to 100%.

        BypassV2 resetFilter: {} (empty payload)
        """
        if not self.supports_reset_filter:
            # Translators: Error message when the device cannot reset its
            # filter counter.
            raise RuntimeError(_("Filter reset is not supported by this device"))
        resp = self._api.call_bypass_v2(self, "resetFilter", {})
        if not self._bypass_call_succeeded(resp):
            # Translators: Error message after a refused filter reset, shown to
            # the user.
            raise RuntimeError(_("VeSync: filter reset failed"))
        with self._lock:
            self.filter_life = 100
            # The cloud cache still returns the old filter value after the
            # reset. Without this field protection window the dialog would
            # briefly show 100% and then jump back to the old value (e.g. 17%).
            self._protect('filter_life')
        return True


# ============================================================
# Levoit tower fan (Bypass V2)
# ============================================================
class VeSyncTowerFan(_VeSyncBaseDevice):
    """Wrapper for Levoit tower fans (Classic 42-Inch Tower Fan).

    Corresponds to pyvesync.devices.vesyncfan.VeSyncTowerFan - uses the
    BypassV2 API with camelCase payloads.
    """

    def __init__(self, raw_data, api, feature_map):
        super().__init__(raw_data, api, feature_map)
        # modes is a mapping here (logical -> API value)
        self.modes = dict(feature_map.get("modes", {}))
        self._reverse_modes = {v: k for k, v in self.modes.items()}
        self.fan_levels = list(feature_map.get("fan_levels", []))
        self.alias = feature_map.get("alias", self.device_type_raw)

        # Status fields
        self.mode = None  # API value (normal/turbo/auto/advancedSleep)
        self.fan_level = None
        self.fan_set_level = None
        self.oscillation_on = None  # bool - current oscillation state
        self.oscillation_set_on = None  # bool - desired switch state
        self.mute_on = None  # bool - mute currently on
        self.mute_set_on = None  # bool - desired mute state
        self.display_on = None  # bool - display shown
        self.display_set_on = None  # bool - display switch
        self.displaying_type = None  # 0/1 - undocumented (display content)
        self.temperature = None  # degrees C, comes as int*10 from the API
        self.timer_remaining = None  # s
        self.sleep_preference_type = None

    # ------ Display ------
    def get_type_display(self):
        return self.alias

    def get_status_summary(self):
        if self.is_offline:
            # Translators: Device is not reachable.
            return _("offline")

        parts = []
        # Translators: Device state on/off (short).
        parts.append(_("on") if self._is_on else _("off"))

        if self._is_on:
            if self.mode:
                # Translators: Operating mode names of a VeSync fan.
                mode_de = {
                    "normal": _("Normal"),
                    "turbo": _("Turbo"),
                    "auto": _("Auto"),
                    "advancedSleep": _("Sleep mode"),
                }.get(self.mode, self.mode)
                # Translators: Status announcement: current operating mode.
                parts.append(_("Mode: {mode}").format(mode=mode_de))
            if self.fan_level is not None:
                # Translators: Status announcement: current fan level.
                parts.append(_("Level {level}").format(level=self.fan_level))
            if self.oscillation_on is not None:
                # Translators: Status announcement: oscillation on/off.
                parts.append(_("Oscillation: on") if self.oscillation_on else _("Oscillation: "
                                                                                 "off"))
            if self.mute_on is True:
                # Translators: Status announcement: device sounds muted.
                parts.append(_("Muted"))

        if self.temperature is not None:
            try:
                # Translators: Status announcement: measured temperature.
                parts.append(_("Temperature: {value:.1f}°C").format(value=float(self.temperature)))
            except (TypeError, ValueError):
                pass

        return ", ".join(parts)

    # ------ Status retrieval ------
    def get_status_method(self):
        return "getTowerFanStatus"

    def apply_status_response(self, resp):
        result = self._bypass_inner_result(resp)
        if result is None:
            # is_offline is set from the device list - do not overwrite here,
            # otherwise the status flickers on short API dropouts.
            return

        # Build a response hash to detect whether the VeSync cloud re-
        # serialized its bypassV2 cache. For tower fans the cloud otherwise
        # returns byte-for-byte identical responses for minutes - a new hash is
        # therefore the only reliable signal that ``powerSwitch`` is really
        # current (instead of a stale cache response).
        try:
            import json as _json
            current_hash = hash(_json.dumps(result, sort_keys=True, default=str))
        except Exception:
            current_hash = None
        response_changed = (current_hash is not None
                            and current_hash != self._last_status_hash)
        self._last_status_hash = current_hash

        with self._lock:
            # ``_is_on``/``is_offline`` normally come from the device list
            # (``_refresh_devicelist_state``) - but for tower fans it returns
            # ``None``. The power state therefore comes from ``powerSwitch`` of
            # the bypassV2 response:
            #
            # ``powerSwitch`` is the device's real power field. We only take it
            # over when the response has actually changed (hash change) - with
            # an unchanged (cached) response the state has not changed, so the
            # last known value is kept.
            #
            # IMPORTANT - sleep mode: previously ``_is_on`` was derived from
            # ``screenState``/``screenSwitch`` (display physically off +
            # preference on => device off). That is WRONG in **sleep mode
            # (advancedSleep)**: sleep mode dims the display (``screenState`` =
            # 0) although the device is running. The heuristic then falsely
            # reported the device as "off", which led to random on/off
            # announcements and a device that permanently appeared switched off
            # (incl. a vanished display control). The harmful negative signal
            # has therefore been removed; as a fallback without ``powerSwitch``
            # only the unambiguous positive signal is used (display physically
            # on => device definitely running).
            in_toggle_window = (time.time()
                                - getattr(self, '_last_local_toggle_ts', 0.0)) < TOGGLE_WINDOW
            if not in_toggle_window and not self._is_field_protected('is_on'):
                power_switch = result.get("powerSwitch")
                if power_switch is not None:
                    if response_changed:
                        try:
                            self._is_on = bool(int(power_switch))
                        except (TypeError, ValueError):
                            pass
                else:
                    # Fallback ONLY without powerSwitch: exclusively the
                    # unambiguous positive signal (display physically on =>
                    # on).
                    screen_state = result.get("screenState")
                    if screen_state is not None:
                        try:
                            if int(screen_state) == 1:
                                self._is_on = True
                        except (TypeError, ValueError):
                            pass
            if "workMode" in result and not self._is_field_protected('mode'):
                self.mode = result.get("workMode")
            if "fanSpeedLevel" in result and result.get("fanSpeedLevel") is not None \
                    and not self._is_field_protected('fan_level'):
                try:
                    self.fan_level = int(result.get("fanSpeedLevel"))
                except (TypeError, ValueError):
                    self.fan_level = None
            if "manualSpeedLevel" in result and result.get("manualSpeedLevel") is not None \
                    and not self._is_field_protected('fan_level'):
                try:
                    self.fan_set_level = int(result.get("manualSpeedLevel"))
                except (TypeError, ValueError):
                    self.fan_set_level = None
            if "oscillationState" in result and not self._is_field_protected('oscillation'):
                self.oscillation_on = bool(result.get("oscillationState"))
            if "oscillationSwitch" in result and not self._is_field_protected('oscillation'):
                self.oscillation_set_on = bool(result.get("oscillationSwitch"))
            if "muteState" in result and not self._is_field_protected('mute'):
                self.mute_on = bool(result.get("muteState"))
            if "muteSwitch" in result and not self._is_field_protected('mute'):
                self.mute_set_on = bool(result.get("muteSwitch"))
            if "screenState" in result and not self._is_field_protected('display'):
                self.display_on = bool(result.get("screenState"))
            if "screenSwitch" in result and not self._is_field_protected('display'):
                self.display_set_on = bool(result.get("screenSwitch"))
            if "displayingType" in result:
                self.displaying_type = result.get("displayingType")
            if "temperature" in result and result.get("temperature") is not None:
                # The API delivers the temperature in tenths of Fahrenheit,
                # e.g. 759 = 75.9 F. Source: pyvesync (humidifier_base.py, "#
                # Fahrenheit but without decimals") and vesyncfan.py
                # PedestalFanResult (`/ 10`). The pyvesync tower fan class
                # stores the raw value; we convert uniformly to degrees Celsius
                # because users in the DACH region expect Celsius.
                try:
                    fahrenheit = float(result.get("temperature")) / 10.0
                    self.temperature = (fahrenheit - 32.0) * 5.0 / 9.0
                except (TypeError, ValueError):
                    self.temperature = None
            if "timerRemain" in result:
                self.timer_remaining = result.get("timerRemain")
            sleep_pref = result.get("sleepPreference")
            if isinstance(sleep_pref, dict):
                self.sleep_preference_type = sleep_pref.get("sleepPreferenceType")

    # ------ Actions ------
    def toggle_switch(self, on):
        """Switches the fan on or off.

        BypassV2 setSwitch (V2): {'powerSwitch': int, 'switchIdx': 0}
        """
        data = {"powerSwitch": int(bool(on)), "switchIdx": 0}
        resp = self._api.call_bypass_v2(self, "setSwitch", data)
        if not self._bypass_call_succeeded(resp):
            raise RuntimeError(_("VeSync: switch command failed"))
        with self._lock:
            self._is_on = bool(on)
            self._last_local_toggle_ts = time.time()
            self._protect('is_on')
        return True

    def set_mode(self, mode):
        """Sets the operating mode (logical key or direct API value).

        BypassV2 setTowerFanMode: {'workMode': str}
        """
        if mode not in self.modes and mode not in self._reverse_modes:
            raise ValueError(_("Mode '{mode}' is not supported by this device").format(mode=mode))
        # Map a logical value to the API value if needed
        api_mode = self.modes.get(mode, mode)
        data = {"workMode": api_mode}
        resp = self._api.call_bypass_v2(self, "setTowerFanMode", data)
        if not self._bypass_call_succeeded(resp):
            raise RuntimeError(_("VeSync: mode '{mode}' could not be set").format(mode=mode))
        with self._lock:
            self.mode = api_mode
            self._is_on = True
            self._protect('mode', 'is_on')
        return True

    def set_fan_speed(self, speed):
        """Sets the fan level (1-12).

        BypassV2 setLevel (tower fan): {'manualSpeedLevel': int, 'levelType': 'wind', 'levelIdx': 0}

        Unlike the purifier, setLevel on the tower fan does NOT automatically
        switch the operating mode to "normal" - pyvesync
        (`vesyncfan.VeSyncTowerFan.set_fan_speed`) also only updates the level
        value. An optimistic mode change would otherwise briefly show
        "Modus: Normal" and jump back to the actual API value (e.g. "Auto")
        on the next refresh.
        """
        try:
            speed_int = int(speed)
        except (TypeError, ValueError):
            raise ValueError(_("Invalid fan level"))
        if speed_int not in self.fan_levels:
            raise ValueError(_("Fan level {level} is not supported").format(level=speed_int))

        data = {"manualSpeedLevel": speed_int, "levelType": "wind", "levelIdx": 0}
        resp = self._api.call_bypass_v2(self, "setLevel", data)
        if not self._bypass_call_succeeded(resp):
            raise RuntimeError(_("VeSync: fan level {level} could not be set").format(level=speed_int))
        with self._lock:
            self.fan_level = speed_int
            self.fan_set_level = speed_int
            self._is_on = True
            self._protect('fan_level', 'is_on')
        return True

    def toggle_oscillation(self, on):
        """Switches the oscillation on or off.

        BypassV2 setOscillationSwitch: {'oscillationSwitch': int}
        """
        data = {"oscillationSwitch": int(bool(on))}
        resp = self._api.call_bypass_v2(self, "setOscillationSwitch", data)
        if not self._bypass_call_succeeded(resp):
            # Translators: Error message after a refused oscillation command,
            # shown to the user.
            raise RuntimeError(_("VeSync: oscillation could not be toggled"))
        with self._lock:
            self.oscillation_on = bool(on)
            self.oscillation_set_on = bool(on)
            self._protect('oscillation')
        return True

    def toggle_mute(self, on):
        """Switches the mute (sounds off) on or off.

        BypassV2 setMuteSwitch: {'muteSwitch': int}
        """
        data = {"muteSwitch": int(bool(on))}
        resp = self._api.call_bypass_v2(self, "setMuteSwitch", data)
        if not self._bypass_call_succeeded(resp):
            # Translators: Error message after a refused mute command, shown to
            # the user.
            raise RuntimeError(_("VeSync: mute toggle failed"))
        with self._lock:
            self.mute_on = bool(on)
            self.mute_set_on = bool(on)
            self._protect('mute')
        return True

    def toggle_display(self, on):
        """Switches the display on or off.

        BypassV2 setDisplay (tower fan): {'screenSwitch': int}
        """
        data = {"screenSwitch": int(bool(on))}
        resp = self._api.call_bypass_v2(self, "setDisplay", data)
        if not self._bypass_call_succeeded(resp):
            raise RuntimeError(_("VeSync: display switch command failed"))
        # Optimistically update both the desired and the displayed state so the
        # status line in the dialog is correct immediately.
        with self._lock:
            self.display_set_on = bool(on)
            self.display_on = bool(on)
            self._protect('display')
        return True


class VeSyncAirFryer(_VeSyncBaseDevice):
    """Wrapper for Cosori air fryers - display only.

    Shows what the appliance reports: the switching state and whether it is
    online from the device list, and from getAirfryerStatus the cooking
    state, the selected programme, the set and the measured temperature and
    the remaining time. Nothing is sent to it - see toggle_switch.

    Deliberately not a subclass of the purifier or fan wrapper: those bring
    modes, fan levels and a filter, none of which exist here, and code that
    reads them would fail in ways that are hard to trace.
    """

    # The call the appliance actually answers. Established by asking it:
    # of seven candidates only this one came back with code 0, the other
    # six answered result.code = -1 (method unknown). Note the lower case
    # f - "getAirFryerStatus" is one of the six that do not work.
    #
    # Only for the single-basket line. The two-zone TwinFry models answer
    # "getAirfryerMultiStatus" instead and would fall through here.
    STATUS_METHOD = "getAirfryerStatus"

    # How far the measured temperature has to move before the displayed
    # value follows it.
    #
    # While the appliance holds its set temperature the reading oscillates
    # by a few degrees - 195, 194, 195, 193, 195 within one minute of a
    # logged Steak programme. The tree line carrying it is re-read by the
    # screen reader on every change, so an undamped value turns the whole
    # second half of a cook into chatter that says nothing new. The climb
    # to the set temperature moves in steps of ten to twenty degrees and
    # still gets through unhindered.
    TEMP_HYSTERESIS = {"c": 5, "f": 9}

    def __init__(self, raw_data, api, feature_map):
        super().__init__(raw_data, api, feature_map)
        self.alias = feature_map.get("alias", self.device_type_raw)

        # Status fields from getAirfryerStatus. Raw API values; what they
        # are called in the interface is decided when displayed.
        self.cook_status = None   # 'standby', 'ready', 'cooking', 'cookEnd'
        # 'normal' on every single reading ever taken, including while a
        # Steak programme was running. It is NOT the cooking programme -
        # that one lives in stepArray, see apply_status_response.
        self.cook_mode = None
        self.temp_unit = None     # 'c' or 'f' - the appliance decides
        self.current_temp = None  # measured air temperature
        # Damped copy of current_temp - this is what gets displayed, see
        # TEMP_HYSTERESIS.
        self.display_temp = None
        self.time_remaining = None  # seconds, see apply_status_response

        # From stepArray[0], i.e. only while a programme is loaded.
        self.programme = None     # language-neutral key, e.g. 'Steak'
        self.target_temp = None   # set temperature of the programme
        self.cook_set_time = None  # total duration in seconds

        self.preheat_temp = None

        # A temperature that has been sent and not yet judged. The cloud's
        # "accepted" says nothing about it: one accepted call carrying
        # both fields had its time applied and its temperature dropped on
        # a real appliance, so only the next status can tell. See
        # _judge_sent_temperature.
        self._sent_temp = None
        self._sent_temp_time = None   # the cookSetTime sent alongside it
        self._sent_temp_at = 0.0
        self._temp_verdict = None     # (requested, kept), read out once

    # ------ Display ------
    def get_type_display(self):
        return self.alias

    @property
    def is_running(self):
        """Whether the appliance is doing something.

        'standby' is what a fryer with no programme loaded reports.
        Anything else counts as running - including 'cookEnd', where the
        programme is over but the appliance still holds the result and its
        measured temperature, 'cookStop', which is a pause with the meal
        still in there, and any value nobody has seen yet.
        Deliberately that way round: an unknown state announced as idle
        would be the more expensive mistake.
        """
        status = (self.cook_status or "").lower()
        return bool(status) and status != "standby"

    def cook_status_display(self):
        """The cooking state as a word, raw value if it is a new one.

        Four states are known (see VESYNC_FRYER_COOK_STATES). An unknown
        value is passed through unchanged rather than guessed at, and the
        log then says which one to add.
        """
        from .constants import VESYNC_FRYER_COOK_STATES
        status = (self.cook_status or "").lower()
        return VESYNC_FRYER_COOK_STATES.get(status, self.cook_status or "")

    @staticmethod
    def programme_display_for(mode):
        """One programme key as a word, raw value if it is a new one.

        Keyed on the appliance's English `mode`, not on `recipeName` - that
        one arrives in the language of the VeSync app and would put foreign
        text into the interface.

        The key is normalised for case and spaces, because the spellings on
        the wire are not the obvious ones: 'AirFry' has no space and fries
        arrive as 'French fries'.
        """
        from .constants import VESYNC_FRYER_PROGRAMME_NAMES
        if not mode:
            return ""
        key = mode.replace(" ", "").replace("_", "").lower()
        return VESYNC_FRYER_PROGRAMME_NAMES.get(key, mode)

    def programme_display(self):
        """The loaded cooking programme as a word."""
        return self.programme_display_for(self.programme)

    def _format_temperature(self, value):
        """A temperature with the unit the appliance itself reports."""
        if value is None:
            return ""
        if (self.temp_unit or "c").lower() == "f":
            return f"{value} °F"
        return f"{value} °C"

    def temperature_display(self):
        """The measured temperature, damped (see TEMP_HYSTERESIS)."""
        return self._format_temperature(self.display_temp)

    def target_temperature_display(self):
        """The set temperature of the running programme."""
        return self._format_temperature(self.target_temp)

    @property
    def time_is_counting_down(self):
        """Whether the time reported is a countdown or a duration.

        Only while the programme runs does the number fall. Before that it
        equals the programme's whole duration - a logged appliance sat in
        standby reporting Frozen with 720 seconds and 200 degrees, which is
        what the programme takes, not what is left of it. Calling that
        "remaining time" would say something untrue about an appliance
        doing nothing.

        'cookStop' does count as a countdown: it holds the time that was
        genuinely left when the programme was stopped.
        """
        return (self.cook_status or "").lower() in ("cooking", "cookstop")  # paused keeps a real remainder

    def remaining_time_display(self):
        """The cooking time as minutes and seconds.

        The unit is settled: over a logged programme the counter fell by
        456 while 456 seconds passed on the clock, exactly one to one, and
        a cookSetTime of 480 belonged to a programme set to eight minutes.

        Spelled out rather than as "7:12", because a colon is silent at the
        symbol level most listeners run and "seven twelve" is not a time.
        """
        if self.time_remaining is None:
            return ""
        total = int(self.time_remaining)
        if total <= 0:
            return ""
        minutes, seconds = divmod(total, 60)
        if minutes and not seconds:
            # Translators: Remaining cooking time of an air fryer, whole
            # minutes. {minutes} = minutes.
            return _("{minutes} min").format(minutes=minutes)
        if minutes:
            # Translators: Remaining cooking time of an air fryer.
            # {minutes} = whole minutes, {seconds} = remaining seconds.
            # Abbreviations rather than single letters: a lone "s" is read
            # out as the letter.
            return _("{minutes} min {seconds} sec").format(
                minutes=minutes, seconds=seconds)
        # Translators: Remaining cooking time of an air fryer, under a
        # minute. {seconds} = seconds.
        return _("{seconds} sec").format(seconds=seconds)

    def get_status_summary(self):
        if self.is_offline:
            # Translators: Device is not reachable.
            return _("offline")
        if self.is_running:
            # While something is running the cooking state says more than
            # on/off - and cannot contradict it. The two come from
            # different calls: on/off from the device list, the cooking
            # state from the detail call. A stale device list would
            # otherwise produce "off, cooking".
            return self.cook_status_display()
        # Translators: Device state on/off (short).
        return _("on") if self._is_on else _("off")

    # ------ Status ------
    def get_status_method(self):
        return self.STATUS_METHOD

    def apply_status_response(self, resp):
        """Takes over the fields of getAirfryerStatus.

        currentTemp is the MEASURED air temperature, not a target: over a
        logged programme it climbed to 217 degrees against a set value of
        205, and 205 is the highest this model can be set to at all.

        In standby it stops being a measurement. It then holds the last
        value of the previous cook - 172 degrees unchanged across 48 polls
        over 35 minutes, which no fryer standing in a kitchen does - and
        only refreshes when the next programme starts. That is where the
        181 degrees on a cold appliance came from. It is dropped in that
        state rather than displayed with a caveat nobody can hear.
        """
        result = self._bypass_inner_result(resp)
        if not result:
            return

        status = result.get("cookStatus")
        status_key = (status or "").lower()
        new_temp = result.get("currentTemp")
        if not isinstance(new_temp, (int, float)):
            new_temp = None

        # The programme lives one level down, in the first step. An empty
        # stepArray is what standby looks like, so the fields are cleared
        # rather than left standing from the previous cook.
        steps = result.get("stepArray") or []
        step = steps[0] if isinstance(steps, list) and steps else {}
        if not isinstance(step, dict):
            step = {}

        with self._lock:
            status_changed = status_key != (self.cook_status or "").lower()
            self.cook_status = status
            self.cook_mode = result.get("cookMode")
            self.temp_unit = (result.get("tempUnit") or "c").lower()
            self.current_temp = new_temp
            self.time_remaining = result.get("totalTimeRemaining")
            self.preheat_temp = result.get("preheatTemp")

            self.programme = step.get("mode")
            self.target_temp = step.get("cookTemp")
            self.cook_set_time = step.get("cookSetTime")

            self._judge_sent_temperature(step)

            if status_key == "standby" or new_temp is None:
                self.display_temp = None
            else:
                threshold = self.TEMP_HYSTERESIS.get(self.temp_unit, 5)
                # A state change always gets through: the reading at the
                # end of a programme is the one worth hearing exactly.
                if (self.display_temp is None or status_changed
                        or abs(new_temp - self.display_temp) >= threshold):
                    self.display_temp = new_temp

        # Note the programme, and deliberately outside the lock: the store
        # writes to disk on a genuinely new one, and holding the device
        # lock across a file write would block the next poll for no reason.
        # Merely selecting a programme on the appliance is enough to learn
        # it - it does not have to be cooked.
        if step.get("mode") and step.get("recipeId") is not None:
            try:
                from .fryer_presets import get_fryer_presets
                get_fryer_presets().remember(
                    self.unique_id, step.get("mode"), step.get("recipeId"),
                    step.get("recipeType"), step.get("cookTemp"),
                    step.get("cookSetTime"),
                    # Only a freshly loaded programme reports the settings
                    # the appliance itself holds for it. Once it is
                    # running, the same fields carry whatever was adjusted
                    # for this one cook.
                    trust_settings=(status_key == "ready"))
            except Exception as e:
                # Learning is a convenience; failing at it must not cost
                # the status update that already happened above.
                log.debug(f"Fryer programme could not be noted: {e}")

        # The whole answer at debug level. Still open, and only a log of a
        # programme that uses them can settle it: what the preheat fields
        # carry - every reading so far had them at 0 - and whether
        # shakeStatus turns into something when a programme asks for the
        # basket to be shaken.
        log.debug(f"VeSync air fryer {self.name}: status {result}")

    # How long a sent temperature waits for the appliance's verdict.
    # Several polls at the foreground interval - after that the answer is
    # not coming, and saying nothing beats saying it once the cook has
    # moved on.
    TEMP_VERDICT_TIMEOUT = 90

    def _judge_sent_temperature(self, step):
        """Compares a temperature that was sent with the one that came back.

        Called from apply_status_response, under the lock.

        Dating the response is the hard part. The cloud serves bypassV2
        answers from a cache for up to a minute, and a cached answer looks
        exactly like a refused temperature: both carry the old value.
        The time sent alongside settles it. That one the appliance does
        apply - a tester's log shows cookSetTime going 600 -> 508 three
        seconds after the call, in the same reply that kept cookTemp at
        205 - so a response already carrying it was formed after the
        command. Anything else is left to the next poll.

        Nothing is judged while the fields are missing (an empty stepArray
        means the programme is over), and the request then simply expires:
        a finished cook has no business announcing a temperature.
        """
        if self._sent_temp is None:
            return
        if time.time() - self._sent_temp_at > self.TEMP_VERDICT_TIMEOUT:
            self._sent_temp = None
            return
        if (self._sent_temp_time is not None
                and step.get("cookSetTime") != self._sent_temp_time):
            return  # this response predates the command
        kept = step.get("cookTemp")
        if kept != self._sent_temp:
            self._temp_verdict = (self._sent_temp, kept)
            log.info(f"VeSync air fryer {self.name}: temperature "
                     f"{self._sent_temp} was accepted and not applied - "
                     f"the appliance stays at {kept}")
        self._sent_temp = None

    def take_temperature_verdict(self):
        """The pending verdict on a sent temperature, once.

        Returns ``(requested, kept)`` or None. Clearing it here is what
        keeps it to a single utterance: the poll that produced it comes
        round again every fifteen seconds.
        """
        with self._lock:
            verdict = self._temp_verdict
            self._temp_verdict = None
        return verdict

    # ------ Control ------
    @property
    def can_start_cook(self):
        """Whether a programme can be started right now.

        Only from a state known to be idle - deliberately the opposite way
        round from can_end_cook. Stopping something unknown is safe;
        starting into a state nobody has seen is not, and the appliance
        heats.

        'cookStop' is NOT idle: it means paused, so a programme is still
        loaded with time left on it. Starting another one over the top of
        it would throw away a half-cooked meal.
        """
        status = (self.cook_status or "").lower()
        return status in ("standby", "cookend")

    @property
    def can_end_cook(self):
        """Whether there is a programme worth stopping.

        'cookEnd' is over already and 'standby' never started; stopping
        either would be a command with nothing to do. A paused programme
        ('cookStop') very much can be stopped - that is the one way to be
        rid of it without going back to the appliance. A state nobody has
        seen counts as stoppable too: stopping is the safe direction, so
        the cautious answer here is yes.
        """
        status = (self.cook_status or "").lower()
        return bool(status) and status not in ("standby", "cookend")

    # The range the appliance itself accepts, from its manual. A value
    # outside it is refused here rather than sent: the cloud would reject
    # it too, but as a bare error code that says nothing a cook can act on.
    TEMP_RANGE_C = (80, 205)
    TEMP_RANGE_F = (175, 400)
    # Durations the appliance offers. One second is not a cooking time and
    # would more likely be a typo; the upper end is its own maximum.
    TIME_RANGE_SECONDS = (60, 60 * 60)

    # What a programme started by hand is called on the wire. Kept, and
    # no longer used: a startCook carrying this mode with recipe id 1 -
    # the shape the open documentation gives for a manual cook - was
    # refused by a CAF-P583S with the cloud's code 11000000, so the free
    # start is no longer offered. The open documentation puts a manual
    # cook on the separate `cookMode` method instead, in a different
    # envelope; should that ever be tried, these are the values it wants.
    CUSTOM_MODE = "custom"
    CUSTOM_RECIPE_ID = 1
    CUSTOM_RECIPE_TYPE = 3

    def temperature_range(self):
        """(minimum, maximum) in the unit the appliance reports."""
        if (self.temp_unit or "c").lower() == "f":
            return self.TEMP_RANGE_F
        return self.TEMP_RANGE_C

    def known_programmes(self):
        """The programme keys this appliance has shown us, in a fixed order.

        Empty until the appliance has had a programme loaded at least once
        - which is why the interface says so in words rather than opening
        an empty list. There used to be a free start to fall back on here;
        the appliance refuses it, so the explanation is all there is.
        """
        try:
            from .fryer_presets import get_fryer_presets
            return get_fryer_presets().modes_for(self.unique_id)
        except Exception as e:
            log.debug(f"Fryer programmes could not be read: {e}")
            return []

    def programme_details(self, mode):
        """Stored id, type, temperature and duration of one programme."""
        try:
            from .fryer_presets import get_fryer_presets
            return get_fryer_presets().get(self.unique_id, mode)
        except Exception as e:
            log.debug(f"Fryer programme could not be read: {e}")
            return None

    def start_cook(self, mode, temperature, seconds, recipe_id=None,
                   recipe_type=None):
        """Starts a cooking programme.

        Args:
            mode: language-neutral programme key ('Steak', 'custom', ...)
            temperature: set temperature in the appliance's own unit
            seconds: duration in seconds - the appliance counts in seconds
                (see remaining_time_display), and passing minutes here
                would cook for a sixtieth of the intended time
            recipe_id / recipe_type: from the learned programme; omitted
                for a programme whose id is not known

        The payload mirrors what the VeSync app sends. ``startAct`` carries
        the actual settings; the fields beside it identify the programme.
        """
        low, high = self.temperature_range()
        if not isinstance(temperature, int) or not low <= temperature <= high:
            # Translators: Error when a temperature outside the appliance's
            # range was entered. {low}/{high} = the permitted values.
            raise ValueError(_("The temperature has to be between {low} and "
                               "{high}").format(low=low, high=high))
        min_s, max_s = self.TIME_RANGE_SECONDS
        if not isinstance(seconds, int) or not min_s <= seconds <= max_s:
            # Translators: Error when a cooking time outside the permitted
            # range was entered. {low}/{high} = the permitted values in
            # minutes.
            raise ValueError(_("The time has to be between {low} and {high} "
                               "minutes").format(low=min_s // 60,
                                                 high=max_s // 60))

        if recipe_id is None:
            # Refuse rather than guess, and that changed with the free
            # start. While one existed, falling back to the pair a manual
            # cook uses was the right answer here. Without it the same
            # line is a trap: id 1 is Steak, so a programme that had lost
            # its id would have been sent as Steak while the interface
            # said something else - on an appliance that heats.
            #
            # Only reachable with a damaged programme file: the store
            # refuses to save an entry without an id (see
            # FryerPresets.remember). Saying so is then the whole of the
            # safe answer.
            #
            # Translators: Error when a cooking programme is missing the
            # identifier the appliance needs to start it.
            raise ValueError(_("VeSync: this programme has no identifier "
                               "and cannot be started"))
        data = {
            "accountId": getattr(self._api, "account_id", None),
            "mode": mode,
            "recipeId": recipe_id,
            # A missing type, unlike a missing id, is safe to fill in:
            # every programme this appliance has ever reported came back
            # with recipeType 3, all eleven of them.
            "recipeType": recipe_type if recipe_type is not None
            else self.CUSTOM_RECIPE_TYPE,
            "recipeName": mode,
            "readyStart": True,
            "hasPreheat": 0,
            "hasWarm": False,
            "cookTempDECP": 0,
            "imageUrl": "",
            "tempUnit": (self.temp_unit or "c").lower(),
            "startAct": {
                "appointingTime": 0,
                "cookSetTime": seconds,
                "cookTemp": temperature,
                "cookTempDECP": 0,
                "imageUrl": "",
                "level": 0,
                "preheatTemp": 0,
                "shakeTime": 0,
                "targetTemp": 0,
            },
        }
        resp = self._api.call_bypass_v2(self, "startCook", data)
        if not self._bypass_call_succeeded(resp):
            self._log_rejected_command("startCook", data, resp)
            # Translators: Error when a cooking programme could not be
            # started.
            raise RuntimeError(_("VeSync: the programme could not be started"))
        self._log_accepted_command("startCook", data)
        # No optimistic state here either, for the same reason as in
        # end_cook: what the appliance is doing is the appliance's to
        # report, and the next poll is seconds away.
        return True

    @property
    def can_adjust_cook(self):
        """Whether time and temperature can still be changed.

        Only once the programme is actually running.

        'ready' is deliberately excluded, and that is measured, not
        assumed: a time change and a temperature change were both refused
        in that state with the cloud's code 11017000, while the same two
        changes went through minutes later while cooking. Offering a
        control that is certain to be refused is worse than not offering
        it - the reader gets an error for doing exactly what the interface
        invited.

        A pause ('cookStop') is kept: it is a running programme with the
        clock held, much closer to cooking than to a programme that has
        not begun. Whether the appliance agrees has not been tested, and
        the cost of being wrong there is one error message.
        """
        status = (self.cook_status or "").lower()
        return status in ("cooking", "cookstop")

    def set_time_or_temp(self, temperature=None, seconds=None):
        """Changes the temperature or the time of a loaded programme.

        One of the two is asked for; BOTH are sent.

        Sending only the field being changed looked tidier and does not
        work: a payload of nothing but ``cookSetTemp`` was refused four
        times in a row on a real appliance, in two different states, while
        a payload of nothing but ``cookSetTime`` went through. The only
        shape anyone has documented carries both, so that is what goes out
        now, with the unchanged quantity filled in from what the appliance
        currently reports.

        The time sent is the time REMAINING, not the programme's whole
        duration. Measured: a six-minute programme 25 seconds in was given
        600 seconds, and came back with 600 seconds still to run - the
        appliance takes the value as the new total and starts the clock
        again from it. Sending the remaining time therefore leaves the
        cook with what it had, which is what someone changing only the
        temperature is entitled to expect.

        Confirmed a second time on a whole programme: 508 seconds went out
        with a temperature change, cookSetTime came back as 508 and the
        countdown carried straight on at 505. The clock did not restart
        and the meal came out at the time it was going to anyway - only
        the programme's nominal duration is quietly redefined to what was
        left of it, which nothing reads out.

        Note the field names. ``startCook`` carries ``cookTemp`` inside
        ``startAct``; this call wants ``cookSetTemp`` at the top level.
        Same quantity, different spelling, and mixing them up would be
        accepted as "no temperature given".

        The time lands and the temperature does not - six attempts, two
        payload shapes, both directions, cooking and paused. The caller
        is told either way, because ``_judge_sent_temperature`` reads
        the next status and says so when the appliance kept its own.
        """
        if (temperature is None) == (seconds is None):
            raise ValueError("set_time_or_temp takes a temperature OR a time")

        data = {}
        if temperature is not None:
            low, high = self.temperature_range()
            if not isinstance(temperature, int) or not low <= temperature <= high:
                # Translators: Error when a temperature outside the
                # appliance's range was entered. {low}/{high} = the
                # permitted values.
                raise ValueError(_("The temperature has to be between {low} "
                                   "and {high}").format(low=low, high=high))
            data["cookSetTemp"] = temperature
        else:
            min_s, max_s = self.TIME_RANGE_SECONDS
            if not isinstance(seconds, int) or not min_s <= seconds <= max_s:
                # Translators: Error when a cooking time outside the
                # permitted range was entered. {low}/{high} = the permitted
                # values in minutes.
                raise ValueError(_("The time has to be between {low} and "
                                   "{high} minutes").format(
                    low=min_s // 60, high=max_s // 60))
            data["cookSetTime"] = seconds

        # Fill in the quantity that is not being changed, so the payload
        # carries both (see the docstring).
        if "cookSetTemp" not in data and self.target_temp is not None:
            data["cookSetTemp"] = self.target_temp
        if "cookSetTime" not in data:
            keep = self.time_remaining or self.cook_set_time
            if keep:
                data["cookSetTime"] = int(keep)

        # Nothing else goes in here, and that is measured rather than
        # tidy. The payload was once enriched with tempUnit,
        # cookTempDECP and accountId, on the theory that a temperature
        # cannot be read without its unit while a number of seconds can -
        # startCook sends all three and its temperature does land.
        #
        # The appliance refused the enriched call outright, with the
        # cloud's code 11000000, and refused it for a pure TIME change
        # too - the one thing that had worked reliably four times over.
        # So the three fields do not help the temperature and they break
        # the time. The bare pair is the only shape this appliance takes.
        #
        # What that leaves: setTimeOrTemp on a CAF-P583S can set the time
        # and cannot set the temperature, in any shape tried so far.

        resp = self._api.call_bypass_v2(self, "setTimeOrTemp", data)
        if not self._bypass_call_succeeded(resp):
            self._log_rejected_command("setTimeOrTemp", data, resp)
            # Translators: Error when the time or temperature of a running
            # programme could not be changed.
            raise RuntimeError(_("VeSync: the change was not accepted"))
        self._log_accepted_command("setTimeOrTemp", data)
        if temperature is not None:
            # "Accepted" covers the call, not the temperature in it. One
            # accepted call sent 180 degrees with 508 seconds while the
            # appliance was cooking at 205: the seconds landed, the
            # degrees did not, and it went on regulating to 205 for the
            # rest of the programme. Only the next status shows which
            # happened, so the answer is noted and judged there.
            with self._lock:
                self._sent_temp = temperature
                self._sent_temp_time = data.get("cookSetTime")
                self._sent_temp_at = time.time()
                self._temp_verdict = None
        return True

    def end_cook(self):
        """Stops the running programme.

        The only command this class sends, and deliberately so: on an
        appliance that heats, stopping is the direction that cannot go
        wrong, and it establishes that the command channel works at all
        before anything is trusted to start a programme. Empty payload,
        like resetFilter.
        """
        resp = self._api.call_bypass_v2(self, "endCook", {})
        if not self._bypass_call_succeeded(resp):
            self._log_rejected_command("endCook", {}, resp)
            # Translators: Error when a running cooking programme could not
            # be stopped.
            raise RuntimeError(_("VeSync: the programme could not be stopped"))
        self._log_accepted_command("endCook", {})
        # No optimistic state update here, unlike the switches elsewhere.
        # Those flip a relay and are done; this appliance is hot, and
        # showing "standby" a moment before it is true would be the one
        # kind of wrong worth avoiding. The next poll is at most fifteen
        # seconds away with the dialog open, and it reports what the
        # appliance actually did.
        return True

    def toggle_switch(self, on):
        """Refuses, with a reason.

        Not simply absent: several paths (the device tree, the favorites
        layer) call this on any device, and an AttributeError there would
        arrive as "switching error" - correct but uninformative. A refusal
        that names the model can be reported.
        """
        # Translators: Error when a device is recognised but cannot be
        # operated yet. {name} = device name, {type} = model designation.
        raise ValueError(_("{name} is shown but cannot be operated yet "
                           "({type})").format(
            name=self.name, type=self.device_type_raw))
