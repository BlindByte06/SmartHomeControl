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
                log.info(
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
                # Sensor values: never protected, always go into the wrapper.
                if "air_quality" in result:
                    self.air_quality = result.get("air_quality")
                if "air_quality_value" in result:
                    self.air_quality_value = result.get("air_quality_value")
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
                # Sensor values: no protection window.
                aq_level = ext.get("airQualityLevel")
                if aq_level is not None:
                    self.air_quality = aq_level
                aq_value = ext.get("airQuality")
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
            raise ValueError(_("Invalid fan level"))
        if speed_int not in self.fan_levels:
            raise ValueError(_("Fan level {level} is not supported").format(level=speed_int))

        data = {"id": 0, "level": speed_int, "type": "wind"}
        resp = self._api.call_bypass_v2(self, "setLevel", data)
        if not self._bypass_call_succeeded(resp):
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
            raise RuntimeError(_("Display control is not supported by this "
                                 "device"))
        data = {"state": bool(on)}
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

    def toggle_child_lock(self, on):
        """Switches the child lock on or off.

        BypassV2 setChildLock: {'child_lock': bool}
        """
        if not self.supports_child_lock:
            raise RuntimeError(_("Child lock is not supported by this device"))
        data = {"child_lock": bool(on)}
        resp = self._api.call_bypass_v2(self, "setChildLock", data)
        if not self._bypass_call_succeeded(resp):
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
            raise RuntimeError(_("Night light is not supported by this device"))
        if mode not in self.nightlight_modes:
            raise ValueError(_("Night light mode '{mode}' is not supported").format(mode=mode))
        data = {"night_light": mode}
        resp = self._api.call_bypass_v2(self, "setNightLight", data)
        if not self._bypass_call_succeeded(resp):
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
            raise RuntimeError(_("Auto profile is not supported by this device"))
        if preference not in self.auto_preferences:
            raise ValueError(_("Auto profile '{value}' is not supported").format(value=preference))
        if room_size is None:
            room_size = self.auto_room_size or 600
        data = {"type": preference, "room_size": int(room_size)}
        resp = self._api.call_bypass_v2(self, "setAutoPreference", data)
        if not self._bypass_call_succeeded(resp):
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
            raise RuntimeError(_("Filter reset is not supported by this device"))
        resp = self._api.call_bypass_v2(self, "resetFilter", {})
        if not self._bypass_call_succeeded(resp):
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
