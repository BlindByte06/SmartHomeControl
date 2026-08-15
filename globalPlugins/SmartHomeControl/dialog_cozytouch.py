# -*- coding: utf-8 -*-
"""Smart Home Control - Cozytouch/Atlantic-specific dialog methods (mixin).

Follows the same structure as dialog_vesync.py: _compute_*_items as the
single source of truth for the tree items, _fill/_live_update/_rebuild for
the tree and one handler per action. Actions use the combined info+action
label ("target temperature: 55 degrees - Enter to change").

"""

import wx
import ui
import threading
import time
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

from .history import get_history
from .favorites import get_favorites
from .dialog_helpers import _beep
from .constants import BEEP_ON, BEEP_OFF, BEEP_ACTION, BEEP_ERROR
from .cozytouch_devices import COZYTOUCH_HEATING_MODE_NAMES, _fmt_temp

log = _nvda_log


class _CozytouchDialogMixin:
    """Cozytouch methods for SmartHomeControlDialog (hot water heat pump)."""

    # ---------------- Selection helpers (analogous to VeSync) ----------------
    def _cozytouch_choose_from_list(self, title, prompt, values, label_map, current_value):
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

    # ---------------- Handlers ----------------
    def _run_cozytouch_cloud_action(self, action_call, on_success, failure_message):
        """Runs a Cozytouch cloud call in the background.

        The ``device.set_*`` methods block for up to ~15 s because of the
        value verification in cozytouch_api; on the wx thread that froze the
        dialog and NVDA. Hence a thread plus _safe_call_after, the same
        pattern as _favorite_toggle in __init__.py. ``action_call`` takes no
        arguments and returns True/False; ``on_success`` then runs on the UI
        thread (announcement, history, tree rebuild), ``failure_message`` is
        announced on False.
        """
        if not self._begin_cloud_action():
            return

        def task():
            try:
                ok = action_call()
            except Exception as e:
                _beep(BEEP_ERROR)
                log.error(f"Cozytouch error: {e}")
                # Translators: Generic Cozytouch error message with detail
                # text.
                self._safe_call_after(
                    ui.message,
                    _("Cozytouch error: {error}").format(error=str(e)[:80]))
                return
            finally:
                self._cloud_action_running = False
            if ok:
                self._safe_call_after(on_success)
            else:
                _beep(BEEP_ERROR)
                self._safe_call_after(ui.message, failure_message)

        threading.Thread(target=task, daemon=True).start()

    def _handle_cozytouch_temp(self, device, item):
        """Lets the user enter the target temperature."""
        lo = device.target_temp_min
        hi = device.target_temp_max
        current = device.target_temperature
        # Translators: Input prompt for the target temperature. {lo}/{hi} =
        # bounds.
        prompt = _("Target temperature in degrees ({lo} to {hi}):").format(
            lo=_fmt_temp(lo), hi=_fmt_temp(hi))
        default = _fmt_temp(current) if current is not None else ""
        # Translators: Title of the target temperature dialog. {name} = device
        # name.
        dlg = wx.TextEntryDialog(
            self, prompt, _("{name}: target temperature").format(name=device.name), default)
        previous = getattr(self, '_suppress_live_updates', False)
        self._suppress_live_updates = True
        chosen = None
        try:
            if dlg.ShowModal() == wx.ID_OK:
                chosen = dlg.GetValue().strip().replace(",", ".")
        finally:
            self._suppress_live_updates = previous
            dlg.Destroy()
        if not chosen:
            return
        try:
            temp = float(chosen)
        except ValueError:
            _beep(BEEP_ERROR)
            # Translators: Message on non-numeric temperature input.
            ui.message(_("Invalid input"))
            return
        def on_success():
            _beep(BEEP_ACTION)
            # Translators: Confirmation after setting the target
            # temperature.
            ui.message(_("{name}: target temperature {temp} degrees").format(
                name=device.name, temp=_fmt_temp(device.target_temperature)))
            self.plugin._record_local_cozytouch_action(device.uuid)
            get_history().log_action(device, 'set_target_temp', f"{_fmt_temp(device.target_temperature)}°C")
            self._rebuild_cozytouch_device_children(item, device)

        self._run_cozytouch_cloud_action(
            lambda: device.set_target_temperature(temp),
            on_success,
            # Translators: Error message when the API rejects the setting.
            _("Target temperature could not be set"))

    def _handle_cozytouch_mode(self, device, item):
        """Lets the user choose the operating mode."""
        values = list(COZYTOUCH_HEATING_MODE_NAMES.keys())  # ['0','3','4']
        chosen = self._cozytouch_choose_from_list(
            # Translators: Title of the mode selection dialog. {name} = device
            # name.
            _("{name}: choose mode").format(name=device.name),
            # Translators: Prompt in the mode selection dialog.
            _("Choose the operating mode:"),
            values, COZYTOUCH_HEATING_MODE_NAMES, device.mode_value,
        )
        if chosen is None:
            return
        def on_success():
            mode_de = COZYTOUCH_HEATING_MODE_NAMES.get(chosen, chosen)
            _beep(BEEP_ACTION)
            # Translators: Confirmation after a mode change. {mode} = mode
            # name.
            ui.message(_("{name}: mode {mode}").format(name=device.name, mode=mode_de))
            self.plugin._record_local_cozytouch_action(device.uuid)
            # Mode key, not its label - the display translates it.
            get_history().log_action(device, 'set_mode', str(chosen))
            self._rebuild_cozytouch_device_children(item, device)

        self._run_cozytouch_cloud_action(
            lambda: device.set_mode(chosen),
            on_success,
            # Translators: Error message when the mode change fails.
            _("Mode could not be set"))

    def _handle_cozytouch_boost_time(self, device, item):
        """Lets the user change the boost duration (minutes). Experimental:
        the cloud may reject the write; the value verification in the API
        layer reports that honestly."""
        current = device.boost_total_time
        # Translators: Input prompt for the boost duration in minutes.
        prompt = _("Boost duration in minutes (e.g. 60):")
        default = str(int(current)) if current else ""
        dlg = wx.TextEntryDialog(
            self, prompt,
            # Translators: Title of the boost duration dialog. {name} =
            # device name.
            _("{name}: boost duration").format(name=device.name), default)
        previous = getattr(self, '_suppress_live_updates', False)
        self._suppress_live_updates = True
        chosen = None
        try:
            if dlg.ShowModal() == wx.ID_OK:
                chosen = dlg.GetValue().strip()
        finally:
            self._suppress_live_updates = previous
            dlg.Destroy()
        if not chosen:
            return
        try:
            minutes = int(chosen)
            if minutes <= 0 or minutes > 1440:
                raise ValueError
        except ValueError:
            _beep(BEEP_ERROR)
            # Translators: Message on invalid boost duration input.
            ui.message(_("Invalid input - please enter minutes between 1 and "
                         "1440"))
            return
        def on_success():
            _beep(BEEP_ACTION)
            # Translators: Confirmation after changing the boost duration.
            ui.message(_("{name}: boost duration {minutes} minutes").format(
                name=device.name, minutes=minutes))
            self.plugin._record_local_cozytouch_action(device.uuid)
            # Bare number - the display adds the unit in the current
            # language.
            get_history().log_action(
                device, 'set_boost_duration', str(minutes))
            self._rebuild_cozytouch_device_children(item, device)

        self._run_cozytouch_cloud_action(
            lambda: device.set_boost_duration(minutes),
            on_success,
            # Translators: Error when the cloud rejects the boost duration
            # write (the capability may be read-only on some models).
            _("Boost duration could not be set - the device may not accept "
              "this change"))

    def _handle_cozytouch_boost(self, device, item):
        """Toggles boost mode."""
        new_state = not device.boost_on

        def on_success():
            _beep(BEEP_ON if new_state else BEEP_OFF)
            status = _("on") if new_state else _("off")
            # Translators: Confirmation after toggling boost.
            ui.message(_("{name}: boost {status}").format(name=device.name, status=status))
            self.plugin._record_local_cozytouch_action(device.uuid)
            get_history().log_action(
                device, 'boost_on' if new_state else 'boost_off', "")
            self._rebuild_cozytouch_device_children(item, device)

        self._run_cozytouch_cloud_action(
            lambda: device.set_boost(new_state),
            on_success,
            # Translators: Error message when toggling boost fails.
            _("Boost could not be toggled"))

    def _handle_cozytouch_toggle(self, device, item):
        """Switches hot water production on/off (CAP_DHW_ON)."""
        new_state = not device.is_on

        def on_success():
            _beep(BEEP_ON if new_state else BEEP_OFF)
            status = _("on") if new_state else _("off")
            # Translators: Confirmation after switching hot water
            # production on/off.
            ui.message(_("{name}: operation {status}").format(name=device.name, status=status))
            self.plugin._record_local_toggle(device.uuid, new_state)
            self.plugin._record_local_cozytouch_action(device.uuid)
            get_history().log_action(
                device, 'toggle_on' if new_state else 'toggle_off', "")
            self._rebuild_cozytouch_device_children(item, device)

        self._run_cozytouch_cloud_action(
            lambda: device.set_dhw(new_state),
            on_success,
            # Translators: Error message when toggling hot water production
            # fails.
            _("Hot water could not be toggled"))

    def _handle_cozytouch_away(self, device, item):
        """Turns away mode off directly, or opens the scheduling dialog."""
        if device.away_on:
            # Turn off directly (also works from the "pending" state).
            def on_success_off():
                _beep(BEEP_OFF)
                # Translators: Confirmation after toggling away mode.
                ui.message(_("{name}: away mode {status}").format(
                    name=device.name, status=_("off")))
                self.plugin._record_local_cozytouch_action(device.uuid)
                # Translators: History detail: away mode switched off.
                get_history().log_action(device, 'away_off', "")
                self._rebuild_cozytouch_device_children(item, device)

            self._run_cozytouch_cloud_action(
                lambda: device.set_away(False),
                on_success_off,
                # Translators: Error message when toggling away mode fails.
                _("Away mode could not be toggled"))
            return

        # Turn on: let the user schedule the period first.
        window = self._prompt_away_schedule(device)
        if window is None:
            # Translators: Message when the user cancels an action.
            ui.message(_("Cancelled"))
            return
        start_ts, end_ts = window

        def on_success_on():
            _beep(BEEP_ON)
            start_str = time.strftime("%d.%m.%Y %H:%M", time.localtime(start_ts))
            end_str = time.strftime("%d.%m.%Y %H:%M", time.localtime(end_ts))
            # Translators: Confirmation after scheduling away mode.
            # {start}/{end} = date and time.
            ui.message(_("{name}: away from {start} until {end}").format(
                name=device.name, start=start_str, end=end_str))
            self.plugin._record_local_cozytouch_action(device.uuid)
            get_history().log_action(
                device, 'away_on', f"{start_str} - {end_str}")
            self._rebuild_cozytouch_device_children(item, device)

        self._run_cozytouch_cloud_action(
            lambda: device.set_away(True, start_ts, end_ts),
            on_success_on,
            _("Away mode could not be toggled"))

    def _prompt_away_schedule(self, device):
        """Accessible dialog for scheduling the away period.

        Two prefilled text fields (start/end, format DD.MM.YYYY HH:MM);
        validation happens in the OK handler so the dialog stays open on
        invalid input and NVDA announces the error.

        Returns (start_ts, end_ts) as Unix timestamps, or None if cancelled.
        """
        import datetime as _dt
        fmt = "%d.%m.%Y %H:%M"
        now = _dt.datetime.now()
        start_default = (now + _dt.timedelta(minutes=2)).strftime(fmt)
        end_default = (now + _dt.timedelta(days=2)).strftime(fmt)

        # Translators: Title of the away scheduling dialog. {name} = device name.
        dlg = wx.Dialog(self, title=_("Schedule absence – {name}").format(name=device.name))
        sizer = wx.BoxSizer(wx.VERTICAL)
        # Translators: Help text in the away scheduling dialog.
        intro = _("Set the absence period. During the absence, hot water "
                  "production is reduced.")
        # Translators: Date format hint in the away scheduling dialog.
        intro += "\n" + _("Format: DD.MM.YYYY HH:MM (e.g. {example})").format(
            example=start_default)
        sizer.Add(wx.StaticText(dlg, label=intro), 0, wx.ALL, 8)

        grid = wx.FlexGridSizer(2, 2, 6, 6)
        # Translators: Label of the start field in the away scheduling dialog.
        grid.Add(wx.StaticText(dlg, label=_("&Start:")), 0, wx.ALIGN_CENTER_VERTICAL)
        start_ctrl = wx.TextCtrl(dlg, value=start_default)
        grid.Add(start_ctrl, 1, wx.EXPAND)
        # Translators: Label of the end field in the away scheduling dialog.
        grid.Add(wx.StaticText(dlg, label=_("&End:")), 0, wx.ALIGN_CENTER_VERTICAL)
        end_ctrl = wx.TextCtrl(dlg, value=end_default)
        grid.Add(end_ctrl, 1, wx.EXPAND)
        grid.AddGrowableCol(1)
        sizer.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        btns = dlg.CreateButtonSizer(wx.OK | wx.CANCEL)
        sizer.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        dlg.SetSizerAndFit(sizer)

        result = {}

        def on_ok(event):
            try:
                start = _dt.datetime.strptime(start_ctrl.GetValue().strip(), fmt)
                end = _dt.datetime.strptime(end_ctrl.GetValue().strip(), fmt)
            except ValueError:
                _beep(BEEP_ERROR)
                # Translators: Error message for an invalid date/time input.
                ui.message(_("Invalid format. Expected: DD.MM.YYYY HH:MM "
                             "(e.g. {example})").format(
                    example=start_default))
                start_ctrl.SetFocus()
                return
            if end <= start or end.timestamp() <= _dt.datetime.now().timestamp():
                _beep(BEEP_ERROR)
                # Translators: Validation error in the away scheduling dialog.
                ui.message(_("The end must be after the start and in the "
                             "future"))
                end_ctrl.SetFocus()
                return
            result["window"] = (int(start.timestamp()), int(end.timestamp()))
            dlg.EndModal(wx.ID_OK)

        dlg.Bind(wx.EVT_BUTTON, on_ok, id=wx.ID_OK)
        start_ctrl.SetFocus()
        try:
            code = self._show_modal_safely(dlg)
        finally:
            dlg.Destroy()
        if code != wx.ID_OK:
            return None
        return result.get("window")

    # ---------------- Tree construction ----------------
    def _compute_cozytouch_device_label(self, device):
        type_display = device.get_type_display() if hasattr(device, 'get_type_display') else device.type
        if getattr(device, 'is_offline', False):
            # Translators: Tree label of a device without connection.
            return _("{name} ({type}) - offline").format(name=device.name, type=type_display)
        tt = device.target_temperature
        # If the actual heating target (Eco+ reduction or boost) differs
        # from the setpoint, the collapsed label shows BOTH - otherwise the
        # setpoint (say 58°C) is misleading while the device really heats to
        # 53.2°C. Same logic as the expanded "current heating target" row.
        at = device.active_target
        if tt is not None and at is not None and abs(at - tt) >= 0.5:
            # Translators: Tree label when the actual heating target differs
            # from the setpoint. {active} = actual target, {temp} = setpoint.
            return _("{name} ({type}) - target {temp}°C, current heating "
                     "target {active}°C").format(
                name=device.name, type=type_display,
                temp=_fmt_temp(tt), active=_fmt_temp(at))
        if tt is not None:
            # Translators: Tree label with target temperature. {temp} =
            # temperature.
            return _("{name} ({type}) - target {temp}°C").format(
                name=device.name, type=type_display, temp=_fmt_temp(tt))
        return f"{device.name} ({type_display})"

    def _compute_cozytouch_favorite_item(self, device, is_favorite_view):
        favorites = get_favorites()
        is_fav = favorites.is_favorite(device.unique_id)
        if is_favorite_view or is_fav:
            # Translators: Action entry in the device tree.
            return {'text': _("Remove from favorites - Enter"), 'kind': 'action', 'action': 'favorite_remove'}
        # Translators: Action entry in the device tree.
        return {'text': _("Add to favorites - Enter"), 'kind': 'action', 'action': 'favorite_add'}

    def _compute_cozytouch_items(self, device, is_favorite_view=False):
        """Single source of truth for the tree items of a Cozytouch device."""
        items = []
        if getattr(device, 'is_offline', False):
            # Translators: Status entry in the device tree.
            items.append({'text': _("Status: offline"), 'kind': 'info', 'action': None})
            items.append(self._compute_cozytouch_favorite_item(device, is_favorite_view))
            return items

        # ---- Status info ----
        # Note: capability 22 is a mode-dependent setpoint (not a measured
        # value) and is therefore NOT shown as "water temperature".
        hw = device.hot_water_percent
        if hw is not None:
            # Translators: Fill level of the hot water tank in percent.
            text = _("Hot water supply: {percent} percent").format(percent=hw)
            # Rated capacity (liters) optionally configured in the settings ->
            # rough liter estimate (V40 equivalent, hence "approx.").
            capacity = getattr(self.plugin, 'cozytouch_capacity_liters', 0) or 0
            if capacity > 0:
                liters = round(hw / 100.0 * capacity)
                # Translators: Liter estimate after the percentage.
                text += _(" (approx. {liters} liters)").format(liters=liters)
            items.append({'text': text, 'kind': 'info', 'action': None})
        items.append({
            # Translators: Operating state in the device tree.
            'text': _("Operation: on") if device.is_on else _("Operation: off"),
            'kind': 'info', 'action': None,
        })
        if device.offpeak_active:
            # Translators: Note that off-peak electricity is currently used.
            items.append({'text': _("Off-peak tariff active"), 'kind': 'info', 'action': None})
        if device.resistance_on:
            # Translators: Note that the electric heating element is currently
            # heating.
            items.append({'text': _("Electric heating element active "
                                    "(currently heating)"), 'kind': 'info', 'action': None})
        sched = device.today_schedule_text
        if sched:
            # Translators: Today's heating time windows. {schedule} = window
            # text.
            items.append({'text': _("Today's heating times: {schedule}").format(schedule=sched),
                          'kind': 'info', 'action': None})
        kwh = device.energy_total_kwh
        if kwh is not None:
            items.append({
                # Translators: Total energy consumption. {kwh} = kilowatt
                # hours.
                'text': _("Energy consumption, total: {kwh} kWh").format(
                    kwh=f"{kwh:.1f}".replace(".", ",")),
                'kind': 'info', 'action': None,
            })
        # ---- Actions (combined info+action label) ----
        tt = device.target_temperature
        if tt is not None:
            items.append({
                # Translators: Combined info+action label in the device tree.
                'text': _("Target temperature: {temp} degrees - press Enter "
                          "to change").format(temp=_fmt_temp(tt)),
                'kind': 'action', 'action': 'cozytouch_temp',
            })
        # Only show the current heating target when it differs from the
        # setpoint (e.g. Eco reduction or boost override) - explains why the
        # device actually heats warmer/colder than the configured setpoint.
        at = device.active_target
        if at is not None and tt is not None and abs(at - tt) >= 0.5:
            items.append({
                # Translators: Actual heating target when it differs from the
                # setpoint.
                'text': _("Current heating target: {temp} degrees").format(temp=_fmt_temp(at)),
                'kind': 'info', 'action': None,
            })

        if device.mode_value is not None:
            items.append({
                # Translators: Combined info+action label in the device tree.
                'text': _("Operating mode: {mode} - press Enter to change").format(mode=device.mode_name),
                'kind': 'action', 'action': 'cozytouch_mode',
            })
        items.append({
            # Translators: Combined info+action label in the device tree.
            'text': _("Boost: {state} - press Enter to toggle").format(
                state=_("On") if device.boost_on else _("Off")),
            'kind': 'action', 'action': 'cozytouch_boost',
        })
        if device.boost_on:
            # Boost duration directly BELOW the boost switch. Always
            # visible while boost is on; with no value set that is stated
            # instead of hiding the row.
            bt = device.boost_total_time
            if bt:
                items.append({
                    # Translators: Combined info+action label: boost duration
                    # in minutes, changeable with Enter.
                    'text': _("Boost duration: {minutes} minutes - press "
                              "Enter to change").format(
                        minutes=int(bt)),
                    'kind': 'action', 'action': 'cozytouch_boost_time',
                })
            else:
                items.append({
                    # Translators: Combined info+action label: boost has no
                    # time limit set, changeable with Enter.
                    'text': _("Boost duration: no time limit set - press "
                              "Enter to change"),
                    'kind': 'action', 'action': 'cozytouch_boost_time',
                })
        items.append({
            # Translators: Action entry for switching operation on/off.
            'text': _("Turn operation off") if device.is_on else _("Turn "
                                                                    "operation "
                                                                    "on"),
            'kind': 'action', 'action': 'cozytouch_toggle',
        })
        if device.away_on:
            state = _("On (scheduled)") if device.away_pending else _("On")
            _aw_start, _aw_end = device.away_window
            if _aw_end:
                end_str = time.strftime("%d.%m.%Y %H:%M", time.localtime(_aw_end))
                # Translators: Combined info+action label in the device tree:
                # away mode is active/scheduled until the given date and time.
                away_text = _("Away mode: {state} until {end} - press Enter "
                              "to turn off").format(
                    state=state, end=end_str)
            else:
                # Translators: Combined info+action label in the device tree.
                away_text = _("Away mode: {state} - press Enter to toggle").format(state=state)
        else:
            # Translators: Combined info+action label in the device tree.
            away_text = _("Away mode: {state} - press Enter to toggle").format(state=_("Off"))
        items.append({
            'text': away_text,
            'kind': 'action', 'action': 'cozytouch_away',
        })

        # ---- Technical block right before the favorites: Wi-Fi, firmware
        # (Wi-Fi deliberately BEFORE the firmware version). The model already
        # appears in brackets on the collapsed device row (see
        # get_type_display) and is NOT repeated here - only an unknown model
        # ID gets a note, so new models can be reported. ----
        if not device.model_display and device.model_id is not None:
            # Translators: Fallback when the model ID is not yet known to the
            # add-on. Helps users report new models.
            items.append({'text': _("Model ID: {model_id} (unknown model)").format(
                model_id=device.model_id), 'kind': 'info', 'action': None})
        ws = device.wifi_signal
        if ws is not None:
            # Translators: Wi-Fi signal strength in dBm.
            items.append({'text': _("Wi-Fi signal: {dbm} dBm").format(dbm=ws),
                          'kind': 'info', 'action': None})
        ssid = device.wifi_ssid
        if ssid:
            # Translators: Name of the connected Wi-Fi network.
            items.append({'text': _("Wi-Fi network: {ssid}").format(ssid=ssid),
                          'kind': 'info', 'action': None})
        fw = device.firmware
        if fw:
            # Translators: Firmware version of the device.
            items.append({'text': _("Firmware version: {version}").format(version=fw),
                          'kind': 'info', 'action': None})

        items.append(self._compute_cozytouch_favorite_item(device, is_favorite_view))
        return items

    def _fill_cozytouch_device_children(self, device_node, device, is_favorite_view=False):
        items = self._compute_cozytouch_items(device, is_favorite_view=is_favorite_view)
        for item in items:
            if item['kind'] == 'info':
                self._append_info(device_node, device, item['text'])
            elif item['kind'] == 'action':
                self._append_action(device_node, device, item['text'], item['action'])

    def _live_update_cozytouch_children(self, device_item, device):
        """Incremental live update (no rebuild if the structure is unchanged)."""
        new_label = self._compute_cozytouch_device_label(device)
        if self.tree.GetItemText(device_item) != new_label:
            self.tree.SetItemText(device_item, new_label)

        expected = self._compute_cozytouch_items(device, is_favorite_view=False)
        children = []
        child, cookie = self.tree.GetFirstChild(device_item)
        while child.IsOk():
            children.append(child)
            child, cookie = self.tree.GetNextChild(device_item, cookie)

        structure_matches = (len(children) == len(expected))
        if structure_matches:
            for ch, exp in zip(children, expected):
                ch_data = self.tree.GetItemData(ch) or {}
                ch_type = ch_data.get('type')
                if exp['kind'] == 'info' and ch_type != 'info':
                    structure_matches = False
                    break
                if exp['kind'] == 'action':
                    if ch_type != 'action' or ch_data.get('action') != exp['action']:
                        structure_matches = False
                        break

        if not structure_matches:
            self._rebuild_cozytouch_children_preserving_focus(device_item, device)
            return

        for ch, exp in zip(children, expected):
            if self.tree.GetItemText(ch) != exp['text']:
                self.tree.SetItemText(ch, exp['text'])

    def _rebuild_cozytouch_device_children(self, action_item, device):
        """Updates the tree after an action (focus-preserving)."""
        try:
            data = self.tree.GetItemData(action_item)
            if data and data.get('type') == 'device':
                device_node = action_item
            else:
                device_node = self.tree.GetItemParent(action_item)
                if not device_node.IsOk():
                    return
            self._live_update_cozytouch_children(device_node, device)
        except Exception as e:
            log.debug(f"Cozytouch tree update failed: {e}")

    def _rebuild_cozytouch_children_preserving_focus(self, device_item, device):
        """Rebuilds the children without losing focus."""
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

        new_label = self._compute_cozytouch_device_label(device)
        if self.tree.GetItemText(device_item) != new_label:
            self.tree.SetItemText(device_item, new_label)

        self.tree.DeleteChildren(device_item)
        self._fill_cozytouch_device_children(device_item, device)

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

    def _add_cozytouch_devices_to_category(self, cat, devices):
        for device in devices:
            self._add_single_cozytouch_device(cat, device)

    def _add_single_cozytouch_device(self, parent_node, device, is_favorite_view=False):
        label = self._compute_cozytouch_device_label(device)
        device_item = self.tree.AppendItem(parent_node, label)
        self.tree.SetItemData(device_item, {'type': 'device', 'device': device})
        self._fill_cozytouch_device_children(device_item, device, is_favorite_view=is_favorite_view)
        self.tree.Collapse(device_item)

    def _init_cozytouch_in_background(self):
        """Initializes the Cozytouch API in the background and loads devices afterwards.

        Called from the settings dialog when Cozytouch has just been enabled -
        without blocking the UI. This way NVDA does not have to be restarted
        for the device to appear in the tree.
        """
        plugin = self.plugin

        def _login_and_refresh():
            try:
                from .cozytouch_api import CozytouchAPI
                api = CozytouchAPI()
                if hasattr(api, 'set_reauth_callback'):
                    api.set_reauth_callback(plugin._cozytouch_reauth)

                _pw = plugin.cozytouch_password
                try:
                    api.login(plugin.cozytouch_email, _pw)
                finally:
                    _pw = None
                    del _pw

                devices = api.get_devices()

                creds = api.get_credentials()
                if creds.get("token"):
                    plugin.cozytouch_token = creds["token"]
                    plugin.save_settings()

                plugin.cozytouch_api = api

                # Add Cozytouch devices to the shared list
                existing_uuids = {d.uuid for d in plugin.devices}
                added = 0
                for dev in devices:
                    if dev.uuid not in existing_uuids:
                        plugin.devices.append(dev)
                        plugin._previous_cozytouch_states[dev.uuid] = \
                            plugin._snapshot_cozytouch_state(dev)
                        added += 1
                # Set is_logged_in if Cozytouch is the first/only platform, and
                # make sure the background refresh is running.
                if devices:
                    plugin.is_logged_in = True
                    plugin._start_background_refresh()
                log.info(f"Cozytouch initialised late: {added} new devices")

                wx.CallAfter(self._refresh_after_cozytouch_init, len(devices))
            except Exception as e:
                log.error(f"Late Cozytouch initialisation failed: {e}")
                wx.CallAfter(
                    ui.message,
                    # Translators: Error message for the deferred Cozytouch
                    # login.
                    _("Cozytouch login failed: {error}").format(error=str(e)[:80])
                )

        threading.Thread(target=_login_and_refresh, daemon=True).start()
        # Translators: Note that the Cozytouch connection is being established
        # in the background.
        ui.message(_("Connecting to Cozytouch..."))

    def _refresh_after_cozytouch_init(self, count):
        """Updates the tree after Cozytouch was connected afterwards."""
        if self._is_destroyed:
            return
        try:
            self._load_devices_internal(self.plugin.devices)
            self._refresh_favorites_tree()
            # Translators: Confirmation after loading the Cozytouch devices
            # afterwards.
            ui.message(_("Cozytouch: {count} device(s) loaded").format(count=count))
        except Exception as e:
            log.debug(f"Refresh after the Cozytouch init failed: {e}")
