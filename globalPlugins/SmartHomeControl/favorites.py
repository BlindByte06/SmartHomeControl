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

# NVDA-Logger verwenden, damit Meldungen (z.B. Speicherfehler) im NVDA-Log
# landen. NVDA hängt seine Handler an den "nvda"-Logger, nicht an den
# Root-Logger – logging.getLogger(__name__) würde ins Leere loggen.
# Fallback auf Standard-Logging für Nutzung außerhalb von NVDA (z.B. Tests).
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
                log.info(f"Favoriten: Legacy-Datei nach Migration archiviert: {backup}")
            except Exception as e:
                log.debug(f"Ignorierter Fehler in _migrate_legacy_file: {e}")
            return
        os.replace(_LEGACY_FAVORITES_FILE, FAVORITES_FILE)
        log.info(f"Favoriten von {_LEGACY_FAVORITES_FILE} nach {FAVORITES_FILE} migriert")
    except Exception as e:
        log.warning(f"Favoriten-Migration fehlgeschlagen: {e}")


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
                log.debug(f"Favoriten geladen: {len(self._favorites)} Geräte")
                self._migrate_platform_fields()
            else:
                self._favorites = []
        except Exception as e:
            log.error(f"Favoriten-Datei konnte nicht geladen werden: {e}")
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
            log.info("Favoriten: Cozytouch-Einträge auf korrekte Plattform migriert")
            self._save()

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
            log.debug(f"Favoriten gespeichert: {len(self._favorites)} Geräte")
        except Exception as e:
            log.error(f"Favoriten konnten nicht gespeichert werden: {e}")
    
    def add(self, device):
        """Adds a device to the favorites.

        Args:
            device: device wrapper (Meross/Netatmo/VeSync/Cozytouch)

        Returns:
            True if added, False if already present
        """
        uid = device.unique_id if hasattr(device, 'unique_id') else device.uuid
        if self.is_favorite(uid):
            return False

        platform = platform_of(device)

        # Determine the device type
        device_type = self._determine_device_type(device, platform)
        
        self._favorites.append({
            "uuid": uid,
            "name": device.name,
            "platform": platform,
            "device_type": device_type,
        })
        self._save()
        log.info(f"Favorit hinzugefügt: {device.name} ({platform})")
        return True
    
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
            log.info(f"Favorit entfernt: {uuid}")
            return True
        return False
    
    def is_favorite(self, uuid):
        """Checks whether a device is a favorite"""
        return any(f.get('uuid') == uuid for f in self._favorites)
    
    def get_all(self):
        """Returns all favorites"""
        return list(self._favorites)
    
    def get_ordered(self):
        """Favoriten in der ANZEIGE-Reihenfolge des Favoriten-Tabs.

        Gruppiert nach Plattform in fester Reihenfolge (Meross, Netatmo,
        VeSync, Cozytouch); innerhalb jeder Plattform in der Reihenfolge, in
        der die Favoriten hinzugefügt wurden. So entspricht "Favorit N" genau
        dem N-ten Eintrag von oben im Favoriten-Tab.
        """
        from .platform_utils import PLATFORMS
        ordered = []
        for platform in PLATFORMS:
            ordered.extend(self.get_by_platform(platform))
        return ordered

    def get_by_platform(self, platform):
        """Returns the favorites of one platform.

        Args:
            platform: 'meross', 'netatmo', 'vesync' or 'cozytouch'
        """
        return [f for f in self._favorites if f.get('platform') == platform]

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
        """Gleicht die gespeicherten Anzeigenamen mit der Geräteliste ab.

        Der Name eines Favoriten wurde bisher beim Anlegen eingefroren. Wird
        das Gerät später in der Meross-/Levoit-/Netatmo-App umbenannt, zeigte
        der Favoriten-Tab weiter den alten Namen - sichtbar überall dort, wo
        auf den gespeicherten Namen zurückgegriffen wird: in der Zeile
        "{name} (nicht verfügbar)" und in den Ansagen der Favoriten-Gesten.
        Zwei verschiedene Namen für dasselbe Gerät sind ohne Blickkontakt
        besonders unangenehm, weil man sie nicht nebeneinanderlegen kann.

        Läuft über Geräte UND deren Kanäle (Favoriten können einzelne
        Ausgänge einer Steckdosenleiste sein). Gespeichert wird höchstens
        einmal, auch wenn sich mehrere Namen geändert haben.

        Args:
            devices: aktuelle Geräteliste (``plugin.devices``)

        Returns:
            Anzahl der aktualisierten Einträge
        """
        if not devices or not self._favorites:
            return 0
        # Aktuelle Namen einsammeln: unique_id -> name
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
                log.debug(f"Ignorierter Fehler in sync_names: {e}")

        changed = 0
        for fav in self._favorites:
            new_name = current.get(fav.get('uuid'))
            if new_name and new_name != fav.get('name'):
                log.info(
                    f"Favorit umbenannt: '{fav.get('name')}' -> '{new_name}'")
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
