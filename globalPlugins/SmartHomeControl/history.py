# -*- coding: utf-8 -*-
"""
Smart Home Control - history (events + readings).

The history deliberately separates two things that used to share one list:

* **Events** are rare, individually important and kept for a long time - they
  are wanted *complete*. That includes the origin: a switch is either
  ``local`` (the user, via dialog or favorites gesture), ``extern`` (vendor
  app, voice assistant, button on the device) or ``system`` (automatic).
* **Readings** are frequent, individually meaningless and only interesting as
  a series - they are wanted *condensed*. They are therefore stored as change
  points only: a value reaches the file only if it differs from the last
  stored one by more than a threshold, or if an hour has passed since the
  last data point.

Both used to live in one ring of 5000 entries. Since readings occur orders of
magnitude more often, they crowded out exactly what is looked for later: the
switching actions. ``energy.py`` already solved this for the power samples
(its own file); the same step follows here for temperature, humidity, CO2 and
air pressure.

Power (watts) is deliberately NOT kept here - ``energy.py`` is responsible
for that and also integrates energy amounts from it.

Persisted as JSON in the NVDA addons folder (next to e.g. ``clock.json``).
That path survives add-on updates: NVDA only replaces the add-on subfolder,
not its neighbouring files.
"""

import os
import json
import time
import csv
import io
import locale
import threading
from datetime import datetime

from .platform_utils import platform_of

# Use the NVDA logger (see favorites.py) so this module's messages show up
# in the NVDA log. Fallback for use outside NVDA.
try:
    from logHandler import log
except ImportError:
    import logging
    log = logging.getLogger(__name__)

# i18n: display texts (action names, relative times, CSV headers) should follow
# the NVDA language. The fallback keeps the module importable outside of NVDA.
try:
    import addonHandler
    addonHandler.initTranslation()
except Exception:
    pass
if "_" not in globals():  # fallback outside of NVDA
    def _(s):
        return s

# Directory derivation analogous to favorites.py:
# __file__         =
# .../addons/SmartHomeControl/globalPlugins/SmartHomeControl/history.py
# dirname(...) (3) = ADDON folder  = .../addons/SmartHomeControl
# dirname(...) (4) = ADDONS folder = .../addons   <- this is where we persist
_ADDON_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_ADDONS_DIR = os.path.dirname(_ADDON_DIR)

# Events keep the previous file name, so the file stays the same across
# updates and the migration can work in place.
HISTORY_FILE = os.path.join(_ADDONS_DIR, "SmartHomeControl_history.json")
# Readings get their own file (like SmartHomeControl_energy.json), so a
# switching action never rewrites the much larger readings file.
MEASUREMENTS_FILE = os.path.join(_ADDONS_DIR, "SmartHomeControl_measurements.json")
# Legacy path (old versions stored inside the add-on folder).
_LEGACY_HISTORY_FILE = os.path.join(_ADDON_DIR, "device_history.json")

# ---------------------------------------------------------------------------
# Retention: separate quotas, trimmed separately. An event must NEVER be
# crowded out by a reading - the core flaw of the old version with its shared
# ring of 5000 entries.
# ---------------------------------------------------------------------------
MAX_EVENT_ENTRIES = 2000
EVENT_RETENTION_SECONDS = 365 * 86400        # 1 year
MAX_MEASUREMENT_ENTRIES = 20000
MEASUREMENT_RETENTION_SECONDS = 90 * 86400   # 90 days

# Backwards compatibility: old name that other code may read.
MAX_HISTORY_ENTRIES = MAX_EVENT_ENTRIES

# Debounced saving: every entry used to rewrite the WHOLE file at once.
# Entries are now collected and only saved once enough time has passed OR
# enough of them have piled up. flush_pending() (called from the plugin's
# terminate) writes the rest.
SAVE_DEBOUNCE_SECONDS = 30
SAVE_DEBOUNCE_MAX_ENTRIES = 20

# ---------------------------------------------------------------------------
# Origin of an event
# ---------------------------------------------------------------------------
SOURCE_LOCAL = 'local'     # the user (dialog or favorites gesture)
SOURCE_EXTERN = 'extern'   # vendor app, voice assistant, button on the device
SOURCE_SYSTEM = 'system'   # automatic (schedule, rule)

# ---------------------------------------------------------------------------
# Readings: change thresholds
# ---------------------------------------------------------------------------
# A value is only stored if it differs from the last STORED one by more than
# this threshold. The thresholds sit deliberately just above the noise of the
# respective sensors: smaller would be noise again, larger would cut off real
# curves.
MEASUREMENT_THRESHOLDS = {
    'temperature': 0.3,   # K
    'humidity': 2.0,      # %
    'co2': 50.0,          # ppm
    'pressure': 1.0,      # mbar
    'pm25': 3.0,          # ug/m3 (Levoit air purifiers)
    'pm10': 3.0,          # ug/m3 (Levoit Sprout/V2)
    'noise': 5.0,         # dB (Netatmo indoor module)
}
# Even without a change, a data point after this time at the latest, so a
# flat line does not look like a gap and min/max/average stay correct over
# long quiet phases.
MEASUREMENT_MAX_SILENCE = 3600.0

# Order and labels of the quantities in the display and the CSV.
MEASUREMENT_ORDER = ('temperature', 'humidity', 'co2', 'pressure',
                     'pm25', 'pm10', 'noise')


