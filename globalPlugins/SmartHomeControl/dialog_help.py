# -*- coding: utf-8 -*-
"""
Smart Home Control - context help (F1) of the device dialog.
Split out of device_dialog.py; behaviour unchanged.
"""

import time

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

from .constants import (
    VESYNC_PURIFIER_MODE_NAMES, VESYNC_FAN_MODE_NAMES,
    VESYNC_AIR_QUALITY_NAMES,
)


class _ContextHelpMixin:
    """F1 context help: general help and device-specific help."""

    def _show_context_help(self):
        """
        IMPROVEMENT 5: context-sensitive help (F1)
        Shows comprehensive help based on the currently selected element.
        Same level of detail for Meross and Netatmo devices.
        """
        item = self.tree.GetSelection()
        
        if not item.IsOk():
            # General help
            # Translators: General F1 help with all keyboard shortcuts of the
            # dialog.
            help_text = _(
                "Smart home device control - keyboard shortcuts: arrow keys: "
                "navigate, Enter or Space: execute action, F5: refresh, "
                "Ctrl+F: search, Ctrl+B: add or remove favorite, Ctrl+H: show "
                "history, Ctrl+T: announce status, Ctrl+Tab: switch tab, 1-9: "
                "jump to category, letters: quick search, F1: context help, "
                "ESC: close"
            )
            ui.message(help_text)
            return
        
        data = self.tree.GetItemData(item)
        
        if data is None:
            # Category
            # Translators: F1 help for a category node.
            ui.message(_("Category. Press Enter to expand or collapse. Right "
                         "arrow to open."))
        elif data.get('type') == 'loading':
            # Translators: F1 help while loading.
            ui.message(_("Loading devices, please wait."))
        elif data.get('type') == 'error':
            # Translators: F1 help on a loading error.
            ui.message(_("Loading error – press F5 to retry."))
        elif data.get('type') == 'device':
            device = data.get('device')
            channel = data.get('channel')
            self._show_device_context_help(device, channel)
        elif data.get('type') == 'info':
            # Context-sensitive info help. The startswith prefixes must match
            # the (translated) tree labels, hence also via _().
            # Translators: The following texts are F1 context help entries for
            # info items.
            current_text = self.tree.GetItemText(item)
            if current_text.startswith(_("Temperature:")):
                ui.message(_("Current room temperature from the sensor. "
                             "Updates automatically."))
            elif current_text.startswith(_("Humidity:")):
                ui.message(_("Current humidity. Updates automatically."))
            elif current_text.startswith(_("Heating:")):
                ui.message(_("Boiler status: shows whether the heating is "
                             "currently active or off."))
            elif current_text.startswith(_("Pre-heating:")):
                ui.message(_("Pre-heating: the heating starts early so the "
                             "target temperature is already reached at the "
                             "scheduled time."))
            elif current_text.startswith(_("Open window:")):
                ui.message(_("Open window detected: heating is paused "
                             "automatically to avoid wasting energy."))
            elif current_text.startswith(_("Next schedule change:")):
                ui.message(_("Shows the next scheduled temperature change "
                             "from the heating program: zone, target "
                             "temperature and time."))
            elif current_text.startswith(_("Hot water supply:")):
                ui.message(_("Available hot water supply in percent, "
                             "optionally with a liter estimate. Updates "
                             "automatically."))
            elif current_text.startswith(_("Off-peak tariff")):
                ui.message(_("Indicates that the heat pump is currently "
                             "heating with off-peak electricity."))
            elif current_text.startswith(_("Electric heating element")):
                ui.message(_("The electric backup heating element is "
                             "currently assisting the heat pump."))
            elif current_text.startswith(_("Today's heating times:")):
                ui.message(_("Today's programmed heating time windows of the "
                             "hot water heat pump."))
            elif current_text.startswith(_("Energy consumption")):
                ui.message(_("Accumulated energy consumption of the device in "
                             "kilowatt hours."))
            elif current_text.startswith(_("Status:")):
                ui.message(_("Device status. Updates automatically."))
            elif current_text.startswith(_("Power:")):
                ui.message(_("Current power consumption in watts. Updates "
                             "automatically."))
            elif current_text.startswith(_("Voltage:")):
                ui.message(_("Current mains voltage in volts."))
            elif current_text.startswith(_("Amperage:")):
                ui.message(_("Current amperage in amps."))
            elif current_text.startswith(_("Battery:")):
                ui.message(_("Current battery level of the device."))
            elif current_text.startswith("CO"):
                ui.message(_("CO₂ concentration in the room air."))
            elif current_text.startswith(_("Air pressure:")):
                ui.message(_("Current air pressure in millibars."))
            elif current_text.startswith(_("Air quality:")):
                ui.message(_("Air quality rating of the purifier from "
                             "excellent to poor. Updates automatically."))
            elif current_text.startswith("PM2.5:"):
                ui.message(_("Particulate matter PM2.5: concentration of very "
                             "fine particles in micrograms per cubic meter."))
            elif current_text.startswith("PM1.0:"):
                ui.message(_("Particulate matter PM1.0: concentration of "
                             "ultra-fine particles in micrograms per cubic "
                             "meter."))
            elif current_text.startswith("PM10:"):
                ui.message(_("Particulate matter PM10: concentration of "
                             "coarse particles in micrograms per cubic meter."))
            elif current_text.startswith("VOC:"):
                ui.message(_("Volatile organic compounds in the room air."))
            elif current_text.startswith(_("Filter life:")):
                ui.message(_("Remaining life of the HEPA filter in percent. "
                             "At low values, replace the filter and then "
                             "reset."))
            else:
                ui.message(_("Status information. Display only."))
        elif data.get('type') == 'action':
            action = data.get('action')
            # Translators: The following texts are F1 context help entries for
            # action items.
            if action == 'toggle':
                ui.message(_("Switch action. Press Enter or Space to turn on "
                             "or off."))
            elif action and action.startswith('diffuser'):
                ui.message(_("Diffuser action. Press Enter or Space to "
                             "execute."))
            elif action == 'light_luminance':
                ui.message(_("Set brightness. Press Enter to open the input "
                             "dialog. Value from 0 to 100 percent."))
            elif action == 'light_temperature':
                ui.message(_("Set light color. Press Enter to choose between "
                             "warm white, daylight and cool white."))
            elif action == 'light_rgb':
                ui.message(_("Set RGB color. Press Enter to open the color "
                             "picker with presets or custom input."))
            elif action == 'netatmo_thermostat':
                ui.message(_("Set target temperature. Press Enter to enter a "
                             "temperature from 5 to 30 degrees in 0.5-degree "
                             "steps with optional duration."))
            elif action == 'netatmo_therm_mode':
                ui.message(_("Choose heating mode: schedule, away or frost "
                             "guard. Press Enter to change."))
            elif action == 'netatmo_switch_schedule':
                ui.message(_("Switch heating schedule. Press Enter to select "
                             "a saved heating plan."))
            elif action == 'netatmo_back_to_schedule':
                ui.message(_("Back to the automatic schedule. Press Enter to "
                             "execute."))
            elif action == 'vesync_toggle':
                ui.message(_("Turn VeSync device on or off. Press Enter or "
                             "Space to execute."))
            elif action == 'vesync_end_cook':
                # Translators: Context help for the tree entry that stops a
                # cooking programme.
                ui.message(_("Stop the running cooking programme of the air "
                             "fryer. Enter asks for confirmation first."))
            elif action == 'vesync_start_cook':
                # Translators: Context help for the tree entry that starts a
                # cooking programme.
                ui.message(_("Start a cooking programme. Enter offers the "
                             "programmes this appliance has reported, plus a "
                             "free start with a temperature and a time. "
                             "Temperature and time can be adjusted for a "
                             "programme as well, and everything is confirmed "
                             "before it is sent. Cosori appliances may "
                             "require their own start button to be pressed "
                             "afterwards; the programme state then says "
                             "“ready to start” instead of "
                             "“cooking”."))
            elif action in ('vesync_set_cook_temp', 'vesync_set_cook_time'):
                # Translators: Context help for the tree entries that
                # change a loaded cooking programme.
                ui.message(_("Change the temperature or the cooking time "
                             "of the loaded programme. Enter asks for the "
                             "new value and confirms it before sending. "
                             "The remaining time line then shows what the "
                             "appliance made of it."))
            elif action == 'vesync_mode':
                ui.message(_("Choose operating mode. Air purifiers: auto, "
                             "manual or sleep. Fans: normal, turbo, auto or "
                             "sleep mode. Enter opens a selection list."))
            elif action == 'vesync_fan_speed':
                ui.message(_("Change fan speed. Enter opens a list of "
                             "available levels. Selecting a level "
                             "automatically switches to manual mode."))
            elif action == 'vesync_oscillation':
                ui.message(_("Turn tower fan oscillation on or off. Press "
                             "Enter or Space to toggle."))
            elif action == 'vesync_mute':
                ui.message(_("Turn tower fan mute on or off. When muted, the "
                             "device does not play confirmation sounds. Press "
                             "Enter or Space to toggle."))
            elif action == 'vesync_display':
                ui.message(_("Turn the device's display on or off. Press "
                             "Enter or Space to toggle."))
            elif action == 'vesync_child_lock':
                ui.message(_("Turn child lock on or off. Locks the controls "
                             "on the device. Press Enter or Space to toggle."))
            elif action == 'vesync_nightlight':
                ui.message(_("Choose night light mode: off, dimmed or on. "
                             "Enter opens a selection list."))
            elif action == 'vesync_auto_preference':
                ui.message(_("Choose auto profile: default, efficient or "
                             "quiet. Determines behavior in auto mode. Enter "
                             "opens a selection list."))
            elif action == 'vesync_reset_filter':
                ui.message(_("Reset filter life to 100 percent. Only do this "
                             "if the filter has actually been replaced. Enter "
                             "opens a confirmation dialog."))
            elif action == 'cozytouch_temp':
                ui.message(_("Set the target temperature of the hot water "
                             "heat pump. Press Enter to enter a temperature "
                             "within the allowed range."))
            elif action == 'cozytouch_mode':
                ui.message(_("Choose the operating mode of the hot water heat "
                             "pump. Enter opens a selection list."))
            elif action == 'cozytouch_boost':
                ui.message(_("Turn boost mode on or off: quickly heats the "
                             "water to maximum temperature. Press Enter or "
                             "Space to toggle."))
            elif action == 'cozytouch_toggle':
                ui.message(_("Turn hot water production on or off. Press "
                             "Enter or Space to execute."))
            elif action == 'cozytouch_away':
                ui.message(_("Schedule or end away mode: reduces hot water "
                             "production during longer absences. Enter opens "
                             "the period input (start and end); when away "
                             "mode is active or scheduled, Enter turns it off."))
            else:
                ui.message(_("Action. Press Enter or Space to execute."))
    
    def _show_device_context_help(self, device, channel=None):
        """
        Shows comprehensive, device-specific help via F1.
        Same level of detail for Meross and Netatmo.
        """
        parts = []

        # Translators: The following texts form the device-specific F1 help.
        if channel:
            parts.append(_("Channel: {name}").format(name=channel.name))
            parts.append(_("Status: {status}").format(status=_("On") if channel.is_on else _("Off")))
            if channel.has_power_meter:
                power = channel.get_power()
                if power is not None:
                    parts.append(_("Power: {power} W").format(power=power))
            parts.append(_("Action: turn on or off"))
        elif getattr(device, 'is_cozytouch', False):
            # --- Cozytouch (Atlantic / Austria Email) ---
            type_display = device.get_type_display() if hasattr(device, 'get_type_display') else device.type
            parts.append(f"{device.name} ({type_display})")
            if getattr(device, 'is_offline', False):
                parts.append(_("Status: offline"))
            else:
                parts.append(_("Status: {status}").format(status=_("On") if device.is_on else _("Off")))
                hw = device.hot_water_percent
                if hw is not None:
                    parts.append(_("Hot water supply: {percent}%").format(percent=hw))
                tt = device.target_temperature
                if tt is not None:
                    parts.append(_("Target temperature: {temp}°C").format(temp=tt))
                if device.mode_name:
                    parts.append(_("Mode: {mode}").format(mode=device.mode_name))
                if device.boost_on:
                    parts.append(_("Boost active"))
                if device.away_on:
                    parts.append(_("Away mode active"))
                parts.append(_("Actions: target temperature, mode, boost, "
                               "on/off, away mode"))
        elif getattr(device, 'is_vesync', False):
            # --- VeSync (Levoit) ---
            type_display = device.get_type_display() if hasattr(device, 'get_type_display') else device.type
            parts.append(f"{device.name} ({type_display})")
            if hasattr(device, 'is_offline') and device.is_offline:
                parts.append(_("Status: offline"))
            else:
                parts.append(_("Status: {status}").format(status=_("On") if device.is_on else _("Off")))
                cls_name = type(device).__name__
                if cls_name == 'VeSyncPurifier':
                    mode_label = VESYNC_PURIFIER_MODE_NAMES.get(device.mode, device.mode or '?')
                else:
                    mode_label = VESYNC_FAN_MODE_NAMES.get(device.mode, device.mode or '?')
                parts.append(_("Mode: {mode}").format(mode=mode_label))
                if device.fan_level is not None:
                    parts.append(_("Fan speed: {level}").format(level=device.fan_level))
                if cls_name == 'VeSyncPurifier':
                    if getattr(device, 'supports_air_quality', False) and device.air_quality is not None:
                        aq_text = VESYNC_AIR_QUALITY_NAMES.get(
                            device.air_quality, str(device.air_quality)
                        )
                        parts.append(_("Air quality: {value}").format(value=aq_text))
                    if device.air_quality_value is not None:
                        parts.append(f"PM2.5: {device.air_quality_value} µg/m³")
                    if device.filter_life is not None:
                        parts.append(_("Filter: {percent}%").format(percent=device.filter_life))
                    parts.append(_("Actions: on/off, mode, fan speed"))
                if cls_name == 'VeSyncTowerFan':
                    if device.oscillation_on is not None:
                        parts.append(_("Oscillation: {status}").format(
                            status=_("on") if device.oscillation_on else _("off")))
                    if device.temperature is not None:
                        try:
                            parts.append(_("Temperature: {temp}°C").format(
                                temp=f"{float(device.temperature):.1f}"))
                        except (TypeError, ValueError):
                            pass
                    parts.append(_("Actions: on/off, mode, fan speed, "
                                   "oscillation"))
        elif getattr(device, 'is_netatmo', False):
            # --- Netatmo ---
            type_display = device.get_type_display() if hasattr(device, 'get_type_display') else device.type
            parts.append(f"{device.name} ({type_display})")

            if hasattr(device, 'is_offline') and device.is_offline:
                parts.append(_("Status: offline"))
            else:
                temp = device.get_temperature()
                if temp is not None:
                    parts.append(_("Temperature: {temp}°C").format(temp=f"{temp:.1f}"))
                humidity = device.get_humidity()
                if humidity is not None:
                    parts.append(_("Humidity: {humidity}%").format(humidity=f"{humidity:g}"))
                co2 = device.get_co2() if hasattr(device, 'get_co2') else None
                if co2 is not None:
                    parts.append(f"CO₂: {co2} ppm")
                pressure = device.get_pressure() if hasattr(device, 'get_pressure') else None
                if pressure is not None:
                    parts.append(_("Air pressure: {pressure} mbar").format(pressure=f"{pressure:.1f}"))
                rain = device.get_rain() if hasattr(device, 'get_rain') else None
                if rain is not None:
                    parts.append(_("Rain: {rain} mm").format(rain=rain))
                wind = device.get_wind_strength() if hasattr(device, 'get_wind_strength') else None
                if wind is not None:
                    parts.append(_("Wind: {wind} km/h").format(wind=wind))

                if getattr(device, 'is_thermostat', False):
                    setpoint = device.get_setpoint_temp()
                    if setpoint is not None:
                        parts.append(_("Target: {temp}°C").format(temp=f"{setpoint:.1f}"))
                    mode_text = self._get_netatmo_mode_text(device)
                    parts.append(_("Mode: {mode}").format(mode=mode_text))
                    boiler = device.get_boiler_status() if hasattr(device, 'get_boiler_status') else None
                    if boiler is not None:
                        parts.append(_("Heating: {status}").format(
                            status=_("active") if boiler else _("off")))
                    # Show anticipation
                    anticipating = device.is_anticipating() if hasattr(device, 'is_anticipating') else None
                    if anticipating:
                        parts.append(_("Pre-heating active"))
                    open_window = device.is_open_window() if hasattr(device, 'is_open_window') else None
                    if open_window:
                        parts.append(_("Open window detected"))
                    next_change = device.get_next_schedule_change() if hasattr(device, 'get_next_schedule_change') else None
                    if next_change and next_change.get('time'):
                        try:
                            change_time_str = time.strftime("%H:%M", time.localtime(next_change['time']))
                            nc_zone = next_change.get('zone_name', '')
                            nc_temp = next_change.get('temp')
                            if nc_temp is not None:
                                parts.append(_("Next change: {zone} "
                                               "({temp}°C) at {time}").format(
                                    zone=nc_zone, temp=f"{nc_temp:.1f}", time=change_time_str))
                            else:
                                parts.append(_("Next change: {zone} at {time}").format(
                                    zone=nc_zone, time=change_time_str))
                        except Exception as e:
                            log.debug(f"Ignored error in _show_device_context_help: {e}")
                    parts.append(_("Actions: target temperature, heating "
                                   "mode, heating schedule"))
                elif getattr(device, 'is_relay', False):
                    boiler = device.get_boiler_status() if hasattr(device, 'get_boiler_status') else None
                    if boiler is not None:
                        parts.append(_("Heating: {status}").format(
                            status=_("active") if boiler else _("off")))

            battery = device.get_battery_percent() if hasattr(device, 'get_battery_percent') else None
            if battery is not None:
                parts.append(_("Battery: {percent}%").format(percent=battery))
        else:
            # --- Meross ---
            parts.append(f"{device.name} ({device.type})")

            if hasattr(device, 'is_offline') and device.is_offline:
                parts.append(_("Status: offline"))
            elif device.is_temperature_sensor:
                temp = device.get_temperature()
                humidity = device.get_humidity()
                if temp is not None:
                    parts.append(_("Temperature: {temp}°C").format(temp=f"{temp:.1f}"))
                if humidity is not None:
                    parts.append(_("Humidity: {humidity}%").format(humidity=f"{humidity:g}"))
                parts.append(_("Sensor - display only"))
            elif device.is_water_sensor:
                alarm = device.is_water_detected()
                parts.append(_("WATER ALARM!") if alarm else _("No water "
                                                               "detected"))
                parts.append(_("Sensor - display only"))
            elif device.is_hub:
                parts.append(_("Smart hub - manages sensors"))
                try:
                    if hasattr(device, 'get_subdevices'):
                        subdevices = device.get_subdevices()
                        if subdevices:
                            parts.append(_("Connected sensors: {count}").format(count=len(subdevices)))
                except Exception as e:
                    log.debug(f"Ignored error in _show_device_context_help: {e}")
            elif device.is_diffuser:
                spray = device.get_diffuser_spray_mode()
                parts.append(_("Spray mode: {mode}").format(mode=spray))
                parts.append(_("Actions: light spray, strong spray, off"))
            elif device.is_multi_channel:
                channels = device.get_channels()
                on_count = sum(1 for ch in channels if ch.is_on)
                parts.append(_("{on} of {total} channels switched on").format(
                    on=on_count, total=len(channels)))
                parts.append(_("Expand channels for individual control"))
            else:
                parts.append(_("Status: {status}").format(status=_("On") if device.is_on else _("Off")))
                if device.has_power_meter:
                    power = device.get_power()
                    if power is not None:
                        parts.append(_("Power: {power} W").format(power=power))
                if device.is_light:
                    parts.append(_("Actions: on/off, brightness, light color"))
                elif not device.is_sensor:
                    parts.append(_("Action: turn on/off"))

        parts.append(_("Press Enter to expand or collapse"))
        ui.message(". ".join(parts))
    
