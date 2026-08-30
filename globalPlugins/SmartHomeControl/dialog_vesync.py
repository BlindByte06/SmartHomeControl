# -*- coding: utf-8 -*-
"""Smart Home Control - VeSync/Levoit-specific dialog methods (mixin)."""

import wx
import ui
import threading
from logHandler import log as _nvda_log

import addonHandler
try:
    addonHandler.initTranslation()
except Exception as e:
    _nvda_log.debug(f"initTranslation failed: {e}")
if "_" not in globals():  # fallback if initTranslation() fails
    # Without this fallback `_` stays undefined and the first `_()` call
    # raises a NameError mid-dialog instead of at import time.
    def _(s):
        return s

from .constants import (
    VESYNC_PURIFIER_MODE_NAMES, VESYNC_FAN_MODE_NAMES,
    VESYNC_AIR_QUALITY_NAMES, VESYNC_NIGHTLIGHT_MODE_NAMES, VESYNC_AUTO_PREFERENCE_NAMES,
    VESYNC_PURIFIER_LEVEL_LABELS_3, BEEP_ON,
    BEEP_OFF,
    BEEP_ACTION, BEEP_ERROR,
)
from .history import get_history
from .favorites import get_favorites
from .dialog_helpers import _beep, _vesync_purifier_level_label

log = _nvda_log