def _measurement_labels():
    """Display names of the quantities (at runtime, so they translate)."""
    return {
        # Translators: Names of the measured quantities in the history.
        'temperature': _("Temperature"),
        # Translators: Name of a measured quantity, in the history and as a CSV
        # column heading.
        'humidity': _("Humidity"),
        # Translators: Name of a measured quantity, in the history and as a CSV
        # column heading.
        'co2': _("CO₂"),
        # Translators: Name of a measured quantity, in the history and as a CSV
        # column heading.
        'pressure': _("Air pressure"),
        # Translators: Name of a measured quantity, in the history and as a CSV
        # column heading.
        'pm25': _("Particulate matter PM2.5"),
        # Translators: Name of a measured quantity, in the history and as a CSV
        # column heading.
        'pm10': _("Particulate matter PM10"),
        # Translators: Name of a measured quantity, in the history and as a CSV
        # column heading.
        'noise': _("Noise"),
    }


MEASUREMENT_UNITS = {
    'temperature': '°C',
    'humidity': '%',
    'co2': 'ppm',
    'pressure': 'mbar',
    'pm25': 'µg/m³',
    'pm10': 'µg/m³',
    'noise': 'dB',
}

# Decimal places per quantity for the display.
_MEASUREMENT_DIGITS = {
    'temperature': 1,
    'humidity': 0,
    'co2': 0,
    'pressure': 1,
    'pm25': 0,
    'pm10': 0,
    'noise': 0,
}


def format_measurement(quantity, value):
    """Formats a reading with its unit, e.g. ``21.4 °C``."""
    if value is None:
        return ''
    digits = _MEASUREMENT_DIGITS.get(quantity, 1)
    try:
        text = f"{float(value):.{digits}f}".replace('.', ',')
    except (TypeError, ValueError):
        return str(value)
    unit = MEASUREMENT_UNITS.get(quantity, '')
    return f"{text} {unit}".strip()


def local_date(dt):
    """Date in the notation of the system region.

    NVDA sets the Python locale to the interface language, so %x gives what
    the reader is used to: "16.08.2026" in German, "8/16/2026" in the USA,
    "16/08/2026" in Spain. A fixed "%d.%m.%Y" made every date German - and
    for an American reader "08.09.2026" is genuinely ambiguous.
    """
    return dt.strftime('%x')


def local_time(dt):
    """Time of day in the notation of the region.

    Regions that write the time with AM/PM get "4:47 PM", the others the
    24-hour form. %X is deliberately not used: it appends the seconds, which
    lengthens a list column without telling the reader anything.
    """
    if dt.strftime('%p'):
        return dt.strftime('%I:%M %p').lstrip('0')
    return dt.strftime('%H:%M')


def local_datetime(dt):
    """Date and time, both in the notation of the region."""
    return f"{local_date(dt)} {local_time(dt)}"


def _csv_quantity_header(quantity):
    """Column heading of a quantity, with its unit: "Temperature (°C)"."""
    label = _measurement_labels().get(quantity, quantity)
    unit = MEASUREMENT_UNITS.get(quantity, '')
    return f"{label} ({unit})" if unit else label


def _csv_number(value, decimal_point):
    """Number for the CSV in the decimal notation of the system locale.

    Excel reads a CSV with the settings of the region. In a German Excel
    "28.5" is not a number at all but matches the date pattern day.month -
    the temperature 28.5 °C silently became "28 May" (cell value 46170,
    measured). With a comma it is a number that can be calculated with.
    """
    if value is None or value == '':
        return ''
    text = str(value)
    return text.replace('.', decimal_point) if decimal_point != '.' else text


def _device_key(device):
    """Identity of a device for the history: ``unique_id`` before ``uuid``.

    Sensors on a Meross hub ALL carry the hub's ``uuid`` - the wrapper offers
    ``unique_id`` for exactly that reason (hub UUID plus subdevice ID). The
    history used ``uuid`` and therefore merged every sensor of one hub into a
    single series: two sensors in different rooms became one row whose
    minimum, maximum and average mixed both rooms.
    """
    return getattr(device, 'unique_id', None) or device.uuid


def _migrate_legacy_file():
    """Moves an old history file from the add-on folder to /addons/.

    Called before the first _load. If a file already exists at the new
    location, the legacy file is archived as ``.migrated.bak`` instead of
    overwriting it – so existing history is never lost.
    """
    if not os.path.isfile(_LEGACY_HISTORY_FILE):
        return
    try:
        if os.path.isfile(HISTORY_FILE):
            backup = _LEGACY_HISTORY_FILE + ".migrated.bak"
            try:
                os.replace(_LEGACY_HISTORY_FILE, backup)
                log.info(f"History: legacy file archived after the migration: {backup}")
            except Exception as e:
                log.debug(f"Ignored error in _migrate_legacy_file: {e}")
            return
        os.replace(_LEGACY_HISTORY_FILE, HISTORY_FILE)
        log.info(f"History migrated from {_LEGACY_HISTORY_FILE} to {HISTORY_FILE}")
    except Exception as e:
        log.warning(f"History migration failed: {e}")


def _atomic_write_json(path, data):
    """Writes JSON atomically: .tmp first, then os.replace.

    Protects against half-written files if NVDA or the power dies mid-write.
    """
    tmp_path = path + ".tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=None)
    os.replace(tmp_path, path)


