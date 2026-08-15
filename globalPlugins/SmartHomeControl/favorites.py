# -*- coding: utf-8 -*-
"""
Smart Home Control - Favorites system
Mark devices as favorites for quicker access.
Persistent storage as JSON in the NVDA addons folder – on the same level
as e.g. ``clock.json`` or ``AVC.json``. This path survives add-on updates
because NVDA only replaces the add-on subfolder on update, not its siblings.

"""

import os
import json

from .platform_utils import platform_of

# Use NVDA's logger so messages (e.g. save errors) reach the NVDA log.
# NVDA attaches its handlers to the "nvda" logger, not to the root logger,
# so logging.getLogger(__name__) would go nowhere. Falls back to standard
# logging for use outside NVDA (e.g. tests).
try:
    from logHandler import log
except ImportError:
    import logging
    log = logging.getLogger(__name__)

# Directory derivation:
# __file__                        =
# .../addons/SmartHomeControl/globalPlugins/SmartHomeControl/favorites.py
# dirname(__file__)               =
# .../addons/SmartHomeControl/globalPlugins/SmartHomeControl
# dirname(...) (2)                = .../addons/SmartHomeControl/globalPlugins
# dirname(...) (3) = ADDON folder = .../addons/SmartHomeControl
# dirname(...) (4) = ADDONS folder = .../addons   <- this is where we persist
_ADDON_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_ADDONS_DIR = os.path.dirname(_ADDON_DIR)

# Current (persistent) path: next to the other .json files in /addons/.
FAVORITES_FILE = os.path.join(_ADDONS_DIR, "SmartHomeControl_favorites.json")
# Legacy path (old versions stored inside the add-on folder). Migrated once and
# deleted afterwards.
_LEGACY_FAVORITES_FILE = os.path.join(_ADDON_DIR, "device_favorites.json")


def _migrate_legacy_file():
    """Moves an old favorites file from the add-on folder to /addons/.

    Called before the first _load. If a file already exists at the new
    location, the legacy file is NOT overwritten (protects against an
    accidental data rollback) – it is renamed instead.
    """
    if not os.path.isfile(_LEGACY_FAVORITES_FILE):
        return
    try:
        if os.path.isfile(FAVORITES_FILE):
            # Both files exist: mark legacy as .bak, the new file stays.
            backup = _LEGACY_FAVORITES_FILE + ".migrated.bak"
            try:
                os.replace(_LEGACY_FAVORITES_FILE, backup)
                log.info(f"Favourites: legacy file archived after migration: {backup}")
            except Exception as e:
                log.debug(f"Ignored error in _migrate_legacy_file: {e}")
            return
        os.replace(_LEGACY_FAVORITES_FILE, FAVORITES_FILE)
        log.info(f"Favourites migrated from {_LEGACY_FAVORITES_FILE} to {FAVORITES_FILE}")
    except Exception as e:
        log.warning(f"Favourites migration failed: {e}")


