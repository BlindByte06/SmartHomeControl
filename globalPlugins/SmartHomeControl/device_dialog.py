# -*- coding: utf-8 -*-
"""
Smart Home Control - Device dialog
Dialog with a hierarchical tree view for managing smart home devices (Meross, Netatmo)

"""

import wx
import ui
import threading
import tones
import time
import api
import eventHandler
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
    DIFFUSER_MODE_NAMES, BEEP_ON, BEEP_OFF, BEEP_ERROR, BEEP_LOADING,
)




from .history import get_history
from .meross_devices import get_subdevice_battery

# Use the NVDA log instead of python-logging so all messages end up uniformly
# in the NVDA log (otherwise they may not show up at all depending on the NVDA
# config).
log = _nvda_log

from .dialog_helpers import _beep
from .platform_utils import split_by_platform
from .dialog_netatmo import _NetatmoDialogMixin
from .dialog_vesync import _VeSyncDialogMixin
from .dialog_meross import _MerossDialogMixin
from .dialog_cozytouch import _CozytouchDialogMixin
from .dialog_help import _ContextHelpMixin
from .dialog_favorites import _FavoritesTreeMixin
from .dialog_history import HistoryDialog  # noqa: F401 (re-export, wird unten genutzt)

class SmartHomeControlDialog(_NetatmoDialogMixin, _VeSyncDialogMixin, _MerossDialogMixin,
                         _CozytouchDialogMixin, _ContextHelpMixin, _FavoritesTreeMixin,
                         wx.Dialog):
    """Dialog for managing smart home devices with a TreeCtrl"""
    
    def __init__(self, parent, plugin):
        super(SmartHomeControlDialog, self).__init__(
            parent,
            # Translators: Title of the main window with the device overview.
            title=_("Smart Home Geräte"),
            size=(800, 600)  # larger for the status line
        )
        
        self.plugin = plugin
        self.filter_mode = "all"  # filter: all, online, offline, plugs, lights, sensors
        self.sort_mode = "name"  # sort order: name, type, status
        self.last_update_time = None
        self.search_text = ""
        self.last_announced_device = None  # for shorter follow-up announcements
        self._loading_beep_timer = None  # timer for the loading beep
        self._is_destroyed = False  # flag for safe closing while loading
        self._suppress_tree_focus_event = False  # prevents duplicate NVDA announcements on live updates
        # Blocks tree rebuilds while a child dialog (mode selection,
        # confirmation dialog, ...) is open. Otherwise a background refresh
        # running in parallel can rebuild the tree items underneath the active
        # modal dialog, which crashes NVDA's gainFocus with
        # 'BrokenCommctrl5Item' errors and briefly freezes the tree (see the
        # NVDA_LOG analysis).
        self._suppress_live_updates = False
        # Filter warning banner state (see _update_filter_warning_banner).
        self._last_filter_warning_text = None
        self._filter_warning_announced = False
        self._create_ui()
        
        # Bind EVT_CLOSE for clean teardown
        self.Bind(wx.EVT_CLOSE, self._on_dialog_close)
        self.CenterOnScreen()
    
    def _create_ui(self):
        """Builds the user interface"""
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Dialog-wide ESC handler
        self.Bind(wx.EVT_CHAR_HOOK, self._on_dialog_char)

        # ---- Filter warning banner (at the very top) ----
        # Only becomes visible and focusable when at least one VeSync purifier
        # reaches the filter warning threshold. Deliberately created AND
        # inserted BEFORE the notebook so it is both visually at the top and
        # first in the tab order. A wx.TextCtrl (not StaticText) because NVDA
        # only reads StaticText with the review cursor - a focusable field is
        # read while tabbing.
        self.filter_warning_bar = wx.TextCtrl(panel, value="", style=wx.BORDER_NONE)
        self.filter_warning_bar.SetBackgroundColour(panel.GetBackgroundColour())
        self.filter_warning_bar.SetForegroundColour(wx.RED)
        self.filter_warning_bar.Bind(wx.EVT_CHAR, lambda e: None)  # block input
        # Translators: Accessible name of the filter warning field at the top
        # of the dialog.
        self.filter_warning_bar.SetName(_("Filterwarnung"))
        self.filter_warning_bar.Hide()
        main_sizer.Add(self.filter_warning_bar, 0, wx.ALL | wx.EXPAND, 5)

        # ---- Notebook with two tabs: devices + device favorites ----
        self.notebook = wx.Notebook(panel)
        # Translators: Accessible name for the tab switching control in the
        # device dialog.
        self.notebook.SetName(_("Ansicht"))

        # ========== Tab 1: devices ==========
        devices_page = wx.Panel(self.notebook)
        devices_sizer = wx.BoxSizer(wx.VERTICAL)

        # Toolbar for filter/sort order
        toolbar_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # Filter with accessible name and help text
        # Translators: Label for the filter selection in the device overview.
        filter_label = wx.StaticText(devices_page, label=_("&Filter:"))
        toolbar_sizer.Add(filter_label, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)

        self.filter_choice = wx.Choice(devices_page, choices=[
            # Translators: Filter option: show all devices.
            _("Alle"),
            # Translators: Filter option: show only online devices.
            _("Nur Online"),
            # Translators: Filter option: show only offline devices.
            _("Nur Offline"),
            # Translators: Filter option: only smart plugs.
            _("Nur Steckdosen"),
            # Translators: Filter option: only smart lamps.
            _("Nur Lampen"),
            # Translators: Filter option: only sensors (temperature, water,
            # etc.).
            _("Nur Sensoren"),
        ])
        self.filter_choice.SetSelection(0)
        self.filter_choice.Bind(wx.EVT_CHOICE, self._on_filter_changed)
        self.filter_choice.SetName(_("Filter"))
        # Translators: Tooltip for the filter control.
        self.filter_choice.SetToolTip(_("Geräteliste nach Typ oder Status filtern"))
        toolbar_sizer.Add(self.filter_choice, 0, wx.ALL, 5)

        # Sort order with accessible name and help text
        # Translators: Label for the sort selection in the device overview.
        sort_label = wx.StaticText(devices_page, label=_("S&ortierung:"))
        toolbar_sizer.Add(sort_label, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)

        self.sort_choice = wx.Choice(devices_page, choices=[
            # Translators: Sort option: alphabetically by device name.
            _("Nach Name"),
            # Translators: Sort option: grouped by device type.
            _("Nach Typ"),
            # Translators: Sort option: by on/off or online status.
            _("Nach Status"),
        ])
        self.sort_choice.SetSelection(0)
        self.sort_choice.Bind(wx.EVT_CHOICE, self._on_sort_changed)
        self.sort_choice.SetName(_("Sortierung"))
        # Translators: Tooltip for the sort control.
        self.sort_choice.SetToolTip(_("Reihenfolge der Geräte ändern"))
        toolbar_sizer.Add(self.sort_choice, 0, wx.ALL, 5)

        devices_sizer.Add(toolbar_sizer, 0, wx.ALL | wx.EXPAND, 0)

        # Tree view with accessible name
        # Translators: Label above the device tree.
        tree_label = wx.StaticText(devices_page, label=_("&Geräte:"))
        devices_sizer.Add(tree_label, 0, wx.ALL, 5)

        self.tree = wx.TreeCtrl(
            devices_page,
            style=wx.TR_DEFAULT_STYLE | wx.TR_HIDE_ROOT | wx.TR_SINGLE
        )
        self.tree.Bind(wx.EVT_TREE_ITEM_ACTIVATED, self._on_item_activated)
        self.tree.Bind(wx.EVT_CHAR_HOOK, self._on_tree_char)
        self.tree.Bind(wx.EVT_TREE_ITEM_EXPANDING, self._on_item_expanding)
        self.tree.SetName(_("Geräteliste"))
        # Translators: Tooltip with the most important shortcuts for the device
        # list.
        self.tree.SetToolTip(_(
            "Enter/Leertaste: Aktion ausführen, Pfeiltasten: Navigation, "
            "F1: Kontexthilfe, F5: Aktualisieren, Strg+F: Suchen, "
            "1-9: Zur Kategorie springen, Buchstaben: Schnellsuche"
        ))
        devices_sizer.Add(self.tree, 1, wx.ALL | wx.EXPAND, 5)

        devices_page.SetSizer(devices_sizer)
        # Translators: Name of the first tab in the device dialog (& marks the
        # accelerator).
        self.notebook.AddPage(devices_page, _("Alle Geräte"))

        # ========== Tab 2: device favorites ==========
        fav_page = wx.Panel(self.notebook)
        fav_sizer = wx.BoxSizer(wx.VERTICAL)

        # Translators: Hint at the top of the favorites tab about keyboard
        # usage.
        fav_hint = wx.StaticText(fav_page, label=_(
            "Strg+B auf einem Gerät im Geräte-Tab: Favorit hinzufügen/entfernen"
        ))
        fav_sizer.Add(fav_hint, 0, wx.ALL, 5)

        self.fav_tree = wx.TreeCtrl(
            fav_page,
            style=wx.TR_DEFAULT_STYLE | wx.TR_HIDE_ROOT | wx.TR_SINGLE
        )
        self.fav_tree.Bind(wx.EVT_TREE_ITEM_ACTIVATED, self._on_fav_item_activated)
        self.fav_tree.Bind(wx.EVT_CHAR_HOOK, self._on_fav_tree_char)
        self.fav_tree.SetName(_("Geräte-Favoriten"))
        # Translators: Tooltip of the favorites list with keyboard shortcuts.
        self.fav_tree.SetToolTip(_(
            "Enter/Leertaste: Aktion ausführen, Pfeiltasten: Navigation, "
            "F1: Hilfe, F5: Aktualisieren, Strg+B: Favorit entfernen"
        ))
        fav_sizer.Add(self.fav_tree, 1, wx.ALL | wx.EXPAND, 5)

        fav_page.SetSizer(fav_sizer)
        # Translators: Name of the second tab in the device dialog.
        self.notebook.AddPage(fav_page, _("Geräte-Favoriten"))

        # Start-Tab gemäß Einstellung wählen (0 = Geräte, 1 = Favoriten).
        if getattr(self.plugin, 'start_tab', 'devices') == 'favorites':
            self.notebook.SetSelection(1)

        main_sizer.Add(self.notebook, 1, wx.ALL | wx.EXPAND, 5)
        
        # ---- Status line (outside the notebook) ----
        # Important for NVDA: wx.TextCtrl WITHOUT TE_READONLY (input is blocked
        # via EVT_CHAR). The TE_READONLY variant was skipped in the tab order
        # by NVDA / wx depending on the version, so blind users could not reach
        # the field at all. NVDA only reads wx.StaticText with the review
        # cursor - also unsuitable.
        status_sizer = wx.BoxSizer(wx.HORIZONTAL)
        # Translators: Label before the status display at the bottom of the
        # dialog.
        status_label = wx.StaticText(panel, label=_("&Status:"))
        status_sizer.Add(status_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)

        # Translators: Initial text of the status display.
        self.status_bar = wx.TextCtrl(
            panel,
            value=_("Lade Geräte..."),
            style=wx.BORDER_NONE,
        )
        self.status_bar.SetBackgroundColour(panel.GetBackgroundColour())
        # Block input (the cursor stays visible/readable, but no typing). NVDA
        # still announces the control as "edit" with its current content.
        self.status_bar.Bind(wx.EVT_CHAR, lambda e: None)
        # Accessible name; some screen readers use the label of the preceding
        # StaticText, but the duplication does no harm.
        self.status_bar.SetName(_("Status"))
        status_sizer.Add(self.status_bar, 1, wx.ALIGN_CENTER_VERTICAL)

        main_sizer.Add(status_sizer, 0, wx.ALL | wx.EXPAND, 5)

        # ---- Buttons ----
        button_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # Translators: Button for opening the device search (shortcut in
        # parentheses).
        search_btn = wx.Button(panel, label=_("Su&chen (Strg+F)"))
        search_btn.SetName(_("Geräte suchen"))
        search_btn.Bind(wx.EVT_BUTTON, lambda e: self._on_search())
        button_sizer.Add(search_btn, 0, wx.ALL, 5)

        # Translators: Button for reloading the device status.
        self.refresh_btn = wx.Button(panel, label=_("A&ktualisieren (F5)"))
        self.refresh_btn.SetName(_("Geräte aktualisieren"))
        self.refresh_btn.Bind(wx.EVT_BUTTON, self._on_refresh)
        button_sizer.Add(self.refresh_btn, 0, wx.ALL, 5)

        # Translators: Button opens the history of all switch actions and
        # sensor values.
        history_btn = wx.Button(panel, label=_("&Verlauf (Strg+H)"))
        history_btn.SetName(_("Verlauf öffnen"))
        history_btn.Bind(wx.EVT_BUTTON, lambda e: self._show_history_dialog())
        # Translators: Tooltip of the history button.
        history_btn.SetToolTip(_("Zeigt den Verlauf aller Schaltaktionen und Sensorwerte"))
        button_sizer.Add(history_btn, 0, wx.ALL, 5)

        # Translators: Button opens the settings dialog (Smart Home).
        settings_btn = wx.Button(panel, label=_("&Einstellungen (Alt+E)"))
        settings_btn.SetName(_("Einstellungen öffnen"))
        settings_btn.Bind(wx.EVT_BUTTON, self._on_settings)
        button_sizer.Add(settings_btn, 0, wx.ALL, 5)

        # Translators: Button for closing the device dialog.
        close_btn = wx.Button(panel, wx.ID_CLOSE, label=_("&Schließen (Esc)"))
        close_btn.SetName(_("Dialog schließen"))
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        button_sizer.Add(close_btn, 0, wx.ALL, 5)
        
        main_sizer.Add(button_sizer, 0, wx.ALL | wx.CENTER, 5)
        
        panel.SetSizer(main_sizer)
        
        # PERFORMANCE: check whether the cache is fresh for an immediate
        # display
        cache_fresh = self.plugin.is_cache_fresh() if hasattr(self.plugin, 'is_cache_fresh') else False
        
        if cache_fresh and self.plugin.devices:
            # IMMEDIATE display from the cache - no loading needed!
            log.debug(f"Dialog: Sofortige Anzeige von {len(self.plugin.devices)} gecachten Geräten")
            self._populate_tree(self.plugin.devices)
            self._focus_first_tree_item()
        else:
            # The dialog initially shows "Loading..." in the tree
            self._show_loading_state()
            # Load the devices asynchronously (does not block the UI thread!)
            wx.CallAfter(self._load_devices_async)
        
        # The ESC key for closing is handled in _on_tree_char

    def _set_status_text(self, text, announce=False):
        """Sets the text of the status line.

        The status field is a wx.TextCtrl(TE_READONLY) - focusable, readable
        by NVDA as "read-only text". However, SetValue does NOT trigger an
        automatic NVDA speech event. For important status changes (login
        phases, errors, "Refreshing...") announce=True should be set - then
        an additional ui.message() happens.

        Args:
            text: new status line text
            announce: True -> additionally announce via NVDA speech
        """
        try:
            self.status_bar.SetValue(text or "")
        except Exception:
            return
        if announce and text:
            try:
                ui.message(text)
            except Exception as e:
                log.debug(f"Ignorierter Fehler in _set_status_text: {e}")

    def _get_status_text(self):
        """Current status line text."""
        try:
            return self.status_bar.GetValue()
        except Exception:
            return ""

    def _show_loading_state(self):
        """Shows the loading state in the tree (immediately, without blocking)"""
        self.tree.DeleteAllItems()
        # Translators: Root node label of the device tree.
        root = self.tree.AddRoot(_("Geräte"))
        # Translators: Placeholder entry while the device list is loading.
        loading_item = self.tree.AppendItem(root, _("Lade Geräte..."))
        self.tree.SetItemData(loading_item, {'type': 'loading'})
        # Note: with TR_HIDE_ROOT the root's children are shown automatically,
        # Expand(root) must NOT be called!
        # Translators: Status line text while the connection is being
        # established. No separate ui.message - the ui.message("Lade
        # Geräte...") below is enough.
        self._set_status_text(_("Verbinde..."))
        # Short announcement (its own phrase - the status line shows
        # "Connecting...")
        ui.message(_("Lade Geräte..."))
        # Start the periodic loading beep (every 1 second)
        self._start_loading_beep()

    def _start_loading_beep(self):
        """Starts the periodic beep while loading"""
        self._stop_loading_beep()  # make sure no old timer is running
        self._loading_beep_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_loading_beep, self._loading_beep_timer)
        self._loading_beep_timer.Start(1000)  # every 1000ms = 1 second
        # Play the first beep immediately
        _beep(BEEP_LOADING)

    def _stop_loading_beep(self):
        """Stops the loading beep timer"""
        if self._loading_beep_timer:
            self._loading_beep_timer.Stop()
            self._loading_beep_timer = None

    def _on_loading_beep(self, event):
        """Timer event: plays the loading beep"""
        _beep(BEEP_LOADING)
    
    
    
    
    
    
    
    
    
    
    
    # ----------------------------------------------------------
    # VeSync action handlers (choice dialog style as with Netatmo)
    # ----------------------------------------------------------

    def _show_modal_safely(self, dlg):
        """Shows a child dialog modally and blocks tree rebuilds meanwhile.

        Used for all modal child dialogs whose appearance could collide with
        a running background refresh. During the modal, live updates of the
        main tree are suppressed so NVDA does not end up in the
        'BrokenCommctrl5Item' state (see the log analysis: mode selection +
        parallel refresh => tree corruption / freeze).
        """
        previous = getattr(self, '_suppress_live_updates', False)
        self._suppress_live_updates = True
        try:
            return dlg.ShowModal()
        finally:
            self._suppress_live_updates = previous











    # ----------------------------------------------------------
    # Building the VeSync device children in the tree
    # ----------------------------------------------------------







    def _append_info(self, parent_node, device, text):
        """Helper: appends an info line"""
        item = self.tree.AppendItem(parent_node, text)
        self.tree.SetItemData(item, {'type': 'info', 'device': device})
        return item

    def _append_action(self, parent_node, device, text, action):
        """Helper: appends an action"""
        item = self.tree.AppendItem(parent_node, text)
        self.tree.SetItemData(item, {
            'type': 'action', 'device': device, 'action': action
        })
        return item

    def _on_dialog_close(self, event):
        """Handler for closing the dialog - safe teardown"""
        # Set the flag to stop background threads
        self._is_destroyed = True
        # Stop the timer to prevent access to a destroyed window
        self._stop_loading_beep()
        # Clear the dialog reference in the plugin
        if hasattr(self.plugin, '_active_dialog') and self.plugin._active_dialog is self:
            self.plugin._active_dialog = None
        # Forward the event (closes the dialog)
        event.Skip()
    
    def _safe_call_after(self, func, *args, **kwargs):
        """
        Thread-safe wx.CallAfter wrapper that checks whether the dialog still exists.
        Prevents crashes when the dialog is closed during background loading.
        """
        if not self._is_destroyed:
            if args or kwargs:
                wx.CallAfter(lambda: func(*args, **kwargs) if not self._is_destroyed else None)
            else:
                wx.CallAfter(lambda: func() if not self._is_destroyed else None)
    
    def _load_devices_async(self):
        """Loads the devices in the background without freezing the UI"""
        import threading
        
        def background_load():
            try:
                # Check whether the dialog was already closed
                if self._is_destroyed:
                    return
                    
                # OPTIMIZED: use the central plugin cache for immediate
                # loading. Check whether cached devices exist and are still
                # fresh
                cache_valid = self.plugin.is_cache_fresh() if hasattr(self.plugin, 'is_cache_fresh') else (
                    self.plugin.devices and 
                    self.last_update_time and 
                    (time.time() - self.last_update_time) < 30
                )
                
                if cache_valid and self.plugin.devices:
                    # Use the cache - super fast! (NO network call)
                    log.debug(f"Dialog: Cache frisch - sofortige Anzeige von {len(self.plugin.devices)} Geräten")
                    self._safe_call_after(self._populate_tree_from_cache)
                else:
                    # Load fresh data (status update only, no full discovery)
                    self._safe_call_after(self._set_status_text, _("Lade Geräte..."))
                    
                    # Check again before the network call
                    if self._is_destroyed:
                        return
                        
                    devices = self.plugin.refresh_devices()
                    self.last_update_time = time.time()
                    self._safe_call_after(self._populate_tree, devices)
                    
            except Exception as e:
                log.error(f"Fehler beim Laden: {e}")
                if not self._is_destroyed:
                    self._safe_call_after(self._show_error_state, str(e))
        
        threading.Thread(target=background_load, daemon=True).start()
    
    def _populate_tree_from_cache(self):
        """Quickly fills the tree from the cache"""
        # Stop the loading beep (if still active)
        self._stop_loading_beep()
        # Use the timestamp from the plugin cache
        if hasattr(self.plugin, '_last_refresh_time') and self.plugin._last_refresh_time > 0:
            self.last_update_time = self.plugin._last_refresh_time
        self._populate_tree(self.plugin.devices)
        # Translators: Message when opening the dialog with cached data.
        ui.message(_("{count} Geräte aus Cache geladen").format(count=len(self.plugin.devices)))
    
    def refresh_all_device_data_live(self):
        """
        Updates ALL device data in the tree view after a background refresh.

        This method is called by the background refresh thread while the
        dialog is open. It updates all dynamic values:
        - on/off status
        - power (watts)
        - temperature/humidity
        - brightness, color, color temperature
        - Netatmo thermostat: target temperature, heating mode, device label

        If the focused element changed, NVDA is notified.
        """
        if self._is_destroyed:
            return

        # If a child dialog (mode selection, confirmation, ...) is open
        # modally, we postpone the live update. Otherwise recreating the tree
        # children would break NVDA's focus tracking on the main dialog behind
        # it (BrokenCommctrl5Item / "level 0").
        if getattr(self, '_suppress_live_updates', False):
            log.debug("Live-Update übersprungen: Kind-Dialog ist aktiv")
            return

        # Debounce: the polling scheduler can trigger this method in quick
        # succession via wx.CallAfter (e.g. the forced immediate poll on
        # opening plus a regular tick). If they arrive close together, two tree
        # walks would run back to back, which is harmless with the incremental
        # update but costs NVDA unnecessary CPU time. Hence ~0.7 s debounce.
        now = time.time()
        last = getattr(self, '_last_live_refresh_ts', 0)
        if now - last < 0.7:
            log.debug("Live-Update debounced (zu kurz nach vorherigem Refresh)")
            return
        self._last_live_refresh_ts = now

        try:
            root = self.tree.GetRootItem()
            if not root.IsOk():
                return

            # Remember the focused element for change detection
            focused_item = self.tree.GetFocusedItem()
            focused_old_text = self.tree.GetItemText(focused_item) if focused_item.IsOk() else ""

            # Walk platform nodes > categories > devices (3-level hierarchy)
            platform, p_cookie = self.tree.GetFirstChild(root)
            while platform.IsOk():
                category, c_cookie = self.tree.GetFirstChild(platform)
                while category.IsOk():
                    device_item, d_cookie = self.tree.GetFirstChild(category)
                    while device_item.IsOk():
                        data = self.tree.GetItemData(device_item)
                        if data and data.get('type') == 'device':
                            device = data.get('device')
                            if device:
                                self._update_device_tree_item_data(device_item, device)
                                # Netatmo thermostat: update the device label
                                # (mode in the name)
                                if getattr(device, 'is_netatmo', False) and getattr(device, 'is_thermostat', False):
                                    new_label = self._get_netatmo_device_label(device)
                                    current_label = self.tree.GetItemText(device_item)
                                    if new_label != current_label:
                                        self.tree.SetItemText(device_item, new_label)
                        device_item, d_cookie = self.tree.GetNextChild(category, d_cookie)
                    category, c_cookie = self.tree.GetNextChild(platform, c_cookie)
                platform, p_cookie = self.tree.GetNextChild(root, p_cookie)
            
            # Note: no additional ui.message() here! The change is already
            # reported to NVDA via SelectItem in
            # _rebuild_netatmo_thermostat_children. This eliminated a duplicate
            # announcement.
            new_focused_item = self.tree.GetFocusedItem()
            if new_focused_item.IsOk():
                focused_new_text = self.tree.GetItemText(new_focused_item)
                if focused_old_text and focused_new_text != focused_old_text:
                    log.debug(f"Live-Update: Fokussiertes Element geändert: '{focused_old_text}' -> '{focused_new_text}'")
                    
            # Update the status line - use _update_status_bar for a consistent
            # format
            self.last_update_time = time.time()
            self._update_status_bar()
            # Reconcile the filter warning banner with fresh filter_life values
            # (only announces on a new/changed warning).
            self._update_filter_warning_banner()

        except Exception as e:
            log.debug(f"Fehler beim Live-Refresh: {e}")
    
    def _update_device_tree_item_data(self, device_item, device):
        """
        Updates all dynamic data of a device in the tree view.

        Args:
            device_item: TreeItemId of the device
            device: MerossDevice object with current data
        """
        # Netatmo thermostats: incremental update analogous to Meross/VeSync.
        # Avoids the DeleteChildren/AppendItem in the standard refresh, which
        # caused flickering on the braille display and re-speaking of the
        # focused item on every 30 s tick. Structural changes (e.g. "open
        # window" appears) still trigger a one-time full rebuild.
        if getattr(device, 'is_netatmo', False) and getattr(device, 'is_thermostat', False):
            self._live_update_netatmo_thermostat_children(device_item, device)
            return

        # VeSync devices: incremental update analogous to Meross. NO
        # DeleteChildren/AppendItem is executed as long as the item structure
        # stays unchanged - only changed texts are updated via SetItemText.
        # This avoids the BrokenCommctrl5Item flickering and NVDA constantly
        # re-speaking the focused item on every 5 s fast poll.
        if getattr(device, 'is_vesync', False):
            self._live_update_vesync_children(device_item, device)
            return

        # Cozytouch devices: incremental update analogous to VeSync.
        if getattr(device, 'is_cozytouch', False):
            self._live_update_cozytouch_children(device_item, device)
            return

        # Meross hubs: their info lines (Status: Online/Offline, connected
        # sensor count, and the per-sensor lines incl. battery) are recomputed
        # by _get_device_info, not by the generic on/off logic. Refresh them in
        # place so the battery value appears once the background poll delivered
        # it - without DeleteChildren flicker.
        if getattr(device, 'is_hub', False):
            self._live_update_hub_children(device_item, device)
            return

        child, cookie = self.tree.GetFirstChild(device_item)
        while child.IsOk():
            child_data = self.tree.GetItemData(child)
            current_text = self.tree.GetItemText(child)
            
            if child_data:
                info_type = child_data.get('type')
                
                # Update the info lines
                if info_type == 'info':
                    new_text = self._get_updated_info_text(current_text, device, child_data)
                    if new_text and new_text != current_text:
                        self.tree.SetItemText(child, new_text)
                
                # Update the toggle action
                elif info_type == 'action' and child_data.get('action') == 'toggle':
                    channel = child_data.get('channel')
                    if channel:
                        is_on = channel.is_on
                        target_name = channel.name
                    else:
                        is_on = device.is_on if hasattr(device, 'is_on') else False
                        target_name = device.name
                    new_label = (_("{name} ausschalten") if is_on else _("{name} einschalten")).format(name=target_name)
                    if new_label != current_text:
                        self.tree.SetItemText(child, new_label)
                
                # Update the brightness action
                elif info_type == 'action' and child_data.get('action') == 'light_luminance':
                    if hasattr(device, 'get_luminance'):
                        luminance = device.get_luminance()
                        if luminance is not None:
                            new_text = _("Helligkeit: {value}% - Enter zum Ändern").format(value=luminance)
                            if new_text != current_text:
                                self.tree.SetItemText(child, new_text)
                
                # Netatmo thermostat: update the target temperature
                elif info_type == 'action' and child_data.get('action') == 'netatmo_thermostat':
                    setpoint = device.get_setpoint_temp() if hasattr(device, 'get_setpoint_temp') else None
                    end_time = device.get_setpoint_end_time() if hasattr(device, 'get_setpoint_end_time') else None
                    new_text = self._format_setpoint_text(setpoint, end_time)
                    if new_text != current_text:
                        self.tree.SetItemText(child, new_text)
                
                # Netatmo thermostat: update the heating mode
                elif info_type == 'action' and child_data.get('action') == 'netatmo_therm_mode':
                    mode_text = self._get_netatmo_mode_text(device)
                    new_text = _("Heizmodus: {mode} - Enter zum Ändern").format(mode=mode_text)
                    if new_text != current_text:
                        self.tree.SetItemText(child, new_text)
                
                # Recursively for sub-elements (channels, etc.)
                if child_data.get('type') == 'device' and child_data.get('channel'):
                    channel = child_data.get('channel')
                    self._update_channel_tree_item_data(child, channel)

            child, cookie = self.tree.GetNextChild(device_item, cookie)

    def _live_update_hub_children(self, device_item, device):
        """Incrementally refreshes a hub's info lines without DeleteChildren.

        A hub's children are info lines (Status, connected-sensor count, one
        line per sensor with online status, model and battery) plus the
        favorite action. We recompute the info lines via _get_device_info and
        write them in place with SetItemText, so the battery value shows up as
        soon as the background poll delivered it - without the flicker/re-speak
        that DeleteChildren would cause. If the number of info lines changed
        (e.g. a sensor was added/removed), fall back to a full rebuild.
        """
        try:
            new_lines = self._get_device_info(device)

            info_children = []
            child, cookie = self.tree.GetFirstChild(device_item)
            while child.IsOk():
                cdata = self.tree.GetItemData(child)
                if cdata and cdata.get('type') == 'info':
                    info_children.append(child)
                child, cookie = self.tree.GetNextChild(device_item, cookie)

            if len(info_children) == len(new_lines):
                # Same structure: update only the changed texts in place.
                for item, text in zip(info_children, new_lines):
                    if self.tree.GetItemText(item) != text:
                        self.tree.SetItemText(item, text)
            else:
                # Structure changed: rebuild fully (keeps status cache).
                self._update_device_item(device_item, {'device': device}, skip_status_update=True)
        except Exception as e:
            log.debug(f"Ignorierter Fehler in _live_update_hub_children: {e}")

    def _get_updated_info_text(self, current_text, device, child_data):
        """
        Determines the updated text for an info line.

        Args:
            current_text: current text of the tree item
            device: MerossDevice object
            child_data: data of the tree item

        Returns:
            updated text, or None if unchanged
        """
        # Status line
        if current_text.startswith(_("Status:")):
            # Hubs and sensors do NOT have an on/off status: a hub shows
            # "Status: Online/Offline" and a water sensor "Status: Kein Wasser".
            # The generic is_on logic below would wrongly turn those into
            # "Status: Aus". Those lines are refreshed by their own paths
            # (hub: _live_update_hub_children; sensors: rebuild on expand).
            if getattr(device, 'is_hub', False) or getattr(device, 'is_sensor', False):
                return None
            if hasattr(device, 'is_multi_channel') and device.is_multi_channel:
                channels = device.get_channels() if hasattr(device, 'get_channels') else []
                if channels:
                    channel_stati = [f"{ch.name}: {_('Ein') if ch.is_on else _('Aus')}" for ch in channels]
                    return _("Status: {status}").format(status=", ".join(channel_stati))
            else:
                is_on = device.is_on if hasattr(device, 'is_on') else False
                return _("Status: {status}").format(status=_('Ein') if is_on else _('Aus'))
        
        # Power line
        if current_text.startswith(_("Leistung:")):
            if hasattr(device, 'get_power'):
                power = device.get_power()
                if power is not None:
                    return _("Leistung: {value} W").format(value=power)

        # Consumption lines (today / last 7 days) - replace the placeholder
        # or refresh values once the background fetch filled the cache.
        if (current_text.startswith(_("Verbrauch heute:"))
                or current_text.startswith(_("Verbrauch letzte 7 Tage:"))):
            api = getattr(self.plugin, 'api', None)
            if api and hasattr(api, 'peek_daily_consumption'):
                data = api.peek_daily_consumption(device.uuid)
                if data:
                    kwh_today, kwh_week = api.summarize_daily_consumption(data)
                    if current_text.startswith(_("Verbrauch heute:")):
                        return _("Verbrauch heute: {kwh} kWh").format(
                            kwh=f"{kwh_today:.2f}".replace(".", ","))
                    return _("Verbrauch letzte 7 Tage: {kwh} kWh").format(
                        kwh=f"{kwh_week:.2f}".replace(".", ","))
            return None
        
        # Temperature line
        if current_text.startswith(_("Temperatur:")):
            if hasattr(device, 'get_temperature'):
                temp = device.get_temperature()
                if temp is not None:
                    return _("Temperatur: {value:.1f}°C").format(value=temp)
        
        # Humidity line
        if current_text.startswith(_("Luftfeuchtigkeit:")):
            if hasattr(device, 'get_humidity'):
                humidity = device.get_humidity()
                if humidity is not None:
                    return _("Luftfeuchtigkeit: {value:g}%").format(value=humidity)
        
        # Brightness line (as info, not as an action)
        if current_text.startswith(_("Helligkeit:")) and "Enter" not in current_text:
            if hasattr(device, 'get_luminance'):
                luminance = device.get_luminance()
                if luminance is not None:
                    return _("Helligkeit: {value}%").format(value=luminance)
        
        # Color temperature line
        if current_text.startswith(_("Farbtemperatur:")):
            if hasattr(device, 'get_color_temperature'):
                temp = device.get_color_temperature()
                if temp is not None:
                    return _("Farbtemperatur: {value}K").format(value=temp)
        
        # RGB color line
        if current_text.startswith(_("Farbe:")) or current_text.startswith("RGB:"):
            if hasattr(device, 'get_rgb'):
                rgb = device.get_rgb()
                if rgb:
                    return _("Farbe: R={r}, G={g}, B={b}").format(r=rgb[0], g=rgb[1], b=rgb[2])
        
        # Netatmo: heating status (boiler)
        if current_text.startswith(_("Heizung:")):
            boiler = device.get_boiler_status() if hasattr(device, 'get_boiler_status') else None
            if boiler is not None:
                return _("Heizung: aktiv") if boiler else _("Heizung: aus")
        
        # Netatmo: pre-heating (anticipation)
        if current_text.startswith(_("Vorausheizen:")):
            anticipating = device.is_anticipating() if hasattr(device, 'is_anticipating') else None
            if anticipating:
                return _("Vorausheizen: aktiv")
            return None  # remove the item when no longer active
        
        # Netatmo: open window
        if current_text.startswith(_("Offenes Fenster:")):
            open_window = device.is_open_window() if hasattr(device, 'is_open_window') else None
            if open_window:
                return _("Offenes Fenster: erkannt (Heizung pausiert)")
            return None  # remove the item when no longer active
        
        # Netatmo: next schedule change
        if current_text.startswith(_("Nächste Planänderung:")):
            next_change = device.get_next_schedule_change() if hasattr(device, 'get_next_schedule_change') else None
            if next_change and next_change.get('time'):
                try:
                    change_time_str = time.strftime("%H:%M", time.localtime(next_change['time']))
                    nc_zone = next_change.get('zone_name', '')
                    nc_temp = next_change.get('temp')
                    if nc_temp is not None:
                        return _("Nächste Planänderung: {zone} ({temp:.1f}°C) um {time}").format(zone=nc_zone, temp=nc_temp, time=change_time_str)
                    else:
                        return _("Nächste Planänderung: {zone} um {time}").format(zone=nc_zone, time=change_time_str)
                except Exception as e:
                    log.debug(f"Ignorierter Fehler in _get_updated_info_text: {e}")
            return None
        
        # Netatmo: battery
        if current_text.startswith(_("Batterie:")):
            battery = device.get_battery_percent() if hasattr(device, 'get_battery_percent') else None
            if battery is not None:
                return _("Batterie: {value}%").format(value=battery)
        
        return None
    
    def _update_channel_tree_item_data(self, channel_item, channel):
        """
        Updates the data of a channel entry.

        Args:
            channel_item: TreeItemId of the channel
            channel: MerossChannel object
        """
        child, cookie = self.tree.GetFirstChild(channel_item)
        while child.IsOk():
            child_data = self.tree.GetItemData(child)
            current_text = self.tree.GetItemText(child)
            
            if child_data and child_data.get('type') == 'info':
                if current_text.startswith(_("Status:")):
                    is_on = channel.is_on if hasattr(channel, 'is_on') else False
                    new_text = _("Status: {status}").format(status=_('Ein') if is_on else _('Aus'))
                    if new_text != current_text:
                        self.tree.SetItemText(child, new_text)
            
            if child_data and child_data.get('type') == 'action' and child_data.get('action') == 'toggle':
                is_on = channel.is_on if hasattr(channel, 'is_on') else False
                new_label = (_("{name} ausschalten") if is_on else _("{name} einschalten")).format(name=channel.name)
                if new_label != current_text:
                    self.tree.SetItemText(child, new_label)
            
            child, cookie = self.tree.GetNextChild(channel_item, cookie)

    def update_device_status_live(self, device_uuid, new_state, channel_name=None):
        """
        Updates the status of a device in the tree view LIVE (on external changes).

        This method is called when a device is switched via Alexa, the Meross
        app or other external sources. It updates the status directly in the
        open dialog so NVDA notices the change immediately.

        Args:
            device_uuid: UUID of the changed device
            new_state: True = switched on, False = switched off
            channel_name: optional - name of the channel for multi-channel devices
        """
        # Safety check: dialog still active?
        if self._is_destroyed:
            return
            
        try:
            # Find the device in the tree and update the status text
            root = self.tree.GetRootItem()
            if not root.IsOk():
                return
            
            # Walk platform nodes > categories > devices (3-level hierarchy)
            platform, p_cookie = self.tree.GetFirstChild(root)
            while platform.IsOk():
                category, c_cookie = self.tree.GetFirstChild(platform)
                while category.IsOk():
                    device_item, d_cookie = self.tree.GetFirstChild(category)
                    while device_item.IsOk():
                        data = self.tree.GetItemData(device_item)
                        if data and data.get('type') == 'device':
                            device = data.get('device')
                            if device and device.uuid == device_uuid:
                                # Device found! For multi-channel: also update
                                # the channel status in the main device
                                if hasattr(device, 'is_multi_channel') and device.is_multi_channel:
                                    self._update_multi_channel_status(device_item, device, channel_name, new_state)
                                else:
                                    # Normal device: update the status child
                                    # node
                                    self._update_status_child(device_item, new_state)
                                log.debug(f"Live-Update: {device.name} -> {'Ein' if new_state else 'Aus'}")
                                return
                        device_item, d_cookie = self.tree.GetNextChild(category, d_cookie)
                    category, c_cookie = self.tree.GetNextChild(platform, c_cookie)
                platform, p_cookie = self.tree.GetNextChild(root, p_cookie)
                
        except Exception as e:
            log.debug(f"Fehler beim Live-Update: {e}")
    
    def _update_multi_channel_status(self, device_item, device, channel_name, new_state):
        """
        Updates the status of a multi-channel device and its channels.

        Args:
            device_item: TreeItemId of the main device
            device: MerossDevice object
            channel_name: name of the changed channel (if known)
            new_state: True = on, False = off
        """
        # Update the status text in the main device
        child, cookie = self.tree.GetFirstChild(device_item)
        while child.IsOk():
            child_data = self.tree.GetItemData(child)
            
            # Update the status line on the main device
            if child_data and child_data.get('type') == 'info':
                current_text = self.tree.GetItemText(child)
                if current_text.startswith(_("Status:")):
                    # Fetch the current status of all channels
                    channels = device.get_channels() if hasattr(device, 'get_channels') else []
                    if channels:
                        channel_stati = [f"{ch.name}: {_('Ein') if ch.is_on else _('Aus')}" for ch in channels]
                        new_status = _("Status: {status}").format(status=", ".join(channel_stati))
                        self.tree.SetItemText(child, new_status)
            
            # Update the channel sub-entry
            if child_data and child_data.get('type') == 'device':
                ch = child_data.get('channel')
                if ch and channel_name and ch.name == channel_name:
                    # Channel found - update its sub-entries
                    self._update_channel_children(child, ch, new_state)
            
            child, cookie = self.tree.GetNextChild(device_item, cookie)
    
    def _update_channel_children(self, channel_item, channel, new_state):
        """
        Updates the child nodes of a channel entry.

        Args:
            channel_item: TreeItemId of the channel
            channel: MerossChannel object
            new_state: True = on, False = off
        """
        child, cookie = self.tree.GetFirstChild(channel_item)
        while child.IsOk():
            child_data = self.tree.GetItemData(child)
            current_text = self.tree.GetItemText(child)
            
            # Update the status entry
            if child_data and child_data.get('type') == 'info' and current_text.startswith(_("Status:")):
                new_text = _("Status: {status}").format(status=_('Ein') if new_state else _('Aus'))
                self.tree.SetItemText(child, new_text)
            
            # Update the toggle action
            if child_data and child_data.get('type') == 'action' and child_data.get('action') == 'toggle':
                label = (_("{name} ausschalten") if new_state else _("{name} einschalten")).format(name=channel.name)
                self.tree.SetItemText(child, label)
            
            child, cookie = self.tree.GetNextChild(channel_item, cookie)
    
    def _update_status_child(self, device_item, new_state):
        """
        Updates the status child node of a device entry in the tree view.

        Args:
            device_item: TreeItemId of the device
            new_state: True = on, False = off
        """
        # Look for the status child node
        child, cookie = self.tree.GetFirstChild(device_item)
        while child.IsOk():
            child_data = self.tree.GetItemData(child)
            if child_data and child_data.get('type') == 'info':
                # Check whether it is a status entry
                current_text = self.tree.GetItemText(child)
                if current_text.startswith(_("Status:")):
                    # Update the status text
                    new_text = _("Status: {status}").format(status=_('Ein') if new_state else _('Aus'))
                    self.tree.SetItemText(child, new_text)
                    
                    # Also update the device object in the plugin cache
                    device_data = self.tree.GetItemData(device_item)
                    if device_data and device_data.get('device'):
                        device_data['device']._is_on = new_state

                    # Adjust the toggle label
                    self._update_toggle_action_text(device_item, new_state)
                    
                    return
            child, cookie = self.tree.GetNextChild(device_item, cookie)

    def _update_toggle_action_text(self, device_item, new_state):
        """Updates the label of the toggle entry"""
        child, cookie = self.tree.GetFirstChild(device_item)
        while child.IsOk():
            child_data = self.tree.GetItemData(child)
            if child_data and child_data.get('type') == 'action' and child_data.get('action') == 'toggle':
                device = child_data.get('device')
                channel = child_data.get('channel')
                target_name = channel.name if channel else (device.name if device else _("Gerät"))
                label = (_("{name} ausschalten") if new_state else _("{name} einschalten")).format(name=target_name)
                self.tree.SetItemText(child, label)
                if channel:
                    channel._is_on = new_state
                return
            child, cookie = self.tree.GetNextChild(device_item, cookie)
    
    @staticmethod
    def _format_network_error(error_msg):
        """Converts technical error messages into user-friendly texts"""
        msg_lower = error_msg.lower()
        
        # DNS resolution / network outage
        if any(s in msg_lower for s in [
            'failed to resolve', 'getaddrinfo failed', 'name resolution',
            'nodename nor servname', 'dns',
        ]):
            return _("Keine Internetverbindung - DNS-Auflösung fehlgeschlagen")
        
        # Connection refused / unreachable
        if any(s in msg_lower for s in [
            'connection refused', 'winerror 10061',
            'no route to host', 'nicht erreichbar', 'winerror 10065',
            'network is unreachable', 'winerror 10051',
        ]):
            return _("Keine Internetverbindung - Server nicht erreichbar")
        
        # Connection aborted / reset
        if any(s in msg_lower for s in [
            'connection reset', 'connection aborted', 'broken pipe',
            'winerror 10054', 'winerror 10053',
        ]):
            return _("Verbindung unterbrochen")
        
        # Max retries / general connection problems
        if 'max retries exceeded' in msg_lower:
            return _("Keine Internetverbindung - Server antwortet nicht")
        
        # Timeout
        if 'timeout' in msg_lower or 'zeitüberschreitung' in msg_lower:
            return _("Zeitüberschreitung - bitte erneut versuchen")
        
        # Event loop / internal errors
        if 'event loop' in msg_lower:
            return _("Interner Verbindungsfehler - bitte erneut versuchen")
        
        # Not logged in
        if 'nicht angemeldet' in msg_lower or 'not logged' in msg_lower:
            return _("Nicht angemeldet - bitte Einstellungen prüfen")
        
        # Fallback: shorten the original message
        return _("Fehler: {msg}").format(msg=error_msg[:50])
    
    def _show_error_state(self, error_msg):
        """Shows the error state in the tree"""
        # Stop the loading beep on error
        self._stop_loading_beep()
        self.tree.DeleteAllItems()
        root = self.tree.AddRoot(_("Geräte"))
        
        # Phrase network errors in a user-friendly way
        user_msg = self._format_network_error(error_msg)
        
        error_item = self.tree.AppendItem(root, user_msg)
        self.tree.SetItemData(error_item, {'type': 'error'})
        # Translators: Hint in the device tree how the user can retry after an
        # error.
        retry_item = self.tree.AppendItem(root, _("F5 drücken zum Wiederholen"))
        self.tree.SetItemData(retry_item, {'type': 'info'})
        # With TR_HIDE_ROOT children become visible automatically
        self._set_status_text(user_msg[:40])
        _beep(BEEP_ERROR)
        ui.message(user_msg)
    
    def _populate_tree(self, devices):
        """Fills the tree with devices (fast, without API calls)"""
        # Stop the loading beep - loading finished!
        self._stop_loading_beep()
        self._load_devices_internal(devices)
        self._focus_first_tree_item()
        
        # IMPROVEMENT 6: update the dialog title with the device count
        self._update_dialog_title()
        
        online = sum(1 for d in devices if not (hasattr(d, 'is_offline') and d.is_offline))
        # Translators: Message after loading the device list.
        ui.message(_("{count} Geräte geladen, {online} online").format(count=len(devices), online=online))

        # Set the filter warning banner at the very top (without immediate
        # speech - it would run before the dialog is shown and be swallowed by
        # the focus event). On the very first fill the announcement happens
        # delayed, once the dialog is open and the focus announcement has
        # faded.
        self._update_filter_warning_banner(speak=False)
        if self._last_filter_warning_text and not self._filter_warning_announced:
            try:
                wx.CallLater(800, self._announce_filter_warning_on_open)
            except Exception as e:
                log.debug(f"Ignorierter Fehler in _populate_tree: {e}")

        # Load the favorites tree delayed (does not block the display).
        # Die Sensorerfassung hängt NICHT mehr hier: sie lief früher bei jedem
        # Aufbau der Liste, und die 15-Minuten-Sperre lag auf der Dialog-
        # Instanz, die bei jedem Öffnen neu entsteht. Fünfmal Menü öffnen
        # ergab fünf identische Schnappschüsse pro Gerät. Erfasst wird jetzt
        # im Hintergrund-Scheduler (_log_sensor_measurements), gefiltert auf
        # echte Änderungen - erst dadurch ist es ein Verlauf und nicht ein
        # Protokoll der Menüöffnungen.
        wx.CallAfter(self._refresh_favorites_tree)

    def _announce_device_action(self, device_name, new_state, is_first_mention=False):
        """
        Optimized speech output for device actions - ALWAYS with a colon

        Args:
            device_name: name of the device
            new_state: True = on, False = off
            is_first_mention: no longer used (kept for compatibility)
        """
        # Phonetic replacements
        device_name = device_name.replace("WLAN", "W-LAN")
        
        # ALWAYS a uniform announcement with a colon
        # Translators: Announcement of an external switch state change.
        ui.message(_("{name}: {status}").format(
            name=device_name, status=_("Ein") if new_state else _("Aus")))
    
    def _announce_status_bar(self):
        """Announces the current status of the status line (Ctrl+T)

        In addition to the absolute time, the RELATIVE age of the last
        refresh is announced ("X seconds ago"). The absolute time alone makes
        the refresh rate hard to hear - with the relative value the user can
        directly verify that the open dialog refreshes at least every ~15 s.
        """
        # First update the status line with the current timestamp
        self._update_status_bar()
        # Then fetch the text and announce it
        status_text = self._get_status_text()
        if status_text:
            # Append the relative age of the last refresh.
            update_time = self.last_update_time
            if (not update_time and hasattr(self.plugin, '_last_refresh_time')
                    and self.plugin._last_refresh_time > 0):
                update_time = self.plugin._last_refresh_time
            if update_time:
                age = max(0, int(time.time() - update_time))
                # Translators: Relative age of the last refresh (Ctrl+T). {sec}
                # = seconds since the last update.
                status_text += _(" – zuletzt aktualisiert vor {sec} Sekunden").format(sec=age)
            ui.message(status_text)
            _beep(BEEP_OFF)  # short confirmation tone (600 Hz)
        else:
            # Translators: Announced when the status line is empty.
            ui.message(_("Kein Status verfügbar"))

    def _update_filter_warning_banner(self, announce=False, speak=True):
        """Shows/hides the filter warning banner at the top of the dialog.

        Visible (and focusable) as soon as at least one VeSync purifier
        reaches the filter warning threshold set in the settings. The warning
        is announced when it newly appears or changes - or when
        ``announce=True``.

        Args:
            announce: forces the speech output (in addition to the change detection).
            speak: if False, the banner is only updated visually and the text
                remembered, but NOTHING is spoken. Used on the first fill
                because the announcement would otherwise run during dialog
                construction (before showing) and be swallowed by the focus
                event - the announcement then happens delayed via
                _announce_filter_warning_on_open().
        """
        if self._is_destroyed:
            return
        try:
            warnings = (self.plugin.get_vesync_filter_warnings()
                        if hasattr(self.plugin, 'get_vesync_filter_warnings') else [])
        except Exception as e:
            log.debug(f"Filter-Warnungen konnten nicht ermittelt werden: {e}")
            return
        try:
            if warnings:
                parts = [_("{name} {pct}%").format(name=n, pct=p) for n, p in warnings]
                # Translators: Warning banner at the top of the dialog:
                # affected purifiers and remaining filter life. Prompt to
                # replace the filter.
                text = _("Filter wechseln: {list}").format(list=", ".join(parts))
                changed = (text != self._last_filter_warning_text)
                self.filter_warning_bar.SetValue(text)
                if not self.filter_warning_bar.IsShown():
                    self.filter_warning_bar.Show()
                    self.Layout()
                if speak and (announce or changed):
                    ui.message(text)
                    self._filter_warning_announced = True
                self._last_filter_warning_text = text
            else:
                if self.filter_warning_bar.IsShown():
                    self.filter_warning_bar.Hide()
                    self.Layout()
                self._last_filter_warning_text = None
        except RuntimeError:
            # The dialog was destroyed in the meantime
            pass

    def _announce_filter_warning_on_open(self):
        """Announces the filter warning delayed after the dialog opens.

        Called via wx.CallLater AFTER the dialog was shown and the focus
        announcement of the first tree element has faded. A ui.message issued
        directly while filling would otherwise be overwritten by the focus
        event of the TreeCtrl and would not be audible.
        """
        if self._is_destroyed or self._filter_warning_announced:
            return
        if self._last_filter_warning_text:
            try:
                ui.message(self._last_filter_warning_text)
                self._filter_warning_announced = True
            except Exception as e:
                log.debug(f"Ignorierter Fehler in _announce_filter_warning_on_open: {e}")
    
    def _on_dialog_char(self, event):
        """Handles keyboard events at dialog level (ESC to close, Ctrl+T for status, Ctrl+Tab for tab switching)"""
        keycode = event.GetKeyCode()
        
        # ESC: close the dialog
        if keycode == wx.WXK_ESCAPE:
            self.Close()
            return
        
        # Ctrl+Tab / Ctrl+Shift+Tab: switch tabs
        if keycode == wx.WXK_TAB and event.ControlDown():
            current = self.notebook.GetSelection()
            total = self.notebook.GetPageCount()
            if event.ShiftDown():
                new_page = (current - 1) % total
            else:
                new_page = (current + 1) % total
            self.notebook.SetSelection(new_page)
            page_name = self.notebook.GetPageText(new_page)
            # Set the focus to the tree in the new tab
            if new_page == 0:
                self.tree.SetFocus()
            elif new_page == 1:
                self.fav_tree.SetFocus()
            ui.message(page_name)
            return
        
        # Ctrl+T: announce the status (also outside the tree)
        if event.ControlDown() and keycode == ord('T'):
            self._announce_status_bar()
            return
        
        # Pass on other keys
        event.Skip()
    
    def _on_tree_char(self, event):
        """Handles keyboard events in the tree"""
        keycode = event.GetKeyCode()
        
        # ESC: close the dialog (also handled by _on_dialog_char)
        if keycode == wx.WXK_ESCAPE:
            self.Close()
            return
        
        # F5: refresh
        if keycode == wx.WXK_F5:
            self._on_refresh(None)
            return
        
        # Ctrl+F: search
        if event.ControlDown() and keycode == ord('F'):
            self._on_search()
            return
        
        # Ctrl+H: show the history
        if event.ControlDown() and keycode == ord('H'):
            self._show_history_dialog()
            return
        
        # Ctrl+B: add/remove the current device to/from the favorites
        if event.ControlDown() and keycode == ord('B'):
            self._toggle_favorite_for_selected()
            return
        
        # Ctrl+T: announce the status
        if event.ControlDown() and keycode == ord('T'):
            self._announce_status_bar()
            return
        
        # Number keys 1-9: jump to a category
        if ord('1') <= keycode <= ord('9'):
            category_index = keycode - ord('1')  # 0-based
            self._jump_to_category(category_index)
            return
        
        # IMPROVEMENT 3: Space for a quick toggle (like Enter)
        if keycode == wx.WXK_SPACE:
            item = self.tree.GetSelection()
            if item.IsOk():
                # Simulate an item activation
                evt = wx.TreeEvent(wx.wxEVT_TREE_ITEM_ACTIVATED, self.tree, item)
                self._on_item_activated(evt)
            return
        
        # IMPROVEMENT 4: quick letter navigation (a-z)
        if ord('A') <= keycode <= ord('Z') or ord('a') <= keycode <= ord('z'):
            if not event.ControlDown() and not event.AltDown():
                char = chr(keycode).lower()
                self._quick_navigate_by_letter(char)
                return
        
        # F1: context-sensitive help
        if keycode == wx.WXK_F1:
            self._show_context_help()
            return
        
        event.Skip()
    
    def _on_filter_changed(self, event):
        """The filter was changed"""
        selection = self.filter_choice.GetSelection()
        filter_map = {0: "all", 1: "online", 2: "offline", 3: "plugs", 4: "lights", 5: "sensors"}
        self.filter_mode = filter_map.get(selection, "all")
        
        # Translators: Short loading announcement on a filter/sort change.
        ui.message(_("Lade..."))
        self._load_devices(force_refresh=False)
        
        # Set the focus to the first tree entry (BEFORE the announcement)
        self._focus_first_tree_item()
        
        # Announcement and tone AFTER focusing
        tones.beep(600, 30)
        # Translators: Announcement of the selected filter.
        ui.message(_("Filter: {filter}").format(filter=self.filter_choice.GetStringSelection()))
    
    def _on_sort_changed(self, event):
        """The sort order was changed"""
        selection = self.sort_choice.GetSelection()
        sort_map = {0: "name", 1: "type", 2: "status"}
        self.sort_mode = sort_map.get(selection, "name")
        
        # Translators: Short loading announcement on a filter/sort change.
        ui.message(_("Lade..."))
        self._load_devices(force_refresh=False)
        
        # Set the focus to the first tree entry (BEFORE the announcement)
        self._focus_first_tree_item()
        
        # Announcement and tone AFTER focusing
        tones.beep(600, 30)
        # Translators: Announcement of the selected sort order.
        ui.message(_("Sortiert: {sort}").format(sort=self.sort_choice.GetStringSelection()))
    
    def _focus_first_tree_item(self):
        """Sets the focus to the first entry in the tree"""
        root = self.tree.GetRootItem()
        if root.IsOk():
            first_item = self.tree.GetFirstChild(root)[0]
            if first_item.IsOk():
                self.tree.SelectItem(first_item)
                self.tree.SetFocus()
    
    def _on_search(self):
        """Opens the search dialog"""
        # Translators: Prompt and title of the device search dialog.
        dlg = wx.TextEntryDialog(self, _("Gerätename eingeben:"), _("Gerät suchen"), self.search_text)
        if dlg.ShowModal() == wx.ID_OK:
            self.search_text = dlg.GetValue().strip().lower()
            if self.search_text:
                self._find_device(self.search_text)
            else:
                # Translators: Message when the search is cancelled.
                ui.message(_("Suche abgebrochen"))
        dlg.Destroy()
    
    def _find_device(self, search_text):
        """Finds and selects a device"""
        root = self.tree.GetRootItem()
        item = self.tree.GetFirstChild(root)[0]
        
        found = False
        while item.IsOk():
            text = self.tree.GetItemText(item).lower()
            if search_text in text:
                self.tree.SelectItem(item)
                self.tree.EnsureVisible(item)
                # Expand if it has children
                if self.tree.ItemHasChildren(item):
                    self.tree.Expand(item)
                # Set the focus to the tree
                self.tree.SetFocus()
                # Translators: Announcement of a search hit.
                ui.message(_("Gefunden: {item}").format(item=self.tree.GetItemText(item)))
                found = True
                break
            
            # Search recursively in the children
            if self.tree.ItemHasChildren(item):
                child = self.tree.GetFirstChild(item)[0]
                while child.IsOk():
                    child_text = self.tree.GetItemText(child).lower()
                    if search_text in child_text:
                        self.tree.SelectItem(child)
                        self.tree.EnsureVisible(child)
                        self.tree.Expand(item)  # expand the parent
                        # Set the focus to the tree
                        self.tree.SetFocus()
                        # Translators: Announcement of a search hit.
                        ui.message(_("Gefunden: {item}").format(item=self.tree.GetItemText(child)))
                        found = True
                        break
                    child = self.tree.GetNextSibling(child)
                if found:
                    break
            
            item = self.tree.GetNextSibling(item)
        
        if not found:
            tones.beep(200, 100)  # error tone
            # Translators: Message when the search found no match.
            ui.message(_("'{text}' nicht gefunden").format(text=search_text))
    
    def _jump_to_category(self, index):
        """Jumps to the category with the given index (0-based)"""
        root = self.tree.GetRootItem()
        item = self.tree.GetFirstChild(root)[0]
        
        current_index = 0
        while item.IsOk():
            if current_index == index:
                self.tree.SelectItem(item)
                self.tree.EnsureVisible(item)
                self.tree.Expand(item)
                # Translators: Announcement when jumping to a category via a
                # number key.
                ui.message(_("Kategorie {number}: {name}").format(
                    number=index + 1, name=self.tree.GetItemText(item)))
                return
            current_index += 1
            item = self.tree.GetNextSibling(item)
        
        tones.beep(200, 100)  # error tone
        # Translators: Message when the chosen category number does not exist.
        ui.message(_("Kategorie {number} nicht vorhanden").format(number=index + 1))
    
    def _quick_navigate_by_letter(self, char):
        """
        IMPROVEMENT 4: quick letter navigation in the tree
        Jumps to the next element that starts with the letter
        """
        root = self.tree.GetRootItem()
        if not root.IsOk():
            return
        
        # Determine the current selection
        current = self.tree.GetSelection()
        start_from_beginning = not current.IsOk()
        
        # Collect all visible items
        visible_items = []
        
        def collect_visible_items(parent):
            """Collects all visible items recursively"""
            item, cookie = self.tree.GetFirstChild(parent)
            while item.IsOk():
                visible_items.append(item)
                # If expanded, also add the children
                if self.tree.IsExpanded(item):
                    collect_visible_items(item)
                item, cookie = self.tree.GetNextChild(parent, cookie)
        
        collect_visible_items(root)
        
        if not visible_items:
            return
        
        # Find the start position
        start_idx = 0
        if not start_from_beginning and current.IsOk():
            try:
                start_idx = visible_items.index(current) + 1
            except ValueError:
                start_idx = 0
        
        # Search from the start position (with wraparound)
        for i in range(len(visible_items)):
            idx = (start_idx + i) % len(visible_items)
            item = visible_items[idx]
            text = self.tree.GetItemText(item).lower()
            
            # Check whether the text starts with the letter (ignore
            # emojis/symbols at the beginning). Remove leading special
            # characters
            clean_text = text.strip()
            
            if clean_text.startswith(char):
                self.tree.SelectItem(item)
                self.tree.EnsureVisible(item)
                # Short announcement of the found element
                tones.beep(600, 20)
                return
        
        # Not found
        tones.beep(200, 50)
    
    def _update_dialog_title(self):
        """
        IMPROVEMENT 6: updates the dialog title with the device count
        """
        if not self.plugin.devices:
            self.SetTitle(_("Smart Home Geräte"))
            return
        
        total = len(self.plugin.devices)
        online = sum(1 for d in self.plugin.devices if not (hasattr(d, 'is_offline') and d.is_offline))
        
        # Translators: Window title with online counter.
        self.SetTitle(_("Smart Home Geräte ({online}/{total} online)").format(online=online, total=total))
    
    def _update_status_bar(self, announce=False):
        """
        Updates the status line

        Args:
            announce: if True, the status is announced via ui.message (live region)
        """
        if not self.plugin.devices:
            # Translators: Status line text when no devices have been loaded
            # yet.
            status_text = _("Keine Geräte geladen")
            self._set_status_text(status_text)
            if announce:
                ui.message(status_text)
            return

        online = sum(1 for d in self.plugin.devices if not (hasattr(d, 'is_offline') and d.is_offline))
        offline = len(self.plugin.devices) - online

        # Timestamp: dialog time or plugin cache time
        update_time = self.last_update_time
        if not update_time and hasattr(self.plugin, '_last_refresh_time') and self.plugin._last_refresh_time > 0:
            update_time = self.plugin._last_refresh_time

        if update_time:
            # Absolute time (HH:MM:SS) in the permanently visible field. With
            # the dialog open, the polling scheduler regularly calls
            # refresh_all_device_data_live and sets last_update_time to "now";
            # a relative display running permanently in the field would
            # therefore be constantly outdated. The relative "X sec ago" is
            # available on demand in the Ctrl+T announcement instead
            # (_announce_status_bar).
            time_text = time.strftime("%H:%M:%S", time.localtime(update_time))
        else:
            # Translators: Status line hint while no refresh has happened yet.
            time_text = _("noch nicht aktualisiert")

        # Translators: Format of the status line. {online}/{offline} = counts,
        # {time} = time of the last refresh in HH:MM:SS format.
        status_text = _("{online} online, {offline} offline | Aktualisiert: {time}").format(
            online=online, offline=offline, time=time_text,
        )

        self._set_status_text(status_text)

        # IMPROVEMENT 2: live region - automatic announcement on important
        # changes
        if announce:
            # Translators: Short live announcement of the device online status.
            ui.message(_("{online} Geräte online, {offline} offline").format(
                online=online, offline=offline,
            ))
        
        # If the status line is focused, announce the current value
        if self.status_bar.HasFocus():
            # Send a VALUE_CHANGE event so NVDA notices the change
            wx.CallAfter(lambda: eventHandler.queueEvent("valueChange", api.getFocusObject()))
        
        # IMPROVEMENT 6: update the dialog title
        self._update_dialog_title()
    
    def _on_char(self, event):
        """Handle the ESC key (deprecated - handled in _on_tree_char)"""
        event.Skip()
    
    
    def _meross_subdevice_online(self, subdev, hub_is_online):
        """Determines whether a hub subdevice (sensor) is online.

        meross_iot only maintains an online flag for subdevice types it has a
        dedicated class for (MS100, MS400/MS405, MTS100). Unmapped types
        such as the MS130 fall back to the base GenericSubDevice, whose
        online_status is never updated (and may even raise). For those the
        library value is meaningless, so we inherit the hub's status: a sensor
        paired to an online hub and delivering data is online.
        """
        online_val = None
        try:
            online = getattr(subdev, 'online_status', None)
            online_val = getattr(online, 'value', None)
        except Exception as e:
            log.debug(f"Ignorierter Fehler beim Lesen des Online-Status: {e}")
            online_val = None
        if online_val == 1:      # OnlineStatus.ONLINE
            return True
        if online_val == 2:      # OnlineStatus.OFFLINE (from a tracking class)
            return False
        # 0 / -1 / None / error: library does not track this type -> use hub.
        return hub_is_online

    def _format_hub_subdevice_line(self, subdev, hub_is_online=True):
        """Builds a one-line description of a hub subdevice (sensor).

        Format: 'Name: Online, MS130, Batterie: 100%'. The online status,
        model type and battery level are each shown when available; missing
        parts fall back to a readable placeholder so the layout stays stable
        for screen reader users.
        """
        # Name
        name = getattr(subdev, 'name', None) or _("Sensor")

        # Online status (inherits the hub status for untracked types like MS130)
        status_text = _("Online") if self._meross_subdevice_online(subdev, hub_is_online) else _("Offline")

        # Model type (e.g. MS130)
        type_str = getattr(subdev, 'type', None)
        type_text = type_str.upper() if type_str else _("unbekannt")

        # Battery (from the background-polled cache)
        battery = None
        try:
            battery = get_subdevice_battery(getattr(subdev, 'subdevice_id', None))
        except Exception as e:
            log.debug(f"Ignorierter Fehler beim Lesen des Batteriestands: {e}")
        # Translators: Battery level of a hub sensor. {value} = percentage.
        # 'wird ermittelt' is shown until the first background battery poll has
        # completed (battery is fetched separately and cached).
        battery_text = (_("Batterie: {value}%").format(value=battery)
                        if battery is not None else _("Batterie: wird ermittelt"))

        # Translators: One line per hub sensor. {name} = sensor name,
        # {status} = Online/Offline, {type} = model (e.g. MS130),
        # {battery} = battery text.
        return _("{name}: {status}, {type}, {battery}").format(
            name=name, status=status_text, type=type_text, battery=battery_text)

    def _get_device_info(self, device):
        """Returns status info for a device"""
        # Netatmo devices
        # Translators: The following texts are status lines in the device tree.
        # The prefixes (e.g. "Temperatur:") must match the startswith checks of
        # the F1 context help, hence consistently via _().
        if getattr(device, 'is_netatmo', False):
            # Offline check first
            if hasattr(device, 'is_offline') and device.is_offline:
                info = [_("Status: Offline")]
                battery = device.get_battery_percent() if hasattr(device, 'get_battery_percent') else None
                if battery is not None:
                    info.append(_("Batterie: {percent}%").format(percent=battery))
                return info

            info = []
            temp = device.get_temperature()
            humidity = device.get_humidity()
            co2 = device.get_co2()
            noise = device.get_noise()
            pressure = device.get_pressure()
            rain = device.get_rain()
            wind = device.get_wind_strength()

            if temp is not None:
                info.append(_("Temperatur: {temp}°C").format(temp=f"{temp:.1f}"))
            if humidity is not None:
                info.append(_("Luftfeuchtigkeit: {humidity}%").format(humidity=f"{humidity:g}"))
            if co2 is not None:
                info.append(f"CO₂: {co2} ppm")
            if noise is not None:
                info.append(_("Lautstärke: {noise} dB").format(noise=noise))
            if pressure is not None:
                info.append(_("Luftdruck: {pressure} mbar").format(pressure=f"{pressure:.1f}"))
            if rain is not None:
                info.append(_("Regen: {rain} mm").format(rain=rain))
            if wind is not None:
                info.append(_("Wind: {wind} km/h").format(wind=wind))

            # Only for non-thermostats: setpoint/mode as pure info (thermostats
            # show these as interactive action items)
            if not getattr(device, 'is_thermostat', False):
                setpoint = device.get_setpoint_temp()
                if setpoint is not None:
                    info.append(_("Soll-Temperatur: {temp}°C").format(temp=f"{setpoint:.1f}"))

            # NAPlug (relay): show the boiler status
            if getattr(device, 'is_relay', False):
                boiler = device.get_boiler_status() if hasattr(device, 'get_boiler_status') else None
                if boiler is not None:
                    info.append(_("Heizung: {status}").format(
                        status=_("aktiv") if boiler else _("aus")))

            # Battery
            battery = device.get_battery_percent() if hasattr(device, 'get_battery_percent') else None
            if battery is not None:
                info.append(_("Batterie: {percent}%").format(percent=battery))

            return info if info else [_("Status: Verbunden")]

        # Handle offline devices first
        if hasattr(device, 'is_offline') and device.is_offline:
            return [_("Status: Offline")]
        
        type_lower = device.type.lower()
        
        # Temperature sensor (MS100, MS130)
        if device.is_temperature_sensor:
            temp = device.get_temperature()
            humidity = device.get_humidity()

            info = []
            if temp is not None:
                info.append(_("Temperatur: {temp}°C").format(temp=f"{temp:.1f}"))
            if humidity is not None:
                info.append(_("Luftfeuchtigkeit: {humidity}%").format(humidity=f"{humidity:g}"))

            return info if info else [_("Status: Keine Daten")]

        # Water sensor (MS400, MS405)
        elif device.is_water_sensor:
            alarm = device.is_water_detected()
            return [_("Status: WASSERALARM")] if alarm else [_("Status: Kein Wasser")]
        
        # Hub (MSH300, MSH450)
        elif "msh" in type_lower:
            info = []
            try:
                # Check the online status. online_status is an OnlineStatus
                # enum where ONLINE == value 1; bool() of the enum would be
                # truthy even for OFFLINE(2), so compare the value explicitly.
                is_online = False  # default
                raw_online = getattr(device._device, 'online_status', None)
                online_val = getattr(raw_online, 'value', None)
                if online_val is not None:
                    is_online = (online_val == 1)
                elif hasattr(device._device, '_online'):
                    is_online = (getattr(getattr(device._device, '_online', None), 'value', None) == 1)

                info.append(_("Status: Online") if is_online else _("Status: Offline"))

                # Show the connected sensors: first the count, then one line per
                # sensor with name, online status, model type and battery level.
                try:
                    if hasattr(device._device, 'get_subdevices'):
                        subdevices = list(device._device.get_subdevices()) if callable(device._device.get_subdevices) else []
                        if subdevices:
                            info.append(_("Verbundene Sensoren: {count}").format(count=len(subdevices)))
                            for subdev in subdevices:
                                info.append(self._format_hub_subdevice_line(subdev, is_online))
                except Exception as e:
                    log.debug(f"Ignorierter Fehler in _get_device_info: {e}")
            except Exception:
                info.append(_("Status: Unbekannt"))

            return info

        # Normal devices (plugs, lamps, LED strips)
        else:
            info = [_("Status: Ein") if device.is_on else _("Status: Aus")]

            # Power consumption for MSS310/MSS315 (with voltage and amperage).
            # NOTE: lamp-specific info is now added as interactive elements
            if device.has_power_meter:
                power = device.get_power()
                voltage = device.get_voltage()
                current = device.get_current()

                if power is not None:
                    info.append(_("Leistung: {power} W").format(power=power))
                if voltage is not None:
                    info.append(_("Spannung: {voltage} V").format(voltage=voltage))
                if current is not None:
                    info.append(_("Stromstärke: {current} A").format(current=current))

                # Verbrauch heute + letzte 7 Tage aus dem Geräte-Zähler
                # (consumptionX). Anzeige NUR aus dem Cache - der eigentliche
                # Abruf läuft im Hintergrund und ist per 15-Minuten-Cache
                # cloud-schonend (kein Budget-Verbrauch bei Dialog-Refreshes).
                info.extend(self._consumption_info_lines(device))

            return info

    def _consumption_info_lines(self, device):
        """Baut die Verbrauchs-Zeilen (heute / letzte 7 Tage) für eine
        Messsteckdose. Liest nur den Cache; fehlt er, wird EIN Hintergrund-
        Abruf gestartet und zunächst ein Platzhalter gezeigt, den das
        Live-Update später ersetzt."""
        api = getattr(self.plugin, 'api', None)
        if not api or not hasattr(api, 'peek_daily_consumption'):
            return []
        data = api.peek_daily_consumption(device.uuid)
        if data:
            kwh_today, kwh_week = api.summarize_daily_consumption(data)
            return [
                # Translators: Today's consumption from the device meter.
                _("Verbrauch heute: {kwh} kWh").format(
                    kwh=f"{kwh_today:.2f}".replace(".", ",")),
                # Translators: Last 7 days' consumption from the device meter.
                _("Verbrauch letzte 7 Tage: {kwh} kWh").format(
                    kwh=f"{kwh_week:.2f}".replace(".", ",")),
            ]
        # Noch kein Cache: Abruf im Hintergrund anstoßen (nur einmal je
        # Gerät und Dialog-Sitzung), Platzhalter anzeigen.
        if not hasattr(self, '_consumption_fetch_started'):
            self._consumption_fetch_started = set()
        if device.uuid not in self._consumption_fetch_started:
            self._consumption_fetch_started.add(device.uuid)

            def fetch(uuid=device.uuid):
                try:
                    if api.get_daily_consumption(uuid):
                        self._safe_call_after(self.refresh_all_device_data_live)
                except Exception as e:
                    log.debug(f"Verbrauchs-Abruf fehlgeschlagen: {e}")
            threading.Thread(target=fetch, daemon=True).start()
        return [
            # Translators: Placeholder while the consumption data is fetched.
            _("Verbrauch heute: wird abgerufen..."),
            # Translators: Placeholder while the consumption data is fetched.
            _("Verbrauch letzte 7 Tage: wird abgerufen..."),
        ]
    
    def _load_devices(self, force_refresh=False):
        """
        Loads devices into the tree (legacy - uses _load_devices_internal)

        Args:
            force_refresh: if True, reload the devices
                          if False, use the already loaded devices
        """
        try:
            # Only reload on the first load or when explicitly requested
            if force_refresh or not self.plugin.devices:
                devices = self.plugin.refresh_devices()
                self.last_update_time = time.time()
            else:
                # Use the already loaded devices (faster!)
                devices = self.plugin.devices
            
            self._load_devices_internal(devices)
            self._refresh_favorites_tree()
            
        except Exception as e:
            _beep(BEEP_ERROR)  # long error tone
            # Translators: Error dialog when loading the device list.
            wx.MessageBox(
                _("Fehler beim Laden: {error}").format(error=e),
                _("Fehler"),
                wx.OK | wx.ICON_ERROR
            )
    
    def _load_devices_internal(self, devices):
        """
        Internal method: fills the tree with devices (WITHOUT API calls!)
        Structure: platform main nodes > categories > devices

        Args:
            devices: list of MerossDevice objects
        """
        # Freeze prevents repaints during the tree construction (performance)
        self.tree.Freeze()
        self.tree.DeleteAllItems()
        # Translators: Invisible root node of the device tree.
        root = self.tree.AddRoot(_("Geräte"))
        
        try:
            # Apply the filter
            if self.filter_mode == "online":
                devices = [d for d in devices if not (hasattr(d, 'is_offline') and d.is_offline)]
            elif self.filter_mode == "offline":
                devices = [d for d in devices if hasattr(d, 'is_offline') and d.is_offline]
            elif self.filter_mode == "plugs":
                devices = [d for d in devices if getattr(d, 'is_plug', False)]
            elif self.filter_mode == "lights":
                devices = [d for d in devices if getattr(d, 'is_light', False)]
            elif self.filter_mode == "sensors":
                devices = [d for d in devices if getattr(d, 'is_sensor', False) or getattr(d, 'is_netatmo', False)]

            # Split by platform (central mapping)
            by_platform = split_by_platform(devices)
            meross_devices = by_platform['meross']
            netatmo_devices = by_platform['netatmo']
            vesync_devices = by_platform['vesync']
            cozytouch_devices = by_platform['cozytouch']
            
            # ---- Meross main node ----
            if meross_devices:
                # Translators: Platform main node. Brand name, do not
                # translate.
                meross_root = self.tree.AppendItem(root, _("Meross-Geräte ({count})").format(count=len(meross_devices)))
                self.tree.SetItemData(meross_root, None)
                
                # Group Meross devices by type
                # Translators: Category names in the device tree.
                meross_groups = {
                    _('Steckdosen'): [],
                    _('Lampen und LED-Strips'): [],
                    _('Diffuser'): [],
                    _('Temperatursensoren'): [],
                    _('Wassersensoren'): [],
                    _('Hubs'): [],
                    _('Andere Geräte'): []
                }

                for device in meross_devices:
                    type_lower = device.type.lower()
                    if device.is_temperature_sensor:
                        meross_groups[_('Temperatursensoren')].append(device)
                    elif device.is_water_sensor:
                        meross_groups[_('Wassersensoren')].append(device)
                    elif "msh" in type_lower:
                        meross_groups[_('Hubs')].append(device)
                    elif device.is_plug:
                        meross_groups[_('Steckdosen')].append(device)
                    elif device.is_light:
                        meross_groups[_('Lampen und LED-Strips')].append(device)
                    elif device.is_diffuser:
                        meross_groups[_('Diffuser')].append(device)
                    else:
                        meross_groups[_('Andere Geräte')].append(device)
                
                # Apply the sort order
                for group_devices in meross_groups.values():
                    self._sort_device_list(group_devices)
                
                # Create the categories under the Meross main node
                for group_name, group_devices in meross_groups.items():
                    if not group_devices:
                        continue
                    cat = self.tree.AppendItem(meross_root, f"{group_name} ({len(group_devices)})")
                    self.tree.SetItemData(cat, None)
                    self._add_meross_devices_to_category(cat, group_devices)
                    self.tree.Collapse(cat)
                
                # The Meross main node stays collapsed (the user expands it if
                # needed)
            
            # ---- Netatmo main node ----
            if netatmo_devices:
                # Translators: Platform main node. Brand name, do not
                # translate.
                netatmo_root = self.tree.AppendItem(root, _("Netatmo-Geräte ({count})").format(count=len(netatmo_devices)))
                self.tree.SetItemData(netatmo_root, None)
                
                # Group Netatmo devices by type
                # Translators: Category names in the device tree.
                netatmo_groups = {
                    _('Wetterstationen'): [],
                    _('Thermostate'): [],
                    _('Gateways'): [],
                    _('Raumluft'): [],
                    _('Andere Geräte'): []
                }

                # Heiz-Geräte (Thermostate/Ventile) werden nach Räumen
                # gruppiert, sofern Netatmo einen Raumnamen liefert. Geräte
                # ohne Raum bleiben in der Sammelkategorie "Thermostate".
                room_groups = {}
                for device in netatmo_devices:
                    dev_type = getattr(device, 'device_type', '')
                    if dev_type == 'thermostat':
                        room = getattr(device, 'room_name', '') or ''
                        if room:
                            # Translators: Room category in the device tree.
                            # {room} = room name from the Netatmo app.
                            key = _("Raum {room}").format(room=room)
                            room_groups.setdefault(key, []).append(device)
                        else:
                            netatmo_groups[_('Thermostate')].append(device)
                    elif dev_type == 'gateway':
                        netatmo_groups[_('Gateways')].append(device)
                    elif dev_type == 'aircare':
                        netatmo_groups[_('Raumluft')].append(device)
                    elif dev_type == 'weather':
                        netatmo_groups[_('Wetterstationen')].append(device)
                    else:
                        netatmo_groups[_('Andere Geräte')].append(device)

                # Raum-Kategorien alphabetisch direkt nach den
                # Wetterstationen einsortieren (vor der Sammelkategorie).
                if room_groups:
                    merged = {_('Wetterstationen'): netatmo_groups.pop(_('Wetterstationen'))}
                    for key in sorted(room_groups):
                        merged[key] = room_groups[key]
                    merged.update(netatmo_groups)
                    netatmo_groups = merged
                
                # Apply the sort order
                for group_devices in netatmo_groups.values():
                    self._sort_device_list(group_devices)
                
                # Create the categories under the Netatmo main node
                for group_name, group_devices in netatmo_groups.items():
                    if not group_devices:
                        continue
                    cat = self.tree.AppendItem(netatmo_root, f"{group_name} ({len(group_devices)})")
                    self.tree.SetItemData(cat, None)
                    self._add_netatmo_devices_to_category(cat, group_devices)
                    self.tree.Collapse(cat)
                
                # The Netatmo main node stays collapsed (the user expands it if
                # needed)

            # ---- VeSync main node ----
            if vesync_devices:
                # Translators: Platform main node. Brand name, do not
                # translate.
                vesync_root = self.tree.AppendItem(root, _("VeSync-Geräte ({count})").format(count=len(vesync_devices)))
                self.tree.SetItemData(vesync_root, None)

                # Group VeSync devices by type (air purifiers / fans)
                # Translators: Category names in the device tree.
                vesync_groups = {
                    _('Luftreiniger'): [],
                    _('Ventilatoren'): [],
                    _('Andere Geräte'): [],
                }
                for device in vesync_devices:
                    cls_name = type(device).__name__
                    if cls_name == 'VeSyncPurifier':
                        vesync_groups[_('Luftreiniger')].append(device)
                    elif cls_name == 'VeSyncTowerFan':
                        vesync_groups[_('Ventilatoren')].append(device)
                    else:
                        vesync_groups[_('Andere Geräte')].append(device)

                # Apply the sort order
                for group_devices in vesync_groups.values():
                    self._sort_device_list(group_devices)

                for group_name, group_devices in vesync_groups.items():
                    if not group_devices:
                        continue
                    cat = self.tree.AppendItem(vesync_root, f"{group_name} ({len(group_devices)})")
                    self.tree.SetItemData(cat, None)
                    self._add_vesync_devices_to_category(cat, group_devices)
                    self.tree.Collapse(cat)

            # ---- Cozytouch main node (Atlantic / Austria Email) ----
            if cozytouch_devices:
                # Translators: Platform main node. Brand name, do not
                # translate.
                cozytouch_root = self.tree.AppendItem(root, _("Cozytouch-Geräte ({count})").format(count=len(cozytouch_devices)))
                self.tree.SetItemData(cozytouch_root, None)
                self._sort_device_list(cozytouch_devices)
                # Translators: Category name in the device tree.
                cat = self.tree.AppendItem(
                    cozytouch_root, _("Warmwasser-Wärmepumpen ({count})").format(count=len(cozytouch_devices)))
                self.tree.SetItemData(cat, None)
                self._add_cozytouch_devices_to_category(cat, cozytouch_devices)
                self.tree.Collapse(cat)

            # If no devices are present
            if not meross_devices and not netatmo_devices and not vesync_devices \
                    and not cozytouch_devices:
                # Translators: Entry when no devices are present/after
                # filtering.
                empty_item = self.tree.AppendItem(root, _("Keine Geräte gefunden"))
                self.tree.SetItemData(empty_item, {'type': 'info'})
            
            # Update the status line
            self._update_status_bar()
                
        except Exception as e:
            log.error(f"Fehler beim Befüllen des Baums: {e}")
            _beep(BEEP_ERROR)
        finally:
            self.tree.Thaw()
    
    def _sort_device_list(self, device_list):
        """Sorts a device list based on the current sort mode"""
        if self.sort_mode == "name":
            device_list.sort(key=lambda d: d.name.lower())
        elif self.sort_mode == "type":
            device_list.sort(key=lambda d: d.type.lower())
        elif self.sort_mode == "status":
            device_list.sort(key=lambda d: (
                hasattr(d, 'is_offline') and d.is_offline,
                d.name.lower()
            ))
    
    
    


    
    # Favorites helper methods
    # ----------------------------------------------------------
    def _show_history_dialog(self):
        """Shows the history dialog (accessible for screen readers)"""
        dlg = HistoryDialog(self, self.plugin)
        dlg.ShowModal()
        dlg.Destroy()
    
    def _on_item_activated(self, event):
        """Called when Enter is pressed on an element"""
        item = event.GetItem()
        if not item.IsOk():
            return
        
        data = self.tree.GetItemData(item)
        
        # Category or device: expand/collapse
        if data is None or data.get('type') == 'device':
            if self.tree.ItemHasChildren(item):
                if self.tree.IsExpanded(item):
                    self.tree.Collapse(item)
                else:
                    self.tree.Expand(item)
                    # Lazy loading: update the status on expand
                    if data and data.get('type') == 'device':
                        self._update_device_item(item, data)
        
        # Info: do nothing
        elif data.get('type') == 'info':
            pass
        
        # Action: execute
        elif data.get('type') == 'action':
            self._execute_action(item, data)
    
    def _on_item_expanding(self, event):
        """Called when an element is expanded"""
        item = event.GetItem()
        if not item.IsOk():
            return

        data = self.tree.GetItemData(item)

        # For devices: update the status (lazy loading)
        if data and data.get('type') == 'device':
            device = data.get('device')
            # VeSync devices: the children are already built completely by
            # _fill_vesync_device_children when the tree is filled. The generic
            # _update_device_item path would delete them and replace them with
            # a simple status+toggle pair, making the user wait for the
            # complete device list until the next background refresh. Hence
            # skip here.
            if getattr(device, 'is_vesync', False):
                return
            # Cozytouch devices: the children come completely from the mixin
            # (_fill_cozytouch_device_children). The generic
            # _update_device_item path does not know them and would
            # additionally call device._update_status(), which does not exist
            # for Cozytouch.
            if getattr(device, 'is_cozytouch', False):
                return
            channel = data.get('channel')
            is_off = bool(hasattr(device, 'is_offline') and device.is_offline)
            if is_off:
                # Offline-Geräte: kein Status-Refresh (kostet nur Zeit). Ein
                # Kanal-Node wird trotzdem befüllt, damit er sich aufklappen
                # lässt und "Status: Offline" zeigt, statt leer zu bleiben.
                if channel is not None:
                    self.tree.DeleteChildren(item)
                    self._build_meross_device_children(item, device, channel)
                return
            self._update_device_item(item, data)
    
    def _build_meross_device_children(self, node, device, channel=None, is_favorite_view=False):
        """Single source of truth for the child nodes of a Meross device.

        Builds the info/action rows under a device node (or a channel node) plus
        the favorite action. The CALLER is responsible for clearing existing
        children (DeleteChildren) and for refreshing the device status
        beforehand; this method only reads the already-current cached state and
        never performs network I/O.

        Used by the initial tree build (_add_single_meross_device), the
        expand/refresh path (_update_device_item) and the toggle/diffuser action
        handlers, so all of them stay consistent (this consolidation also fixes
        the favorite entry that previously vanished after a rebuild).

        Returns a dict mapping action name -> tree item (e.g. 'toggle',
        'diffuser_light') so callers can re-select a specific row afterwards.
        """
        action_items = {}
        offline = bool(hasattr(device, 'is_offline') and device.is_offline)

        # ---- Channel node (one output of a multi-channel device) ----
        if channel:
            status = _("Offline") if offline else (_("Ein") if channel.is_on else _("Aus"))
            info_item = self.tree.AppendItem(node, _("Status: {status}").format(status=status))
            self.tree.SetItemData(info_item, {'type': 'info', 'device': device, 'channel': channel})

            # Power consumption for power-meter channels (with voltage/amperage)
            if channel.has_power_meter:
                power = channel.get_power()
                if power is not None:
                    power_item = self.tree.AppendItem(node, _("Leistung: {value} W").format(value=power))
                    self.tree.SetItemData(power_item, {'type': 'info', 'device': device, 'channel': channel})
                voltage = channel.get_voltage() if hasattr(channel, 'get_voltage') else None
                if voltage is not None:
                    voltage_item = self.tree.AppendItem(node, _("Spannung: {value} V").format(value=voltage))
                    self.tree.SetItemData(voltage_item, {'type': 'info', 'device': device, 'channel': channel})
                current = channel.get_current() if hasattr(channel, 'get_current') else None
                if current is not None:
                    current_item = self.tree.AppendItem(node, _("Stromstärke: {value} A").format(value=current))
                    self.tree.SetItemData(current_item, {'type': 'info', 'device': device, 'channel': channel})

            # Action only when online
            if not offline:
                action_text = (_("{name} ausschalten") if channel.is_on else _("{name} einschalten")).format(name=channel.name)
                action_item = self.tree.AppendItem(node, action_text)
                self.tree.SetItemData(action_item, {'type': 'action', 'device': device, 'channel': channel, 'action': 'toggle'})
                action_items['toggle'] = action_item
            # Channels carry no favorite (favorites are per device).
            return action_items

        # ---- Device node ----
        channels = device.get_channels() if hasattr(device, 'get_channels') else []

        if channels:
            # Multi-channel device: combined status + expandable channel nodes.
            # Im Kombistatus reicht das kurze Ausgangs-Label (das Elterngerät
            # ist der Baumknoten darüber): "Ausgang 1: Ein, Ausgang 2: Aus".
            def _ch_label(ch):
                return getattr(ch, 'outlet_label', None) or ch.name
            if offline:
                channel_stati = [f"{_ch_label(ch)}: {_('Offline')}" for ch in channels]
            else:
                channel_stati = [f"{_ch_label(ch)}: {_('Ein') if ch.is_on else _('Aus')}" for ch in channels]
            status_item = self.tree.AppendItem(node, _("Status: {status}").format(status=", ".join(channel_stati)))
            self.tree.SetItemData(status_item, {'type': 'info', 'device': device})

            for ch in channels:
                # Kanal-Node trägt den vollen Namen "Garten: Ausgang 1" ohne
                # zusätzliches "Kanal:"-Präfix (selbsterklärend, wie vom Nutzer
                # gewünscht).
                channel_item = self.tree.AppendItem(node, ch.name)
                self.tree.SetItemData(channel_item, {'type': 'device', 'device': device, 'channel': ch})
                self.tree.SetItemHasChildren(channel_item, True)  # lazy: filled on expand
                self.tree.Collapse(channel_item)

            self._add_favorite_action(node, device, is_favorite_view)
            return action_items

        # Normal single device: diffuser vs. everything else
        if device.is_diffuser:
            if offline:
                status_text = _("Status: Offline")
            else:
                spray_mode = device.get_diffuser_spray_mode()
                status_text = _("Status: {status}").format(status=spray_mode)
            info_item = self.tree.AppendItem(node, status_text)
            self.tree.SetItemData(info_item, {'type': 'info', 'device': device})

            if not offline:
                action_light = self.tree.AppendItem(node, _("Schwach sprühen"))
                self.tree.SetItemData(action_light, {'type': 'action', 'device': device, 'action': 'diffuser_light'})
                action_items['diffuser_light'] = action_light

                action_strong = self.tree.AppendItem(node, _("Stark sprühen"))
                self.tree.SetItemData(action_strong, {'type': 'action', 'device': device, 'action': 'diffuser_strong'})
                action_items['diffuser_strong'] = action_strong

                action_off = self.tree.AppendItem(node, _("Sprühen aus"))
                self.tree.SetItemData(action_off, {'type': 'action', 'device': device, 'action': 'diffuser_off'})
                action_items['diffuser_off'] = action_off
        else:
            # NORMAL DEVICES (plugs, lamps, sensors, hubs)
            info_lines = self._get_device_info(device)
            for info_line in info_lines:
                info_item = self.tree.AppendItem(node, info_line)
                self.tree.SetItemData(info_item, {'type': 'info', 'device': device})

            # Toggle only for switchable, online, non-sensor, non-hub devices
            if not device.is_sensor and not getattr(device, 'is_hub', False) and not offline:
                action_text = (_("{name} ausschalten") if device.is_on else _("{name} einschalten")).format(name=device.name)
                action_item = self.tree.AppendItem(node, action_text)
                self.tree.SetItemData(action_item, {'type': 'action', 'device': device, 'action': 'toggle'})
                action_items['toggle'] = action_item

                # LAMP ACTIONS: brightness, color temperature, RGB (lamp on only)
                if device.is_light and device.is_on:
                    if device.supports_luminance():
                        luminance = device.get_luminance()
                        if luminance is not None:
                            lum_text = _("Helligkeit: {value}% - Enter zum Ändern").format(value=luminance)
                        else:
                            lum_text = _("Helligkeit einstellen - Enter zum Öffnen")
                        brightness_item = self.tree.AppendItem(node, lum_text)
                        self.tree.SetItemData(brightness_item, {'type': 'action', 'device': device, 'action': 'light_luminance'})
                        action_items['light_luminance'] = brightness_item

                    if device.supports_temperature():
                        temp = device.get_color_temperature()
                        is_in_rgb = device.is_in_rgb_mode() if hasattr(device, 'is_in_rgb_mode') else None
                        if is_in_rgb == False or is_in_rgb is None:
                            if temp is not None and temp > 0:
                                if temp <= 33:
                                    temp_text = _("Lichtfarbe: Warmweiß (gemütlich) - Enter zum Ändern")
                                elif temp <= 66:
                                    temp_text = _("Lichtfarbe: Tageslicht (neutral) - Enter zum Ändern")
                                else:
                                    temp_text = _("Lichtfarbe: Kaltweiß (hell) - Enter zum Ändern")
                            else:
                                temp_text = _("Lichtfarbe (Weiß) einstellen - Enter zum Öffnen")
                            temp_item = self.tree.AppendItem(node, temp_text)
                            self.tree.SetItemData(temp_item, {'type': 'action', 'device': device, 'action': 'light_temperature'})
                            action_items['light_temperature'] = temp_item
                        else:
                            temp_text = _("Auf Weißmodus wechseln - Enter zum Öffnen")
                            temp_item = self.tree.AppendItem(node, temp_text)
                            self.tree.SetItemData(temp_item, {'type': 'action', 'device': device, 'action': 'light_temperature'})
                            action_items['light_temperature'] = temp_item

                    if device.supports_rgb():
                        is_in_rgb = device.is_in_rgb_mode() if hasattr(device, 'is_in_rgb_mode') else None
                        if is_in_rgb == True:
                            rgb = device.get_rgb_color()
                            if rgb is not None:
                                color_name = self._get_color_name_from_rgb(rgb)
                                if color_name:
                                    rgb_text = _("Farbe: {color} (RGB {r},{g},{b}) - Enter zum Ändern").format(color=color_name, r=rgb[0], g=rgb[1], b=rgb[2])
                                else:
                                    rgb_text = _("Farbe: RGB({r}, {g}, {b}) - Enter zum Ändern").format(r=rgb[0], g=rgb[1], b=rgb[2])
                            else:
                                rgb_text = _("Farbe einstellen - Enter zum Öffnen")
                        else:
                            rgb_text = _("Auf Farbmodus wechseln - Enter zum Öffnen")
                        rgb_item = self.tree.AppendItem(node, rgb_text)
                        self.tree.SetItemData(rgb_item, {'type': 'action', 'device': device, 'action': 'light_rgb'})
                        action_items['light_rgb'] = rgb_item

        # Favorite action as the last child on the device node.
        self._add_favorite_action(node, device, is_favorite_view)
        return action_items

    def _prefetch_netatmo_schedules(self, home_id):
        """Füllt den Heizprogramm-Zwischenspeicher im Hintergrund.

        Wird aufgerufen, wenn beim Aufklappen eines Thermostats noch nichts
        zwischengespeichert war. Der Baum bekommt bewusst KEIN nachträgliches
        Update: einen Eintrag umzubenennen, während der Nutzer schon mit den
        Pfeiltasten darauf steht, verwirrt mehr als es hilft - NVDA würde die
        Zeile erneut ansagen. Beim nächsten Aufklappen steht der Name da.

        Höchstens ein Abruf gleichzeitig; ein Fehlschlag bleibt folgenlos.
        """
        if getattr(self, '_schedule_prefetch_running', False):
            return
        netatmo_api = self.plugin.netatmo_api
        if not netatmo_api:
            return
        self._schedule_prefetch_running = True

        def task():
            try:
                netatmo_api.get_schedules(home_id)
            except Exception as e:
                log.debug(f"Heizprogramme konnten nicht vorgeladen werden: {e}")
            finally:
                self._schedule_prefetch_running = False

        threading.Thread(target=task, daemon=True).start()

    def _update_device_item(self, item, data, skip_status_update=False):
        """Updates the status of a single device

        Args:
            item: the tree entry
            data: data of the device/channel
            skip_status_update: if True, _update_status() is not called
                               (useful when the cache should be kept)
        """
        device = data.get('device')
        channel = data.get('channel')
        
        # Do not update offline devices
        if hasattr(device, 'is_offline') and device.is_offline:
            return
        
        try:
            # Update the status (only when not skipped)
            if not skip_status_update:
                if channel:
                    channel._update_status()
                else:
                    device._update_status()
            
            # Update the tree entries
            self.tree.DeleteChildren(item)
            
            # --- Netatmo devices: their own path (no toggle!) ---
            if getattr(device, 'is_netatmo', False):
                # Info items
                info_lines = self._get_device_info(device)
                for info_line in info_lines:
                    info_item = self.tree.AppendItem(item, info_line)
                    self.tree.SetItemData(info_item, {'type': 'info', 'device': device})
                
                # Thermostat actions (NATherm1, NRV)
                if getattr(device, 'is_thermostat', False):
                    # Show the boiler status
                    boiler = device.get_boiler_status() if hasattr(device, 'get_boiler_status') else None
                    if boiler is not None:
                        boiler_text = _("Heizung: aktiv") if boiler else _("Heizung: aus")
                        boiler_item = self.tree.AppendItem(item, boiler_text)
                        self.tree.SetItemData(boiler_item, {'type': 'info', 'device': device})
                    
                    # Show pre-heating (anticipation)
                    anticipating = device.is_anticipating() if hasattr(device, 'is_anticipating') else None
                    if anticipating:
                        antic_item = self.tree.AppendItem(item, _("Vorausheizen: aktiv"))
                        self.tree.SetItemData(antic_item, {'type': 'info', 'device': device})
                    
                    # Show the open window
                    open_window = device.is_open_window() if hasattr(device, 'is_open_window') else None
                    if open_window:
                        ow_item = self.tree.AppendItem(item, _("Offenes Fenster: erkannt (Heizung pausiert)"))
                        self.tree.SetItemData(ow_item, {'type': 'info', 'device': device})
                    
                    # Show the next schedule change (schedule mode only)
                    next_change = device.get_next_schedule_change() if hasattr(device, 'get_next_schedule_change') else None
                    if next_change and next_change.get('time'):
                        try:
                            change_time_str = time.strftime("%H:%M", time.localtime(next_change['time']))
                            nc_zone = next_change.get('zone_name', '')
                            nc_temp = next_change.get('temp')
                            if nc_temp is not None:
                                nc_text = _("Nächste Planänderung: {zone} ({temp:.1f}°C) um {time}").format(zone=nc_zone, temp=nc_temp, time=change_time_str)
                            else:
                                nc_text = _("Nächste Planänderung: {zone} um {time}").format(zone=nc_zone, time=change_time_str)
                            nc_item = self.tree.AppendItem(item, nc_text)
                            self.tree.SetItemData(nc_item, {'type': 'info', 'device': device})
                        except Exception as e:
                            log.debug(f"Ignorierter Fehler in _update_device_item: {e}")
                    
                    setpoint = device.get_setpoint_temp()
                    end_time = device.get_setpoint_end_time() if hasattr(device, 'get_setpoint_end_time') else None
                    sp_text = self._format_setpoint_text(setpoint, end_time)
                    sp_item = self.tree.AppendItem(item, sp_text)
                    self.tree.SetItemData(sp_item, {'type': 'action', 'device': device, 'action': 'netatmo_thermostat'})
                    
                    mode_text = self._get_netatmo_mode_text(device)
                    hm_item = self.tree.AppendItem(item, _("Heizmodus: {mode} - Enter zum Ändern").format(mode=mode_text))
                    self.tree.SetItemData(hm_item, {'type': 'action', 'device': device, 'action': 'netatmo_therm_mode'})
                    
                    # Switch the heating schedule (with the active schedule
                    # name).
                    #
                    # WICHTIG: hier NIE eine Netzanfrage auslösen. Diese
                    # Methode läuft im wx-Hauptthread am Aufklapp-Ereignis des
                    # Baums. get_schedules() ging früher direkt an die
                    # Netatmo-API - mit zwei Wiederholungen und 30 s Timeout
                    # dahinter. Antwortete Netatmo mit 503 (kommt vor, deren
                    # Server), fror der Dialog rund 7 Sekunden ein, bei
                    # hängender Verbindung bis zu 97. Ein eingefrorenes Fenster
                    # ohne Rückmeldung ist für Screenreader-Nutzer die
                    # unangenehmste Art zu scheitern.
                    #
                    # Jetzt: nur aus dem Zwischenspeicher (cached_only). Ist er
                    # leer, wird er im Hintergrund gefüllt und die Zeile beim
                    # nächsten Aufklappen beschriftet.
                    sched_label = _("Heizprogramm wechseln")
                    try:
                        netatmo_api = self.plugin.netatmo_api
                        home_id = getattr(device, 'home_id', '')
                        if netatmo_api and home_id:
                            schedules = netatmo_api.get_schedules(
                                home_id, cached_only=True)
                            for sched in schedules:
                                if sched.get('selected', False):
                                    sched_label = _("Heizprogramm: {name} - Enter zum Wechseln").format(name=sched['name'])
                                    break
                            else:
                                self._prefetch_netatmo_schedules(home_id)
                    except Exception as e:
                        log.debug(f"Ignorierter Fehler in _update_device_item: {e}")
                    sched_item = self.tree.AppendItem(item, sched_label)
                    self.tree.SetItemData(sched_item, {'type': 'action', 'device': device, 'action': 'netatmo_switch_schedule'})
                    
                    current_mode = device.get_setpoint_mode() if hasattr(device, 'get_setpoint_mode') else None
                    if current_mode and current_mode != 'schedule':
                        back_item = self.tree.AppendItem(item, _("Zurück zum Zeitplan - Enter zum Aktivieren"))
                        self.tree.SetItemData(back_item, {'type': 'action', 'device': device, 'action': 'netatmo_back_to_schedule'})
                
                # No toggle for Netatmo devices (thermostats, gateways, etc.)
                return
            
            # All Meross device/channel rows are built by the shared builder so
            # the expand/refresh path stays identical to the initial build and
            # the action handlers.
            self._build_meross_device_children(item, device, channel)
        except Exception as e:
            log.debug(f"Fehler beim Aktualisieren von {device.name}: {e}")
    
    # Action type -> method name of the handler (VeSync/Cozytouch/Netatmo). As
    # a name mapping (instead of bound methods) so the table can be defined at
    # class level.
    _ACTION_HANDLERS = {
        'vesync_toggle': '_handle_vesync_toggle',
        'vesync_mode': '_handle_vesync_mode',
        'vesync_fan_speed': '_handle_vesync_fan_speed',
        'vesync_oscillation': '_handle_vesync_oscillation',
        'vesync_mute': '_handle_vesync_mute',
        'vesync_display': '_handle_vesync_display',
        'vesync_child_lock': '_handle_vesync_child_lock',
        'vesync_nightlight': '_handle_vesync_nightlight',
        'vesync_auto_preference': '_handle_vesync_auto_preference',
        'vesync_reset_filter': '_handle_vesync_reset_filter',
        'cozytouch_temp': '_handle_cozytouch_temp',
        'cozytouch_mode': '_handle_cozytouch_mode',
        'cozytouch_boost': '_handle_cozytouch_boost',
        'cozytouch_boost_time': '_handle_cozytouch_boost_time',
        'cozytouch_toggle': '_handle_cozytouch_toggle',
        'cozytouch_away': '_handle_cozytouch_away',
        'netatmo_thermostat': '_handle_netatmo_thermostat',
        'netatmo_therm_mode': '_handle_netatmo_therm_mode',
        'netatmo_switch_schedule': '_handle_netatmo_switch_schedule',
        'netatmo_back_to_schedule': '_handle_netatmo_back_to_schedule',
    }

    def _execute_action(self, item, data):
        """Executes an action"""
        device = data.get('device')
        channel = data.get('channel')
        action = data.get('action')
        
        # Favorite actions (no offline check needed)
        if action in ('favorite_add', 'favorite_remove'):
            self._execute_favorite_action(item, data)
            return
        
        # Check whether the device is offline
        if hasattr(device, 'is_offline') and device.is_offline:
            tones.beep(300, 100)  # error tone
            # Translators: Message when the device is offline.
            ui.message(_("{name}: Offline").format(name=device.name))
            return
        
        # Platform handlers via a dispatch table instead of a long if/elif
        # cascade: one entry per action type keeps _execute_action flat and
        # makes adding new actions a one-line change.
        handler = self._ACTION_HANDLERS.get(action)
        if handler:
            getattr(self, handler)(device, item)
            return
        
        # Diffuser actions
        if action in ['diffuser_light', 'diffuser_strong', 'diffuser_off']:
            try:
                mode_name = DIFFUSER_MODE_NAMES.get(action, action)
                
                # Set the spray mode via meross_api
                self.plugin.set_diffuser_mode(device.uuid, action)
                
                # Update the status
                device._update_status()
                
                # Feedback
                _beep(BEEP_ON)
                # Translators: Confirmation after a diffuser mode change.
                ui.message(_("{name}: {mode}").format(name=device.name, mode=mode_name))

                # Verlaufseintrag: siehe plugin.set_diffuser_mode()

                # Rebuild the children via the shared builder (status + spray
                # actions + favorite) and re-select the executed action.
                parent = self.tree.GetItemParent(item)
                self.tree.DeleteChildren(parent)
                action_items = self._build_meross_device_children(parent, device)
                self.tree.Expand(parent)
                sel = action_items.get(action) or action_items.get('diffuser_light')
                if sel:
                    self.tree.SelectItem(sel)
                
            except Exception as e:
                _beep(BEEP_ERROR)
                log.error(f"Fehler beim Setzen des Diffuser-Modus: {e}")
                # Translators: Error message on a diffuser mode change.
                ui.message(_("{name}: Fehler beim Einstellen").format(name=device.name))
            return
        
        if action == 'toggle':
            try:
                # For a channel: use the channel UUID and channel status
                if channel:
                    device_uuid = channel.uuid  # format: "parent_uuid_chX"
                    device_name = channel.name
                else:
                    device_uuid = device.uuid
                    device_name = device.name
                
                # Execute the toggle - the status is only updated AFTER success
                self.plugin.toggle_device(device_uuid)
                
                # Read the new state from the authoritative device object:
                # toggle_device switched relative to the REAL current status
                # and already updated the object. The former ``not old_state``
                # from the tree would be wrong if the state was changed
                # externally between display and click.
                new_state = channel.is_on if channel else device.is_on
                
                # Now update the local status (AFTER a successful toggle)
                if channel:
                    channel._is_on = new_state
                    # For channels: also call _update_status() for correct
                    # diffuser support
                    channel._update_status()
                else:
                    device._is_on = new_state
                    # For devices: call _update_status() to read the spray mode
                    # correctly
                    device._update_status()
                
                # Short message + sound
                _beep(BEEP_ON if new_state else BEEP_OFF)

                # Der Verlaufseintrag entsteht in plugin.toggle_device() -
                # dem gemeinsamen Engpass von Dialog UND Favoriten-Gesten.
                # Hier wäre er doppelt und würde die Gesten weiter auslassen.

                # Optimized speech output
                is_first = self.last_announced_device != device_name
                self._announce_device_action(device_name, new_state, is_first)
                
                # Rebuild the children via the shared builder (info + toggle +
                # lamp actions + favorite) and re-select the toggle row.
                parent = self.tree.GetItemParent(item)
                self.tree.DeleteChildren(parent)
                action_items = self._build_meross_device_children(parent, device, channel)
                self.tree.Expand(parent)
                sel = action_items.get('toggle')
                if sel:
                    self.tree.SelectItem(sel)
                
            except TimeoutError as e:
                _beep(BEEP_ERROR)
                log.warning(f"Timeout beim Schalten von {device_name}: {e}")
                # Check whether the device is unreachable (most common case on
                # timeout)
                error_msg = str(e).lower()
                if "nicht erreichbar" in error_msg or "offline" in error_msg:
                    # Translators: Error message when the device is
                    # unreachable.
                    ui.message(_("{name}: Nicht erreichbar").format(name=device_name))
                else:
                    # Translators: Error message on timeout.
                    ui.message(_("{name}: Zeitüberschreitung").format(name=device_name))
                # Do NOT change the status on error!
                
            except (ConnectionError, OSError) as e:
                _beep(BEEP_ERROR)
                log.warning(f"Verbindungsfehler beim Schalten von {device_name}: {e}")
                # Check for a general network outage (DNS, no route)
                err_str = str(e).lower()
                if any(s in err_str for s in ['resolve', 'getaddrinfo', 'dns', 'no route', '10065', '10051']):
                    # Translators: Error message when there is no internet
                    # connection.
                    ui.message(_("{name}: Keine Internetverbindung").format(name=device_name))
                else:
                    # Translators: Error message on a general connection error.
                    ui.message(_("{name}: Verbindungsfehler").format(name=device_name))
                # Do NOT change the status on error!
                
            except RuntimeError as e:
                # Offline devices raise RuntimeError
                _beep(BEEP_ERROR)
                log.warning(f"RuntimeError beim Schalten von {device_name}: {e}")
                error_msg = str(e).lower()
                if "event loop" in error_msg:
                    # Translators: Message when the connection is being re-
                    # established.
                    ui.message(_("{name}: Verbindung wird wiederhergestellt").format(name=device_name))
                elif "offline" in error_msg or "nicht erreichbar" in error_msg:
                    # Translators: Error message when the device is
                    # unreachable.
                    ui.message(_("{name}: Nicht erreichbar").format(name=device_name))
                else:
                    # Translators: Generic error message with detail text.
                    ui.message(_("{name}: Fehler - {error}").format(name=device_name, error=str(e)[:40]))
                # Do NOT change the status on error!
                
            except Exception as e:
                _beep(BEEP_ERROR)
                # No exc_info: could leak tokens/headers in the stack trace.
                log.error(f"Fehler beim Schalten von {device_name}: {type(e).__name__}: {e}")
                # Translators: Error message when switching a device on/off
                # fails (e.g. cloud unreachable, device offline).
                ui.message(_("{name}: Schaltfehler").format(name=device_name))
                # Do NOT change the status on error!
        
        # LAMP ACTIONS
        elif action == 'light_temperature':
            try:
                # Fetch the current color temperature
                temp = device.get_color_temperature()
                
                # Extended selection with descriptions for screen readers
                # Translators: Choices for the light color (with description).
                choices = [
                    _("Warmweiß - gemütliches, warmes Licht wie Kerzenschein (ca. 2700K)"),
                    _("Tageslicht - neutrales, natürliches Licht zum Arbeiten (ca. 4000K)"),
                    _("Kaltweiß - helles, bläuliches Licht für Konzentration (ca. 6500K)"),
                ]
                
                # Help text with the current value
                # Translators: Help text in the light color dialog.
                help_text = _("Wählen Sie eine Lichtfarbe für {name}.").format(name=device.name) + "\n"
                if temp is not None:
                    if temp <= 33:
                        # Translators: Current light color in the help text.
                        help_text += _("Aktuelle Einstellung: Warmweiß") + "\n"
                    elif temp <= 66:
                        help_text += _("Aktuelle Einstellung: Tageslicht") + "\n"
                    else:
                        help_text += _("Aktuelle Einstellung: Kaltweiß") + "\n"
                # Translators: Usage hint in the light color dialog.
                help_text += _("Mit Pfeiltasten navigieren, Enter zum Bestätigen.")
                
                dlg = wx.SingleChoiceDialog(
                    self, 
                    help_text, 
                    # Translators: Title of the light color dialog.
                    _("Farbtemperatur für {name}").format(name=device.name),
                    choices
                )
                
                # Preselection based on the current value
                if temp is not None:
                    if temp <= 33:
                        dlg.SetSelection(0)
                    elif temp <= 66:
                        dlg.SetSelection(1)
                    else:
                        dlg.SetSelection(2)
                
                if dlg.ShowModal() == wx.ID_OK:
                    selection = dlg.GetSelection()
                    mode_map = {0: 'warm', 1: 'tageslicht', 2: 'kalt'}
                    # Translators: Short names of the light colors (for
                    # announcement/history).
                    name_map = {0: _('Warmweiß'), 1: _('Tageslicht'), 2: _('Kaltweiß')}
                    temp_map = {0: 0, 1: 50, 2: 100}  # temperature values for the cache
                    
                    mode_key = mode_map[selection]
                    mode_name = name_map[selection]
                    temp_value = temp_map[selection]
                    
                    try:
                        # Set the white tone via meross_api
                        self.plugin.api.set_light_white(uuid=device.uuid, white_type=mode_key)
                        
                        # Set the mode to white and cache the temperature (for
                        # a correct display)
                        if hasattr(device, 'set_light_mode'):
                            device.set_light_mode('white', temperature=temp_value)
                        else:
                            if hasattr(device, '_light_mode'):
                                device._light_mode = 'white'
                            if hasattr(device, '_cached_temperature'):
                                device._cached_temperature = temp_value
                            if hasattr(device, '_cached_rgb'):
                                device._cached_rgb = None
                        
                        # Success feedback
                        _beep(BEEP_ON)
                        # Translators: Confirmation after a light color change.
                        ui.message(_("{name}: {mode} eingestellt").format(name=device.name, mode=mode_name))
                        # Translators: History detail: light color that was
                        # set.
                        get_history().log_action(device, 'light_temperature', _('Farbtemperatur: {mode}').format(mode=mode_name))
                        
                        # Update the tree
                        try:
                            parent = self.tree.GetItemParent(item)
                            # skip_status_update=True: keep the local cache
                            self._update_device_item(parent, {'device': device}, skip_status_update=True)
                            self.tree.Expand(parent)
                        except Exception as e:
                            log.debug(f"Fehler beim Aktualisieren des Baums: {e}")
                            
                    except TimeoutError:
                        _beep(BEEP_ERROR)
                        # Translators: Error message on timeout.
                        ui.message(_("{name}: Zeitüberschreitung").format(name=device.name))
                    except Exception as e:
                        _beep(BEEP_ERROR)
                        log.error(f"Fehler beim Setzen der Farbtemperatur: {e}")
                        # Translators: Error message when setting the color
                        # temperature.
                        ui.message(_("{name}: Farbtemperatur nicht einstellbar").format(name=device.name))
                
                dlg.Destroy()
                
            except Exception as e:
                _beep(BEEP_ERROR)
                log.error(f"Fehler beim Öffnen des Farbtemperatur-Dialogs: {e}")
                # Translators: Error message when opening a sub-dialog.
                ui.message(_("{name}: Fehler beim Öffnen des Dialogs").format(name=device.name))
        
        elif action == 'light_luminance':
            try:
                # Fetch the current value for the preselection
                current_luminance = device.get_luminance()
                default_value = str(current_luminance) if current_luminance is not None else "50"
                
                # Detailed help text for screen readers
                # Translators: Help texts in the brightness dialog.
                help_text = _("Geben Sie die Helligkeit für {name} ein.").format(name=device.name) + "\n"
                help_text += _("Wert zwischen 0 (aus) und 100 (maximale Helligkeit).") + "\n"
                if current_luminance is not None:
                    help_text += _("Aktuelle Helligkeit: {value}%").format(value=current_luminance)
                
                dlg = wx.TextEntryDialog(
                    self, 
                    help_text, 
                    # Translators: Title of the brightness dialog.
                    _("Helligkeit für {name}").format(name=device.name),
                    default_value
                )
                
                if dlg.ShowModal() == wx.ID_OK:
                    try:
                        input_value = dlg.GetValue().strip()
                        
                        # Remove a percent sign if present
                        input_value = input_value.replace('%', '').strip()
                        
                        luminance = int(input_value)
                        if not (0 <= luminance <= 100):
                            tones.beep(300, 100)
                            # Translators: Error message for a brightness value
                            # out of range.
                            ui.message(_("Ungültiger Wert: {value}. Erlaubt: 0 bis 100.").format(value=luminance))
                            dlg.Destroy()
                            return
                        
                        try:
                            # Set the brightness
                            self.plugin.api.set_light_luminance(uuid=device.uuid, luminance=luminance)
                            
                            # Success feedback
                            _beep(BEEP_ON)
                            # Translators: Confirmation after a brightness
                            # change.
                            ui.message(_("{name}: Helligkeit auf {percent}% eingestellt").format(
                                name=device.name, percent=luminance))
                            # Translators: History detail: brightness that was
                            # set.
                            get_history().log_action(device, 'light_luminance', _('Helligkeit: {value}%').format(value=luminance))
                            
                            # Update the tree
                            try:
                                parent = self.tree.GetItemParent(item)
                                self._update_device_item(parent, {'device': device})
                                self.tree.Expand(parent)
                            except Exception as e:
                                log.debug(f"Fehler beim Aktualisieren des Baums: {e}")
                                
                        except TimeoutError:
                            _beep(BEEP_ERROR)
                            # Translators: Error message on timeout.
                            ui.message(_("{name}: Zeitüberschreitung").format(name=device.name))
                        except Exception as e:
                            _beep(BEEP_ERROR)
                            log.error(f"Fehler beim Setzen der Helligkeit: {e}")
                            # Translators: Error message when setting the
                            # brightness.
                            ui.message(_("{name}: Helligkeit nicht einstellbar").format(name=device.name))
                        
                    except ValueError:
                        tones.beep(300, 100)
                        # Translators: Error message on non-numeric brightness
                        # input.
                        ui.message(_("Ungültige Eingabe. Erlaubt: Zahl von 0 bis 100."))
                        
                dlg.Destroy()
                
            except Exception as e:
                _beep(BEEP_ERROR)
                log.error(f"Fehler beim Öffnen des Helligkeits-Dialogs: {e}")
                # Translators: Error message when opening the brightness
                # dialog.
                ui.message(_("{name}: Dialogfehler").format(name=device.name))
        
        elif action == 'light_rgb':
            # Show the accessible color picker dialog
            self._show_color_picker_dialog(device, item)
    
    
    
    
    def _on_refresh(self, event):
        """Refreshes the device list - OPTIMIZED for faster updates"""
        # Disable the button and change its text
        # Translators: Temporary label of the refresh button while loading.
        self.refresh_btn.SetLabel(_("Lädt..."))
        self.refresh_btn.Enable(False)

        # OPTIMIZED: set the status line + speak in one step.
        # Translators: Status while a manual refresh is running.
        self._set_status_text(_("Aktualisiere..."), announce=True)
        
        # Start the periodic loading beep (as on opening)
        self._start_loading_beep()
        
        # Asynchronous refresh in the background
        def refresh_thread():
            try:
                # Check whether the dialog still exists
                if self._is_destroyed:
                    return
                    
                start_time = time.time()
                self.plugin.refresh_devices()
                elapsed = time.time() - start_time
                log.debug(f"Refresh dauerte {elapsed:.1f}s")
                self._safe_call_after(self._reload_and_notify, elapsed)
            except TimeoutError:
                self._safe_call_after(self._stop_loading_beep)
                self._safe_call_after(tones.beep, 200, 200)
                # Translators: Error message on a refresh timeout.
                self._safe_call_after(ui.message, _("Zeitüberschreitung – bitte erneut versuchen"))
                self._safe_call_after(self._reset_refresh_button)
            except (ConnectionError, OSError) as e:
                self._safe_call_after(self._stop_loading_beep)
                self._safe_call_after(tones.beep, 200, 200)
                friendly_msg = self._format_network_error(str(e))
                self._safe_call_after(ui.message, friendly_msg)
                self._safe_call_after(self._reset_refresh_button)
            except Exception as e:
                self._safe_call_after(self._stop_loading_beep)
                self._safe_call_after(tones.beep, 200, 200)
                friendly_msg = self._format_network_error(str(e))
                self._safe_call_after(ui.message, friendly_msg)
                self._safe_call_after(self._reset_refresh_button)
        
        threading.Thread(target=refresh_thread, daemon=True).start()
    
    def _reload_and_notify(self, elapsed=None):
        """Reloads the devices and notifies the user"""
        # Stop the loading beep
        self._stop_loading_beep()
        self.last_update_time = time.time()
        # Use the internal method directly (the devices are already updated)
        self._load_devices_internal(self.plugin.devices)
        self._refresh_favorites_tree()
        self._reset_refresh_button()
        _beep(BEEP_OFF)  # ehemals 600,80 = Erfolg  # formerly 600,80 = success  # success tone (same length as the loading beep)
        
        # Detailed feedback
        online = sum(1 for d in self.plugin.devices if not (hasattr(d, 'is_offline') and d.is_offline))
        if elapsed:
            # Translators: Message after a manual refresh with duration.
            ui.message(_("Aktualisiert in {seconds}s - {online} Geräte online").format(
                seconds=f"{elapsed:.1f}", online=online))
        else:
            # Translators: Message after a manual refresh.
            ui.message(_("Aktualisiert - {online} Geräte online").format(online=online))
        
        # Set the focus to the tree
        self._focus_first_tree_item()
    
    def _reset_refresh_button(self):
        """Resets the refresh button"""
        self.refresh_btn.SetLabel("A&ktualisieren (F5)")
        self.refresh_btn.Enable(True)
    
    def _on_settings(self, event):
        """Opens the settings dialog"""
        from .settings_panel import MerossSettingsDialog

        dlg = MerossSettingsDialog(self, self.plugin)
        if dlg.ShowModal() == wx.ID_OK:
            # The settings were saved. Check whether Netatmo was newly
            # connected (tokens present, but the API not initialized)
            if (self.plugin.use_netatmo and
                self.plugin.netatmo_client_id and
                self.plugin.netatmo_refresh_token and
                not self.plugin.netatmo_api):
                # Initialize the Netatmo API
                try:
                    from .netatmo_api import NetatmoAPI
                    self.plugin.netatmo_api = NetatmoAPI(
                        self.plugin.netatmo_client_id,
                        self.plugin.netatmo_client_secret,
                        redirect_port=getattr(self.plugin, 'netatmo_redirect_port', 8474),
                    )
                    self.plugin.netatmo_api.set_tokens(
                        self.plugin.netatmo_access_token,
                        self.plugin.netatmo_refresh_token,
                        self.plugin.netatmo_token_expiry
                    )
                    log.info("Netatmo API aus Einstellungsdialog heraus initialisiert")
                except Exception as e:
                    log.error(f"Netatmo API Initialisierung fehlgeschlagen: {e}")

            # Reinitialize VeSync when enabled and credentials are present but
            # the API is not built yet (e.g. after enabling it in the
            # settings). The login runs in the background so the dialog does
            # not block; after a successful login the devices are loaded.
            if (self.plugin.use_vesync
                    and (self.plugin.vesync_email
                         and self.plugin._encrypted_vesync_password)
                    and not self.plugin.vesync_api):
                self._init_vesync_in_background()

            # Connect Cozytouch afterwards when freshly enabled and no API is
            # built yet (avoids the forced NVDA restart).
            if (self.plugin.use_cozytouch
                    and self.plugin.cozytouch_email
                    and self.plugin._encrypted_cozytouch_password
                    and not self.plugin.cozytouch_api):
                self._init_cozytouch_in_background()

            # Connect Meross afterwards when freshly (re-)enabled and no API is
            # built yet - otherwise the Meross devices would only appear after
            # an NVDA restart.
            if (self.plugin.use_meross
                    and self.plugin.email
                    and self.plugin._encrypted_password
                    and not self.plugin.api):
                self._init_meross_in_background()

            # Update the dialog
            if self.plugin.is_logged_in:
                self._on_refresh(None)
        dlg.Destroy()