class _EntryStore:
    """A list of entries with its own file, quota and debounce.

    Two instances make up the history: one for events, one for readings. That
    way trimming only ever affects its own kind, and a switching action never
    rewrites the large readings file.
    """

    def __init__(self, path, max_entries, retention_seconds, label):
        self.path = path
        self.max_entries = max_entries
        self.retention_seconds = retention_seconds
        self.label = label
        self.entries = []
        self._dirty = False
        self._last_save_time = 0.0
        self._unsaved_count = 0

    def load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.entries = data
                else:
                    log.warning(f"{self.label}: unexpected file format, starting empty")
                    self.entries = []
                log.debug(f"{self.label} loaded: {len(self.entries)} entries")
            else:
                self.entries = []
        except Exception as e:
            log.error(f"{self.label} could not be loaded: {e}")
            self.entries = []

    def _trim(self):
        """Trims by age AND count. Returns the number of dropped entries."""
        before = len(self.entries)
        if self.retention_seconds:
            cutoff = time.time() - self.retention_seconds
            self.entries = [e for e in self.entries
                            if e.get('timestamp', 0) >= cutoff]
        if self.max_entries and len(self.entries) > self.max_entries:
            self.entries = self.entries[-self.max_entries:]
        return before - len(self.entries)

    def save(self):
        if not self._dirty:
            return
        try:
            self._trim()
            _atomic_write_json(self.path, self.entries)
            self._dirty = False
            self._last_save_time = time.time()
            self._unsaved_count = 0
            log.debug(f"{self.label} saved: {len(self.entries)} entries")
        except Exception as e:
            log.error(f"{self.label} could not be saved: {e}")

    def append(self, entry):
        self.entries.append(entry)
        self._dirty = True
        self._unsaved_count += 1
        now = time.time()
        if (self._unsaved_count >= SAVE_DEBOUNCE_MAX_ENTRIES
                or (now - self._last_save_time) >= SAVE_DEBOUNCE_SECONDS):
            self.save()

    def clear(self):
        self.entries = []
        self._dirty = True
        self.save()

    def mark_dirty(self):
        self._dirty = True


