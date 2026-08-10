# -*- coding: utf-8 -*-
"""
Smart Home Control - Erkennung externer Geraeteaenderungen (alle Plattformen)
Ausgelagert aus __init__.py (Modul-Aufteilung, Verhalten unverändert).
"""

import time

import wx
import ui
from logHandler import log

import addonHandler
try:
    addonHandler.initTranslation()
except Exception as e:
    log.debug(f"initTranslation fehlgeschlagen: {e}")
if "_" not in globals():  # Fallback, falls initTranslation() scheitert
    # Ohne diesen Fallback bleibt `_` undefiniert und der erste `_()`-Aufruf
    # wirft einen NameError mitten im Dialogaufbau statt beim Import.
    def _(s):
        return s

from .dialog_helpers import _beep
from .constants import (
    CACHE_VALID_DURATION, BOILER_COOLDOWN, NETATMO_MODE_NAMES,
    VESYNC_FILTER_WARN_THRESHOLD, BEEP_EXTERNAL_CHANGE,
    BEEP_ON, BEEP_OFF, BEEP_ACTION,
)


class _ChangeDetectionMixin:
    """Vergleicht Zustands-Snapshots und sagt externe Aenderungen an."""

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
        stale = [key for key, (_, ts) in self._recent_local_toggles.items() if now - ts > window]
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
            msg = _("Meross {name}: Aktualisierung pausiert – Cloud-Limit dieses Geräts erreicht").format(
                name=device_name)
        else:
            # Translators: As above, in case the device name is unavailable.
            msg = _("Ein Meross-Gerät wird vorübergehend seltener aktualisiert – sein Cloud-Limit ist erreicht")
        wx.CallAfter(ui.message, msg)

    def _log_external_change(self, device_uuid, device_name, new_state):
        """Schreibt eine extern ausgelöste Schaltung in den Verlauf.

        Läuft im MQTT-Push-Thread, deshalb bewusst komplett gekapselt: ein
        Fehler beim Protokollieren darf die Ansage nicht verhindern.
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
                'toggle_on' if new_state else 'toggle_off',
                # Translators: Detail column of a switch action in the history.
                _('Ein') if new_state else _('Aus'))
        except Exception as e:
            log.debug(f"Verlaufseintrag (extern) fehlgeschlagen: {e}")

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
        import time
        
        # Do not announce the user's own recent actions twice
        if self._is_recent_local_toggle(device_uuid, new_state):
            log.debug(f"Unterdrücke doppelte Ansage für {device_uuid}")
            return

        # Check whether announcements are enabled
        # - global switch "Announce external changes"
        # - per-platform switch from the "Notifications" tab: Meross toggle
        if not self.announce_external_changes:
            log.debug(f"Externe Änderung ignoriert (Ansagen deaktiviert): {device_name}")
            return
        if not getattr(self, 'notify_meross_toggle', True):
            log.debug(f"Meross-Toggle-Ansage deaktiviert: {device_name}")
            return

        # Prevent duplicate announcements (within 2 seconds)
        current_time = time.time()
        change_key = f"{device_uuid}_{new_state}_{channel_name or ''}"
        if (self._last_announced_change == change_key and 
            (current_time - self._last_announced_time) < 2.0):
            log.debug(f"Doppelte Ansage unterdrückt: {device_name}")
            return
        
        self._last_announced_change = change_key
        self._last_announced_time = current_time

        # Verlauf: externe Schaltungen wurden bisher nur angesagt, aber nie
        # protokolliert - damit konnte der Verlauf ausgerechnet die Frage
        # nicht beantworten, für die man ihn aufschlägt: "war ich das oder
        # jemand anders?". Als Herkunft steht schlicht "extern": ob es die
        # Hersteller-App, ein Sprachassistent oder der Taster am Gerät war,
        # geht aus der Cloud-Benachrichtigung nicht hervor.
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

        # Phonetic improvements
        display_name = device_name.replace("WLAN", "W-LAN")
        # Translators: Status "switched on" for smart home devices (short).
        status = _("ein") if new_state else _("aus")

        # Accessible announcement via wx.CallAfter (thread-safe)
        wx.CallAfter(_beep, BEEP_ON if new_state else BEEP_OFF)
        wx.CallAfter(ui.message, f"{display_name}: {status}")
        
        # LIVE UPDATE: update the open dialog if present
        if self._active_dialog and hasattr(self._active_dialog, 'update_device_status_live'):
            # Also pass channel_name for multi-channel devices
            wx.CallAfter(self._active_dialog.update_device_status_live, device_uuid, new_state, channel_name)
        
        log.info(f"Externe Änderung angesagt: {device_name} -> {status}")
    
    def is_cache_fresh(self):
        """Checks whether the device cache is still fresh"""
        import time
        if not self.devices or self._last_refresh_time == 0:
            return False
        return (time.time() - self._last_refresh_time) < CACHE_VALID_DURATION
    
    def _normalize_end_time(self, et):
        """Normalizes end_time: None, 0 and expired timestamps become None"""
        if not et or et <= time.time():
            return None
        return et

    def _detect_netatmo_changes(self, new_devices):
        """
        Detects external changes on Netatmo thermostats and issues push notifications.
        Compares the current state with the previous one and reports differences.

        Normalization against false notifications:
        - end_time: None/0/expired are treated the same
        - zone_name: on an API error (None) the previous value is kept
        - boiler: pure boiler toggles are only reported every 5 minutes (normal heating cycles)
        - setpoint: compared rounded to 0.1°C
        """
        if not self.announce_external_changes:
            return
        
        for dev in new_devices:
            if not getattr(dev, 'is_thermostat', False):
                continue
            
            uid = dev.uuid
            
            # Normalize the values
            raw_end_time = dev._therm_setpoint_end_time
            normalized_end_time = self._normalize_end_time(raw_end_time)
            raw_setpoint = dev._therm_setpoint
            normalized_setpoint = round(raw_setpoint, 1) if raw_setpoint is not None else None
            new_zone = getattr(dev, '_schedule_zone_name', None)
            
            old_state = self._previous_netatmo_therm_states.get(uid)
            
            # Zone name stabilization: if new_zone is None (API error), keep
            # the previous value instead of triggering a false change
            if new_zone is None and old_state is not None and old_state.get('zone_name'):
                new_zone = old_state['zone_name']
            
            new_state = {
                'setpoint': normalized_setpoint,
                'mode': dev._therm_setpoint_mode,
                'end_time': normalized_end_time,
                'zone_name': new_zone,
                'boiler': getattr(dev, '_boiler_status', None),
                'anticipating': getattr(dev, '_anticipating', None),
                'open_window': getattr(dev, '_open_window', None),
            }
            
            # FIX: state stabilization against API inconsistency. The Netatmo
            # API does not deliver fields like anticipating, open_window,
            # boiler_status, therm_setpoint_temperature etc. on every call. New
            # NetatmoDevice objects then have None as the default. Without this
            # stabilization a flip-flop occurs:
            # cycle A: API delivers the field -> value stored
            # cycle B: API does NOT deliver the field -> None stored
            # cycle C: API delivers the field again -> false-positive "change"!
            # Solution: if the new value is None and an old value exists, keep
            # the old value (the API just did not include it).
            if old_state is not None:
                for key in new_state:
                    if new_state[key] is None and old_state.get(key) is not None:
                        new_state[key] = old_state[key]
            
            # Boolean normalization: None and False are semantically identical
            # for boolean fields ("not active"). Prevents residual cases.
            for bool_key in ('anticipating', 'open_window'):
                if new_state[bool_key] is None:
                    new_state[bool_key] = False
            
            if old_state is not None:
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
                if wants_setpoint:
                    if new_sp is not None and old_sp is not None and abs(old_sp - new_sp) >= 0.05:
                        # Translators: Netatmo thermostat announcement: new
                        # target temperature in °C.
                        changes.append(_("Soll-Temperatur: {temp:.1f}°C").format(temp=new_sp))
                    elif new_sp is not None and old_sp is None:
                        changes.append(_("Soll-Temperatur: {temp:.1f}°C").format(temp=new_sp))

                # Mode changed
                if wants_mode and old_state.get('mode') != new_state.get('mode') and new_state.get('mode') is not None:
                    mode_text = NETATMO_MODE_NAMES.get(new_state['mode'], new_state['mode'])
                    # For schedule mode also show the active zone name
                    if new_state['mode'] == 'schedule' and new_state.get('zone_name'):
                        mode_text += f" ({new_state['zone_name']})"
                    # Translators: Netatmo thermostat announcement: new heating
                    # mode (e.g. schedule, manual, away).
                    changes.append(_("Heizmodus: {mode}").format(mode=mode_text))

                # Schedule zone changed (even if the mode stays the same)
                if (wants_mode
                        and old_state.get('mode') == new_state.get('mode') == 'schedule'
                        and old_state.get('zone_name') != new_state.get('zone_name')
                        and new_state.get('zone_name')):
                    # Translators: Netatmo thermostat announcement: the
                    # schedule switched to a different zone (e.g. day ->
                    # night).
                    changes.append(_("Zeitplan-Zone: {zone}").format(zone=new_state['zone_name']))

                # End time changed (normalized) - tied to the setpoint category
                old_et = old_state.get('end_time')
                new_et = new_state.get('end_time')
                if wants_setpoint and old_et != new_et:
                    if new_et and new_et > time.time():
                        end_str = time.strftime("%H:%M", time.localtime(new_et))
                        # Translators: Netatmo thermostat announcement: until
                        # when the manual target temperature applies (HH:MM).
                        changes.append(_("bis {time} Uhr").format(time=end_str))
                    elif old_et and not new_et:
                        # Translators: Netatmo thermostat announcement: the
                        # time limit of a manual setting was removed.
                        changes.append(_("Endzeit entfernt"))

                # Boiler status changed (heating on/off). Pure boiler toggles
                # (without other changes) are only reported every 5 min
                boiler_changed = (old_state.get('boiler') != new_state.get('boiler')
                                  and new_state.get('boiler') is not None)
                if boiler_changed and wants_boiler:
                    # Translators: Netatmo thermostat announcement: the boiler
                    # is currently heating.
                    boiler_text = _("Heizung aktiv") if new_state['boiler'] else _("Heizung aus")
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
                            log.debug(f"Netatmo Boiler-Toggle unterdrückt (Cooldown): {dev.name}")

                # Pre-heating (anticipation) changed
                old_antic = old_state.get('anticipating')
                new_antic = new_state.get('anticipating')
                if wants_anticipation and old_antic != new_antic and new_antic is not None:
                    if new_antic:
                        # Translators: Netatmo announcement: the thermostat is
                        # pre-heating for an upcoming target temperature
                        # increase.
                        changes.append(_("Vorausheizen gestartet"))
                    else:
                        # Translators: Netatmo announcement: pre-heating has
                        # finished.
                        changes.append(_("Vorausheizen beendet"))

                # Open window changed
                old_ow = old_state.get('open_window')
                new_ow = new_state.get('open_window')
                if wants_open_window and old_ow != new_ow and new_ow is not None:
                    if new_ow:
                        # Translators: Netatmo announcement: the thermostat
                        # detected an open window (sudden temperature drop).
                        changes.append(_("Offenes Fenster erkannt"))
                    else:
                        # Translators: Netatmo announcement: the open window
                        # was closed.
                        changes.append(_("Fenster geschlossen"))

                if changes:
                    change_text = ", ".join(changes)
                    wx.CallAfter(_beep, BEEP_EXTERNAL_CHANGE)
                    wx.CallAfter(ui.message, f"{dev.name}: {change_text}")
                    log.info(f"Netatmo externe Änderung: {dev.name} - {change_text}")
                    # Also refresh the boiler cooldown on combined changes
                    if boiler_changed:
                        self._last_boiler_announce_time[uid] = time.time()
            
            # Store the current (normalized) state
            self._previous_netatmo_therm_states[uid] = new_state

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
            VESYNC_FILTER_WARN_THRESHOLD,
        )

        for dev in devices:
            uid = dev.uuid
            new_state = self._snapshot_vesync_state(dev)
            old_state = self._previous_vesync_states.get(uid)

            # ALWAYS remember the current state (even when we do not announce)
            # so no stale comparisons occur on the next refresh.
            self._previous_vesync_states[uid] = new_state

            if old_state is None:
                continue  # first snapshot - no announcement
            if not self.announce_external_changes:
                continue
            # Deliberately do NOT override the window: the 30 s default of
            # _is_recent_local_vesync_action covers both the foreground poll
            # interval (15 s) and the 30-60 s cached bypassV2 cloud response.
            # Previously window=5.0 stood here - which caused the user's own
            # action (e.g. turning the fan off in the dialog) to be falsely
            # announced as an external change after a few seconds, because the
            # confirming poll lay outside the 5 s. Applies to ALL VeSync device
            # types, since the detection runs centrally here.
            if self._is_recent_local_vesync_action(uid):
                log.debug(f"VeSync Änderung unterdrückt (lokale Aktion): {dev.name}")
                continue

            cls_name = type(dev).__name__
            mode_map = (VESYNC_PURIFIER_MODE_NAMES if cls_name == 'VeSyncPurifier'
                        else VESYNC_FAN_MODE_NAMES)

            # Per-event switches: maintained in the "Notifications" tab (see
            # settings_panel._create_notifications_tab).
            wants_toggle = getattr(self, 'notify_vesync_toggle', True)
            wants_mode = getattr(self, 'notify_vesync_mode', True)
            wants_fan_speed = getattr(self, 'notify_vesync_fan_speed', True)
            wants_air_quality = getattr(self, 'notify_vesync_air_quality', True)
            wants_filter = getattr(self, 'notify_vesync_filter', True)

            changes = []
            on_change = False

            # On/off
            if old_state['is_on'] != new_state['is_on']:
                if wants_toggle:
                    # Translators: Status of a VeSync device (switched on /
                    # switched off).
                    changes.append(_("ein") if new_state['is_on'] else _("aus"))
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
                changes.append(_("Modus: {mode}").format(mode=mode_label))

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
            #
            # Tower fans have "normal" as the counterpart to the purifier's
            # manual mode.
            manual_mode_active = (
                (cls_name == 'VeSyncPurifier' and new_state['mode'] == 'manual')
                or (cls_name == 'VeSyncTowerFan' and new_state['mode'] == 'normal')
            )
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
                level_text = _("Stufe {level}").format(level=level_value)
                if cls_name == 'VeSyncPurifier':
                    fan_levels = list(getattr(dev, 'fan_levels', []) or [])
                    if (len(fan_levels) == 3
                            and level_value in VESYNC_PURIFIER_LEVEL_LABELS_3):
                        # Translators: VeSync fan level announcement with a
                        # descriptive name (e.g. "level 1 (low)") for models
                        # with 3 levels.
                        level_text = _("Stufe {level} ({label})").format(
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
                changes.append(_("Oszillation ein") if new_state['oscillation_on']
                               else _("Oszillation aus"))

            # Mute (mode category)
            if (old_state['mute_on'] is not None
                    and old_state['mute_on'] != new_state['mute_on']
                    and new_state['mute_on'] is not None
                    and wants_mode):
                # Translators: VeSync announcement: sounds (the device's
                # beeps).
                changes.append(_("stumm") if new_state['mute_on']
                               else _("Tonsignale ein"))

            # Display (mode category)
            if (old_state['display_on'] is not None
                    and old_state['display_on'] != new_state['display_on']
                    and new_state['display_on'] is not None
                    and wants_mode):
                # Translators: VeSync announcement: LED display on the device.
                changes.append(_("Display ein") if new_state['display_on']
                               else _("Display aus"))

            # Child lock (mode category)
            if (old_state['child_lock'] is not None
                    and old_state['child_lock'] != new_state['child_lock']
                    and new_state['child_lock'] is not None
                    and wants_mode):
                # Translators: VeSync announcement: the child lock locks the
                # buttons.
                changes.append(_("Kindersicherung ein") if new_state['child_lock']
                               else _("Kindersicherung aus"))

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
                changes.append(_("Nachtlicht {mode}").format(mode=nl))

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
                changes.append(_("Auto-Profil {profile}").format(profile=ap))

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
                changes.append(_("Luftqualität {level}").format(level=aq))

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
                changes.append(_("Filter bald wechseln, Restlebensdauer {percent} Prozent").format(
                    percent=new_filter))

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
            log.info(f"VeSync externe Änderung: {dev.name} - {change_text}")

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

    def _detect_cozytouch_changes(self, devices):
        """Detects external changes (app/physical) and announces them.

        Compares the current state against the last snapshot - analogous to
        ``_detect_vesync_changes``/``_detect_netatmo_changes``. Respects the
        notification settings, ``announce_external_changes``, suppresses the
        user's own recent actions and duplicate announcements.
        """
        from .cozytouch_devices import COZYTOUCH_HEATING_MODE_NAMES, _fmt_temp
        for dev in devices:
            uuid = dev.uuid
            new_state = self._snapshot_cozytouch_state(dev)
            prev = self._previous_cozytouch_states.get(uuid)
            # ALWAYS update the snapshot (even when we do not announce) so no
            # stale comparisons occur on the next poll.
            self._previous_cozytouch_states[uuid] = new_state
            if prev is None:
                continue  # first observation: only remember, do not announce
            if not self.announce_external_changes:
                continue
            # Do not report the user's own recent action as an external change.
            if self._is_recent_local_cozytouch_action(uuid):
                log.debug(f"Cozytouch Änderung unterdrückt (lokale Aktion): {dev.name}")
                continue

            parts = []
            if (self.notify_cozytouch_power
                    and prev['is_on'] != new_state['is_on']
                    and new_state['is_on'] is not None):
                # Translators: Cozytouch announcement: hot water operation
                # switched on/off.
                parts.append(_("Betrieb ein") if new_state['is_on'] else _("Betrieb aus"))
            if (self.notify_cozytouch_temp
                    and prev['target_temp'] != new_state['target_temp']
                    and new_state['target_temp'] is not None):
                # Translators: Cozytouch announcement: new target temperature
                # in degrees.
                parts.append(_("Zieltemperatur {temp} Grad").format(
                    temp=_fmt_temp(new_state['target_temp'])))
            if self.notify_cozytouch_mode and prev['mode'] != new_state['mode']:
                mode_de = COZYTOUCH_HEATING_MODE_NAMES.get(new_state['mode'], new_state['mode'])
                # Translators: Cozytouch announcement: new heating mode.
                parts.append(_("Modus {mode}").format(mode=mode_de))
            if self.notify_cozytouch_boost and prev['boost'] != new_state['boost']:
                # Translators: Cozytouch announcement: boost mode switched on
                # or off.
                parts.append(_("Boost ein") if new_state['boost'] else _("Boost aus"))
            if (self.notify_cozytouch_away
                    and prev['away'] != new_state['away']
                    and new_state['away'] is not None):
                # Translators: Cozytouch announcement: away mode switched
                # on/off.
                parts.append(_("Abwesenheit ein") if new_state['away'] else _("Abwesenheit aus"))

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
            log.info(f"Cozytouch externe Änderung: {dev.name} - {change_text}")

