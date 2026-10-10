# -*- coding: utf-8 -*-
"""
Smart Home Control - platform status dialog.

A failure is announced once. Anyone who missed that - away from the keyboard,
in another program, speech interrupted - had no way to find out what happened,
and the values on the screen looked exactly like fresh ones. This is where it
can be read.

Two views, for two different questions:

* **Events** - "was Netatmo away earlier, and for how long?" One row per state
  change since the sign-in, newest first.
* **Devices** - "which device is not answering?" A platform can answer while
  one of its devices keeps timing out; that is deliberately not announced (one
  Meross device out of twenty-two missed roughly every other poll and produced
  three announcements in nine minutes), and it is deliberately recorded. Shown
  are the devices that missed at least one poll, still failing ones first.

Both live for as long as NVDA runs: the record is cleared on sign-out, because
a state nobody saw fail must not be announced as recovered afterwards.
"""

from datetime import datetime

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

from . import platform_health
from .dialog_history import _DetailDialog
from .history import local_time, local_datetime, relative_time
from .platform_utils import PLATFORM_LABELS

# Views
VIEW_EVENTS = 0
VIEW_DEVICES = 1

PLATFORM_KEYS = ('meross', 'netatmo', 'vesync', 'cozytouch', 'petlibro')


def _clock(ts):
    return local_time(datetime.fromtimestamp(ts)) if ts else "-"


def _stamp(ts):
    return local_datetime(datetime.fromtimestamp(ts)) if ts else "-"


def _device_state_text(row):
    """The state of one device in one short phrase."""
    if not row['ok']:
        # Translators: State of a device in the platform status: it has not
        # answered since {time}.
        return _("no answer since {time}").format(time=_clock(row['since']))
    # Translators: State of a device in the platform status: it answers again,
    # {time} = when it last did not.
    return _("answers again, last missed at {time}").format(
        time=_clock(row['last_miss']))


