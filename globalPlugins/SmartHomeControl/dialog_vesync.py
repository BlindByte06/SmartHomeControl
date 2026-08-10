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
    _nvda_log.debug(f"initTranslation fehlgeschlagen: {e}")
if "_" not in globals():  # Fallback, falls initTranslation() scheitert
    # Ohne diesen Fallback bleibt `_` undefiniert und der erste `_()`-Aufruf
    # wirft einen NameError mitten im Dialogaufbau statt beim Import.
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
            status = _("ein") if new_state else _("aus")
            ui.message(_("{name}: {status}").format(name=device.name, status=status))
            self.plugin._record_local_toggle(device.uuid, new_state)
            self.plugin._record_local_vesync_action(device.uuid)
            get_history().log_action(
                device, 'toggle_on' if new_state else 'toggle_off',
                # Translators: History detail: device switched on/off.
                _('Ein') if new_state else _('Aus')
            )
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync Toggle-Fehler: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync-Fehler: {error}").format(error=str(e)[:80]))

    def _vesync_choose_from_list(self, device, title, prompt,
                                  values, label_map, current_value):
        """Opens a selection dialog (analogous to Netatmo) and returns the
        chosen value (or None if cancelled).
        """
        if not values:
            # Translators: Message when a selection list has no options.
            ui.message(_("Keine Auswahl verfügbar"))
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
            _("{name}: Modus wählen").format(name=device.name),
            # Translators: Prompt in the mode selection dialog.
            _("Wählen Sie den Betriebsmodus:"),
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
            ui.message(_("{name}: Modus {mode}").format(name=device.name, mode=mode_de))
            self.plugin._record_local_vesync_action(device.uuid)
            get_history().log_action(device, 'set_mode', mode_de)
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync Modus-Fehler: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync-Fehler: {error}").format(error=str(e)[:80]))

    def _handle_vesync_fan_speed(self, device, item):
        """Lets the user choose a fan speed"""
        levels = list(getattr(device, 'fan_levels', []))
        if not levels:
            # Translators: Message when the device has no selectable fan
            # levels.
            ui.message(_("Keine Lüftergeschwindigkeit verfügbar"))
            return
        # For Core 200S/300S (3 levels) append the plain text "low/medium/high"
        cls_name = type(device).__name__
        if cls_name == 'VeSyncPurifier' and len(levels) == 3:
            label_map = {
                # Translators: List entry of a fan level with plain-text
                # suffix.
                lvl: _("Stufe {level} – {label}").format(
                    level=lvl, label=VESYNC_PURIFIER_LEVEL_LABELS_3[lvl])
                for lvl in levels if lvl in VESYNC_PURIFIER_LEVEL_LABELS_3
            }
        else:
            # Translators: List entry of a fan level.
            label_map = {lvl: _("Stufe {level}").format(level=lvl) for lvl in levels}
        chosen = self._vesync_choose_from_list(
            device,
            # Translators: Title of the fan level dialog. {name} = device name.
            _("{name}: Lüftergeschwindigkeit").format(name=device.name),
            # Translators: Prompt in the fan level dialog.
            _("Wählen Sie die Lüftergeschwindigkeit:"),
            levels, label_map, device.fan_level,
        )
        if chosen is None:
            return
        try:
            device.set_fan_speed(chosen)
            _beep(BEEP_ACTION)
            # Translators: Confirmation after a level change. {level} = fan
            # level.
            ui.message(_("{name}: Stufe {level}").format(name=device.name, level=chosen))
            self.plugin._record_local_vesync_action(device.uuid)
            # Translators: History detail: fan level that was set.
            get_history().log_action(device, 'set_fan_speed', _("Stufe {level}").format(level=chosen))
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync Lüfter-Fehler: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync-Fehler: {error}").format(error=str(e)[:80]))

    def _handle_vesync_oscillation(self, device, item):
        """Toggles the oscillation of a tower fan"""
        try:
            new_state = not bool(device.oscillation_on)
            device.toggle_oscillation(new_state)
            _beep(BEEP_ACTION)
            status = _("ein") if new_state else _("aus")
            # Translators: Confirmation after toggling oscillation.
            ui.message(_("{name}: Oszillation {status}").format(name=device.name, status=status))
            self.plugin._record_local_vesync_action(device.uuid)
            get_history().log_action(
                device, 'oscillation_on' if new_state else 'oscillation_off',
                # Translators: History detail: oscillation switched on/off.
                _("Oszillation {status}").format(status=status))
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync Oszillations-Fehler: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync-Fehler: {error}").format(error=str(e)[:80]))

    def _handle_vesync_mute(self, device, item):
        """Toggles the mute of a tower fan"""
        try:
            new_state = not bool(device.mute_on)
            device.toggle_mute(new_state)
            _beep(BEEP_ACTION)
            # Translators: Status after toggling mute.
            status = _("stumm") if new_state else _("Tonsignale ein")
            ui.message(_("{name}: {status}").format(name=device.name, status=status))
            self.plugin._record_local_vesync_action(device.uuid)
            get_history().log_action(
                device, 'mute_on' if new_state else 'mute_off',
                "Stumm ein" if new_state else "Stumm aus")
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync Mute-Fehler: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync-Fehler: {error}").format(error=str(e)[:80]))

    def _handle_vesync_display(self, device, item):
        """Toggles the display of a VeSync device"""
        try:
            current = device.display_set_on
            if current is None:
                current = device.display_on
            new_state = not bool(current)
            device.toggle_display(new_state)
            _beep(BEEP_ACTION)
            status = _("ein") if new_state else _("aus")
            # Translators: Confirmation after toggling the device display.
            ui.message(_("{name}: Display {status}").format(name=device.name, status=status))
            self.plugin._record_local_vesync_action(device.uuid)
            get_history().log_action(
                device, 'display_on' if new_state else 'display_off',
                # Translators: History detail: display switched on/off.
                _("Display {status}").format(status=status))
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync Display-Fehler: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync-Fehler: {error}").format(error=str(e)[:80]))

    def _handle_vesync_child_lock(self, device, item):
        """Toggles the child lock of an air purifier"""
        try:
            new_state = not bool(device.child_lock)
            device.toggle_child_lock(new_state)
            _beep(BEEP_ACTION)
            status = _("ein") if new_state else _("aus")
            # Translators: Confirmation after toggling the child lock.
            ui.message(_("{name}: Kindersicherung {status}").format(name=device.name, status=status))
            self.plugin._record_local_vesync_action(device.uuid)
            get_history().log_action(
                device, 'child_lock_on' if new_state else 'child_lock_off',
                # Translators: History detail: child lock switched on/off.
                _("Kindersicherung {status}").format(status=status))
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync Kindersicherung-Fehler: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync-Fehler: {error}").format(error=str(e)[:80]))

    def _handle_vesync_nightlight(self, device, item):
        """Lets the user choose the night light mode"""
        modes = list(getattr(device, 'nightlight_modes', []))
        chosen = self._vesync_choose_from_list(
            device,
            # Translators: Title of the night light dialog. {name} = device
            # name.
            _("{name}: Nachtlicht").format(name=device.name),
            # Translators: Prompt in the night light dialog.
            _("Wählen Sie den Nachtlicht-Modus:"),
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
            ui.message(_("{name}: Nachtlicht {mode}").format(name=device.name, mode=label))
            self.plugin._record_local_vesync_action(device.uuid)
            get_history().log_action(device, 'set_nightlight', label)
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync Nachtlicht-Fehler: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync-Fehler: {error}").format(error=str(e)[:80]))

    def _handle_vesync_auto_preference(self, device, item):
        """Lets the user choose an auto profile (default/efficient/quiet)"""
        prefs = list(getattr(device, 'auto_preferences', []))
        chosen = self._vesync_choose_from_list(
            device,
            # Translators: Title of the auto profile dialog. {name} = device
            # name.
            _("{name}: Auto-Profil").format(name=device.name),
            # Translators: Prompt in the auto profile dialog.
            _("Wählen Sie das Auto-Profil:"),
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
            ui.message(_("{name}: Auto-Profil {profile}").format(name=device.name, profile=label))
            self.plugin._record_local_vesync_action(device.uuid)
            get_history().log_action(device, 'set_auto_preference', label)
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync Auto-Profil-Fehler: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync-Fehler: {error}").format(error=str(e)[:80]))

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
            _("Filter-Lebensdauer von {name} auf 100% zurücksetzen?\n\n"
              "Diese Aktion ist nur sinnvoll, wenn Sie den HEPA-Filter "
              "tatsächlich gerade gegen einen neuen ausgetauscht haben. "
              "Ein Zurücksetzen ohne Filterwechsel führt dazu, dass das "
              "Gerät keine Filterwechsel-Erinnerung mehr anzeigt.").format(name=device.name),
            # Translators: Title of the filter reset dialog.
            _("Filter-Lebensdauer zurücksetzen"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
        )
        # Translators: Button labels of the first safety prompt.
        confirm.SetYesNoLabels(_("&Weiter"), _("&Abbrechen"))
        previous_suppress = getattr(self, '_suppress_live_updates', False)
        self._suppress_live_updates = True
        try:
            try:
                result = confirm.ShowModal()
            finally:
                confirm.Destroy()
            if result != wx.ID_YES:
                # Translators: Message when the user cancels an action.
                ui.message(_("Abgebrochen"))
                return
            # Step 2: final safety prompt
            final_confirm = wx.MessageDialog(
                self,
                # Translators: Second safety prompt before the filter reset.
                # {name} = device name.
                _("Sind Sie wirklich sicher, dass Sie die Filter-Lebensdauer "
                  "von {name} jetzt auf 100% zurücksetzen möchten?\n\n"
                  "Diese Aktion kann nicht rückgängig gemacht werden.").format(name=device.name),
                # Translators: Title of the second safety prompt.
                _("Sicherheitsabfrage"),
                wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
            )
            # Translators: Button labels of the second safety prompt.
            final_confirm.SetYesNoLabels(_("&Ja, zurücksetzen"), _("&Abbrechen"))
            try:
                result2 = final_confirm.ShowModal()
            finally:
                final_confirm.Destroy()
            if result2 != wx.ID_YES:
                # Translators: Message when the user cancels an action.
                ui.message(_("Abgebrochen"))
                return
        finally:
            self._suppress_live_updates = previous_suppress
        try:
            device.reset_filter()
            _beep(BEEP_ACTION)
            # Translators: Confirmation after a successful filter reset.
            ui.message(_("{name}: Filter-Lebensdauer zurückgesetzt").format(name=device.name))
            self.plugin._record_local_vesync_action(device.uuid)
            # Translators: History detail: filter life reset.
            get_history().log_action(device, 'reset_filter', _('Filter zurückgesetzt'))
            self._rebuild_vesync_device_children(item, device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"VeSync Filter-Reset-Fehler: {e}")
            # Translators: Generic VeSync error message with detail text.
            ui.message(_("VeSync-Fehler: {error}").format(error=str(e)[:80]))

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
            log.debug(f"VeSync Baum-Update fehlgeschlagen: {e}")

    def _rebuild_vesync_children_preserving_focus(self, device_item, device):
        """Rebuilds the children of a VeSync device without losing focus.

        Called by the background refresh so mode/level/air quality are
        updated live while the user is not ripped out of the currently
        focused action entry.
        """
        # Remember the focus (index of the focused child)
        focused_item = self.tree.GetFocusedItem()
        focused_child_index = -1
        if focused_item.IsOk():
            child, cookie = self.tree.GetFirstChild(device_item)
            idx = 0
            while child.IsOk():
                if child == focused_item:
                    focused_child_index = idx
                    break
                idx += 1
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
            child, cookie = self.tree.GetFirstChild(device_item)
            idx = 0
            last_child = child
            target_child = None
            while child.IsOk():
                if idx == focused_child_index:
                    target_child = child
                    break
                last_child = child
                idx += 1
                child, cookie = self.tree.GetNextChild(device_item, cookie)
            if target_child is None and last_child and last_child.IsOk():
                target_child = last_child
            if target_child and target_child.IsOk():
                self._suppress_tree_focus_event = True
                try:
                    self.tree.SelectItem(target_child)
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
        on_state = _("ein") if device.is_on else _("aus")
        label = f"{device.name} ({type_display}) - {on_state}"
        if self._vesync_filter_is_low(device):
            # Translators: Filter warning in the device label. {percent} =
            # remaining life.
            label += _(" - Achtung: Filter wechseln {percent}%").format(percent=device.filter_life)
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
            items.append({'text': _("Status: Offline"), 'kind': 'info', 'action': None})
            items.append(self._compute_vesync_favorite_item(device, is_favorite_view))
            return items

        cls_name = type(device).__name__

        # ---- 0. Filter warning at the very top (if the remaining life is low)
        # ----
        # Deliberately the very first child so the hint appears immediately
        # when expanding the purifier, before all other values.
        if self._vesync_filter_is_low(device):
            items.append({
                # Translators: Filter warning as the first tree entry.
                # {percent} = remaining life.
                'text': _("Achtung: Filter wechseln, Restlebensdauer {percent}%").format(
                    percent=device.filter_life),
                'kind': 'info', 'action': None,
            })

        # ---- 1. Status info ----
        items.append({
            # Translators: Operating state in the device tree.
            'text': _("Status: Ein") if device.is_on else _("Status: Aus"),
            'kind': 'info', 'action': None,
        })

        # Translators: Placeholder for an unknown mode.
        unknown_mode = _("unbekannt")
        if cls_name == 'VeSyncPurifier':
            mode_label = VESYNC_PURIFIER_MODE_NAMES.get(device.mode, device.mode or unknown_mode)
        else:
            mode_label = VESYNC_FAN_MODE_NAMES.get(device.mode, device.mode or unknown_mode)

        # Sensor/measurement values
        if cls_name == 'VeSyncPurifier':
            if device.supports_air_quality and device.air_quality is not None:
                aq_text = VESYNC_AIR_QUALITY_NAMES.get(device.air_quality, str(device.air_quality))
                # Translators: Air quality rating in the device tree.
                items.append({'text': _("Luftqualität: {value}").format(value=aq_text), 'kind': 'info', 'action': None})
            if device.air_quality_value is not None:
                items.append({'text': f"PM2.5: {device.air_quality_value} µg/m³", 'kind': 'info', 'action': None})
            if device.pm1 is not None:
                items.append({'text': f"PM1.0: {device.pm1} µg/m³", 'kind': 'info', 'action': None})
            if device.pm10 is not None:
                items.append({'text': f"PM10: {device.pm10} µg/m³", 'kind': 'info', 'action': None})
            if device.aq_percent is not None:
                # Translators: Air quality in percent in the device tree.
                items.append({'text': _("Luftqualität: {percent}%").format(percent=device.aq_percent), 'kind': 'info', 'action': None})
            if device.voc is not None:
                items.append({'text': f"VOC: {device.voc}", 'kind': 'info', 'action': None})
            if device.co2 is not None:
                items.append({'text': f"CO₂: {device.co2} ppm", 'kind': 'info', 'action': None})
            if device.filter_life is not None:
                # Translators: Remaining filter life in the device tree.
                items.append({'text': _("Filter-Lebensdauer: {percent}%").format(percent=device.filter_life), 'kind': 'info', 'action': None})

        if cls_name == 'VeSyncTowerFan':
            if device.temperature is not None:
                try:
                    items.append({
                        # Translators: Measured temperature in the device tree.
                        'text': _("Temperatur: {temp}°C").format(temp=f"{float(device.temperature):.1f}"),
                        'kind': 'info', 'action': None,
                    })
                except (TypeError, ValueError):
                    pass

        # ---- 2. Actions ----
        # Translators: Action entry for switching on/off. {name} = device name.
        toggle_text = (_("{name} ausschalten") if device.is_on else _("{name} einschalten")).format(name=device.name)
        items.append({'text': toggle_text, 'kind': 'action', 'action': 'vesync_toggle'})

        if device.is_on:
            if (cls_name == 'VeSyncPurifier' and len(device.modes) > 1) or \
               (cls_name == 'VeSyncTowerFan' and len(device.modes) > 1):
                items.append({
                    # Translators: Combined info+action label in the device
                    # tree.
                    'text': _("Modus: {mode} - Enter zum Ändern").format(mode=mode_label),
                    'kind': 'action', 'action': 'vesync_mode',
                })

            fan_levels = getattr(device, 'fan_levels', [])
            if fan_levels and len(fan_levels) > 1:
                if cls_name == 'VeSyncPurifier' and device.mode != 'manual':
                    items.append({
                        # Translators: Note that the fan level is currently
                        # controlled automatically.
                        'text': _("Lüftergeschwindigkeit: automatisch geregelt - Enter zum Manuell-Setzen"),
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
                        'text': _("Lüftergeschwindigkeit: {level} - Enter zum Ändern").format(level=level_label),
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
                    'text': _("Auto-Profil: {profile} - Enter zum Ändern").format(profile=ap_label),
                    'kind': 'action', 'action': 'vesync_auto_preference',
                })

            if cls_name == 'VeSyncPurifier' and device.supports_nightlight \
                    and device.nightlight_modes:
                nl_label = VESYNC_NIGHTLIGHT_MODE_NAMES.get(
                    device.nightlight_status, device.nightlight_status or _("aus")
                )
                items.append({
                    # Translators: Combined info+action label in the device
                    # tree.
                    'text': _("Nachtlicht: {mode} - Enter zum Ändern").format(mode=nl_label),
                    'kind': 'action', 'action': 'vesync_nightlight',
                })

            if cls_name == 'VeSyncTowerFan' and device.oscillation_on is not None:
                items.append({
                    # Translators: Combined info+action label in the device
                    # tree.
                    'text': (_("Oszillation: ein - Enter zum Ausschalten") if device.oscillation_on
                             else _("Oszillation: aus - Enter zum Einschalten")),
                    'kind': 'action', 'action': 'vesync_oscillation',
                })

            if cls_name == 'VeSyncTowerFan' and device.mute_on is not None:
                items.append({
                    # Translators: Combined info+action label in the device
                    # tree.
                    'text': (_("Stummschaltung: ein - Enter zum Ausschalten") if device.mute_on
                             else _("Stummschaltung: aus - Enter zum Einschalten")),
                    'kind': 'action', 'action': 'vesync_mute',
                })

            if device.display_on is not None or device.display_set_on is not None:
                current_disp = device.display_set_on if device.display_set_on is not None else device.display_on
                items.append({
                    # Translators: Combined info+action label in the device
                    # tree.
                    'text': (_("Display: ein - Enter zum Ausschalten") if current_disp
                             else _("Display: aus - Enter zum Einschalten")),
                    'kind': 'action', 'action': 'vesync_display',
                })

            if cls_name == 'VeSyncPurifier' and device.supports_child_lock \
                    and device.child_lock is not None:
                items.append({
                    # Translators: Combined info+action label in the device
                    # tree.
                    'text': (_("Kindersicherung: ein - Enter zum Ausschalten") if device.child_lock
                             else _("Kindersicherung: aus - Enter zum Einschalten")),
                    'kind': 'action', 'action': 'vesync_child_lock',
                })

        if cls_name == 'VeSyncPurifier' and device.supports_reset_filter:
            items.append({
                # Translators: Action entry for the filter reset.
                'text': _("Filter-Lebensdauer auf 100% zurücksetzen - Enter zum Ausführen"),
                'kind': 'action', 'action': 'vesync_reset_filter',
            })

        items.append(self._compute_vesync_favorite_item(device, is_favorite_view))
        return items

    def _compute_vesync_favorite_item(self, device, is_favorite_view):
        """Computes the favorite entry (add/remove) for a VeSync device."""
        favorites = get_favorites()
        is_fav = favorites.is_favorite(device.unique_id)
        if is_favorite_view or is_fav:
            return {
                # Translators: Action entry in the device tree.
                'text': _("Aus Favoriten entfernen - Enter"),
                'kind': 'action', 'action': 'favorite_remove',
            }
        return {
            # Translators: Action entry in the device tree.
            'text': _("Zu Favoriten hinzufügen - Enter"),
            'kind': 'action', 'action': 'favorite_add',
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
                self._append_info(device_node, device, item['text'])
            elif kind == 'action':
                self._append_action(device_node, device, item['text'], item['action'])

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

        Called from the settings dialog when VeSync has just been enabled -
        without blocking the UI.
        """
        plugin = self.plugin

        def _login_and_refresh():
            try:
                from .vesync_api import VeSyncAPI
                api = VeSyncAPI(country_code=plugin.vesync_country_code or "DE")

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

                plugin.vesync_api = api

                # Add the VeSync devices to the device list
                existing_uuids = {d.uuid for d in plugin.devices}
                added = 0
                for dev in devices:
                    if dev.uuid not in existing_uuids:
                        plugin.devices.append(dev)
                        added += 1
                log.info(f"VeSync nachträglich initialisiert: {added} neue Geräte")

                # Update the dialog on the UI thread
                wx.CallAfter(self._refresh_after_vesync_init, len(devices))
            except Exception as e:
                log.error(f"VeSync nachträgliche Initialisierung fehlgeschlagen: {e}")
                wx.CallAfter(
                    ui.message,
                    # Translators: Error message for the deferred VeSync login.
                    _("VeSync-Login fehlgeschlagen: {error}").format(error=str(e)[:80])
                )

        threading.Thread(target=_login_and_refresh, daemon=True).start()
        # Translators: Note that the VeSync connection is being established in
        # the background.
        ui.message(_("VeSync wird verbunden..."))

    def _refresh_after_vesync_init(self, count):
        """Updates the tree after VeSync was connected afterwards."""
        if self._is_destroyed:
            return
        try:
            self._load_devices_internal(self.plugin.devices)
            self._refresh_favorites_tree()
            # Translators: Confirmation after loading the VeSync devices
            # afterwards.
            ui.message(_("VeSync: {count} Gerät(e) geladen").format(count=count))
        except Exception as e:
            log.debug(f"Refresh nach VeSync-Init fehlgeschlagen: {e}")

