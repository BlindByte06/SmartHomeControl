# -*- coding: utf-8 -*-
"""Smart Home Control - Petlibro-specific dialog methods (mixin).

Follows the same structure as dialog_cozytouch.py: _compute_*_items as the
single source of truth for the tree items, _fill/_live_update/_rebuild for
the tree and one handler per action. Actions use the combined info+action
label ("Feeding plan: on - press Enter to toggle").

Two things are handled more carefully than anywhere else, because they reach
an animal:

- "Feed now" sends exactly one command and then waits for the feeder's own
  record of the dispensing. What is announced is what the feeder reports,
  not what was sent; an unclear outcome is said to be unclear.
- The reminder resets (desiccant, filter, cleaning) ask first, with Cancel
  preselected - a reset without the replacement silences a reminder that is
  still due.

The meals of the feeding plan have a dialog of their own: one line per meal
reads better than a dozen rows in the tree, and the actions for one meal sit
together there.
"""

import threading
import time

import wx
import ui
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

from .errors import log_failure
from .history import get_history
from .favorites import get_favorites
from .dialog_helpers import _beep
from .constants import (
    BEEP_ON, BEEP_OFF, BEEP_ACTION, BEEP_ERROR, WEEKDAY_NAMES,
    PETLIBRO_WATER_MODE_NAMES,
)
from .petlibro_devices import (
    portions_text, ml_text, days_text, record_time_text, PetlibroPending,
    drink_rows, duration_text, drinks_from_records, flatten_records, fault_text,
    PLAN_PENDING, PLAN_TO_BE_SKIPPED, PLAN_DISPENSED, PLAN_SKIPPED,
    SENSING_DURATIONS,
)

log = _nvda_log


def petlibro_error_text(error):
    """One sentence for a failed Petlibro action, from the cloud's code."""
    from .petlibro_api import (
        PetlibroError, CODE_DEVICE_TIMEOUT, CODE_ALREADY_FEEDING,
    )
    if isinstance(error, PetlibroError):
        if error.code == CODE_DEVICE_TIMEOUT:
            # Translators: Error of a Petlibro action: the cloud could not
            # reach the device.
            return _("the device did not answer the cloud")
        if error.code == CODE_ALREADY_FEEDING:
            # Translators: Error of a Petlibro action: the feeder is feeding
            # right now.
            return _("the feeder is feeding right now")
        if error.code is not None:
            # Translators: Error of a Petlibro action with the cloud's code.
            return _("refused by the cloud (code {code})").format(code=error.code)
    if isinstance(error, (ConnectionError, OSError)) or \
            type(error).__name__ in ("ConnectionError", "Timeout", "ReadTimeout",
                                     "ConnectTimeout"):
        # Translators: Error of a Petlibro action: no connection.
        return _("no connection to the cloud")
    return str(error)[:80]


def _repeat_days(plan):
    """The ISO weekdays (1 = Monday) of a plan, from its "[7,1,2]" text."""
    raw = plan.get("repeatDay")
    if isinstance(raw, list):
        values = raw
    else:
        text = str(raw or "").strip().strip("[]")
        values = [part for part in text.split(",") if part.strip()]
    days = []
    for value in values:
        try:
            day = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= day <= 7 and day not in days:
            days.append(day)
    return sorted(days)


def _days_label(days):
    if not days:
        # Translators: A meal that is not repeated - it is given once.
        return _("once")
    if len(days) == 7:
        # Translators: A meal given on every day of the week.
        return _("every day")
    return ", ".join(WEEKDAY_NAMES[d - 1] for d in days)


def _today_state_text(state):
    if state == PLAN_DISPENSED:
        # Translators: State of a meal today: it has been given.
        return _("given today")
    if state == PLAN_PENDING:
        # Translators: State of a meal today: still to come.
        return _("still to come today")
    if state in (PLAN_TO_BE_SKIPPED, PLAN_SKIPPED):
        # Translators: State of a meal today: skipped for today only.
        return _("skipped today")
    return ""


