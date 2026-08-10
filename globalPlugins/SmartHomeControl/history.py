# -*- coding: utf-8 -*-
"""
Smart Home Control - Verlauf (Ereignisse + Messwerte)

Der Verlauf trennt bewusst zwei Dinge, die frueher in einem Topf lagen:

* **Ereignisse** sind selten, einzeln wichtig und sollen lange aufbewahrt
  werden - man will sie *vollstaendig*. Dazu gehoert die Herkunft: eine
  Schaltung ist entweder ``local`` (der Nutzer, egal ob ueber Dialog oder
  Favoriten-Geste), ``extern`` (Hersteller-App, Sprachassistent, Taster am
  Geraet) oder ``system`` (automatisch).
* **Messwerte** sind haeufig, einzeln bedeutungslos und nur als Verlauf
  interessant - man will sie *verdichtet*. Sie werden deshalb nur als
  Aenderungspunkte gespeichert: ein Wert landet nur dann in der Datei, wenn er
  sich gegenueber dem zuletzt gespeicherten um mehr als eine Schwelle
  unterscheidet, oder wenn seit dem letzten Stuetzpunkt eine Stunde vergangen
  ist.

Frueher lagen beide in einem Ring von 5000 Eintraegen. Da Messwerte um
Groessenordnungen haeufiger anfallen, verdraengten sie genau das, was man
spaeter sucht: die Schaltvorgaenge. ``energy.py`` hat dieses Problem fuer die
Leistungs-Stichproben schon geloest (eigene Ablage); hier folgt derselbe
Schritt fuer Temperatur, Feuchte, CO2 und Luftdruck.

Leistung (Watt) wird hier bewusst NICHT gefuehrt - dafuer ist ``energy.py``
zustaendig, das daraus auch Energiemengen integriert.

Persistente Ablage als JSON im NVDA-addons-Ordner (auf derselben Ebene wie
z.B. ``clock.json``). Dieser Pfad ueberlebt Add-on-Updates - beim Update
ersetzt NVDA nur den Add-on-Unterordner, nicht dessen Nachbardateien.
"""

import os
import json
import time
import csv
import io
import threading
from datetime import datetime

from .platform_utils import platform_of

# NVDA-Logger verwenden (siehe favorites.py): Meldungen dieses Moduls sollen
# im NVDA-Log erscheinen. Fallback für Nutzung außerhalb von NVDA.
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

# Ereignisse behalten den bisherigen Dateinamen - dadurch bleibt die Datei
# beim Update dieselbe und die Migration kann an Ort und Stelle arbeiten.
HISTORY_FILE = os.path.join(_ADDONS_DIR, "SmartHomeControl_history.json")
# Messwerte bekommen eine eigene Ablage (wie SmartHomeControl_energy.json).
# Vorteil: ein Schaltvorgang schreibt nie die - deutlich groessere -
# Messwertdatei mit.
MEASUREMENTS_FILE = os.path.join(_ADDONS_DIR, "SmartHomeControl_measurements.json")
# Legacy path (old versions stored inside the add-on folder).
_LEGACY_HISTORY_FILE = os.path.join(_ADDON_DIR, "device_history.json")

# ---------------------------------------------------------------------------
# Aufbewahrung: getrennte Kontingente, getrennt gekuerzt. Ein Ereignis darf
# NIE von einem Messwert verdraengt werden - das war der Kernfehler der alten
# Fassung mit ihrem gemeinsamen Ring von 5000 Eintraegen.
# ---------------------------------------------------------------------------
MAX_EVENT_ENTRIES = 2000
EVENT_RETENTION_SECONDS = 365 * 86400        # 1 Jahr
MAX_MEASUREMENT_ENTRIES = 20000
MEASUREMENT_RETENTION_SECONDS = 90 * 86400   # 90 Tage

# Rueckwaertskompatibilitaet: alter Name, den evtl. anderer Code liest.
MAX_HISTORY_ENTRIES = MAX_EVENT_ENTRIES

