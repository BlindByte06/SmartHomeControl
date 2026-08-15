# -*- coding: utf-8 -*-
"""
Smart Home Control - energy report for metering plugs.

Collects power samples (watts) from the Meross metering plugs
(MSS310/MSS315, MOP320) in the background and integrates them over time
into daily and weekly energy figures (kWh).

Kept separate from the action history (history.py): power samples arrive
far more often than switching actions and would push everything else out
of its 5000-entry limit within days.

Storage format (JSON, in the NVDA addons folder like favourites/history):
{
    "<device_uuid>": {
        "name": "Server plug",
        "samples": [[ts, watt], [ts, watt], ...]
    }, ...
}
"""

import os
import json
import time
import datetime

# NVDA logger (see favorites.py); fallback for use outside NVDA.
try:
    from logHandler import log
except ImportError:
    import logging
    log = logging.getLogger(__name__)

# Next to favourites/history in the addons folder (survives updates).
_ADDON_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_ADDONS_DIR = os.path.dirname(_ADDON_DIR)
ENERGY_FILE = os.path.join(_ADDONS_DIR, "SmartHomeControl_energy.json")

# Minimum spacing between two stored samples per device. The Meross metrics
# call runs at most every 120 s anyway (MEROSS_METRICS_MIN_INTERVAL).
SAMPLE_MIN_INTERVAL = 60.0
# Samples older than 8 days are dropped on save (7-day report plus buffer).
RETENTION_SECONDS = 8 * 86400
# Gaps larger than this (device offline, NVDA closed) are NOT counted as
# consumption - better honestly too little than invented kWh.
MAX_GAP_SECONDS = 900.0
# Batched saving (same approach as history.py).
SAVE_DEBOUNCE_SECONDS = 120
SAVE_DEBOUNCE_MAX_SAMPLES = 20


def _local_midnight(ts):
    """Timestamp of the last local midnight before ``ts``.

    Deliberately NOT ``ts - (hour*3600 + minute*60 + second)``: that formula
    assumes as many epoch seconds have passed since midnight as the clock
    shows, which breaks on daylight saving changeover days (measured with
    TZ=Europe/Berlin it landed on 01:00 instead of 00:00, and once on 23:00
    of the previous day). ``replace()`` on a local ``datetime`` handles the
    changeover correctly.
    """
    return datetime.datetime.fromtimestamp(ts).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()


class EnergyLog:
    """Keeps power samples and derives energy figures from them."""

    def __init__(self):
        self._data = {}
        self._dirty = False
        self._last_save_time = 0.0
        self._unsaved_count = 0
        self._last_sample_time = {}  # device_uuid -> ts of the last sample
        self._load()

    # ---------------- Persistence ----------------
    def _load(self):
        try:
            if os.path.exists(ENERGY_FILE):
                with open(ENERGY_FILE, 'r', encoding='utf-8') as f:
                    self._data = json.load(f)
                total = sum(len(d.get('samples', [])) for d in self._data.values())
                log.debug(f"Energy log loaded: {len(self._data)} devices, {total} samples")
        except Exception as e:
            log.error(f"Could not load energy log: {e}")
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
            log.error(f"Could not save energy log: {e}")

    def flush(self):
        """Forces an immediate save (e.g. when NVDA shuts down)."""
        self._save()

    # ---------------- Collection ----------------
    def add_sample(self, device_uuid, device_name, watts):
        """Records one power sample (throttled, debounced)."""
        if watts is None:
            return
        now = time.time()
        last = self._last_sample_time.get(device_uuid, 0.0)
        if (now - last) < SAMPLE_MIN_INTERVAL:
            return
        self._last_sample_time[device_uuid] = now
        rec = self._data.setdefault(device_uuid, {'name': device_name, 'samples': []})
        rec['name'] = device_name  # pick up renames
        rec['samples'].append([round(now, 1), round(float(watts), 1)])
        self._dirty = True
        self._unsaved_count += 1
        if (self._unsaved_count >= SAVE_DEBOUNCE_MAX_SAMPLES
                or (now - self._last_save_time) >= SAVE_DEBOUNCE_SECONDS):
            self._save()

    # ---------------- Reporting ----------------
    @staticmethod
    def _integrate(samples, since_ts, until_ts):
        """Trapezoidal integration of power over time -> kWh.

        Gaps > MAX_GAP_SECONDS are skipped (no invented consumption while
        offline). The last sample is carried forward to ``until_ts``, capped
        the same way.
        """
        pts = [(t, w) for t, w in samples if since_ts <= t <= until_ts]
        if not pts:
            return 0.0
        ws = 0.0  # watt-seconds
        for (t1, w1), (t2, w2) in zip(pts, pts[1:]):
            dt = t2 - t1
            if 0 < dt <= MAX_GAP_SECONDS:
                ws += (w1 + w2) / 2.0 * dt
        # Tail: last sample up to until_ts (capped)
        t_last, w_last = pts[-1]
        tail = min(max(until_ts - t_last, 0.0), MAX_GAP_SECONDS)
        ws += w_last * tail
        return ws / 3_600_000.0  # Ws -> kWh

    def summary(self):
        """Returns [(uuid, name, kwh_today, kwh_7days, last_watts), ...].

        Only devices with at least one sample in the last 7 days, sorted by
        today's consumption (descending).
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
    """Returns the global EnergyLog instance (singleton)."""
    global _instance
    if _instance is None:
        _instance = EnergyLog()
    return _instance


def flush_pending():
    """Saves anything pending without creating a new instance."""
    if _instance is not None:
        _instance.flush()