class _PetlibroDialogMixin:
    """Petlibro methods for SmartHomeControlDialog (feeders, fountains)."""

    # ---------------- Helpers ----------------
    def _petlibro_choose_from_list(self, title, prompt, values, labels, current):
        """A selection list; returns the chosen value or None."""
        if not values:
            # Translators: Message when a selection list has no options.
            ui.message(_("No selection available"))
            return None
        dlg = wx.SingleChoiceDialog(self, prompt, title, labels)
        try:
            dlg.SetSelection(values.index(current))
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

    def _petlibro_confirm(self, question, title, yes_label):
        """Yes/No with Cancel preselected; True only on an explicit yes."""
        dlg = wx.MessageDialog(self, question, title,
                               wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION)
        # Translators: Cancel button of a Petlibro safety question.
        dlg.SetYesNoLabels(yes_label, _("&Cancel"))
        try:
            dlg.SetEscapeId(wx.ID_NO)
        except Exception as e:
            log.debug(f"Ignored error in _petlibro_confirm: {e}")
        previous = getattr(self, '_suppress_live_updates', False)
        self._suppress_live_updates = True
        try:
            return dlg.ShowModal() == wx.ID_YES
        finally:
            self._suppress_live_updates = previous
            dlg.Destroy()

    def _run_petlibro_action(self, device, action_call, on_result, pending=None,
                             on_start=None, worker_done=None):
        """Runs a Petlibro call in the background.

        ``action_call`` returns whatever ``on_result`` needs; ``on_result``
        runs on the UI thread. An exception is announced with the reason the
        cloud gave - never retried.

        ``pending`` is what to say once a change the device did not confirm
        in time arrives after all (keys ``confirm``, ``fail``, ``history``,
        ``detail``, as taken by _expect_petlibro). Without it the add-on only
        says that the change is on its way.

        ``on_start`` runs once it is certain the call goes out - after the
        check that no other action is running. "Feed now" used to mark its
        feeding as under way BEFORE that check: with another action still
        running nothing was sent, yet "Feeding, waiting for the feeder" was
        spoken, and for ten minutes every feeding made elsewhere was taken
        for this one and reached neither the speech nor the history.

        ``worker_done(result)`` runs on the worker thread, before
        ``on_result``: the history entry and the marks of a change made here
        belong there. In ``on_result`` they were lost whenever the menu was
        closed before the cloud answered - the UI callback is dropped then.
        """
        if not self._begin_cloud_action():
            return
        # See _on_tree in device_dialog.py: the callbacks belong to the tree
        # the action was started from.
        tree = self.tree
        if on_start is not None:
            on_start()

        def task():
            try:
                result = action_call()
            except PetlibroPending as e:
                self.plugin._record_local_petlibro_action(device.uuid)
                follow = bool(e.key and pending)
                if follow:
                    self.plugin._expect_petlibro(device, e.key, e.expected,
                                                 **pending)
                self._safe_call_after(self._on_tree, tree,
                                      self._announce_petlibro_pending,
                                      device, follow)
                return
            except Exception as e:
                _beep(BEEP_ERROR)
                log_failure(f"Petlibro action failed on {device.name}", e)
                # Translators: A Petlibro action failed. {name} = device,
                # {reason} = why.
                self._safe_call_after(
                    self._on_tree, tree, ui.message,
                    _("{name}: not done - {reason}").format(
                        name=device.name, reason=petlibro_error_text(e)))
                return
            finally:
                self._cloud_action_running = False
            if worker_done is not None:
                try:
                    worker_done(result)
                except Exception as e:
                    log.debug(f"Petlibro bookkeeping after the action failed: {e}")
            self._safe_call_after(self._on_tree, tree, on_result, result)

        threading.Thread(target=task, daemon=True).start()

    def _announce_petlibro_pending(self, device, follow):
        """Says that a change is on its way but not confirmed yet."""
        _beep(BEEP_ACTION)
        if device.on_mains is False and follow:
            # Translators: A change was sent to a Petlibro device on battery
            # that has not answered yet. It sleeps to save the battery.
            ui.message(_("{name}: sent. The device runs on its battery and "
                         "only wakes now and then - the change usually "
                         "arrives within a few minutes and is announced "
                         "then.").format(name=device.name))
        elif device.on_mains is False:
            # Translators: As above, for a change the add-on cannot watch
            # for (a reminder reset).
            ui.message(_("{name}: sent. The device runs on its battery and "
                         "only wakes now and then - the change usually "
                         "arrives within a few minutes, the device menu "
                         "shows it then.").format(name=device.name))
        elif follow:
            # Translators: A change was sent to a Petlibro device that has
            # not answered the cloud yet.
            ui.message(_("{name}: sent, but the device has not answered yet - "
                         "the change is announced as soon as the device "
                         "reports it.").format(name=device.name))
        else:
            # Translators: As above, for a change the add-on cannot watch
            # for (a reminder reset).
            ui.message(_("{name}: sent, but the device has not answered yet - "
                         "the device menu shows the change once it "
                         "arrives.").format(name=device.name))

    def _petlibro_bookkeeping(self, device, history_action, detail=""):
        """What every successful action records - on the worker thread."""
        self.plugin._record_local_petlibro_action(device.uuid)
        self.plugin._accept_local_petlibro_change(device)
        if history_action:
            get_history().log_action(device, history_action, detail)

    def _petlibro_book_if_ok(self, device, history_action, detail=""):
        """A ``worker_done`` for the actions that return ``(ok, actual)``."""
        def bookkeeping(result):
            if result and result[0]:
                self._petlibro_bookkeeping(device, history_action, detail)
        return bookkeeping

    def _petlibro_done(self, device, item):
        """What every successful action shows afterwards (wx thread)."""
        self._rebuild_petlibro_device_children(item, device)

    # ---------------- Feeder ----------------
    def _handle_petlibro_feed(self, device, item):
        """Gives food now - one command, confirmed by the feeder's record."""
        maximum = device.max_portions
        values = list(range(1, maximum + 1))
        labels = [portions_text(n) for n in values]
        last = int(getattr(self.plugin, 'petlibro_last_portions', 1) or 1)
        chosen = self._petlibro_choose_from_list(
            # Translators: Title of the "feed now" selection. {name} = feeder.
            _("{name}: feed now").format(name=device.name),
            # Translators: Prompt of the "feed now" selection. {max} = the
            # most the feeder gives at once.
            _("Portions to give now (at most {max}):").format(max=maximum),
            values, labels, min(last, maximum))
        if chosen is None:
            # Translators: Message when the user cancels an action.
            ui.message(_("Cancelled"))
            return
        self.plugin.petlibro_last_portions = chosen
        try:
            self.plugin.save_settings()
        except Exception as e:
            log.debug(f"Saving the last portions failed: {e}")

        def started():
            # Marked before sending: the scheduler must not announce this
            # feeding as one made elsewhere while the dialog is still
            # waiting. Only here, once it is certain the command goes out.
            self.plugin._record_local_petlibro_action(device.uuid)
            self.plugin._begin_petlibro_feed(device)
            # Translators: Spoken while the feeder is being asked to feed and
            # the add-on waits for its confirmation. {amount} = e.g. "2
            # portions".
            ui.message(_("Feeding {amount}, waiting for the feeder...").format(
                amount=portions_text(chosen)))

        def bookkeeping(result):
            # On the worker thread - the feeding is recorded and the wait for
            # its record ended even when the menu is closed meanwhile.
            status = result.get("status")
            self.plugin._end_petlibro_feed(
                device, confirmed=status in ("confirmed", "failed", "refused"))
            if status in ("confirmed", "failed"):
                self.plugin._mark_petlibro_records_seen(
                    device, only=result.get("record_id"))
            if status == "confirmed":
                given = result.get("portions")
                self._petlibro_bookkeeping(
                    device, 'feed_manual',
                    str(given if given is not None else chosen))

        def on_result(result):
            status = result.get("status")
            if status == "confirmed":
                given = result.get("portions")
                _beep(BEEP_ACTION)
                # Translators: The feeder confirmed a feeding. {amount} =
                # what it reports as given.
                ui.message(_("{name}: {amount} given").format(
                    name=device.name,
                    amount=portions_text(given if given is not None else chosen)))
                self._petlibro_done(device, item)
                return
            _beep(BEEP_ERROR)
            if status == "refused":
                from .petlibro_api import PetlibroError
                reason = petlibro_error_text(
                    PetlibroError("", result.get("code")))
                # Translators: The cloud refused the feeding command, nothing
                # was given. {reason} = why.
                ui.message(_("{name}: not fed - {reason}").format(
                    name=device.name, reason=reason))
            elif status == "failed":
                # Translators: The feeder reports that the feeding failed
                # (blocked outlet, empty container ...).
                ui.message(_("{name}: the feeder reports a failed feeding - "
                             "check the food container and the outlet").format(
                    name=device.name))
            elif result.get("unclear"):
                # Translators: The feeding command got no answer. It is not
                # known whether food was given.
                ui.message(_("{name}: no answer - it is unknown whether food "
                             "was given. Check the last feeding before feeding "
                             "again").format(name=device.name))
            else:
                # Translators: The command was accepted, but the feeder has
                # not confirmed the feeding within half a minute.
                ui.message(_("{name}: command accepted, but the feeder has not "
                             "confirmed the feeding yet. Check the last feeding "
                             "before feeding again").format(name=device.name))
            self._rebuild_petlibro_device_children(item, device)

        def feed():
            try:
                return device.feed_now(chosen)
            except Exception:
                # The command may be out; a record that comes is the late
                # confirmation of it.
                self.plugin._end_petlibro_feed(device, confirmed=False)
                raise

        self._run_petlibro_action(device, feed, on_result, on_start=started,
                                  worker_done=bookkeeping)

    def _handle_petlibro_plan_switch(self, device, item):
        """Switches the whole feeding plan on or off."""
        new_state = not bool(device.feeding_plan_enabled)

        def on_result(result):
            ok, actual = result
            if ok:
                _beep(BEEP_ON if new_state else BEEP_OFF)
                # Translators: Confirmation after switching the feeding plan.
                ui.message(_("{name}: feeding plan {state}").format(
                    name=device.name, state=_("on") if new_state else _("off")))
                self._petlibro_done(device, item)
            else:
                _beep(BEEP_ERROR)
                # Translators: The feeder did not take the change of the
                # feeding plan.
                ui.message(_("{name}: the feeding plan was not changed").format(
                    name=device.name))
                self._rebuild_petlibro_device_children(item, device)

        state_text = _("on") if new_state else _("off")
        self._run_petlibro_action(
            device, lambda: device.set_feeding_plan(new_state), on_result,
            worker_done=self._petlibro_book_if_ok(
                device, 'feeding_plan_on' if new_state else 'feeding_plan_off'),
            pending={
                'confirm': _("{name}: feeding plan {state}").format(
                    name=device.name, state=state_text),
                'fail': _("{name}: the feeding plan was not changed").format(
                    name=device.name),
                'history_action': 'feeding_plan_on' if new_state
                else 'feeding_plan_off'})

    def _handle_petlibro_meals(self, device, item):
        """Opens the list of meals."""
        dlg = PetlibroMealsDialog(self, self.plugin, device)
        try:
            self._show_modal_safely(dlg)
        finally:
            dlg.Destroy()
        self._rebuild_petlibro_device_children(item, device)

    def _handle_petlibro_desiccant(self, device, item):
        cycle = device.desiccant_cycle
        self._petlibro_reset(
            device, item,
            # Translators: Safety question before the desiccant reminder of a
            # feeder is reset. {name} = feeder, {days} = e.g. "14 days".
            _("{name}: has the desiccant been replaced? The reminder then "
              "starts again with {days}.").format(
                name=device.name,
                days=days_text(cycle) if cycle else "?"),
            # Translators: Title of the desiccant reset question.
            _("Reset desiccant reminder"),
            device.reset_desiccant,
            'desiccant_reset',
            # Translators: Confirmation after resetting the desiccant
            # reminder. {days} = e.g. "14 days".
            _("{name}: desiccant reminder reset, next change in {days}"),
            # Translators: The desiccant reminder was not reset.
            _("{name}: the desiccant reminder was not reset"))

    # ---------------- Fountain ----------------
    def _handle_petlibro_filter(self, device, item):
        cycle = device.filter_cycle
        self._petlibro_reset(
            device, item,
            # Translators: Safety question before the filter reminder of a
            # fountain is reset. {name} = fountain, {days} = e.g. "14 days".
            _("{name}: has the filter been replaced? The reminder then starts "
              "again with {days}.").format(
                name=device.name,
                days=days_text(cycle) if cycle else "?"),
            # Translators: Title of the filter reset question.
            _("Reset filter reminder"),
            device.reset_filter,
            'reset_filter',
            # Translators: Confirmation after resetting the filter reminder.
            _("{name}: filter reminder reset, next change in {days}"),
            # Translators: The filter reminder was not reset.
            _("{name}: the filter reminder was not reset"))

    def _handle_petlibro_cleaning(self, device, item):
        cycle = device.cleaning_cycle
        self._petlibro_reset(
            device, item,
            # Translators: Safety question before the cleaning reminder of a
            # fountain is reset. {name} = fountain, {days} = e.g. "14 days".
            _("{name}: has the fountain been cleaned? The reminder then "
              "starts again with {days}.").format(
                name=device.name,
                days=days_text(cycle) if cycle else "?"),
            # Translators: Title of the cleaning reset question.
            _("Reset cleaning reminder"),
            device.reset_cleaning,
            'cleaning_reset',
            # Translators: Confirmation after resetting the cleaning reminder.
            _("{name}: cleaning reminder reset, next cleaning in {days}"),
            # Translators: The cleaning reminder was not reset.
            _("{name}: the cleaning reminder was not reset"))

    def _petlibro_reset(self, device, item, question, title, call,
                        history_action, done_text, failed_text):
        # Translators: Button that confirms a reminder reset.
        if not self._petlibro_confirm(question, title, _("&Reset")):
            ui.message(_("Cancelled"))
            return

        def on_result(result):
            ok, days = result
            if ok:
                _beep(BEEP_ACTION)
                ui.message(done_text.format(name=device.name,
                                            days=days_text(days)))
                self._petlibro_done(device, item)
            else:
                _beep(BEEP_ERROR)
                ui.message(failed_text.format(name=device.name))
                self._rebuild_petlibro_device_children(item, device)

        self._run_petlibro_action(
            device, call, on_result,
            worker_done=self._petlibro_book_if_ok(device, history_action))

    def _handle_petlibro_water_mode(self, device, item):
        values = device.water_modes()
        labels = [PETLIBRO_WATER_MODE_NAMES.get(v, v) for v in values]
        if "constant" in values:
            # Translators: Prompt of the water mode selection.
            prompt = _("Choose the water mode:")
        else:
            # Translators: Prompt of the water mode selection while the
            # fountain runs on its battery: the Petlibro app does not offer
            # constant flow then, and neither does the add-on.
            prompt = _("Choose the water mode. Constant flow is offered on the "
                       "power supply only, as in the Petlibro app:")
        chosen = self._petlibro_choose_from_list(
            # Translators: Title of the water mode selection. {name} = fountain.
            _("{name}: water mode").format(name=device.name),
            prompt, values, labels, device.water_mode)
        if chosen is None:
            ui.message(_("Cancelled"))
            return
        if chosen == device.water_mode:
            return

        def on_result(result):
            ok, actual = result
            if ok:
                _beep(BEEP_OFF if chosen == "off" else BEEP_ON)
                # Translators: Confirmation after changing the water mode.
                # {mode} = new mode.
                ui.message(_("{name}: water mode {mode}").format(
                    name=device.name,
                    mode=PETLIBRO_WATER_MODE_NAMES.get(chosen, chosen)))
                self._petlibro_done(device, item)
            else:
                _beep(BEEP_ERROR)
                # Translators: The fountain kept another water mode than the
                # one chosen. {mode} = what it reports now.
                ui.message(_("{name}: the fountain reports the water mode "
                             "{mode}").format(
                    name=device.name,
                    mode=PETLIBRO_WATER_MODE_NAMES.get(actual, actual or "?")))
                self._rebuild_petlibro_device_children(item, device)

        mode_text = PETLIBRO_WATER_MODE_NAMES.get(chosen, chosen)
        self._run_petlibro_action(
            device, lambda: device.set_water_mode(chosen), on_result,
            worker_done=self._petlibro_book_if_ok(device, 'set_water_mode', chosen),
            pending={
                'confirm': _("{name}: water mode {mode}").format(
                    name=device.name, mode=mode_text),
                # Translators: A water mode sent to a fountain that did not
                # answer in time never arrived. {mode} = the mode sent.
                'fail': _("{name}: the water mode {mode} was not taken "
                          "over").format(name=device.name, mode=mode_text),
                'history_action': 'set_water_mode', 'detail': chosen})

    def _handle_petlibro_flow_duration(self, device, item):
        """Chooses how long the water flows once the sensor noticed the pet."""
        if device.water_mode not in ("sensor_near", "sensor_far"):
            # Translators: The flow duration can only be changed in a sensor
            # water mode.
            ui.message(_("The flow duration applies to the sensor modes only"))
            return
        current = device.sensing_duration
        values = list(SENSING_DURATIONS)
        if current is not None and current not in values:
            values = sorted(values + [current])
        chosen = self._petlibro_choose_from_list(
            # Translators: Title of the flow duration selection. {name} =
            # fountain.
            _("{name}: flow duration").format(name=device.name),
            # Translators: Prompt of the flow duration selection.
            _("How long the water flows once the sensor notices the pet. "
              "Longer gives more time to drink, shorter saves the battery:"),
            values, [duration_text(v) for v in values], current)
        if chosen is None:
            ui.message(_("Cancelled"))
            return
        if chosen == current:
            return
        duration = duration_text(chosen)

        def on_result(result):
            ok, actual = result
            if ok:
                _beep(BEEP_ACTION)
                # Translators: Confirmation after changing the flow duration.
                # {duration} = e.g. "60 seconds".
                ui.message(_("{name}: flow duration {duration}").format(
                    name=device.name, duration=duration))
                self._petlibro_done(device, item)
            else:
                _beep(BEEP_ERROR)
                # Translators: The fountain kept another flow duration.
                # {duration} = what it reports now.
                ui.message(_("{name}: the fountain reports a flow duration of "
                             "{duration}").format(
                    name=device.name,
                    duration=duration_text(actual) if actual is not None else "?"))
                self._rebuild_petlibro_device_children(item, device)

        self._run_petlibro_action(
            device, lambda: device.set_sensing_duration(chosen), on_result,
            worker_done=self._petlibro_book_if_ok(
                device, 'set_flow_duration', str(chosen)),
            pending={
                'confirm': _("{name}: flow duration {duration}").format(
                    name=device.name, duration=duration),
                # Translators: A flow duration sent to a fountain that did not
                # answer in time never arrived.
                'fail': _("{name}: the flow duration was not taken over").format(
                    name=device.name),
                'history_action': 'set_flow_duration', 'detail': str(chosen)})

    def _handle_petlibro_low_water(self, device, item):
        current = device.low_water_ml
        dlg = wx.TextEntryDialog(
            self,
            # Translators: Prompt for the low water warning threshold of a
            # fountain.
            _("Warn when the water is below (millilitres, 650 to 3000):"),
            # Translators: Title of the low water threshold input. {name} =
            # fountain.
            _("{name}: water warning").format(name=device.name),
            str(current) if current else "")
        previous = getattr(self, '_suppress_live_updates', False)
        self._suppress_live_updates = True
        text = None
        try:
            if dlg.ShowModal() == wx.ID_OK:
                text = dlg.GetValue().strip()
        finally:
            self._suppress_live_updates = previous
            dlg.Destroy()
        if not text:
            return
        try:
            ml = int(text)
            # The range the Petlibro app offers (measured by the community
            # on the PLWF116).
            if not 650 <= ml <= 3000:
                raise ValueError
        except ValueError:
            _beep(BEEP_ERROR)
            # Translators: Invalid input for the low water threshold.
            ui.message(_("Invalid input - enter millilitres between 650 and "
                         "3000"))
            return

        def on_result(result):
            ok, actual = result
            if ok:
                _beep(BEEP_ACTION)
                # Translators: Confirmation after changing the low water
                # threshold. {ml} = e.g. "1100 ml".
                ui.message(_("{name}: water warning below {ml}").format(
                    name=device.name, ml=ml_text(ml)))
                self._petlibro_done(device, item)
            else:
                _beep(BEEP_ERROR)
                # Translators: The fountain kept another threshold. {ml} =
                # what it reports now.
                ui.message(_("{name}: the fountain reports a warning below "
                             "{ml}").format(
                    name=device.name,
                    ml=ml_text(actual) if actual is not None else "?"))
                self._rebuild_petlibro_device_children(item, device)

        self._run_petlibro_action(
            device, lambda: device.set_low_water(ml), on_result,
            worker_done=self._petlibro_book_if_ok(device, 'set_low_water', str(ml)),
            pending={
                'confirm': _("{name}: water warning below {ml}").format(
                    name=device.name, ml=ml_text(ml)),
                # Translators: A low water threshold sent to a fountain that
                # did not answer in time never arrived.
                'fail': _("{name}: the water warning was not taken over").format(
                    name=device.name),
                'history_action': 'set_low_water', 'detail': str(ml)})

    def _handle_petlibro_drinks(self, device, item):
        """Opens what the fountain registered hour by hour."""
        dlg = PetlibroDrinksDialog(self, device)
        try:
            self._show_modal_safely(dlg)
        finally:
            dlg.Destroy()

    # ---------------- Both ----------------
    def _handle_petlibro_light(self, device, item):
        new_state = not bool(device.light_on)

        def on_result(result):
            ok, actual = result
            if ok:
                _beep(BEEP_ON if new_state else BEEP_OFF)
                # Translators: Confirmation after switching the light of a
                # Petlibro device.
                ui.message(_("{name}: light {state}").format(
                    name=device.name, state=_("on") if new_state else _("off")))
                self._petlibro_done(device, item)
            else:
                _beep(BEEP_ERROR)
                # Translators: The device did not switch its light.
                ui.message(_("{name}: the light was not switched").format(
                    name=device.name))
                self._rebuild_petlibro_device_children(item, device)

        self._run_petlibro_action(
            device, lambda: device.set_light(new_state), on_result,
            worker_done=self._petlibro_book_if_ok(
                device, 'light_on' if new_state else 'light_off'),
            pending={
                'confirm': _("{name}: light {state}").format(
                    name=device.name,
                    state=_("on") if new_state else _("off")),
                'fail': _("{name}: the light was not switched").format(
                    name=device.name),
                'history_action': 'light_on' if new_state else 'light_off'})

    # ---------------- Tree construction ----------------
    def _compute_petlibro_device_label(self, device):
        base = f"{device.name} ({device.get_type_display()})"
        if device.is_offline:
            # Translators: Tree label of a device without connection.
            return _("{name} ({type}) - offline").format(
                name=device.name, type=device.get_type_display())
        extras = []
        if device.is_feeder:
            nxt = device.next_feed
            if nxt:
                # Translators: Part of a feeder's tree label. {time} = HH:MM,
                # {amount} = e.g. "2 portions".
                extras.append(_("next feeding {time}, {amount}").format(
                    time=nxt[0], amount=portions_text(nxt[1])))
            elif device.feeding_plan_enabled is False:
                extras.append(_("feeding plan off"))
            if device.food_low:
                extras.append(_("food low"))
        elif device.is_fountain:
            if device.constant_idle:
                # Translators: Part of a fountain's tree label: constant flow
                # is set while it runs on its battery, so no water flows.
                extras.append(_("no water flow"))
            percent = device.water_percent
            if percent is not None:
                # Translators: Part of a fountain's tree label. {percent} =
                # fill level.
                extras.append(_("water level {percent} percent").format(
                    percent=percent))
            if device.water_low:
                extras.append(_("water low"))
            if device.water_mode == "off":
                extras.append(_("pump off"))
        if device.fault:
            extras.append(_("fault"))
        if not extras:
            return base
        return f"{base} - {', '.join(extras)}"

    def _compute_petlibro_favorite_item(self, device, is_favorite_view):
        favorites = get_favorites()
        if is_favorite_view or favorites.is_favorite(device.unique_id):
            # Translators: Action entry in the device tree.
            return {'text': _("Remove from favorites - Enter"), 'kind': 'action',
                    'action': 'favorite_remove'}
        # Translators: Action entry in the device tree.
        return {'text': _("Add to favorites - Enter"), 'kind': 'action',
                'action': 'favorite_add'}

    @staticmethod
    def _petlibro_info(text):
        return {'text': text, 'kind': 'info', 'action': None}

    @staticmethod
    def _petlibro_action(text, action):
        return {'text': text, 'kind': 'action', 'action': action}

    def _compute_petlibro_items(self, device, is_favorite_view=False):
        """Single source of truth for the tree items of a Petlibro device."""
        info, action = self._petlibro_info, self._petlibro_action
        items = []
        if device.is_offline:
            # Translators: Status entry in the device tree.
            items.append(info(_("Status: offline")))
            items.append(self._compute_petlibro_favorite_item(device, is_favorite_view))
            return items

        if device.fault:
            detail = fault_text(device.fault_message)
            if detail:
                # Translators: A Petlibro device reports a fault. {detail} =
                # what it is, e.g. "water tank empty or not seated on its
                # base".
                items.append(info(_("Fault: {detail}").format(detail=detail)))
            else:
                # Translators: A Petlibro device reports a fault without text.
                items.append(info(_("Fault reported by the device")))

        if device.is_feeder:
            items.extend(self._compute_petlibro_feeder_items(device))
        elif device.is_fountain:
            items.extend(self._compute_petlibro_fountain_items(device))
        else:
            # Translators: A Petlibro product the add-on does not know yet.
            # {model} = its model number.
            items.append(info(_("Model {model} is not supported yet - only "
                                "the status is shown").format(
                model=device.model or "?")))

        battery = device.battery_text()
        if battery:
            # Translators: Battery of a Petlibro device in the device tree.
            # {battery} = e.g. "50 percent, on battery".
            items.append(info(_("Battery: {battery}").format(battery=battery)))
        if device.light_on is not None:
            items.append(action(
                # Translators: Combined info+action label: the light of a
                # Petlibro device, switchable with Enter.
                _("Light: {state} - press Enter to toggle").format(
                    state=_("On") if device.light_on else _("Off")),
                'petlibro_light'))
        ws = device.wifi_signal
        if ws is not None:
            items.append(info(_("Wi-Fi signal: {dbm} dBm").format(dbm=ws)))
        if device.firmware:
            items.append(info(_("Firmware version: {version}").format(
                version=device.firmware)))
        items.append(self._compute_petlibro_favorite_item(device, is_favorite_view))
        return items

    def _compute_petlibro_feeder_items(self, device):
        info, action = self._petlibro_info, self._petlibro_action
        items = []
        low = device.food_low
        if low is not None:
            # Translators: Food supply of a feeder in the device tree.
            items.append(info(_("Food supply: low - refill") if low
                              else _("Food supply: sufficient")))
        count, portions = device.today_count, device.today_portions
        if count is not None:
            # Translators: What a feeder gave today, planned meals and
            # feedings by hand together. {times} = number of feedings,
            # {amount} = e.g. "8 portions".
            items.append(info(_("Fed today in total (plan and by hand): "
                                "{times} times, {amount}").format(
                times=count, amount=portions_text(portions or 0))))
        last = device.last_feed
        if last:
            when = record_time_text(last["time"])
            amount = portions_text(last["portions"] or 0)
            if last["manual"]:
                # Translators: The last feeding was started by hand (here, in
                # the app or at the feeder). {time}, {amount}.
                text = _("Last feeding: {time}, {amount}, by hand").format(
                    time=when, amount=amount)
            else:
                label = device.plan_label(last["plan_id"])
                # Translators: The last feeding came from the feeding plan.
                # {time}, {amount}.
                text = _("Last feeding: {time}, {amount}").format(
                    time=when, amount=amount)
                if label:
                    # The meal's own name as typed in the app.
                    text = f"{text} ({label})"
            items.append(info(text))
        nxt = device.next_feed
        if nxt:
            # Translators: The next planned meal. {time} = HH:MM, {amount}.
            items.append(info(_("Next feeding: {time}, {amount}").format(
                time=nxt[0], amount=portions_text(nxt[1]))))
        # Translators: Action entry: give food now.
        items.append(action(_("Feed now - press Enter"), 'petlibro_feed'))
        plan = device.feeding_plan_enabled
        if plan is not None:
            items.append(action(
                # Translators: Combined info+action label: the feeding plan
                # of a feeder, switchable with Enter.
                _("Feeding plan: {state} - press Enter to toggle").format(
                    state=_("On") if plan else _("Off")),
                'petlibro_plan'))
        done = device.meals_done_text()
        if done:
            # Translators: Combined info+action label: today's meals of the
            # feeding plan - feedings by hand do not count here. Enter opens
            # the list of all meals of the feeding plan. {done} = e.g. "3 of
            # 4 given".
            items.append(action(_("Planned meals today: {done} - press Enter "
                                  "for the meals of the feeding plan").format(
                                      done=done),
                                'petlibro_meals'))
        else:
            # Translators: Action entry when the feeding plan has no meal
            # for today; Enter opens the list of all meals of the feeding
            # plan.
            items.append(action(_("Planned meals: none today - press Enter "
                                  "for the meals of the feeding plan"),
                                'petlibro_meals'))
        days = device.desiccant_days
        if days is not None:
            if days > 0:
                # Translators: Combined info+action label: when the desiccant
                # of a feeder is due. {days} = e.g. "5 days".
                text = _("Desiccant: change in {days} - press Enter once "
                         "the desiccant has been replaced").format(
                    days=days_text(days))
            elif days == 0:
                # Translators: Combined info+action label: the desiccant is
                # due today.
                text = _("Desiccant: change due today - press Enter once "
                         "the desiccant has been replaced")
            else:
                # Translators: Combined info+action label: the desiccant is
                # overdue. {days} = e.g. "27 days".
                text = _("Desiccant: overdue by {days} - press Enter once "
                         "the desiccant has been replaced").format(
                    days=days_text(-days))
            items.append(action(text, 'petlibro_desiccant'))
        return items

    def _compute_petlibro_fountain_items(self, device):
        info, action = self._petlibro_info, self._petlibro_action
        items = []
        level = device.water_level_text()
        if level:
            if device.water_low:
                # Translators: Water level of a fountain below the warning
                # threshold. {level} = e.g. "20 percent, about 600 ml".
                text = _("Water level: {level} - below the warning, refill").format(
                    level=level)
            else:
                # Translators: Water level of a fountain. {level} = e.g.
                # "73 percent, about 2100 ml".
                text = _("Water level: {level}").format(level=level)
            items.append(info(text))
        drunk = device.drink_today_ml
        if drunk is not None:
            count = device.drink_today_count
            if count is not None:
                # Translators: Water drunk today. {ml} = e.g. "35 ml",
                # {count} = number of drinks.
                text = _("Drunk today: {ml} in {count} drinks").format(
                    ml=ml_text(drunk), count=count)
            else:
                # Translators: Water drunk today. {ml} = e.g. "35 ml".
                text = _("Drunk today: {ml}").format(ml=ml_text(drunk))
            yesterday = device.drink_yesterday_ml
            if yesterday is not None:
                # Translators: Appended to the water drunk today. {ml} = e.g.
                # "43 ml".
                text += _(", yesterday {ml}").format(ml=ml_text(yesterday))
            # Translators: Appended to the drinking line: Enter opens the
            # list of the day's drinks.
            text += _(" - press Enter for the single drinks")
            items.append(action(text, 'petlibro_drinks'))
        mode = device.water_mode
        if device.constant_idle:
            items.append(action(
                # Translators: Combined info+action label: constant flow is
                # stored, but the fountain runs on its battery, where it only
                # gives water when its sensor notices an animal - so no water
                # flows at all.
                _("Water mode: constant flow - does not run on the battery, "
                  "no water flows. Press Enter to choose a sensor mode"),
                'petlibro_water_mode'))
        elif mode:
            items.append(action(
                # Translators: Combined info+action label: the water mode of a
                # fountain. {mode} = e.g. "Sensor, near".
                _("Water mode: {mode} - press Enter to change").format(
                    mode=PETLIBRO_WATER_MODE_NAMES.get(mode, mode)),
                'petlibro_water_mode'))
        if mode in ("sensor_near", "sensor_far"):
            seconds = device.sensing_duration
            supported = device.sensing_duration_supported
            if seconds is not None and supported is not False:
                items.append(action(
                    # Translators: Combined info+action label: how long a
                    # fountain gives water once its sensor noticed the pet.
                    # {duration} = e.g. "45 seconds".
                    _("Flow duration: {duration} - press Enter to change").format(
                        duration=duration_text(seconds)),
                    'petlibro_flow_duration'))
            elif seconds is not None:
                items.append(info(
                    # Translators: Info line of a fountain whose firmware is
                    # too old to change the flow duration. {duration} = e.g.
                    # "45 seconds", {version} = the firmware it needs.
                    _("Flow duration: {duration} - changing it needs firmware "
                      "{version} or later").format(
                        duration=duration_text(seconds), version="0.3.25")))
        limit = device.low_water_ml
        if limit:
            items.append(action(
                # Translators: Combined info+action label: the low water
                # warning threshold. {ml} = e.g. "1100 ml".
                _("Water warning below: {ml} - press Enter to change").format(
                    ml=ml_text(limit)),
                'petlibro_low_water'))
        days = device.filter_days
        if days is not None:
            if days > 0:
                # Translators: Combined info+action label: when the filter of
                # a fountain is due. {days} = e.g. "12 days".
                text = _("Filter: change in {days} - press Enter once the "
                         "filter has been changed").format(days=days_text(days))
            elif days == 0:
                # Translators: Combined info+action label: the filter of a
                # fountain is due today.
                text = _("Filter: change due today - press Enter once the "
                         "filter has been changed")
            else:
                # Translators: Combined info+action label: the filter change
                # is overdue. {days} = e.g. "3 days".
                text = _("Filter: change overdue by {days} - press Enter once "
                         "the filter has been changed").format(
                    days=days_text(-days))
            items.append(action(text, 'petlibro_filter'))
        days = device.cleaning_days
        if days is not None:
            if days > 0:
                # Translators: Combined info+action label: when the fountain
                # should be cleaned. {days} = e.g. "12 days".
                text = _("Cleaning: due in {days} - press Enter once the "
                         "fountain has been cleaned").format(days=days_text(days))
            elif days == 0:
                # Translators: Combined info+action label: the fountain should
                # be cleaned today.
                text = _("Cleaning: due today - press Enter once the "
                         "fountain has been cleaned")
            else:
                # Translators: Combined info+action label: the cleaning is
                # overdue. {days} = e.g. "3 days".
                text = _("Cleaning: overdue by {days} - press Enter once the "
                         "fountain has been cleaned").format(
                    days=days_text(-days))
            items.append(action(text, 'petlibro_cleaning'))
        return items

    def _fill_petlibro_device_children(self, device_node, device, is_favorite_view=False):
        for item in self._compute_petlibro_items(device, is_favorite_view=is_favorite_view):
            if item['kind'] == 'info':
                self._append_info(device_node, device, item['text'])
            else:
                self._append_action(device_node, device, item['text'], item['action'])

    def _live_update_petlibro_children(self, device_item, device):
        """Incremental live update (no rebuild if the structure is unchanged)."""
        new_label = self._compute_petlibro_device_label(device)
        if self.tree.GetItemText(device_item) != new_label:
            self.tree.SetItemText(device_item, new_label)

        data = self.tree.GetItemData(device_item) or {}
        expected = self._compute_petlibro_items(
            device, is_favorite_view=bool(data.get('favorite_view')))
        children = []
        child, cookie = self.tree.GetFirstChild(device_item)
        while child.IsOk():
            children.append(child)
            child, cookie = self.tree.GetNextChild(device_item, cookie)

        matches = len(children) == len(expected)
        if matches:
            for ch, exp in zip(children, expected):
                ch_data = self.tree.GetItemData(ch) or {}
                if exp['kind'] == 'info' and ch_data.get('type') != 'info':
                    matches = False
                    break
                if exp['kind'] == 'action' and (
                        ch_data.get('type') != 'action'
                        or ch_data.get('action') != exp['action']):
                    matches = False
                    break
        if not matches:
            self._rebuild_petlibro_children_preserving_focus(device_item, device)
            return
        for ch, exp in zip(children, expected):
            if self.tree.GetItemText(ch) != exp['text']:
                self.tree.SetItemText(ch, exp['text'])

    def _rebuild_petlibro_device_children(self, action_item, device):
        """Updates the tree after an action (focus-preserving)."""
        try:
            if getattr(self, '_is_destroyed', False):
                return
            data = self.tree.GetItemData(action_item)
            if data and data.get('type') == 'device':
                device_node = action_item
            else:
                device_node = self.tree.GetItemParent(action_item)
                if not device_node.IsOk():
                    return
            self._live_update_petlibro_children(device_node, device)
        except Exception as e:
            log.debug(f"Petlibro tree update failed: {e}")

    def _rebuild_petlibro_children_preserving_focus(self, device_item, device):
        """Rebuilds the children without losing the focus position."""
        focused = self.tree.GetFocusedItem()
        index = -1
        if focused.IsOk():
            child, cookie = self.tree.GetFirstChild(device_item)
            pos = 0
            while child.IsOk():
                if child == focused:
                    index = pos
                    break
                pos += 1
                child, cookie = self.tree.GetNextChild(device_item, cookie)

        new_label = self._compute_petlibro_device_label(device)
        if self.tree.GetItemText(device_item) != new_label:
            self.tree.SetItemText(device_item, new_label)
        data = self.tree.GetItemData(device_item) or {}
        self.tree.DeleteChildren(device_item)
        self._fill_petlibro_device_children(
            device_item, device, is_favorite_view=bool(data.get('favorite_view')))

        if index >= 0:
            child, cookie = self.tree.GetFirstChild(device_item)
            pos = 0
            target = None
            last = child
            while child.IsOk():
                if pos == index:
                    target = child
                    break
                last = child
                pos += 1
                child, cookie = self.tree.GetNextChild(device_item, cookie)
            if target is None and last and last.IsOk():
                target = last
            if target and target.IsOk():
                self._suppress_tree_focus_event = True
                try:
                    self.tree.SelectItem(target)
                    self.tree.SetFocusedItem(target)
                finally:
                    wx.CallAfter(setattr, self, '_suppress_tree_focus_event', False)

    def _add_petlibro_devices_to_category(self, cat, devices):
        for device in devices:
            self._add_single_petlibro_device(cat, device)

    def _add_single_petlibro_device(self, parent_node, device, is_favorite_view=False):
        label = self._compute_petlibro_device_label(device)
        device_item = self.tree.AppendItem(parent_node, label)
        self.tree.SetItemData(device_item, {
            'type': 'device', 'device': device,
            'favorite_view': bool(is_favorite_view)})
        self._fill_petlibro_device_children(device_item, device,
                                            is_favorite_view=is_favorite_view)
        self.tree.Collapse(device_item)

    def _init_petlibro_in_background(self):
        """Connects Petlibro after it was enabled in the settings.

        Same path as Cozytouch: no NVDA restart needed for the devices to
        appear in the tree.
        """
        plugin = self.plugin
        if not plugin.begin_platform_login('petlibro'):
            log.info("Petlibro login already running - not starting a second one")
            return

        def _login_and_refresh():
            try:
                from .petlibro_api import PetlibroAPI
                api = PetlibroAPI()
                api.set_reauth_callback(plugin._petlibro_reauth)
                if plugin.petlibro_token:
                    api.set_credentials(plugin.petlibro_token)
                else:
                    _pw = plugin.petlibro_password
                    try:
                        api.login(plugin.petlibro_email, _pw)
                    finally:
                        _pw = None
                        del _pw
                devices = api.get_devices()
                creds = api.get_credentials()
                if creds.get("token") and creds["token"] != plugin.petlibro_token:
                    plugin.petlibro_token = creds["token"]
                    plugin.save_settings()
                old_api, plugin.petlibro_api = plugin.petlibro_api, api
                if old_api is not None and old_api is not api:
                    try:
                        old_api.logout()
                    except Exception as e:
                        log.debug(f"Closing the old Petlibro session failed: {e}")
                count = plugin.replace_platform_devices('petlibro', devices)
                for dev in devices:
                    plugin._seed_petlibro_state(dev)
                if devices:
                    plugin.is_logged_in = True
                    plugin._start_background_refresh()
                log.info(f"Petlibro initialised late: {count} devices")
                wx.CallAfter(self._refresh_after_petlibro_init, len(devices))
            except Exception as e:
                log_failure("Late Petlibro initialisation failed", e)
                wx.CallAfter(self._offer_login_reentry, 'petlibro', e)
            finally:
                plugin.end_platform_login('petlibro')

        threading.Thread(target=_login_and_refresh, daemon=True).start()
        # Translators: Note that the Petlibro connection is being established
        # in the background.
        ui.message(_("Connecting to Petlibro..."))

    def _refresh_after_petlibro_init(self, count):
        if self._is_destroyed:
            return
        try:
            self._load_devices_internal(self.plugin.devices)
            self._refresh_favorites_tree()
            # Translators: Confirmation after loading the Petlibro devices
            # afterwards.
            ui.message(_("Petlibro: {count} device(s) loaded").format(count=count))
        except Exception as e:
            log.debug(f"Refresh after the Petlibro init failed: {e}")


