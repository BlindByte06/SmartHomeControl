# -*- coding: utf-8 -*-
"""
Smart Home Control - Energie-Auswertung für Messsteckdosen.

Sammelt Leistungs-Stichproben (Watt) der Meross-Messsteckdosen
(MSS310/MSS315, MOP320) im Hintergrund und berechnet daraus
Tages-/Wochen-Energiemengen (kWh) per Zeitintegration.

Eigene, kompakte Ablage getrennt vom Aktions-Verlauf (history.py):
Leistungs-Stichproben fallen deutlich häufiger an als Schalt-Aktionen
und würden dessen 5000-Einträge-Limit binnen Tagen verdrängen.

Speicherformat (JSON, im NVDA-addons-Ordner wie Favoriten/Verlauf):
{
    "<device_uuid>": {
        "name": "Steckdose Server",
        "samples": [[ts, watt], [ts, watt], ...]
    }, ...
}
"""

import os
import json
import time
import datetime

# NVDA-Logger (siehe favorites.py); Fallback für Nutzung außerhalb von NVDA.
try:
    from logHandler import log
except ImportError:
    import logging
    log = logging.getLogger(__name__)

# Ablage neben Favoriten/Verlauf im addons-Ordner (übersteht Updates).
_ADDON_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_ADDONS_DIR = os.path.dirname(_ADDON_DIR)
ENERGY_FILE = os.path.join(_ADDONS_DIR, "SmartHomeControl_energy.json")

# Mindestabstand zwischen zwei gespeicherten Stichproben pro Gerät. Der
# Meross-Metrik-Abruf läuft ohnehin höchstens alle 120 s (siehe
# MEROSS_METRICS_MIN_INTERVAL); 60 s hier ist nur eine Untergrenze.
SAMPLE_MIN_INTERVAL = 60.0
# Stichproben älter als 8 Tage werden beim Speichern verworfen (für die
# 7-Tage-Auswertung reicht das inkl. Puffer).
RETENTION_SECONDS = 8 * 86400
# Lücken größer als dieser Wert (Gerät offline, NVDA aus) werden bei der
# Integration NICHT als Verbrauch gezählt - lieber ehrlich zu wenig als
# erfundene kWh.
MAX_GAP_SECONDS = 900.0
# Speichern gesammelt (analog history.py).
SAVE_DEBOUNCE_SECONDS = 120
SAVE_DEBOUNCE_MAX_SAMPLES = 20


def _local_midnight(ts):
    """Zeitstempel der letzten lokalen Mitternacht vor ``ts``.

    Bewusst NICHT ``ts - (Stunde*3600 + Minute*60 + Sekunde)``: diese Formel
    unterstellt, dass seit Mitternacht genau so viele Epoch-Sekunden vergangen
    sind, wie die Uhr anzeigt. An den Zeitumstellungstagen stimmt das nicht -
    gemessen mit TZ=Europe/Berlin am 25.10.2026 um 12:00 landete sie auf
    01:00 statt 00:00 (Tagesverbrauch ohne die erste Stunde), am 29.03.2026
    auf 23:00 des Vortags (eine Stunde zu viel). ``replace()`` auf einem
    lokalen ``datetime`` rechnet die Umstellung korrekt heraus.
    """
    return datetime.datetime.fromtimestamp(ts).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()


