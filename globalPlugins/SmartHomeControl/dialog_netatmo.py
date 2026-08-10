# -*- coding: utf-8 -*-
"""Smart Home Control - Netatmo-specific dialog methods (mixin)."""

import wx
import ui
import threading
import tones
import time
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
    NETATMO_MODE_NAMES, BEEP_OFF,
    BEEP_ACTION, BEEP_ERROR,
)
from .history import get_history
from .dialog_helpers import _beep

log = _nvda_log


class _NetatmoDialogMixin:
    """Netatmo methods for SmartHomeControlDialog (thermostats, heating modes, schedule)."""

    def _handle_netatmo_thermostat(self, device, item):
        """Shows the dialog for setting the Netatmo thermostat temperature with duration"""
        import datetime
        current = device.get_setpoint_temp()
        measured = device.get_temperature()
        current_end_time = device.get_setpoint_end_time()
        
        # Translators: Title of the thermostat dialog.
        dlg = wx.Dialog(self, title=_("Thermostat einstellen"), style=wx.DEFAULT_DIALOG_STYLE)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Help text
        # Translators: Help text in the thermostat dialog. {name} = device
        # name.
        help_text = _("Soll-Temperatur für {name} eingeben.\n").format(name=device.name)
        # Translators: Valid range of the temperature input.
        help_text += _("Gültig: 5 bis 30 Grad in 0,5°C-Schritten.\n")
        if measured is not None:
            # Translators: Display of the measured room temperature.
            help_text += _("Aktuelle Raumtemperatur: {temp}°C\n").format(temp=f"{measured:.1f}")
        if current is not None:
            # Translators: Display of the current target temperature.
            help_text += _("Aktuelle Soll-Temperatur: {temp}°C").format(temp=f"{current:.1f}")
        info_label = wx.StaticText(dlg, label=help_text)
        sizer.Add(info_label, 0, wx.ALL, 8)

        # Temperature selection (0.5°C steps)
        # Translators: Label of the temperature selection list.
        temp_label = wx.StaticText(dlg, label=_("&Temperatur (°C):"))
        sizer.Add(temp_label, 0, wx.LEFT | wx.TOP, 8)

        # Build the list from 5.0 to 30.0 in 0.5 steps
        temp_values = [f"{t/2:.1f}" for t in range(10, 61)]  # 5.0 to 30.0
        temp_ctrl = wx.Choice(dlg, choices=temp_values)
        # Translators: Name/tooltip of the temperature selection list.
        temp_ctrl.SetName(_("Temperatur"))
        temp_ctrl.SetToolTip(_("Soll-Temperatur in 0,5°C-Schritten wählen"))
        
        # Preselection based on the current temperature
        if current is not None:
            rounded = round(current * 2) / 2
            target = f"{rounded:.1f}"
            if target in temp_values:
                temp_ctrl.SetSelection(temp_values.index(target))
            else:
                temp_ctrl.SetSelection(temp_values.index("20.0"))
        else:
            temp_ctrl.SetSelection(temp_values.index("20.0"))
        sizer.Add(temp_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        
        # Duration option: default duration or until a given time
        # Translators: Label of the duration section in the thermostat dialog.
        duration_label = wx.StaticText(dlg, label=_("Dauer:"))
        sizer.Add(duration_label, 0, wx.LEFT | wx.TOP, 8)

        # Determine the default duration from the Netatmo setting
        default_dur_min = getattr(device, '_therm_setpoint_default_duration', None)
        if default_dur_min:
            # Translators: Checkbox label with a known default duration.
            default_label = _("&Standard-Dauer verwenden ({minutes} Minuten)").format(minutes=default_dur_min)
        else:
            # Translators: Checkbox label without a known default duration.
            default_label = _("&Standard-Dauer verwenden (wie in Netatmo-App eingestellt)")
        permanent_cb = wx.CheckBox(dlg, label=default_label)
        permanent_cb.SetValue(True)
        sizer.Add(permanent_cb, 0, wx.LEFT | wx.RIGHT, 8)
        
        # Time input (hour + minute)
        time_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        # Translators: Label of the hour selection.
        hour_label = wx.StaticText(dlg, label=_("&Stunde:"))
        time_sizer.Add(hour_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)

        hour_choices = [f"{h:02d}" for h in range(24)]
        hour_ctrl = wx.Choice(dlg, choices=hour_choices)
        # Translators: Name/tooltip of the hour selection.
        hour_ctrl.SetName(_("Stunde"))
        hour_ctrl.SetToolTip(_("Stunde bis wann die Temperatur gelten soll"))
        # Preselection: from the existing end time or current hour + 1
        now = datetime.datetime.now()
        if current_end_time and current_end_time > time.time():
            end_dt = datetime.datetime.fromtimestamp(current_end_time)
            hour_ctrl.SetSelection(end_dt.hour)
        else:
            hour_ctrl.SetSelection((now.hour + 1) % 24)
        time_sizer.Add(hour_ctrl, 0, wx.RIGHT, 8)
        
        # Translators: Label of the minute selection.
        minute_label = wx.StaticText(dlg, label=_("Mi&nute:"))
        time_sizer.Add(minute_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)

        minute_choices = [f"{m:02d}" for m in range(0, 60, 5)]
        minute_ctrl = wx.Choice(dlg, choices=minute_choices)
        # Translators: Name/tooltip of the minute selection.
        minute_ctrl.SetName(_("Minute"))
        minute_ctrl.SetToolTip(_("Minute bis wann die Temperatur gelten soll (5-Minuten-Schritte)"))
        if current_end_time and current_end_time > time.time():
            end_dt = datetime.datetime.fromtimestamp(current_end_time)
            nearest_min = (end_dt.minute // 5) * 5
            min_str = f"{nearest_min:02d}"
            if min_str in minute_choices:
                minute_ctrl.SetSelection(minute_choices.index(min_str))
            else:
                minute_ctrl.SetSelection(0)
        else:
            minute_ctrl.SetSelection(0)  # :00
        time_sizer.Add(minute_ctrl, 0, wx.RIGHT, 4)
        
        sizer.Add(time_sizer, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        
        # Disable the time controls initially (permanent = checked)
        hour_ctrl.Enable(False)
        minute_ctrl.Enable(False)
        
        def on_permanent_changed(evt):
            is_permanent = permanent_cb.GetValue()
            hour_ctrl.Enable(not is_permanent)
            minute_ctrl.Enable(not is_permanent)
        
        permanent_cb.Bind(wx.EVT_CHECKBOX, on_permanent_changed)
        
        # Buttons
        btn_sizer = dlg.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 8)
        
        dlg.SetSizer(sizer)
        sizer.Fit(dlg)
        
        if dlg.ShowModal() == wx.ID_OK:
            try:
                selection = temp_ctrl.GetSelection()
                if selection == wx.NOT_FOUND:
                    tones.beep(300, 100)
                    # Translators: Message when no temperature was selected.
                    ui.message(_("Bitte Temperatur wählen"))
                    dlg.Destroy()
                    return
                
                temp = float(temp_values[selection])
                
                # Compute the end time
                if permanent_cb.GetValue():
                    endtime = None
                else:
                    hour_sel = hour_ctrl.GetSelection()
                    minute_sel = minute_ctrl.GetSelection()
                    selected_hour = int(hour_choices[hour_sel])
                    selected_minute = int(minute_choices[minute_sel])
                    
                    # Assemble the end time as a datetime
                    end_dt = now.replace(hour=selected_hour, minute=selected_minute, second=0, microsecond=0)
                    
                    # If the time has already passed today, use the next day
                    if end_dt <= now:
                        end_dt += datetime.timedelta(days=1)
                    
                    endtime = int(end_dt.timestamp())
                
                # Set the temperature via the Netatmo API
                netatmo_api = self.plugin.netatmo_api
                home_id = getattr(device, 'home_id', '')
                room_id = getattr(device, 'room_id', '')
                
                if netatmo_api and home_id and room_id:
                    netatmo_api.set_room_thermpoint(home_id, room_id, 'manual', temp, endtime=endtime)
                    device._therm_setpoint = temp
                    device._therm_setpoint_mode = 'manual'
                    device._therm_setpoint_end_time = endtime
                    _beep(BEEP_OFF)  # formerly 600,80 = success
                    
                    # Log to history
                    history = get_history()
                    details = f"{temp:.1f}°C"
                    if endtime:
                        details += f" bis {time.strftime('%H:%M', time.localtime(endtime))}"
                    history.log_action(device, 'set_temp', details)
                    
                    # Confirmation message
                    if endtime:
                        end_str = time.strftime("%H:%M", time.localtime(endtime))
                        # Translators: Confirmation with end time. {temp} =
                        # temperature, {end} = time.
                        ui.message(_("{name}: Soll-Temperatur auf {temp}°C gesetzt bis {end} Uhr").format(
                            name=device.name, temp=f"{temp:.1f}", end=end_str))
                    else:
                        # Without an explicit end time: Netatmo uses the
                        # default duration
                        dur = getattr(device, '_therm_setpoint_default_duration', None)
                        if dur:
                            # Translators: Confirmation with default duration.
                            # {minutes} = minutes.
                            ui.message(_("{name}: Soll-Temperatur auf {temp}°C gesetzt (Standard-Dauer: {minutes} Minuten)").format(
                                name=device.name, temp=f"{temp:.1f}", minutes=dur))
                        else:
                            # Translators: Confirmation without a duration.
                            ui.message(_("{name}: Soll-Temperatur auf {temp}°C gesetzt").format(
                                name=device.name, temp=f"{temp:.1f}"))
                    
                    # Update the info in the tree
                    sp_text = self._format_setpoint_text(temp, endtime)
                    self.tree.SetItemText(item, sp_text)
                    
                    # Also update the device label in the tree (mode display)
                    self._update_netatmo_device_label(device)
                    # Fetch fresh data immediately (confirm end time and mode
                    # from the API)
                    self._refresh_netatmo_device_after_action(device)
                elif not netatmo_api:
                    tones.beep(200, 100)
                    # Translators: Error message when the Netatmo API is not
                    # initialized.
                    ui.message(_("Netatmo-API nicht verfügbar"))
                else:
                    tones.beep(200, 100)
                    # Translators: Error message for missing Netatmo IDs.
                    ui.message(_("Home- oder Raum-ID fehlt"))
            except ValueError:
                tones.beep(300, 100)
                # Translators: Message on invalid temperature selection.
                ui.message(_("Bitte gültige Temperatur wählen"))
            except Exception as e:
                _beep(BEEP_ERROR)
                log.error(f"Netatmo Thermostat-Fehler: {e}")
                # Translators: Error message with detail text.
                ui.message(_("Fehler beim Setzen der Temperatur: {error}").format(error=str(e)[:60]))
        dlg.Destroy()

    def _format_setpoint_text(self, setpoint, end_time=None):
        """Formats the target temperature text for the tree incl. optional end time"""
        if setpoint is None:
            # Translators: Action entry when no target temperature is known.
            return _("Soll-Temperatur einstellen")
        # Translators: Display of the current target temperature in the device
        # tree.
        text = _("Soll-Temperatur: {temp}°C").format(temp=f"{setpoint:.1f}")
        if end_time and end_time > time.time():
            end_str = time.strftime("%H:%M", time.localtime(end_time))
            # Translators: End time suffix after the target temperature.
            text += _(" (bis {end} Uhr)").format(end=end_str)
        # Translators: Usage hint after the target temperature.
        text += _(" - Enter zum Ändern")
        return text

    def _get_netatmo_mode_text(self, device):
        """Returns the current heating mode as readable text"""
        mode = device.get_setpoint_mode() if hasattr(device, 'get_setpoint_mode') else None
        if mode:
            mode_text = NETATMO_MODE_NAMES.get(mode, mode)
            # For schedule mode also show the active zone name (e.g. comfort,
            # eco, night)
            if mode == 'schedule':
                zone_name = device.get_schedule_zone_name() if hasattr(device, 'get_schedule_zone_name') else None
                if zone_name:
                    mode_text += f" ({zone_name})"
            return mode_text
        elif device.get_setpoint_temp() is not None:
            # Translators: Heating mode display for a manually set temperature.
            return _("Manuell")
        # Translators: Heating mode display when the mode is unknown.
        return _("Unbekannt")

    def _get_netatmo_device_label(self, device):
        """Builds the device label for Netatmo devices incl. room name and
        heating mode for thermostats."""
        display_type = device.get_type_display() if hasattr(device, 'get_type_display') else device.type
        room = getattr(device, 'room_name', '') or ''
        if room:
            # Translators: Device label with type and room name. {name} =
            # device name, {type} = device type, {room} = room name.
            display_name = _("{name} ({type}, Raum {room})").format(
                name=device.name, type=display_type, room=room)
        else:
            display_name = f"{device.name} ({display_type})"
        if getattr(device, 'is_thermostat', False):
            mode_text = self._get_netatmo_mode_text(device)
            if mode_text:
                display_name += f" - {mode_text}"
            # The boiler status is shown as a separate info item, not in the
            # label (avoids redundancy)
        return display_name

    def _update_netatmo_device_label(self, device):
        """Updates the label of a Netatmo device in the tree (e.g. after a mode change)"""
        if not getattr(device, 'is_netatmo', False):
            return
        try:
            root = self.tree.GetRootItem()
            if not root.IsOk():
                return
            # Search the categories (Netatmo main node > category > device)
            platform, p_cookie = self.tree.GetFirstChild(root)
            while platform.IsOk():
                category, c_cookie = self.tree.GetFirstChild(platform)
                while category.IsOk():
                    dev_item, d_cookie = self.tree.GetFirstChild(category)
                    while dev_item.IsOk():
                        data = self.tree.GetItemData(dev_item)
                        if data and data.get('type') == 'device' and data.get('device') is device:
                            new_label = self._get_netatmo_device_label(device)
                            current_label = self.tree.GetItemText(dev_item)
                            if new_label != current_label:
                                self.tree.SetItemText(dev_item, new_label)
                            return
                        dev_item, d_cookie = self.tree.GetNextChild(category, d_cookie)
                    category, c_cookie = self.tree.GetNextChild(platform, c_cookie)
                platform, p_cookie = self.tree.GetNextChild(root, p_cookie)
        except Exception as e:
            log.debug(f"Fehler beim Aktualisieren des Netatmo-Labels: {e}")

    def _refresh_netatmo_device_after_action(self, device, delay=3):
        """Fetches fresh data immediately after a Netatmo action (schedule change, mode change etc.).

        Args:
            device: the affected NetatmoDevice
            delay: wait time in seconds before the refresh starts (the API needs a moment)
        """
        def _do_refresh():
            try:
                netatmo_api = self.plugin.netatmo_api
                if not netatmo_api:
                    return
                
                import time as _time
                _time.sleep(delay)
                
                # Fetch fresh data from the API
                fresh_devices = netatmo_api.get_devices()
                fresh_map = {d.uuid: d for d in fresh_devices}
                
                # Update all Netatmo devices in place
                for dev in self.plugin.devices:
                    if getattr(dev, 'is_netatmo', False):
                        fresh = fresh_map.get(dev.uuid)
                        if fresh:
                            dev._dashboard_data = fresh._dashboard_data
                            dev._therm_measured = fresh._therm_measured
                            dev._therm_setpoint = fresh._therm_setpoint
                            dev._therm_setpoint_mode = fresh._therm_setpoint_mode
                            dev._therm_setpoint_end_time = fresh._therm_setpoint_end_time
                            dev._boiler_status = fresh._boiler_status
                            dev._schedule_zone_name = fresh._schedule_zone_name
                            dev._therm_setpoint_default_duration = fresh._therm_setpoint_default_duration
                            dev._anticipating = fresh._anticipating
                            dev._open_window = fresh._open_window
                            dev._next_schedule_change = fresh._next_schedule_change
                            dev.is_offline = fresh.is_offline
                            dev.raw_data = fresh.raw_data
                
                # Update the state tracking (prevents duplicate change
                # announcements)
                if hasattr(self.plugin, '_previous_netatmo_therm_states'):
                    for dev in fresh_devices:
                        if getattr(dev, 'is_thermostat', False):
                            # Normalization consistent with
                            # _detect_netatmo_changes
                            raw_et = dev._therm_setpoint_end_time
                            norm_et = self.plugin._normalize_end_time(raw_et) if hasattr(self.plugin, '_normalize_end_time') else raw_et
                            raw_sp = dev._therm_setpoint
                            norm_sp = round(raw_sp, 1) if raw_sp is not None else None
                            self.plugin._previous_netatmo_therm_states[dev.uuid] = {
                                'setpoint': norm_sp,
                                'mode': dev._therm_setpoint_mode,
                                'end_time': norm_et,
                                'zone_name': getattr(dev, '_schedule_zone_name', None),
                                'boiler': getattr(dev, '_boiler_status', None),
                                'anticipating': getattr(dev, '_anticipating', None),
                                'open_window': getattr(dev, '_open_window', None),
                            }
                
                # Update the UI on the main thread
                if not self._is_destroyed:
                    wx.CallAfter(self._finish_netatmo_refresh, device)
                
                log.debug("Netatmo Sofort-Refresh nach Aktion erfolgreich")
            except Exception as e:
                log.debug(f"Netatmo Sofort-Refresh fehlgeschlagen: {e}")
        
        import threading
        threading.Thread(target=_do_refresh, daemon=True).start()

    def _finish_netatmo_refresh(self, device):
        """Called on the main thread to update the UI after a Netatmo refresh."""
        if self._is_destroyed:
            return
        try:
            # Update the device label
            self._update_netatmo_device_label(device)
            # Live-update the whole tree (updates setpoint, mode etc.)
            self.refresh_all_device_data_live()
            log.debug("Netatmo UI nach Sofort-Refresh aktualisiert")
        except Exception as e:
            log.debug(f"Netatmo UI-Update nach Refresh fehlgeschlagen: {e}")

    def _handle_netatmo_therm_mode(self, device, item):
        """Shows the dialog for switching the Netatmo heating mode"""
        netatmo_api = self.plugin.netatmo_api
        home_id = getattr(device, 'home_id', '')
        
        if not netatmo_api or not home_id:
            tones.beep(200, 100)
            # Translators: Error message for a missing Netatmo API or home ID.
            ui.message(_("Netatmo-API oder Home-ID fehlt"))
            return
        
        current_mode = device.get_setpoint_mode() if hasattr(device, 'get_setpoint_mode') else None
        
        # Translators: Choices in the heating mode dialog.
        choices = [
            _("Zeitplan – automatisch nach Heizprogramm"),
            _("Abwesend – reduzierte Temperatur"),
            _("Frostschutz – Minimaltemperatur"),
        ]
        mode_keys = ['schedule', 'away', 'hg']

        # Translators: Help text in the heating mode dialog. {name} = device
        # name.
        help_text = _("Heizmodus für {name} wählen.\n").format(name=device.name)
        if current_mode:
            # Translators: Display of the current heating mode.
            help_text += _("Aktueller Modus: {mode}").format(
                mode=NETATMO_MODE_NAMES.get(current_mode, current_mode))
        elif device.get_setpoint_temp() is not None:
            # Translators: Display when the heating mode is manual.
            help_text += _("Aktueller Modus: Manuell")

        dlg = wx.SingleChoiceDialog(
            # Translators: Title of the heating mode dialog.
            self, help_text, _("Heizmodus wählen"), choices
        )
        
        # Preselection based on the current mode
        if current_mode in mode_keys:
            dlg.SetSelection(mode_keys.index(current_mode))
        else:
            dlg.SetSelection(0)
        
        if dlg.ShowModal() == wx.ID_OK:
            selection = dlg.GetSelection()
            mode_key = mode_keys[selection]
            mode_display = NETATMO_MODE_NAMES.get(mode_key, mode_key)

            try:
                netatmo_api.set_therm_mode(home_id, mode_key)
                device._therm_setpoint_mode = mode_key
                _beep(BEEP_OFF)  # formerly 600,80 = success
                # Translators: Confirmation after a heating mode change. {mode}
                # = mode name.
                ui.message(_("{name}: Heizmodus auf {mode} gesetzt").format(
                    name=device.name, mode=mode_display))
                # Translators: History detail: heating mode that was set.
                get_history().log_action(device, 'therm_mode', _('Modus: {mode} ({key})').format(
                    mode=mode_display, key=mode_key))
                # Update the tree text
                # Translators: Combined info+action label in the device tree.
                self.tree.SetItemText(item, _("Heizmodus: {mode} - Enter zum Ändern").format(mode=mode_display))
                # Update the device label in the tree (mode display in the
                # name)
                self._update_netatmo_device_label(device)
                # Fetch fresh data immediately (the setpoint changes depending
                # on the mode)
                self._refresh_netatmo_device_after_action(device)
            except Exception as e:
                _beep(BEEP_ERROR)
                log.error(f"Netatmo Heizmodus-Fehler: {e}")
                # Translators: Error message with detail text.
                ui.message(_("Fehler beim Setzen des Heizmodus: {error}").format(error=str(e)[:60]))
        dlg.Destroy()

    def _handle_netatmo_switch_schedule(self, device, item):
        """Shows the dialog for switching the Netatmo heating schedule.

        The API call ``get_schedules`` runs in a thread so the UI does not
        block (e.g. when the Netatmo cloud responds slowly).
        """
        netatmo_api = self.plugin.netatmo_api
        home_id = getattr(device, 'home_id', '')

        if not netatmo_api or not home_id:
            _beep(BEEP_ERROR)
            # Translators: Error when the Netatmo API is not ready yet.
            ui.message(_("Netatmo-API oder Home-ID fehlt"))
            return

        # Load the available heating schedules asynchronously (UI stays
        # responsive).
        # Translators: Announcement while the heating schedules are loaded from
        # the cloud.
        ui.message(_("Lade Heizprogramme..."))
        self._start_loading_beep()

        def fetch_schedules():
            try:
                return netatmo_api.get_schedules(home_id)
            except Exception as e:
                return e

        def on_loaded(result):
            self._stop_loading_beep()
            if self._is_destroyed:
                return
            if isinstance(result, Exception):
                _beep(BEEP_ERROR)
                _nvda_log.error(f"Netatmo Heizprogramme-Fehler: {result}")
                ui.message(_("Fehler beim Laden der Heizprogramme"))
                return
            schedules = result
            if not schedules:
                _beep(BEEP_ACTION)
                # Translators: When the Netatmo cloud returns no heating
                # schedules.
                ui.message(_("Keine Heizprogramme gefunden"))
                return

            # Build the selection list
            choices = []
            current_idx = 0
            for i, sched in enumerate(schedules):
                label = sched['name']
                if sched.get('selected', False):
                    # Translators: Marker on the active heating schedule in the
                    # selection list.
                    label += " " + _("(aktiv)")
                    current_idx = i
                choices.append(label)

            dlg = wx.SingleChoiceDialog(
                self,
                # Translators: Hint text above the heating schedule selection.
                # {name}=device name, {count}=number of available schedules.
                _("Heizprogramm für {name} wählen.\n{count} Programme verfügbar.").format(
                    name=device.name, count=len(schedules),
                ),
                # Translators: Title of the heating schedule selection dialog.
                _("Heizprogramm wechseln"),
                choices,
            )
            try:
                dlg.SetSelection(current_idx)
                if dlg.ShowModal() == wx.ID_OK:
                    selection = dlg.GetSelection()
                    sched = schedules[selection]
                    try:
                        netatmo_api.switch_home_schedule(home_id, sched['id'])
                        # Zwischenspeicher verwerfen: sonst zeigt das
                        # Geräte-Menü bis zu HOMESDATA_CACHE_SECONDS lang
                        # weiter das vorherige Programm an.
                        netatmo_api.invalidate_homesdata_cache()
                        _beep(BEEP_OFF)
                        # Translators: Success message when switching the
                        # heating schedule.
                        ui.message(_("{name}: Heizprogramm '{schedule}' aktiviert").format(
                            name=device.name, schedule=sched['name'],
                        ))
                        get_history().log_action(device, 'switch_schedule', f'Programm: {sched["name"]}')
                        self._refresh_netatmo_device_after_action(device)
                    except Exception as e:
                        _beep(BEEP_ERROR)
                        _nvda_log.error(f"Netatmo Heizprogramm-Fehler: {e}")
                        # Translators: Error text when switching the heating
                        # schedule.
                        ui.message(_("Fehler beim Wechseln des Heizprogramms: {error}").format(
                            error=str(e)[:60],
                        ))
            finally:
                dlg.Destroy()

        def worker():
            result = fetch_schedules()
            wx.CallAfter(on_loaded, result)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_netatmo_back_to_schedule(self, device, item):
        """Resets the Netatmo thermostat back to schedule mode"""
        netatmo_api = self.plugin.netatmo_api
        home_id = getattr(device, 'home_id', '')
        room_id = getattr(device, 'room_id', '')
        
        if not netatmo_api or not home_id:
            tones.beep(200, 100)
            # Translators: Error message for a missing Netatmo API or home ID.
            ui.message(_("Netatmo-API oder Home-ID fehlt"))
            return
        
        try:
            # Set the room back to the schedule
            if room_id:
                netatmo_api.set_room_thermpoint(home_id, room_id, 'home')
            # Also set the home mode to schedule
            netatmo_api.set_therm_mode(home_id, 'schedule')
            device._therm_setpoint_mode = 'schedule'
            device._therm_setpoint_end_time = None
            _beep(BEEP_OFF)  # formerly 600,80 = success
            # Translators: Confirmation after returning to the heating
            # schedule.
            ui.message(_("{name}: Zurück zum Zeitplan").format(name=device.name))
            # Translators: History detail: schedule reactivated.
            get_history().log_action(device, 'back_to_schedule', _('Zeitplan wiederhergestellt'))
            # Update the device label in the tree (mode display)
            self._update_netatmo_device_label(device)
            # Fetch fresh data immediately (new setpoint depending on the
            # schedule)
            self._refresh_netatmo_device_after_action(device)
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"Netatmo Zeitplan-Fehler: {e}")
            # Translators: Generic error message with detail text.
            ui.message(_("Fehler: {error}").format(error=str(e)[:60]))

    def _compute_netatmo_thermostat_items(self, device):
        """Computes the ordered list of all tree items of a Netatmo thermostat.

        Returns:
            list of dicts ``{'text', 'kind', 'action'}``. ``kind`` is
            ``'info'`` or ``'action'``; for info items ``action`` is None.

        Used both by the full rebuild and by the incremental live update so
        both paths produce exactly the same order and conditions.
        """
        items = []

        # Info items (e.g. status, temperature)
        for info_line in self._get_device_info(device):
            items.append({'text': info_line, 'kind': 'info', 'action': None})

        # Boiler status
        boiler = device.get_boiler_status() if hasattr(device, 'get_boiler_status') else None
        if boiler is not None:
            # Translators: Boiler status in the device tree.
            boiler_text = _("Heizung: aktiv") if boiler else _("Heizung: aus")
            items.append({'text': boiler_text, 'kind': 'info', 'action': None})

        # Pre-heating
        anticipating = device.is_anticipating() if hasattr(device, 'is_anticipating') else None
        if anticipating:
            # Translators: Note that the thermostat is pre-heating.
            items.append({'text': _("Vorausheizen: aktiv"), 'kind': 'info', 'action': None})

        # Open window
        open_window = device.is_open_window() if hasattr(device, 'is_open_window') else None
        if open_window:
            items.append({
                # Translators: Note about a detected open window.
                'text': _("Offenes Fenster: erkannt (Heizung pausiert)"),
                'kind': 'info', 'action': None,
            })

        # Next schedule change
        next_change = device.get_next_schedule_change() if hasattr(device, 'get_next_schedule_change') else None
        if next_change and next_change.get('time'):
            try:
                change_time_str = time.strftime("%H:%M", time.localtime(next_change['time']))
                nc_zone = next_change.get('zone_name', '')
                nc_temp = next_change.get('temp')
                if nc_temp is not None:
                    # Translators: Next schedule change with temperature.
                    nc_text = _("Nächste Planänderung: {zone} ({temp}°C) um {time}").format(
                        zone=nc_zone, temp=f"{nc_temp:.1f}", time=change_time_str)
                else:
                    # Translators: Next schedule change without temperature.
                    nc_text = _("Nächste Planänderung: {zone} um {time}").format(
                        zone=nc_zone, time=change_time_str)
                items.append({'text': nc_text, 'kind': 'info', 'action': None})
            except Exception as e:
                log.debug(f"Ignorierter Fehler in _compute_netatmo_thermostat_items: {e}")

        # Target temperature (action)
        setpoint = device.get_setpoint_temp()
        end_time = device.get_setpoint_end_time() if hasattr(device, 'get_setpoint_end_time') else None
        items.append({
            'text': self._format_setpoint_text(setpoint, end_time),
            'kind': 'action', 'action': 'netatmo_thermostat',
        })

        # Heating mode
        mode_text = self._get_netatmo_mode_text(device)
        items.append({
            # Translators: Combined info+action label in the device tree.
            'text': _("Heizmodus: {mode} - Enter zum Ändern").format(mode=mode_text),
            'kind': 'action', 'action': 'netatmo_therm_mode',
        })

        # Heating schedule
        active_sched_name = getattr(device, '_active_schedule_name', None)
        if active_sched_name:
            # Translators: Combined info+action label in the device tree.
            sched_label = _("Heizprogramm: {schedule} - Enter zum Wechseln").format(schedule=active_sched_name)
        else:
            # Translators: Action entry for switching the heating schedule.
            sched_label = _("Heizprogramm wechseln")
        items.append({
            'text': sched_label,
            'kind': 'action', 'action': 'netatmo_switch_schedule',
        })

        # Back to schedule (only when not in schedule mode)
        current_mode = device.get_setpoint_mode() if hasattr(device, 'get_setpoint_mode') else None
        if current_mode and current_mode != 'schedule':
            items.append({
                # Translators: Action entry for returning to schedule mode.
                'text': _("Zurück zum Zeitplan - Enter zum Aktivieren"),
                'kind': 'action', 'action': 'netatmo_back_to_schedule',
            })

        return items

    def _live_update_netatmo_thermostat_children(self, device_item, device):
        """Updates a Netatmo thermostat without a tree rebuild.

        Analogous to the VeSync counterpart: compare the target item list
        with the current tree children. For structurally identical items only
        ``SetItemText`` for changed texts. Structural changes (e.g. "open
        window" appears or disappears, "back to schedule" becomes visible)
        trigger a one-time full rebuild that preserves focus.

        This removes the constant flickering on the braille display and the
        repeated speaking of the focused item that a full rebuild caused on
        every 30 s refresh.
        """
        # Update the device label on the main node if needed (mode in the name)
        new_label = self._get_netatmo_device_label(device)
        if self.tree.GetItemText(device_item) != new_label:
            self.tree.SetItemText(device_item, new_label)

        expected = self._compute_netatmo_thermostat_items(device)

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
                    if ch_type != 'action':
                        structure_matches = False
                        break
                    if ch_data.get('action') != exp['action']:
                        structure_matches = False
                        break

        if not structure_matches:
            self._rebuild_netatmo_thermostat_children(device_item, device)
            return

        # Incremental update: only changed texts via SetItemText
        for ch, exp in zip(children, expected):
            if self.tree.GetItemText(ch) != exp['text']:
                self.tree.SetItemText(ch, exp['text'])

    def _rebuild_netatmo_thermostat_children(self, device_item, device):
        """
        Completely rebuilds the child items of a Netatmo thermostat.

        Only called when the item structure changes (e.g. "open window"
        appears/disappears) - otherwise the live update
        ``_live_update_netatmo_thermostat_children`` is used and only updates
        the changed texts without a rebuild.
        Preserves the focus position if a child item was focused.
        """
        # Remember the focus position (index of the focused child)
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

        # Remove the children and rebuild them via the central item list
        self.tree.DeleteChildren(device_item)
        for item in self._compute_netatmo_thermostat_items(device):
            tree_item = self.tree.AppendItem(device_item, item['text'])
            data = {'type': item['kind'], 'device': device}
            if item['kind'] == 'action':
                data['action'] = item['action']
            self.tree.SetItemData(tree_item, data)
        
        # Restore the focus (without a duplicate NVDA announcement)
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
            else:
                # Index was larger than the new child count -> focus the last
                # child
                target_child = last_child if last_child and last_child.IsOk() else None
            
            if target_child and target_child.IsOk():
                # FIX: temporarily suppress focus events to avoid duplicate
                # NVDA announcements. SelectItem triggers EVT_TREE_SEL_CHANGED,
                # which makes NVDA speak. Since refresh_all_device_data_live
                # checks again afterwards and possibly calls ui.message, the
                # focused item was announced twice.
                self._suppress_tree_focus_event = True
                try:
                    self.tree.SelectItem(target_child)
                    self.tree.SetFocusedItem(target_child)
                finally:
                    # Reset the flag via CallAfter so the event stays
                    # suppressed during the current processing
                    wx.CallAfter(setattr, self, '_suppress_tree_focus_event', False)

    def _add_netatmo_devices_to_category(self, cat, devices):
        """Inserts Netatmo devices into a category"""
        for device in devices:
            self._add_single_netatmo_device(cat, device)

    def _add_single_netatmo_device(self, parent_node, device, is_favorite_view=False):
        """Inserts a single Netatmo device as a child node.

        Args:
            parent_node: parent tree item
            device: NetatmoDevice object
            is_favorite_view: True when in the favorites view (shows 'remove' instead of 'add')
        """
        # Display name with readable device type, heating mode and boiler
        # status
        display_name = self._get_netatmo_device_label(device)
        device_item = self.tree.AppendItem(parent_node, display_name)
        self.tree.SetItemData(device_item, {'type': 'device', 'device': device})
        
        info_lines = self._get_device_info(device)
        for info_line in info_lines:
            info_item = self.tree.AppendItem(device_item, info_line)
            self.tree.SetItemData(info_item, {'type': 'info', 'device': device})
        
        # Thermostat actions (NATherm1, NRV)
        if getattr(device, 'is_thermostat', False):
            # Show the boiler status
            boiler = device.get_boiler_status() if hasattr(device, 'get_boiler_status') else None
            if boiler is not None:
                # Translators: Boiler status in the device tree.
                boiler_text = _("Heizung: aktiv") if boiler else _("Heizung: aus")
                boiler_item = self.tree.AppendItem(device_item, boiler_text)
                self.tree.SetItemData(boiler_item, {'type': 'info', 'device': device})
            
            # Show pre-heating (anticipation)
            anticipating = device.is_anticipating() if hasattr(device, 'is_anticipating') else None
            if anticipating:
                # Translators: Note that the thermostat is pre-heating.
                antic_item = self.tree.AppendItem(device_item, _("Vorausheizen: aktiv"))
                self.tree.SetItemData(antic_item, {'type': 'info', 'device': device})

            # Show open window
            open_window = device.is_open_window() if hasattr(device, 'is_open_window') else None
            if open_window:
                # Translators: Note about a detected open window.
                ow_item = self.tree.AppendItem(device_item, _("Offenes Fenster: erkannt (Heizung pausiert)"))
                self.tree.SetItemData(ow_item, {'type': 'info', 'device': device})
            
            # Show the next schedule change (schedule mode only)
            next_change = device.get_next_schedule_change() if hasattr(device, 'get_next_schedule_change') else None
            if next_change and next_change.get('time'):
                try:
                    change_time_str = time.strftime("%H:%M", time.localtime(next_change['time']))
                    nc_zone = next_change.get('zone_name', '')
                    nc_temp = next_change.get('temp')
                    if nc_temp is not None:
                        # Translators: Next schedule change with temperature.
                        nc_text = _("Nächste Planänderung: {zone} ({temp}°C) um {time}").format(
                            zone=nc_zone, temp=f"{nc_temp:.1f}", time=change_time_str)
                    else:
                        # Translators: Next schedule change without
                        # temperature.
                        nc_text = _("Nächste Planänderung: {zone} um {time}").format(
                            zone=nc_zone, time=change_time_str)
                    nc_item = self.tree.AppendItem(device_item, nc_text)
                    self.tree.SetItemData(nc_item, {'type': 'info', 'device': device})
                except Exception as e:
                    log.debug(f"Ignorierter Fehler in _add_single_netatmo_device: {e}")
            
            # 1. Set the target temperature
            setpoint = device.get_setpoint_temp()
            end_time = device.get_setpoint_end_time() if hasattr(device, 'get_setpoint_end_time') else None
            sp_text = self._format_setpoint_text(setpoint, end_time)
            sp_item = self.tree.AppendItem(device_item, sp_text)
            self.tree.SetItemData(sp_item, {
                'type': 'action', 'device': device,
                'action': 'netatmo_thermostat'
            })
            
            # 2. Switch heating mode (schedule / away / frost guard)
            mode_text = self._get_netatmo_mode_text(device)
            # Translators: Combined info+action label in the device tree.
            hm_item = self.tree.AppendItem(device_item, _("Heizmodus: {mode} - Enter zum Ändern").format(mode=mode_text))
            self.tree.SetItemData(hm_item, {
                'type': 'action', 'device': device,
                'action': 'netatmo_therm_mode'
            })

            # 3. Switch heating schedule (with the active schedule name from
            # the cache)
            # Translators: Action entry for switching the heating schedule.
            sched_label = _("Heizprogramm wechseln")
            active_sched_name = getattr(device, '_active_schedule_name', None)
            if active_sched_name:
                # Translators: Combined info+action label in the device tree.
                sched_label = _("Heizprogramm: {schedule} - Enter zum Wechseln").format(schedule=active_sched_name)
            sched_item = self.tree.AppendItem(device_item, sched_label)
            self.tree.SetItemData(sched_item, {
                'type': 'action', 'device': device,
                'action': 'netatmo_switch_schedule'
            })

            # 4. Back to schedule (if manual)
            current_mode = device.get_setpoint_mode() if hasattr(device, 'get_setpoint_mode') else None
            if current_mode and current_mode != 'schedule':
                # Translators: Action entry for returning to schedule mode.
                back_item = self.tree.AppendItem(device_item, _("Zurück zum Zeitplan - Enter zum Aktivieren"))
                self.tree.SetItemData(back_item, {
                    'type': 'action', 'device': device,
                    'action': 'netatmo_back_to_schedule'
                })
        
        # Favorite action (on every Netatmo device)
        self._add_favorite_action(device_item, device, is_favorite_view)
        
        self.tree.Collapse(device_item)