class PetlibroMealsDialog(wx.Dialog):
    """The meals of a feeder's plan: one line each, the actions beside them."""

    def __init__(self, parent, plugin, device):
        super().__init__(
            parent,
            # Translators: Title of the meals dialog. {name} = feeder.
            title=_("{name}: meals").format(name=device.name),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.plugin = plugin
        self.device = device
        self._busy = False
        self._is_destroyed = False
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy)
        self._rows = []  # plan dicts, in the order of the list

        sizer = wx.BoxSizer(wx.VERTICAL)
        # Translators: Label of the list of meals in the meals dialog.
        label = wx.StaticText(self, label=_("&Meals:"))
        sizer.Add(label, flag=wx.LEFT | wx.TOP, border=10)
        self.list = wx.ListBox(self, style=wx.LB_SINGLE)
        # Translators: Accessible name of the list of meals.
        self.list.SetName(_("Meals"))
        self.list.SetMinSize((460, 200))
        self.list.Bind(wx.EVT_LISTBOX, lambda evt: self._update_buttons())
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda evt: self._on_edit(None))
        sizer.Add(self.list, 1, wx.EXPAND | wx.ALL, 10)

        buttons = wx.WrapSizer()

        def button(text, handler):
            btn = wx.Button(self, label=text)
            btn.Bind(wx.EVT_BUTTON, handler)
            buttons.Add(btn, flag=wx.ALL, border=4)
            return btn

        # Translators: Button in the meals dialog: skip the selected meal for
        # today only (its label changes to "Give today" for a skipped one).
        self.skipBtn = button(_("S&kip today"), self._on_skip)
        # Translators: Button in the meals dialog: switch the selected meal
        # off or on for good.
        self.enableBtn = button(_("Switch &off"), self._on_enable)
        # Translators: Button in the meals dialog: change the selected meal.
        self.editBtn = button(_("&Change..."), self._on_edit)
        # Translators: Button in the meals dialog: add a meal.
        self.addBtn = button(_("&New meal..."), self._on_add)
        # Translators: Button in the meals dialog: delete the selected meal.
        self.deleteBtn = button(_("&Delete..."), self._on_delete)
        # Translators: Button in the meals dialog: skip every meal of today
        # (its label changes to "Give all meals today" when they are skipped).
        self.todayAllBtn = button(_("Skip &all meals today"), self._on_today_all)
        sizer.Add(buttons, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=6)

        # Translators: Button that closes the meals dialog.
        closeBtn = wx.Button(self, wx.ID_CANCEL, _("C&lose"))
        sizer.Add(closeBtn, flag=wx.ALIGN_RIGHT | wx.ALL, border=10)
        self.SetEscapeId(wx.ID_CANCEL)

        self.SetSizerAndFit(sizer)
        self._fill()
        self.list.SetFocus()
        self._reload(announce=False)

    def _on_destroy(self, event):
        if event.GetEventObject() is self:
            self._is_destroyed = True
        event.Skip()

    def _call_after(self, func, *args):
        def run():
            if self._is_destroyed:
                return
            try:
                func(*args)
            except RuntimeError:
                pass
        wx.CallAfter(run)

    # ---------------- Content ----------------
    def _today_state(self, plan_id):
        for today in self.device.today_plans:
            if today.get("planId") == plan_id:
                return today.get("state")
        return None

    def _row_text(self, plan):
        label = str(plan.get("label") or "").strip()
        parts = []
        if label:
            parts.append(label)
        parts.append(str(plan.get("executionTime") or "?"))
        amount = plan.get("grainNum")
        try:
            parts.append(portions_text(int(amount)))
        except (TypeError, ValueError):
            pass
        days = _repeat_days(plan)
        if len(days) != 7:
            parts.append(_days_label(days))
        if not plan.get("enable", True):
            # Translators: A meal of the plan that is switched off.
            parts.append(_("switched off"))
        else:
            state = _today_state_text(self._today_state(plan.get("id")))
            if state:
                parts.append(state)
        return ", ".join(parts)

    def _fill(self, keep_id=None):
        if keep_id is None:
            current = self._selected()
            keep_id = current.get("id") if current else None
        plans = sorted(self.device.plans,
                       key=lambda p: str(p.get("executionTime") or ""))
        self._rows = plans
        self.list.Set([self._row_text(p) for p in plans] or [
            # Translators: Placeholder in the meals list when the plan is
            # empty.
            _("No meals planned")])
        target = 0
        for index, plan in enumerate(plans):
            if plan.get("id") == keep_id:
                target = index
                break
        if self.list.GetCount():
            self.list.SetSelection(target)
        self._update_buttons()

    def _selected(self):
        index = self.list.GetSelection()
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    def _update_buttons(self):
        plan = self._selected()
        has = plan is not None
        for btn in (self.enableBtn, self.editBtn, self.deleteBtn):
            btn.Enable(has and not self._busy)
        state = self._today_state(plan.get("id")) if plan else None
        skippable = has and plan.get("enable", True) and state in (
            PLAN_PENDING, PLAN_TO_BE_SKIPPED)
        self.skipBtn.Enable(bool(skippable) and not self._busy)
        # Translators: Button in the meals dialog for a meal skipped today:
        # give it after all.
        self.skipBtn.SetLabel(_("Give &today") if state == PLAN_TO_BE_SKIPPED
                              else _("S&kip today"))
        if has:
            # Translators: Button in the meals dialog for a switched-off meal.
            self.enableBtn.SetLabel(_("Switch &off") if plan.get("enable", True)
                                    else _("Switch &on"))
        # Translators: Button in the meals dialog when all of today's meals
        # are skipped: give them after all.
        self.todayAllBtn.SetLabel(_("Give &all meals today")
                                  if self.device.today_all_skipped
                                  else _("Skip &all meals today"))
        self.todayAllBtn.Enable(bool(self.device.today_plans) and not self._busy)
        self.addBtn.Enable(not self._busy)
        self.Layout()

    def _reload(self, announce=True, keep_id=None, message=None, verify=None):
        """Reads plan and today's meals again, then refills the list.

        ``verify`` (optional) is asked after the reload whether the feeder
        shows the change; when it does not, that is said instead of
        ``message``.
        """
        def task():
            import time as _time
            shown = None
            for attempt in range(3 if verify else 1):
                if attempt:
                    _time.sleep(2)
                self.device.refresh_plans()
                self.device.refresh_today()
                if verify is None:
                    break
                shown = verify()
                if shown:
                    break
            text = message
            if verify is not None and not shown:
                # Translators: A change of the meals was accepted by the cloud,
                # but the list of the feeder does not show it yet.
                text = _("Sent, but the feeder does not show the change yet - "
                         "the list is read again when the dialog is opened")
                log.warning("Petlibro: meal change not visible after reload")
            self._call_after(self._after_reload, keep_id, text)
        threading.Thread(target=task, daemon=True).start()

    def _after_reload(self, keep_id, message):
        self._fill(keep_id=keep_id)
        if message:
            ui.message(message)

    # ---------------- Actions ----------------
    def _run(self, call, done_message, history_action=None, detail="",
             keep_id=None, verify=None):
        """Sends one change, reloads and announces what the feeder shows."""
        if self._busy:
            ui.message(_("Please wait - the previous action is still running"))
            return
        self._busy = True
        self._update_buttons()
        device = self.device
        plugin = self.plugin

        def task():
            from .petlibro_api import PetlibroError, CODE_DEVICE_TIMEOUT
            try:
                call()
            except PetlibroError as e:
                self._busy = False
                if e.code != CODE_DEVICE_TIMEOUT:
                    _beep(BEEP_ERROR)
                    log_failure("Petlibro meal action failed", e)
                    self._reload(keep_id=keep_id, message=_(
                        "Not done - {reason}").format(
                        reason=petlibro_error_text(e)))
                    return
                # The feeder did not answer in time - on its battery it
                # sleeps like the fountain. The change may well arrive.
                plugin._record_local_petlibro_action(device.uuid)
                _beep(BEEP_ACTION)
                # Translators: A change of the meals was sent, but the feeder
                # did not answer in time.
                pending_text = _("Sent, but the feeder has not answered yet - "
                                 "the list shows the change once it arrives")
                self._reload(keep_id=keep_id, message=pending_text)
                return
            except Exception as e:
                _beep(BEEP_ERROR)
                log_failure("Petlibro meal action failed", e)
                # Translators: A change of the meals failed. {reason} = why.
                message = _("Not done - {reason}").format(
                    reason=petlibro_error_text(e))
                self._busy = False
                self._reload(keep_id=keep_id, message=message)
                return
            plugin._record_local_petlibro_action(device.uuid)
            if history_action:
                get_history().log_action(device, history_action, detail)
            _beep(BEEP_ACTION)
            self._busy = False
            # Give the cloud a moment before reading back.
            import time as _time
            _time.sleep(1.5)
            self._reload(keep_id=keep_id, message=done_message, verify=verify)

        threading.Thread(target=task, daemon=True).start()

    @staticmethod
    def _plan_detail(plan):
        """Language-neutral history detail: "07:00|3|label"."""
        return "|".join((str(plan.get("executionTime") or ""),
                         str(plan.get("grainNum") or ""),
                         str(plan.get("label") or "").strip()))

    def _on_skip(self, event):
        plan = self._selected()
        if not plan:
            return
        skipped = self._today_state(plan.get("id")) == PLAN_TO_BE_SKIPPED
        serial = self.device.serial
        api = self.device._api
        if skipped:
            # Translators: Confirmation: a meal skipped for today is given
            # after all. {time} = HH:MM.
            message = _("Meal at {time} is given today").format(
                time=plan.get("executionTime"))
        else:
            # Translators: Confirmation: a meal is skipped for today only.
            # {time} = HH:MM.
            message = _("Meal at {time} skipped today").format(
                time=plan.get("executionTime"))
        plan_id = plan.get("id")
        self._run(lambda: api.set_today_single(serial, plan["id"], skipped),
                  message,
                  'meal_unskip_today' if skipped else 'meal_skip_today',
                  self._plan_detail(plan), keep_id=plan_id,
                  verify=lambda: (self._today_state(plan_id) == PLAN_PENDING)
                  if skipped else (self._today_state(plan_id)
                                   in (PLAN_TO_BE_SKIPPED, PLAN_SKIPPED)))

    def _on_enable(self, event):
        plan = self._selected()
        if not plan:
            return
        enable = not plan.get("enable", True)
        serial = self.device.serial
        api = self.device._api
        # Translators: Confirmation after switching a meal on or off.
        # {time} = HH:MM, {state} = on/off.
        message = _("Meal at {time} switched {state}").format(
            time=plan.get("executionTime"),
            state=_("on") if enable else _("off"))
        plan_id = plan.get("id")

        def shown():
            now = self.device.plan(plan_id)
            return bool(now) and bool(now.get("enable", True)) == enable
        self._run(lambda: api.set_plan_enabled(serial, plan["id"], enable),
                  message, 'meal_enable' if enable else 'meal_disable',
                  self._plan_detail(plan), keep_id=plan_id, verify=shown)

    def _on_edit(self, event):
        plan = self._selected()
        if not plan:
            return
        result = self._edit(plan)
        if result is None:
            return
        changed = dict(plan)
        changed.update(result)
        serial = self.device.serial
        api = self.device._api
        # Translators: Confirmation after changing a meal. {time} = HH:MM.
        message = _("Meal at {time} changed").format(time=changed["executionTime"])
        plan_id = plan.get("id")

        def shown():
            now = self.device.plan(plan_id)
            return bool(now) and all(
                str(now.get(k)) == str(changed.get(k))
                for k in ("executionTime", "grainNum"))
        self._run(lambda: api.update_plan(serial, changed), message,
                  'meal_change', self._plan_detail(changed),
                  keep_id=plan_id, verify=shown)

    def _on_add(self, event):
        # No weekday ticked to begin with: the days are chosen, not
        # unticked - asked for by the user on 2026-10-02. Left that way, the
        # meal is given once.
        template = {"executionTime": "", "grainNum": 1, "label": "",
                    "repeatDay": "[]", "enable": True,
                    "enableAudio": False, "audioTimes": 2}
        result = self._edit(template, new=True)
        if result is None:
            return
        plan = dict(template)
        plan.update(result)
        serial = self.device.serial
        api = self.device._api
        # Translators: Confirmation after adding a meal. {time} = HH:MM.
        message = _("Meal at {time} added").format(time=plan["executionTime"])
        def shown():
            return any(str(p.get("executionTime")) == plan["executionTime"]
                       and str(p.get("grainNum")) == str(plan["grainNum"])
                       for p in self.device.plans)
        self._run(lambda: api.add_plan(serial, plan), message, 'meal_add',
                  self._plan_detail(plan), verify=shown)

    def _on_delete(self, event):
        plan = self._selected()
        if not plan:
            return
        dlg = wx.MessageDialog(
            self,
            # Translators: Safety question before a meal is deleted. {meal} =
            # the meal's line, e.g. "Breakfast, 07:00, 3 portions".
            _("Delete the meal {meal} for good?").format(meal=self._row_text(plan)),
            # Translators: Title of the delete question.
            _("Delete meal"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        # Translators: Buttons of the delete question.
        dlg.SetYesNoLabels(_("&Delete"), _("&Cancel"))
        try:
            dlg.SetEscapeId(wx.ID_NO)
        except Exception as e:
            log.debug(f"Ignored error in _on_delete: {e}")
        try:
            confirmed = dlg.ShowModal() == wx.ID_YES
        finally:
            dlg.Destroy()
        if not confirmed:
            ui.message(_("Cancelled"))
            return
        serial = self.device.serial
        api = self.device._api
        # Translators: Confirmation after deleting a meal. {time} = HH:MM.
        message = _("Meal at {time} deleted").format(time=plan.get("executionTime"))
        plan_id = plan.get("id")
        self._run(lambda: api.delete_plan(serial, plan["id"]), message,
                  'meal_delete', self._plan_detail(plan),
                  verify=lambda: self.device.plan(plan_id) is None)

    def _on_today_all(self, event):
        give = self.device.today_all_skipped
        serial = self.device.serial
        api = self.device._api
        if give:
            # Translators: Confirmation: today's meals are given after all.
            message = _("All meals of today are given")
        else:
            # Translators: Confirmation: every meal of today is skipped.
            message = _("All meals of today skipped")
        self._run(lambda: api.set_today_all(serial, give), message,
                  'meals_today_given' if give else 'meals_today_skipped',
                  verify=lambda: self.device.today_all_skipped == (not give))

    def _edit(self, plan, new=False):
        dlg = PetlibroMealEditDialog(self, plan, self.device.max_portions, new,
                                     meal_call=self.device.has_meal_call)
        try:
            if dlg.ShowModal() == wx.ID_OK:
                return dlg.result
            return None
        finally:
            dlg.Destroy()


class PetlibroMealEditDialog(wx.Dialog):
    """Time, portions, weekdays, name and sound of one meal."""

    def __init__(self, parent, plan, max_portions, new=False, meal_call=True):
        super().__init__(
            parent,
            # Translators: Title of the dialog that adds or changes a meal.
            title=_("New meal") if new else _("Change meal"))
        self.result = None
        sizer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(cols=2, hgap=10, vgap=8)
        grid.AddGrowableCol(1)

        # Translators: Label of the time field of a meal.
        grid.Add(wx.StaticText(self, label=_("&Time (HH:MM):")),
                 flag=wx.ALIGN_CENTER_VERTICAL)
        self.timeCtrl = wx.TextCtrl(self, value=str(plan.get("executionTime") or ""))
        grid.Add(self.timeCtrl, flag=wx.EXPAND)

        # Translators: Label of the portions field of a meal. {max} = the
        # most the feeder gives at once.
        grid.Add(wx.StaticText(self, label=_("&Portions (1 to {max}):").format(
            max=max_portions)), flag=wx.ALIGN_CENTER_VERTICAL)
        try:
            amount = int(plan.get("grainNum") or 1)
        except (TypeError, ValueError):
            amount = 1
        self.portionsCtrl = wx.SpinCtrl(self, min=1, max=max_portions,
                                        initial=max(1, min(amount, max_portions)))
        grid.Add(self.portionsCtrl, flag=wx.EXPAND)

        # Translators: Label of the name field of a meal (optional).
        grid.Add(wx.StaticText(self, label=_("&Name (optional):")),
                 flag=wx.ALIGN_CENTER_VERTICAL)
        self.nameCtrl = wx.TextCtrl(self, value=str(plan.get("label") or ""))
        grid.Add(self.nameCtrl, flag=wx.EXPAND)
        sizer.Add(grid, flag=wx.EXPAND | wx.ALL, border=10)

        # Seven check boxes rather than a wx.CheckListBox, as in the time
        # program of the heat pump (dialog_cozytouch.py): the list box does
        # not say whether an entry is ticked, so a meal came out "every day"
        # when every day had been ticked without a word of it being read.
        # A check box reads its name and its state, and Space toggles it.
        # Translators: Heading of the weekday group of a meal. No day ticked
        # means the meal is given once.
        day_box = wx.StaticBoxSizer(
            wx.VERTICAL, self, label=_("Weekdays (none ticked = once)"))
        ticked = _repeat_days(plan)
        self.dayChecks = []
        for index, day_name in enumerate(WEEKDAY_NAMES):
            check = wx.CheckBox(day_box.GetStaticBox(), label=day_name)
            check.SetValue(index + 1 in ticked)
            day_box.Add(check, 0, wx.ALL, 2)
            self.dayChecks.append(check)
        sizer.Add(day_box, flag=wx.EXPAND | wx.ALL, border=10)

        # Translators: Checkbox: the feeder plays its sound before this meal.
        # The meal call: a recording the feeder plays before a meal. Offered
        # only where the feeder has a speaker - the Air Smart Feeder has none
        # (Petlibro support), and a box that does nothing is noise.
        self.audioCtrl = None
        if meal_call:
            # Translators: Checkbox of a meal: the feeder plays its recorded
            # meal call before the food comes out.
            self.audioCtrl = wx.CheckBox(
                self, label=_("Play the &meal call before the meal"))
            self.audioCtrl.SetValue(bool(plan.get("enableAudio", False)))
            sizer.Add(self.audioCtrl, flag=wx.LEFT | wx.RIGHT, border=10)

        buttons = self.CreateSeparatedButtonSizer(wx.OK | wx.CANCEL)
        sizer.Add(buttons, flag=wx.EXPAND | wx.ALL, border=10)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self.SetSizerAndFit(sizer)
        self.timeCtrl.SetFocus()

    def _on_ok(self, event):
        text = self.timeCtrl.GetValue().strip().replace(".", ":")
        parts = text.split(":")
        try:
            hours, minutes = int(parts[0]), int(parts[1])
            if len(parts) != 2 or not (0 <= hours <= 23 and 0 <= minutes <= 59):
                raise ValueError
        except (ValueError, IndexError):
            _beep(BEEP_ERROR)
            # Translators: Invalid time in the meal dialog.
            ui.message(_("Invalid time - enter hours and minutes, e.g. 07:30"))
            self.timeCtrl.SetFocus()
            return
        days = [i + 1 for i, check in enumerate(self.dayChecks) if check.GetValue()]
        self.result = {
            "executionTime": f"{hours:02d}:{minutes:02d}",
            "grainNum": int(self.portionsCtrl.GetValue()),
            "label": self.nameCtrl.GetValue().strip(),
            "repeatDay": "[" + ",".join(str(d) for d in days) + "]",
        }
        if self.audioCtrl is not None:
            self.result["enableAudio"] = bool(self.audioCtrl.GetValue())
        self.EndModal(wx.ID_OK)


class PetlibroDrinksDialog(wx.Dialog):
    """What a fountain registered on one day: one line per drink.

    The fountain writes every drink to its work records - time, millilitres
    and duration (DRINK_EVENT, measured on the PLWF116 on 2026-09-23). A
    fountain that does not falls back to the hourly totals of
    /data/deviceDrinkWater/history.
    """

    DAYS = 7

    def __init__(self, parent, device):
        super().__init__(
            parent,
            # Translators: Title of the dialog with the drinks of a day.
            # {name} = fountain.
            title=_("{name}: drinks").format(name=device.name),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        import datetime as _dt
        self.device = device
        self._is_destroyed = False
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy)
        today = _dt.date.today()
        self._dates = [today - _dt.timedelta(days=n) for n in range(self.DAYS)]
        labels = []
        for n, day in enumerate(self._dates):
            if n == 0:
                # Translators: Entry of the day selection: today.
                labels.append(_("Today"))
            elif n == 1:
                # Translators: Entry of the day selection: yesterday.
                labels.append(_("Yesterday"))
            else:
                labels.append(f"{WEEKDAY_NAMES[day.weekday()]}, {day.isoformat()}")

        sizer = wx.BoxSizer(wx.VERTICAL)
        row = wx.BoxSizer(wx.HORIZONTAL)
        # Translators: Label of the day selection in the drinks dialog.
        row.Add(wx.StaticText(self, label=_("&Day:")), 0,
                wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.dayCtrl = wx.Choice(self, choices=labels)
        self.dayCtrl.SetSelection(0)
        self.dayCtrl.Bind(wx.EVT_CHOICE, lambda evt: self._load())
        row.Add(self.dayCtrl, 1, wx.EXPAND)
        sizer.Add(row, 0, wx.EXPAND | wx.ALL, 10)

        # Translators: Label of the list of drinks in the drinks dialog.
        sizer.Add(wx.StaticText(self, label=_("&Drinks:")), 0,
                  wx.LEFT | wx.RIGHT, 10)
        self.list = wx.ListBox(self, style=wx.LB_SINGLE)
        # Translators: Accessible name of the list of drinks.
        self.list.SetName(_("Drinks"))
        self.list.SetMinSize((460, 220))
        sizer.Add(self.list, 1, wx.EXPAND | wx.ALL, 10)

        # Translators: Button that closes the drinks dialog.
        close = wx.Button(self, wx.ID_CANCEL, _("C&lose"))
        sizer.Add(close, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetEscapeId(wx.ID_CANCEL)
        self.SetSizerAndFit(sizer)
        self.dayCtrl.SetFocus()
        self._load()

    def _on_destroy(self, event):
        if event.GetEventObject() is self:
            self._is_destroyed = True
        event.Skip()

    def _load(self):
        index = max(0, self.dayCtrl.GetSelection())
        day = self._dates[index].isoformat()
        # Translators: Shown in the list of hours while they are loaded.
        self.list.Set([_("Loading...")])
        device = self.device

        def task():
            try:
                drinks = drinks_from_records(flatten_records(
                    device._api.fetch_day_records(device.serial, day)))
                if drinks:
                    lines = self._drink_lines(drinks)
                else:
                    data = device._api.fetch_drink_history(device.serial, day)
                    lines = self._lines(data)
            except Exception as e:
                log.debug(f"Petlibro drink history failed: {e}")
                # Translators: The hours could not be loaded. {reason} = why.
                lines = [_("Could not be loaded - {reason}").format(
                    reason=petlibro_error_text(e))]

            def show():
                if self._is_destroyed:
                    return
                try:
                    self.list.Set(lines)
                    self.list.SetSelection(0)
                except RuntimeError:
                    pass
            wx.CallAfter(show)

        threading.Thread(target=task, daemon=True).start()

    @staticmethod
    def _drink_lines(drinks):
        """Total first, then one line per drink: "07:04: 12 ml, 19 seconds"."""
        total_ml = sum(ml or 0 for _ts, ml, _sec in drinks)
        if len(drinks) == 1:
            lines = [_("Total: {ml} in 1 drink").format(ml=ml_text(total_ml))]
        else:
            lines = [_("Total: {ml} in {count} drinks").format(
                ml=ml_text(total_ml), count=len(drinks))]
        for ts, ml, seconds in drinks:
            clock = time.strftime("%H:%M", time.localtime(ts))
            # Translators: One drink in the list of a day. {time} = HH:MM,
            # {ml} = e.g. "12 ml", {duration} = e.g. "19 seconds".
            lines.append(_("{time}: {ml}, {duration}").format(
                time=clock, ml=ml_text(ml if ml is not None else 0),
                duration=duration_text(seconds or 0)))
        return lines

    @staticmethod
    def _lines(data):
        rows = drink_rows(data)
        if not rows:
            # Translators: A day on which the fountain registered no drink.
            return [_("No drinks recorded on this day")]
        total_ml = sum(r[2] for r in rows)
        total_count = sum(r[3] for r in rows)
        if total_count == 1:
            # Translators: First line of the drinks per hour: the day's total,
            # one drink. {ml} = e.g. "35 ml".
            lines = [_("Total: {ml} in 1 drink").format(ml=ml_text(total_ml))]
        else:
            # Translators: First line of the drinks per hour: the day's total.
            # {ml} = e.g. "35 ml", {count} = number of drinks.
            lines = [_("Total: {ml} in {count} drinks").format(
                ml=ml_text(total_ml), count=total_count)]
        for start, end, ml, count, seconds in rows:
            if count == 1:
                # Translators: One hour in the drinks list with one drink.
                # {start}/{end} = HH:MM, {ml} = e.g. "14 ml", {duration} =
                # e.g. "45 seconds".
                lines.append(_("{start} to {end}: {ml}, 1 drink, {duration}").format(
                    start=start, end=end, ml=ml_text(ml),
                    duration=duration_text(seconds)))
            else:
                # Translators: One hour in the drinks list. {count} = drinks,
                # {duration} = their average length, e.g. "45 seconds".
                lines.append(_("{start} to {end}: {ml}, {count} drinks, about "
                               "{duration} each").format(
                    start=start, end=end, ml=ml_text(ml), count=count,
                    duration=duration_text(seconds)))
        return lines
