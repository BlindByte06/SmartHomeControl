# -*- coding: utf-8 -*-
"""
Smart Home Control - history dialog.

Two views instead of one flat list:

* **Events** - what a history is opened for. One row per switching action
  with its origin ("me" / "external" / "automatic"), grouped by day so the
  same date is not read out on every line.
* **Readings** - condensed to min/max/average per device and quantity, so
  one row answers what used to need a hundred.

Split out of device_dialog.py.
"""

import os
from datetime import datetime, timedelta

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

from .history import (
    get_history, _format_action_text, _source_text, format_measurement,
    relative_time, _measurement_labels, local_date, local_time, local_datetime,
)
from .dialog_helpers import _beep
from .constants import BEEP_ON, BEEP_OFF, BEEP_ERROR

# Views
VIEW_EVENTS = 0
VIEW_MEASUREMENTS = 1


def _day_label(dt):
    """Heading for a day group: today / yesterday / weekday, date."""
    today = datetime.now().date()
    day = dt.date()
    if day == today:
        # Translators: Group heading in the history for the current day.
        return _("Today")
    if day == today - timedelta(days=1):
        # Translators: Group heading in the history for the previous day.
        return _("Yesterday")
    # Translators: Group heading in the history: weekday and date,
    # e.g. "Freitag, 24.07.2026".
    return _("{weekday}, {date}").format(
        weekday=dt.strftime('%A'), date=local_date(dt))