class PlatformStatusDialog(wx.Dialog):
    """What is answering, what is not, and what happened before."""

    def __init__(self, parent, plugin):
        super(PlatformStatusDialog, self).__init__(
            parent,
            # Translators: Window title of the platform status dialog.
            title=_("Platform status"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
            size=(720, 480),
        )
        self.plugin = plugin
        self.health = getattr(plugin, 'platform_health', None)
        # Per list row the matching record - None for an informational row.
        self._row_data = []

        self._init_ui()
        self._fill(speak=False)
        self.list_ctrl.SetFocus()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _init_ui(self):
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        view_sizer = wx.BoxSizer(wx.HORIZONTAL)
        # Translators: Label of the view selector in the platform status.
        view_sizer.Add(wx.StaticText(self, label=_("&View:")), 0,
                       wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        # Translators: The two views of the platform status dialog.
        self.view_choice = wx.Choice(self, choices=[
            _("Events"), _("Devices that did not answer")])
        # Translators: Accessible name of the view selector.
        self.view_choice.SetName(_("View"))
        self.view_choice.SetSelection(VIEW_EVENTS)
        self.view_choice.Bind(wx.EVT_CHOICE, self._on_view_changed)
        view_sizer.Add(self.view_choice, 0, wx.ALIGN_CENTER_VERTICAL)
        main_sizer.Add(view_sizer, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)

        self.list_ctrl = wx.ListCtrl(
            self,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_HRULES | wx.LC_VRULES,
        )
        self._build_columns()
        main_sizer.Add(self.list_ctrl, 1, wx.EXPAND | wx.ALL, 8)

        self.status_text = wx.StaticText(self, label="")
        # Translators: Accessible name of the status line.
        self.status_text.SetName(_("Status"))
        main_sizer.Add(self.status_text, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        # Translators: Button that reloads the platform status list.
        refresh_btn = wx.Button(self, label=_("&Refresh (F5)"))
        refresh_btn.Bind(wx.EVT_BUTTON, lambda e: self._fill(speak=True))
        btn_sizer.Add(refresh_btn, 0, wx.RIGHT, 8)
        # Translators: Button for closing the dialog.
        close_btn = wx.Button(self, wx.ID_CLOSE, label=_("C&lose"))
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        btn_sizer.Add(close_btn, 0)
        main_sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 8)

        self.SetSizer(main_sizer)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char)
        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_item_activated)
        self.CenterOnParent()

    def _current_view(self):
        return self.view_choice.GetSelection()

    def _build_columns(self):
        """Rebuilds the columns to match the current view.

        Three and four columns on purpose: a screen reader reads every column
        of the focused row on each arrow key, so the detail belongs one Enter
        deeper rather than in another column.
        """
        self.list_ctrl.ClearAll()
        if self._current_view() == VIEW_DEVICES:
            # The same words as the entry in the view selector: whoever tabs
            # into the list hears which of the two they are standing in.
            self.list_ctrl.SetName(_("Devices that did not answer"))
            # Translators: Column headers of the device view in the platform
            # status.
            columns = [(_("Device"), 220), (_("Platform"), 100),
                       (_("State"), 220), (_("Last answer"), 130)]
        else:
            self.list_ctrl.SetName(_("Events"))
            # Translators: Column headers of the event view in the platform
            # status.
            columns = [(_("Time"), 90), (_("Platform"), 100),
                       (_("What happened"), 380)]
        for index, (label, width) in enumerate(columns):
            self.list_ctrl.InsertColumn(index, label, width=width)

    # ------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------
    def _active_platforms(self):
        return [key for key in PLATFORM_KEYS
                if getattr(self.plugin, f"use_{key}", False)]

    def _fill(self, speak=True):
        """Fills the list from the record and updates the status line."""
        self.list_ctrl.DeleteAllItems()
        self._row_data = []
        if self.health is None:
            self._add_info_row(
                # Translators: Shown when the platform record is not available
                # yet, i.e. before the first sign-in.
                _("Not signed in yet - nothing has been polled."))
            self._set_status("")
            return

        if self._current_view() == VIEW_DEVICES:
            count = self._fill_devices()
            # Translators: Status line of the device view. {count} = number of
            # devices that missed at least one poll.
            summary = _("{count} device(s) missed a poll").format(count=count)
        else:
            count = self._fill_events()
            # Translators: Status line of the event view. {count} = number of
            # recorded changes.
            summary = _("{count} change(s) since signing in").format(count=count)

        state = platform_health.summary_text(
            self.health, self._active_platforms(), PLATFORM_LABELS)
        if not state:
            # Translators: Status line when every enabled platform answers.
            state = _("All platforms answering")
        text = f"{state} | {summary}"
        self._set_status(text)
        if speak:
            ui.message(text)

    def _fill_events(self):
        events = self.health.events()
        if not events:
            self._add_info_row(
                # Translators: Shown in the event view when nothing has
                # changed since signing in.
                _("No changes since signing in - every platform has been "
                  "answering."))
            return 0
        for event in events:
            index = self.list_ctrl.InsertItem(
                self.list_ctrl.GetItemCount(), _clock(event['time']))
            self.list_ctrl.SetItem(
                index, 1, PLATFORM_LABELS.get(event['platform'],
                                              event['platform']))
            self.list_ctrl.SetItem(
                index, 2, platform_health.event_text(event, PLATFORM_LABELS))
            self._row_data.append(('event', event))
        return len(events)

    def _fill_devices(self):
        rows = []
        for platform in self._active_platforms():
            rows.extend(self.health.device_rows(platform))
        # Still failing first, then by the most recent miss - the record
        # already sorts per platform, this keeps that order across them.
        rows.sort(key=lambda row: (row['ok'], -(row['last_miss'] or 0)))
        if not rows:
            self._add_info_row(
                # Translators: Shown in the device view when every device
                # answered every poll.
                _("Every device answered every poll."))
            return 0
        for row in rows:
            index = self.list_ctrl.InsertItem(
                self.list_ctrl.GetItemCount(), row['name'])
            self.list_ctrl.SetItem(
                index, 1, PLATFORM_LABELS.get(row['platform'],
                                              row['platform']))
            self.list_ctrl.SetItem(index, 2, _device_state_text(row))
            self.list_ctrl.SetItem(index, 3, _clock(row['last_ok']))
            self._row_data.append(('device', row))
        return len(rows)

    def _add_info_row(self, text):
        index = self.list_ctrl.InsertItem(self.list_ctrl.GetItemCount(), text)
        self._row_data.append((None, None))
        return index

    def _set_status(self, text):
        self.status_text.SetLabel(text)

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def _on_view_changed(self, event):
        """Rebuilds the list for the chosen view - and leaves the focus alone.

        wx.Choice fires this on every arrow key while the box is closed, so
        moving the focus to the list here took the focus away mid-selection:
        the chosen entry was never read out, and the second arrow key went to
        the list instead of to the next view.
        """
        self._build_columns()
        self._fill(speak=False)

    def _on_item_activated(self, event):
        index = event.GetIndex()
        if index < 0 or index >= len(self._row_data):
            return
        kind, data = self._row_data[index]
        if kind == 'event':
            title, text = self._event_detail(data)
        elif kind == 'device':
            title, text = self._device_detail(data)
        else:
            return
        dlg = _DetailDialog(self, title, text)
        dlg.ShowModal()
        dlg.Destroy()

    def _event_detail(self, event):
        label = PLATFORM_LABELS.get(event['platform'], event['platform'])
        # Translators: Lines of the detail view of one platform event: the
        # platform's brand name, when it happened, and what happened.
        lines = [
            _("Platform: {value}").format(value=label),
            _("Time: {value}").format(value=_stamp(event['time'])),
            _("Event: {value}").format(
                value=platform_health.event_text(event, PLATFORM_LABELS)),
        ]
        parts = platform_health.part_names(event.get('parts'))
        if parts:
            # Translators: Detail view: which services of a platform did not
            # answer.
            lines.append(_("Not answering: {value}").format(
                value=", ".join(parts)))
        return label, "\n".join(lines)

    def _device_detail(self, row):
        label = PLATFORM_LABELS.get(row['platform'], row['platform'])
        # Translators: Lines of the detail view of one device: its name, the
        # platform it belongs to, and whether it is answering.
        lines = [
            _("Device: {value}").format(value=row['name']),
            _("Platform: {value}").format(value=label),
            _("State: {value}").format(value=_device_state_text(row)),
            # Translators: Detail view: when the device last answered.
            _("Last answer: {value}").format(
                value=f"{_stamp(row['last_ok'])} ({relative_time(row['last_ok'])})"
                if row['last_ok'] else "-"),
            # Translators: Detail view: when it last did not answer.
            _("Last missed: {value}").format(value=_stamp(row['last_miss'])),
        ]
        if row['misses']:
            # Translators: Detail view: how many polls in a row were missed.
            lines.append(_("Missed in a row: {value}").format(
                value=row['misses']))
        # Translators: Detail view: how many polls the device missed in total
        # since signing in.
        lines.append(_("Missed since signing in: {value}").format(
            value=row['total_misses']))
        lines.append("")
        # Translators: Detail view: hint that a device missing single polls is
        # not a fault of the platform.
        lines.append(_("A device that misses single polls is usually slow or "
                       "far from the access point; the last known values stay "
                       "in the device menu until it answers again."))
        return row['name'], "\n".join(lines)

    def _on_char(self, event):
        keycode = event.GetKeyCode()
        if keycode == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        if keycode == wx.WXK_F5:
            self._fill(speak=True)
            return
        event.Skip()
