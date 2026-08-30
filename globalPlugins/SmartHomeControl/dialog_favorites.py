# -*- coding: utf-8 -*-
"""
Smart Home Control - favorites view of the device dialog.
Split out of device_dialog.py; behaviour unchanged.
"""

import wx
import ui
import tones
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

from .favorites import get_favorites
from .dialog_helpers import _beep
from .constants import BEEP_ON
from .platform_utils import split_by_platform


class _FavoritesTreeMixin:
    """Methods for the favorites tree (second tab of the dialog)."""

    def _add_favorite_action(self, device_item, device, is_favorite_view=False):
        """Adds a favorite action as the last child of a device node.

        Args:
            device_item: tree item of the device
            device: device object
            is_favorite_view: True -> shows 'remove from favorites'
        """
        is_fav = get_favorites().is_favorite(device.unique_id)
        # One source for label and action, so the rebuild and the later
        # relabelling (_update_favorite_row) cannot drift apart.
        fav_text, fav_action = self._favorite_action_label(is_favorite_view or is_fav)

        fav_item = self.tree.AppendItem(device_item, fav_text)
        self.tree.SetItemData(fav_item, {
            'type': 'action', 'device': device, 'action': fav_action
        })
    
    @staticmethod
    def _favorite_action_label(is_fav):
        """Label of the favorites row for the current state."""
        if is_fav:
            # Translators: Action entry in the device tree.
            return _("Remove from favorites - Enter"), 'favorite_remove'
        # Translators: Action entry in the device tree.
        return _("Add to favorites - Enter"), 'favorite_add'

    def _update_favorite_row(self, row_item):
        """Rewrites label AND action of a favorites row.

        Without this the device tree kept saying "add to favorites" long
        after the device had become one: the tree was only refreshed in the
        favorites tab, never in place. Another Enter would then have
        triggered "add" again and merely reported "already in favorites".

        Deliberately just this one row instead of rebuilding the children:
        the focus sits exactly on it, and a rebuild would destroy it.
        """
        if not row_item or not row_item.IsOk():
            return
        data = self.tree.GetItemData(row_item)
        if not data or data.get('action') not in ('favorite_add', 'favorite_remove'):
            return
        target = data.get('device')
        if target is None:
            return
        text, action = self._favorite_action_label(
            get_favorites().is_favorite(target.unique_id))
        self.tree.SetItemText(row_item, text)
        data['action'] = action
        self.tree.SetItemData(row_item, data)

    def _find_favorite_row(self, node):
        """Returns the favorites row below ``node`` (or None)."""
        if not node or not node.IsOk():
            return None
        child, cookie = self.tree.GetFirstChild(node)
        while child.IsOk():
            data = self.tree.GetItemData(child)
            if data and data.get('action') in ('favorite_add', 'favorite_remove'):
                return child
            child, cookie = self.tree.GetNextChild(node, cookie)
        return None

    def _toggle_favorite_for_selected(self):
        """Ctrl+B: toggle the favorite status of the currently selected device"""
        item = self.tree.GetSelection()
        if not item.IsOk():
            # Translators: Message when no tree entry is selected.
            ui.message(_("No device selected"))
            return
        
        # Determine the device from the selected item or its parents.
        # A channel entry carries BOTH ('device' = parent, 'channel' = the
        # outlet); the outlet is what is meant - otherwise Ctrl+B on
        # "garden: outlet 1" made the whole power strip a favorite.
        data = self.tree.GetItemData(item)
        device = None
        if data:
            device = data.get('channel') or data.get('device')

        # If on a category node: do nothing
        if device is None:
            # Try the parent node
            parent = self.tree.GetItemParent(item)
            if parent.IsOk():
                parent_data = self.tree.GetItemData(parent)
                if parent_data:
                    device = parent_data.get('channel') or parent_data.get('device')
        
        if device is None:
            # Translators: Message when the entry belongs to no device.
            ui.message(_("No device under selection"))
            return
        
        favorites = get_favorites()
        if favorites.is_favorite(device.unique_id):
            favorites.remove(device.unique_id)
            tones.beep(500, 50)
            # Translators: Confirmation after removing from the favorites.
            ui.message(_("{name}: removed from favorites").format(name=device.name))
        else:
            slot = favorites.add(device)
            _beep(BEEP_ON)
            if isinstance(slot, int):
                # Translators: Confirmation after adding to the favorites.
                # {number} = the digit that selects it in the favorites layer.
                ui.message(_("{name}: added to favorites, digit {number}").format(
                    name=device.name, number=slot))
            else:
                # No free slot 1-9 left (from the tenth favorite on)
                # Translators: Confirmation after adding to the favorites.
                ui.message(_("{name}: added to favorites").format(name=device.name))
        
        # Relabel the favorites row of the affected device. Ctrl+B can be
        # pressed on the device/channel node itself or on one of its rows,
        # so search both levels.
        row = self._find_favorite_row(item)
        if row is None:
            row = self._find_favorite_row(self.tree.GetItemParent(item))
        self._update_favorite_row(row)

        # Refresh the favorites tree view
        wx.CallAfter(self._refresh_favorites_tree)

    def _execute_favorite_action(self, item, data):
        """Executes a favorite action (add/remove)"""
        device = data.get('device')
        action = data.get('action')
        favorites = get_favorites()
        
        # Check whether we are currently in the favorites tree context (tree
        # swap active)
        is_fav_context = (self.tree is self.fav_tree)
        
        if action == 'favorite_add':
            slot = favorites.add(device)
            if slot:
                _beep(BEEP_ON)
                if isinstance(slot, int):
                    # Translators: Confirmation after adding to the favorites.
                    # {number} = the digit that selects it in the favorites
                    # layer.
                    ui.message(_("{name}: added to favorites, digit {number}").format(
                        name=device.name, number=slot))
                else:
                    # No free slot 1-9 left (from the tenth favorite on)
                    # Translators: Confirmation after adding to the favorites.
                    ui.message(_("{name}: added to favorites").format(name=device.name))
            else:
                # Translators: Hint when the device is already a favorite.
                ui.message(_("{name}: already in favorites").format(name=device.name))
        
        elif action == 'favorite_remove':
            if favorites.remove(device.unique_id):
                tones.beep(500, 50)
                # Translators: Confirmation after removing from the favorites.
                ui.message(_("{name}: removed from favorites").format(name=device.name))
            else:
                # Translators: Hint when the device was not a favorite.
                ui.message(_("{name}: was not in favorites").format(name=device.name))
        
        if is_fav_context:
            # In the favorites tree: after resetting self.tree, refresh + set
            # the focus. The caller (_on_fav_item_activated) resets self.tree,
            # hence schedule with CallAfter
            wx.CallAfter(self._refresh_and_focus_fav_tree)
        else:
            # Relabel the activated row itself, otherwise it keeps the
            # old action (see _update_favorite_row).
            self._update_favorite_row(item)
            # In the main tree: only refresh the favorites tree in the
            # background (no focus!)
            wx.CallAfter(self._refresh_favorites_tree)
    
    # ----------------------------------------------------------
    # Favorites tree view: construction and event handlers
    # ----------------------------------------------------------
    @staticmethod
    def _find_meross_favorite(all_meross, fav_uuid):
        """Finds the device OR the outlet for a favorite UUID.

        Single outlets are favorites in their own right (UUID
        ``parent_uuid_chN``). Without the channel search they would show as
        "not available" in the favorites tab although the favorites layer
        switches them fine.

        Returns:
            (device, channel) - channel is None for a whole device,
            (None, None) if nothing matches.
        """
        for dev in all_meross:
            if dev.unique_id == fav_uuid:
                return dev, None
            for ch in (dev.get_channels() or []):
                if ch.unique_id == fav_uuid:
                    return dev, ch
        return None, None

    def _add_favorite_channel(self, parent_node, device, channel):
        """Adds a single outlet as a favorites node.

        Built like in the device tree: the node carries device AND channel
        and the same shared builder creates the children, so the outlet
        behaves in the favorites tab exactly as in the device tab.
        """
        if getattr(device, 'is_offline', False):
            # Translators: Favorite outlet label when its device is offline.
            label = _("{name} - offline").format(name=channel.name)
        else:
            label = channel.name
        item = self.fav_tree.AppendItem(parent_node, label)
        self.fav_tree.SetItemData(
            item, {'type': 'device', 'device': device, 'channel': channel})
        self.fav_tree.SetItemHasChildren(item, True)  # lazy: built on expand
        self.fav_tree.Collapse(item)

    def _prefix_fav_slot(self, platform_node, fav_entry):
        """Prefixes the slot number to the entry inserted last.

        "living room lamp: on" becomes "3: living room lamp: on", so the
        favorites tab shows which digit switches the device in the
        favorites layer. The slot is the fixed number from the favorites
        file (favorites._assign_slots), not the position. Favorites without
        a slot (from the tenth on) stay unprefixed.
        """
        slot = fav_entry.get('slot')
        if not slot:
            return
        item = self.fav_tree.GetLastChild(platform_node)
        if item.IsOk():
            self.fav_tree.SetItemText(
                item, f"{slot}: {self.fav_tree.GetItemText(item)}")

    def _refresh_favorites_tree(self):
        """Completely rebuilds the favorites tree view (data only, no focus).

        Uses a trick: self.tree is temporarily redirected to self.fav_tree so
        the existing _add_single_meross_device / _add_single_netatmo_device
        methods also work in the favorites tree.

        IMPORTANT: this method sets NO focus in the favorites tree.
        The focus stays where the user is currently working.
        Only methods called directly in the favorites tab
        (e.g. _toggle_fav_tree_favorite) set the focus themselves afterwards.
        """
        if self._is_destroyed or not hasattr(self, 'fav_tree'):
            return
        
        # Freeze prevents intermediate focus events during the rebuild
        self.fav_tree.Freeze()
        
        try:
            self.fav_tree.DeleteAllItems()
            # Translators: Invisible root node of the favorites tree.
            fav_root = self.fav_tree.AddRoot(_("Device favorites"))
            
            favorites_obj = get_favorites()
            # Pick up renames from the vendor apps BEFORE the names are
            # read, otherwise unavailable devices keep their old name in
            # the favorites tab.
            favorites_obj.sync_names(self.plugin.devices or [])
            fav_meross = favorites_obj.get_by_platform("meross")
            fav_netatmo = favorites_obj.get_by_platform("netatmo")
            fav_vesync = favorites_obj.get_by_platform("vesync")
            fav_cozytouch = favorites_obj.get_by_platform("cozytouch")
            total_favs = (len(fav_meross) + len(fav_netatmo)
                          + len(fav_vesync) + len(fav_cozytouch))

            if total_favs == 0:
                # Translators: Hint in the empty favorites tab.
                hint = self.fav_tree.AppendItem(fav_root, _("No favorites yet "
                                                            "– add them in "
                                                            "the devices tab "
                                                            "with Ctrl+B"))
                self.fav_tree.SetItemData(hint, {'type': 'info'})
                # No SelectItem - the focus stays with the main tree
                return

            # All devices (unfiltered) for resolution - central mapping so
            # Cozytouch devices are not incorrectly rendered as Meross.
            all_by_platform = split_by_platform(self.plugin.devices or [])
            all_meross = all_by_platform['meross']
            all_netatmo = all_by_platform['netatmo']
            all_vesync = all_by_platform['vesync']
            all_cozytouch = all_by_platform['cozytouch']
            
            # Temporarily redirect self.tree to fav_tree (single-threaded wx,
            # safe)
            original_tree = self.tree
            self.tree = self.fav_tree
            
            try:
                # Meross favorites
                if fav_meross:
                    # Translators: Favorites group. Brand name, do not
                    # translate.
                    meross_node = self.fav_tree.AppendItem(fav_root, _("Meross "
                                                                       "favorites "
                                                                       "({count})").format(count=len(fav_meross)))
                    self.fav_tree.SetItemData(meross_node, None)
                    
                    for fav_entry in fav_meross:
                        fav_uuid = fav_entry.get('uuid', '')
                        real_device, real_channel = self._find_meross_favorite(
                            all_meross, fav_uuid)

                        if real_channel is not None:
                            self._add_favorite_channel(
                                meross_node, real_device, real_channel)
                        elif real_device:
                            self._add_single_meross_device(meross_node, real_device, is_favorite_view=True)
                        else:
                            offline_item = self.fav_tree.AppendItem(
                                meross_node, _("{name} (not available)").format(name=fav_entry.get('name', _("Unknown"))))
                            self.fav_tree.SetItemData(offline_item, {
                                'type': 'info', 'fav_uuid': fav_uuid
                            })
                        self._prefix_fav_slot(meross_node, fav_entry)
                
                # Netatmo favorites
                if fav_netatmo:
                    # Translators: Favorites group. Brand name, do not
                    # translate.
                    netatmo_node = self.fav_tree.AppendItem(fav_root, _("Netatmo "
                                                                        "favorites "
                                                                        "({count})").format(count=len(fav_netatmo)))
                    self.fav_tree.SetItemData(netatmo_node, None)

                    for fav_entry in fav_netatmo:
                        fav_uuid = fav_entry.get('uuid', '')
                        real_device = next((d for d in all_netatmo if d.unique_id == fav_uuid), None)

                        if real_device:
                            self._add_single_netatmo_device(netatmo_node, real_device, is_favorite_view=True)
                        else:
                            offline_item = self.fav_tree.AppendItem(
                                # Translators: Entry in the device tree.
                                netatmo_node, _("{name} (not available)").format(name=fav_entry.get('name', _("Unknown"))))
                            self.fav_tree.SetItemData(offline_item, {
                                'type': 'info', 'fav_uuid': fav_uuid
                            })
                        self._prefix_fav_slot(netatmo_node, fav_entry)

                # VeSync favorites
                if fav_vesync:
                    # Translators: Favorites group. Brand name, do not
                    # translate.
                    vesync_node = self.fav_tree.AppendItem(fav_root, _("VeSync "
                                                                       "favorites "
                                                                       "({count})").format(count=len(fav_vesync)))
                    self.fav_tree.SetItemData(vesync_node, None)

                    for fav_entry in fav_vesync:
                        fav_uuid = fav_entry.get('uuid', '')
                        real_device = next((d for d in all_vesync if d.unique_id == fav_uuid), None)

                        if real_device:
                            self._add_single_vesync_device(vesync_node, real_device, is_favorite_view=True)
                        else:
                            offline_item = self.fav_tree.AppendItem(
                                vesync_node, _("{name} (not available)").format(name=fav_entry.get('name', _("Unknown"))))
                            self.fav_tree.SetItemData(offline_item, {
                                'type': 'info', 'fav_uuid': fav_uuid
                            })
                        self._prefix_fav_slot(vesync_node, fav_entry)

                # Cozytouch favorites
                if fav_cozytouch:
                    # Translators: Favorites group. Brand name, do not
                    # translate.
                    cozytouch_node = self.fav_tree.AppendItem(
                        fav_root, _("Cozytouch favorites ({count})").format(count=len(fav_cozytouch)))
                    self.fav_tree.SetItemData(cozytouch_node, None)

                    for fav_entry in fav_cozytouch:
                        fav_uuid = fav_entry.get('uuid', '')
                        real_device = next((d for d in all_cozytouch if d.unique_id == fav_uuid), None)

                        if real_device:
                            self._add_single_cozytouch_device(cozytouch_node, real_device, is_favorite_view=True)
                        else:
                            offline_item = self.fav_tree.AppendItem(
                                cozytouch_node, _("{name} (not available)").format(name=fav_entry.get('name', _("Unknown"))))
                            self.fav_tree.SetItemData(offline_item, {
                                'type': 'info', 'fav_uuid': fav_uuid
                            })
                        self._prefix_fav_slot(cozytouch_node, fav_entry)
            finally:
                # Reset self.tree - ALWAYS, even on error
                self.tree = original_tree
        finally:
            self.fav_tree.Thaw()
    
    def _focus_fav_tree_item(self, device_name=None):
        """Sets the focus in the favorites tree to a matching item.

        Args:
            device_name: if given, a device with this name is searched for.
                        Otherwise the first platform node is selected.
        """
        fav_root = self.fav_tree.GetRootItem()
        if not fav_root.IsOk():
            return
        
        best_item = None
        
        # Iterate through the platform nodes
        platform_node, cookie = self.fav_tree.GetFirstChild(fav_root)
        while platform_node.IsOk():
            # If we are looking for a specific device, search in the children
            if device_name:
                child, child_cookie = self.fav_tree.GetFirstChild(platform_node)
                while child.IsOk():
                    text = self.fav_tree.GetItemText(child)
                    if device_name in text:
                        best_item = child
                        break
                    child, child_cookie = self.fav_tree.GetNextChild(platform_node, child_cookie)
                if best_item:
                    break
            
            # Remember the first platform node as a fallback
            if best_item is None:
                best_item = platform_node
            
            platform_node, cookie = self.fav_tree.GetNextChild(fav_root, cookie)
        
        if best_item and best_item.IsOk():
            self.fav_tree.SelectItem(best_item)
            self.fav_tree.EnsureVisible(best_item)
    
    def _refresh_and_focus_fav_tree(self):
        """Rebuilds the favorites tree and sets the focus afterwards.

        Only use for actions that happen directly in the favorites tab
        (e.g. Enter on 'remove from favorites') so the user does not
        lose focus.
        """
        self._refresh_favorites_tree()
        self._focus_fav_tree_item()
    
    def _on_fav_item_activated(self, event):
        """Called when Enter is pressed on an element in the favorites tree"""
        item = event.GetItem()
        if not item.IsOk():
            return
        
        data = self.fav_tree.GetItemData(item)
        
        # Category or device: expand/collapse
        if data is None or data.get('type') == 'device':
            if self.fav_tree.ItemHasChildren(item):
                if self.fav_tree.IsExpanded(item):
                    self.fav_tree.Collapse(item)
                else:
                    self.fav_tree.Expand(item)
            return
        
        # Info node: do nothing
        if data.get('type') == 'info':
            return
        
        # Action node: temporarily redirect self.tree and execute the action
        if data.get('type') == 'action':
            original_tree = self.tree
            self.tree = self.fav_tree
            try:
                self._execute_action(item, data)
            finally:
                self.tree = original_tree
    
    def _on_fav_tree_char(self, event):
        """Keyboard handler for the favorites tree view"""
        keycode = event.GetKeyCode()
        
        # ESC: close the dialog
        if keycode == wx.WXK_ESCAPE:
            self.Close()
            return
        
        # F5: refresh
        if keycode == wx.WXK_F5:
            self._on_refresh(None)
            return
        
        # Ctrl+H: show the history
        if event.ControlDown() and keycode == ord('H'):
            self._show_history_dialog()
            return
        
        # Ctrl+B: remove the favorite (in the favorites tab)
        if event.ControlDown() and keycode == ord('B'):
            self._toggle_fav_tree_favorite()
            return
        
        # Ctrl+T: announce the status
        if event.ControlDown() and keycode == ord('T'):
            self._announce_status_bar()
            return
        
        # Space: execute the action (like Enter)
        if keycode == wx.WXK_SPACE:
            item = self.fav_tree.GetSelection()
            if item.IsOk():
                evt = wx.TreeEvent(wx.wxEVT_TREE_ITEM_ACTIVATED, self.fav_tree, item)
                self._on_fav_item_activated(evt)
            return
        
        # F1: help
        if keycode == wx.WXK_F1:
            # Translators: F1 help text of the favorites view (keyboard
            # shortcuts).
            ui.message(
                _("Device favorites - keyboard shortcuts: ") +
                _("Arrow keys: navigate, ") +
                _("Enter or Space: execute action, ") +
                _("Ctrl+B: remove favorite, ") +
                _("F5: refresh, ") +
                _("Ctrl+H: show history, ") +
                _("Ctrl+Tab: switch to devices tab, ") +
                _("Ctrl+T: announce status, ") +
                _("F1: context help, ") +
                _("Esc: close dialog")
            )
            return
        
        event.Skip()
    
    def _toggle_fav_tree_favorite(self):
        """Ctrl+B in the favorites tab: remove the selected device from the favorites"""
        item = self.fav_tree.GetSelection()
        if not item.IsOk():
            # Translators: Message when no tree entry is selected.
            ui.message(_("No device selected"))
            return
        
        # The outlet first, exactly as in _toggle_favorite_for_selected: a
        # channel row carries BOTH the strip ('device') and the outlet
        # ('channel'), and the outlet is what the reader is standing on.
        #
        # Reading only 'device' here meant a favourite outlet could never
        # be removed from this tab - its key is the strip's uuid with a
        # "_ch1" suffix, so the lookup missed and answered "is not a
        # favorite" for an entry plainly listed on screen. Worse, when the
        # strip happened to be a favourite too, Ctrl+B on the outlet
        # removed the STRIP and announced the strip's name, which sounds
        # entirely plausible until the outlet is still there afterwards.
        data = self.fav_tree.GetItemData(item)
        device = None
        if data:
            device = data.get('channel') or data.get('device')

        if device is None:
            # Try the parent node
            parent = self.fav_tree.GetItemParent(item)
            if parent.IsOk():
                parent_data = self.fav_tree.GetItemData(parent)
                if parent_data:
                    device = parent_data.get('channel') or parent_data.get('device')
        
        if device is None:
            # Translators: Message when the entry belongs to no device.
            ui.message(_("No device under selection"))
            return
        
        favorites = get_favorites()
        if favorites.is_favorite(device.unique_id):
            # Determine the next device to focus (sibling item)
            next_focus_name = None
            device_item = item
            if data and data.get('type') != 'device':
                device_item = self.fav_tree.GetItemParent(item)
            parent_node = self.fav_tree.GetItemParent(device_item)
            if parent_node.IsOk():
                next_sib = self.fav_tree.GetNextSibling(device_item)
                if next_sib.IsOk():
                    next_focus_name = self.fav_tree.GetItemText(next_sib).split(" (")[0]
                else:
                    prev_sib = self.fav_tree.GetPrevSibling(device_item)
                    if prev_sib.IsOk():
                        next_focus_name = self.fav_tree.GetItemText(prev_sib).split(" (")[0]
            
            favorites.remove(device.unique_id)
            tones.beep(500, 50)
            # Translators: Confirmation after removing from the favorites.
            ui.message(_("{name}: removed from favorites").format(name=device.name))
            # Rebuild + focus in the fav tree (the user is here)
            self._refresh_favorites_tree()
            self._focus_fav_tree_item(next_focus_name)
        else:
            # Translators: Hint when the device is not a favorite.
            ui.message(_("{name}: is not a favorite").format(name=device.name))
    
    # ----------------------------------------------------------
    # History dialog
    # ----------------------------------------------------------
