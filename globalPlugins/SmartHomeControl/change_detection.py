# -*- coding: utf-8 -*-
"""
Smart Home Control - detection of external device changes (all platforms).
Split out of __init__.py; behaviour unchanged.
"""

import time

import wx
import ui
from logHandler import log

import addonHandler
try:
    addonHandler.initTranslation()
except Exception as e:
    log.debug(f"initTranslation failed: {e}")
if "_" not in globals():  # fallback if initTranslation() fails
    # Without this fallback `_` stays undefined and the first `_()` call
    # raises a NameError mid-dialog instead of at import time.
    def _(s):
        return s

from .dialog_helpers import _beep
from .formatting import fmt_decimal
from .constants import (
    CACHE_VALID_DURATION, BOILER_COOLDOWN, NETATMO_MODE_NAMES,
    VESYNC_FILTER_WARN_THRESHOLD, BEEP_EXTERNAL_CHANGE,
    BEEP_ON, BEEP_OFF, BEEP_ACTION, BEEP_ERROR,
)


# What an external change is worth recording as, per platform: the field in
# the snapshot, the action key it goes into the history under, and whether a
# value belongs with it.
#
# Only settings someone can change are in here - the ones the add-on can set
# itself. A boiler starting or a filter running low is the appliance's own
# doing and belongs to the readings, not to the question the history is
# opened for: "was that me or someone else?"
#
# The action key and the detail are language-neutral on purpose. What the
# history shows is translated when it is displayed; an entry written under a
# German interface used to stay German for good.
_EXTERNAL_ACTIONS = {
    'cozytouch': (
        ('is_on', ('toggle_on', 'toggle_off'), False),
        ('boost', ('boost_on', 'boost_off'), False),
        ('away', ('away_on', 'away_off'), False),
        ('target_temp', 'set_target_temp', True),
        ('mode', 'set_mode', True),
    ),
    'vesync': (
        ('is_on', ('toggle_on', 'toggle_off'), False),
        ('oscillation_on', ('oscillation_on', 'oscillation_off'), False),
        ('mute_on', ('mute_on', 'mute_off'), False),
        ('display_on', ('display_on', 'display_off'), False),
        ('child_lock', ('child_lock_on', 'child_lock_off'), False),
        ('mode', 'set_mode', True),
        ('fan_level', 'set_fan_speed', True),
        ('nightlight_status', 'set_nightlight', True),
        ('auto_preference_type', 'set_auto_preference', True),
    ),
    'petlibro': (
        ('plan_enabled', ('feeding_plan_on', 'feeding_plan_off'), False),
        ('light', ('light_on', 'light_off'), False),
        ('water_mode', 'set_water_mode', True),
        ('low_water', 'set_low_water', True),
        ('sensing_duration', 'set_flow_duration', True),
    ),
    'netatmo': (
        ('setpoint', 'set_temp', True),
        ('mode', 'therm_mode', True),
        # The zone WITHIN the heating schedule (comfort, night ...), stored
        # under the name Netatmo gives it and translated when displayed. It
        # used to be written as "Heating schedule switched" - every evening
        # at 22:00, while the schedule itself had not changed at all.
        ('zone_key', 'schedule_zone', True),
    ),
}

# Within this many seconds before the end of its set duration, a boost that
# goes off is taken to have run out rather than to have been switched off by
# someone. The poll only sees the appliance every 15 to 30 seconds.
BOOST_END_TOLERANCE = 180