class _DetailDialog(wx.Dialog):
    """Detail view in a read-only text box instead of a message box.

    A wx.MessageDialog hands its whole text to the screen reader as ONE
    utterance - twenty lines of readings then arrive as a single block that
    cannot be navigated, repeated or copied in parts. A read-only multi-line
    text field can be walked line by line with the arrow keys, follows on the
    braille display and allows Ctrl+C.
    """

    def __init__(self, parent, title, text):
        super().__init__(parent, title=title, size=(620, 460))
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.text_ctrl = wx.TextCtrl(
            self, value=text,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        self.text_ctrl.SetName(title)
        sizer.Add(self.text_ctrl, 1, wx.EXPAND | wx.ALL, 8)
        buttons = self.CreateStdDialogButtonSizer(wx.OK)
        sizer.Add(buttons, 0, wx.ALIGN_CENTER | wx.BOTTOM, 8)
        self.SetSizer(sizer)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char)
        # The focus belongs in the text, not on the OK button: that is where
        # reading starts.
        self.text_ctrl.SetFocus()
        self.text_ctrl.SetInsertionPoint(0)
        self.CenterOnParent()

    def _on_char(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
        else:
            event.Skip()


class HistoryDialog(wx.Dialog):
    """Accessible history dialog for screen reader users.

    Shows switch actions and sensor values with filter options
    and CSV export. Optimized for NVDA navigation.
    """

    def __init__(self, parent, plugin):
        super(HistoryDialog, self).__init__(
            parent,
            # Translators: Window title of the history dialog.
            title=_("Device history"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
            size=(760, 520),
        )
        self.plugin = plugin
        self.history = get_history()
        # Per list row the matching entry - None for a day heading. Needed
        # because the headings shift the indices against the entry list.
        self._row_data = []
        self._current_entries = []

        self._init_ui()
        # Do NOT announce on opening: the window announces itself and the
        # number of entries is in the status line.
        self._apply_filters(speak=False)

        # Focus on the history list
        self.list_ctrl.SetFocus()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _init_ui(self):
        """Builds the dialog interface"""
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # === View ===
        view_sizer = wx.BoxSizer(wx.HORIZONTAL)
        # Translators: Label of the view selector in the history dialog.
        view_sizer.Add(wx.StaticText(self, label=_("&View:")), 0,
                       wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        # Translators: The two views of the history dialog.
        self.view_choice = wx.Choice(self, choices=[
            _("Events"), _("Measurements")])
        self.view_choice.SetSelection(VIEW_EVENTS)
        self.view_choice.Bind(wx.EVT_CHOICE, self._on_view_changed)
        view_sizer.Add(self.view_choice, 0, wx.ALIGN_CENTER_VERTICAL)
        main_sizer.Add(view_sizer, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)

        # === Filter area ===
        # Translators: Label of the filter area in the history dialog.
        filter_box = wx.StaticBox(self, label=_("Filter"))
        filter_sizer = wx.StaticBoxSizer(filter_box, wx.HORIZONTAL)

        # Device filter
        # Translators: Label of the device filter in the history. & marks the
        # accelerator.
        filter_sizer.Add(wx.StaticText(self, label=_("&Device:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self.device_filter = wx.Choice(self)
        # Load the devices from the history (separately, so it can be refreshed
        # via F5)
        self._populate_device_filter()
        filter_sizer.Add(self.device_filter, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        # Platform filter
        # Translators: Label of the platform filter in the history. & marks the
        # accelerator.
        filter_sizer.Add(wx.StaticText(self, label=_("&Platform:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        # Translators: Filter option "All" (platform names are brand names).
        self.platform_filter = wx.Choice(self, choices=[_("All"), "Meross", "Netatmo", "VeSync", "Cozytouch"])
        self.platform_filter.SetSelection(0)
        filter_sizer.Add(self.platform_filter, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        # Time range filter
        # Translators: Label of the period filter in the history. & marks the
        # accelerator.
        filter_sizer.Add(wx.StaticText(self, label=_("&Period:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        # Translators: Time range filter options in the history dialog.
        self.time_filter = wx.Choice(self, choices=[
            _("All time"), _("Last hour"), _("Last 24 hours"),
            _("Last 7 days"), _("Last 30 days")
        ])
        self.time_filter.SetSelection(0)
        filter_sizer.Add(self.time_filter, 0, wx.ALIGN_CENTER_VERTICAL)

        main_sizer.Add(filter_sizer, 0, wx.EXPAND | wx.ALL, 8)

        # Filter button
        # Translators: Button for applying the filters.
        filter_btn = wx.Button(self, label=_("&Filter"))
        filter_btn.Bind(wx.EVT_BUTTON, self._on_apply_filter)
        main_sizer.Add(filter_btn, 0, wx.LEFT | wx.BOTTOM, 8)

        # === History list (ListCtrl in report mode) ===
        self.list_ctrl = wx.ListCtrl(
            self,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_HRULES | wx.LC_VRULES,
        )
        self._build_columns()
        main_sizer.Add(self.list_ctrl, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        # === Status line ===
        self.status_text = wx.StaticText(self, label="")
        main_sizer.Add(self.status_text, 0, wx.EXPAND | wx.ALL, 8)

        # === Button bar ===
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # Translators: Button for the CSV export of the history.
        export_btn = wx.Button(self, label=_("Export as &CSV"))
        export_btn.Bind(wx.EVT_BUTTON, self._on_export_csv)
        btn_sizer.Add(export_btn, 0, wx.RIGHT, 8)

        # Translators: Button for deleting the history.
        clear_btn = wx.Button(self, label=_("De&lete history"))
        clear_btn.Bind(wx.EVT_BUTTON, self._on_clear_history)
        btn_sizer.Add(clear_btn, 0, wx.RIGHT, 8)

        # Translators: Button for closing the dialog.
        close_btn = wx.Button(self, wx.ID_CLOSE, label=_("C&lose"))
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        btn_sizer.Add(close_btn, 0)

        main_sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 8)

        self.SetSizer(main_sizer)

        # Keyboard: Escape closes the dialog
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char)
        # Double-click on an entry shows the details
        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_item_activated)

    def _current_view(self):
        return self.view_choice.GetSelection()

    def _build_columns(self):
        """Rebuilds the columns to match the current view."""
        self.list_ctrl.ClearAll()
        if self._current_view() == VIEW_MEASUREMENTS:
            # Deliberately only four columns: a screen reader reads EVERY
            # column of the focused row on each arrow key, so eight of them
            # became one long sentence per row. Lowest, highest, average and
            # the number of readings are one Enter away in the detail view,
            # where they can be read line by line.
            # Translators: Column headers of the measurement view.
            columns = [
                (_("Device"), 200), (_("Quantity"), 130),
                (_("Latest value"), 110), (_("Latest reading"), 140),
            ]
        else:
            # Translators: Column headers of the event view.
            columns = [
                (_("Time"), 90), (_("Device"), 170), (_("Platform"), 90),
                (_("Event"), 240), (_("Source"), 90),
            ]
        for index, (label, width) in enumerate(columns):
            self.list_ctrl.InsertColumn(index, label, width=width)

    def _populate_device_filter(self):
        """Fills the device dropdown from the history.

        Preserves the current selection (by UUID) across a rebuild so devices
        newly added via F5 appear without losing the selection.
        """
        # Remember the currently selected UUID (empty = "All devices")
        prev_uuid = ""
        sel = self.device_filter.GetSelection()
        if sel > 0:
            prev_uuid = self.device_filter.GetClientData(sel) or ""

        self.device_filter.Clear()
        # Translators: Filter option: show all devices.
        self.device_filter.Append(_("All devices"), "")
        restore_index = 0
        for dev in self.history.get_unique_devices():
            plat = dev.get('platform', '').capitalize()
            label = f"{dev['name']} ({plat})" if plat else dev['name']
            idx = self.device_filter.Append(label, dev['uuid'])
            if dev['uuid'] == prev_uuid:
                restore_index = idx
        self.device_filter.SetSelection(restore_index)

    # ------------------------------------------------------------------
    # Keyboard / view
    # ------------------------------------------------------------------
    def _on_char(self, event):
        """Keyboard handler"""
        key = event.GetKeyCode()
        if key == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CLOSE)
        elif key == wx.WXK_F5:
            # Rebuild the device list so new devices appear in the filter
            self._populate_device_filter()
            self._apply_filters()
            # Translators: Message after refreshing the history list.
            ui.message(_("History refreshed"))
        else:
            event.Skip()

    def _on_view_changed(self, event):
        self._build_columns()
        self._apply_filters()
        self.list_ctrl.SetFocus()

    def _get_filter_values(self):
        """Returns the current filter values"""
        # Device UUID
        dev_sel = self.device_filter.GetSelection()
        device_uuid = self.device_filter.GetClientData(dev_sel) if dev_sel > 0 else None

        # Platform
        plat_sel = self.platform_filter.GetSelection()
        platform_map = {0: None, 1: 'meross', 2: 'netatmo', 3: 'vesync', 4: 'cozytouch'}
        platform = platform_map.get(plat_sel)

        # Time range (in hours)
        time_sel = self.time_filter.GetSelection()
        time_map = {0: None, 1: 1, 2: 24, 3: 168, 4: 720}
        since_hours = time_map.get(time_sel)

        return device_uuid, platform, since_hours

    def _apply_filters(self, speak=True):
        """Applies the filters and refreshes the list"""
        device_uuid, platform, since_hours = self._get_filter_values()

        if self._current_view() == VIEW_MEASUREMENTS:
            self._current_entries = self.history.summarize_measurements(
                device_uuid=device_uuid,
                platform=platform,
                since_hours=since_hours,
            )
            self._populate_measurements(speak=speak)
        else:
            self._current_entries = self.history.get_entries(
                device_uuid=device_uuid,
                event_type='action',
                max_entries=500,
                since_hours=since_hours,
                platform=platform,
            )
            self._populate_events(speak=speak)

    # ------------------------------------------------------------------
    # View: events
    # ------------------------------------------------------------------
    def _populate_events(self, speak=True):
        """Fills the list with events, grouped by day.

        The day headings save the date on every single row - the biggest win
        for speech output, which would otherwise repeat it before every
        event.
        """
        self.list_ctrl.DeleteAllItems()
        self._row_data = []

        row = 0
        last_day = None
        for entry in self._current_entries:
            ts = entry.get('timestamp', 0)
            dt = datetime.fromtimestamp(ts) if ts else None

            if dt is not None:
                day = dt.date()
                if day != last_day:
                    last_day = day
                    header = self.list_ctrl.InsertItem(row, _day_label(dt))
                    for col in range(1, self.list_ctrl.GetColumnCount()):
                        self.list_ctrl.SetItem(header, col, "")
                    self._row_data.append(None)
                    row += 1

            time_str = dt.strftime('%H:%M:%S') if dt else ''
            # Translators: Placeholder for an unknown device name.
            device_name = entry.get('device_name', _("Unknown"))
            platform = entry.get('platform', '').capitalize()
            action_text = _format_action_text(entry.get('action', ''),
                                              entry.get('details', ''))
            source = _source_text(entry.get('source', ''))

            idx = self.list_ctrl.InsertItem(row, time_str)
            self.list_ctrl.SetItem(idx, 1, device_name)
            self.list_ctrl.SetItem(idx, 2, platform)
            self.list_ctrl.SetItem(idx, 3, action_text)
            self.list_ctrl.SetItem(idx, 4, source)
            self._row_data.append(entry)
            row += 1

        count = len(self._current_entries)
        total = self.history.get_event_count()
        # Translators: Status line of the event view. {count} = shown,
        # {total} = total number of events.
        self.status_text.SetLabel(
            _("{count} events shown (total: {total})").format(
                count=count, total=total))
        if not speak:
            return
        if count > 0:
            # Translators: Message after loading the event list.
            ui.message(_("{count} events").format(count=count))
        else:
            # Translators: Message when no events match the filter.
            ui.message(_("No events found"))

    # ------------------------------------------------------------------
    # View: readings
    # ------------------------------------------------------------------
    @staticmethod
    def _short_time(ts):
        """Time of a reading: time of day for today, otherwise with the date."""
        if not ts:
            return ''
        moment = datetime.fromtimestamp(ts)
        if moment.date() == datetime.now().date():
            return local_time(moment)
        return local_datetime(moment)

    def _period_text(self, records):
        """Covered period across all series, e.g. "12.08. 08:00 - 13.08. 22:15".

        Answers the question the bare numbers cannot: WHEN was this measured.
        """
        starts = [r['first_ts'] for r in records if r.get('first_ts')]
        ends = [r['last_ts'] for r in records if r.get('last_ts')]
        if not starts or not ends:
            return ''
        first = datetime.fromtimestamp(min(starts))
        last = datetime.fromtimestamp(max(ends))
        # Translators: Period of the readings. {start}/{end} = date and time.
        return _("{start} to {end}").format(
            start=local_datetime(first), end=local_datetime(last))

    def _populate_measurements(self, speak=True):
        """Fills the list with condensed readings."""
        self.list_ctrl.DeleteAllItems()
        self._row_data = []
        labels = _measurement_labels()

        for row, rec in enumerate(self._current_entries):
            quantity = rec['quantity']
            idx = self.list_ctrl.InsertItem(row, rec['device_name'])
            self.list_ctrl.SetItem(idx, 1, labels.get(quantity, quantity))
            self.list_ctrl.SetItem(idx, 2, format_measurement(quantity, rec['last']))
            self.list_ctrl.SetItem(idx, 3, self._short_time(rec.get('last_ts')))
            self._row_data.append(rec)

        count = len(self._current_entries)
        total = self.history.get_measurement_count()
        period = self._period_text(self._current_entries)
        if period:
            # Translators: Status line of the measurement view. {count} =
            # number of rows, {total} = stored readings, {period} = covered
            # period.
            label = _("{count} measurement series from {total} readings, "
                      "{period}").format(count=count, total=total, period=period)
        else:
            # Translators: Status line of the measurement view without data.
            label = _("{count} measurement series from {total} readings").format(
                count=count, total=total)
        self.status_text.SetLabel(label)
        if not speak:
            return
        if count > 0:
            # Translators: Message after loading the measurement view.
            ui.message(_("{count} measurement series").format(count=count))
        else:
            # Translators: Message when no measurements match the filter.
            ui.message(_("No measurements found"))

    def _on_apply_filter(self, event):
        """Filter button handler"""
        self._apply_filters()
        self.list_ctrl.SetFocus()

    def _on_item_activated(self, event):
        """Shows the details of an entry (Enter/double-click)"""
        idx = event.GetIndex()
        if not (0 <= idx < len(self._row_data)):
            return
        record = self._row_data[idx]
        if record is None:
            return  # Day heading - nothing to show

        if self._current_view() == VIEW_MEASUREMENTS:
            detail_text = self._measurement_detail(record)
        else:
            detail_text = self.history.format_entry_for_display(record)

        # Translators: Title of the detail dialog of a history entry.
        dlg = _DetailDialog(self, _("History entry details"), detail_text)
        dlg.ShowModal()
        dlg.Destroy()

    def _measurement_detail(self, rec):
        """Detail text of a condensed series of readings.

        Shows the summary and below it the most recent change points - the
        row's "expansion" without overloading the list.
        """
        quantity = rec['quantity']
        labels = _measurement_labels()
        _dev, _plat, since_hours = self._get_filter_values()
        points = [
            e for e in self.history.get_entries(
                device_uuid=rec['device_uuid'], event_type='sensor',
                max_entries=15, since_hours=since_hours)
            if quantity in (e.get('sensor_data') or {})
        ]
        lines = [
            # Translators: Header of the measurement detail dialog.
            _("{name} – {quantity}").format(
                name=rec['device_name'],
                quantity=labels.get(quantity, quantity)),
            "",
            # Translators: Summary lines in the measurement detail dialog.
            _("Lowest value: {value}").format(
                value=format_measurement(quantity, rec['min'])),
            # Translators: Detail of a measurement series in the history, one
            # line per figure.
            _("Highest value: {value}").format(
                value=format_measurement(quantity, rec['max'])),
            # Translators: Time-weighted mean of a measurement series.
            _("Average (time-weighted): {value}").format(
                value=format_measurement(quantity, rec['avg'])),
            # Translators: Detail of a measurement series in the history, one
            # line per figure.
            _("Current: {value}").format(
                value=format_measurement(quantity, rec['last'])),
            # Translators: Period and number of readings in the detail dialog.
            # {period} = from-to, {count} = number of stored readings.
            _("Period: {period}").format(
                period=self._period_text([rec]) or "-"),
            # Translators: Detail of a measurement series in the history, one
            # line per figure.
            _("Readings stored: {count}").format(count=rec['count']),
            "",
            # Translators: Heading of the change point list.
            _("Latest changes:"),
        ]
        for entry in points:
            value = (entry.get('sensor_data') or {}).get(quantity)
            lines.append("  {time} ({rel}): {value}".format(
                time=local_datetime(
                    datetime.fromtimestamp(entry.get('timestamp', 0))),
                rel=relative_time(entry.get('timestamp', 0)),
                value=format_measurement(quantity, value)))
        if not points:
            # Translators: Shown when a measurement series has no change
            # points in the selected period.
            lines.append("  " + _("No changes in the selected period"))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Export / delete
    # ------------------------------------------------------------------
    def _on_export_csv(self, event):
        """Exports the current history as CSV"""
        dlg = wx.FileDialog(
            self,
            # Translators: Title of the file dialog for the CSV export.
            _("Export history as CSV"),
            # The suggested name says WHICH of the two views is exported -
            # the button is the same in both, so without it one cannot tell
            # from the file whether events or readings are inside.
            defaultFile=(
                # Translators: Suggested file name for the CSV export of the
                # readings.
                _("smart-home-readings.csv")
                if self._current_view() == VIEW_MEASUREMENTS
                # Translators: Suggested file name for the CSV export of the
                # events.
                else _("smart-home-events.csv")),
            # Translators: File type filter in the file dialog.
            wildcard=_("CSV files (*.csv)|*.csv"),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )

        if dlg.ShowModal() == wx.ID_OK:
            filepath = dlg.GetPath()
            dlg.Destroy()

            try:
                device_uuid, platform, since_hours = self._get_filter_values()
                # In the readings view export the change points, not the
                # summary: a table is there to compute with.
                event_type = ('sensor' if self._current_view() == VIEW_MEASUREMENTS
                              else 'action')
                self.history.export_csv(
                    filepath=filepath,
                    device_uuid=device_uuid,
                    since_hours=since_hours,
                    platform=platform,
                    event_type=event_type,
                )
                _beep(BEEP_ON)  # formerly 800,80
                # Translators: Confirmation after the CSV export of the
                # history.
                ui.message(_("History exported to {filename}").format(
                    filename=os.path.basename(filepath)))

                export_msg = wx.MessageDialog(
                    self,
                    # Translators: Success message in the export dialog.
                    # {path} = file.
                    _("History exported successfully.\n\nFile: {path}").format(
                        path=filepath),
                    # Translators: Title of the export success dialog.
                    _("Export successful"),
                    wx.OK | wx.ICON_INFORMATION,
                )
                export_msg.ShowModal()
                export_msg.Destroy()
            except Exception as e:
                _beep(BEEP_ERROR)
                log.error(f"CSV export failed: {e}")
                # Translators: Error message on CSV export.
                ui.message(_("Export failed: {error}").format(error=str(e)[:60]))
        else:
            dlg.Destroy()

    def _on_clear_history(self, event):
        """Deletes the entire history after confirmation"""
        events = self.history.get_event_count()
        measurements = self.history.get_measurement_count()

        dlg = wx.MessageDialog(
            self,
            # Translators: Confirmation prompt before deleting the history.
            # {events} = number of events, {measurements} = number of
            # measurement change points.
            _("Really delete the entire history?\n\n{events} events and "
              "{measurements} readings will be deleted permanently.").format(
                  events=events, measurements=measurements),
            # Translators: Title of the confirmation prompt.
            _("Delete history"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
        )

        if dlg.ShowModal() == wx.ID_YES:
            self.history.clear()
            self._current_entries = []
            self._apply_filters(speak=False)
            _beep(BEEP_OFF)  # formerly 600,80 = success
            # Translators: Confirmation after deleting the history.
            ui.message(_("History deleted"))
        dlg.Destroy()
