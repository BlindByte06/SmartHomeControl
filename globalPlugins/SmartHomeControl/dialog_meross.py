# -*- coding: utf-8 -*-
"""Smart Home Control - Meross-specific dialog methods (mixin)."""

import wx
import ui
import threading
import tones
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

from .constants import (
    BEEP_ON, BEEP_ERROR,
)
from .history import get_history
from .dialog_helpers import _beep

log = _nvda_log


class _MerossDialogMixin:
    """Meross methods for SmartHomeControlDialog (device tree construction, color picking)."""

    def _get_color_name_from_rgb(self, rgb):
        """Returns a readable color name for RGB values

        Args:
            rgb: tuple (red, green, blue) with values 0-255

        Returns:
            str: color name or None if no mapping is possible
        """
        if rgb is None:
            return None
            
        r, g, b = rgb
        
        # Known colors with tolerance (+-15 for each channel)
        # Translators: Color names for announcing the current lamp color.
        color_definitions = [
            ((255, 0, 0), _("Red")),
            ((0, 255, 0), _("Green")),
            ((0, 0, 255), _("Blue")),
            ((255, 255, 0), _("Yellow")),
            ((255, 165, 0), _("Orange")),
            ((128, 0, 128), _("Purple")),
            ((255, 105, 180), _("Pink")),
            ((0, 255, 255), _("Cyan")),
            ((255, 255, 255), _("White")),
            ((0, 0, 0), _("Off")),
            ((255, 200, 100), _("Warm white")),
            ((200, 200, 255), _("Cool white")),
        ]
        
        tolerance = 30  # tolerance for color comparison
        
        for (cr, cg, cb), name in color_definitions:
            if (abs(r - cr) < tolerance and 
                abs(g - cg) < tolerance and 
                abs(b - cb) < tolerance):
                return name
        
        # Try to detect the color group
        # Translators: Approximate color group names for the announcement.
        if r > 200 and g < 100 and b < 100:
            return _("Reddish")
        elif r < 100 and g > 200 and b < 100:
            return _("Greenish")
        elif r < 100 and g < 100 and b > 200:
            return _("Bluish")
        elif r > 200 and g > 200 and b < 100:
            return _("Yellowish")
        elif r > 200 and g < 150 and b > 150:
            return _("Rose")
        elif r < 100 and g > 200 and b > 200:
            return _("Turquoise")
        elif r > 200 and g > 100 and b < 100:
            return _("Orange tones")
        elif r > 200 and g > 200 and b > 200:
            return _("White tones")
        
        # No recognized color
        return None

    def _add_meross_devices_to_category(self, cat, devices):
        """Inserts Meross devices into a category"""
        for device in devices:
            self._add_single_meross_device(cat, device)

    def _add_single_meross_device(self, parent_node, device, is_favorite_view=False):
        """Inserts a single Meross device as a child node.

        Args:
            parent_node: parent tree item
            device: MerossDevice object
            is_favorite_view: True when in the favorites view
        """
        # Main device node. Offline devices are marked in the label itself
        # (as with Netatmo/Cozytouch) so the offline state is audible right
        # away, multi-channel devices included.
        if getattr(device, 'is_offline', False):
            # Translators: Meross device label when the device is offline.
            label = _("{name} ({type}) - offline").format(
                name=device.name, type=device.type)
        else:
            label = f"{device.name} ({device.type})"
        device_item = self.tree.AppendItem(parent_node, label)
        self.tree.SetItemData(device_item, {'type': 'device', 'device': device})

        # All child rows (info, actions, channel sub-nodes, favorite) are built
        # by the shared builder in SmartHomeControlDialog, so the initial tree is
        # identical to what the expand/refresh and action-handler paths produce.
        self._build_meross_device_children(device_item, device, is_favorite_view=is_favorite_view)

        # Device collapsed by default
        self.tree.Collapse(device_item)

    def _show_color_picker_dialog(self, device, tree_item):
        """Shows an accessible color picker dialog for NVDA users

        Improvements for blind users:
        - preselection based on the current color
        - better descriptions of the colors
        - current RGB values in the custom dialog
        - clear success/error messages
        """
        # Get the current color for preselection
        current_rgb = device.get_rgb_color()
        
        # Basic colors with better descriptions for screen readers
        # Format: (name, RGB value, description)
        # Translators: Color names and descriptions in the color picker dialog.
        color_presets = [
            (_("Red"), (255, 0, 0), _("Strong red")),
            (_("Green"), (0, 255, 0), _("Strong green")),
            (_("Blue"), (0, 0, 255), _("Strong blue")),
            (_("Yellow"), (255, 255, 0), _("Bright yellow")),
            (_("Orange"), (255, 165, 0), _("Warm orange")),
            (_("Purple"), (128, 0, 128), _("Dark purple")),
            (_("Pink"), (255, 105, 180), _("Bright pink")),
            (_("Cyan"), (0, 255, 255), _("Bright turquoise")),
            (_("White"), (255, 255, 255), _("Neutral white")),
        ]
        
        # Check whether the current color matches a preset
        current_preset_index = -1
        is_custom_color = False
        
        if current_rgb is not None:
            # Tolerance for the color comparison (ignore small deviations)
            for i, (name, rgb, desc) in enumerate(color_presets):
                if (abs(current_rgb[0] - rgb[0]) < 10 and 
                    abs(current_rgb[1] - rgb[1]) < 10 and 
                    abs(current_rgb[2] - rgb[2]) < 10):
                    current_preset_index = i
                    break
            
            # If no preset matches, it is a custom color
            if current_preset_index == -1:
                is_custom_color = True
        
        # Build the selection list with the current color value as info
        choices = []
        for name, rgb, desc in color_presets:
            choices.append(f"{name} - {desc}")
        
        # Show the custom color with its current value
        if is_custom_color and current_rgb:
            # Translators: List entry for a custom color with the current RGB
            # value.
            choices.append(_("Custom (current: RGB {r}, {g}, {b})").format(
                r=current_rgb[0], g=current_rgb[1], b=current_rgb[2]))
        else:
            # Translators: List entry for entering custom RGB values.
            choices.append(_("Custom - enter RGB values"))

        # Help text for the dialog
        # Translators: Help text in the color picker dialog. {name} = device
        # name.
        help_text = _("Choose a color for {name}.\n").format(name=device.name)
        if current_rgb:
            # Translators: Display of the current color in the color picker
            # dialog.
            help_text += _("Current color: RGB({r}, {g}, {b})\n").format(
                r=current_rgb[0], g=current_rgb[1], b=current_rgb[2])
        # Translators: Usage hint in the color picker dialog.
        help_text += _("Navigate with arrow keys, Enter to confirm, Escape to "
                       "cancel.")

        dlg = wx.SingleChoiceDialog(
            self,
            help_text,
            # Translators: Title of the color picker dialog. {name} = device
            # name.
            _("Set color for {name}").format(name=device.name),
            choices
        )
        
        # Set the preselection
        if is_custom_color:
            # For a custom color jump directly to "Custom"
            dlg.SetSelection(len(color_presets))
        elif current_preset_index >= 0:
            dlg.SetSelection(current_preset_index)
        else:
            dlg.SetSelection(0)  # default: red
        
        if dlg.ShowModal() == wx.ID_OK:
            selection = dlg.GetSelection()
            dlg.Destroy()
            
            if selection == len(color_presets):  # custom
                self._show_custom_rgb_dialog(device, tree_item, current_rgb)
            else:
                # Set a predefined color. IMPORTANT: do NOT bind the
                # description field to the name "_" - that would shadow the
                # gettext function _() in this scope.
                color_name, (red, green, blue), _desc = color_presets[selection]
                self._apply_rgb_color(device, tree_item, red, green, blue, color_name)
        else:
            dlg.Destroy()

    def _show_custom_rgb_dialog(self, device, tree_item, current_rgb):
        """Shows a dialog for entering custom RGB values

        Args:
            device: the lamp device
            tree_item: the corresponding tree entry
            current_rgb: current RGB values as tuple (r, g, b) or None
        """
        # Prefill with current values or sensible defaults
        if current_rgb:
            default_value = f"{current_rgb[0]},{current_rgb[1]},{current_rgb[2]}"
        else:
            default_value = "255,255,255"
        
        # Detailed help text for screen readers
        # Translators: Help text in the custom RGB values dialog.
        help_text = _(
            "Enter RGB values (red, green, blue).\nFormat: R,G,B (examples: "
            "255,0,0 for red, 0,255,0 for green)\nEach value must be between "
            "0 and 255.\n0 = no color, 255 = full intensity"
        )
        if current_rgb:
            # Translators: Display of the current RGB values in the input
            # dialog.
            help_text += _("\n\nCurrent values: red={r}, green={g}, blue={b}").format(
                r=current_rgb[0], g=current_rgb[1], b=current_rgb[2])

        rgb_dlg = wx.TextEntryDialog(
            self,
            help_text,
            # Translators: Title of the custom RGB values dialog.
            _("Custom RGB color"),
            default_value
        )
        
        if rgb_dlg.ShowModal() == wx.ID_OK:
            input_value = rgb_dlg.GetValue()
            rgb_dlg.Destroy()
            
            try:
                parts = input_value.replace(' ', '').split(',')
                if len(parts) != 3:
                    tones.beep(300, 100)
                    # Translators: Error message for a wrong RGB input format.
                    ui.message(_("Invalid format. Enter three comma-separated "
                                 "values, e.g. 255,128,0"))
                    return

                red = int(parts[0].strip())
                green = int(parts[1].strip())
                blue = int(parts[2].strip())

                # Validation
                errors = []
                if not (0 <= red <= 255):
                    # Translators: Validation error for the red value.
                    errors.append(_("Red {value} invalid (0–255)").format(value=red))
                if not (0 <= green <= 255):
                    # Translators: Validation error for the green value.
                    errors.append(_("Green {value} invalid (0–255)").format(value=green))
                if not (0 <= blue <= 255):
                    # Translators: Validation error for the blue value.
                    errors.append(_("Blue {value} invalid (0–255)").format(value=blue))

                if errors:
                    tones.beep(300, 100)
                    ui.message(". ".join(errors))
                    return

                # Apply the color
                self._apply_rgb_color(device, tree_item, red, green, blue, _("Custom"))

            except ValueError:
                tones.beep(300, 100)
                # Translators: Error message on non-numeric RGB input.
                ui.message(_("Invalid input. Only comma-separated numbers, "
                             "e.g. 255,128,0"))
        else:
            rgb_dlg.Destroy()

    def _apply_rgb_color(self, device, tree_item, red, green, blue, color_name):
        """Applies an RGB color to the device

        Args:
            device: the lamp device
            tree_item: the corresponding tree entry
            red, green, blue: RGB values (0-255)
            color_name: name of the color for the announcement
        """
        try:
            # Set the color
            self.plugin.api.set_light_rgb(uuid=device.uuid, red=red, green=green, blue=blue)
            
            # Set the mode to RGB and cache the RGB values (for a correct
            # display)
            rgb_tuple = (red, green, blue)
            if hasattr(device, 'set_light_mode'):
                device.set_light_mode('rgb', rgb=rgb_tuple)
            else:
                if hasattr(device, '_light_mode'):
                    device._light_mode = 'rgb'
                if hasattr(device, '_cached_rgb'):
                    device._cached_rgb = rgb_tuple
            
            # Success feedback
            _beep(BEEP_ON)
            if color_name == _("Custom"):
                # Translators: Confirmation after setting a custom RGB color.
                ui.message(_("{name}: color set to RGB({r}, {g}, {b})").format(
                    name=device.name, r=red, g=green, b=blue))
            else:
                # Translators: Confirmation after setting a predefined color.
                ui.message(_("{name}: {color} set").format(name=device.name, color=color_name))
            get_history().log_action(
                device, 'light_rgb', f"RGB {red},{green},{blue}")
            
            # Update the tree (in a separate try block so errors here are not
            # reported as color errors)
            try:
                parent = self.tree.GetItemParent(tree_item)
                # skip_status_update=True: keep the local cache (otherwise
                # _update_status would clear the cache)
                self._update_device_item(parent, {'device': device}, skip_status_update=True)
                self.tree.Expand(parent)
            except Exception as e:
                log.debug(f"Failed to refresh the tree after a colour change: {e}")
                # No error sound - the color was set successfully!
            
        except TimeoutError:
            _beep(BEEP_ERROR)
            # Translators: Error message on timeout while setting the color.
            ui.message(_("{name}: timeout – is the lamp reachable?").format(name=device.name))
        except ConnectionError:
            _beep(BEEP_ERROR)
            # Translators: Error message when there is no connection.
            ui.message(_("{name}: no connection").format(name=device.name))
        except RuntimeError as e:
            _beep(BEEP_ERROR)
            error_msg = str(e).lower()
            if "offline" in error_msg:
                # Translators: Message when the device is offline.
                ui.message(_("{name}: offline").format(name=device.name))
            else:
                # Translators: Generic error message with detail text.
                ui.message(_("{name}: error – {error}").format(name=device.name, error=str(e)))
        except Exception as e:
            _beep(BEEP_ERROR)
            log.error(f"Failed to set the RGB colour of {device.name}: {type(e).__name__}: {e}")
            # Translators: Error message when the RGB color cannot be set.
            ui.message(_("{name}: color cannot be set").format(name=device.name))

    def _init_meross_in_background(self):
        """Initializes the Meross API in the background and loads devices afterwards.

        Called from the settings dialog when Meross has just been (re-)enabled
        or its credentials changed - without blocking the UI and without the
        former forced NVDA restart (analogous to VeSync/Cozytouch).
        """
        plugin = self.plugin
        if not plugin.begin_platform_login('meross'):
            log.info("Meross login already running - not starting a second one")
            return

        def _login_and_refresh():
            try:
                from .meross_api import MerossAPI
                api_obj = MerossAPI()
                api_obj.set_device_state_changed_callback(plugin._on_external_device_change)
                api_obj.set_throttle_callback(plugin._on_meross_throttled)

                # Decrypt the password only at login time and delete it
                # immediately.
                _pw = plugin.password
                try:
                    api_obj.login(plugin.email, _pw)
                finally:
                    _pw = None
                    del _pw

                devices = api_obj.get_devices()
                api_obj.set_wrapped_devices(devices)
                # Take over the new session only after the login worked, and
                # only then close the old one: a failed re-login leaves the
                # working session (including its MQTT push) untouched.
                old_api, plugin.api = plugin.api, api_obj
                if old_api is not None and old_api is not api_obj:
                    # Unconditionally, not just on a credential change: an
                    # abandoned session keeps its MQTT connection, polls the
                    # cloud on its own and delivers every push a second time.
                    try:
                        old_api.logout()
                    except Exception as e:
                        log.debug(f"Logout of the old Meross session failed: {e}")

                count = plugin.replace_platform_devices('meross', devices)
                log.info(f"Meross initialised late: {count} devices")

                # Update the dialog on the UI thread
                wx.CallAfter(self._refresh_after_meross_init, len(devices))
            except Exception as e:
                log.error(f"Late Meross initialisation failed: {type(e).__name__}: {e}")
                wx.CallAfter(self._offer_login_reentry, 'meross', e)
            finally:
                plugin.end_platform_login('meross')

        threading.Thread(target=_login_and_refresh, daemon=True).start()
        # Translators: Note that the Meross connection is being established in
        # the background.
        ui.message(_("Connecting to Meross..."))

    def _refresh_after_meross_init(self, count):
        """Updates the tree after Meross was connected afterwards."""
        if getattr(self, '_is_destroyed', False):
            return
        try:
            self._load_devices_internal(self.plugin.devices)
            self._refresh_favorites_tree()
            # Translators: Confirmation after loading the Meross devices
            # afterwards.
            ui.message(_("Meross: {count} device(s) loaded").format(count=count))
        except Exception as e:
            log.debug(f"Refresh after the Meross init failed: {e}")