class DeviceHistory:
    """Manages events and readings.

    Event entry::

        {
            "timestamp": 1709287200.0,
            "device_uuid": "abc123",
            "device_name": "Steckdose Wohnzimmer",
            "platform": "meross" | "netatmo" | "vesync" | "cozytouch",
            "event_type": "action",
            "action": "toggle_on" | "set_temp" | ...,
            "details": "Ein" | "22.5°C" | ...,
            "source": "local" | "extern" | "system"
        }

    Reading entry (change points only)::

        {
            "timestamp": ..., "device_uuid": ..., "device_name": ...,
            "platform": ..., "event_type": "sensor",
            "sensor_data": {"temperature": 22.5, "humidity": 65}
        }
    """

    def __init__(self):
        # One lock for both stores. Needed since the history is also
        # written from the MQTT push thread (external changes) and from the
        # background threads of the favorites gestures - json.dump over a
        # list growing in parallel can otherwise break mid-write.
        self._lock = threading.RLock()
        self._events = _EntryStore(
            HISTORY_FILE, MAX_EVENT_ENTRIES, EVENT_RETENTION_SECONDS,
            "History (events)")
        self._measurements = _EntryStore(
            MEASUREMENTS_FILE, MAX_MEASUREMENT_ENTRIES,
            MEASUREMENT_RETENTION_SECONDS, "History (readings)")
        # State of the change filter: (uuid, quantity) -> (value,
        # timestamp) of the last STORED point. In memory only; after an NVDA
        # restart the first value per quantity is written again, which is
        # even desirable as a data point.
        self._last_written = {}

        # One-time migration of the old file from the add-on subfolder.
        _migrate_legacy_file()
        self._events.load()
        self._measurements.load()
        self._migrate_platform_fields()
        self._migrate_v1_layout()
        self._seed_last_written()

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------
    def _migrate_platform_fields(self):
        """Fixes misclassified legacy entries.

        Older versions did not know Cozytouch in _detect_platform and logged
        Cozytouch actions as "meross". Cozytouch UUIDs carry the prefix
        "cozytouch_", which makes those entries unambiguously repairable.
        """
        changed = 0
        for store in (self._events, self._measurements):
            for entry in store.entries:
                if (str(entry.get('device_uuid', '')).startswith('cozytouch_')
                        and entry.get('platform') != 'cozytouch'):
                    entry['platform'] = 'cozytouch'
                    store.mark_dirty()
                    changed += 1
        if changed:
            log.info(f"History: {changed} Cozytouch entries migrated to the correct platform")

    def migrate_device_keys(self, devices):
        """Rewrites entries that were stored under the plain device UUID.

        Until this version the history wrote ``device.uuid``. Every sensor on
        a Meross hub shares it, which is why _device_key now uses
        ``unique_id``. Switching the key alone would leave everything written
        so far under the OLD one - each sensor would then appear twice, once
        with the readings up to the change and once with those after it.

        The old key plus the device name identifies the entries exactly, so
        they can be repaired. Runs once as soon as the device list is known.
        """
        mapping = {}
        for device in devices:
            uuid = getattr(device, 'uuid', None)
            unique = getattr(device, 'unique_id', None)
            name = getattr(device, 'name', None)
            if uuid and name and unique and unique != uuid:
                mapping[(uuid, name)] = unique
        if not mapping:
            return 0

        changed = 0
        with self._lock:
            for store in (self._events, self._measurements):
                for entry in store.entries:
                    key = (entry.get('device_uuid'), entry.get('device_name'))
                    new_uuid = mapping.get(key)
                    if new_uuid:
                        entry['device_uuid'] = new_uuid
                        store.mark_dirty()
                        changed += 1
            if changed:
                self._events.save()
                self._measurements.save()
                # The change point filter is keyed by UUID - reseed it, or the
                # next reading would be compared against a stale key.
                self._seed_last_written()
        if changed:
            log.info(f"History: {changed} entries moved to the unique device ID")
        return changed

    def _migrate_v1_layout(self):
        """Splits an old history file into events and readings.

        Old versions put actions AND sensor values in the same list. On the
        first start of the new version they are separated once:

        * Actions are kept in full and get ``source: "local"`` - everything
          earlier versions logged was a user action in the dialog.
        * Sensor values run retroactively through the same change filter that
          applies to new values from now on. Only repetitions are dropped;
          every value representing a real change stays.

        Idempotent: the ``event_type`` values show whether anything is left
        to split.
        """
        sensor_entries = [e for e in self._events.entries
                          if e.get('event_type') == 'sensor']
        if not sensor_entries:
            # Nothing to split. Still fill in missing source fields.
            filled = 0
            for entry in self._events.entries:
                if 'source' not in entry:
                    entry['source'] = SOURCE_LOCAL
                    filled += 1
            if filled:
                self._events.mark_dirty()
                self._events.save()
            return

        action_entries = [e for e in self._events.entries
                          if e.get('event_type') != 'sensor']
        for entry in action_entries:
            entry.setdefault('source', SOURCE_LOCAL)

        # Run the sensor values through the change filter in order.
        sensor_entries.sort(key=lambda e: e.get('timestamp', 0))
        state = {}
        kept = []
        for entry in sensor_entries:
            ts = entry.get('timestamp', 0)
            data = entry.get('sensor_data') or {}
            changed = {}
            for quantity, value in data.items():
                if quantity not in MEASUREMENT_THRESHOLDS:
                    continue  # e.g. 'power' -> belongs in energy.py
                if self._is_change_point(state, entry.get('device_uuid', ''),
                                         quantity, value, ts):
                    changed[quantity] = value
            if changed:
                new_entry = dict(entry)
                new_entry['sensor_data'] = changed
                kept.append(new_entry)

        dropped = len(sensor_entries) - len(kept)
        self._events.entries = action_entries
        self._events.mark_dirty()
        self._measurements.entries = sorted(
            self._measurements.entries + kept,
            key=lambda e: e.get('timestamp', 0))
        self._measurements.mark_dirty()
        self._events.save()
        self._measurements.save()
        log.info(
            f"History converted: {len(action_entries)} events kept, "
            f"{len(sensor_entries)} sensor entries thinned to {len(kept)} "
            f"change points ({dropped} repetitions dropped)."
        )

    def _seed_last_written(self):
        """Seeds the filter state from the readings already stored.

        Without it the first value of every quantity would be written after
        each NVDA start, even when identical to the last stored one.
        """
        for entry in self._measurements.entries:
            uuid = entry.get('device_uuid', '')
            ts = entry.get('timestamp', 0)
            for quantity, value in (entry.get('sensor_data') or {}).items():
                key = (uuid, quantity)
                prev = self._last_written.get(key)
                if prev is None or ts >= prev[1]:
                    self._last_written[key] = (value, ts)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def flush(self):
        """Forces immediate saving of all unsaved entries.

        Called on NVDA shutdown (plugin.terminate) so no entries from the
        debounce window are lost.
        """
        with self._lock:
            self._events.save()
            self._measurements.save()

    @staticmethod
    def _detect_platform(device):
        """Maps a device to its platform
        (meross/netatmo/vesync/cozytouch) – central logic in platform_utils.
        """
        return platform_of(device)

    # ------------------------------------------------------------------
    # Recording: events
    # ------------------------------------------------------------------
    def log_action(self, device, action, details="", source=SOURCE_LOCAL):
        """Logs an event.

        Args:
            device: device wrapper (Meross/Netatmo/VeSync/Cozytouch)
            action: action key, e.g. 'toggle_on', 'set_temp', 'set_mode'
            details: description, e.g. '22.5 °C'
            source: SOURCE_LOCAL (user), SOURCE_EXTERN (app/assistant/
                device) or SOURCE_SYSTEM (automatic)
        """
        try:
            entry = {
                "timestamp": time.time(),
                "device_uuid": _device_key(device),
                "device_name": device.name,
                "platform": self._detect_platform(device),
                "event_type": "action",
                "action": action,
                "details": details,
                "source": source,
            }
        except Exception as e:
            log.debug(f"History: could not build the event: {e}")
            return
        with self._lock:
            self._events.append(entry)

    def log_external_action(self, device_uuid, device_name, platform, action,
                            details=""):
        """Logs an external switch without a device object.

        The push path (``_on_external_device_change``) only knows UUID, name
        and state - the device object may already have been replaced by then.
        Hence its own entry point instead of a detour via ``log_action``.
        """
        entry = {
            "timestamp": time.time(),
            "device_uuid": device_uuid,
            "device_name": device_name,
            "platform": platform,
            "event_type": "action",
            "action": action,
            "details": details,
            "source": SOURCE_EXTERN,
        }
        with self._lock:
            self._events.append(entry)

    # ------------------------------------------------------------------
    # Recording: readings
    # ------------------------------------------------------------------
    @staticmethod
    def _is_change_point(state, device_uuid, quantity, value, now):
        """Decides whether a value is stored as a change point.

        It is stored if one of three cases applies:

        1. There is no stored value for this quantity yet.
        2. The value differs by more than the threshold
           (``MEASUREMENT_THRESHOLDS``).
        3. ``MEASUREMENT_MAX_SILENCE`` has passed since the last data point,
           so a flat line does not look like a gap.

        Updates ``state`` directly on a hit.
        """
        threshold = MEASUREMENT_THRESHOLDS.get(quantity)
        if threshold is None:
            return False  # unknown quantity (e.g. 'power') is not kept
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return False

        key = (device_uuid, quantity)
        previous = state.get(key)
        if previous is None:
            state[key] = (numeric, now)
            return True
        prev_value, prev_ts = previous
        try:
            drift = abs(numeric - float(prev_value))
        except (TypeError, ValueError):
            drift = threshold + 1  # not comparable -> count as a change
        if drift > threshold or (now - prev_ts) >= MEASUREMENT_MAX_SILENCE:
            state[key] = (numeric, now)
            return True
        return False

    def log_sensor(self, device, sensor_data):
        """Records readings - but only those that changed.

        Args:
            device: device object
            sensor_data: dict, e.g. {'temperature': 22.5, 'humidity': 65}

        Returns:
            True if a change point was written
        """
        if not sensor_data:
            return False
        try:
            uuid = _device_key(device)
            name = device.name
            platform = self._detect_platform(device)
        except Exception as e:
            log.debug(f"History: reading without device data dropped: {e}")
            return False

        now = time.time()
        with self._lock:
            changed = {}
            for quantity, value in sensor_data.items():
                if self._is_change_point(self._last_written, uuid, quantity,
                                         value, now):
                    changed[quantity] = value
            if not changed:
                return False
            self._measurements.append({
                "timestamp": now,
                "device_uuid": uuid,
                "device_name": name,
                "platform": platform,
                "event_type": "sensor",
                "action": "",
                "details": "",
                "sensor_data": changed,
            })
        return True

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def _select(self, entries, device_uuid=None, platform=None,
                since_hours=None):
        result = entries
        if device_uuid:
            result = [e for e in result if e.get('device_uuid') == device_uuid]
        if platform:
            result = [e for e in result if e.get('platform') == platform]
        if since_hours:
            cutoff = time.time() - (since_hours * 3600)
            result = [e for e in result if e.get('timestamp', 0) >= cutoff]
        return result

    def get_entries(self, device_uuid=None, event_type=None, max_entries=200,
                    since_hours=None, platform=None):
        """Returns filtered history entries (newest first).

        Args:
            device_uuid: optional – only entries for this device
            event_type: 'action' (events), 'sensor' (readings) or None
                (both together)
            max_entries: max. number of returned entries
            since_hours: optional – only entries of the last N hours
            platform: optional – 'meross', 'netatmo', 'vesync' or 'cozytouch'

        Returns:
            list of entries (newest first)
        """
        with self._lock:
            if event_type == 'action':
                pool = list(self._events.entries)
            elif event_type == 'sensor':
                pool = list(self._measurements.entries)
            else:
                pool = self._events.entries + self._measurements.entries
        result = self._select(pool, device_uuid, platform, since_hours)
        result.sort(key=lambda e: e.get('timestamp', 0), reverse=True)
        return result[:max_entries]

    def summarize_measurements(self, device_uuid=None, platform=None,
                               since_hours=None):
        """Condenses readings to min/max/average per device and quantity.

        That is the actual purpose of the readings: one row answers what
        would otherwise mean going through a hundred.

        The average is time-weighted (trapezoid over the data points), not
        the arithmetic mean of the points - otherwise a phase with many
        changes would skew it against a long quiet one. With a single point
        the average is that value.

        Returns:
            list of dicts, sorted by device name and quantity order::

                {'device_uuid':…, 'device_name':…, 'platform':…,
                 'quantity': 'temperature', 'min':…, 'max':…, 'avg':…,
                 'last':…, 'count':…}
        """
        with self._lock:
            pool = list(self._measurements.entries)
        entries = self._select(pool, device_uuid, platform, since_hours)
        entries.sort(key=lambda e: e.get('timestamp', 0))

        # (uuid, quantity) -> {'name':…, 'platform':…, 'points': [(ts, value)]}
        series = {}
        for entry in entries:
            uuid = entry.get('device_uuid', '')
            ts = entry.get('timestamp', 0)
            for quantity, value in (entry.get('sensor_data') or {}).items():
                if quantity not in MEASUREMENT_THRESHOLDS:
                    continue
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    continue
                # Grouped by name as WELL as UUID: entries written before
                # the fix all carry the hub UUID, so two sensors of one hub
                # would still be merged into one row. Splitting by name shows
                # them separately again. A renamed device produces two rows
                # for the period around the rename - the lesser evil against
                # a summary that mixes two rooms.
                rec = series.setdefault((uuid, entry.get('device_name', ''), quantity), {
                    'device_uuid': uuid,
                    'device_name': entry.get('device_name', ''),
                    'platform': entry.get('platform', ''),
                    'quantity': quantity,
                    'points': [],
                })
                rec['device_name'] = entry.get('device_name', rec['device_name'])
                rec['points'].append((ts, numeric))

        out = []
        for rec in series.values():
            points = rec.pop('points')
            values = [v for _ts, v in points]
            rec['min'] = min(values)
            rec['max'] = max(values)
            rec['last'] = values[-1]
            rec['count'] = len(values)
            rec['avg'] = _time_weighted_average(points)
            # Period of the series: without it a row of numbers says nothing
            # about WHEN it was measured - especially with the "all time"
            # filter, where the range is not obvious from the filter either.
            rec['first_ts'] = points[0][0]
            rec['last_ts'] = points[-1][0]
            out.append(rec)

        order = {q: i for i, q in enumerate(MEASUREMENT_ORDER)}
        out.sort(key=lambda r: (r['device_name'].lower(),
                                order.get(r['quantity'], 99)))
        return out

    def get_unique_devices(self):
        """Returns a list of all devices that appear in the history.

        Returns:
            list of dicts: [{'uuid': ..., 'name': ..., 'platform': ...}, ...]
        """
        with self._lock:
            pool = self._events.entries + self._measurements.entries
        seen = {}
        for entry in sorted(pool, key=lambda e: e.get('timestamp', 0),
                            reverse=True):
            uuid = entry.get('device_uuid', '')
            if uuid and uuid not in seen:
                seen[uuid] = {
                    'uuid': uuid,
                    # Translators: Placeholder for an unknown device name.
                    'name': entry.get('device_name', _("Unknown")),
                    'platform': entry.get('platform', ''),
                }
        return sorted(seen.values(), key=lambda d: d['name'].lower())

    def clear(self):
        """Deletes events and readings."""
        with self._lock:
            self._events.clear()
            self._measurements.clear()
            self._last_written = {}

    def get_entry_count(self):
        """Total number of entries (events + readings)."""
        with self._lock:
            return len(self._events.entries) + len(self._measurements.entries)

    def get_event_count(self):
        with self._lock:
            return len(self._events.entries)

    def get_measurement_count(self):
        with self._lock:
            return len(self._measurements.entries)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    @staticmethod
    def _csv_safe(value):
        """Defuses spreadsheet formula injection in an exported cell.

        Device names come from the manufacturer cloud (the user types them in
        the Meross/Netatmo/VeSync app), so they are external input. Excel and
        LibreOffice interpret any cell starting with = + - @ or a leading
        tab/CR as a FORMULA, not as text - a device named
        ``=HYPERLINK("http://evil","click")`` would become an active link on
        opening the export, and DDE-style payloads are possible too (CWE-1236).

        Prefixing with an apostrophe is the standard mitigation: spreadsheets
        show the plain text and never evaluate it.
        """
        if value is None:
            return ''
        text = str(value)
        if text and text[0] in ('=', '+', '-', '@', '\t', '\r'):
            return "'" + text
        return text

    def export_csv(self, filepath=None, device_uuid=None, since_hours=None,
                   platform=None, event_type=None):
        """Exports the history as a CSV file.

        Args:
            filepath: target path. If None, a string is returned.
            device_uuid: optional – only entries for this device
            since_hours: optional – only entries of the last N hours
            platform: optional – 'meross', 'netatmo', 'vesync' or 'cozytouch'
            event_type: optional – 'action' or 'sensor'

        Returns:
            file path on success, or a CSV string if filepath=None
        """
        entries = self.get_entries(
            device_uuid=device_uuid,
            max_entries=MAX_EVENT_ENTRIES + MAX_MEASUREMENT_ENTRIES,
            since_hours=since_hours,
            platform=platform,
            event_type=event_type,
        )

        # Newest first -> reverse to chronological order for CSV
        entries.reverse()

        output = io.StringIO() if filepath is None else None
        f = None

        try:
            if filepath:
                f = open(filepath, 'w', newline='', encoding='utf-8-sig')
            else:
                f = output

            # Both separators follow the locale, because that is what Excel
            # expects: where the decimal separator is a comma, the list
            # separator is a semicolon (and vice versa). A hard-coded ";"
            # put every line of an English Excel into a single cell.
            decimal_point = locale.localeconv().get('decimal_point') or '.'
            delimiter = ';' if decimal_point == ',' else ','
            writer = csv.writer(f, delimiter=delimiter,
                                quoting=csv.QUOTE_MINIMAL)

            # The columns come from the DATA, not from a fixed list. An
            # export of the events used to carry seven empty measurement
            # columns and a "Type" column reading "action" in every row -
            # eight cells per row to skip past for nothing. Now a file only
            # contains what it can fill.
            has_actions = any(e.get('event_type') != 'sensor' for e in entries)
            has_sensors = any(e.get('event_type') == 'sensor' for e in entries)
            present = {q for e in entries for q in (e.get('sensor_data') or {})}
            quantities = [q for q in MEASUREMENT_ORDER if q in present]

            # The readable columns follow the interface language - the file
            # is opened in a spreadsheet and read there. Next to them stand
            # two columns that do NOT change with the language: the ISO
            # timestamp and the action key.
            # Translators: Column headers of the CSV export (history).
            header = [_("Time"), _("Device name"), _("Platform")]
            # Only meaningful when both kinds are in the same file.
            if has_actions and has_sensors:
                # Translators: Column heading in the CSV export of the history.
                header.append(_("Type"))
            if has_actions:
                header += [
                    # Translators: Column heading in the CSV export of the
                    # history.
                    _("Source"), _("Action"),
                    # Translators: CSV column with the untranslated action
                    # key (e.g. "toggle_off") for further processing.
                    _("Action key"),
                    # Translators: Column heading in the CSV export of the
                    # history.
                    _("Details"),
                ]
            header += [_csv_quantity_header(q) for q in quantities]
            writer.writerow(header)

            for entry in entries:
                ts = entry.get('timestamp', 0)
                dt = datetime.fromtimestamp(ts) if ts else None
                # ISO 8601 WITH the T: unambiguous in every language and
                # sorting correctly as text. The T is not cosmetic - with a
                # space Excel recognises a date, formats the cell to the
                # region's pattern and then shows "##########" because the
                # column is too narrow. The screen reader reads exactly
                # those hash marks. With the T the value stays text and
                # remains readable (measured with Excel 16).
                time_str = dt.strftime('%Y-%m-%dT%H:%M:%S') if dt else ''
                action = entry.get('action', '')
                details = entry.get('details', '')

                sensor = entry.get('sensor_data') or {}

                # Free-text columns are defused (see _csv_safe); the numeric
                # sensor columns cannot carry a formula and stay untouched so
                # they remain usable as numbers in the spreadsheet.
                row = [
                    time_str,
                    self._csv_safe(entry.get('device_name', '')),
                    self._csv_safe(entry.get('platform', '')),
                ]
                if has_actions and has_sensors:
                    row.append(self._csv_safe(entry.get('event_type', '')))
                if has_actions:
                    row += [
                        self._csv_safe(_source_text(entry.get('source', ''))),
                        # Same text as in the dialog - this also repairs
                        # details that older versions stored translated.
                        self._csv_safe(_format_action_text(action)),
                        self._csv_safe(action),
                        self._csv_safe('' if _detail_is_redundant(action)
                                       else _detail_text(action, details)),
                    ]
                row += [_csv_number(sensor.get(q, ''), decimal_point)
                        for q in quantities]
                writer.writerow(row)

            if filepath:
                f.close()
                return filepath
            else:
                return output.getvalue()

        except Exception as e:
            log.error(f"CSV export failed: {e}")
            raise
        finally:
            # Only close real files (StringIO is read by the caller).
            if filepath and f is not None:
                try:
                    f.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------
    def format_entry_for_display(self, entry):
        """Formats an entry for screen reader display.

        Returns:
            readable string for NVDA
        """
        ts = entry.get('timestamp', 0)
        dt = datetime.fromtimestamp(ts) if ts else None
        time_rel = relative_time(ts)

        # Translators: Placeholder for an unknown time/device name in the
        # history.
        time_abs = local_datetime(dt) if dt else _("Unknown")
        device_name = entry.get('device_name', _("Unknown"))
        platform = entry.get('platform', '')
        event_type = entry.get('event_type', '')

        if event_type == 'action':
            action_text = _format_action_text(entry.get('action', ''),
                                              entry.get('details', ''))
            source = _source_text(entry.get('source', ''))
            if source:
                action_text = f"{action_text} ({source})"
            return f"{time_abs} ({time_rel}) – {device_name} [{platform}]: {action_text}"

        elif event_type == 'sensor':
            sensor_text = format_sensor_values(entry.get('sensor_data') or {})
            return f"{time_abs} ({time_rel}) – {device_name} [{platform}]: {sensor_text}"

        # Translators: Fallback for a history entry of unknown type.
        return _("{time} – {name}: unknown event").format(
            time=time_abs, name=device_name)