class _ChangeDetectionMixin:
    """Compares state snapshots and announces external changes."""

    def _record_local_toggle(self, device_uuid, new_state):
        """Remembers local switch actions to avoid duplicate announcements"""
        now = time.time()
        self._recent_local_toggles[device_uuid] = (new_state, now)
        # Channels: also mark the parent UUID (without a state check for multi-
        # channel)
        if "_ch" in device_uuid:
            parent_uuid = device_uuid.split("_ch", 1)[0]
            # For multi-channel we store None as the state - every change is
            # suppressed
            self._recent_local_toggles[parent_uuid] = (None, now)

    def _is_recent_local_toggle(self, device_uuid, new_state, window=2.0):
        """Checks whether a push event directly follows a local action"""
        now = time.time()
        # Remove stale entries
        stale = [key for key, (_state, ts) in self._recent_local_toggles.items() if now - ts > window]
        for key in stale:
            self._recent_local_toggles.pop(key, None)
        match = self._recent_local_toggles.get(device_uuid)
        if not match:
            return False
        cached_state, timestamp = match
        # For multi-channel (cached_state=None): every change is suppressed
        if cached_state is None:
            return (now - timestamp) <= window
        # Normal devices: the state must match
        return cached_state == new_state and (now - timestamp) <= window

    def _on_meross_throttled(self, device_name=None):
        """Called by the Meross API when cloud queries are throttled because of
        the hourly limit (200/h PER DEVICE). The API already calls this with
        a cooldown and passes the affected device; here we only announce it
        thread-safely.

        Important: only THIS one device is ever affected - the other Meross
        devices continue to update normally. That is why the announcement
        names the device and clarifies "this device".
        """
        if device_name:
            # Translators: Announcement that ONLY the named Meross device is
            # temporarily updated less often because its (per-device) cloud
            # limit is reached.
            msg = _("Meross {name}: updates paused – cloud limit of this "
                    "device reached").format(
                name=device_name)
        else:
            # Translators: As above, in case the device name is unavailable.
            msg = _("A Meross device is temporarily being updated less often "
                    "– its cloud limit has been reached")
        wx.CallAfter(ui.message, msg)

    def _detect_water_alarms(self, devices):
        """Announces and logs changes of the Meross water sensors.

        A water sensor reports no measured value, only a state - which is why
        it never appeared in the readings and, worse, was announced nowhere at
        all: the alarm was visible only to whoever opened the device menu at
        the right moment. For a leak sensor that is the one thing that must
        not happen.

        The state therefore lands in the history as an EVENT (that is what it
        is) and is announced. Runs after every poll pass.
        """
        from .history import get_history, SOURCE_SYSTEM, _device_key
        for device in devices:
            if not getattr(device, 'is_water_sensor', False):
                continue
            try:
                wet = device.is_water_detected()
            except Exception as e:
                log.debug(f"Water sensor {getattr(device, 'name', '?')}: {e}")
                continue
            if wet is None:
                continue
            # _device_key, not uuid: every sensor on a Meross hub carries
            # the hub's uuid, so two leak sensors on one hub (MS400, MS405,
            # in any combination) shared one entry here and overwrote each
            # other on every pass. Both then counted as changed: an alarm
            # AND the opposite all-clear for the other sensor, plus two
            # history entries, on every pass. The history has used this key
            # since the same fault merged its measurement series.
            key = _device_key(device)
            previous = self._previous_water_states.get(key)
            self._previous_water_states[key] = wet
            if previous is None or previous == wet:
                continue  # first reading or unchanged - nothing to report

            action = 'water_detected' if wet else 'water_cleared'
            try:
                get_history().log_action(device, action, "",
                                         source=SOURCE_SYSTEM)
            except Exception as e:
                log.debug(f"History entry (water sensor) failed: {e}")

            if not getattr(self, 'notify_meross_water', True):
                continue
            if wet:
                # Translators: Alarm of a Meross water sensor. {name} = device.
                msg = _("Water alarm: {name}").format(name=device.name)
                log.warning(f"Water alarm: {device.name}")
                wx.CallAfter(_beep, BEEP_ERROR)
            else:
                # Translators: A water sensor no longer detects water.
                msg = _("{name}: no water detected any more").format(
                    name=device.name)
                log.info(f"Water sensor dry again: {device.name}")
            wx.CallAfter(ui.message, msg)

    def _log_external_state_changes(self, platform, device, prev, new,
                                    system=None):
        """Writes what changed outside the add-on into the history.

        Until now only Meross did this, because its changes arrive as a push
        message and that path logged them. The other three platforms notice
        their changes by comparing snapshots while polling - they announced
        them and then dropped them. The history could answer "was that me or
        someone else?" for one platform out of four.

        ``system`` names the fields the appliance changed on its own - a
        heating schedule moving to its night zone, a boost running out. They
        are written with the origin "automatic" instead of "external", and a
        field may bring an action key of its own ({'boost': 'boost_ended'});
        None keeps the key of the table. Both happened every day and were
        written as if somebody had done them.

        A value that was not known before (None) is no change: the first
        complete reading after a sign-in would otherwise be written as one.

        Runs from the polling thread, so it is fully guarded: a history that
        cannot be written must not cost the announcement.
        """
        system = system or {}
        try:
            from .history import get_history
            history = get_history()
            for field, keys, with_value in _EXTERNAL_ACTIONS.get(platform, ()):
                before, after = prev.get(field), new.get(field)
                if before is None or after is None or before == after:
                    continue
                if with_value:
                    action, detail = keys, str(after)
                else:
                    # A pair: the first key for switched on, the second for off.
                    action, detail = keys[0] if after else keys[1], ""
                if field in system:
                    from .history import SOURCE_SYSTEM
                    history.log_action(device, system[field] or action,
                                       detail, source=SOURCE_SYSTEM)
                    continue
                history.log_external_action(
                    getattr(device, 'unique_id', None) or device.uuid,
                    device.name, platform, action, detail)
        except Exception as e:
            log.debug(f"History entry (external, {platform}) failed: {e}")

    def _log_external_change(self, device_uuid, device_name, new_state):
        """Writes an externally triggered switch to the history.

        Runs on the MQTT push thread, hence fully guarded: a logging error
        must not prevent the announcement.
        """
        try:
            from .history import get_history
            from .platform_utils import platform_of
            platform = 'meross'
            with self._devices_lock:
                for device in self.devices:
                    if device.uuid == device_uuid:
                        platform = platform_of(device)
                        break
            get_history().log_external_action(
                device_uuid, device_name, platform,
                # No detail: the action already says on/off (see
                # _detail_is_redundant in history.py).
                'toggle_on' if new_state else 'toggle_off', "")
        except Exception as e:
            log.debug(f"History entry (external) failed: {e}")

    def _on_external_device_change(self, device_name, new_state, device_uuid, channel_name=None):
        """
        Callback for external device status changes (Alexa, Meross app, etc.)

        This method is called when a device is switched externally.
        It gives an accessible announcement and updates the internal cache.

        Args:
            device_name: name of the device (for channels this already contains the channel name)
            new_state: True = switched on, False = switched off
            device_uuid: UUID of the device
            channel_name: optional - name of the channel for multi-channel devices
        """
        # Do not announce the user's own recent actions twice
        if self._is_recent_local_toggle(device_uuid, new_state):
            log.debug(f"Suppressing duplicate announcement for {device_uuid}")
            return

        # The same push twice within 2 seconds is one change.
        current_time = time.time()
        change_key = f"{device_uuid}_{new_state}_{channel_name or ''}"
        if (self._last_announced_change == change_key and
                (current_time - self._last_announced_time) < 2.0):
            log.debug(f"Duplicate push suppressed: {device_name}")
            return
        self._last_announced_change = change_key
        self._last_announced_time = current_time

        # Everything up to the announcement happens whatever the settings
        # say. The switches used to sit at the top of this method, so with
        # announcements off an external switch reached neither the history
        # nor the device cache nor the open dialog - the tree showed the old
        # state until the next poll, and the history never learnt of it.
        # Switching an announcement off means "do not interrupt me", not
        # "do not keep track".
        #
        # History: the origin is simply "external" - the cloud notification
        # does not say whether it was the vendor app, a voice assistant or
        # the button on the device.
        self._log_external_change(device_uuid, device_name, new_state)

        # Update the internal cache (under the lock, since called from the push
        # thread)
        with self._devices_lock:
            for device in self.devices:
                if device.uuid == device_uuid:
                    device._is_on = new_state
                    # For multi-channel devices: also update all channels
                    if hasattr(device, '_update_channels_status'):
                        device._update_channels_status()
                    break

        # LIVE UPDATE: update the open dialog if present
        if self._active_dialog and hasattr(self._active_dialog, 'update_device_status_live'):
            # Also pass channel_name for multi-channel devices
            wx.CallAfter(self._active_dialog.update_device_status_live, device_uuid, new_state, channel_name)

        # Only the announcement follows the settings:
        # - global switch "Announce external changes"
        # - per-platform switch from the "Notifications" tab: Meross toggle
        if not self.announce_external_changes:
            log.debug(f"External change not announced (announcements off): {device_name}")
            return
        if not getattr(self, 'notify_meross_toggle', True):
            log.debug(f"Meross toggle announcement disabled: {device_name}")
            return

        # Phonetic improvements
        display_name = device_name.replace("WLAN", "W-LAN")
        # Translators: Status "switched on" for smart home devices (short).
        status = _("on") if new_state else _("off")

        # Accessible announcement via wx.CallAfter (thread-safe)
        wx.CallAfter(_beep, BEEP_ON if new_state else BEEP_OFF)
        wx.CallAfter(ui.message, f"{display_name}: {status}")
        log.info(f"External change announced: {device_name} -> {status}")
    
    def is_cache_fresh(self):
        """Checks whether the device cache is still fresh"""
        if not self.devices or self._last_refresh_time == 0:
            return False
        return (time.time() - self._last_refresh_time) < CACHE_VALID_DURATION
    
    def _normalize_end_time(self, et):
        """Normalizes end_time: None, 0 and expired timestamps become None"""
        if not et or et <= time.time():
            return None
        return et

    # ----------------------------------------------------------
    # Netatmo: mark local actions and detect external changes
    # ----------------------------------------------------------
    def _record_local_netatmo_action(self, device_uuids):
        """Remembers a change made here, BEFORE it is sent.

        Netatmo had no such window at all: the snapshot was only brought up
        to date three seconds after the command, by a refresh of the dialog.
        A poll that fell between command and refresh announced the change
        made here as one made elsewhere and wrote it to the history as
        "external". Marked before sending, because the poll can come while
        the command is still on its way.

        ``device_uuids``: one uuid or several - the heating mode is set for
        the whole home, and every thermostat in it changes with it.
        """
        if isinstance(device_uuids, str):
            device_uuids = (device_uuids,)
        recent = getattr(self, '_recent_netatmo_actions', None)
        if recent is None:
            recent = self._recent_netatmo_actions = {}
        now = time.time()
        for uuid in device_uuids:
            recent[uuid] = now

    def _is_recent_local_netatmo_action(self, device_uuid, window=30.0):
        """True if a change was made here within ``window`` seconds."""
        recent = getattr(self, '_recent_netatmo_actions', None) or {}
        ts = recent.get(device_uuid, 0)
        if not ts:
            return False
        if (time.time() - ts) > window:
            recent.pop(device_uuid, None)
            return False
        return True

    def _snapshot_netatmo_state(self, dev, old_state=None):
        """The normalised state of a thermostat, compared from poll to poll.

        The one place it is built. The dialog used to assemble its own copy
        after an action, without the normalisation below - a missing
        open-window flag stayed None there and turned into "Window closed"
        on the next poll.

        Normalization against false notifications:
        - end_time: None/0/expired are treated the same
        - zone: on an API error (None) the previous value is kept
        - setpoint: compared rounded to 0.1°C
        - fields the API left out of this answer keep their previous value
        """
        raw_setpoint = getattr(dev, '_therm_setpoint', None)
        new_state = {
            'setpoint': (round(raw_setpoint, 1)
                         if raw_setpoint is not None else None),
            'mode': getattr(dev, '_therm_setpoint_mode', None),
            'end_time': self._normalize_end_time(
                getattr(dev, '_therm_setpoint_end_time', None)),
            # Translated, for the announcement ...
            'zone_name': getattr(dev, '_schedule_zone_name', None),
            # ... and as Netatmo names it, for the history, which stores
            # nothing translated.
            'zone_key': getattr(dev, '_schedule_zone_raw', None),
            'boiler': getattr(dev, '_boiler_status', None),
            'anticipating': getattr(dev, '_anticipating', None),
            'open_window': getattr(dev, '_open_window', None),
        }
        # State stabilization against API inconsistency. The Netatmo API
        # does not deliver fields like anticipating, open_window,
        # boiler_status or the zone on every call; a fresh NetatmoDevice has
        # None for them. Without this a flip-flop occurs:
        # cycle A: API delivers the field -> value stored
        # cycle B: API does NOT deliver the field -> None stored
        # cycle C: API delivers the field again -> false-positive "change"!
        if old_state is not None:
            for key in new_state:
                if new_state[key] is None and old_state.get(key) is not None:
                    new_state[key] = old_state[key]
        # Boolean normalization: None and False are semantically identical
        # for these fields ("not active").
        for bool_key in ('anticipating', 'open_window'):
            if new_state[bool_key] is None:
                new_state[bool_key] = False
        return new_state

    @staticmethod
    def _netatmo_change_by_schedule(old_state, new_state):
        """True when the thermostat changed because its schedule said so.

        Two cases, both the appliance's own doing:
        - it runs its schedule before and after, and the schedule moved to
          another zone (comfort at 06:00, night at 22:00) or its setpoint;
        - a time-limited manual setting ran out and it went back to the
          schedule by itself.
        Everything else - a mode chosen, a setpoint set while not on the
        schedule - was somebody.
        """
        old_mode, new_mode = old_state.get('mode'), new_state.get('mode')
        if new_mode != 'schedule':
            return False
        if old_mode == 'schedule':
            return True
        old_end = old_state.get('end_time')
        return bool(old_end) and old_end <= time.time() + 60

    def _detect_netatmo_changes(self, new_devices):
        """Detects external changes on Netatmo thermostats and announces them.

        The history is written whatever the announcement switches say -
        switching an announcement off means "do not interrupt me", not "do
        not keep track". This method used to return at once with
        announcements off, and then the snapshot stood still too: switched
        back on, the next poll compared against a state hours old and
        announced every difference since.

        Boiler toggles without other changes are only announced every 5
        minutes (normal heating cycles).
        """
        for dev in new_devices:
            if not getattr(dev, 'is_thermostat', False):
                continue
            uid = dev.uuid
            old_state = self._previous_netatmo_therm_states.get(uid)
            new_state = self._snapshot_netatmo_state(dev, old_state)
            self._previous_netatmo_therm_states[uid] = new_state
            if old_state is None:
                continue  # first observation: remember, do not announce

            if self._is_recent_local_netatmo_action(uid):
                log.debug(f"Netatmo change suppressed (local action): {dev.name}")
                continue

            # The history first, independent of the announcement switches.
            by_schedule = self._netatmo_change_by_schedule(old_state, new_state)
            system = ({'setpoint': None, 'zone_key': None, 'mode': None}
                      if by_schedule else None)
            self._log_external_state_changes(
                'netatmo', dev, old_state, new_state, system=system)

            if not self.announce_external_changes:
                continue

            # Per-event switches from the "Notifications" tab
            wants_setpoint = getattr(self, 'notify_netatmo_setpoint', True)
            wants_mode = getattr(self, 'notify_netatmo_mode', True)
            wants_boiler = getattr(self, 'notify_netatmo_boiler', True)
            wants_open_window = getattr(self, 'notify_netatmo_open_window', True)
            wants_anticipation = getattr(self, 'notify_netatmo_anticipation', False)

            changes = []

            # Setpoint changed (normalized to 0.1°C)
            old_sp = old_state.get('setpoint')
            new_sp = new_state.get('setpoint')
            if (wants_setpoint and new_sp is not None
                    and (old_sp is None or abs(old_sp - new_sp) >= 0.05)):
                # Translators: Netatmo thermostat announcement: new target
                # temperature in °C.
                changes.append(_("Target temperature: {temp}°C").format(
                    temp=fmt_decimal(new_sp, 1)))

            # Mode changed
            if wants_mode and old_state.get('mode') != new_state.get('mode') and new_state.get('mode') is not None:
                mode_text = NETATMO_MODE_NAMES.get(new_state['mode'], new_state['mode'])
                # For schedule mode also show the active zone name
                if new_state['mode'] == 'schedule' and new_state.get('zone_name'):
                    mode_text += f" ({new_state['zone_name']})"
                # Translators: Netatmo thermostat announcement: new heating
                # mode (e.g. schedule, manual, away).
                changes.append(_("Heating mode: {mode}").format(mode=mode_text))

            # Schedule zone changed (even if the mode stays the same)
            if (wants_mode
                    and old_state.get('mode') == new_state.get('mode') == 'schedule'
                    and old_state.get('zone_name') != new_state.get('zone_name')
                    and new_state.get('zone_name')):
                # Translators: Netatmo thermostat announcement: the
                # schedule switched to a different zone (e.g. day ->
                # night).
                changes.append(_("Schedule zone: {zone}").format(zone=new_state['zone_name']))

            # End time changed (normalized) - tied to the setpoint category
            old_et = old_state.get('end_time')
            new_et = new_state.get('end_time')
            if wants_setpoint and old_et != new_et:
                if new_et and new_et > time.time():
                    end_str = time.strftime("%H:%M", time.localtime(new_et))
                    # Translators: Netatmo thermostat announcement: until
                    # when the manual target temperature applies (HH:MM).
                    changes.append(_("until {time}").format(time=end_str))
                elif old_et and not new_et:
                    # Translators: Netatmo thermostat announcement: the
                    # time limit of a manual setting was removed.
                    changes.append(_("End time removed"))

            # Boiler status changed (heating on/off). Pure boiler toggles
            # (without other changes) are only reported every 5 min
            boiler_changed = (old_state.get('boiler') != new_state.get('boiler')
                              and new_state.get('boiler') is not None)
            if boiler_changed and wants_boiler:
                # Translators: Netatmo thermostat announcement: the boiler
                # is currently heating.
                boiler_text = _("Heating active") if new_state['boiler'] else _("Heating "
                                                                               "off")
                # If there are also other changes, always report the boiler
                if changes:
                    changes.append(boiler_text)
                else:
                    # Pure boiler change: check the cooldown
                    last_boiler_time = self._last_boiler_announce_time.get(uid, 0)
                    if (time.time() - last_boiler_time) >= BOILER_COOLDOWN:
                        changes.append(boiler_text)
                        self._last_boiler_announce_time[uid] = time.time()
                    else:
                        log.debug(f"Netatmo boiler toggle suppressed (cooldown): {dev.name}")

            # Pre-heating (anticipation) changed
            old_antic = old_state.get('anticipating')
            new_antic = new_state.get('anticipating')
            if wants_anticipation and old_antic != new_antic and new_antic is not None:
                if new_antic:
                    # Translators: Netatmo announcement: the thermostat is
                    # pre-heating for an upcoming target temperature
                    # increase.
                    changes.append(_("Pre-heating started"))
                else:
                    # Translators: Netatmo announcement: pre-heating has
                    # finished.
                    changes.append(_("Pre-heating finished"))

            # Open window changed
            old_ow = old_state.get('open_window')
            new_ow = new_state.get('open_window')
            if wants_open_window and old_ow != new_ow and new_ow is not None:
                if new_ow:
                    # Translators: Netatmo announcement: the thermostat
                    # detected an open window (sudden temperature drop).
                    changes.append(_("Open window detected"))
                else:
                    # Translators: Netatmo announcement: the open window
                    # was closed.
                    changes.append(_("Window closed"))

            if changes:
                change_text = ", ".join(changes)
                wx.CallAfter(_beep, BEEP_EXTERNAL_CHANGE)
                wx.CallAfter(ui.message, f"{dev.name}: {change_text}")
                log.info(f"Netatmo change: {dev.name} - {change_text}"
                         f"{' (schedule)' if by_schedule else ''}")
                # Also refresh the boiler cooldown on combined changes
                if boiler_changed:
                    self._last_boiler_announce_time[uid] = time.time()

    # ----------------------------------------------------------
    # VeSync: mark local actions and detect external changes
    # ----------------------------------------------------------
    def _record_local_vesync_action(self, device_uuid):
        """Remembers the timestamp of a local VeSync action.

        Called by the dialog after every successful VeSync action so the
        subsequent confirmation in the background refresh is not announced
        again as an external change.
        """
        self._recent_vesync_actions[device_uuid] = time.time()

    def _is_recent_local_vesync_action(self, device_uuid, window=30.0):
        """True if a local action happened within 'window' seconds.

        Default window raised to 30 s (previously 5 s). Background: the
        bypassV2 cloud response is often cached for 30-60 s; the *first*
        API response after a local action still contains the old value.
        With a 5 s window ``_detect_vesync_changes`` would have interpreted
        this cached response as an "external change" and announced the
        opposite of the user's own action. The per-field protection windows
        in the wrapper do prevent the display from jumping back, but they
        do not protect the snapshot comparison - hence the larger window
        here.
        """
        ts = self._recent_vesync_actions.get(device_uuid, 0)
        if not ts:
            return False
        if (time.time() - ts) > window:
            self._recent_vesync_actions.pop(device_uuid, None)
            return False
        return True

    def _snapshot_vesync_state(self, device):
        """Creates a status snapshot of a VeSync device (for diffing).

        Note - ``display`` is read from ``display_set_on`` (user preference)
        instead of ``display_on`` (current state): on the tower fan
        ``display_on`` follows the power switching (the display physically
        turns off with the device), while ``display_set_on`` reflects the
        setting the user wants. If we snapshotted ``display_on``, every
        on/off toggle would look like a display change and would falsely be
        announced as "display on/off".
        """
        display_pref = getattr(device, 'display_set_on', None)
        if display_pref is None:
            display_pref = getattr(device, 'display_on', None)
        return {
            'is_on': bool(getattr(device, 'is_on', False)),
            'is_offline': bool(getattr(device, 'is_offline', False)),
            'mode': getattr(device, 'mode', None),
            'fan_level': getattr(device, 'fan_level', None),
            'oscillation_on': getattr(device, 'oscillation_on', None),
            'mute_on': getattr(device, 'mute_on', None),
            'display_on': display_pref,
            'child_lock': getattr(device, 'child_lock', None),
            'nightlight_status': getattr(device, 'nightlight_status', None),
            'auto_preference_type': getattr(device, 'auto_preference_type', None),
            'air_quality': getattr(device, 'air_quality', None),
            'filter_life': getattr(device, 'filter_life', None),
            # Air fryer only, absent everywhere else. Without it the one
            # event a cook cannot see - the programme ending - passed
            # unnoticed through every path there is.
            'cook_status': getattr(device, 'cook_status', None),
        }

    def get_vesync_filter_warnings(self):
        """Returns VeSync purifiers whose remaining filter life reaches or
        falls below the threshold.

        Returns:
            list of (device name, remaining life %), ascending by %.
            Only devices with a known ``filter_life`` (purifiers) count.
        """
        threshold = getattr(self, 'vesync_filter_threshold', VESYNC_FILTER_WARN_THRESHOLD)
        warnings = []
        with self._devices_lock:
            for d in self.devices:
                if not getattr(d, 'is_vesync', False):
                    continue
                fl = getattr(d, 'filter_life', None)
                if fl is not None and fl <= threshold:
                    warnings.append((d.name, fl))
        warnings.sort(key=lambda x: x[1])
        return warnings

    def _announce_fryer_temp_verdict(self, dev):
        """Says so when an air fryer kept its own temperature.

        The cloud answers a temperature change with code 0 whether or not
        the appliance acts on it. Measured on a CAF-P583S: one accepted
        call carried 180 degrees and 508 seconds while the appliance was
        cooking at 205, the seconds landed, the degrees did not, and it
        went on regulating to 205 to the end of the programme. Without
        this the dialog says "change sent", goes on reading out "Set
        temperature: 205 °C", and nothing ever joins the two together.

        Not behind a notification switch and not behind
        ``announce_external_changes``: this is the reply to something the
        user did a moment ago, in the same class as an error message, not
        news about the world.

        Dormant since the temperature control was taken out of the tree -
        nothing calls ``set_time_or_temp`` with a temperature any more, so
        no verdict is ever produced. Kept for the same reason the control
        itself is: one log from a model that does apply a temperature puts
        both back, and this is the half that makes the control honest.
        """
        take = getattr(dev, 'take_temperature_verdict', None)
        if take is None:
            return  # not an air fryer
        verdict = take()
        if not verdict:
            return
        requested, kept = verdict
        # The appliance's own set temperature, formatted the way the tree
        # shows it - by now target_temp holds exactly the value that came
        # back, so the announcement and the line the user arrows to
        # afterwards cannot disagree.
        kept_text = dev.target_temperature_display()
        if not kept_text:
            return
        # Translators: An air fryer accepted a temperature change and did
        # not carry it out. {value} = the temperature the appliance is
        # still set to, with its unit.
        text = _("Temperature not applied, the appliance stays at "
                 "{value}").format(value=kept_text)
        wx.CallAfter(ui.message, "{name}: {text}".format(
            name=dev.name.replace("WLAN", "W-LAN"), text=text))
        log.info(f"VeSync air fryer {dev.name}: {requested} not applied, "
                 f"appliance stays at {kept}")

    def _detect_vesync_changes(self, devices):
        """Detects external changes on VeSync devices and announces them.

        Called in the background refresh AFTER update_device_status.
        Compares the previous snapshot with the current in-memory status
        and reports differences analogous to the Netatmo detection. The
        user's own recent action is suppressed.
        """
        from .constants import (
            VESYNC_PURIFIER_MODE_NAMES, VESYNC_FAN_MODE_NAMES,
            VESYNC_AIR_QUALITY_NAMES, VESYNC_NIGHTLIGHT_MODE_NAMES,
            VESYNC_AUTO_PREFERENCE_NAMES, VESYNC_PURIFIER_LEVEL_LABELS_3,
            VESYNC_FILTER_WARN_THRESHOLD, VESYNC_FRYER_COOK_ANNOUNCEMENTS,
        )

        for dev in devices:
            uid = dev.uuid

            # The verdict on a temperature the user sent themselves.
            # Deliberately ahead of everything below: this is not an
            # external change, and the local-action suppression a few
            # lines down would swallow it precisely because the change
            # was local - which is the one case it is about.
            self._announce_fryer_temp_verdict(dev)

            new_state = self._snapshot_vesync_state(dev)
            old_state = self._previous_vesync_states.get(uid)

            # ALWAYS remember the current state (even when we do not announce)
            # so no stale comparisons occur on the next refresh.
            self._previous_vesync_states[uid] = new_state

            if old_state is None:
                continue  # first snapshot - no announcement
            # Deliberately do NOT override the window: the 30 s default of
            # _is_recent_local_vesync_action covers both the foreground poll
            # interval (15 s) and the 30-60 s cached bypassV2 cloud response.
            # Previously window=5.0 stood here - which caused the user's own
            # action (e.g. turning the fan off in the dialog) to be falsely
            # announced as an external change after a few seconds, because the
            # confirming poll lay outside the 5 s. Applies to ALL VeSync device
            # types, since the detection runs centrally here.
            if self._is_recent_local_vesync_action(uid):
                log.debug(f"VeSync change suppressed (local action): {dev.name}")
                continue

            cls_name = type(dev).__name__
            mode_map = (VESYNC_PURIFIER_MODE_NAMES if cls_name == 'VeSyncPurifier'
                        else VESYNC_FAN_MODE_NAMES)
            # Tower fans have "normal" as the counterpart to the purifier's
            # manual mode.
            manual_mode_active = (
                (cls_name == 'VeSyncPurifier' and new_state['mode'] == 'manual')
                or (cls_name == 'VeSyncTowerFan' and new_state['mode'] == 'normal')
            )

            # The history, whatever the announcement switches say - it used
            # to be written only together with an announcement, so with the
            # switches off a change made elsewhere was simply lost. The fan
            # level only in a user-controlled mode: in auto and sleep the
            # purifier changes it by itself, and it was written as a fan
            # speed somebody had set whenever something else changed with
            # it.
            history_state = dict(new_state)
            if not (manual_mode_active and new_state['is_on']):
                history_state['fan_level'] = old_state.get('fan_level')
            self._log_external_state_changes('vesync', dev, old_state,
                                             history_state)

            if not self.announce_external_changes:
                continue

            # Per-event switches: maintained in the "Notifications" tab (see
            # settings_panel._create_notifications_tab).
            wants_toggle = getattr(self, 'notify_vesync_toggle', True)
            wants_mode = getattr(self, 'notify_vesync_mode', True)
            wants_fan_speed = getattr(self, 'notify_vesync_fan_speed', True)
            wants_air_quality = getattr(self, 'notify_vesync_air_quality', True)
            wants_filter = getattr(self, 'notify_vesync_filter', True)
            wants_cook = getattr(self, 'notify_vesync_cook', True)

            changes = []
            on_change = False

            # On/off
            if old_state['is_on'] != new_state['is_on']:
                if wants_toggle:
                    # Translators: Status of a VeSync device (switched on /
                    # switched off).
                    changes.append(_("on") if new_state['is_on'] else _("off"))
                # on_change stays set independently of the notification setting
                # because the live update of the dialog should still run
                on_change = True

            # Mode. old_state['mode'] is not None: on the first poll after
            # login the login snapshot is still incomplete (mode=None, since
            # get_devices has not loaded the detail values yet). Without this
            # guard the transition None -> "auto" would be announced as an
            # external change ("Modus: Auto") on every NVDA start - analogous
            # to oscillation_on/display_on etc.
            if (old_state['mode'] is not None
                    and old_state['mode'] != new_state['mode']
                    and new_state['mode'] is not None
                    and wants_mode):
                mode_label = mode_map.get(new_state['mode'], new_state['mode'])
                # Translators: VeSync announcement: new mode
                # (auto/manual/sleep/turbo).
                changes.append(_("Mode: {mode}").format(mode=mode_label))

            # Announce the fan level ONLY in manually controlled mode.
            # Background: pyvesync documents the two fields ``fan_level`` (=
            # current level) and ``fan_set_level`` (= manually set level). In
            # auto/sleep mode the Levoit purifier adjusts ``fan_level`` on its
            # own based on the measured air quality - these internal
            # adjustments are NOT user actions and are not shown as a level in
            # the dialog either (it says "automatically controlled" there).
            # Consequently they are not announced either; otherwise the user
            # would regularly hear "level 3 (high)" during auto operation
            # although they did not switch anything.
            if (old_state['fan_level'] is not None
                    and old_state['fan_level'] != new_state['fan_level']
                    and new_state['fan_level'] is not None
                    and new_state['is_on']
                    and manual_mode_active
                    and wants_fan_speed):
                # For purifiers with 3 levels (Core 200S/300S) append the
                # descriptive name: "level 1 (low)" etc., so the announcement
                # matches the display in the dialog.
                level_value = new_state['fan_level']
                # Translators: VeSync fan level announcement (e.g. "level 3").
                level_text = _("Level {level}").format(level=level_value)
                if cls_name == 'VeSyncPurifier':
                    fan_levels = list(getattr(dev, 'fan_levels', []) or [])
                    if (len(fan_levels) == 3
                            and level_value in VESYNC_PURIFIER_LEVEL_LABELS_3):
                        # Translators: VeSync fan level announcement with a
                        # descriptive name (e.g. "level 1 (low)") for models
                        # with 3 levels.
                        level_text = _("Level {level} ({label})").format(
                            level=level_value,
                            label=VESYNC_PURIFIER_LEVEL_LABELS_3[level_value],
                        )
                changes.append(level_text)

            # Oscillation (counts as the mode category for the tower fan)
            if (old_state['oscillation_on'] is not None
                    and old_state['oscillation_on'] != new_state['oscillation_on']
                    and new_state['oscillation_on'] is not None
                    and wants_mode):
                # Translators: VeSync tower fan announcement: oscillation.
                changes.append(_("Oscillation on") if new_state['oscillation_on']
                               else _("Oscillation off"))

            # Mute (mode category)
            if (old_state['mute_on'] is not None
                    and old_state['mute_on'] != new_state['mute_on']
                    and new_state['mute_on'] is not None
                    and wants_mode):
                # Translators: VeSync announcement: sounds (the device's
                # beeps).
                changes.append(_("muted") if new_state['mute_on']
                               else _("Sounds on"))

            # Display (mode category)
            if (old_state['display_on'] is not None
                    and old_state['display_on'] != new_state['display_on']
                    and new_state['display_on'] is not None
                    and wants_mode):
                # Translators: VeSync announcement: LED display on the device.
                changes.append(_("Display on") if new_state['display_on']
                               else _("Display off"))

            # Child lock (mode category)
            if (old_state['child_lock'] is not None
                    and old_state['child_lock'] != new_state['child_lock']
                    and new_state['child_lock'] is not None
                    and wants_mode):
                # Translators: VeSync announcement: the child lock locks the
                # buttons.
                changes.append(_("Child lock on") if new_state['child_lock']
                               else _("Child lock off"))

            # Night light (mode category)
            if (old_state['nightlight_status'] is not None
                    and old_state['nightlight_status'] != new_state['nightlight_status']
                    and new_state['nightlight_status'] is not None
                    and wants_mode):
                nl = VESYNC_NIGHTLIGHT_MODE_NAMES.get(
                    new_state['nightlight_status'], new_state['nightlight_status']
                )
                # Translators: VeSync Core 200S announcement: night light mode
                # (on/off/dimmed).
                changes.append(_("Night light {mode}").format(mode=nl))

            # Auto profile (mode category)
            if (old_state['auto_preference_type'] is not None
                    and old_state['auto_preference_type'] != new_state['auto_preference_type']
                    and new_state['auto_preference_type'] is not None
                    and wants_mode):
                ap = VESYNC_AUTO_PREFERENCE_NAMES.get(
                    new_state['auto_preference_type'],
                    new_state['auto_preference_type']
                )
                # Translators: VeSync announcement: the auto profile
                # (default/efficient/quiet) determines the control behavior in
                # auto mode.
                changes.append(_("Auto profile {profile}").format(profile=ap))

            # Air quality (its own category)
            if (old_state['air_quality'] is not None
                    and new_state['air_quality'] is not None
                    and old_state['air_quality'] != new_state['air_quality']
                    and wants_air_quality):
                aq = VESYNC_AIR_QUALITY_NAMES.get(
                    new_state['air_quality'], str(new_state['air_quality'])
                )
                # Translators: VeSync purifier announcement: new air quality
                # level (excellent/good/moderate/poor).
                changes.append(_("Air quality {level}").format(level=aq))

            # Filter life warning: report once when the remaining life crosses
            # the warning threshold from above. A pure threshold crossing (old
            # > threshold >= new) prevents repeated announcements on every
            # poll. The permanent hint is handled by the warning banner in the
            # dialog (for filters already below the threshold).
            threshold = getattr(self, 'vesync_filter_threshold', VESYNC_FILTER_WARN_THRESHOLD)
            old_filter = old_state.get('filter_life')
            new_filter = new_state.get('filter_life')
            if (wants_filter and old_filter is not None and new_filter is not None
                    and old_filter > threshold and new_filter <= threshold):
                # Translators: VeSync purifier warning: remaining filter life
                # low (in percent). Replace the filter soon.
                changes.append(_("Replace filter soon, remaining life "
                                 "{percent} percent").format(
                    percent=new_filter))

            # Air fryer: the cooking state. Only the transitions that
            # carry news are spoken (see
            # VESYNC_FRYER_COOK_ANNOUNCEMENTS) - above all the end of a
            # programme, which nothing else in the add-on reports.
            #
            # old_cook has to be non-empty, as with mode and fan level
            # above: after a login the first complete snapshot turns None
            # into 'cooking', and announcing that would report an
            # appliance as having just started when it has been running
            # for eight minutes.
            old_cook = (old_state.get('cook_status') or "").lower()
            new_cook = (new_state.get('cook_status') or "").lower()
            if (wants_cook and old_cook and new_cook != old_cook
                    and new_cook in VESYNC_FRYER_COOK_ANNOUNCEMENTS):
                changes.append(VESYNC_FRYER_COOK_ANNOUNCEMENTS[new_cook])

            if not changes:
                continue

            # Suppress duplicate announcements in quick succession
            current_time = time.time()
            change_key = f"vesync_{uid}_{','.join(changes)}"
            if (self._last_announced_change == change_key
                    and (current_time - self._last_announced_time) < 2.0):
                continue
            self._last_announced_change = change_key
            self._last_announced_time = current_time

            # Phonetic improvement for the device announcement
            display_name = dev.name.replace("WLAN", "W-LAN")
            change_text = ", ".join(changes)

            # Beep consistent with Meross: BEEP_ON/BEEP_OFF on switching,
            # otherwise BEEP_ACTION
            if on_change:
                beep_const = BEEP_ON if new_state['is_on'] else BEEP_OFF
            else:
                beep_const = BEEP_ACTION
            wx.CallAfter(_beep, beep_const)
            wx.CallAfter(ui.message, f"{display_name}: {change_text}")
            log.info(f"VeSync external change: {dev.name} - {change_text}")

            # LIVE UPDATE: nudge the open dialog so the tree nodes show the new
            # status immediately (analogous to Meross/Netatmo).
            if (on_change and self._active_dialog
                    and hasattr(self._active_dialog, 'update_device_status_live')):
                wx.CallAfter(
                    self._active_dialog.update_device_status_live,
                    uid, new_state['is_on'], None,
                )

    def _record_local_cozytouch_action(self, device_uuid):
        """Remembers the timestamp of a local Cozytouch action.

        Called by the dialog after every successful action so the user's own
        change is not announced as an external change on the next background
        poll (analogous to VeSync).
        """
        self._recent_cozytouch_actions[device_uuid] = time.time()

    def _is_recent_local_cozytouch_action(self, device_uuid, window=30.0):
        """True if a local action happened within 'window' seconds.

        Window set to 30 s because after writecapability the Cozytouch cloud
        often needs a few seconds until the GET status returns the new value
        - the first poll response after that must not count as an external
        change.
        """
        ts = self._recent_cozytouch_actions.get(device_uuid, 0)
        if not ts:
            return False
        if (time.time() - ts) > window:
            self._recent_cozytouch_actions.pop(device_uuid, None)
            return False
        return True

    def _snapshot_cozytouch_state(self, device):
        """Snapshot of the monitored Cozytouch fields."""
        return {
            'target_temp': device.target_temperature,
            'mode': device.mode_value,
            'boost': device.boost_on,
            'is_on': device.is_on,
            'away': getattr(device, 'away_on', None),
        }

    def _cozytouch_boost_runs(self):
        """The running boosts: uuid -> {'start': time, 'minutes': duration}.

        Either may be None - a start the add-on did not see, a duration
        nobody set.
        """
        runs = getattr(self, '_cozytouch_boosts', None)
        if runs is None:
            runs = self._cozytouch_boosts = {}
        return runs

    def _note_cozytouch_boost(self, uuid, prev, new_state, minutes=None):
        """Keeps track of a running boost: since when and for how long.

        The start is known when the poll sees the boost come on, or from the
        dialog that switched it on (_note_local_cozytouch_boost); a boost
        already running at the sign-in has an unknown start, and its end is
        then taken as somebody's doing - nothing else can be said.

        The duration is remembered here because the appliance does not keep
        it. Right after a boost was switched on on 2026-10-02, capability 232
        read "no time limit set", and the history shows a duration set
        seconds after every start since 2026-09-09. Each of those eight
        boosts then ran out to the minute - 30, 60, 62, 65, 70 and 90 - and
        every end was written as "Boost switched off", origin "external": the
        duration was read from the appliance at the poll that saw the boost
        go off, and it was gone by then.
        """
        runs = self._cozytouch_boost_runs()
        if not new_state.get('boost'):
            runs.pop(uuid, None)
            return
        run = runs.get(uuid)
        if run is None:
            began = prev is not None and prev.get('boost') is False
            run = runs[uuid] = {'start': time.time() if began else None,
                                'minutes': None}
        if minutes:
            run['minutes'] = minutes

    def _note_local_cozytouch_boost(self, uuid, on=None, minutes=None):
        """The dialog switched a boost on or off, or set its duration.

        Known better here than by the poll: the start is the moment the
        cloud confirmed it, and the duration the one that was sent - the
        appliance does not report it back. A duration set while no boost
        runs is not taken: whether the appliance keeps it for the next one
        is not known, and a boost that runs without limit and is switched
        off by somebody would otherwise count as having run out.
        """
        runs = self._cozytouch_boost_runs()
        if on is False:
            runs.pop(uuid, None)
            return
        if on:
            run = runs.setdefault(uuid, {'start': None, 'minutes': None})
            run['start'] = time.time()
        run = runs.get(uuid)
        if run is not None and minutes:
            run['minutes'] = minutes

    def _cozytouch_boost_ran_out(self, dev, uuid):
        """True when a boost that just went off had reached its duration."""
        run = self._cozytouch_boost_runs().get(uuid) or {}
        started, minutes = run.get('start'), run.get('minutes')
        if not minutes:
            try:
                minutes = float(getattr(dev, 'boost_total_time', None) or 0)
            except (TypeError, ValueError):
                minutes = 0
        if not started or not minutes or minutes <= 0:
            return False
        return time.time() - started >= minutes * 60 - BOOST_END_TOLERANCE

    def _detect_cozytouch_changes(self, devices):
        """Detects external changes (app/physical) and announces them.

        Compares the current state against the last snapshot - analogous to
        ``_detect_vesync_changes``/``_detect_netatmo_changes``. The history
        is written whatever the announcement switches say; the announcement
        follows them and ``announce_external_changes``. The user's own recent
        actions and duplicate announcements are suppressed.

        A boost that goes off at the end of its duration is the appliance's
        own doing: written as "Boost ended", origin "automatic", and said so.
        """
        from .cozytouch_devices import COZYTOUCH_HEATING_MODE_NAMES, _fmt_temp
        for dev in devices:
            uuid = dev.uuid
            new_state = self._snapshot_cozytouch_state(dev)
            prev = self._previous_cozytouch_states.get(uuid)
            # ALWAYS update the snapshot (even when we do not announce) so no
            # stale comparisons occur on the next poll.
            self._previous_cozytouch_states[uuid] = new_state
            # Before the boost bookkeeping below forgets the start time.
            boost_ended = (prev is not None and bool(prev.get('boost'))
                           and new_state.get('boost') is False
                           and self._cozytouch_boost_ran_out(dev, uuid))
            self._note_cozytouch_boost(uuid, prev, new_state,
                                       getattr(dev, 'boost_total_time', None))
            if prev is None:
                continue  # first observation: only remember, do not announce
            # Do not report the user's own recent action as an external change.
            if self._is_recent_local_cozytouch_action(uuid):
                log.debug(f"Cozytouch change suppressed (local action): {dev.name}")
                continue

            self._log_external_state_changes(
                'cozytouch', dev, prev, new_state,
                system={'boost': 'boost_ended'} if boost_ended else None)

            if not self.announce_external_changes:
                continue

            parts = []
            if (self.notify_cozytouch_power
                    and prev['is_on'] != new_state['is_on']
                    and new_state['is_on'] is not None):
                # Translators: Cozytouch announcement: hot water operation
                # switched on/off.
                parts.append(_("Operation on") if new_state['is_on'] else _("Operation "
                                                                           "off"))
            if (self.notify_cozytouch_temp
                    and prev['target_temp'] != new_state['target_temp']
                    and new_state['target_temp'] is not None):
                # Translators: Cozytouch announcement: new target temperature
                # in degrees.
                parts.append(_("Target temperature {temp} degrees").format(
                    temp=_fmt_temp(new_state['target_temp'])))
            if (self.notify_cozytouch_mode and prev['mode'] != new_state['mode']
                    and new_state['mode'] is not None):
                mode_de = COZYTOUCH_HEATING_MODE_NAMES.get(new_state['mode'], new_state['mode'])
                # Translators: Cozytouch announcement: new heating mode.
                parts.append(_("Mode {mode}").format(mode=mode_de))
            if self.notify_cozytouch_boost and prev['boost'] != new_state['boost']:
                if boost_ended:
                    # Translators: Cozytouch announcement: the boost ran out
                    # after its set duration.
                    parts.append(_("Boost ended"))
                else:
                    # Translators: Cozytouch announcement: boost mode switched
                    # on or off.
                    parts.append(_("Boost on") if new_state['boost'] else _("Boost "
                                                                             "off"))
            if (self.notify_cozytouch_away
                    and prev['away'] != new_state['away']
                    and new_state['away'] is not None):
                # Translators: Cozytouch announcement: away mode switched
                # on/off.
                parts.append(_("Away mode on") if new_state['away'] else _("Away "
                                                                              "mode "
                                                                              "off"))

            if not parts:
                continue

            change_text = ", ".join(parts)
            # Suppress duplicate announcements in quick succession (analogous
            # to VeSync).
            current_time = time.time()
            change_key = f"cozytouch_{uuid}_{change_text}"
            if (self._last_announced_change == change_key
                    and (current_time - self._last_announced_time) < 2.0):
                continue
            self._last_announced_change = change_key
            self._last_announced_time = current_time

            wx.CallAfter(_beep, BEEP_EXTERNAL_CHANGE)
            wx.CallAfter(ui.message, f"{dev.name}: {change_text}")
            log.info(f"Cozytouch change: {dev.name} - {change_text}")

    # ------------------------------------------------------------------
    # Petlibro (feeders and water fountains)
    # ------------------------------------------------------------------
    def _record_local_petlibro_action(self, device_uuid):
        """Remembers a local Petlibro action (same purpose as Cozytouch)."""
        self._recent_petlibro_actions[device_uuid] = time.time()

    def _is_recent_local_petlibro_action(self, device_uuid, window=30.0):
        ts = self._recent_petlibro_actions.get(device_uuid, 0)
        if not ts:
            return False
        if (time.time() - ts) > window:
            self._recent_petlibro_actions.pop(device_uuid, None)
            return False
        return True

    def _snapshot_petlibro_state(self, device):
        """Snapshot of the monitored Petlibro fields."""
        snap = {
            'online': not device.is_offline,
            'light': device.light_on,
            'battery_low': device.battery_low,
            'fault': device.fault,
        }
        if getattr(device, 'is_feeder', False):
            snap.update(
                plan_enabled=device.feeding_plan_enabled,
                food_low=device.food_low,
                desiccant_due=device.desiccant_due,
            )
        if getattr(device, 'is_fountain', False):
            snap.update(
                water_mode=device.water_mode,
                low_water=device.low_water_ml,
                sensing_duration=device.sensing_duration,
                water_low=device.water_low,
                filter_due=device.filter_due,
                cleaning_due=device.cleaning_due,
            )
        return snap

    def _seed_petlibro_state(self, device):
        """Takes the current state as the baseline, without announcing it.

        The work records only the first time: a refresh the user asked for
        must not swallow a feeding that happened since the last poll.
        """
        self._previous_petlibro_states[device.uuid] = \
            self._snapshot_petlibro_state(device)
        if (device.records_loaded_at
                and device.uuid not in self._petlibro_seen_records):
            self._mark_petlibro_records_seen(device)

    def _mark_petlibro_records_seen(self, device, only=None):
        """Counts records as known: all the feeder has now, or ``only`` one.

        After "Feed now" only the record of that feeding. Marking all of
        them swallowed a planned meal that had come in since the last poll
        (2026-09-23: the 12:30 meal never reached the history).
        """
        seen = self._petlibro_seen_records.setdefault(device.uuid, set())
        if only is not None:
            seen.add(only)
            return
        seen.update(r.get('id') for r in device.records)
        if len(seen) > 500:
            # The cloud returns the newest records only; older ids can go.
            self._petlibro_seen_records[device.uuid] = {
                r.get('id') for r in device.records}

    def _detect_petlibro_changes(self, devices):
        """Announces what a Petlibro poll brought.

        Two kinds, kept apart as for the other platforms:

        - Conditions of the device itself (a feeding, a failed feeding, food
          or water running low, maintenance due, offline). They are no
          change anyone made, so the master switch for external changes
          does not silence them - each has a switch of its own.
        - Settings someone changed elsewhere (feeding plan, water mode,
          light, low water threshold). Those follow the master switch, are
          suppressed right after a change made here, and go to the history
          as external actions.
        """
        from .petlibro_devices import ml_text, fault_alert
        from .constants import PETLIBRO_WATER_MODE_NAMES
        for dev in devices:
            uuid = dev.uuid
            new = self._snapshot_petlibro_state(dev)
            prev = self._previous_petlibro_states.get(uuid)
            self._previous_petlibro_states[uuid] = new
            self._detect_petlibro_feedings(dev)
            if prev is None:
                continue
            # A change sent from here that the device has not confirmed yet
            # is answered here - with a confirmation, not as a change made
            # elsewhere. The fields it covers are taken out of the comparison.
            for key in self._check_petlibro_expectations(dev, new):
                prev[key] = new.get(key)

            alerts = []
            # A fault gets the error tone; a warning (food low, maintenance
            # due) the notice tone - "food low" came with the error tone and
            # sounded like something had broken.
            fault = False
            if (prev['online'] != new['online']
                    and getattr(self, 'notify_petlibro_connection', True)):
                # Translators: Petlibro announcement: the device got its
                # connection back.
                alerts.append(_("online again") if new['online'] else _("offline"))
                fault = fault or not new['online']
            if new['online']:
                if new['fault'] and not prev['fault']:
                    message = dev.fault_message
                    # The same event may already have come as a record, or
                    # come as one in the next minutes - said once.
                    if not self._petlibro_fault_said_recently(dev, message):
                        self._log_petlibro_event(dev, 'device_fault', message)
                        if getattr(self, 'notify_petlibro_feed_failed', True):
                            fault = True
                            alerts.append(fault_alert(message))
                # Written to the history whether or not it is announced.
                if new.get('food_low') and not prev.get('food_low'):
                    self._log_petlibro_event(dev, 'food_low')
                    if getattr(self, 'notify_petlibro_food_low', True):
                        # Translators: Petlibro announcement: the food
                        # container of a feeder is almost empty.
                        alerts.append(_("food low"))
                if new.get('water_low') and not prev.get('water_low'):
                    self._log_petlibro_event(dev, 'water_low')
                    if getattr(self, 'notify_petlibro_water_low', True):
                        # Translators: Petlibro announcement: the water of a
                        # fountain is below the warning threshold.
                        alerts.append(_("water low"))
                if getattr(self, 'notify_petlibro_maintenance', True):
                    if new.get('desiccant_due') and not prev.get('desiccant_due'):
                        # Translators: Petlibro announcement: the desiccant of
                        # a feeder should be replaced.
                        alerts.append(_("desiccant due"))
                    if new.get('filter_due') and not prev.get('filter_due'):
                        # Translators: Petlibro announcement: the filter of a
                        # fountain should be replaced.
                        alerts.append(_("filter due"))
                    if new.get('cleaning_due') and not prev.get('cleaning_due'):
                        # Translators: Petlibro announcement: a fountain should
                        # be cleaned.
                        alerts.append(_("cleaning due"))
                if (new['battery_low'] and not prev['battery_low']
                        and getattr(self, 'notify_petlibro_battery', True)):
                    # Translators: Petlibro announcement: the battery is low.
                    alerts.append(_("battery low"))
            if alerts:
                wx.CallAfter(_beep, BEEP_ERROR if fault else BEEP_EXTERNAL_CHANGE)
                wx.CallAfter(ui.message, f"{dev.name}: {', '.join(alerts)}")
                log.info(f"Petlibro condition: {dev.name} - {', '.join(alerts)}")

            if not new['online'] or not prev['online']:
                continue
            if self._is_recent_local_petlibro_action(uuid):
                log.debug(f"Petlibro change suppressed (local action): {dev.name}")
                continue
            parts = []
            if (prev.get('plan_enabled') is not None
                    and new.get('plan_enabled') is not None
                    and prev['plan_enabled'] != new['plan_enabled']):
                # Translators: Petlibro announcement: the feeding plan was
                # switched on or off elsewhere.
                parts.append(_("feeding plan on") if new['plan_enabled']
                             else _("feeding plan off"))
            if (prev.get('water_mode') and new.get('water_mode')
                    and prev['water_mode'] != new['water_mode']):
                # Translators: Petlibro announcement: the water mode of a
                # fountain was changed elsewhere. {mode} = new mode.
                parts.append(_("water mode {mode}").format(
                    mode=PETLIBRO_WATER_MODE_NAMES.get(
                        new['water_mode'], new['water_mode'])))
            if (prev['light'] is not None and new['light'] is not None
                    and prev['light'] != new['light']):
                # Translators: Petlibro announcement: the light was switched
                # on or off elsewhere.
                parts.append(_("light on") if new['light'] else _("light off"))
            if (prev.get('low_water') and new.get('low_water')
                    and prev['low_water'] != new['low_water']):
                # Translators: Petlibro announcement: the low water warning
                # threshold was changed elsewhere. {ml} = e.g. "1100 ml".
                parts.append(_("water warning below {ml}").format(
                    ml=ml_text(new['low_water'])))
            if (prev.get('sensing_duration') and new.get('sensing_duration')
                    and prev['sensing_duration'] != new['sensing_duration']):
                from .petlibro_devices import duration_text
                # Translators: Petlibro announcement: the flow duration of a
                # fountain was changed elsewhere. {duration} = e.g. "60
                # seconds".
                parts.append(_("flow duration {duration}").format(
                    duration=duration_text(new['sensing_duration'])))
            if not parts:
                continue
            # Settings changes are history in any case; whether they are
            # spoken is up to the settings.
            self._log_external_state_changes('petlibro', dev, prev, new)
            if not (self.announce_external_changes
                    and getattr(self, 'notify_petlibro_settings', True)):
                continue
            change_text = ", ".join(parts)
            wx.CallAfter(_beep, BEEP_EXTERNAL_CHANGE)
            wx.CallAfter(ui.message, f"{dev.name}: {change_text}")
            log.info(f"Petlibro external change: {dev.name} - {change_text}")

    def _begin_petlibro_feed(self, device):
        """"Feed now" is running: its record is the dialog's to report."""
        self._petlibro_feed_wait[device.uuid] = {
            'state': 'running', 'until': time.time() + 600}

    def _end_petlibro_feed(self, device, confirmed):
        """"Feed now" is done. Unconfirmed, a record that still comes is the
        late confirmation of that command - announced as such, not as a
        feeding made elsewhere, and not swallowed either."""
        if confirmed:
            self._petlibro_feed_wait.pop(device.uuid, None)
        else:
            self._petlibro_feed_wait[device.uuid] = {
                'state': 'late', 'until': time.time() + 600}

    def _petlibro_feed_state(self, device):
        wait = self._petlibro_feed_wait.get(device.uuid)
        if not wait:
            return None
        if time.time() > wait['until']:
            self._petlibro_feed_wait.pop(device.uuid, None)
            return None
        return wait['state']

    def _detect_petlibro_feedings(self, dev):
        """Announces and records what a device wrote to its work records."""
        if not dev.records_loaded_at:
            return
        if getattr(dev, 'is_fountain', False):
            self._detect_petlibro_fountain_records(dev)
            return
        if not getattr(dev, 'is_feeder', False):
            return
        from .petlibro_devices import (
            feed_from_record, portions_text, record_kind,
        )
        seen = self._petlibro_seen_records.get(dev.uuid)
        if seen is None:
            self._mark_petlibro_records_seen(dev)
            return
        fresh = [r for r in reversed(dev.records) if r.get('id') not in seen]
        self._mark_petlibro_records_seen(dev)
        for record in fresh:
            kind = record_kind(record)
            # Every new record is written to the log: what the feeder calls
            # its records is known from two devices only, and a report of a
            # wrong announcement starts from here.
            log.info(f"Petlibro: new record on {dev.name}: "
                     f"{record.get('type')}/{record.get('eventType')} "
                     f"({kind})")
            if kind == 'fed':
                feed = feed_from_record(record)
                amount = feed.get('portions')
                amount_text = portions_text(amount) if amount is not None else ""
                if feed['manual']:
                    waiting = self._petlibro_feed_state(dev)
                    if waiting == 'running':
                        continue  # "Feed now" is still waiting - it reports
                    if waiting == 'late':
                        # The feeder confirmed our command after the dialog
                        # had given up waiting for it.
                        self._petlibro_feed_wait.pop(dev.uuid, None)
                        try:
                            from .history import get_history
                            get_history().log_action(
                                dev, 'feed_manual',
                                "" if amount is None else str(amount))
                        except Exception as e:
                            log.debug(f"History entry (Petlibro feeding) failed: {e}")
                        wx.CallAfter(_beep, BEEP_ACTION)
                        # Translators: Petlibro announcement: the feeder
                        # confirmed a "Feed now" late, after the add-on had
                        # said it was not confirmed. {amount} = e.g.
                        # "1 portion".
                        wx.CallAfter(ui.message, _("{name}: feeding confirmed after all, {amount} given").format(
                            name=dev.name, amount=amount_text))
                        continue
                    self._log_petlibro_external_feed(dev, amount)
                    if (self.announce_external_changes
                            and getattr(self, 'notify_petlibro_feed_external', True)):
                        wx.CallAfter(_beep, BEEP_EXTERNAL_CHANGE)
                        # Translators: Petlibro announcement: a feeding was
                        # started elsewhere (app, button on the feeder).
                        # {amount} = e.g. "2 portions".
                        wx.CallAfter(ui.message, _("{name}: fed from elsewhere, {amount}").format(
                            name=dev.name, amount=amount_text))
                    continue
                self._log_petlibro_event(dev, 'feed_planned',
                                         "" if amount is None else str(amount))
                expected = feed.get('expected')
                short = (amount is not None and expected is not None
                         and amount < expected)
                if short and getattr(self, 'notify_petlibro_feed_failed', True):
                    wx.CallAfter(_beep, BEEP_ERROR)
                    # Translators: Petlibro announcement: a planned meal gave
                    # less than planned. {amount} = given, {expected} = planned.
                    wx.CallAfter(ui.message, _("{name}: meal gave only {amount} of {expected}").format(
                        name=dev.name, amount=amount_text,
                        expected=portions_text(expected)))
                elif getattr(self, 'notify_petlibro_feed_planned', False):
                    label = dev.plan_label(feed.get('plan_id'))
                    # Translators: Petlibro announcement: a planned meal was
                    # given. {amount} = e.g. "3 portions".
                    text = _("{name}: meal given, {amount}").format(
                        name=dev.name, amount=amount_text)
                    if label:
                        # The meal's own name from the app, as typed there.
                        text = f"{text} ({label})"
                    wx.CallAfter(ui.message, text)
            elif kind == 'failed':
                kind = str(record.get('eventType') or record.get('type') or "")
                self._log_petlibro_event(dev, 'feed_failed', kind)
                log.warning(f"Petlibro: {dev.name} reports {kind}")
                if self._petlibro_feed_state(dev) == 'running':
                    continue  # "Feed now" reports its own failure
                if getattr(self, 'notify_petlibro_feed_failed', True):
                    wx.CallAfter(_beep, BEEP_ERROR)
                    # Translators: Petlibro announcement: the feeder reported a
                    # failed feeding or a blocked outlet.
                    wx.CallAfter(ui.message, _("{name}: feeding failed").format(
                        name=dev.name))

    # Faults a fountain writes to its records, with what to say about them.
    # Known from the PLWF116 (2026-09-23); another one is announced as a
    # fault without the detail.
    _FOUNTAIN_FAULTS = ('WATER_BOX_LEAVE_ERROR',)

    def _detect_petlibro_fountain_records(self, dev):
        """Announces the faults a fountain wrote since the last poll.

        Its drinks are records too; they are not announced - the drinking
        line and its list are where they are read.
        """
        from .petlibro_devices import record_kind, fault_alert
        seen = self._petlibro_seen_records.get(dev.uuid)
        if seen is None:
            self._mark_petlibro_records_seen(dev)
            return
        fresh = [r for r in reversed(dev.records) if r.get('id') not in seen]
        self._mark_petlibro_records_seen(dev)
        for record in fresh:
            kind = record_kind(record)
            code = str(record.get('eventType') or record.get('type') or "")
            log.info(f"Petlibro: new record on {dev.name}: "
                     f"{record.get('type')}/{record.get('eventType')} ({kind})")
            if kind != 'failed' and not (record.get('alarmTag')
                                         and code.upper() in self._FOUNTAIN_FAULTS):
                continue
            if self._petlibro_fault_said_recently(dev, code):
                log.debug(f"Petlibro: {code} on {dev.name} already announced")
                continue
            self._log_petlibro_event(dev, 'device_fault', code)
            if not getattr(self, 'notify_petlibro_feed_failed', True):
                continue
            wx.CallAfter(_beep, BEEP_ERROR)
            wx.CallAfter(ui.message, f"{dev.name}: {fault_alert(code)}")

    def _petlibro_fault_said_recently(self, device, value, window=600.0):
        """True when this fault of this device was reported in the window.

        A fountain reports one event twice: the cloud's fault text arrives
        with the next poll, the record minutes later (four, measured on
        2026-09-26) - and was announced and written to the history a second
        time. Recording here that it was said is part of asking; a fault
        without a name is never counted as known.
        """
        from .petlibro_devices import fault_key
        key = fault_key(value)
        if not key:
            return False
        said = getattr(self, '_petlibro_recent_faults', None)
        if said is None:
            said = self._petlibro_recent_faults = {}
        per_device = said.setdefault(device.uuid, {})
        now = time.time()
        if now - per_device.get(key, 0.0) < window:
            return True
        per_device[key] = now
        return False

    # The settings a change made here can touch. After such a change they
    # become the baseline of the comparison at once: the scheduler polls
    # Petlibro every two minutes in the background, the local-action window
    # is thirty seconds, and the next poll announced the change made here as
    # one made elsewhere (2026-09-26, water mode "off"). The conditions keep
    # their old value, so a fault or low water that came with the same
    # read-back is still announced.
    _PETLIBRO_SETTING_KEYS = ('plan_enabled', 'water_mode', 'light', 'low_water',
                              'sensing_duration')

    def _accept_local_petlibro_change(self, device):
        """Takes the device's current settings as the baseline."""
        prev = self._previous_petlibro_states.get(device.uuid)
        if prev is None:
            return
        now = self._snapshot_petlibro_state(device)
        for key in self._PETLIBRO_SETTING_KEYS:
            if key in now:
                prev[key] = now[key]

    def _log_petlibro_event(self, device, action, detail=""):
        try:
            from .history import get_history, SOURCE_SYSTEM
            get_history().log_action(device, action, detail, source=SOURCE_SYSTEM)
        except Exception as e:
            log.debug(f"History entry (Petlibro {action}) failed: {e}")

    def _log_petlibro_external_feed(self, device, amount):
        try:
            from .history import get_history
            get_history().log_external_action(
                device.unique_id, device.name, 'petlibro', 'feed_manual',
                "" if amount is None else str(amount))
        except Exception as e:
            log.debug(f"History entry (Petlibro feeding) failed: {e}")

    def _expect_petlibro(self, device, key, expected, confirm, fail,
                         history_action=None, detail="", window=600.0):
        """Waits for a change the device has not confirmed yet (see
        petlibro_devices.PetlibroPending). Answered by the next polls:
        ``confirm`` is spoken when the device reports ``expected``, ``fail``
        when ``window`` seconds pass without it. Ten minutes: the cordless
        fountain took two in the one case measured."""
        self._petlibro_expected.setdefault(device.uuid, {})[key] = {
            'expected': expected, 'until': time.time() + window,
            'confirm': confirm, 'fail': fail,
            'history': history_action, 'detail': detail}

    def _check_petlibro_expectations(self, dev, new):
        """Confirms or gives up waiting changes; returns the keys they cover."""
        waiting = self._petlibro_expected.get(dev.uuid)
        if not waiting:
            return set()
        covered = set()
        now = time.time()
        for key, expect in list(waiting.items()):
            covered.add(key)
            if new.get(key) == expect['expected']:
                del waiting[key]
                wx.CallAfter(_beep, BEEP_ACTION)
                wx.CallAfter(ui.message, expect['confirm'])
                log.info(f"Petlibro: {dev.name} took {key}={expect['expected']!r}")
                if expect['history']:
                    try:
                        from .history import get_history
                        get_history().log_action(dev, expect['history'],
                                                 expect['detail'])
                    except Exception as e:
                        log.debug(f"History entry (Petlibro {key}) failed: {e}")
            elif now > expect['until']:
                del waiting[key]
                wx.CallAfter(_beep, BEEP_ERROR)
                wx.CallAfter(ui.message, expect['fail'])
                log.warning(f"Petlibro: {dev.name} did not take "
                            f"{key}={expect['expected']!r}")
        return covered
