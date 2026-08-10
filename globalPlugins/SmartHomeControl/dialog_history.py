# -*- coding: utf-8 -*-
"""
Smart Home Control - Verlaufs-Dialog

Zwei Ansichten statt einer flachen Liste:

* **Ereignisse** - das, wofuer man einen Verlauf aufschlaegt. Eine Zeile je
  Schaltvorgang, mit der Herkunft ("ich" / "extern" / "automatisch"). Nach
  Tagen gruppiert, damit nicht in jeder Zeile dasselbe Datum vorgelesen wird.
* **Messwerte** - verdichtet zu Min/Max/Mittelwert je Geraet und Groesse. Eine
  Zeile beantwortet damit, wofuer man vorher hundert Einzelzeilen durchgehen
  musste.

Ausgelagert aus device_dialog.py (Modul-Aufteilung).
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
    log.debug(f"initTranslation fehlgeschlagen: {e}")
if "_" not in globals():  # Fallback, falls initTranslation() scheitert
    # Ohne diesen Fallback bleibt `_` undefiniert und der erste `_()`-Aufruf
    # wirft einen NameError mitten im Dialogaufbau statt beim Import.
    def _(s):
        return s

from .history import (
    get_history, _format_action_text, _source_text, format_measurement,
    relative_time, _measurement_labels,
)
from .dialog_helpers import _beep
from .constants import BEEP_ON, BEEP_OFF, BEEP_ERROR

# Ansichten
VIEW_EVENTS = 0
VIEW_MEASUREMENTS = 1


def _day_label(dt):
    """Überschrift für eine Tagesgruppe: Heute / Gestern / Wochentag, Datum."""
    today = datetime.now().date()
    day = dt.date()
    if day == today:
        # Translators: Group heading in the history for the current day.
        return _("Heute")
    if day == today - timedelta(days=1):
        # Translators: Group heading in the history for the previous day.
        return _("Gestern")
    # Translators: Group heading in the history: weekday and date,
    # e.g. "Freitag, 24.07.2026".
    return _("{weekday}, {date}").format(
        weekday=dt.strftime('%A'), date=dt.strftime('%d.%m.%Y'))


class HistoryDialog(wx.Dialog):
    """Accessible history dialog for screen reader users.

    Shows switch actions and sensor values with filter options
    and CSV export. Optimized for NVDA navigation.
    """

    def __init__(self, parent, plugin):
        super(HistoryDialog, self).__init__(
            parent,
            # Translators: Window title of the history dialog.
            title=_("Geräteverlauf"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
            size=(760, 520),
        )
        self.plugin = plugin
        self.history = get_history()
        # Je Zeile der Liste der zugehörige Eintrag - None bei einer
        # Tagesüberschrift. Nötig, weil die Überschriften die Indizes
        # gegenüber der Eintragsliste verschieben.
        self._row_data = []
        self._current_entries = []

        self._init_ui()
        # Beim Öffnen NICHT ansagen: das Fenster meldet sich ohnehin, und die
        # Zahl der Einträge steht in der Statuszeile.
        self._apply_filters(speak=False)

        # Focus on the history list
        self.list_ctrl.SetFocus()

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------
    def _init_ui(self):
        """Builds the dialog interface"""
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # === Ansicht ===
        view_sizer = wx.BoxSizer(wx.HORIZONTAL)
        # Translators: Label of the view selector in the history dialog.
        view_sizer.Add(wx.StaticText(self, label=_("&Ansicht:")), 0,
                       wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        # Translators: The two views of the history dialog.
        self.view_choice = wx.Choice(self, choices=[
            _("Ereignisse"), _("Messwerte")])
        self.view_choice.SetSelection(VIEW_EVENTS)
        self.view_choice.Bind(wx.EVT_CHOICE, self._on_view_changed)
        view_sizer.Add(self.view_choice, 0, wx.ALIGN_CENTER_VERTICAL)
        main_sizer.Add(view_sizer, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)

        # === Filter area ===
        # Translators: Label of the filter area in the history dialog.
        filter_box = wx.StaticBox(self, label=_("Filter"))
        filter_sizer = wx.StaticBoxSizer(filter_box, wx.HORIZONTAL)

        # Device filter
        filter_sizer.Add(wx.StaticText(self, label=_("&Gerät:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self.device_filter = wx.Choice(self)
        # Load the devices from the history (separately, so it can be refreshed
        # via F5)
        self._populate_device_filter()
        filter_sizer.Add(self.device_filter, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        # Platform filter
        filter_sizer.Add(wx.StaticText(self, label=_("&Plattform:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        # Translators: Filter option "All" (platform names are brand names).
        self.platform_filter = wx.Choice(self, choices=[_("Alle"), "Meross", "Netatmo", "VeSync", "Cozytouch"])
        self.platform_filter.SetSelection(0)
        filter_sizer.Add(self.platform_filter, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        # Time range filter
        filter_sizer.Add(wx.StaticText(self, label=_("&Zeitraum:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        # Translators: Time range filter options in the history dialog.
        self.time_filter = wx.Choice(self, choices=[
            _("Alles"), _("Letzte Stunde"), _("Letzte 24 Stunden"),
            _("Letzte 7 Tage"), _("Letzte 30 Tage")
        ])
        self.time_filter.SetSelection(0)
        filter_sizer.Add(self.time_filter, 0, wx.ALIGN_CENTER_VERTICAL)

        main_sizer.Add(filter_sizer, 0, wx.EXPAND | wx.ALL, 8)

        # Filter button
        # Translators: Button for applying the filters.
        filter_btn = wx.Button(self, label=_("&Filtern"))
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
        export_btn = wx.Button(self, label=_("Als &CSV exportieren"))
        export_btn.Bind(wx.EVT_BUTTON, self._on_export_csv)
        btn_sizer.Add(export_btn, 0, wx.RIGHT, 8)

        # Translators: Button for deleting the history.
        clear_btn = wx.Button(self, label=_("Verlauf &löschen"))
        clear_btn.Bind(wx.EVT_BUTTON, self._on_clear_history)
        btn_sizer.Add(clear_btn, 0, wx.RIGHT, 8)

        # Translators: Button for closing the dialog.
        close_btn = wx.Button(self, wx.ID_CLOSE, label=_("Sc&hließen"))
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
        """Setzt die Spalten passend zur aktuellen Ansicht neu."""
        self.list_ctrl.ClearAll()
        if self._current_view() == VIEW_MEASUREMENTS:
            # Translators: Column headers of the measurement view.
            columns = [
                (_("Gerät"), 170), (_("Größe"), 110), (_("Kleinster"), 100),
                (_("Größter"), 100), (_("Mittelwert"), 100),
                (_("Aktuell"), 100), (_("Punkte"), 70),
            ]
        else:
            # Translators: Column headers of the event view.
            columns = [
                (_("Zeitpunkt"), 90), (_("Gerät"), 170), (_("Plattform"), 90),
                (_("Ereignis"), 240), (_("Herkunft"), 90),
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
        self.device_filter.Append(_("Alle Geräte"), "")
        restore_index = 0
        for dev in self.history.get_unique_devices():
            plat = dev.get('platform', '').capitalize()
            label = f"{dev['name']} ({plat})" if plat else dev['name']
            idx = self.device_filter.Append(label, dev['uuid'])
            if dev['uuid'] == prev_uuid:
                restore_index = idx
        self.device_filter.SetSelection(restore_index)

    # ------------------------------------------------------------------
    # Tastatur / Ansicht
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
            ui.message(_("Verlauf aktualisiert"))
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
    # Ansicht: Ereignisse
    # ------------------------------------------------------------------
    def _populate_events(self, speak=True):
        """Füllt die Liste mit Ereignissen, nach Tagen gruppiert.

        Die Tagesüberschriften ersparen das Datum in jeder einzelnen Zeile -
        bei Sprachausgabe der größte Einzelgewinn, weil sonst vor jedem
        Ereignis dasselbe Datum steht.
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
            device_name = entry.get('device_name', _('Unbekannt'))
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
            _("{count} Ereignisse angezeigt (gesamt: {total})").format(
                count=count, total=total))
        if not speak:
            return
        if count > 0:
            # Translators: Message after loading the event list.
            ui.message(_("{count} Ereignisse").format(count=count))
        else:
            # Translators: Message when no events match the filter.
            ui.message(_("Keine Ereignisse gefunden"))

    # ------------------------------------------------------------------
    # Ansicht: Messwerte
    # ------------------------------------------------------------------
    def _populate_measurements(self, speak=True):
        """Füllt die Liste mit verdichteten Messwerten."""
        self.list_ctrl.DeleteAllItems()
        self._row_data = []
        labels = _measurement_labels()

        for row, rec in enumerate(self._current_entries):
            quantity = rec['quantity']
            idx = self.list_ctrl.InsertItem(row, rec['device_name'])
            self.list_ctrl.SetItem(idx, 1, labels.get(quantity, quantity))
            self.list_ctrl.SetItem(idx, 2, format_measurement(quantity, rec['min']))
            self.list_ctrl.SetItem(idx, 3, format_measurement(quantity, rec['max']))
            self.list_ctrl.SetItem(idx, 4, format_measurement(quantity, rec['avg']))
            self.list_ctrl.SetItem(idx, 5, format_measurement(quantity, rec['last']))
            self.list_ctrl.SetItem(idx, 6, str(rec['count']))
            self._row_data.append(rec)

        count = len(self._current_entries)
        total = self.history.get_measurement_count()
        # Translators: Status line of the measurement view. {count} = number of
        # summarized rows, {total} = number of stored change points.
        self.status_text.SetLabel(
            _("{count} Messreihen (aus {total} Änderungspunkten)").format(
                count=count, total=total))
        if not speak:
            return
        if count > 0:
            # Translators: Message after loading the measurement view.
            ui.message(_("{count} Messreihen").format(count=count))
        else:
            # Translators: Message when no measurements match the filter.
            ui.message(_("Keine Messwerte gefunden"))

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
            return  # Tagesüberschrift - nichts zu zeigen

        if self._current_view() == VIEW_MEASUREMENTS:
            detail_text = self._measurement_detail(record)
        else:
            detail_text = self.history.format_entry_for_display(record)

        dlg = wx.MessageDialog(
            self,
            detail_text,
            # Translators: Title of the detail dialog of a history entry.
            _("Verlaufseintrag Details"),
            wx.OK | wx.ICON_INFORMATION,
        )
        dlg.ShowModal()
        dlg.Destroy()

    def _measurement_detail(self, rec):
        """Detailtext einer verdichteten Messreihe.

        Zeigt die Verdichtung und darunter die letzten Änderungspunkte - das
        ist das "Aufklappen" der Zeile, ohne die Liste zu überfrachten.
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
            _("Kleinster Wert: {value}").format(
                value=format_measurement(quantity, rec['min'])),
            _("Größter Wert: {value}").format(
                value=format_measurement(quantity, rec['max'])),
            # Translators: Time-weighted mean of a measurement series.
            _("Mittelwert (zeitgewichtet): {value}").format(
                value=format_measurement(quantity, rec['avg'])),
            _("Aktuell: {value}").format(
                value=format_measurement(quantity, rec['last'])),
            "",
            # Translators: Heading of the change point list.
            _("Letzte Änderungen:"),
        ]
        for entry in points:
            value = (entry.get('sensor_data') or {}).get(quantity)
            lines.append("  {time} ({rel}): {value}".format(
                time=datetime.fromtimestamp(
                    entry.get('timestamp', 0)).strftime('%d.%m.%Y %H:%M'),
                rel=relative_time(entry.get('timestamp', 0)),
                value=format_measurement(quantity, value)))
        if not points:
            # Translators: Shown when a measurement series has no change
            # points in the selected period.
            lines.append("  " + _("Keine Änderungen im gewählten Zeitraum"))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Export / Löschen
    # ------------------------------------------------------------------
    def _on_export_csv(self, event):
        """Exports the current history as CSV"""
        dlg = wx.FileDialog(
            self,
            # Translators: Title of the file dialog for the CSV export.
            _("Verlauf als CSV exportieren"),
            defaultFile="smart_home_verlauf.csv",
            # Translators: File type filter in the file dialog.
            wildcard=_("CSV-Dateien (*.csv)|*.csv"),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )

        if dlg.ShowModal() == wx.ID_OK:
            filepath = dlg.GetPath()
            dlg.Destroy()

            try:
                device_uuid, platform, since_hours = self._get_filter_values()
                # In der Messwert-Ansicht die Änderungspunkte exportieren
                # (nicht die Verdichtung): eine Tabelle ist zum Weiterrechnen
                # da, und dafür braucht man die Punkte.
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
                ui.message(_("Verlauf exportiert nach {filename}").format(
                    filename=os.path.basename(filepath)))

                export_msg = wx.MessageDialog(
                    self,
                    # Translators: Success message in the export dialog.
                    # {path} = file.
                    _("Verlauf erfolgreich exportiert.\n\nDatei: {path}").format(
                        path=filepath),
                    # Translators: Title of the export success dialog.
                    _("Export erfolgreich"),
                    wx.OK | wx.ICON_INFORMATION,
                )
                export_msg.ShowModal()
                export_msg.Destroy()
            except Exception as e:
                _beep(BEEP_ERROR)
                log.error(f"CSV-Export fehlgeschlagen: {e}")
                # Translators: Error message on CSV export.
                ui.message(_("Export fehlgeschlagen: {error}").format(error=str(e)[:60]))
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
            _("Möchten Sie wirklich den gesamten Verlauf löschen?\n\n"
              "Es werden {events} Ereignisse und {measurements} Messwerte "
              "unwiderruflich gelöscht.").format(
                  events=events, measurements=measurements),
            # Translators: Title of the confirmation prompt.
            _("Verlauf löschen"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
        )

        if dlg.ShowModal() == wx.ID_YES:
            self.history.clear()
            self._current_entries = []
            self._apply_filters(speak=False)
            _beep(BEEP_OFF)  # formerly 600,80 = success
            # Translators: Confirmation after deleting the history.
            ui.message(_("Verlauf gelöscht"))
        dlg.Destroy()