def _time_weighted_average(points):
    """Time-weighted average over (timestamp, value) points.

    Trapezoid rule over the data points divided by the total duration. With a
    single point or a total duration of 0 that value is returned.
    """
    if not points:
        return None
    if len(points) == 1:
        return points[0][1]
    area = 0.0
    duration = 0.0
    for (t1, v1), (t2, v2) in zip(points, points[1:]):
        dt = t2 - t1
        if dt <= 0:
            continue
        area += (v1 + v2) / 2.0 * dt
        duration += dt
    if duration <= 0:
        return sum(v for _t, v in points) / len(points)
    return area / duration


def relative_time(ts):
    """Formats a timestamp as ``5 minutes ago`` or similar."""
    if not ts:
        # Translators: Placeholder for an unknown time in the history.
        return _("Unknown")
    diff = time.time() - ts
    if diff < 60:
        # Translators: Relative time in the history (less than 1 minute ago).
        return _("just now")
    if diff < 3600:
        mins = int(diff / 60)
        # Translators: Relative time in the history (minutes).
        return (_("{count} minute ago") if mins == 1
                else _("{count} minutes ago")).format(count=mins)
    if diff < 86400:
        hours = int(diff / 3600)
        # Translators: Relative time in the history (hours).
        return (_("{count} hour ago") if hours == 1
                else _("{count} hours ago")).format(count=hours)
    days = int(diff / 86400)
    # Translators: Relative time in the history (days).
    return (_("{count} day ago") if days == 1
            else _("{count} days ago")).format(count=days)


