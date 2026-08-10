# -*- coding: utf-8 -*-
"""
Smart Home Control - Kontexthilfe (F1) des Geraete-Dialogs
Ausgelagert aus device_dialog.py (Modul-Aufteilung, Verhalten unverändert).
"""

import time

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

from .constants import (
    VESYNC_PURIFIER_MODE_NAMES, VESYNC_FAN_MODE_NAMES,
    VESYNC_AIR_QUALITY_NAMES,
)


class _ContextHelpMixin:
    """F1-Kontexthilfe: allgemeine Hilfe und geraetespezifische Hilfe."""

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
                "Smart Home Gerätesteuerung - Tastaturkürzel: "
                "Pfeiltasten: Navigation, "
                "Enter oder Leertaste: Aktion ausführen, "
                "F5: Aktualisieren, "
                "Strg+F: Suchen, "
                "Strg+B: Favorit hinzufügen oder entfernen, "
                "Strg+H: Verlauf anzeigen, "
                "Strg+T: Status ansagen, "
                "Strg+Tab: Tab wechseln, "
                "1-9: Zur Kategorie springen, "
                "Buchstaben: Schnellsuche, "
                "F1: Kontexthilfe, "
                "ESC: Schließen"
            )
            ui.message(help_text)
            return
        
        data = self.tree.GetItemData(item)
        
        if data is None:
            # Category
            # Translators: F1 help for a category node.
            ui.message(_("Kategorie. Enter zum Auf- oder Zuklappen. Pfeil rechts zum Öffnen."))
        elif data.get('type') == 'loading':
            # Translators: F1 help while loading.
            ui.message(_("Geräte werden geladen, bitte warten."))
        elif data.get('type') == 'error':
            # Translators: F1 help on a loading error.
            ui.message(_("Ladefehler – F5 zum Wiederholen."))
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
            if current_text.startswith(_("Temperatur:")):
                ui.message(_("Aktuelle Raumtemperatur des Sensors. Wird automatisch aktualisiert."))
            elif current_text.startswith(_("Luftfeuchtigkeit:")):
                ui.message(_("Aktuelle Luftfeuchtigkeit. Wird automatisch aktualisiert."))
            elif current_text.startswith(_("Heizung:")):
                ui.message(_("Boiler-Status: zeigt ob die Heizung gerade aktiv heizt oder aus ist."))
            elif current_text.startswith(_("Vorausheizen:")):
                ui.message(_("Vorausheizen: Die Heizung startet frühzeitig, damit die Soll-Temperatur zum geplanten Zeitpunkt bereits erreicht ist."))
            elif current_text.startswith(_("Offenes Fenster:")):
                ui.message(_("Offenes Fenster erkannt: Die Heizung wird automatisch pausiert, um Energieverschwendung zu vermeiden."))
            elif current_text.startswith(_("Nächste Planänderung:")):
                ui.message(_("Zeigt die nächste geplante Temperaturänderung aus dem Heizprogramm an: Zone, Zieltemperatur und Uhrzeit."))
            elif current_text.startswith(_("Warmwasservorrat:")):
                ui.message(_("Verfügbarer Warmwasservorrat in Prozent, optional mit Liter-Schätzung. Wird automatisch aktualisiert."))
            elif current_text.startswith(_("Niedertarif")):
                ui.message(_("Zeigt an, dass die Wärmepumpe gerade mit Niedertarif-Strom heizt."))
            elif current_text.startswith(_("Elektro-Heizstab")):
                ui.message(_("Der elektrische Zusatz-Heizstab unterstützt gerade die Wärmepumpe."))
            elif current_text.startswith(_("Heizzeiten heute:")):
                ui.message(_("Die heute programmierten Heizzeitfenster der Warmwasser-Wärmepumpe."))
            elif current_text.startswith(_("Energieverbrauch")):
                ui.message(_("Aufsummierter Energieverbrauch des Geräts in Kilowattstunden."))
            elif current_text.startswith(_("Status:")):
                ui.message(_("Gerätestatus. Wird automatisch aktualisiert."))
            elif current_text.startswith(_("Leistung:")):
                ui.message(_("Aktueller Stromverbrauch in Watt. Wird automatisch aktualisiert."))
            elif current_text.startswith(_("Spannung:")):
                ui.message(_("Aktuelle Netzspannung in Volt."))
            elif current_text.startswith(_("Stromstärke:")):
                ui.message(_("Aktuelle Stromstärke in Ampere."))
            elif current_text.startswith(_("Batterie:")):
                ui.message(_("Aktueller Batteriestand des Geräts."))
            elif current_text.startswith("CO"):
                ui.message(_("CO₂-Konzentration in der Raumluft."))
            elif current_text.startswith(_("Luftdruck:")):
                ui.message(_("Aktueller Luftdruck in Millibar."))
            elif current_text.startswith(_("Luftqualität:")):
                ui.message(_("Luftqualitäts-Einstufung des Luftreinigers von ausgezeichnet bis schlecht. Wird automatisch aktualisiert."))
            elif current_text.startswith("PM2.5:"):
                ui.message(_("Feinstaub PM2.5: Konzentration sehr feiner Partikel in Mikrogramm pro Kubikmeter."))
            elif current_text.startswith("PM1.0:"):
                ui.message(_("Feinstaub PM1.0: Konzentration ultrafeiner Partikel in Mikrogramm pro Kubikmeter."))
            elif current_text.startswith("PM10:"):
                ui.message(_("Feinstaub PM10: Konzentration grober Partikel in Mikrogramm pro Kubikmeter."))
            elif current_text.startswith("VOC:"):
                ui.message(_("Flüchtige organische Verbindungen in der Raumluft."))
            elif current_text.startswith(_("Filter-Lebensdauer:")):
                ui.message(_("Verbleibende Lebensdauer des HEPA-Filters in Prozent. Bei niedrigen Werten Filter wechseln und anschließend zurücksetzen."))
            else:
                ui.message(_("Statusinformation. Nur zur Anzeige."))
        elif data.get('type') == 'action':
            action = data.get('action')
            # Translators: The following texts are F1 context help entries for
            # action items.
            if action == 'toggle':
                ui.message(_("Schaltaktion. Enter oder Leertaste zum Ein- oder Ausschalten."))
            elif action and action.startswith('diffuser'):
                ui.message(_("Diffuser-Aktion. Enter oder Leertaste zum Ausführen."))
            elif action == 'light_luminance':
                ui.message(_("Helligkeit einstellen. Enter zum Öffnen des Eingabedialogs. Wert von 0 bis 100 Prozent."))
            elif action == 'light_temperature':
                ui.message(_("Lichtfarbe einstellen. Enter zum Auswählen zwischen Warmweiß, Tageslicht und Kaltweiß."))
            elif action == 'light_rgb':
                ui.message(_("RGB-Farbe einstellen. Enter zum Öffnen der Farbauswahl mit Voreinstellungen oder benutzerdefinierter Eingabe."))
            elif action == 'netatmo_thermostat':
                ui.message(_("Soll-Temperatur setzen. Enter zum Eingeben einer Temperatur von 5 bis 30 Grad in 0,5-Grad-Schritten mit optionaler Dauer."))
            elif action == 'netatmo_therm_mode':
                ui.message(_("Heizmodus wählen: Zeitplan, Abwesend oder Frostschutz. Enter zum Ändern."))
            elif action == 'netatmo_switch_schedule':
                ui.message(_("Heizprogramm wechseln. Enter zum Auswählen eines gespeicherten Heizplans."))
            elif action == 'netatmo_back_to_schedule':
                ui.message(_("Zurück zum automatischen Zeitplan. Enter zum Ausführen."))
            elif action == 'vesync_toggle':
                ui.message(_("VeSync-Gerät ein- oder ausschalten. Enter oder Leertaste zum Ausführen."))
            elif action == 'vesync_mode':
                ui.message(_("Betriebsmodus wählen. Bei Luftreinigern: Auto, Manuell oder Schlaf. Bei Ventilatoren: Normal, Turbo, Auto oder Schlafmodus. Enter öffnet eine Auswahlliste."))
            elif action == 'vesync_fan_speed':
                ui.message(_("Lüftergeschwindigkeit ändern. Enter öffnet eine Auswahlliste der verfügbaren Stufen. Wechsel auf eine Stufe schaltet automatisch in den Manuell-Modus."))
            elif action == 'vesync_oscillation':
                ui.message(_("Oszillation des Tower-Ventilators ein- oder ausschalten. Enter oder Leertaste zum Umschalten."))
            elif action == 'vesync_mute':
                ui.message(_("Stummschaltung des Tower-Ventilators ein- oder ausschalten. Bei aktiver Stummschaltung gibt das Gerät keine Bestätigungstöne aus. Enter oder Leertaste zum Umschalten."))
            elif action == 'vesync_display':
                ui.message(_("Display-Anzeige am Gerät ein- oder ausschalten. Enter oder Leertaste zum Umschalten."))
            elif action == 'vesync_child_lock':
                ui.message(_("Kindersicherung ein- oder ausschalten. Sperrt die Bedienung am Gerät. Enter oder Leertaste zum Umschalten."))
            elif action == 'vesync_nightlight':
                ui.message(_("Nachtlicht-Modus wählen: Aus, Gedimmt oder Ein. Enter öffnet eine Auswahlliste."))
            elif action == 'vesync_auto_preference':
                ui.message(_("Auto-Profil wählen: Standard, Effizient oder Leise. Bestimmt das Verhalten im Auto-Modus. Enter öffnet eine Auswahlliste."))
            elif action == 'vesync_reset_filter':
                ui.message(_("Filter-Lebensdauer auf 100 Prozent zurücksetzen. Nur ausführen, wenn der Filter tatsächlich gewechselt wurde. Enter öffnet einen Bestätigungs-Dialog."))
            elif action == 'cozytouch_temp':
                ui.message(_("Zieltemperatur der Warmwasser-Wärmepumpe setzen. Enter zum Eingeben einer Temperatur innerhalb des erlaubten Bereichs."))
            elif action == 'cozytouch_mode':
                ui.message(_("Betriebsmodus der Warmwasser-Wärmepumpe wählen. Enter öffnet eine Auswahlliste."))
            elif action == 'cozytouch_boost':
                ui.message(_("Boost-Modus ein- oder ausschalten: heizt das Wasser schnell auf Maximaltemperatur. Enter oder Leertaste zum Umschalten."))
            elif action == 'cozytouch_toggle':
                ui.message(_("Warmwasser-Erzeugung ein- oder ausschalten. Enter oder Leertaste zum Ausführen."))
            elif action == 'cozytouch_away':
                ui.message(_("Abwesenheits-Modus planen oder beenden: reduziert die Warmwasser-Erzeugung während längerer Abwesenheit. Enter öffnet die Zeitraum-Eingabe (Beginn und Ende); bei aktiver oder geplanter Abwesenheit schaltet Enter sie aus."))
            else:
                ui.message(_("Aktion. Enter oder Leertaste zum Ausführen."))
    
    def _show_device_context_help(self, device, channel=None):
        """
        Shows comprehensive, device-specific help via F1.
        Same level of detail for Meross and Netatmo.
        """
        parts = []

        # Translators: The following texts form the device-specific F1 help.
        if channel:
            parts.append(_("Kanal: {name}").format(name=channel.name))
            parts.append(_("Status: {status}").format(status=_("Ein") if channel.is_on else _("Aus")))
            if channel.has_power_meter:
                power = channel.get_power()
                if power is not None:
                    parts.append(_("Leistung: {power} W").format(power=power))
            parts.append(_("Aktion: Ein- oder Ausschalten"))
        elif getattr(device, 'is_cozytouch', False):
            # --- Cozytouch (Atlantic / Austria Email) ---
            type_display = device.get_type_display() if hasattr(device, 'get_type_display') else device.type
            parts.append(f"{device.name} ({type_display})")
            if getattr(device, 'is_offline', False):
                parts.append(_("Status: Offline"))
            else:
                parts.append(_("Status: {status}").format(status=_("Ein") if device.is_on else _("Aus")))
                hw = device.hot_water_percent
                if hw is not None:
                    parts.append(_("Warmwasservorrat: {percent}%").format(percent=hw))
                tt = device.target_temperature
                if tt is not None:
                    parts.append(_("Zieltemperatur: {temp}°C").format(temp=tt))
                if device.mode_name:
                    parts.append(_("Modus: {mode}").format(mode=device.mode_name))
                if device.boost_on:
                    parts.append(_("Boost aktiv"))
                if device.away_on:
                    parts.append(_("Abwesenheit aktiv"))
                parts.append(_("Aktionen: Zieltemperatur, Modus, Boost, Ein/Aus, Abwesenheit"))
        elif getattr(device, 'is_vesync', False):
            # --- VeSync (Levoit) ---
            type_display = device.get_type_display() if hasattr(device, 'get_type_display') else device.type
            parts.append(f"{device.name} ({type_display})")
            if hasattr(device, 'is_offline') and device.is_offline:
                parts.append(_("Status: Offline"))
            else:
                parts.append(_("Status: {status}").format(status=_("Ein") if device.is_on else _("Aus")))
                cls_name = type(device).__name__
                if cls_name == 'VeSyncPurifier':
                    mode_label = VESYNC_PURIFIER_MODE_NAMES.get(device.mode, device.mode or '?')
                else:
                    mode_label = VESYNC_FAN_MODE_NAMES.get(device.mode, device.mode or '?')
                parts.append(_("Modus: {mode}").format(mode=mode_label))
                if device.fan_level is not None:
                    parts.append(_("Lüftergeschwindigkeit: {level}").format(level=device.fan_level))
                if cls_name == 'VeSyncPurifier':
                    if getattr(device, 'supports_air_quality', False) and device.air_quality is not None:
                        aq_text = VESYNC_AIR_QUALITY_NAMES.get(
                            device.air_quality, str(device.air_quality)
                        )
                        parts.append(_("Luftqualität: {value}").format(value=aq_text))
                    if device.air_quality_value is not None:
                        parts.append(f"PM2.5: {device.air_quality_value} µg/m³")
                    if device.filter_life is not None:
                        parts.append(_("Filter: {percent}%").format(percent=device.filter_life))
                    parts.append(_("Aktionen: Ein/Aus, Modus, Lüftergeschwindigkeit"))
                if cls_name == 'VeSyncTowerFan':
                    if device.oscillation_on is not None:
                        parts.append(_("Oszillation: {status}").format(
                            status=_("ein") if device.oscillation_on else _("aus")))
                    if device.temperature is not None:
                        try:
                            parts.append(_("Temperatur: {temp}°C").format(
                                temp=f"{float(device.temperature):.1f}"))
                        except (TypeError, ValueError):
                            pass
                    parts.append(_("Aktionen: Ein/Aus, Modus, Lüftergeschwindigkeit, Oszillation"))
        elif getattr(device, 'is_netatmo', False):
            # --- Netatmo ---
            type_display = device.get_type_display() if hasattr(device, 'get_type_display') else device.type
            parts.append(f"{device.name} ({type_display})")

            if hasattr(device, 'is_offline') and device.is_offline:
                parts.append(_("Status: Offline"))
            else:
                temp = device.get_temperature()
                if temp is not None:
                    parts.append(_("Temperatur: {temp}°C").format(temp=f"{temp:.1f}"))
                humidity = device.get_humidity()
                if humidity is not None:
                    parts.append(_("Luftfeuchtigkeit: {humidity}%").format(humidity=f"{humidity:g}"))
                co2 = device.get_co2() if hasattr(device, 'get_co2') else None
                if co2 is not None:
                    parts.append(f"CO₂: {co2} ppm")
                pressure = device.get_pressure() if hasattr(device, 'get_pressure') else None
                if pressure is not None:
                    parts.append(_("Luftdruck: {pressure} mbar").format(pressure=f"{pressure:.1f}"))
                rain = device.get_rain() if hasattr(device, 'get_rain') else None
                if rain is not None:
                    parts.append(_("Regen: {rain} mm").format(rain=rain))
                wind = device.get_wind_strength() if hasattr(device, 'get_wind_strength') else None
                if wind is not None:
                    parts.append(_("Wind: {wind} km/h").format(wind=wind))

                if getattr(device, 'is_thermostat', False):
                    setpoint = device.get_setpoint_temp()
                    if setpoint is not None:
                        parts.append(_("Soll: {temp}°C").format(temp=f"{setpoint:.1f}"))
                    mode_text = self._get_netatmo_mode_text(device)
                    parts.append(_("Modus: {mode}").format(mode=mode_text))
                    boiler = device.get_boiler_status() if hasattr(device, 'get_boiler_status') else None
                    if boiler is not None:
                        parts.append(_("Heizung: {status}").format(
                            status=_("aktiv") if boiler else _("aus")))
                    # Show anticipation
                    anticipating = device.is_anticipating() if hasattr(device, 'is_anticipating') else None
                    if anticipating:
                        parts.append(_("Vorausheizen aktiv"))
                    open_window = device.is_open_window() if hasattr(device, 'is_open_window') else None
                    if open_window:
                        parts.append(_("Offenes Fenster erkannt"))
                    next_change = device.get_next_schedule_change() if hasattr(device, 'get_next_schedule_change') else None
                    if next_change and next_change.get('time'):
                        try:
                            change_time_str = time.strftime("%H:%M", time.localtime(next_change['time']))
                            nc_zone = next_change.get('zone_name', '')
                            nc_temp = next_change.get('temp')
                            if nc_temp is not None:
                                parts.append(_("Nächste Änderung: {zone} ({temp}°C) um {time}").format(
                                    zone=nc_zone, temp=f"{nc_temp:.1f}", time=change_time_str))
                            else:
                                parts.append(_("Nächste Änderung: {zone} um {time}").format(
                                    zone=nc_zone, time=change_time_str))
                        except Exception as e:
                            log.debug(f"Ignorierter Fehler in _show_device_context_help: {e}")
                    parts.append(_("Aktionen: Soll-Temperatur, Heizmodus, Heizprogramm"))
                elif getattr(device, 'is_relay', False):
                    boiler = device.get_boiler_status() if hasattr(device, 'get_boiler_status') else None
                    if boiler is not None:
                        parts.append(_("Heizung: {status}").format(
                            status=_("aktiv") if boiler else _("aus")))

            battery = device.get_battery_percent() if hasattr(device, 'get_battery_percent') else None
            if battery is not None:
                parts.append(_("Batterie: {percent}%").format(percent=battery))
        else:
            # --- Meross ---
            parts.append(f"{device.name} ({device.type})")

            if hasattr(device, 'is_offline') and device.is_offline:
                parts.append(_("Status: Offline"))
            elif device.is_temperature_sensor:
                temp = device.get_temperature()
                humidity = device.get_humidity()
                if temp is not None:
                    parts.append(_("Temperatur: {temp}°C").format(temp=f"{temp:.1f}"))
                if humidity is not None:
                    parts.append(_("Luftfeuchtigkeit: {humidity}%").format(humidity=f"{humidity:g}"))
                parts.append(_("Sensor - nur Anzeige"))
            elif device.is_water_sensor:
                alarm = device.is_water_detected()
                parts.append(_("WASSERALARM!") if alarm else _("Kein Wasser erkannt"))
                parts.append(_("Sensor - nur Anzeige"))
            elif device.is_hub:
                parts.append(_("Smart Hub - verwaltet Sensoren"))
                try:
                    if hasattr(device, 'get_subdevices'):
                        subdevices = device.get_subdevices()
                        if subdevices:
                            parts.append(_("Verbundene Sensoren: {count}").format(count=len(subdevices)))
                except Exception as e:
                    log.debug(f"Ignorierter Fehler in _show_device_context_help: {e}")
            elif device.is_diffuser:
                spray = device.get_diffuser_spray_mode()
                parts.append(_("Sprühmodus: {mode}").format(mode=spray))
                parts.append(_("Aktionen: Schwaches Sprühen, Starkes Sprühen, Aus"))
            elif device.is_multi_channel:
                channels = device.get_channels()
                on_count = sum(1 for ch in channels if ch.is_on)
                parts.append(_("{on} von {total} Kanälen eingeschaltet").format(
                    on=on_count, total=len(channels)))
                parts.append(_("Kanäle aufklappen für einzelne Steuerung"))
            else:
                parts.append(_("Status: {status}").format(status=_("Ein") if device.is_on else _("Aus")))
                if device.has_power_meter:
                    power = device.get_power()
                    if power is not None:
                        parts.append(_("Leistung: {power} W").format(power=power))
                if device.is_light:
                    parts.append(_("Aktionen: Ein/Aus, Helligkeit, Lichtfarbe"))
                elif not device.is_sensor:
                    parts.append(_("Aktion: Ein/Ausschalten"))

        parts.append(_("Enter zum Auf- oder Zuklappen"))
        ui.message(". ".join(parts))
    
