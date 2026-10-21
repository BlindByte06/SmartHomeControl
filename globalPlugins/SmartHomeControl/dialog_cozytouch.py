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
from .formatting import fmt_decimal
from .errors import log_failure
from .favorites import get_favorites
from .dialog_helpers import _beep
from .constants import BEEP_ON, BEEP_OFF, BEEP_ACTION, BEEP_ERROR
from .cozytouch_devices import (COZYTOUCH_HEATING_MODE_NAMES, _fmt_temp,
                                CozytouchPending)

log = _nvda_log


def _local_datetime_of(timestamp):
    """A Unix time as date and time in the notation of the region.

    The away period used to be written with a fixed "%d.%m.%Y" - German
    under every interface language (see history.local_date).
    """
    import datetime as _dt
    from .history import local_datetime
    return local_datetime(_dt.datetime.fromtimestamp(timestamp))


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
    def _run_cozytouch_cloud_action(self, device, action_call, on_success,
                                    failure_message, worker_done=None,
                                    pending=None):
        """Runs a Cozytouch cloud call in the background.

        The ``device.set_*`` methods block for up to about half a minute
        because of the value verification in cozytouch_api; on the wx thread
        that froze the dialog and NVDA. Hence a thread plus _safe_call_after,
        the same pattern as _favorite_toggle in __init__.py. ``action_call``
        takes no arguments and returns True/False.

        On True, ``worker_done`` runs first, on the worker thread: the
        bookkeeping that must not depend on the menu still being open - the
        history, the local-action mark, the start of a boost. It used to sit
        in ``on_success``, which _safe_call_after drops once the dialog is
        closed: a boost switched on and the menu closed during the wait was
        never written, and its end counted as somebody's doing. Then
        ``on_success`` on the UI thread (announcement, tree rebuild). On
        False ``failure_message`` is announced, menu open or not.

        A change the cloud accepted but the appliance has not shown yet
        (CozytouchPending) is neither - it used to be "could not be
        toggled" for a boost that was running a little later. It is said to
        be on its way, and with ``pending`` (keys confirm, fail,
        history_action, detail, as taken by _expect_cozytouch) the next
        polls confirm it or report that it never arrived.

        Every caller marks the change as made here BEFORE it calls this: the
        poll runs on a thread of its own and can read the new state while
        the command and its read-back are still on their way. The history
        shows it happening - one boost written as "local" and "external" in
        the same second. ``worker_done`` marks it once more, so the window of
        thirty seconds starts from the confirmation.
        """
        if not self._begin_cloud_action():
            return
        plugin = self.plugin
        # See _on_tree in device_dialog.py: the callback belongs to the tree
        # the action was started from.
        tree = self.tree

        def task():
            try:
                ok = action_call()
            except CozytouchPending as e:
                # Nothing on the tree follows - it may be rebuilt again.
                self._action_started_at = 0
                plugin._record_local_cozytouch_action(device.uuid)
                follow = bool(e.field and pending)
                if follow:
                    plugin._expect_cozytouch(device, e.field, e.expected,
                                             **pending)
                _beep(BEEP_ACTION)
                wx.CallAfter(ui.message,
                             self._cozytouch_pending_text(device, follow))
                return
            except Exception as e:
                self._action_started_at = 0
                _beep(BEEP_ERROR)
                log_failure("Cozytouch action failed", e)
                # Translators: Generic Cozytouch error message with detail
                # text.
                wx.CallAfter(
                    ui.message,
                    _("Cozytouch error: {error}").format(error=str(e)[:80]))
                return
            finally:
                self._cloud_action_running = False
            if ok:
                if worker_done is not None:
                    try:
                        worker_done()
                    except Exception as e:
                        log.debug(f"Cozytouch: bookkeeping after success "
                                  f"failed: {e}")
                self._safe_call_after(self._on_tree, tree, on_success)
            else:
                self._action_started_at = 0
                _beep(BEEP_ERROR)
                wx.CallAfter(ui.message, failure_message)

        threading.Thread(target=task, daemon=True).start()

    @staticmethod
    def _cozytouch_pending_text(device, follow):
        """What is said for a change that is on its way, not confirmed yet."""
        if follow:
            # Translators: A change was sent to a device that has not
            # answered the cloud yet.
            return _("{name}: sent, but the device has not answered yet - "
                     "the change is announced as soon as the device reports "
                     "it.").format(name=device.name)
        # Translators: As above, for a change the add-on cannot watch for.
        return _("{name}: sent, but the device has not answered yet - the "
                 "device menu shows the change once it arrives.").format(
            name=device.name)

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
        # The value the appliance will get - set_target_temperature keeps it
        # within the bounds the same way.
        target = max(device.target_temp_min,
                     min(device.target_temp_max, temp))
        # Translators: Confirmation after setting the target temperature.
        confirm = _("{name}: target temperature {temp} degrees").format(
            name=device.name, temp=_fmt_temp(target))
        # Translators: Error message when the API rejects the setting.
        failure = _("Target temperature could not be set")

        def worker_done():
            self.plugin._record_local_cozytouch_action(device.uuid)
            self.plugin._accept_local_change('cozytouch', device)
            # The bare number - the history writes it in the language of the day.
            get_history().log_action(device, 'set_target_temp', str(device.target_temperature))

        def on_success():
            _beep(BEEP_ACTION)
            ui.message(confirm)
            self._rebuild_cozytouch_device_children(item, device)

        # Marked before sending - see _run_cozytouch_cloud_action.
        self.plugin._record_local_cozytouch_action(device.uuid)
        self._run_cozytouch_cloud_action(
            device, lambda: device.set_target_temperature(temp),
            on_success, failure, worker_done=worker_done,
            pending={'confirm': confirm, 'fail': f"{device.name}: {failure}",
                     'history_action': 'set_target_temp',
                     'detail': str(float(target))})

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
        mode_label = COZYTOUCH_HEATING_MODE_NAMES.get(chosen, chosen)
        # Translators: Confirmation after a mode change. {mode} = mode name.
        confirm = _("{name}: mode {mode}").format(name=device.name, mode=mode_label)
        # Translators: Error message when the mode change fails.
        failure = _("Mode could not be set")

        def worker_done():
            self.plugin._record_local_cozytouch_action(device.uuid)
            self.plugin._accept_local_change('cozytouch', device)
            # Mode key, not its label - the display translates it.
            get_history().log_action(device, 'set_mode', str(chosen))

        def on_success():
            _beep(BEEP_ACTION)
            ui.message(confirm)
            self._rebuild_cozytouch_device_children(item, device)

        # Marked before sending - see _run_cozytouch_cloud_action.
        self.plugin._record_local_cozytouch_action(device.uuid)
        self._run_cozytouch_cloud_action(
            device, lambda: device.set_mode(chosen),
            on_success, failure, worker_done=worker_done,
            pending={'confirm': confirm, 'fail': f"{device.name}: {failure}",
                     'history_action': 'set_mode', 'detail': str(chosen)})

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
        def worker_done():
            self.plugin._record_local_cozytouch_action(device.uuid)
            # The appliance does not report the duration back - without it
            # the end of the boost cannot be told from somebody's doing.
            self.plugin._note_local_cozytouch_boost(device.uuid,
                                                    minutes=minutes)
            # Bare number - the display adds the unit in the current
            # language.
            get_history().log_action(
                device, 'set_boost_duration', str(minutes))

        def on_success():
            _beep(BEEP_ACTION)
            # Translators: Confirmation after changing the boost duration.
            ui.message(_("{name}: boost duration {minutes} minutes").format(
                name=device.name, minutes=minutes))
            self._rebuild_cozytouch_device_children(item, device)

        # Marked before sending - see _run_cozytouch_cloud_action.
        self.plugin._record_local_cozytouch_action(device.uuid)
        self._run_cozytouch_cloud_action(
            device, lambda: device.set_boost_duration(minutes),
            on_success,
            # Translators: Error when the cloud rejects the boost duration
            # write (the capability may be read-only on some models).
            _("Boost duration could not be set - the device may not accept "
              "this change"))

    def _handle_cozytouch_boost(self, device, item):
        """Toggles boost mode."""
        new_state = not device.boost_on
        status = _("on") if new_state else _("off")
        # Translators: Confirmation after toggling boost.
        confirm = _("{name}: boost {status}").format(name=device.name, status=status)
        # Translators: Error message when toggling boost fails.
        failure = _("Boost could not be toggled")
        history_action = 'boost_on' if new_state else 'boost_off'

        def worker_done():
            self.plugin._record_local_cozytouch_action(device.uuid)
            self.plugin._accept_local_change('cozytouch', device)
            self.plugin._note_local_cozytouch_boost(device.uuid, on=new_state)
            get_history().log_action(device, history_action, "")

        def on_success():
            _beep(BEEP_ON if new_state else BEEP_OFF)
            ui.message(confirm)
            self._rebuild_cozytouch_device_children(item, device)

        # Marked before sending - see _run_cozytouch_cloud_action.
        self.plugin._record_local_cozytouch_action(device.uuid)
        self._run_cozytouch_cloud_action(
            device, lambda: device.set_boost(new_state),
            on_success, failure, worker_done=worker_done,
            pending={'confirm': confirm, 'fail': f"{device.name}: {failure}",
                     'history_action': history_action})

    def _handle_cozytouch_toggle(self, device, item):
        """Switches hot water production on/off (CAP_DHW_ON)."""
        new_state = not device.is_on
        status = _("on") if new_state else _("off")
        # Translators: Confirmation after switching hot water production
        # on/off.
        confirm = _("{name}: operation {status}").format(name=device.name, status=status)
        # Translators: Error message when toggling hot water production
        # fails.
        failure = _("Hot water could not be toggled")
        history_action = 'toggle_on' if new_state else 'toggle_off'

        def worker_done():
            self.plugin._record_local_toggle(device.uuid, new_state)
            self.plugin._record_local_cozytouch_action(device.uuid)
            self.plugin._accept_local_change('cozytouch', device)
            get_history().log_action(device, history_action, "")

        def on_success():
            _beep(BEEP_ON if new_state else BEEP_OFF)
            ui.message(confirm)
            self._rebuild_cozytouch_device_children(item, device)

        # Marked before sending - see _run_cozytouch_cloud_action.
        self.plugin._record_local_cozytouch_action(device.uuid)
        self._run_cozytouch_cloud_action(
            device, lambda: device.set_dhw(new_state),
            on_success, failure, worker_done=worker_done,
            pending={'confirm': confirm, 'fail': f"{device.name}: {failure}",
                     'history_action': history_action})

    def _handle_cozytouch_away(self, device, item):
        """Turns away mode off directly, or opens the scheduling dialog."""
        if device.away_on:
            # Turn off directly (also works from the "pending" state).
            def worker_done_off():
                self.plugin._record_local_cozytouch_action(device.uuid)
                self.plugin._accept_local_change('cozytouch', device)
                get_history().log_action(device, 'away_off', "")

            def on_success_off():
                _beep(BEEP_OFF)
                # Translators: Confirmation after toggling away mode.
                ui.message(_("{name}: away mode {status}").format(
                    name=device.name, status=_("off")))
                self._rebuild_cozytouch_device_children(item, device)

            # Marked before sending - see _run_cozytouch_cloud_action.
            self.plugin._record_local_cozytouch_action(device.uuid)
            self._run_cozytouch_cloud_action(
                device, lambda: device.set_away(False),
                on_success_off,
                # Translators: Error message when toggling away mode fails.
                _("Away mode could not be toggled"),
                worker_done=worker_done_off)
            return

        # Turn on: let the user schedule the period first.
        window = self._prompt_away_schedule(device)
        if window is None:
            # Translators: Message when the user cancels an action.
            ui.message(_("Cancelled"))
            return
        start_ts, end_ts = window

        def worker_done_on():
            self.plugin._record_local_cozytouch_action(device.uuid)
            self.plugin._accept_local_change('cozytouch', device)
            # The period as Unix seconds - the history writes it in the
            # notation of the region it is read in. Stored as text with a
            # fixed "%d.%m.%Y" it stayed German for good.
            get_history().log_action(
                device, 'away_on', f"{int(start_ts)}-{int(end_ts)}")

        def on_success_on():
            _beep(BEEP_ON)
            # Translators: Confirmation after scheduling away mode.
            # {start}/{end} = date and time.
            ui.message(_("{name}: away from {start} until {end}").format(
                name=device.name, start=_local_datetime_of(start_ts),
                end=_local_datetime_of(end_ts)))
            self._rebuild_cozytouch_device_children(item, device)

        # Marked before sending - see _run_cozytouch_cloud_action.
        self.plugin._record_local_cozytouch_action(device.uuid)
        self._run_cozytouch_cloud_action(
            device, lambda: device.set_away(True, start_ts, end_ts),
            on_success_on,
            # Translators: Error message when toggling away mode fails.
            _("Away mode could not be toggled"),
            worker_done=worker_done_on)

    # ---------------- Time program ----------------
    # The names live in constants.py: the history needs them too, to render
    # a stored time program in the current language.
    from .constants import WEEKDAY_NAMES

    @staticmethod
    def _minutes_to_text(minutes):
        return "%02d:%02d" % (int(minutes) // 60, int(minutes) % 60)

    @staticmethod
    def _text_to_minutes(text):
        """'07:30' -> 450. None when it cannot be read."""
        text = (text or "").strip().replace(".", ":")
        if not text:
            return None
        parts = text.split(":")
        if len(parts) != 2:
            return False
        try:
            hours, minutes = int(parts[0]), int(parts[1])
        except ValueError:
            return False
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            return False
        return hours * 60 + minutes

    def _handle_cozytouch_schedule(self, device, item):
        """Lets the user change one heating window of the time program."""
        from .cozytouch_devices import _format_schedule_windows
        import json as _json

        choice = self._prompt_cozytouch_schedule(device)
        if choice is None:
            # Translators: Message when the user cancels an action.
            ui.message(_("Cancelled"))
            return
        days, windows, before = choice

        result = {"done": [], "failed": [], "actual": None}

        def write():
            # The verdict is not the return value: the cloud confirms the
            # execution, which the air fryer taught us is not the same as the
            # appliance having done it. set_schedule_windows reads the
            # program back, and what it found is reported below.
            for day in days:
                ok, actual = device.set_schedule_windows(day, windows)
                result["actual"] = actual
                (result["done"] if ok else result["failed"]).append(day)
                if not ok:
                    # One refusal is enough: the appliance will not take the
                    # remaining days either, and every further attempt costs
                    # the read-back's patience over again.
                    break
            return True

        def worker_done():
            if not result["done"] or result["failed"]:
                return
            self.plugin._record_local_cozytouch_action(device.uuid)
            get_history().log_action(
                device, 'set_schedule',
                # Language-neutral: the weekday numbers and the windows in
                # minutes. _detail_text renders it in the language the
                # history is read in - "Monday, Saturday: 07:20-17:50".
                f"{','.join(str(d) for d in result['done'])} "
                f"{_json.dumps(result.get('actual') or [], separators=(',', ':'))}")

        def on_success():
            actual = result.get("actual") or []
            actual_text = _format_schedule_windows(actual) or "?"
            wanted_text = _format_schedule_windows(windows) or "?"
            names = ", ".join(self.WEEKDAY_NAMES[d] for d in result["done"])
            if result["done"] and not result["failed"]:
                _beep(BEEP_ON)
                # Translators: Confirmation after changing the time program.
                # {days} = the weekdays it was written to, {schedule} = the
                # new windows.
                ui.message(_("{days}: heating times now {schedule}").format(
                    days=names, schedule=actual_text))
            elif actual == before:
                _beep(BEEP_ERROR)
                # Translators: The appliance accepted the command but kept
                # its old time program. {schedule} = what still stands there.
                ui.message(_("The appliance did not take the change. The "
                             "heating times are still {schedule}").format(
                    schedule=actual_text))
            else:
                _beep(BEEP_ERROR)
                # Translators: The appliance stored something other than what
                # was asked for. {schedule} = what it holds now, {wanted} =
                # what was asked for.
                ui.message(_("The appliance stored {schedule} instead of "
                             "{wanted}").format(
                    schedule=actual_text, wanted=wanted_text))
            self._rebuild_cozytouch_device_children(item, device)

        # Marked before sending - see _run_cozytouch_cloud_action.
        self.plugin._record_local_cozytouch_action(device.uuid)
        self._run_cozytouch_cloud_action(
            device, write, on_success,
            # Translators: Error message when the time program could not be
            # changed.
            _("The time program could not be changed"),
            worker_done=worker_done)

    def _prompt_cozytouch_schedule(self, device):
        """Accessible dialog for one heating window.

        Returns (weekday, windows, before) - the day, the complete new set of
        windows and the set as it was - or None when cancelled.
        """
        from .cozytouch_devices import (_schedule_windows_valid,
                                        SCHEDULE_SLOTS)
        import datetime as _dt

        today = _dt.date.today().weekday()
        state = {"weekday": today}

        def windows_for(day):
            got = device.schedule_windows(day)
            if got is None:
                got = [[0, 0] for _ in range(SCHEDULE_SLOTS)]
            return [list(w) for w in got]

        # Translators: Title of the dialog that changes the time program.
        # {name} = device name.
        dlg = wx.Dialog(self, title=_("Heating times – {name}").format(
            name=device.name))
        sizer = wx.BoxSizer(wx.VERTICAL)
        # Translators: Help text in the dialog that changes the time program.
        intro = _("Hot water is heated inside these windows. Times as HH:MM.\n"
                  "Leaving both fields empty deletes the window.")
        sizer.Add(wx.StaticText(dlg, label=intro), 0, wx.ALL, 8)

        grid = wx.FlexGridSizer(4, 2, 6, 6)
        # Translators: Label of the weekday box in the time program dialog.
        grid.Add(wx.StaticText(dlg, label=_("&Day:")), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        day_ctrl = wx.Choice(dlg, choices=list(self.WEEKDAY_NAMES))
        day_ctrl.SetSelection(today)
        grid.Add(day_ctrl, 1, wx.EXPAND)

        # Translators: Label of the window box in the time program dialog.
        grid.Add(wx.StaticText(dlg, label=_("&Window:")), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        window_ctrl = wx.Choice(dlg, choices=[])
        grid.Add(window_ctrl, 1, wx.EXPAND)

        # Translators: Label of the start field in the time program dialog.
        grid.Add(wx.StaticText(dlg, label=_("&Start:")), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        start_ctrl = wx.TextCtrl(dlg, value="")
        grid.Add(start_ctrl, 1, wx.EXPAND)

        # Translators: Label of the end field in the time program dialog.
        grid.Add(wx.StaticText(dlg, label=_("&End:")), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        end_ctrl = wx.TextCtrl(dlg, value="")
        grid.Add(end_ctrl, 1, wx.EXPAND)
        grid.AddGrowableCol(1)
        sizer.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        # The same windows can go to several days at once - a program is
        # usually the same from Monday to Friday, and setting it seven times
        # by hand is seven times the same dialog.
        #
        # Seven separate check boxes rather than a wx.CheckListBox: the list
        # box says nothing usable when arrowing through it - in a recorded
        # session twenty arrow keys in it produced no speech at all, and the
        # ticked state was never announced. A check box is a check box to the
        # screen reader: it reads its name and its state, and space toggles
        # it. Seven tab stops are the price, and the group heading tells the
        # user what they are for.
        # Translators: Heading of the day group in the time program dialog.
        # The edited windows are written to every day ticked here.
        day_box = wx.StaticBoxSizer(
            wx.VERTICAL, dlg, label=_("Apply to these days"))
        day_checks = []
        for index, day_name in enumerate(self.WEEKDAY_NAMES):
            check = wx.CheckBox(day_box.GetStaticBox(), label=day_name)
            check.SetValue(index == today)
            day_box.Add(check, 0, wx.ALL, 2)
            day_checks.append(check)
        sizer.Add(day_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

        btns = dlg.CreateButtonSizer(wx.OK | wx.CANCEL)
        sizer.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        dlg.SetSizerAndFit(sizer)

        def fill_windows(select=0):
            state["windows"] = windows_for(state["weekday"])
            labels = []
            for index, (start, end) in enumerate(state["windows"], start=1):
                if start == 0 and end == 0:
                    # Translators: Entry for an unused window in the time
                    # program dialog. {number} = 1, 2 or 3. The number has to
                    # stay: two entries reading the same make the arrow keys
                    # sound as if nothing happened.
                    labels.append(_("Window {number}: unused").format(
                        number=index))
                else:
                    # Translators: Entry for a window in the time program
                    # dialog. {number} = 1, 2 or 3, {times} = e.g. 07:00-17:00.
                    labels.append(_("Window {number}: {times}").format(
                        number=index,
                        times="%s-%s" % (self._minutes_to_text(start),
                                         self._minutes_to_text(end))))
            window_ctrl.Set(labels)
            window_ctrl.SetSelection(min(select, len(labels) - 1))
            fill_times()

        def fill_times():
            index = max(0, window_ctrl.GetSelection())
            start, end = state["windows"][index]
            if start == 0 and end == 0:
                start_ctrl.SetValue("")
                end_ctrl.SetValue("")
            else:
                start_ctrl.SetValue(self._minutes_to_text(start))
                end_ctrl.SetValue(self._minutes_to_text(end))

        def checked_days():
            return [i for i, check in enumerate(day_checks) if check.GetValue()]

        def on_day(event):
            previous = state["weekday"]
            state["weekday"] = day_ctrl.GetSelection()
            # As long as only the shown day is ticked, the tick follows it -
            # otherwise a day chosen to look at would silently be written to.
            checked = checked_days()
            if checked in ([], [previous]):
                if previous in checked:
                    day_checks[previous].SetValue(False)
                day_checks[state["weekday"]].SetValue(True)
            fill_windows()
            event.Skip()

        def on_window(event):
            fill_times()
            event.Skip()

        day_ctrl.Bind(wx.EVT_CHOICE, on_day)
        window_ctrl.Bind(wx.EVT_CHOICE, on_window)
        fill_windows()

        result = {}

        def on_ok(event):
            start = self._text_to_minutes(start_ctrl.GetValue())
            end = self._text_to_minutes(end_ctrl.GetValue())
            if start is False or end is False:
                _beep(BEEP_ERROR)
                # Translators: Error message for an invalid time in the time
                # program dialog.
                ui.message(_("Invalid time. Expected HH:MM, for example "
                             "07:00"))
                start_ctrl.SetFocus()
                return
            if (start is None) != (end is None):
                _beep(BEEP_ERROR)
                # Translators: Validation error in the time program dialog.
                ui.message(_("Enter both times, or leave both empty to delete "
                             "the window"))
                (end_ctrl if end is None else start_ctrl).SetFocus()
                return
            index = max(0, window_ctrl.GetSelection())
            windows = [list(w) for w in state["windows"]]
            windows[index] = [0, 0] if start is None else [start, end]
            problem = _schedule_windows_valid(windows)
            if problem == "order":
                _beep(BEEP_ERROR)
                # Translators: Validation error in the time program dialog.
                ui.message(_("The end has to be after the start"))
                end_ctrl.SetFocus()
                return
            if problem == "overlap":
                _beep(BEEP_ERROR)
                # Translators: Validation error in the time program dialog.
                ui.message(_("The windows of one day must not overlap"))
                start_ctrl.SetFocus()
                return
            if problem:
                _beep(BEEP_ERROR)
                # Translators: Validation error in the time program dialog.
                ui.message(_("Times have to lie within one day"))
                start_ctrl.SetFocus()
                return
            if windows == state["windows"]:
                _beep(BEEP_ERROR)
                # Translators: Message when nothing was changed in the time
                # program dialog.
                ui.message(_("Nothing changed"))
                return
            days = checked_days()
            if not days:
                days = [state["weekday"]]
            result["choice"] = (days, windows,
                                [list(w) for w in state["windows"]])
            dlg.EndModal(wx.ID_OK)

        dlg.Bind(wx.EVT_BUTTON, on_ok, id=wx.ID_OK)
        day_ctrl.SetFocus()
        try:
            code = self._show_modal_safely(dlg)
        finally:
            dlg.Destroy()
        if code != wx.ID_OK:
            return None
        return result.get("choice")

    def _prompt_away_schedule(self, device):
        """Accessible dialog for scheduling the away period.

        Two prefilled text fields (start/end) in the date notation of the
        region and a 24-hour time; validation happens in the OK handler so
        the dialog stays open on invalid input and NVDA announces the error.
        The field used to expect DD.MM.YYYY whatever the language - an
        English reader typing 10/19/2026 was told the format was wrong. The
        German and the ISO notation are taken as well.

        Returns (start_ts, end_ts) as Unix timestamps, or None if cancelled.
        """
        import datetime as _dt
        fmt = "%x %H:%M"
        accepted = (fmt, "%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M")
        now = _dt.datetime.now()
        start_default = (now + _dt.timedelta(minutes=2)).strftime(fmt)
        end_default = (now + _dt.timedelta(days=2)).strftime(fmt)

        def parse(text):
            for pattern in accepted:
                try:
                    return _dt.datetime.strptime(text.strip(), pattern)
                except ValueError:
                    continue
            raise ValueError(text)

        # Translators: Title of the away scheduling dialog. {name} = device name.
        dlg = wx.Dialog(self, title=_("Schedule absence – {name}").format(name=device.name))
        sizer = wx.BoxSizer(wx.VERTICAL)
        # Translators: Help text in the away scheduling dialog.
        intro = _("Set the absence period. During the absence, hot water "
                  "production is reduced.")
        # Translators: Date format hint in the away scheduling dialog.
        # {example} = a date and time in the notation the field expects.
        intro += "\n" + _("Date and time as in the example: {example}").format(
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
                start = parse(start_ctrl.GetValue())
                end = parse(end_ctrl.GetValue())
            except ValueError:
                _beep(BEEP_ERROR)
                # Translators: Error message for an invalid date/time input.
                # {example} = a date and time in the notation the field
                # expects.
                ui.message(_("Invalid date or time. Expected as in the "
                             "example: {example}").format(
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
        if device.heating_now:
            # Translators: Line in the device tree: the water heater is
            # heating right now. Not "heating element": the cloud value
            # behind it is also on while only the heat pump heats. F1 picks
            # its help by this text.
            items.append({'text': _("Currently heating"), 'kind': 'info',
                          'action': None})
        sched = device.today_schedule_text
        if sched:
            # Translators: Today's heating time windows, changeable with
            # Enter. {schedule} = window text. The beginning of this line has
            # to stay the same as in the F1 help.
            items.append({'text': _("Today's heating times: {schedule} - press "
                                    "Enter to change").format(schedule=sched),
                          'kind': 'action', 'action': 'cozytouch_schedule'})
        kwh = device.energy_total_kwh
        if kwh is not None:
            items.append({
                # Translators: Total energy consumption. {kwh} = kilowatt
                # hours.
                'text': _("Energy consumption, total: {kwh} kWh").format(
                    kwh=fmt_decimal(kwh, 1)),
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
            # Translators: State of the hot water heat pump in the device tree:
            # switched on by its schedule, not by hand.
            state = _("On (scheduled)") if device.away_pending else _("On")
            _aw_start, _aw_end = device.away_window
            if _aw_end:
                end_str = _local_datetime_of(_aw_end)
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