def format_sensor_values(sensor):
    """Formats a sensor_data dict as a readable enumeration."""
    parts = []
    for quantity in MEASUREMENT_ORDER:
        if quantity in sensor:
            parts.append(format_measurement(quantity, sensor[quantity]))
    # Append unknown quantities (e.g. 'power' from old files) at the end.
    for quantity, value in sensor.items():
        if quantity not in MEASUREMENT_ORDER:
            parts.append(f"{value} {MEASUREMENT_UNITS.get(quantity, '')}".strip())
    # Translators: History entry without sensor values.
    return ", ".join(p for p in parts if p) if parts else _("No data")


def _source_text(source):
    """Display text for the origin of an event."""
    if source == SOURCE_EXTERN:
        # Translators: Origin of a history event - the device was switched
        # outside of NVDA (manufacturer app, voice assistant, button on the
        # device). Deliberately not more specific: which of these it was
        # cannot be determined from the cloud notification.
        return _("external")
    if source == SOURCE_SYSTEM:
        # Translators: Origin of a history event - triggered automatically.
        return _("automatic")
    if source == SOURCE_LOCAL:
        # Translators: Origin of a history event - the user switched the
        # device themselves through this add-on. Addressed as "you": the
        # speaker of the text is the add-on, so "me" would read as if the
        # add-on had done it.
        return _("you")
    return ""


