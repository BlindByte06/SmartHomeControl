# -*- coding: utf-8 -*-
"""Smart Home Control - is a platform answering, and since when is it not?

The add-on announced a platform going away and coming back, once each. That
covers the whole platform falling over, and nothing else - and it is gone the
moment it has been spoken. Three things were missing.

A **partial failure** never reached it. The Netatmo cloud answers with two
services: the weather station and the heating. One of them can fail with a
503 while the other is fine; the device list then comes back without that
half, the poll counts as a success, and the last known values keep being read
out as if they had just arrived. Nothing was ever said.

There was **nowhere to look it up**. Anyone who missed the announcement -
away from the keyboard, in another program, speech interrupted - had no way
to ask what is reachable right now, let alone what happened half an hour ago.

And **a single device that does not answer is not a platform fault**. The log
of 2026-09-09 shows one Meross device out of twenty-two timing out on roughly
every other poll: "1 of 22 devices not answering", "Meross reconnected", and
again, three times in nine minutes. That is a device, and it belongs in a
list one can read - not in speech, and not in the label of the platform,
which would rebuild the tree every few minutes and make NVDA repeat the
selected entry.

So the split is deliberate:

* **Spoken and shown at the platform**: the platform itself, and a whole
  service of it - things a user can act on.
* **Recorded only**: which device missed which poll, since when, and how
  often. The platform status dialog shows it.

What is stored is language-neutral - platform and part keys, device names and
numbers; the wording is put together when it is displayed.
"""

import threading
import time
from collections import deque

try:
    import addonHandler
    addonHandler.initTranslation()
except Exception:  # outside of NVDA (checks, build)
    def _(text):
        return text

# The three states a platform can be in. 'partial' is the one that used to be
# invisible: something answered, but not everything.
OK = 'ok'
PARTIAL = 'partial'
DOWN = 'down'

# How many state changes are kept. They only happen on a transition, so this
# holds days of a quiet installation and still survives a bad afternoon.
EVENT_LIMIT = 300

# Names of the parts a platform can be missing, for the display. Keys are
# neutral and stay in the record; only these texts are translated.
PART_NAMES = {
    # Translators: Part of the Netatmo cloud: the weather station service.
    'weather': _("weather station"),
    # Translators: Part of the Netatmo cloud: the heating service
    # (thermostat, valves).
    'energy': _("heating"),
}