# Debounce für das Speichern: Jeder Eintrag schrieb früher sofort die GESAMTE
# Datei neu. Jetzt wird gesammelt und erst gespeichert, wenn genügend Zeit
# vergangen ist ODER sich genug Einträge angesammelt haben.
# flush_pending() (vom Plugin-terminate aufgerufen) sichert den Rest.
SAVE_DEBOUNCE_SECONDS = 30
SAVE_DEBOUNCE_MAX_ENTRIES = 20

# ---------------------------------------------------------------------------
# Herkunft eines Ereignisses
# ---------------------------------------------------------------------------
SOURCE_LOCAL = 'local'     # der Nutzer selbst (Dialog oder Favoriten-Geste)
SOURCE_EXTERN = 'extern'   # Hersteller-App, Sprachassistent, Taster am Gerät
SOURCE_SYSTEM = 'system'   # automatisch (Zeitplan, Regel)

# ---------------------------------------------------------------------------
# Messwerte: Aenderungsschwellen
# ---------------------------------------------------------------------------
# Ein Wert wird nur gespeichert, wenn er sich gegenueber dem zuletzt
# GESPEICHERTEN Wert um mehr als diese Schwelle unterscheidet. Die Schwellen
# liegen bewusst knapp ueber dem Messrauschen der jeweiligen Sensoren: kleiner
# waere wieder Rauschen, groesser wuerde echte Verlaeufe abschneiden.
MEASUREMENT_THRESHOLDS = {
    'temperature': 0.3,   # K
    'humidity': 2.0,      # %
    'co2': 50.0,          # ppm
    'pressure': 1.0,      # mbar
}
# Auch ohne Aenderung spaetestens nach dieser Zeit ein Stuetzpunkt, damit eine
# flache Linie nicht als Datenluecke erscheint und Min/Max/Mittelwert ueber
# lange ruhige Phasen stimmen.
MEASUREMENT_MAX_SILENCE = 3600.0

# Reihenfolge und Beschriftung der Messgroessen in Anzeige und CSV.
MEASUREMENT_ORDER = ('temperature', 'humidity', 'co2', 'pressure')


def _measurement_labels():
    """Anzeigenamen der Messgrößen (zur Laufzeit, damit übersetzbar)."""
    return {
        # Translators: Names of the measured quantities in the history.
        'temperature': _("Temperatur"),
        'humidity': _("Luftfeuchte"),
        'co2': _("CO₂"),
        'pressure': _("Luftdruck"),
    }


MEASUREMENT_UNITS = {
    'temperature': '°C',
    'humidity': '%',
    'co2': 'ppm',
    'pressure': 'mbar',
}

# Nachkommastellen je Messgröße für die Anzeige.
_MEASUREMENT_DIGITS = {
    'temperature': 1,
    'humidity': 0,
    'co2': 0,
    'pressure': 1,
}


def format_measurement(quantity, value):
    """Formatiert einen Messwert mit Einheit, z.B. ``21,4 °C``."""
    if value is None:
        return ''
    digits = _MEASUREMENT_DIGITS.get(quantity, 1)
    try:
        text = f"{float(value):.{digits}f}".replace('.', ',')
    except (TypeError, ValueError):
        return str(value)
    unit = MEASUREMENT_UNITS.get(quantity, '')
    return f"{text} {unit}".strip()


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
                log.info(f"History: Legacy-Datei nach Migration archiviert: {backup}")
            except Exception as e:
                log.debug(f"Ignorierter Fehler in _migrate_legacy_file: {e}")
            return
        os.replace(_LEGACY_HISTORY_FILE, HISTORY_FILE)
        log.info(f"History von {_LEGACY_HISTORY_FILE} nach {HISTORY_FILE} migriert")
    except Exception as e:
        log.warning(f"History-Migration fehlgeschlagen: {e}")


def _atomic_write_json(path, data):
    """Schreibt JSON atomar: erst .tmp, dann os.replace.

    Schützt gegen halb geschriebene Dateien bei Absturz/Stromausfall mitten
    im Schreiben.
    """
    tmp_path = path + ".tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=None)
    os.replace(tmp_path, path)