class DeviceFavorites:
    """Manages device favorites.

    Stored format:
    [
        {
            "uuid": "abc123",
            "name": "Steckdose Wohnzimmer",
            "platform": "meross",
            "device_type": "plug"
        },
        ...
    ]
    """
    
    def __init__(self):
        self._favorites = []
        # One-time migration of the old file from the add-on subfolder.
        _migrate_legacy_file()
        self._load()
    
    def _load(self):
        """Loads favorites from the JSON file"""
        try:
            if os.path.exists(FAVORITES_FILE):
                with open(FAVORITES_FILE, 'r', encoding='utf-8') as f:
                    self._favorites = json.load(f)
                log.debug(f"Favourites loaded: {len(self._favorites)} devices")
                self._migrate_platform_fields()
                self._assign_slots()
            else:
                self._favorites = []
        except Exception as e:
            log.error(f"Could not load the favourites file: {e}")
            self._favorites = []
    
    def _migrate_platform_fields(self):
        """Fixes misclassified legacy entries.

        Older versions did not know Cozytouch in the platform detection
        and stored Cozytouch favorites as "meross". Cozytouch UUIDs carry
        the prefix "cozytouch_" (see CozytouchWaterHeater.uuid), which
        allows repairing them losslessly.
        """
        changed = False
        for fav in self._favorites:
            if (str(fav.get('uuid', '')).startswith('cozytouch_')
                    and fav.get('platform') != 'cozytouch'):
                fav['platform'] = 'cozytouch'
                fav['device_type'] = 'water_heater'
                changed = True
        if changed:
            log.info("Favourites: Cozytouch entries migrated to the correct platform")
            self._save()

    def _assign_slots(self):
        """Hands out the fixed layer slots 1-9.

        The slot belongs to the device, not to its list position: once
        given, it survives the removal of other favourites, so a memorised
        "2 switches the floor lamp" keeps holding. (The earlier numbering
        by display order shifted on every change to the list.)

        On the first load after the update, existing favourites get their
        slots in the previous display order, so nothing moves for the user.
        Invalid or duplicate slots (e.g. a hand-edited file) are cleaned up;
        freed slots go to favourites without one.

        Returns:
            True if anything changed (in which case it was saved).
        """
        changed = False
        seen = set()
        for fav in self._favorites:
            slot = fav.get('slot')
            if isinstance(slot, int) and 1 <= slot <= 9 and slot not in seen:
                seen.add(slot)
            elif slot is not None:
                fav['slot'] = None
                changed = True
        free = sorted(set(range(1, 10)) - seen)
        # Display order, so the first assignment matches the previous
        # numbering
        for fav in self.get_ordered():
            if not free:
                break
            if fav.get('slot') is None:
                fav['slot'] = free.pop(0)
                changed = True
        if changed:
            self._save()
        return changed

    def get_by_slot(self, number):
        """Returns the favourite with layer slot ``number`` (or None)."""
        for fav in self._favorites:
            if fav.get('slot') == number:
                return fav
        return None

    def _save(self):
        """Saves favorites atomically to the JSON file.

        Writes to a ``.tmp`` file first and then replaces the target file
        via ``os.replace`` – protects against half-written files on power
        loss/crash in the middle of writing.
        """
        try:
            tmp_path = FAVORITES_FILE + ".tmp"
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self._favorites, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, FAVORITES_FILE)
            log.debug(f"Favourites saved: {len(self._favorites)} devices")
        except Exception as e:
            log.error(f"Could not save the favourites: {e}")
    
    def add(self, device):
        """Adds a device to the favorites.

        Args:
            device: device wrapper (Meross/Netatmo/VeSync/Cozytouch)

        Returns:
            The assigned layer slot (1-9), True when added without a free
            slot (from the tenth favourite on), False if already present.
            Both success cases are truthy, so existing ``if add(...)``
            callers keep working.
        """
        uid = device.unique_id if hasattr(device, 'unique_id') else device.uuid
        if self.is_favorite(uid):
            return False

        platform = platform_of(device)

        # Determine the device type
        device_type = self._determine_device_type(device, platform)

        entry = {
            "uuid": uid,
            "name": device.name,
            "platform": platform,
            "device_type": device_type,
            "slot": None,
        }
        self._favorites.append(entry)
        # _assign_slots saves by itself once it hands out a slot; with no
        # free slot the new entry still has to be persisted.
        if not self._assign_slots():
            self._save()
        log.info(f"Favourite added: {device.name} ({platform}), slot {entry['slot']}")
        return entry['slot'] if entry['slot'] else True
    
    def remove(self, uuid):
        """Removes a device from the favorites.

        Args:
            uuid: UUID of the device

        Returns:
            True if removed, False if not found
        """
        before = len(self._favorites)
        self._favorites = [f for f in self._favorites if f.get('uuid') != uuid]
        if len(self._favorites) < before:
            self._save()
            # The freed slot goes straight to a favourite without one
            # (only exists from the tenth favourite on). Slots already
            # handed out do NOT move.
            self._assign_slots()
            log.info(f"Favourite removed: {uuid}")
            return True
        return False
    
    def is_favorite(self, uuid):
        """Checks whether a device is a favorite"""
        return any(f.get('uuid') == uuid for f in self._favorites)
    
    def get_all(self):
        """Returns all favorites"""
        return list(self._favorites)
    
    def get_ordered(self):
        """Favourites in the display order of the favourites tab.

        Grouped by platform in a fixed order (Meross, Netatmo, VeSync,
        Cozytouch), and within each platform by slot.
        """
        from .platform_utils import PLATFORMS
        ordered = []
        for platform in PLATFORMS:
            ordered.extend(self.get_by_platform(platform))
        return ordered

    def get_by_platform(self, platform):
        """Returns the favorites of one platform.

        Sorted by layer slot (1-9), favourites without one after them in
        the order they were added, so the tab reads "1: ..., 2: ...".

        Args:
            platform: 'meross', 'netatmo', 'vesync' or 'cozytouch'
        """
        matches = [f for f in self._favorites if f.get('platform') == platform]
        return sorted(matches, key=lambda f: (f.get('slot') is None, f.get('slot') or 0))

    def get_count(self, platform=None):
        """Returns the number of favorites.

        Args:
            platform: optional – 'meross', 'netatmo', 'vesync' or 'cozytouch'
        """
        if platform:
            return len(self.get_by_platform(platform))
        return len(self._favorites)

    def get_meross_count(self):
        """Returns the number of Meross favorites"""
        return self.get_count("meross")

    def get_netatmo_count(self):
        """Returns the number of Netatmo favorites"""
        return self.get_count("netatmo")

    def get_vesync_count(self):
        """Returns the number of VeSync favorites"""
        return self.get_count("vesync")

    def get_cozytouch_count(self):
        """Returns the number of Cozytouch favorites"""
        return self.get_count("cozytouch")
    
    def update_name(self, uuid, new_name):
        """Updates the device name of a favorite"""
        for fav in self._favorites:
            if fav.get('uuid') == uuid:
                fav['name'] = new_name
                self._save()
                return True
        return False

    def sync_names(self, devices):
        """Brings the stored display names in line with the device list.

        A favourite's name used to be frozen when it was added, so renaming
        the device in the manufacturer app left the old name showing - in
        the "{name} (unavailable)" row and in the favourites announcements.
        Two names for one device are especially awkward without sight.

        Covers devices AND their channels (a favourite can be a single
        outlet). Saves at most once, however many names changed.

        Args:
            devices: current device list (``plugin.devices``)

        Returns:
            Number of updated entries
        """
        if not devices or not self._favorites:
            return 0
        # Collect current names: unique_id -> name
        current = {}
        for dev in devices:
            try:
                uid = getattr(dev, 'unique_id', None) or getattr(dev, 'uuid', None)
                name = getattr(dev, 'name', None)
                if uid and name:
                    current[uid] = name
                for ch in (dev.get_channels() or []):
                    ch_uid = getattr(ch, 'unique_id', None)
                    ch_name = getattr(ch, 'name', None)
                    if ch_uid and ch_name:
                        current[ch_uid] = ch_name
            except Exception as e:
                log.debug(f"Ignored error in sync_names: {e}")

        changed = 0
        for fav in self._favorites:
            new_name = current.get(fav.get('uuid'))
            if new_name and new_name != fav.get('name'):
                log.info(
                    f"Favourite renamed: '{fav.get('name')}' -> '{new_name}'")
                fav['name'] = new_name
                changed += 1
        if changed:
            self._save()
        return changed


    def clear(self):
        """Deletes all favorites"""
        self._favorites = []
        self._save()
    
    def _determine_device_type(self, device, platform):
        """Detects the device type for categorization"""
        if platform == "netatmo":
            return getattr(device, 'device_type', 'unknown')

        if platform == "vesync":
            cls_name = type(device).__name__
            if cls_name == 'VeSyncPurifier':
                return 'purifier'
            if cls_name == 'VeSyncTowerFan':
                return 'fan'
            return 'other'

        if platform == "cozytouch":
            return 'water_heater'

        # Meross
        if getattr(device, 'is_plug', False):
            return 'plug'
        elif getattr(device, 'is_light', False):
            return 'light'
        elif getattr(device, 'is_diffuser', False):
            return 'diffuser'
        elif getattr(device, 'is_temperature_sensor', False):
            return 'temperature_sensor'
        elif getattr(device, 'is_water_sensor', False):
            return 'water_sensor'
        elif hasattr(device, 'type') and 'msh' in device.type.lower():
            return 'hub'
        return 'other'


# Singleton instance
_instance = None

def get_favorites():
    """Returns the global favorites instance (singleton)"""
    global _instance
    if _instance is None:
        _instance = DeviceFavorites()
    return _instance