class _VeSyncDialogMixin:
    """VeSync methods for SmartHomeControlDialog (air purifiers, fans)."""

    def _handle_vesync_toggle(self, device, item):
        """Switches a VeSync device on or off"""
        try:
            new_state = not device.is_on
            device.toggle_switch(new_state)
            _beep(BEEP_ON if new_state else BEEP_OFF)
            status = _("on") if new_state else _("off")
            ui.message(_("{name}: {status}").format(name=device.name, status=status))
            self.plugin._record_local_toggle(device.uuid, new_state)
            self.plugin._record_local_vesync_action(device.uuid)
            get_history().log_action(
                device, 'toggle_on' if new_state else 'toggle_off', ""
            )
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync toggle error: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync error: {error}").format(error=str(e)[:80]))

    def _vesync_choose_from_list(self, device, title, prompt,
                                  values, label_map, current_value):
        """Opens a selection dialog (analogous to Netatmo) and returns the
        chosen value (or None if cancelled).
        """
        if not values:
            # Translators: Message when a selection list has no options.
            ui.message(_("No selection available"))
            return None
        labels = [label_map.get(v, str(v)) for v in values]
        dlg = wx.SingleChoiceDialog(self, prompt, title, labels)
        try:
            dlg.SetSelection(values.index(current_value))
        except (ValueError, TypeError):
            pass
        chosen = None
        # Suppress live updates of the main tree during the modal selection
        # (otherwise a background refresh running in parallel corrupts the tree
        # items, which NVDA answers with 'BrokenCommctrl5Item').
        previous = getattr(self, '_suppress_live_updates', False)
        self._suppress_live_updates = True
        try:
            if dlg.ShowModal() == wx.ID_OK:
                idx = dlg.GetSelection()
                if 0 <= idx < len(values):
                    chosen = values[idx]
        finally:
            self._suppress_live_updates = previous
            dlg.Destroy()
        return chosen

    def _handle_vesync_mode(self, device, item):
        """Lets the user choose an operating mode (auto/manual/sleep)"""
        cls_name = type(device).__name__
        if cls_name == 'VeSyncPurifier':
            available_modes = list(getattr(device, 'modes', []))
            label_map = VESYNC_PURIFIER_MODE_NAMES
        else:
            available_modes = list(getattr(device, 'modes', {}).keys())
            label_map = VESYNC_FAN_MODE_NAMES

        chosen = self._vesync_choose_from_list(
            device,
            # Translators: Title of the mode selection dialog. {name} = device
            # name.
            _("{name}: choose mode").format(name=device.name),
            # Translators: Prompt in the mode selection dialog.
            _("Choose the operating mode:"),
            available_modes, label_map, device.mode,
        )
        if chosen is None:
            return
        try:
            device.set_mode(chosen)
            mode_de = label_map.get(chosen, chosen)
            _beep(BEEP_ACTION)
            # Translators: Confirmation after a mode change. {mode} = mode
            # name.
            ui.message(_("{name}: mode {mode}").format(name=device.name, mode=mode_de))
            self.plugin._record_local_vesync_action(device.uuid)
            # Mode key, not its label - the display translates it.
            get_history().log_action(device, 'set_mode', str(chosen))
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync mode error: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync error: {error}").format(error=str(e)[:80]))

    def _handle_vesync_fan_speed(self, device, item):
        """Lets the user choose a fan speed"""
        levels = list(getattr(device, 'fan_levels', []))
        if not levels:
            # Translators: Message when the device has no selectable fan
            # levels.
            ui.message(_("No fan speed available"))
            return
        # For Core 200S/300S (3 levels) append the plain text "low/medium/high"
        cls_name = type(device).__name__
        if cls_name == 'VeSyncPurifier' and len(levels) == 3:
            label_map = {
                # Translators: List entry of a fan level with plain-text
                # suffix.
                lvl: _("Level {level} – {label}").format(
                    level=lvl, label=VESYNC_PURIFIER_LEVEL_LABELS_3[lvl])
                for lvl in levels if lvl in VESYNC_PURIFIER_LEVEL_LABELS_3
            }
        else:
            # Translators: List entry of a fan level.
            label_map = {lvl: _("Level {level}").format(level=lvl) for lvl in levels}
        chosen = self._vesync_choose_from_list(
            device,
            # Translators: Title of the fan level dialog. {name} = device name.
            _("{name}: fan speed").format(name=device.name),
            # Translators: Prompt in the fan level dialog.
            _("Choose the fan speed:"),
            levels, label_map, device.fan_level,
        )
        if chosen is None:
            return
        try:
            device.set_fan_speed(chosen)
            _beep(BEEP_ACTION)
            # Translators: Confirmation after a level change. {level} = fan
            # level.
            ui.message(_("{name}: level {level}").format(name=device.name, level=chosen))
            self.plugin._record_local_vesync_action(device.uuid)
            # Bare level - the display renders "Level N" in the current
            # language.
            get_history().log_action(device, 'set_fan_speed', str(chosen))
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync fan error: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync error: {error}").format(error=str(e)[:80]))

    def _handle_vesync_oscillation(self, device, item):
        """Toggles the oscillation of a tower fan"""
        try:
            new_state = not bool(device.oscillation_on)
            device.toggle_oscillation(new_state)
            _beep(BEEP_ACTION)
            status = _("on") if new_state else _("off")
            # Translators: Confirmation after toggling oscillation.
            ui.message(_("{name}: oscillation {status}").format(name=device.name, status=status))
            self.plugin._record_local_vesync_action(device.uuid)
            get_history().log_action(
                device, 'oscillation_on' if new_state else 'oscillation_off', "")
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync oscillation error: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync error: {error}").format(error=str(e)[:80]))

    def _handle_vesync_mute(self, device, item):
        """Toggles the mute of a tower fan"""
        try:
            new_state = not bool(device.mute_on)
            device.toggle_mute(new_state)
            _beep(BEEP_ACTION)
            # Translators: Status after toggling mute.
            status = _("muted") if new_state else _("Sounds on")
            ui.message(_("{name}: {status}").format(name=device.name, status=status))
            self.plugin._record_local_vesync_action(device.uuid)
            get_history().log_action(
                device, 'mute_on' if new_state else 'mute_off', "")
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync mute error: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync error: {error}").format(error=str(e)[:80]))

    def _handle_vesync_display(self, device, item):
        """Toggles the display of a VeSync device"""
        try:
            current = device.display_set_on
            if current is None:
                current = device.display_on
            new_state = not bool(current)
            device.toggle_display(new_state)
            _beep(BEEP_ACTION)
            status = _("on") if new_state else _("off")
            # Translators: Confirmation after toggling the device display.
            ui.message(_("{name}: display {status}").format(name=device.name, status=status))
            self.plugin._record_local_vesync_action(device.uuid)
            get_history().log_action(
                device, 'display_on' if new_state else 'display_off', "")
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync display error: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync error: {error}").format(error=str(e)[:80]))

    def _handle_vesync_child_lock(self, device, item):
        """Toggles the child lock of an air purifier"""
        try:
            new_state = not bool(device.child_lock)
            device.toggle_child_lock(new_state)
            _beep(BEEP_ACTION)
            status = _("on") if new_state else _("off")
            # Translators: Confirmation after toggling the child lock.
            ui.message(_("{name}: child lock {status}").format(name=device.name, status=status))
            self.plugin._record_local_vesync_action(device.uuid)
            get_history().log_action(
                device, 'child_lock_on' if new_state else 'child_lock_off', "")
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync child lock error: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync error: {error}").format(error=str(e)[:80]))

    def _handle_vesync_nightlight(self, device, item):
        """Lets the user choose the night light mode"""
        modes = list(getattr(device, 'nightlight_modes', []))
        chosen = self._vesync_choose_from_list(
            device,
            # Translators: Title of the night light dialog. {name} = device
            # name.
            _("{name}: night light").format(name=device.name),
            # Translators: Prompt in the night light dialog.
            _("Choose the night light mode:"),
            modes, VESYNC_NIGHTLIGHT_MODE_NAMES, device.nightlight_status,
        )
        if chosen is None:
            return
        try:
            device.set_nightlight_mode(chosen)
            label = VESYNC_NIGHTLIGHT_MODE_NAMES.get(chosen, chosen)
            _beep(BEEP_ACTION)
            # Translators: Confirmation after a night light change. {mode} =
            # mode name.
            ui.message(_("{name}: night light {mode}").format(name=device.name, mode=label))
            self.plugin._record_local_vesync_action(device.uuid)
            get_history().log_action(device, 'set_nightlight', str(chosen))
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync night light error: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync error: {error}").format(error=str(e)[:80]))

    def _handle_vesync_auto_preference(self, device, item):
        """Lets the user choose an auto profile (default/efficient/quiet)"""
        prefs = list(getattr(device, 'auto_preferences', []))
        chosen = self._vesync_choose_from_list(
            device,
            # Translators: Title of the auto profile dialog. {name} = device
            # name.
            _("{name}: auto profile").format(name=device.name),
            # Translators: Prompt in the auto profile dialog.
            _("Choose the auto profile:"),
            prefs, VESYNC_AUTO_PREFERENCE_NAMES, device.auto_preference_type,
        )
        if chosen is None:
            return
        try:
            device.set_auto_preference(chosen)
            label = VESYNC_AUTO_PREFERENCE_NAMES.get(chosen, chosen)
            _beep(BEEP_ACTION)
            # Translators: Confirmation after an auto profile change. {profile}
            # = profile name.
            ui.message(_("{name}: auto profile {profile}").format(name=device.name, profile=label))
            self.plugin._record_local_vesync_action(device.uuid)
            get_history().log_action(device, 'set_auto_preference', str(chosen))
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync auto profile error: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync error: {error}").format(error=str(e)[:80]))

    def _handle_vesync_reset_filter(self, device, item):
        """Resets the filter status to 100% after confirmation.

        Double safety prompt: first an explanatory dialog, then a second
        "Are you really sure?" dialog. Only then is the API called, because
        the reset cannot be undone and only makes sense when the filter was
        physically replaced.
        """
        # Step 1: explanation & confirmation
        confirm = wx.MessageDialog(
            self,
            # Translators: First safety prompt before the filter reset. {name}
            # = device name.
            _("Reset the filter life of {name} to 100%?\n\nThis only makes "
              "sense if the HEPA filter has actually just been replaced with "
              "a new one. Resetting without a filter change means the device "
              "no longer shows a filter reminder.").format(name=device.name),
            # Translators: Title of the filter reset dialog.
            _("Reset filter life"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
        )
        # Translators: Button labels of the first safety prompt.
        confirm.SetYesNoLabels(_("&Next"), _("&Cancel"))
        previous_suppress = getattr(self, '_suppress_live_updates', False)
        self._suppress_live_updates = True
        try:
            try:
                result = confirm.ShowModal()
            finally:
                confirm.Destroy()
            if result != wx.ID_YES:
                # Translators: Message when the user cancels an action.
                ui.message(_("Cancelled"))
                return
            # Step 2: final safety prompt
            final_confirm = wx.MessageDialog(
                self,
                # Translators: Second safety prompt before the filter reset.
                # {name} = device name.
                _("Really reset the filter life of {name} to 100%?\n\nThis "
                  "cannot be undone.").format(name=device.name),
                # Translators: Title of the second safety prompt.
                _("Confirmation"),
                wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
            )
            # Translators: Button labels of the second safety prompt.
            final_confirm.SetYesNoLabels(_("&Yes, reset"), _("&Cancel"))
            try:
                result2 = final_confirm.ShowModal()
            finally:
                final_confirm.Destroy()
            if result2 != wx.ID_YES:
                # Translators: Message when the user cancels an action.
                ui.message(_("Cancelled"))
                return
        finally:
            self._suppress_live_updates = previous_suppress
        try:
            device.reset_filter()
            _beep(BEEP_ACTION)
            # Translators: Confirmation after a successful filter reset.
            ui.message(_("{name}: filter life reset").format(name=device.name))
            self.plugin._record_local_vesync_action(device.uuid)
            # Translators: History detail: filter life reset.
            get_history().log_action(device, 'reset_filter', "")
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync filter reset error: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync error: {error}").format(error=str(e)[:80]))

    def _ask_number(self, title, prompt, low, high, preset=None):
        """Asks for a whole number in a range. None when cancelled.

        A plain text entry rather than a spin control on purpose: a spin
        control is read as its current value on every arrow key, and
        stepping from 80 to 200 degrees that way is a long listen. Typing
        the number and hearing it back is shorter.
        """
        default = '' if preset is None else str(preset)
        previous = getattr(self, '_suppress_live_updates', False)
        self._suppress_live_updates = True
        try:
            while True:
                dlg = wx.TextEntryDialog(self, prompt, title, default)
                try:
                    if dlg.ShowModal() != wx.ID_OK:
                        return None
                    raw = dlg.GetValue().strip()
                finally:
                    dlg.Destroy()
                try:
                    value = int(raw)
                except (TypeError, ValueError):
                    value = None
                if value is not None and low <= value <= high:
                    return value
                default = raw
                # Translators: Message after an entry outside the permitted
                # range. {low}/{high} = the permitted values.
                ui.message(_("Please enter a whole number between {low} and "
                             "{high}").format(low=low, high=high))
        finally:
            self._suppress_live_updates = previous

    def _handle_vesync_start_cook(self, device, item):
        """Starts a cooking programme, after confirmation.

        One way in: a programme the appliance has shown us before. Its
        temperature and its time can still be changed on the way, which is
        the one moment this appliance does accept a temperature.

        There used to be a second way, a free start with a temperature and
        a time of one's own, and it never worked: sent as mode "custom"
        with recipe id 1 - the shape the open documentation gives for a
        manual cook - the appliance refused it with the cloud's code
        11000000. It is gone rather than left in, because it sat at the end
        of four dialogs and produced an error every time.
        """
        modes = device.known_programmes()
        if not modes:
            # Nothing to offer, and nothing worth inventing. The free start
            # used to cover exactly this case and is refused by the
            # appliance, so an empty list plus an explanation is the honest
            # answer.
            #
            # Translators: Message when a cooking programme is to be
            # started but the appliance has not reported one yet.
            ui.message(_("No programme has been reported by this appliance "
                         "yet. Selecting one on the appliance itself teaches "
                         "it to the add-on."))
            return
        values = list(modes)
        labels = {m: device.programme_display_for(m) for m in modes}
        # Translators: Prompt of the programme list. {count} = how many
        # programmes the appliance has shown so far.
        prompt = _("Which programme? The appliance has reported {count} "
                   "so far.").format(count=len(modes))
        chosen = self._vesync_choose_from_list(
            device,
            # Translators: Title of the dialog that starts a cooking
            # programme.
            _("Start programme"), prompt, values, labels,
            values[0] if values else None)
        if chosen is None:
            # Translators: Message when the user cancels an action.
            ui.message(_("Cancelled"))
            return

        low, high = device.temperature_range()
        details = device.programme_details(chosen)
        mode = chosen
        preset_temp = (details or {}).get('cook_temp')
        preset_time = (details or {}).get('cook_set_time')
        programme_label = labels.get(chosen, chosen)

        # A programme whose settings are known can be started from here in
        # one more keystroke. Asking for a temperature and a time that are
        # already right turned "the usual vegetables" into four dialogs,
        # three of which only had to be confirmed unchanged.
        if preset_temp is not None and preset_time is not None:
            preset_minutes = max(1, int(preset_time) // 60)
            decision = self._confirm_start(
                device, programme_label, preset_temp, preset_minutes,
                offer_change=True)
            if decision == 'cancel':
                # Translators: Message when the user cancels an action.
                ui.message(_("Cancelled"))
                return
            if decision == 'start':
                self._send_start_cook(device, item, mode, programme_label,
                                      preset_temp, preset_minutes * 60,
                                      details)
                return
            # 'change' falls through to the two entries below, prefilled.

        temperature = self._ask_number(
            # Translators: Title of the temperature entry.
            _("Temperature"),
            # Translators: Prompt of the temperature entry. {low}/{high} =
            # the permitted values.
            _("Temperature between {low} and {high}:").format(low=low, high=high),
            low, high, preset_temp)
        if temperature is None:
            # Translators: Message when the user cancels an action.
            ui.message(_("Cancelled"))
            return

        min_s, max_s = device.TIME_RANGE_SECONDS
        minutes = self._ask_number(
            # Translators: Title of the cooking time entry.
            _("Cooking time"),
            # Translators: Prompt of the cooking time entry. {low}/{high} =
            # the permitted values in minutes.
            _("Time in minutes, between {low} and {high}:").format(
                low=min_s // 60, high=max_s // 60),
            min_s // 60, max_s // 60,
            None if preset_time is None else max(1, int(preset_time) // 60))
        if minutes is None:
            # Translators: Message when the user cancels an action.
            ui.message(_("Cancelled"))
            return

        if self._confirm_start(device, programme_label, temperature, minutes,
                               offer_change=False) != 'start':
            # Translators: Message when the user cancels an action.
            ui.message(_("Cancelled"))
            return
        self._send_start_cook(device, item, mode, programme_label,
                              temperature, minutes * 60, details)

    def _confirm_start(self, device, programme_label, temperature, minutes,
                       offer_change):
        """Asks before a programme is sent. 'start', 'change' or 'cancel'.

        ``offer_change`` adds the third button, used when the settings came
        from the programme itself and may want adjusting.
        """
        message = (
            # Translators: Safety prompt before a cooking programme is
            # started. {name} = device name, {programme} = programme name,
            # {temperature} = set temperature with unit, {minutes} =
            # duration in minutes.
            #
            # Deliberately no promise about heating: Cosori appliances
            # comply with a safety standard that forbids switching them on
            # remotely, and whether this model begins by itself or waits
            # for its own start button is not established. The programme
            # state in the tree answers that within one poll.
            # Translators: Question before a cooking programme is started, with
            # the programme, the appliance, the temperature and the time.
            _("Start {programme} on {name}?\n\n"
              "{temperature} for {minutes} minutes.").format(
                name=device.name, programme=programme_label,
                temperature=device._format_temperature(temperature),
                minutes=minutes))
        # Translators: Title of the dialog that starts a cooking programme.
        title = _("Start programme")

        style = wx.ICON_QUESTION | wx.NO_DEFAULT
        style |= wx.YES_NO | wx.CANCEL if offer_change else wx.YES_NO
        confirm = wx.MessageDialog(self, message, title, style)
        if offer_change:
            # Translators: Button labels of the prompt before starting a
            # cooking programme, when the settings can still be changed.
            confirm.SetYesNoCancelLabels(_("&Yes, start"), _("C&hange..."),
                                         _("&Cancel"))
        else:
            # Translators: Button labels of the prompt before starting a
            # cooking programme.
            confirm.SetYesNoLabels(_("&Yes, start"), _("&Cancel"))

        previous = getattr(self, '_suppress_live_updates', False)
        self._suppress_live_updates = True
        try:
            try:
                result = confirm.ShowModal()
            finally:
                confirm.Destroy()
        finally:
            self._suppress_live_updates = previous
        if result == wx.ID_YES:
            return 'start'
        if offer_change and result == wx.ID_NO:
            return 'change'
        return 'cancel'

    def _send_start_cook(self, device, item, mode, programme_label,
                         temperature, seconds, details):
        """Sends startCook and reports what came of it."""
        try:
            device.start_cook(
                mode, temperature, seconds,
                recipe_id=(details or {}).get('recipe_id'),
                recipe_type=(details or {}).get('recipe_type'))
            _beep(BEEP_ACTION)
            # "set", not "started": the command was accepted, which is all
            # that is known at this moment. Whether the appliance began
            # heating or is waiting for its own start button shows up in
            # the programme state on the next poll, and that line says it
            # in words - "cooking" or "ready to start".
            # Translators: Confirmation after a cooking programme was sent
            # to the appliance. {name} = device name, {programme} =
            # programme name.
            ui.message(_("{name}: {programme} set").format(
                name=device.name, programme=programme_label))
            self.plugin._record_local_vesync_action(device.uuid)
            # Translators: History detail: a cooking programme was started.
            get_history().log_action(device, 'start_cook', mode)
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync startCook error: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync error: {error}").format(error=str(e)[:80]))

    def _handle_vesync_set_cook_temp(self, device, item):
        """Changes the temperature of a programme that is already loaded.

        No longer reachable: the tree stopped offering a temperature
        control once six attempts had shown the appliance never applies
        one while it cooks. Kept whole, with its dispatch entry, because
        the path itself is sound and one log from a model that does
        accept a temperature would put it straight back - and because
        deleting a tested route to re-type it later is the more expensive
        of the two mistakes.
        """
        low, high = device.temperature_range()
        temperature = self._ask_number(
            # Translators: Title of the temperature entry.
            _("Temperature"),
            # Translators: Prompt of the temperature entry. {low}/{high} =
            # the permitted values.
            _("Temperature between {low} and {high}:").format(low=low, high=high),
            low, high, device.target_temp)
        if temperature is None:
            # Translators: Message when the user cancels an action.
            ui.message(_("Cancelled"))
            return
        self._send_cook_adjustment(
            device, item,
            # Translators: Safety prompt before the temperature of a
            # running programme is changed. {name} = device name,
            # {temperature} = new set temperature with unit.
            _("Change the temperature on {name} to {temperature}?").format(
                name=device.name,
                temperature=device._format_temperature(temperature)),
            temperature=temperature)

    def _handle_vesync_set_cook_time(self, device, item):
        """Changes the time of a programme that is already loaded."""
        min_s, max_s = device.TIME_RANGE_SECONDS
        minutes = self._ask_number(
            # Translators: Title of the cooking time entry.
            _("Cooking time"),
            # Translators: Prompt of the cooking time entry. {low}/{high} =
            # the permitted values in minutes.
            _("Time in minutes, between {low} and {high}:").format(
                low=min_s // 60, high=max_s // 60),
            min_s // 60, max_s // 60,
            None if device.cook_set_time is None
            else max(1, int(device.cook_set_time) // 60))
        if minutes is None:
            # Translators: Message when the user cancels an action.
            ui.message(_("Cancelled"))
            return
        self._send_cook_adjustment(
            device, item,
            # Translators: Safety prompt before the time of a running
            # programme is changed. {name} = device name, {minutes} = new
            # time in minutes. Deliberately "set to" rather than "add":
            # the value sent becomes the new remaining time, measured
            # twice - it replaces what was left, it is not added to it.
            _("Set the cooking time on {name} to {minutes} minutes?").format(
                name=device.name, minutes=minutes),
            seconds=minutes * 60)

    def _send_cook_adjustment(self, device, item, question,
                              temperature=None, seconds=None):
        """Confirms and sends one setTimeOrTemp change."""
        confirm = wx.MessageDialog(
            self, question,
            # Translators: Title of the dialog that changes the time or
            # temperature of a running cooking programme.
            _("Change programme"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
        )
        # Translators: Button labels of the prompt before the time or
        # temperature of a running programme is changed.
        confirm.SetYesNoLabels(_("&Yes, change"), _("&Cancel"))
        previous = getattr(self, '_suppress_live_updates', False)
        self._suppress_live_updates = True
        try:
            try:
                result = confirm.ShowModal()
            finally:
                confirm.Destroy()
        finally:
            self._suppress_live_updates = previous
        if result != wx.ID_YES:
            # Translators: Message when the user cancels an action.
            ui.message(_("Cancelled"))
            return

        try:
            device.set_time_or_temp(temperature=temperature, seconds=seconds)
            _beep(BEEP_ACTION)
            # Translators: Confirmation after the time or temperature of a
            # running programme was changed. {name} = device name.
            ui.message(_("{name}: change sent").format(name=device.name))
            self.plugin._record_local_vesync_action(device.uuid)
            detail = 'temp' if temperature is not None else 'time'
            # Translators: History detail: time or temperature of a running
            # programme was changed.
            get_history().log_action(device, 'adjust_cook', detail)
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync setTimeOrTemp error: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync error: {error}").format(error=str(e)[:80]))

    def _handle_vesync_end_cook(self, device, item):
        """Stops a running cooking programme, after confirmation.

        Confirmed rather than sent straight away, even though stopping is
        the harmless direction: an eight-minute programme that is thrown
        away three minutes in by a mistyped Enter costs a meal. The
        question names the programme and the remaining time, so it can be
        answered without going back to the tree.
        """
        remaining = device.remaining_time_display()
        programme = device.programme_display()
        if programme and remaining:
            # Translators: Safety prompt before a cooking programme is
            # stopped. {name} = device name, {programme} = programme name,
            # {remaining} = remaining time.
            question = _("Stop the programme {programme} on {name}?\n\n"
                         "{remaining} still to run.").format(
                name=device.name, programme=programme, remaining=remaining)
        elif programme:
            # Translators: Safety prompt before a cooking programme is
            # stopped, with no remaining time known. {name} = device name,
            # {programme} = programme name.
            question = _("Stop the programme {programme} on {name}?").format(
                name=device.name, programme=programme)
        else:
            # Translators: Safety prompt before a cooking programme is
            # stopped. {name} = device name.
            question = _("Stop the running programme on {name}?").format(
                name=device.name)

        confirm = wx.MessageDialog(
            self, question,
            # Translators: Title of the dialog that stops a cooking programme.
            _("Stop programme"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
        )
        # Translators: Button labels of the prompt before stopping a
        # cooking programme.
        confirm.SetYesNoLabels(_("&Yes, stop"), _("&Cancel"))
        previous_suppress = getattr(self, '_suppress_live_updates', False)
        self._suppress_live_updates = True
        try:
            try:
                result = confirm.ShowModal()
            finally:
                confirm.Destroy()
        finally:
            self._suppress_live_updates = previous_suppress
        if result != wx.ID_YES:
            # Translators: Message when the user cancels an action.
            ui.message(_("Cancelled"))
            return

        try:
            device.end_cook()
            _beep(BEEP_ACTION)
            # Translators: Confirmation after a cooking programme was
            # stopped. {name} = device name.
            ui.message(_("{name}: programme stopped").format(name=device.name))
            self.plugin._record_local_vesync_action(device.uuid)
            # Translators: History detail: a cooking programme was stopped.
            get_history().log_action(device, 'end_cook', "")
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync endCook error: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync error: {error}").format(error=str(e)[:80]))

    def _rebuild_vesync_device_children(self, action_item, device):
        """Rebuilds the child nodes of a VeSync device after an action.

        Important: do NOT query the VeSync status API directly AFTER a write
        action (toggle, set_mode, ...). Immediately after a command the cloud
        API still returns the OLD status, which would overwrite the optimistic
        in-memory update of the action methods. The next background refresh
        fetches fresh data and reconciles the state. Focus is preserved as
        with Netatmo so NVDA does not read the new position twice.
        """
        try:
            # Determine the device node (parent or self)
            data = self.tree.GetItemData(action_item)
            if data and data.get('type') == 'device':
                device_node = action_item
            else:
                device_node = self.tree.GetItemParent(action_item)
                if not device_node.IsOk():
                    return

            # Use the incremental in-place update - if the structure stays the
            # same (e.g. after a mode or level change), only the changed texts
            # are updated via SetItemText. On structural changes (on/off
            # transition) the method automatically falls back to the focus-
            # preserving rebuild.
            self._live_update_vesync_children(device_node, device)
        except Exception as e:
            log.debug(f"VeSync tree update failed: {e}")

    def _rebuild_vesync_children_preserving_focus(self, device_item, device):
        """Rebuilds the children of a VeSync device without losing focus.

        Called by the background refresh so mode/level/air quality are
        updated live while the user is not ripped out of the currently
        focused action entry.

        The focus is restored by key where the line carries one, and only
        otherwise by position. The difference is audible on an air fryer:
        when a programme ends, the temperature and the remaining time drop
        out of the list, and restoring by position alone silently moved the
        reader from "Temperature: 192 °C" onto "Cannot be operated yet",
        which is what the screen reader then announced - at the very moment
        the interesting news was that the food was done.

        When the line really is gone, the search walks back up the old order
        to the nearest line that survived. Going up rather than down on
        purpose: the lines above are the ones the vanished line belonged to,
        so a reader parked on the temperature ends up on the programme state
        - which is where the news is - instead of on the favorites entry.
        """
        # Remember the focus (key and index) and the order of the keys, so a
        # line that disappears can be traded for its nearest neighbour.
        focused_item = self.tree.GetFocusedItem()
        focused_child_index = -1
        focused_key = None
        old_keys = []
        child, cookie = self.tree.GetFirstChild(device_item)
        while child.IsOk():
            old_keys.append((self.tree.GetItemData(child) or {}).get('key'))
            if focused_item.IsOk() and child == focused_item:
                focused_child_index = len(old_keys) - 1
                focused_key = old_keys[-1]
            child, cookie = self.tree.GetNextChild(device_item, cookie)

        # Update the device label (model alias + status + filter warning if
        # any)
        new_label = self._compute_vesync_device_label(device)
        current_label = self.tree.GetItemText(device_item)
        if new_label != current_label:
            self.tree.SetItemText(device_item, new_label)

        # Rebuild the children
        self.tree.DeleteChildren(device_item)
        self._fill_vesync_device_children(device_item, device)

        # Restore the focus (silently, without a duplicate NVDA announcement)
        if focused_child_index >= 0:
            children = []
            by_key = {}
            child, cookie = self.tree.GetFirstChild(device_item)
            while child.IsOk():
                children.append(child)
                key = (self.tree.GetItemData(child) or {}).get('key')
                if key is not None:
                    by_key.setdefault(key, child)
                child, cookie = self.tree.GetNextChild(device_item, cookie)

            target_child = by_key.get(focused_key)
            if target_child is None:
                # The line is gone: the nearest one above it that survived.
                for key in reversed(old_keys[:focused_child_index]):
                    if key is not None and key in by_key:
                        target_child = by_key[key]
                        break
            if target_child is None and focused_child_index < len(children):
                # Unkeyed lines (purifiers, fans) keep the old behaviour.
                target_child = children[focused_child_index]
            if target_child is None and children:
                target_child = children[-1]
            if target_child and target_child.IsOk():
                if self.tree.GetFocusedItem() == target_child:
                    # Already where it belongs. Selecting it again would
                    # fire a focus event for no movement, and the screen
                    # reader would read the line out once more.
                    return
                self._suppress_tree_focus_event = True
                try:
                    self.tree.SelectItem(target_child)
                    # SelectItem moves the focus in a single-selection tree,
                    # so SetFocusedItem was firing a SECOND focus event for
                    # the same line: after a programme ended, NVDA announced
                    # "Programme state: standby" twice, eight milliseconds
                    # apart. Only nudge the focus if selecting did not
                    # already take it there.
                    if self.tree.GetFocusedItem() != target_child:
                        self.tree.SetFocusedItem(target_child)
                finally:
                    wx.CallAfter(setattr, self, '_suppress_tree_focus_event', False)

    def _vesync_filter_is_low(self, device):
        """True if the remaining filter life reaches the warning threshold.

        Uses the threshold configured in the settings (default 15%). Only
        relevant for devices with ``filter_life`` (air purifiers).
        """
        fl = getattr(device, 'filter_life', None)
        if fl is None:
            return False
        threshold = getattr(self.plugin, 'vesync_filter_threshold', 15)
        return fl <= threshold

    def _compute_vesync_device_label(self, device):
        """Computes the display text of a VeSync device node (name + status).

        When the remaining filter life is low, the warning is appended
        directly to the label so it is immediately audible while navigating
        the tree - even without expanding the device node.
        """
        type_display = device.get_type_display() if hasattr(device, 'get_type_display') else device.type
        if hasattr(device, 'is_offline') and device.is_offline:
            # Translators: Tree label of a device without connection.
            return _("{name} ({type}) - offline").format(name=device.name, type=type_display)
        # An air fryer's on/off says nothing about what it is doing. It
        # reported "off" from the device list through an entire cook in a
        # tester's log, so the row read "Sigh fry (Cosori Dual Blaze) -
        # off" while the basket sat at 200 degrees and the line one level
        # down said "cooking". Its own summary already resolves the two
        # (see VeSyncAirFryer.get_status_summary) and is no longer.
        #
        # Deliberately not for purifiers and fans: their summary runs to
        # mode, level, air quality and filter life. That belongs in the
        # favourites announcement, which is asked for once, and not in a
        # tree row the screen reader repeats on every arrow key.
        if hasattr(device, 'cook_status'):
            on_state = device.get_status_summary()
        else:
            on_state = _("on") if device.is_on else _("off")
        label = f"{device.name} ({type_display}) - {on_state}"
        if self._vesync_filter_is_low(device):
            # Translators: Filter warning in the device label. {percent} =
            # remaining life.
            label += _(" - Warning: replace filter {percent}%").format(percent=device.filter_life)
        return label

    def _compute_vesync_items(self, device, is_favorite_view=False):
        """Computes the ordered list of all tree items for a VeSync device.

        Returns:
            list of dicts ``{'text', 'kind', 'action'}``. ``kind`` is
            ``'info'``, ``'action'`` or ``'favorite'``. For actions,
            ``action`` is the action identifier (e.g. ``'vesync_mode'``);
            for info items ``action`` is ``None``.

        Used both by the initial construction (``_fill_vesync_device_children``)
        and by the incremental live update. This guarantees that both paths
        produce the same order and logic.
        """
        items = []

        if hasattr(device, 'is_offline') and device.is_offline:
            # Translators: Status entry in the device tree.
            items.append({'text': _("Status: offline"), 'kind': 'info', 'action': None})
            items.append(self._compute_vesync_favorite_item(device, is_favorite_view))
            return items

        cls_name = type(device).__name__

        # ---- Devices that are shown but not operated ----
        # Everything below this point assumes a purifier or a fan: mode, fan
        # level, filter life. An air fryer has none of that, and reading it
        # would fail in the middle of building the tree. It gets what the
        # device list knows - and a line saying why there is nothing to
        # press, because an entry with no actions and no explanation reads
        # like a defect.
        if cls_name == 'VeSyncAirFryer':
            # One state line, not two. The on/off of the device list is
            # not wrong, it is about something else - it stayed "off"
            # across a whole cook - and standing first it was the first
            # thing read out about an appliance that was busy heating,
            # one line above "Programme state: cooking". It therefore
            # steps aside as soon as the appliance says what it is doing,
            # and only stands in until the first status has arrived.
            if device.cook_status:
                items.append({
                    # Translators: Cooking state of an air fryer. {state} =
                    # what the appliance reports, e.g. "standby".
                    'text': _("Programme state: {state}").format(
                        state=device.cook_status_display()),
                    'kind': 'info', 'action': None, 'key': 'fryer_state',
                })
            else:
                items.append({
                    # Translators: Operating state in the device tree.
                    'text': _("Status: on") if device.is_on else _("Status: off"),
                    'kind': 'info', 'action': None, 'key': 'fryer_switch',
                })
            # The programme, the remaining time and the temperatures only
            # exist while a programme is loaded - in standby the appliance
            # sends an empty stepArray and a stale temperature. Rows that
            # would carry nothing are left out rather than shown empty; the
            # focus survives that because it is restored by key, not by
            # position (see _rebuild_vesync_children_preserving_focus).
            #
            # Not device.cook_mode: that reads 'normal' whatever is
            # running. The programme is what the appliance was set to.
            programme = device.programme_display()
            if programme:
                items.append({
                    # Translators: Selected cooking programme of an air fryer.
                    # {mode} = programme name, e.g. "Steak".
                    'text': _("Programme: {mode}").format(mode=programme),
                    'kind': 'info', 'action': None, 'key': 'fryer_programme',
                })
            remaining = device.remaining_time_display()
            if remaining and device.time_is_counting_down:
                items.append({
                    # Translators: Remaining cooking time of an air fryer.
                    # {value} = time, e.g. "7 min 12 s".
                    'text': _("Remaining time: {value}").format(value=remaining),
                    'kind': 'info', 'action': None, 'key': 'fryer_remaining',
                })
            elif remaining:
                items.append({
                    # Translators: How long the selected cooking programme
                    # will take, before it has started running. {value} =
                    # time, e.g. "6 min".
                    'text': _("Duration: {value}").format(value=remaining),
                    'kind': 'info', 'action': None, 'key': 'fryer_remaining',
                })
            temperature = device.temperature_display()
            if temperature:
                items.append({
                    # Translators: Measured temperature of an air fryer.
                    # {value} = number with unit.
                    'text': _("Temperature: {value}").format(value=temperature),
                    'kind': 'info', 'action': None, 'key': 'fryer_temp',
                })
            target = device.target_temperature_display()
            if target:
                items.append({
                    # Translators: Temperature an air fryer was set to, as
                    # opposed to the one it currently measures. {value} =
                    # number with unit.
                    'text': _("Set temperature: {value}").format(value=target),
                    'kind': 'info', 'action': None, 'key': 'fryer_target',
                })
                if device.can_adjust_cook:
                    # There used to be a control for this, and it never
                    # worked: six attempts in two payload shapes, upwards
                    # and downwards, cooking and paused - the appliance
                    # either took the call and kept its own degrees, or
                    # refused it outright. Four keystrokes to a certain
                    # disappointment is worse than no control at all.
                    #
                    # Right here rather than down among the actions: the
                    # line the reader has just heard is the set
                    # temperature, and this is the answer to the question
                    # that raises. Putting it below would wedge an
                    # explanation between two Enter entries.
                    items.append({
                        # Translators: Tree entry for an air fryer, in
                        # place of a control for the temperature. The
                        # appliance accepts no temperature while cooking.
                        'text': _("Temperature only settable when starting "
                                  "the programme, or at the appliance"),
                        'kind': 'info', 'action': None,
                        'key': 'fryer_temp_hint',
                    })
            if device.can_start_cook:
                items.append({
                    # Translators: Action entry in the device tree, starts a
                    # cooking programme.
                    'text': _("Start programme - Enter"),
                    'kind': 'action', 'action': 'vesync_start_cook',
                    'key': 'fryer_start_cook',
                })
            if device.can_adjust_cook:
                items.append({
                    # Translators: Action entry in the device tree, changes
                    # the time of a loaded cooking programme.
                    'text': _("Change cooking time - Enter"),
                    'kind': 'action', 'action': 'vesync_set_cook_time',
                    'key': 'fryer_set_time',
                })
            if device.can_end_cook:
                items.append({
                    # Translators: Action entry in the device tree, stops a
                    # running cooking programme.
                    'text': _("Stop programme - Enter"),
                    'kind': 'action', 'action': 'vesync_end_cook',
                    'key': 'fryer_end_cook',
                })
            if (device.can_end_cook and not device.can_adjust_cook
                    and (device.cook_status or '').lower() == 'ready'):
                # The appliance refuses setTimeOrTemp before the programme
                # runs (its code 11017000), so the two entries are not
                # offered here. Saying why beats letting the reader hunt
                # for a control that was there a minute ago.
                items.append({
                    # Translators: Tree entry for an air fryer whose
                    # programme is loaded but not yet running.
                    'text': _("The cooking time can only be changed once "
                              "the programme runs"),
                    'kind': 'info', 'action': None, 'key': 'fryer_adjust_hint',
                })
            if not device.can_start_cook and not device.can_end_cook:
                # Before the first status reply the appliance has told us
                # nothing, so neither action can be offered. An entry with
                # no actions and no explanation reads like a defect, which
                # is what this line is for.
                items.append({
                    # Translators: Tree entry for an air fryer that has not
                    # reported its state yet.
                    'text': _("Waiting for the appliance"),
                    'kind': 'info', 'action': None, 'key': 'fryer_waiting',
                })
            items.append(self._compute_vesync_favorite_item(device, is_favorite_view))
            return items

        # ---- 0. Filter warning at the very top (if the remaining life is low)
        # ----
        # Deliberately the very first child so the hint appears immediately
        # when expanding the purifier, before all other values.
        if self._vesync_filter_is_low(device):
            items.append({
                # Translators: Filter warning as the first tree entry.
                # {percent} = remaining life.
                'text': _("Warning: replace filter, remaining life {percent}%").format(
                    percent=device.filter_life),
                'kind': 'info', 'action': None,
            })

        # ---- 1. Status info ----
        items.append({
            # Translators: Operating state in the device tree.
            'text': _("Status: on") if device.is_on else _("Status: off"),
            'kind': 'info', 'action': None,
        })

        # Translators: Placeholder for an unknown mode.
        unknown_mode = _("unknown")
        if cls_name == 'VeSyncPurifier':
            mode_label = VESYNC_PURIFIER_MODE_NAMES.get(device.mode, device.mode or unknown_mode)
        else:
            mode_label = VESYNC_FAN_MODE_NAMES.get(device.mode, device.mode or unknown_mode)

        # Sensor/measurement values
        if cls_name == 'VeSyncPurifier':
            if device.supports_air_quality and device.air_quality is not None:
                aq_text = VESYNC_AIR_QUALITY_NAMES.get(device.air_quality, str(device.air_quality))
                # Translators: Air quality rating in the device tree.
                items.append({'text': _("Air quality: {value}").format(value=aq_text), 'kind': 'info', 'action': None})
            if device.air_quality_value is not None:
                items.append({'text': f"PM2.5: {device.air_quality_value} µg/m³", 'kind': 'info', 'action': None})
            if device.pm1 is not None:
                items.append({'text': f"PM1.0: {device.pm1} µg/m³", 'kind': 'info', 'action': None})
            if device.pm10 is not None:
                items.append({'text': f"PM10: {device.pm10} µg/m³", 'kind': 'info', 'action': None})
            if device.aq_percent is not None:
                # Translators: Air quality in percent in the device tree.
                items.append({'text': _("Air quality: {percent}%").format(percent=device.aq_percent), 'kind': 'info', 'action': None})
            if device.voc is not None:
                items.append({'text': f"VOC: {device.voc}", 'kind': 'info', 'action': None})
            if device.co2 is not None:
                items.append({'text': f"CO₂: {device.co2} ppm", 'kind': 'info', 'action': None})
            if device.filter_life is not None:
                # Translators: Remaining filter life in the device tree.
                items.append({'text': _("Filter life: {percent}%").format(percent=device.filter_life), 'kind': 'info', 'action': None})

        if cls_name == 'VeSyncTowerFan':
            if device.temperature is not None:
                try:
                    items.append({
                        # Translators: Measured temperature in the device tree.
                        'text': _("Temperature: {temp}°C").format(temp=f"{float(device.temperature):.1f}"),
                        'kind': 'info', 'action': None,
                    })
                except (TypeError, ValueError):
                    pass

        # ---- 2. Actions ----
        # Translators: Action entry for switching on/off. {name} = device name.
        toggle_text = (_("Turn {name} off") if device.is_on else _("Turn "
                                                                      "{name} "
                                                                      "on")).format(name=device.name)
        items.append({'text': toggle_text, 'kind': 'action', 'action': 'vesync_toggle'})

        if device.is_on:
            if (cls_name == 'VeSyncPurifier' and len(device.modes) > 1) or \
               (cls_name == 'VeSyncTowerFan' and len(device.modes) > 1):
                items.append({
                    # Translators: Combined info+action label in the device
                    # tree.
                    'text': _("Mode: {mode} - press Enter to change").format(mode=mode_label),
                    'kind': 'action', 'action': 'vesync_mode',
                })

            fan_levels = getattr(device, 'fan_levels', [])
            if fan_levels and len(fan_levels) > 1:
                if cls_name == 'VeSyncPurifier' and device.mode != 'manual':
                    items.append({
                        # Translators: Note that the fan level is currently
                        # controlled automatically.
                        'text': _("Fan speed: automatically controlled - "
                                  "press Enter to set manually"),
                        'kind': 'action', 'action': 'vesync_fan_speed',
                    })
                else:
                    if cls_name == 'VeSyncPurifier':
                        level_label = _vesync_purifier_level_label(device.fan_level, fan_levels)
                    else:
                        level_label = device.fan_level if device.fan_level is not None else "?"
                    items.append({
                        # Translators: Combined info+action label in the device
                        # tree.
                        'text': _("Fan speed: {level} - press Enter to change").format(level=level_label),
                        'kind': 'action', 'action': 'vesync_fan_speed',
                    })

            if cls_name == 'VeSyncPurifier' and device.supports_auto_preference \
                    and device.auto_preferences:
                ap_label = VESYNC_AUTO_PREFERENCE_NAMES.get(
                    device.auto_preference_type,
                    device.auto_preference_type or "?",
                )
                items.append({
                    # Translators: Combined info+action label in the device
                    # tree.
                    'text': _("Auto profile: {profile} - press Enter to change").format(profile=ap_label),
                    'kind': 'action', 'action': 'vesync_auto_preference',
                })

            if cls_name == 'VeSyncPurifier' and device.supports_nightlight \
                    and device.nightlight_modes:
                nl_label = VESYNC_NIGHTLIGHT_MODE_NAMES.get(
                    device.nightlight_status, device.nightlight_status or _("off")
                )
                items.append({
                    # Translators: Combined info+action label in the device
                    # tree.
                    'text': _("Night light: {mode} - press Enter to change").format(mode=nl_label),
                    'kind': 'action', 'action': 'vesync_nightlight',
                })

            if cls_name == 'VeSyncTowerFan' and device.oscillation_on is not None:
                items.append({
                    # Translators: Combined info+action label in the device
                    # tree.
                    'text': (_("Oscillation: on - press Enter to turn off") if device.oscillation_on
                             # Translators: Line in the device tree:
                             # oscillation is off, Enter turns it on.
                             else _("Oscillation: off - press Enter to turn on")),
                    'kind': 'action', 'action': 'vesync_oscillation',
                })

            if cls_name == 'VeSyncTowerFan' and device.mute_on is not None:
                items.append({
                    # Translators: Combined info+action label in the device
                    # tree.
                    'text': (_("Mute: on - press Enter to turn off") if device.mute_on
                             # Translators: Line in the device tree: the sound
                             # is off, Enter turns it on.
                             else _("Mute: off - press Enter to turn on")),
                    'kind': 'action', 'action': 'vesync_mute',
                })

            if device.display_on is not None or device.display_set_on is not None:
                current_disp = device.display_set_on if device.display_set_on is not None else device.display_on
                items.append({
                    # Translators: Combined info+action label in the device
                    # tree.
                    'text': (_("Display: on - press Enter to turn off") if current_disp
                             # Translators: Line in the device tree: the
                             # display is off, Enter turns it on.
                             else _("Display: off - press Enter to turn on")),
                    'kind': 'action', 'action': 'vesync_display',
                })

            if cls_name == 'VeSyncPurifier' and device.supports_child_lock \
                    and device.child_lock is not None:
                items.append({
                    # Translators: Combined info+action label in the device
                    # tree.
                    'text': (_("Child lock: on - press Enter to turn off") if device.child_lock
                             # Translators: Line in the device tree: the child
                             # lock is off, Enter turns it on.
                             else _("Child lock: off - press Enter to turn on")),
                    'kind': 'action', 'action': 'vesync_child_lock',
                })

        if cls_name == 'VeSyncPurifier' and device.supports_reset_filter:
            items.append({
                # Translators: Action entry for the filter reset.
                'text': _("Reset filter life to 100% - press Enter to execute"),
                'kind': 'action', 'action': 'vesync_reset_filter',
            })

        items.append(self._compute_vesync_favorite_item(device, is_favorite_view))
        return items

    def _compute_vesync_favorite_item(self, device, is_favorite_view):
        """Computes the favorite entry (add/remove) for a VeSync device."""
        favorites = get_favorites()
        is_fav = favorites.is_favorite(device.unique_id)
        # One key for both variants: the entry keeps its place when the
        # device is added to or removed from the favourites, so the focus
        # stays on it across the rebuild that the changed action triggers.
        if is_favorite_view or is_fav:
            return {
                # Translators: Action entry in the device tree.
                'text': _("Remove from favorites - Enter"),
                'kind': 'action', 'action': 'favorite_remove',
                'key': 'favorite',
            }
        return {
            # Translators: Action entry in the device tree.
            'text': _("Add to favorites - Enter"),
            'kind': 'action', 'action': 'favorite_add',
            'key': 'favorite',
        }

    def _fill_vesync_device_children(self, device_node, device, is_favorite_view=False):
        """Fills the child nodes of a VeSync device (initial construction).

        Uses ``_compute_vesync_items`` as the single source of truth for the
        item list. Live updates use the same list to update texts in place
        instead of deleting and rebuilding the children (see
        ``_live_update_vesync_children``).
        """
        items = self._compute_vesync_items(device, is_favorite_view=is_favorite_view)
        for item in items:
            kind = item['kind']
            if kind == 'info':
                self._append_info(device_node, device, item['text'],
                                  key=item.get('key'))
            elif kind == 'action':
                self._append_action(device_node, device, item['text'],
                                    item['action'], key=item.get('key'))

    def _live_update_vesync_children(self, device_item, device):
        """Updates a VeSync device without a tree rebuild (for fast poll/refresh).

        Compares the computed target item list with the current tree children.
        If order and item types are identical, only the changed texts are
        updated via ``SetItemText`` - this prevents the ``BrokenCommctrl5Item``
        flickering and the duplicate NVDA announcements a full rebuild would
        cause every time.

        Structural changes (e.g. device on/off -> actions appear or
        disappear) trigger a one-time full rebuild that still preserves
        focus.
        """
        # Update the device label (name + status) if needed - only affects the
        # device node itself, not the children.
        new_label = self._compute_vesync_device_label(device)
        if self.tree.GetItemText(device_item) != new_label:
            self.tree.SetItemText(device_item, new_label)

        # Compute the target list
        expected = self._compute_vesync_items(device, is_favorite_view=False)

        # Collect the current children
        children = []
        child, cookie = self.tree.GetFirstChild(device_item)
        while child.IsOk():
            children.append(child)
            child, cookie = self.tree.GetNextChild(device_item, cookie)

        # Structural change? -> one-time rebuild
        structure_matches = (len(children) == len(expected))
        if structure_matches:
            for ch, exp in zip(children, expected):
                ch_data = self.tree.GetItemData(ch) or {}
                ch_type = ch_data.get('type')
                # Same count is not the same list: one line can drop out
                # while another appears. Where lines are keyed, the key has
                # to line up too, otherwise a text swap would quietly turn
                # the focused line into a different one.
                if ch_data.get('key') != exp.get('key'):
                    structure_matches = False
                    break
                # 'info' vs 'action' must match
                if exp['kind'] == 'info' and ch_type != 'info':
                    structure_matches = False
                    break
                if exp['kind'] == 'action':
                    if ch_type != 'action':
                        structure_matches = False
                        break
                    if ch_data.get('action') != exp['action']:
                        structure_matches = False
                        break

        if not structure_matches:
            self._rebuild_vesync_children_preserving_focus(device_item, device)
            return

        # Incremental update: only change texts where they differ. NO
        # DeleteChildren/AppendItem -> NVDA focus stays stable, no accRole
        # errors, no braille flickering.
        for ch, exp in zip(children, expected):
            if self.tree.GetItemText(ch) != exp['text']:
                self.tree.SetItemText(ch, exp['text'])

    def _add_vesync_devices_to_category(self, cat, devices):
        """Inserts VeSync devices into a category"""
        for device in devices:
            self._add_single_vesync_device(cat, device)

    def _add_single_vesync_device(self, parent_node, device, is_favorite_view=False):
        """Inserts a single VeSync device as a child node.

        Args:
            parent_node: parent tree item
            device: VeSyncPurifier or VeSyncTowerFan
            is_favorite_view: True when in the favorites view
        """
        # Display name with model alias, status and filter warning if any
        label = self._compute_vesync_device_label(device)
        device_item = self.tree.AppendItem(parent_node, label)
        self.tree.SetItemData(device_item, {'type': 'device', 'device': device})

        # Build the device node content via the shared method.
        # _fill_vesync_device_children adds _add_favorite_action at the end; in
        # the favorites view this action must show "remove from favorites" -
        # _add_favorite_action does that automatically once the device is in
        # the favorites. For the favorites view the is_favorite_view hint is
        # detected through the existing favorite flag.
        self._fill_vesync_device_children(device_item, device,
                                          is_favorite_view=is_favorite_view)

        self.tree.Collapse(device_item)

    def _init_vesync_in_background(self):
        """Initializes the VeSync API in the background and loads devices afterwards.

        Called from the settings dialog when VeSync has just been enabled or
        its credentials changed - without blocking the UI.
        """
        plugin = self.plugin
        if not plugin.begin_platform_login('vesync'):
            log.info("VeSync login already running - not starting a second one")
            return

        def _login_and_refresh():
            try:
                from .vesync_api import VeSyncAPI
                api = VeSyncAPI(country_code=plugin.vesync_country_code or "DE")
                if hasattr(api, 'set_reauth_callback'):
                    api.set_reauth_callback(plugin._vesync_reauth)

                # Preferred: existing tokens, otherwise email/password
                if plugin.vesync_token and plugin.vesync_account_id:
                    api.set_credentials(
                        token=plugin.vesync_token,
                        account_id=plugin.vesync_account_id,
                        country_code=plugin.vesync_country_code or "DE",
                        region=plugin.vesync_region or None,
                    )
                else:
                    _pw = plugin.vesync_password
                    try:
                        api.login(plugin.vesync_email, _pw)
                    finally:
                        _pw = None
                        del _pw

                try:
                    devices = api.get_devices()
                except RuntimeError:
                    # Token possibly expired -> password login
                    if plugin.vesync_email and plugin._encrypted_vesync_password:
                        _pw = plugin.vesync_password
                        try:
                            api.login(plugin.vesync_email, _pw)
                        finally:
                            _pw = None
                            del _pw
                        devices = api.get_devices()
                    else:
                        raise

                # Save the tokens (they can change due to cross-region)
                creds = api.get_credentials()
                if creds["token"] and creds["account_id"]:
                    plugin.vesync_token = creds["token"]
                    plugin.vesync_account_id = creds["account_id"]
                    plugin.vesync_country_code = creds["country_code"]
                    plugin.vesync_region = creds["region"]
                    plugin.save_settings()

                # Take over the new session only after the login worked; the
                # old one is closed afterwards so its HTTP session does not
                # stay open.
                old_api, plugin.vesync_api = plugin.vesync_api, api
                if old_api is not None and old_api is not api:
                    try:
                        old_api.logout()
                    except Exception as e:
                        log.debug(f"Logout of the old VeSync session failed: {e}")

                # Replace the VeSync devices in the shared list - under the
                # lock, since the scheduler thread reads and writes it in
                # parallel.
                count = plugin.replace_platform_devices('vesync', devices)
                for dev in devices:
                    plugin._previous_vesync_states.setdefault(
                        dev.uuid, plugin._snapshot_vesync_state(dev))
                log.info(f"VeSync initialised late: {count} devices")

                # Update the dialog on the UI thread
                wx.CallAfter(self._refresh_after_vesync_init, len(devices))
            except Exception as e:
                log.error(f"Late VeSync initialisation failed: "
                          f"{type(e).__name__}: {e}")
                wx.CallAfter(self._offer_login_reentry, 'vesync', e)
            finally:
                plugin.end_platform_login('vesync')

        threading.Thread(target=_login_and_refresh, daemon=True).start()
        # Translators: Note that the VeSync connection is being established in
        # the background.
        ui.message(_("Connecting to VeSync..."))

    def _refresh_after_vesync_init(self, count):
        """Updates the tree after VeSync was connected afterwards."""
        if self._is_destroyed:
            return
        try:
            self._load_devices_internal(self.plugin.devices)
            self._refresh_favorites_tree()
            # Translators: Confirmation after loading the VeSync devices
            # afterwards.
            ui.message(_("VeSync: {count} device(s) loaded").format(count=count))
        except Exception as e:
            log.debug(f"Refresh after the VeSync init failed: {e}")