class _EntryStore:
    """Eine Liste von Einträgen mit eigener Datei, eigenem Kontingent, Debounce.

    Zwei Instanzen davon bilden den Verlauf: eine für Ereignisse, eine für
    Messwerte. Dadurch trifft das Kürzen jeweils nur die eigene Art, und ein
    Schaltvorgang schreibt nie die große Messwertdatei mit.
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
                    log.warning(f"{self.label}: unerwartetes Dateiformat, starte leer")
                    self.entries = []
                log.debug(f"{self.label} geladen: {len(self.entries)} Einträge")
            else:
                self.entries = []
        except Exception as e:
            log.error(f"{self.label} konnte nicht geladen werden: {e}")
            self.entries = []

    def _trim(self):
        """Kürzt nach Alter UND Anzahl. Gibt die Zahl der Verworfenen zurück."""
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
            log.debug(f"{self.label} gespeichert: {len(self.entries)} Einträge")
        except Exception as e:
            log.error(f"{self.label} konnte nicht gespeichert werden: {e}")

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
    """Verwaltet Ereignisse und Messwerte.

    Ereignis-Eintrag::

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

    Messwert-Eintrag (nur Änderungspunkte)::

        {
            "timestamp": ..., "device_uuid": ..., "device_name": ...,
            "platform": ..., "event_type": "sensor",
            "sensor_data": {"temperature": 22.5, "humidity": 65}
        }
    """

    def __init__(self):
        # Ein Lock für beide Speicher. Nötig, seit der Verlauf auch aus dem
        # MQTT-Push-Thread (externe Änderungen) und aus den Hintergrund-
        # Threads der Favoriten-Gesten beschrieben wird - json.dump über eine
        # Liste, die parallel wächst, kann sonst mitten im Schreiben brechen.
        self._lock = threading.RLock()
        self._events = _EntryStore(
            HISTORY_FILE, MAX_EVENT_ENTRIES, EVENT_RETENTION_SECONDS,
            "Verlauf (Ereignisse)")
        self._measurements = _EntryStore(
            MEASUREMENTS_FILE, MAX_MEASUREMENT_ENTRIES,
            MEASUREMENT_RETENTION_SECONDS, "Verlauf (Messwerte)")
        # Zustand des Änderungsfilters: (uuid, größe) -> (wert, zeitstempel)
        # des zuletzt GESPEICHERTEN Punktes. Nur im Speicher; nach einem
        # NVDA-Neustart wird der erste Wert je Größe wieder geschrieben, was
        # als Stützpunkt sogar erwünscht ist.
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
            log.info(f"Verlauf: {changed} Cozytouch-Einträge auf korrekte Plattform migriert")

    def _migrate_v1_layout(self):
        """Trennt eine alte Verlaufsdatei in Ereignisse und Messwerte.

        Alte Fassungen legten Aktionen UND Sensorwerte in dieselbe Liste.
        Beim ersten Start der neuen Fassung wird einmalig getrennt:

        * Aktionen bleiben vollständig erhalten und bekommen ``source:
          "local"`` - alles, was frühere Fassungen protokolliert haben, war
          eine Aktion des Nutzers im Dialog.
        * Sensorwerte laufen rückwirkend durch denselben Änderungsfilter, der
          ab jetzt auch für neue Werte gilt. Verworfen werden ausschließlich
          Wiederholungen; jeder Wert, der eine echte Änderung darstellt,
          bleibt.

        Idempotent: erkennt an den ``event_type``-Werten, ob noch etwas zu
        trennen ist.
        """
        sensor_entries = [e for e in self._events.entries
                          if e.get('event_type') == 'sensor']
        if not sensor_entries:
            # Nichts zu trennen. Fehlende source-Felder trotzdem nachziehen.
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

        # Sensorwerte chronologisch durch den Änderungsfilter schicken.
        sensor_entries.sort(key=lambda e: e.get('timestamp', 0))
        state = {}
        kept = []
        for entry in sensor_entries:
            ts = entry.get('timestamp', 0)
            data = entry.get('sensor_data') or {}
            changed = {}
            for quantity, value in data.items():
                if quantity not in MEASUREMENT_THRESHOLDS:
                    continue  # z.B. 'power' -> gehört in energy.py
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
            f"Verlauf umgestellt: {len(action_entries)} Ereignisse behalten, "
            f"{len(sensor_entries)} Sensor-Einträge ausgedünnt auf {len(kept)} "
            f"Änderungspunkte ({dropped} Wiederholungen verworfen)."
        )

    def _seed_last_written(self):
        """Füllt den Filterzustand aus den bereits gespeicherten Messwerten.

        Ohne das würde nach jedem NVDA-Start der erste Wert jeder Größe
        geschrieben, auch wenn er identisch zum letzten gespeicherten ist.
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
    # Persistenz
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
    # Erfassung: Ereignisse
    # ------------------------------------------------------------------
    def log_action(self, device, action, details="", source=SOURCE_LOCAL):
        """Protokolliert ein Ereignis.

        Args:
            device: Geräte-Wrapper (Meross/Netatmo/VeSync/Cozytouch)
            action: Aktionsschlüssel, z.B. 'toggle_on', 'set_temp', 'set_mode'
            details: Beschreibung, z.B. '22,5 °C'
            source: SOURCE_LOCAL (Nutzer), SOURCE_EXTERN (App/Assistent/
                Gerät) oder SOURCE_SYSTEM (automatisch)
        """
        try:
            entry = {
                "timestamp": time.time(),
                "device_uuid": device.uuid,
                "device_name": device.name,
                "platform": self._detect_platform(device),
                "event_type": "action",
                "action": action,
                "details": details,
                "source": source,
            }
        except Exception as e:
            log.debug(f"Verlauf: Ereignis konnte nicht gebildet werden: {e}")
            return
        with self._lock:
            self._events.append(entry)

    def log_external_action(self, device_uuid, device_name, platform, action,
                            details=""):
        """Protokolliert eine externe Schaltung ohne Geräte-Objekt.

        Der Push-Pfad (``_on_external_device_change``) kennt nur UUID, Name
        und Zustand - das Geräteobjekt kann zu dem Zeitpunkt bereits ersetzt
        worden sein. Deshalb ein eigener Einstieg statt eines Umwegs über
        ``log_action``.
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
    # Erfassung: Messwerte
    # ------------------------------------------------------------------
    @staticmethod
    def _is_change_point(state, device_uuid, quantity, value, now):
        """Entscheidet, ob ein Wert als Änderungspunkt gespeichert wird.

        Gespeichert wird, wenn einer der drei Fälle zutrifft:

        1. Für diese Größe gibt es noch keinen gespeicherten Wert.
        2. Der Wert weicht um mehr als die Schwelle ab
           (``MEASUREMENT_THRESHOLDS``).
        3. Seit dem letzten Stützpunkt ist ``MEASUREMENT_MAX_SILENCE``
           vergangen - damit eine flache Linie nicht wie eine Datenlücke
           aussieht.

        Aktualisiert ``state`` bei einem Treffer direkt mit.
        """
        threshold = MEASUREMENT_THRESHOLDS.get(quantity)
        if threshold is None:
            return False  # unbekannte Größe (z.B. 'power') nicht führen
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
            drift = threshold + 1  # nicht vergleichbar -> als Änderung werten
        if drift > threshold or (now - prev_ts) >= MEASUREMENT_MAX_SILENCE:
            state[key] = (numeric, now)
            return True
        return False

    def log_sensor(self, device, sensor_data):
        """Nimmt Messwerte auf - aber nur die, die sich geändert haben.

        Args:
            device: Geräteobjekt
            sensor_data: dict, z.B. {'temperature': 22.5, 'humidity': 65}

        Returns:
            True, wenn ein Änderungspunkt geschrieben wurde
        """
        if not sensor_data:
            return False
        try:
            uuid = device.uuid
            name = device.name
            platform = self._detect_platform(device)
        except Exception as e:
            log.debug(f"Verlauf: Messwert ohne Geräteangaben verworfen: {e}")
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
    # Abfrage
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
            event_type: 'action' (Ereignisse), 'sensor' (Messwerte) oder None
                (beides zusammen)
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
        """Verdichtet Messwerte zu Min/Max/Mittelwert je Gerät und Größe.

        Das ist der eigentliche Zweck der Messwerte: eine Zeile beantwortet,
        wofür man sonst hundert Einzelzeilen durchgehen müsste.

        Der Mittelwert ist zeitgewichtet (Trapez über die Stützpunkte), nicht
        das arithmetische Mittel der Punkte - sonst würde eine Phase mit
        vielen Änderungen den Durchschnitt gegenüber einer langen ruhigen
        Phase verzerren. Bei nur einem Punkt ist der Mittelwert dieser Wert.

        Returns:
            Liste von dicts, sortiert nach Gerätename und Größenreihenfolge::

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
                rec = series.setdefault((uuid, quantity), {
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
                    'name': entry.get('device_name', _('Unbekannt')),
                    'platform': entry.get('platform', ''),
                }
        return sorted(seen.values(), key=lambda d: d['name'].lower())

    def clear(self):
        """Löscht Ereignisse und Messwerte."""
        with self._lock:
            self._events.clear()
            self._measurements.clear()
            self._last_written = {}

    def get_entry_count(self):
        """Gesamtzahl aller Einträge (Ereignisse + Messwerte)."""
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

            writer = csv.writer(f, delimiter=';', quoting=csv.QUOTE_MINIMAL)

            # Header
            # Translators: Column headers of the CSV export (history).
            writer.writerow([
                _('Zeitpunkt'), _('Gerätename'), _('Plattform'), _('Typ'),
                _('Herkunft'), _('Aktion'), _('Details'),
                _('Temperatur (°C)'), _('Luftfeuchtigkeit (%)'),
                _('CO₂ (ppm)'), _('Luftdruck (mbar)'),
            ])

            for entry in entries:
                ts = entry.get('timestamp', 0)
                dt = datetime.fromtimestamp(ts) if ts else None
                time_str = dt.strftime('%d.%m.%Y %H:%M:%S') if dt else ''

                sensor = entry.get('sensor_data') or {}

                # Free-text columns are defused (see _csv_safe); the numeric
                # sensor columns cannot carry a formula and stay untouched so
                # they remain usable as numbers in the spreadsheet.
                writer.writerow([
                    time_str,
                    self._csv_safe(entry.get('device_name', '')),
                    self._csv_safe(entry.get('platform', '')),
                    self._csv_safe(entry.get('event_type', '')),
                    self._csv_safe(_source_text(entry.get('source', ''))),
                    self._csv_safe(entry.get('action', '')),
                    self._csv_safe(entry.get('details', '')),
                    sensor.get('temperature', ''),
                    sensor.get('humidity', ''),
                    sensor.get('co2', ''),
                    sensor.get('pressure', ''),
                ])

            if filepath:
                f.close()
                return filepath
            else:
                return output.getvalue()

        except Exception as e:
            log.error(f"CSV-Export fehlgeschlagen: {e}")
            raise
        finally:
            # Nur echte Dateien schließen (StringIO wird vom Aufrufer gelesen).
            if filepath and f is not None:
                try:
                    f.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Darstellung
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
        time_abs = dt.strftime('%d.%m.%Y %H:%M') if dt else _('Unbekannt')
        device_name = entry.get('device_name', _('Unbekannt'))
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
        return _("{time} – {name}: Unbekanntes Ereignis").format(
            time=time_abs, name=device_name)


def _time_weighted_average(points):
    """Zeitgewichteter Mittelwert über (Zeitstempel, Wert)-Punkte.

    Trapezregel über die Stützpunkte, geteilt durch die Gesamtdauer. Bei
    einem einzelnen Punkt oder einer Gesamtdauer von 0 wird dieser Wert
    zurückgegeben.
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
    """Formatiert einen Zeitstempel als ``vor 5 Minuten`` o.ä."""
    if not ts:
        # Translators: Placeholder for an unknown time in the history.
        return _('Unbekannt')
    diff = time.time() - ts
    if diff < 60:
        # Translators: Relative time in the history (less than 1 minute ago).
        return _("gerade eben")
    if diff < 3600:
        mins = int(diff / 60)
        # Translators: Relative time in the history (minutes).
        return (_("vor {count} Minute") if mins == 1
                else _("vor {count} Minuten")).format(count=mins)
    if diff < 86400:
        hours = int(diff / 3600)
        # Translators: Relative time in the history (hours).
        return (_("vor {count} Stunde") if hours == 1
                else _("vor {count} Stunden")).format(count=hours)
    days = int(diff / 86400)
    # Translators: Relative time in the history (days).
    return (_("vor {count} Tag") if days == 1
            else _("vor {count} Tagen")).format(count=days)


def format_sensor_values(sensor):
    """Formatiert ein sensor_data-dict als lesbare Aufzählung."""
    parts = []
    for quantity in MEASUREMENT_ORDER:
        if quantity in sensor:
            parts.append(format_measurement(quantity, sensor[quantity]))
    # Unbekannte Größen (z.B. 'power' aus alten Dateien) hinten anhängen.
    for quantity, value in sensor.items():
        if quantity not in MEASUREMENT_ORDER:
            parts.append(f"{value} {MEASUREMENT_UNITS.get(quantity, '')}".strip())
    # Translators: History entry without sensor values.
    return ", ".join(p for p in parts if p) if parts else _("Keine Daten")


def _source_text(source):
    """Anzeigetext für die Herkunft eines Ereignisses."""
    if source == SOURCE_EXTERN:
        # Translators: Origin of a history event - the device was switched
        # outside of NVDA (manufacturer app, voice assistant, button on the
        # device). Deliberately not more specific: which of these it was
        # cannot be determined from the cloud notification.
        return _("extern")
    if source == SOURCE_SYSTEM:
        # Translators: Origin of a history event - triggered automatically.
        return _("automatisch")
    if source == SOURCE_LOCAL:
        # Translators: Origin of a history event - the user did it themselves.
        return _("ich")
    return ""


def _format_action_text(action, details=""):
    """Formats an action text for display"""
    # Translators: The following texts describe actions in the device history.
    action_map = {
        'toggle_on': _('Eingeschaltet'),
        'toggle_off': _('Ausgeschaltet'),
        'set_temp': _('Temperatur gesetzt'),
        'set_mode': _('Modus geändert'),
        'back_to_schedule': _('Zurück zum Zeitplan'),
        'diffuser_light': _('Schwach sprühen'),
        'diffuser_strong': _('Stark sprühen'),
        'diffuser_off': _('Sprühen aus'),
        # Netatmo-specific actions (keys as logged in dialog_netatmo.py)
        'therm_mode': _('Modus geändert'),
        'switch_schedule': _('Heizprogramm gewechselt'),
        # Meross light actions (keys as logged in
        # dialog_meross.py/device_dialog.py)
        'light_luminance': _('Helligkeit geändert'),
        'light_temperature': _('Lichtfarbe geändert'),
        'light_rgb': _('Farbe geändert'),
        # Cozytouch-specific actions (keys as logged in dialog_cozytouch.py)
        'set_target_temp': _('Zieltemperatur gesetzt'),
        'boost_on': _('Boost eingeschaltet'),
        'boost_off': _('Boost ausgeschaltet'),
        'away_on': _('Abwesenheit eingeschaltet'),
        'away_off': _('Abwesenheit ausgeschaltet'),
        # VeSync-specific actions
        'set_fan_speed': _('Lüftergeschwindigkeit geändert'),
        'set_auto_preference': _('Auto-Profil geändert'),
        'set_nightlight': _('Nachtlicht geändert'),
        'oscillation_on': _('Oszillation eingeschaltet'),
        'oscillation_off': _('Oszillation ausgeschaltet'),
        'mute_on': _('Stumm eingeschaltet'),
        'mute_off': _('Stumm ausgeschaltet'),
        'display_on': _('Display eingeschaltet'),
        'display_off': _('Display ausgeschaltet'),
        'child_lock_on': _('Kindersicherung eingeschaltet'),
        'child_lock_off': _('Kindersicherung ausgeschaltet'),
        'reset_filter': _('Filter zurückgesetzt'),
    }
    text = action_map.get(action, action)
    if details:
        text = f"{text}: {details}"
    return text


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
