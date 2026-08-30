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
                # Translators: F1 help for the "Temperature:" line in the
                # device tree.
                ui.message(_("Current room temperature from the sensor. "
                             "Updates automatically."))
            elif current_text.startswith(_("Humidity:")):
                # Translators: F1 help for the "Humidity:" line in the device
                # tree.
                ui.message(_("Current humidity. Updates automatically."))
            elif current_text.startswith(_("Heating:")):
                # Translators: F1 help for the "Heating:" line in the device
                # tree.
                ui.message(_("Boiler status: shows whether the heating is "
                             "currently active or off."))
            elif current_text.startswith(_("Pre-heating:")):
                # Translators: F1 help for the "Pre-heating:" line in the
                # device tree.
                ui.message(_("Pre-heating: the heating starts early so the "
                             "target temperature is already reached at the "
                             "scheduled time."))
            elif current_text.startswith(_("Open window:")):
                # Translators: F1 help for the "Open window:" line in the
                # device tree.
                ui.message(_("Open window detected: heating is paused "
                             "automatically to avoid wasting energy."))
            elif current_text.startswith(_("Next schedule change:")):
                # Translators: F1 help for the "Next schedule change:" line in
                # the device tree.
                ui.message(_("Shows the next scheduled temperature change "
                             "from the heating program: zone, target "
                             "temperature and time."))
            # Translators: Beginning of the "Hot water supply:" line in the
            # device tree. F1 picks its help by this text, so it has to be
            # translated exactly like the line itself.
            elif current_text.startswith(_("Hot water supply:")):
                # Translators: F1 help for the "Hot water supply:" line in the
                # device tree.
                ui.message(_("Available hot water supply in percent, "
                             "optionally with a liter estimate. Updates "
                             "automatically."))
            # Translators: Beginning of the "Off-peak tariff" line in the
            # device tree. F1 picks its help by this text, so it has to be
            # translated exactly like the line itself.
            elif current_text.startswith(_("Off-peak tariff")):
                # Translators: F1 help for the "Off-peak tariff" line in the
                # device tree.
                ui.message(_("Indicates that the heat pump is currently "
                             "heating with off-peak electricity."))
            # Translators: Beginning of the "Electric heating element" line in
            # the device tree. F1 picks its help by this text, so it has to be
            # translated exactly like the line itself.
            elif current_text.startswith(_("Electric heating element")):
                # Translators: F1 help for the "Electric heating element" line
                # in the device tree.
                ui.message(_("The electric backup heating element is "
                             "currently assisting the heat pump."))
            # Translators: Beginning of the "Today's heating times:" line in
            # the device tree. F1 picks its help by this text, so it has to be
            # translated exactly like the line itself.
            elif current_text.startswith(_("Today's heating times:")):
                # Translators: F1 help for the "Today's heating times:" line in
                # the device tree.
                ui.message(_("Today's programmed heating time windows of the "
                             "hot water heat pump."))
            # Translators: Beginning of the "Energy consumption" line in the
            # device tree. F1 picks its help by this text, so it has to be
            # translated exactly like the line itself.
            elif current_text.startswith(_("Energy consumption")):
                # Translators: F1 help for the "Energy consumption" line in the
                # device tree.
                ui.message(_("Accumulated energy consumption of the device in "
                             "kilowatt hours."))
            elif current_text.startswith(_("Status:")):
                # Translators: F1 help for the "Status:" line in the device
                # tree.
                ui.message(_("Device status. Updates automatically."))
            elif current_text.startswith(_("Power:")):
                # Translators: F1 help for the "Power:" line in the device
                # tree.
                ui.message(_("Current power consumption in watts. Updates "
                             "automatically."))
            # Translators: Beginning of the "Voltage:" line in the device tree.
            # F1 picks its help by this text, so it has to be translated
            # exactly like the line itself.
            elif current_text.startswith(_("Voltage:")):
                # Translators: F1 help for the "Voltage:" line in the device
                # tree.
                ui.message(_("Current mains voltage in volts."))
            # Translators: Beginning of the "Amperage:" line in the device
            # tree. F1 picks its help by this text, so it has to be translated
            # exactly like the line itself.
            elif current_text.startswith(_("Amperage:")):
                # Translators: F1 help for the "Amperage:" line in the device
                # tree.
                ui.message(_("Current amperage in amps."))
            elif current_text.startswith(_("Battery:")):
                # Translators: F1 help for the "Battery:" line in the device
                # tree.
                ui.message(_("Current battery level of the device."))
            elif current_text.startswith("CO"):
                # Translators: F1 help for the "CO" line in the device tree.
                ui.message(_("CO₂ concentration in the room air."))
            # Translators: Beginning of the "Air pressure:" line in the device
            # tree. F1 picks its help by this text, so it has to be translated
            # exactly like the line itself.
            elif current_text.startswith(_("Air pressure:")):
                # Translators: F1 help for the "Air pressure:" line in the
                # device tree.
                ui.message(_("Current air pressure in millibars."))
            # Translators: Beginning of the "Air quality:" line in the device
            # tree. F1 picks its help by this text, so it has to be translated
            # exactly like the line itself.
            elif current_text.startswith(_("Air quality:")):
                # Translators: F1 help for the "Air quality:" line in the
                # device tree.
                ui.message(_("Air quality rating of the purifier from "
                             "excellent to poor. Updates automatically."))
            elif current_text.startswith("PM2.5:"):
                # Translators: F1 help for the "PM2.5:" line in the device
                # tree.
                ui.message(_("Particulate matter PM2.5: concentration of very "
                             "fine particles in micrograms per cubic meter."))
            elif current_text.startswith("PM1.0:"):
                # Translators: F1 help for the "PM1.0:" line in the device
                # tree.
                ui.message(_("Particulate matter PM1.0: concentration of "
                             "ultra-fine particles in micrograms per cubic "
                             "meter."))
            elif current_text.startswith("PM10:"):
                # Translators: F1 help for the "PM10:" line in the device tree.
                ui.message(_("Particulate matter PM10: concentration of "
                             "coarse particles in micrograms per cubic meter."))
            elif current_text.startswith("VOC:"):
                # Translators: F1 help for the "VOC:" line in the device tree.
                ui.message(_("Volatile organic compounds in the room air."))
            # Translators: Beginning of the "Filter life:" line in the device
            # tree. F1 picks its help by this text, so it has to be translated
            # exactly like the line itself.
            elif current_text.startswith(_("Filter life:")):
                # Translators: F1 help for the "Filter life:" line in the
                # device tree.
                ui.message(_("Remaining life of the HEPA filter in percent. "
                             "At low values, replace the filter and then "
                             "reset."))
            else:
                # Translators: F1 help for any other line in the device tree:
                # the fallback when none of the more specific texts fits.
                ui.message(_("Status information. Display only."))
        elif data.get('type') == 'action':
            action = data.get('action')
            # Translators: The following texts are F1 context help entries for
            # action items.
            if action == 'toggle':
                # Translators: F1 help for the action line "toggle" in the
                # device tree.
                ui.message(_("Switch action. Press Enter or Space to turn on "
                             "or off."))
            elif action and action.startswith('diffuser'):
                # Translators: F1 help for the action line "diffuser" in the
                # device tree.
                ui.message(_("Diffuser action. Press Enter or Space to "
                             "execute."))
            elif action == 'light_luminance':
                # Translators: F1 help for the action line "light_luminance" in
                # the device tree.
                ui.message(_("Set brightness. Press Enter to open the input "
                             "dialog. Value from 0 to 100 percent."))
            elif action == 'light_temperature':
                # Translators: F1 help for the action line "light_temperature"
                # in the device tree.
                ui.message(_("Set light color. Press Enter to choose between "
                             "warm white, daylight and cool white."))
            elif action == 'light_rgb':
                # Translators: F1 help for the action line "light_rgb" in the
                # device tree.
                ui.message(_("Set RGB color. Press Enter to open the color "
                             "picker with presets or custom input."))
            elif action == 'netatmo_thermostat':
                # Translators: F1 help for the action line "netatmo_thermostat"
                # in the device tree.
                ui.message(_("Set target temperature. Press Enter to enter a "
                             "temperature from 5 to 30 degrees in 0.5-degree "
                             "steps with optional duration."))
            elif action == 'netatmo_therm_mode':
                # Translators: F1 help for the action line "netatmo_therm_mode"
                # in the device tree.
                ui.message(_("Choose heating mode: schedule, away or frost "
                             "guard. Press Enter to change."))
            elif action == 'netatmo_switch_schedule':
                # Translators: F1 help for the action line
                # "netatmo_switch_schedule" in the device tree.
                ui.message(_("Switch heating schedule. Press Enter to select "
                             "a saved heating plan."))
            elif action == 'netatmo_back_to_schedule':
                # Translators: F1 help for the action line
                # "netatmo_back_to_schedule" in the device tree.
                ui.message(_("Back to the automatic schedule. Press Enter to "
                             "execute."))
            elif action == 'vesync_toggle':
                # Translators: F1 help for the action line "vesync_toggle" in
                # the device tree.
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
                             "programmes this appliance has reported. "
                             "Temperature and time can be adjusted on the "
                             "way, and everything is confirmed "
                             "before it is sent. Cosori appliances may "
                             "require their own start button to be pressed "
                             "afterwards; the programme state then says "
                             "“ready to start” instead of "
                             "“cooking”."))
            elif action == 'vesync_set_cook_time':
                # Translators: Context help for the tree entry that changes
                # the time of a loaded cooking programme. The temperature
                # is deliberately not mentioned as changeable: the
                # appliance does not accept one while it cooks.
                ui.message(_("Change the cooking time of the loaded "
                             "programme. Enter asks for the new value and "
                             "confirms it before sending. The remaining "
                             "time line then shows what the appliance made "
                             "of it. The temperature cannot be changed "
                             "while the appliance cooks - only when a "
                             "programme is started, or at the appliance."))
            elif action == 'vesync_mode':
                # Translators: F1 help for the action line "vesync_mode" in the
                # device tree.
                ui.message(_("Choose operating mode. Air purifiers: auto, "
                             "manual or sleep. Fans: normal, turbo, auto or "
                             "sleep mode. Enter opens a selection list."))
            elif action == 'vesync_fan_speed':
                # Translators: F1 help for the action line "vesync_fan_speed"
                # in the device tree.
                ui.message(_("Change fan speed. Enter opens a list of "
                             "available levels. Selecting a level "
                             "automatically switches to manual mode."))
            elif action == 'vesync_oscillation':
                # Translators: F1 help for the action line "vesync_oscillation"
                # in the device tree.
                ui.message(_("Turn tower fan oscillation on or off. Press "
                             "Enter or Space to toggle."))
            elif action == 'vesync_mute':
                # Translators: F1 help for the action line "vesync_mute" in the
                # device tree.
                ui.message(_("Turn tower fan mute on or off. When muted, the "
                             "device does not play confirmation sounds. Press "
                             "Enter or Space to toggle."))
            elif action == 'vesync_display':
                # Translators: F1 help for the action line "vesync_display" in
                # the device tree.
                ui.message(_("Turn the device's display on or off. Press "
                             "Enter or Space to toggle."))
            elif action == 'vesync_child_lock':
                # Translators: F1 help for the action line "vesync_child_lock"
                # in the device tree.
                ui.message(_("Turn child lock on or off. Locks the controls "
                             "on the device. Press Enter or Space to toggle."))
            elif action == 'vesync_nightlight':
                # Translators: F1 help for the action line "vesync_nightlight"
                # in the device tree.
                ui.message(_("Choose night light mode: off, dimmed or on. "
                             "Enter opens a selection list."))
            elif action == 'vesync_auto_preference':
                # Translators: F1 help for the action line
                # "vesync_auto_preference" in the device tree.
                ui.message(_("Choose auto profile: default, efficient or "
                             "quiet. Determines behavior in auto mode. Enter "
                             "opens a selection list."))
            elif action == 'vesync_reset_filter':
                # Translators: F1 help for the action line
                # "vesync_reset_filter" in the device tree.
                ui.message(_("Reset filter life to 100 percent. Only do this "
                             "if the filter has actually been replaced. Enter "
                             "opens a confirmation dialog."))
            elif action == 'cozytouch_temp':
                # Translators: F1 help for the action line "cozytouch_temp" in
                # the device tree.
                ui.message(_("Set the target temperature of the hot water "
                             "heat pump. Press Enter to enter a temperature "
                             "within the allowed range."))
            elif action == 'cozytouch_mode':
                # Translators: F1 help for the action line "cozytouch_mode" in
                # the device tree.
                ui.message(_("Choose the operating mode of the hot water heat "
                             "pump. Enter opens a selection list."))
            elif action == 'cozytouch_boost':
                # Translators: F1 help for the action line "cozytouch_boost" in
                # the device tree.
                ui.message(_("Turn boost mode on or off: quickly heats the "
                             "water to maximum temperature. Press Enter or "
                             "Space to toggle."))
            elif action == 'cozytouch_toggle':
                # Translators: F1 help for the action line "cozytouch_toggle"
                # in the device tree.
                ui.message(_("Turn hot water production on or off. Press "
                             "Enter or Space to execute."))
            elif action == 'cozytouch_away':
                # Translators: F1 help for the action line "cozytouch_away" in
                # the device tree.
                ui.message(_("Schedule or end away mode: reduces hot water "
                             "production during longer absences. Enter opens "
                             "the period input (start and end); when away "
                             "mode is active or scheduled, Enter turns it off."))
            else:
                # Translators: F1 help for any other line in the device tree:
                # the fallback when none of the more specific texts fits.
                ui.message(_("Action. Press Enter or Space to execute."))
    
    def _show_device_context_help(self, device, channel=None):
        """
        Shows comprehensive, device-specific help via F1.
        Same level of detail for Meross and Netatmo.
        """
        parts = []

        # Translators: The following texts form the device-specific F1 help.
        if channel:
            # Translators: One piece of the device help spoken with F1. The
            # pieces are joined with ". ", so no full stop at the end.
            parts.append(_("Channel: {name}").format(name=channel.name))
            parts.append(_("Status: {status}").format(status=_("On") if channel.is_on else _("Off")))
            if channel.has_power_meter:
                power = channel.get_power()
                if power is not None:
                    # Translators: One piece of the device help spoken with F1.
                    # The pieces are joined with ". ", so no full stop at the
                    # end.
                    parts.append(_("Power: {power} W").format(power=power))
            # Translators: One piece of the device help spoken with F1. The
            # pieces are joined with ". ", so no full stop at the end.
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
                    # Translators: One piece of the device help spoken with F1.
                    # The pieces are joined with ". ", so no full stop at the
                    # end.
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
                # Translators: One piece of the device help spoken with F1. The
                # pieces are joined with ". ", so no full stop at the end.
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
                    # Translators: One piece of the device help spoken with F1.
                    # The pieces are joined with ". ", so no full stop at the
                    # end.
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
                        # Translators: One piece of the device help spoken with
                        # F1. The pieces are joined with ". ", so no full stop
                        # at the end.
                        parts.append(_("Filter: {percent}%").format(percent=device.filter_life))
                    # Translators: One piece of the device help spoken with F1.
                    # The pieces are joined with ". ", so no full stop at the
                    # end.
                    parts.append(_("Actions: on/off, mode, fan speed"))
                if cls_name == 'VeSyncTowerFan':
                    if device.oscillation_on is not None:
                        # Translators: One piece of the device help spoken with
                        # F1. The pieces are joined with ". ", so no full stop
                        # at the end.
                        parts.append(_("Oscillation: {status}").format(
                            status=_("on") if device.oscillation_on else _("off")))
                    if device.temperature is not None:
                        try:
                            parts.append(_("Temperature: {temp}°C").format(
                                temp=f"{float(device.temperature):.1f}"))
                        except (TypeError, ValueError):
                            pass
                    # Translators: One piece of the device help spoken with F1.
                    # The pieces are joined with ". ", so no full stop at the
                    # end.
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
                    # Translators: One piece of the device help spoken with F1.
                    # The pieces are joined with ". ", so no full stop at the
                    # end.
                    parts.append(_("Humidity: {humidity}%").format(humidity=f"{humidity:g}"))
                co2 = device.get_co2() if hasattr(device, 'get_co2') else None
                if co2 is not None:
                    parts.append(f"CO₂: {co2} ppm")
                pressure = device.get_pressure() if hasattr(device, 'get_pressure') else None
                if pressure is not None:
                    # Translators: One piece of the device help spoken with F1.
                    # The pieces are joined with ". ", so no full stop at the
                    # end.
                    parts.append(_("Air pressure: {pressure} mbar").format(pressure=f"{pressure:.1f}"))
                rain = device.get_rain() if hasattr(device, 'get_rain') else None
                if rain is not None:
                    # Translators: One piece of the device help spoken with F1.
                    # The pieces are joined with ". ", so no full stop at the
                    # end.
                    parts.append(_("Rain: {rain} mm").format(rain=rain))
                wind = device.get_wind_strength() if hasattr(device, 'get_wind_strength') else None
                if wind is not None:
                    # Translators: One piece of the device help spoken with F1.
                    # The pieces are joined with ". ", so no full stop at the
                    # end.
                    parts.append(_("Wind: {wind} km/h").format(wind=wind))

                if getattr(device, 'is_thermostat', False):
                    setpoint = device.get_setpoint_temp()
                    if setpoint is not None:
                        # Translators: One piece of the device help spoken with
                        # F1. The pieces are joined with ". ", so no full stop
                        # at the end.
                        parts.append(_("Target: {temp}°C").format(temp=f"{setpoint:.1f}"))
                    mode_text = self._get_netatmo_mode_text(device)
                    parts.append(_("Mode: {mode}").format(mode=mode_text))
                    boiler = device.get_boiler_status() if hasattr(device, 'get_boiler_status') else None
                    if boiler is not None:
                        # Translators: One piece of the device help spoken with
                        # F1. The pieces are joined with ". ", so no full stop
                        # at the end.
                        parts.append(_("Heating: {status}").format(
                            # Translators: One piece of the device help spoken
                            # with F1. The pieces are joined with ". ", so no
                            # full stop at the end.
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
                                # Translators: One piece of the device help
                                # spoken with F1. The pieces are joined with ".
                                # ", so no full stop at the end.
                                parts.append(_("Next change: {zone} "
                                               "({temp}°C) at {time}").format(
                                    zone=nc_zone, temp=f"{nc_temp:.1f}", time=change_time_str))
                            else:
                                parts.append(_("Next change: {zone} at {time}").format(
                                    zone=nc_zone, time=change_time_str))
                        except Exception as e:
                            log.debug(f"Ignored error in _show_device_context_help: {e}")
                    # Translators: One piece of the device help spoken with F1.
                    # The pieces are joined with ". ", so no full stop at the
                    # end.
                    parts.append(_("Actions: target temperature, heating "
                                   "mode, heating schedule"))
                elif getattr(device, 'is_relay', False):
                    boiler = device.get_boiler_status() if hasattr(device, 'get_boiler_status') else None
                    if boiler is not None:
                        # Translators: One piece of the device help spoken with
                        # F1. The pieces are joined with ". ", so no full stop
                        # at the end.
                        parts.append(_("Heating: {status}").format(
                            # Translators: One piece of the device help spoken
                            # with F1. The pieces are joined with ". ", so no
                            # full stop at the end.
                            status=_("active") if boiler else _("off")))

            battery = device.get_battery_percent() if hasattr(device, 'get_battery_percent') else None
            if battery is not None:
                # Translators: One piece of the device help spoken with F1. The
                # pieces are joined with ". ", so no full stop at the end.
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
                    # Translators: One piece of the device help spoken with F1.
                    # The pieces are joined with ". ", so no full stop at the
                    # end.
                    parts.append(_("Humidity: {humidity}%").format(humidity=f"{humidity:g}"))
                # Translators: One piece of the device help spoken with F1. The
                # pieces are joined with ". ", so no full stop at the end.
                parts.append(_("Sensor - display only"))
            elif device.is_water_sensor:
                alarm = device.is_water_detected()
                # Translators: One piece of the device help spoken with F1. The
                # pieces are joined with ". ", so no full stop at the end.
                parts.append(_("WATER ALARM!") if alarm else _("No water "
                                                               "detected"))
                # Translators: One piece of the device help spoken with F1. The
                # pieces are joined with ". ", so no full stop at the end.
                parts.append(_("Sensor - display only"))
            elif device.is_hub:
                # Translators: One piece of the device help spoken with F1. The
                # pieces are joined with ". ", so no full stop at the end.
                parts.append(_("Smart hub - manages sensors"))
                try:
                    if hasattr(device, 'get_subdevices'):
                        subdevices = device.get_subdevices()
                        if subdevices:
                            # Translators: One piece of the device help spoken
                            # with F1. The pieces are joined with ". ", so no
                            # full stop at the end.
                            parts.append(_("Connected sensors: {count}").format(count=len(subdevices)))
                except Exception as e:
                    log.debug(f"Ignored error in _show_device_context_help: {e}")
            elif device.is_diffuser:
                spray = device.get_diffuser_spray_mode()
                # Translators: One piece of the device help spoken with F1. The
                # pieces are joined with ". ", so no full stop at the end.
                parts.append(_("Spray mode: {mode}").format(mode=spray))
                # Translators: One piece of the device help spoken with F1. The
                # pieces are joined with ". ", so no full stop at the end.
                parts.append(_("Actions: light spray, strong spray, off"))
            elif device.is_multi_channel:
                channels = device.get_channels()
                on_count = sum(1 for ch in channels if ch.is_on)
                # Translators: One piece of the device help spoken with F1. The
                # pieces are joined with ". ", so no full stop at the end.
                parts.append(_("{on} of {total} channels switched on").format(
                    on=on_count, total=len(channels)))
                # Translators: One piece of the device help spoken with F1. The
                # pieces are joined with ". ", so no full stop at the end.
                parts.append(_("Expand channels for individual control"))
            else:
                parts.append(_("Status: {status}").format(status=_("On") if device.is_on else _("Off")))
                if device.has_power_meter:
                    power = device.get_power()
                    if power is not None:
                        # Translators: One piece of the device help spoken with
                        # F1. The pieces are joined with ". ", so no full stop
                        # at the end.
                        parts.append(_("Power: {power} W").format(power=power))
                if device.is_light:
                    # Translators: One piece of the device help spoken with F1.
                    # The pieces are joined with ". ", so no full stop at the
                    # end.
                    parts.append(_("Actions: on/off, brightness, light color"))
                elif not device.is_sensor:
                    # Translators: One piece of the device help spoken with F1.
                    # The pieces are joined with ". ", so no full stop at the
                    # end.
                    parts.append(_("Action: turn on/off"))

        # Translators: One piece of the device help spoken with F1. The pieces
        # are joined with ". ", so no full stop at the end.
        parts.append(_("Press Enter to expand or collapse"))
        ui.message(". ".join(parts))
    
