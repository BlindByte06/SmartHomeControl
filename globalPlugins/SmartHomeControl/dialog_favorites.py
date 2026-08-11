# -*- coding: utf-8 -*-
"""
Smart Home Control - Favoriten-Ansicht des Geraete-Dialogs
Ausgelagert aus device_dialog.py (Modul-Aufteilung, Verhalten unverändert).
"""

import wx
import ui
import tones
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

from .favorites import get_favorites
from .dialog_helpers import _beep
from .constants import BEEP_ON
from .platform_utils import split_by_platform


class _FavoritesTreeMixin:
    """Methoden fuer den Favoriten-Baum (zweiter Tab des Dialogs)."""

    def _add_favorite_action(self, device_item, device, is_favorite_view=False):
        """Adds a favorite action as the last child of a device node.

        Args:
            device_item: tree item of the device
            device: device object
            is_favorite_view: True -> shows 'remove from favorites'
        """
        favorites = get_favorites()
        is_fav = favorites.is_favorite(device.unique_id)
        
        if is_favorite_view or is_fav:
            # Translators: Action entry in the device tree.
            fav_text = _("Aus Favoriten entfernen - Enter")
            fav_action = 'favorite_remove'
        else:
            # Translators: Action entry in the device tree.
            fav_text = _("Zu Favoriten hinzufügen - Enter")
            fav_action = 'favorite_add'
        
        fav_item = self.tree.AppendItem(device_item, fav_text)
        self.tree.SetItemData(fav_item, {
            'type': 'action', 'device': device, 'action': fav_action
        })
    
    def _toggle_favorite_for_selected(self):
        """Ctrl+B: toggle the favorite status of the currently selected device"""
        item = self.tree.GetSelection()
        if not item.IsOk():
            # Translators: Message when no tree entry is selected.
            ui.message(_("Kein Gerät ausgewählt"))
            return
        
        # Determine the device from the selected item or its parents
        data = self.tree.GetItemData(item)
        device = None
        if data:
            device = data.get('device')
        
        # If on a category node: do nothing
        if device is None:
            # Try the parent node
            parent = self.tree.GetItemParent(item)
            if parent.IsOk():
                parent_data = self.tree.GetItemData(parent)
                if parent_data:
                    device = parent_data.get('device')
        
        if device is None:
            # Translators: Message when the entry belongs to no device.
            ui.message(_("Kein Gerät unter Auswahl"))
            return
        
        favorites = get_favorites()
        if favorites.is_favorite(device.unique_id):
            favorites.remove(device.unique_id)
            tones.beep(500, 50)
            # Translators: Confirmation after removing from the favorites.
            ui.message(_("{name}: Aus Favoriten entfernt").format(name=device.name))
        else:
            slot = favorites.add(device)
            _beep(BEEP_ON)
            if isinstance(slot, int):
                # Translators: Confirmation after adding to the favorites,
                # including the digit that toggles it in the favorites layer.
                ui.message(_("{name}: Als Favorit {number} hinzugefügt").format(
                    name=device.name, number=slot))
            else:
                # Kein freier Platz 1-9 mehr (ab dem zehnten Favoriten)
                # Translators: Confirmation after adding to the favorites.
                ui.message(_("{name}: Zu Favoriten hinzugefügt").format(name=device.name))
        
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
                    # Translators: Confirmation after adding to the
                    # favorites, including the digit that toggles it in the
                    # favorites layer.
                    ui.message(_("{name}: Als Favorit {number} hinzugefügt").format(
                        name=device.name, number=slot))
                else:
                    # Kein freier Platz 1-9 mehr (ab dem zehnten Favoriten)
                    # Translators: Confirmation after adding to the favorites.
                    ui.message(_("{name}: Zu Favoriten hinzugefügt").format(name=device.name))
            else:
                # Translators: Hint when the device is already a favorite.
                ui.message(_("{name}: Bereits in Favoriten").format(name=device.name))
        
        elif action == 'favorite_remove':
            if favorites.remove(device.unique_id):
                tones.beep(500, 50)
                # Translators: Confirmation after removing from the favorites.
                ui.message(_("{name}: Aus Favoriten entfernt").format(name=device.name))
            else:
                # Translators: Hint when the device was not a favorite.
                ui.message(_("{name}: War nicht in Favoriten").format(name=device.name))
        
        if is_fav_context:
            # In the favorites tree: after resetting self.tree, refresh + set
            # the focus. The caller (_on_fav_item_activated) resets self.tree,
            # hence schedule with CallAfter
            wx.CallAfter(self._refresh_and_focus_fav_tree)
        else:
            # In the main tree: only refresh the favorites tree in the
            # background (no focus!)
            wx.CallAfter(self._refresh_favorites_tree)
    
    # ----------------------------------------------------------
    # Favorites tree view: construction and event handlers
    # ----------------------------------------------------------
    def _prefix_fav_slot(self, platform_node, fav_entry):
        """Stellt dem zuletzt eingefügten Eintrag seine Platznummer voran.

        Aus "Wohnzimmerlampe: ein" wird "3: Wohnzimmerlampe: ein" - so
        steht im Favoriten-Tab sichtbar, welche Ziffer das Gerät in der
        Favoriten-Ebene schaltet. Der Platz ist die feste Nummer aus der
        Favoriten-Datei (favorites._assign_slots), nicht die Position.
        Favoriten ohne Platz (ab dem zehnten) bleiben ohne Präfix.
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
            fav_root = self.fav_tree.AddRoot(_("Geräte-Favoriten"))
            
            favorites_obj = get_favorites()
            # Umbenennungen aus den Hersteller-Apps übernehmen, BEVOR die
            # Namen gelesen werden - sonst zeigt der Favoriten-Tab bei
            # nicht verfügbaren Geräten weiter den Namen von damals.
            favorites_obj.sync_names(self.plugin.devices or [])
            fav_meross = favorites_obj.get_by_platform("meross")
            fav_netatmo = favorites_obj.get_by_platform("netatmo")
            fav_vesync = favorites_obj.get_by_platform("vesync")
            fav_cozytouch = favorites_obj.get_by_platform("cozytouch")
            total_favs = (len(fav_meross) + len(fav_netatmo)
                          + len(fav_vesync) + len(fav_cozytouch))

            if total_favs == 0:
                # Translators: Hint in the empty favorites tab.
                hint = self.fav_tree.AppendItem(fav_root, _("Noch keine Favoriten – im Geräte-Tab mit Strg+B hinzufügen"))
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
                    meross_node = self.fav_tree.AppendItem(fav_root, _("Meross-Favoriten ({count})").format(count=len(fav_meross)))
                    self.fav_tree.SetItemData(meross_node, None)
                    
                    for fav_entry in fav_meross:
                        fav_uuid = fav_entry.get('uuid', '')
                        real_device = next((d for d in all_meross if d.unique_id == fav_uuid), None)
                        
                        if real_device:
                            self._add_single_meross_device(meross_node, real_device, is_favorite_view=True)
                        else:
                            offline_item = self.fav_tree.AppendItem(
                                meross_node, _("{name} (nicht verfügbar)").format(name=fav_entry.get('name', _("Unbekannt"))))
                            self.fav_tree.SetItemData(offline_item, {
                                'type': 'info', 'fav_uuid': fav_uuid
                            })
                        self._prefix_fav_slot(meross_node, fav_entry)
                
                # Netatmo favorites
                if fav_netatmo:
                    # Translators: Favorites group. Brand name, do not
                    # translate.
                    netatmo_node = self.fav_tree.AppendItem(fav_root, _("Netatmo-Favoriten ({count})").format(count=len(fav_netatmo)))
                    self.fav_tree.SetItemData(netatmo_node, None)

                    for fav_entry in fav_netatmo:
                        fav_uuid = fav_entry.get('uuid', '')
                        real_device = next((d for d in all_netatmo if d.unique_id == fav_uuid), None)

                        if real_device:
                            self._add_single_netatmo_device(netatmo_node, real_device, is_favorite_view=True)
                        else:
                            offline_item = self.fav_tree.AppendItem(
                                netatmo_node, _("{name} (nicht verfügbar)").format(name=fav_entry.get('name', _("Unbekannt"))))
                            self.fav_tree.SetItemData(offline_item, {
                                'type': 'info', 'fav_uuid': fav_uuid
                            })
                        self._prefix_fav_slot(netatmo_node, fav_entry)

                # VeSync favorites
                if fav_vesync:
                    # Translators: Favorites group. Brand name, do not
                    # translate.
                    vesync_node = self.fav_tree.AppendItem(fav_root, _("VeSync-Favoriten ({count})").format(count=len(fav_vesync)))
                    self.fav_tree.SetItemData(vesync_node, None)

                    for fav_entry in fav_vesync:
                        fav_uuid = fav_entry.get('uuid', '')
                        real_device = next((d for d in all_vesync if d.unique_id == fav_uuid), None)

                        if real_device:
                            self._add_single_vesync_device(vesync_node, real_device, is_favorite_view=True)
                        else:
                            offline_item = self.fav_tree.AppendItem(
                                vesync_node, _("{name} (nicht verfügbar)").format(name=fav_entry.get('name', _("Unbekannt"))))
                            self.fav_tree.SetItemData(offline_item, {
                                'type': 'info', 'fav_uuid': fav_uuid
                            })
                        self._prefix_fav_slot(vesync_node, fav_entry)

                # Cozytouch favorites
                if fav_cozytouch:
                    # Translators: Favorites group. Brand name, do not
                    # translate.
                    cozytouch_node = self.fav_tree.AppendItem(
                        fav_root, _("Cozytouch-Favoriten ({count})").format(count=len(fav_cozytouch)))
                    self.fav_tree.SetItemData(cozytouch_node, None)

                    for fav_entry in fav_cozytouch:
                        fav_uuid = fav_entry.get('uuid', '')
                        real_device = next((d for d in all_cozytouch if d.unique_id == fav_uuid), None)

                        if real_device:
                            self._add_single_cozytouch_device(cozytouch_node, real_device, is_favorite_view=True)
                        else:
                            offline_item = self.fav_tree.AppendItem(
                                cozytouch_node, _("{name} (nicht verfügbar)").format(name=fav_entry.get('name', _("Unbekannt"))))
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
                _("Geräte-Favoriten - Tastaturkürzel: ") +
                _("Pfeiltasten: Navigation, ") +
                _("Enter oder Leertaste: Aktion ausführen, ") +
                _("Strg+B: Favorit entfernen, ") +
                _("F5: Aktualisieren, ") +
                _("Strg+H: Verlauf anzeigen, ") +
                _("Strg+Tab: Zum Geräte-Tab wechseln, ") +
                _("Strg+T: Status ansagen, ") +
                _("F1: Kontexthilfe, ") +
                _("Esc: Dialog schließen")
            )
            return
        
        event.Skip()
    
    def _toggle_fav_tree_favorite(self):
        """Ctrl+B in the favorites tab: remove the selected device from the favorites"""
        item = self.fav_tree.GetSelection()
        if not item.IsOk():
            # Translators: Message when no tree entry is selected.
            ui.message(_("Kein Gerät ausgewählt"))
            return
        
        data = self.fav_tree.GetItemData(item)
        device = None
        if data:
            device = data.get('device')
        
        if device is None:
            # Try the parent node
            parent = self.fav_tree.GetItemParent(item)
            if parent.IsOk():
                parent_data = self.fav_tree.GetItemData(parent)
                if parent_data:
                    device = parent_data.get('device')
        
        if device is None:
            # Translators: Message when the entry belongs to no device.
            ui.message(_("Kein Gerät unter Auswahl"))
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
            ui.message(_("{name}: Aus Favoriten entfernt").format(name=device.name))
            # Rebuild + focus in the fav tree (the user is here)
            self._refresh_favorites_tree()
            self._focus_fav_tree_item(next_focus_name)
        else:
            # Translators: Hint when the device is not a favorite.
            ui.message(_("{name}: Ist kein Favorit").format(name=device.name))
    
    # ----------------------------------------------------------
    # History dialog
    # ----------------------------------------------------------