def _format_action_text(action, details=""):
    """Formats an action text for display"""
    # Translators: The following texts describe actions in the device history.
    action_map = {
        'toggle_on': _("Switched on"),
        'toggle_off': _("Switched off"),
        'set_temp': _("Temperature set"),
        'set_mode': _("Mode changed"),
        'back_to_schedule': _("Back to schedule"),
        'diffuser_light': _("Light spray"),
        'diffuser_strong': _("Strong spray"),
        'diffuser_off': _("Spray off"),
        # Netatmo-specific actions (keys as logged in dialog_netatmo.py)
        'therm_mode': _("Mode changed"),
        'switch_schedule': _("Heating schedule switched"),
        # Meross light actions (keys as logged in
        # dialog_meross.py/device_dialog.py)
        'light_luminance': _("Brightness changed"),
        'light_temperature': _("Light color changed"),
        'light_rgb': _("Color changed"),
        # Cozytouch-specific actions (keys as logged in dialog_cozytouch.py)
        'set_target_temp': _("Target temperature set"),
        'boost_on': _("Boost switched on"),
        'boost_off': _("Boost switched off"),
        'away_on': _("Away mode switched on"),
        'away_off': _("Away mode switched off"),
        # VeSync-specific actions
        'set_fan_speed': _("Fan speed changed"),
        'set_auto_preference': _("Auto profile changed"),
        'set_nightlight': _("Night light changed"),
        'oscillation_on': _("Oscillation switched on"),
        'oscillation_off': _("Oscillation switched off"),
        'mute_on': _("Mute switched on"),
        'mute_off': _("Mute switched off"),
        'display_on': _("Display switched on"),
        'display_off': _("Display switched off"),
        'child_lock_on': _("Child lock switched on"),
        'child_lock_off': _("Child lock switched off"),
        'reset_filter': _("Filter reset"),
        # Translators: History entry: a cooking programme was stopped from
        # the add-on.
        'end_cook': _("Cooking programme stopped"),
        # Translators: History entry: a cooking programme was started
        # from the add-on.
        'start_cook': _("Cooking programme started"),
        # Translators: History entry: time or temperature of a running
        # cooking programme was changed.
        'adjust_cook': _("Cooking programme adjusted"),
        # Water sensors (MS400/MS405)
        'water_detected': _("Water alarm"),
        'water_cleared': _("No water detected any more"),
    }
    text = action_map.get(action, action)
    if details and not _detail_is_redundant(action):
        text = f"{text}: {_detail_text(action, details)}"
    return text