class EnergyLog:
    """Verwaltet Leistungs-Stichproben und berechnet Energiemengen."""

    def __init__(self):
        self._data = {}
        self._dirty = False
        self._last_save_time = 0.0
        self._unsaved_count = 0
        self._last_sample_time = {}  # device_uuid -> ts der letzten Stichprobe
        self._load()

    # ---------------- Persistenz ----------------
    def _load(self):
        try:
            if os.path.exists(ENERGY_FILE):
                with open(ENERGY_FILE, 'r', encoding='utf-8') as f:
                    self._data = json.load(f)
                total = sum(len(d.get('samples', [])) for d in self._data.values())
                log.debug(f"Energie-Log geladen: {len(self._data)} Geräte, {total} Stichproben")
        except Exception as e:
            log.error(f"Energie-Log konnte nicht geladen werden: {e}")
            self._data = {}

    def _save(self):
        if not self._dirty:
            return
        try:
            cutoff = time.time() - RETENTION_SECONDS
            for dev in self._data.values():
                dev['samples'] = [s for s in dev.get('samples', []) if s[0] >= cutoff]
            tmp = ENERGY_FILE + ".tmp"
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, ensure_ascii=False, indent=None)
            os.replace(tmp, ENERGY_FILE)
            self._dirty = False
            self._last_save_time = time.time()
            self._unsaved_count = 0
        except Exception as e:
            log.error(f"Energie-Log konnte nicht gespeichert werden: {e}")

    def flush(self):
        """Erzwingt sofortiges Speichern (z.B. beim NVDA-Beenden)."""
        self._save()

    # ---------------- Erfassung ----------------
    def add_sample(self, device_uuid, device_name, watts):
        """Nimmt eine Leistungs-Stichprobe auf (gedrosselt, debounced)."""
        if watts is None:
            return
        now = time.time()
        last = self._last_sample_time.get(device_uuid, 0.0)
        if (now - last) < SAMPLE_MIN_INTERVAL:
            return
        self._last_sample_time[device_uuid] = now
        rec = self._data.setdefault(device_uuid, {'name': device_name, 'samples': []})
        rec['name'] = device_name  # Namensänderungen mitnehmen
        rec['samples'].append([round(now, 1), round(float(watts), 1)])
        self._dirty = True
        self._unsaved_count += 1
        if (self._unsaved_count >= SAVE_DEBOUNCE_MAX_SAMPLES
                or (now - self._last_save_time) >= SAVE_DEBOUNCE_SECONDS):
            self._save()

    # ---------------- Auswertung ----------------
    @staticmethod
    def _integrate(samples, since_ts, until_ts):
        """Trapez-Integration der Leistung über die Zeit -> kWh.

        Lücken > MAX_GAP_SECONDS werden übersprungen (kein erfundener
        Verbrauch während Offline-Phasen). Die letzte Stichprobe wird bis
        ``until_ts`` fortgeschrieben, ebenfalls gedeckelt.
        """
        pts = [(t, w) for t, w in samples if since_ts <= t <= until_ts]
        if not pts:
            return 0.0
        ws = 0.0  # Wattsekunden
        for (t1, w1), (t2, w2) in zip(pts, pts[1:]):
            dt = t2 - t1
            if 0 < dt <= MAX_GAP_SECONDS:
                ws += (w1 + w2) / 2.0 * dt
        # Tail: letzte Stichprobe bis until_ts (gedeckelt)
        t_last, w_last = pts[-1]
        tail = min(max(until_ts - t_last, 0.0), MAX_GAP_SECONDS)
        ws += w_last * tail
        return ws / 3_600_000.0  # Ws -> kWh

    def summary(self):
        """Liefert [(uuid, name, kwh_heute, kwh_7tage, letzte_leistung_watt), ...].

        Nur Geräte mit mindestens einer Stichprobe in den letzten 7 Tagen;
        sortiert nach Tagesverbrauch (absteigend).
        """
        now = time.time()
        midnight = _local_midnight(now)
        week_start = now - 7 * 86400
        out = []
        for uuid, rec in self._data.items():
            samples = rec.get('samples', [])
            recent = [s for s in samples if s[0] >= week_start]
            if not recent:
                continue
            kwh_today = self._integrate(samples, midnight, now)
            kwh_week = self._integrate(samples, week_start, now)
            last_watt = recent[-1][1]
            out.append((uuid, rec.get('name', uuid), kwh_today, kwh_week, last_watt))
        out.sort(key=lambda x: x[2], reverse=True)
        return out


# Singleton
_instance = None


def get_energy_log():
    """Liefert die globale EnergyLog-Instanz (Singleton)."""
    global _instance
    if _instance is None:
        _instance = EnergyLog()
    return _instance


def flush_pending():
    """Speichert Ungesichertes, ohne eine Instanz neu zu erzeugen."""
    if _instance is not None:
        _instance.flush()