class PlatformHealth:
    """One record per platform, written by the poll and read by the display.

    Thread-safe: the scheduler writes from its own thread while the dialog
    reads from the wx thread.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._entries = {}
        self._events = deque(maxlen=EVENT_LIMIT)

    def _entry(self, platform):
        return self._entries.setdefault(platform, {
            'state': None,       # None = never polled yet
            'since': 0.0,        # when the current state began
            'parts': (),         # neutral keys of what did not answer
            'devices': {},       # key -> per-device record, see note_devices
            'devices_ok': None,
            'devices_total': None,
            'last_ok': 0.0,      # when the platform last answered fully
        })

    # ------------------------------------------------------------------
    # Written by the poll
    # ------------------------------------------------------------------
    def note_parts(self, platform, failed_parts):
        """Records which named parts of a platform did not answer."""
        with self._lock:
            self._entry(platform)['parts'] = tuple(failed_parts or ())

    def note_devices(self, platform, answered, missing):
        """Records which devices answered this poll and which did not.

        Both are sequences of ``(key, name)``. The key identifies the device
        across polls (unique_id/uuid), the name is only for the display.

        Kept per device rather than as a count: "1 of 22" says nothing about
        whether it is the same one every time, which is the difference
        between a device to look at and a poll that was unlucky.
        """
        now = time.time()
        with self._lock:
            devices = self._entry(platform)['devices']
            for key, name in answered or ():
                record = self._device_record(devices, key, name)
                record['name'] = name or record['name']
                record['ok'] = True
                record['last_ok'] = now
                record['misses'] = 0
                record['since'] = 0.0
            for key, name in missing or ():
                record = self._device_record(devices, key, name)
                record['name'] = name or record['name']
                if record['ok'] or not record['since']:
                    record['since'] = now      # start of this run of misses
                record['ok'] = False
                record['last_miss'] = now
                record['misses'] += 1
                record['total_misses'] += 1
            entry = self._entry(platform)
            entry['devices_ok'] = len(answered or ())
            entry['devices_total'] = len(answered or ()) + len(missing or ())

    @staticmethod
    def _device_record(devices, key, name):
        return devices.setdefault(key, {
            'name': name or key,
            'ok': True,
            'last_ok': 0.0,
            'last_miss': 0.0,
            'since': 0.0,        # first miss of the current run
            'misses': 0,         # misses in a row
            'total_misses': 0,   # since the sign-in
        })

    def update(self, platform, ok):
        """Takes the result of one poll; returns the new state when it changed.

        ``ok`` is what the refresh function returned: True, False - or None,
        which means nothing was polled and the record stays as it is.

        Devices that did not answer deliberately do NOT make a state: the
        platform answered, one of its devices did not, and that is what the
        device list in the status dialog is for.

        The first poll after a sign-in is recorded but not returned.
        Announcing it would tell the user that something they never saw fail
        is now working.
        """
        if ok is None:
            return None
        with self._lock:
            entry = self._entry(platform)
            if not ok:
                state = DOWN
            elif entry['parts']:
                state = PARTIAL
            else:
                state = OK
            if state == OK:
                entry['last_ok'] = time.time()
            previous = entry['state']
            if previous == state:
                return None
            entry['state'] = state
            entry['since'] = time.time()
            self._events.append({
                'time': entry['since'],
                # 'since' as well, so an event can be handed to the same text
                # helpers as a live record.
                'since': entry['since'],
                'platform': platform,
                'state': state,
                'parts': entry['parts'],
                'first': previous is None,
            })
            if previous is None:
                return None      # first observation: remember, do not announce
            return state

    def reset(self):
        """Forgets everything - after a sign-out.

        Same reason the connection flags are reset there: the next sign-in
        must not announce a reconnection to a state nobody saw fail.
        """
        with self._lock:
            self._entries.clear()
            self._events.clear()

    # ------------------------------------------------------------------
    # Read by the display
    # ------------------------------------------------------------------
    def get(self, platform):
        """A copy of the record, safe to read outside the lock."""
        with self._lock:
            entry = dict(self._entry(platform))
        entry['devices'] = {key: dict(rec) for key, rec in
                            entry['devices'].items()}
        return entry

    def events(self, platform=None):
        """The state changes, newest first."""
        with self._lock:
            items = [dict(event) for event in self._events]
        if platform:
            items = [event for event in items if event['platform'] == platform]
        items.reverse()
        return items

    def device_rows(self, platform):
        """Devices of one platform that missed a poll, worst case first.

        Devices that always answered are left out: the list is about the
        ones that did not.
        """
        entry = self.get(platform)
        rows = [dict(record, key=key, platform=platform)
                for key, record in entry['devices'].items()
                if not record['ok'] or record['total_misses']]
        rows.sort(key=lambda row: (row['ok'], -(row['last_miss'] or 0)))
        return rows

    def degraded(self, platforms):
        """The platforms among ``platforms`` that are not fully answering."""
        out = []
        for platform in platforms:
            entry = self.get(platform)
            if entry['state'] in (PARTIAL, DOWN):
                out.append((platform, entry))
        return out


def _clock(when):
    return time.strftime("%H:%M", time.localtime(when)) if when else "?"


def part_names(parts):
    """The readable names of the parts that did not answer."""
    return [PART_NAMES[key] for key in parts or () if key in PART_NAMES]


def status_suffix(entry):
    """What to append to a platform's line in the device tree, or ''.

    Deliberately short: it is read out every time the line is passed, so it
    says what is wrong and since when, and nothing else.
    """
    state = entry.get('state')
    if state == DOWN:
        # Translators: Appended to a platform in the device tree while it does
        # not answer at all. {time} = the time it stopped, e.g. 11:30.
        return _("unreachable since {time}").format(time=_clock(entry.get('since')))
    if state == PARTIAL:
        parts = part_names(entry.get('parts'))
        if parts:
            # Translators: Appended to a platform in the device tree when only
            # a part of it answers. {parts} = e.g. "weather station".
            return _("{parts} not answering").format(parts=", ".join(parts))
        # Translators: Appended to a platform in the device tree when part of
        # it does not answer and nothing more precise is known.
        return _("answering only in part")
    return ""


def values_age_suffix(entry, older_than=180.0):
    """"Values from HH:MM" while a platform has been silent for a while.

    A measured value that is hours old is read out exactly like a fresh one.
    This says so once, at the platform, rather than on every line below it.
    """
    if entry.get('state') not in (PARTIAL, DOWN):
        return ""
    last_ok = entry.get('last_ok') or 0.0
    if not last_ok or (time.time() - last_ok) < older_than:
        return ""
    # Translators: Appended to a platform in the device tree when its values
    # have not been updated for a while. {time} = when they last arrived.
    return _("values from {time}").format(time=_clock(last_ok))


def summary_text(health, platforms, labels):
    """One line for the status line: what is fine, and what is not.

    Args:
        health: the PlatformHealth.
        platforms: the platform keys that are switched on.
        labels: {key: brand name} for the display.
    """
    if health is None:
        return ""
    fine, trouble = [], []
    for platform in platforms:
        entry = health.get(platform)
        label = labels.get(platform, platform)
        if entry['state'] == OK:
            fine.append(label)
        elif entry['state'] in (PARTIAL, DOWN):
            trouble.append(f"{label}: {status_suffix(entry)}")
    if not trouble:
        return ""
    if fine:
        # The separator matches the status line the summary is appended to.
        # Translators: Status line summary. {fine} = platforms that answer,
        # {trouble} = the others with the reason.
        return _("{fine} up to date | {trouble}").format(
            fine=", ".join(fine), trouble=" | ".join(trouble))
    return " | ".join(trouble)


def announcement(platform_label, state, entry):
    """What to say once when a platform changes state."""
    if state == OK:
        # Translators: Announced when a smart home platform is reachable again
        # after a connection loss. {platform} is Meross/Netatmo/VeSync.
        return _("{platform} reconnected").format(platform=platform_label)
    if state == PARTIAL:
        # Translators: Announced when only a part of a platform still answers.
        # {platform} is the brand name, {detail} says which part.
        return _("{platform}: {detail}").format(
            platform=platform_label, detail=status_suffix(entry))
    # Translators: Announced when a smart home platform is temporarily
    # unreachable.
    return _("{platform} temporarily unreachable").format(
        platform=platform_label)


def event_text(event, labels):
    """One line for the event list of the status dialog."""
    label = labels.get(event['platform'], event['platform'])
    if event.get('first') and event['state'] == OK:
        # Translators: Event list of the platform status: the first poll after
        # signing in, which is not announced. {platform} = brand name.
        return _("{platform}: connected").format(platform=label)
    return announcement(label, event['state'], event)