def _detail_is_redundant(action):
    """True when the action already says everything the detail would.

    "Switched off: Off" carries nothing beyond "Switched off" - and the
    detail was the one part stored as READY-TRANSLATED text, so old entries
    kept the language they were written in ("Switched off: Aus" in an English
    interface). Dropping it fixes the wording and the language leak at once,
    for entries already on disk as well.
    """
    return action.endswith(('_on', '_off'))


def _detail_text(action, details):
    """Renders a detail in the CURRENT language where that is possible.

    Details are stored language-neutrally now: a fan level as the bare
    number, a mode as its key. Older entries hold ready-translated text -
    that is passed through unchanged, because nothing can recover its
    meaning after the fact.
    """
    from .constants import (
        VESYNC_PURIFIER_MODE_NAMES, VESYNC_FAN_MODE_NAMES,
        VESYNC_NIGHTLIGHT_MODE_NAMES, VESYNC_AUTO_PREFERENCE_NAMES,
        NETATMO_MODE_NAMES, DIFFUSER_MODE_NAMES, MEROSS_WHITE_PRESET_NAMES,
        MEROSS_WHITE_PRESET_LEGACY,
    )
    from .cozytouch_devices import COZYTOUCH_HEATING_MODE_NAMES
    value = str(details)
    if action == 'set_fan_speed':
        # Bare number since the changeover; older entries hold the rendered
        # label ("Level 1" / "Stufe 1") - the digit in it is enough to show
        # them in the current language too.
        digits = ''.join(c for c in value if c.isdigit())
        if digits:
            # Translators: Fan level in the history. {level} = level number.
            return _("Level {level}").format(level=digits)
    if action == 'set_boost_duration':
        digits = ''.join(c for c in value if c.isdigit())
        if digits:
            # Translators: Boost duration in the history. {minutes} = minutes.
            return _("{minutes} minutes").format(minutes=digits)
    if action in ('set_mode', 'therm_mode', 'set_nightlight',
                  'set_auto_preference', 'light_temperature'):
        for table in (VESYNC_PURIFIER_MODE_NAMES, VESYNC_FAN_MODE_NAMES,
                      VESYNC_NIGHTLIGHT_MODE_NAMES, VESYNC_AUTO_PREFERENCE_NAMES,
                      NETATMO_MODE_NAMES, DIFFUSER_MODE_NAMES,
                      COZYTOUCH_HEATING_MODE_NAMES, MEROSS_WHITE_PRESET_NAMES):
            if value in table:
                return table[value]
        # White tones stored under their German key up to 26.7.3.
        legacy = MEROSS_WHITE_PRESET_LEGACY.get(value)
        if legacy in MEROSS_WHITE_PRESET_NAMES:
            return MEROSS_WHITE_PRESET_NAMES[legacy]
    return value


# Singleton instance
_instance = None

def get_history():
    """Returns the global history instance (singleton)"""
    global _instance
    if _instance is None:
        _instance = DeviceHistory()
    return _instance


def flush_pending():
    """Saves unsaved entries WITHOUT lazily creating an instance.

    Safe to call on NVDA shutdown: if no history was used in this
    session, nothing happens (especially no pointless file load).
    """
    if _instance is not None:
        _instance.flush()
