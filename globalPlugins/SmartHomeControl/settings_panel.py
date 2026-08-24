# -*- coding: utf-8 -*-
"""
Smart Home Control - Settings dialog with tabs (General / Meross / Netatmo / VeSync / Notifications)

"""

import wx
import ui
import re
import threading
from logHandler import log

from .constants import netatmo_redirect_uri, NETATMO_REDIRECT_PORT
from .platform_utils import PLATFORM_LABELS, PASSWORD_PLATFORMS

import addonHandler
try:
    addonHandler.initTranslation()
except Exception as e:
    log.debug(f"Ignored error during translation setup: {e}")
if "_" not in globals():  # fallback if initTranslation() fails
    # Without this fallback `_` stays undefined and the first `_()` call
    # raises a NameError mid-dialog instead of at import time.
    def _(s):
        return s


def is_credentials_error(error):
    """Was the login rejected because of the credentials, not the network?

    The distinction decides whether asking for the password again makes
    sense. A timeout during the automatic login at NVDA start (the WLAN is
    often not up yet) must not open a settings dialog.

    Decided by the error TYPE, never by its text: the messages are
    translated, so a check for "login" would work in English and be wrong in
    German. Our API layers raise ``CredentialsRejected`` (a ValueError) where
    the platform refused the credentials; Meross comes from a third-party
    library, whose ``BadLoginException`` can only be recognised by its class
    name.
    """
    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return False
    if "badlogin" in type(error).__name__.lower():
        return True
    # CredentialsRejected and the "email and password required" of the API
    # layers are both ValueErrors.
    return isinstance(error, ValueError)


def login_error_message(platform, error):
    """One line saying why the login failed, for status and speech."""
    label = PLATFORM_LABELS.get(platform, platform)
    if "badlogin" in type(error).__name__.lower():
        # Translators: Login error for wrong credentials. {platform} = brand
        # name (Meross etc.). The library's own English message is replaced
        # here, since it is not translatable.
        return _("{platform}: email address or password not accepted").format(
            platform=label)
    text = str(error)[:100]
    if text.lower().startswith(label.lower()):
        # The platform names itself already ("VeSync login failed: ..."),
        # prefixing it again would stutter.
        return text
    # Translators: Login error with the platform's own message. {platform} =
    # brand name, {error} = message from the platform.
    return _("{platform} login failed: {error}").format(
        platform=label, error=text)


class SmartHomeSettingsDialog(wx.Dialog):
    """Settings dialog with tabs for Meross, Netatmo and VeSync credentials.

    The notifications tab is rebuilt dynamically on every platform toggle
    (Meross/Netatmo/VeSync on/off) so platform changes become visible
    immediately without closing and reopening the dialog (used to be static).
    """

    # Tab index of each platform in the notebook, in the order the pages are
    # added in _create_ui(). on_ok jumps there on a validation error, and
    # ``focus_platform`` uses it to open the dialog straight at the platform
    # whose login just failed.
    _PLATFORM_TABS = {'meross': 1, 'netatmo': 2, 'vesync': 3, 'cozytouch': 4}

    def __init__(self, parent, plugin, focus_platform=None):
        super().__init__(
            parent,
            # Translators: Title of the settings dialog.
            title=_("Smart Home Control - Settings"),
            size=(620, 600),
        )

        self.plugin = plugin
        self._notify_checkboxes = {}
        # Platforms whose credentials were changed by this dialog. The caller
        # reads it after wx.ID_OK: a platform that is already logged in keeps
        # its session otherwise, so a new password would be stored but never
        # used - and a wrong one would stay unnoticed until the next NVDA
        # start.
        self.changed_platforms = set()
        # Set by Cancel/Escape. A credential check running in the background
        # asks for it before it saves anything.
        self._cancelled = False
        # The Netatmo OAuth flow runs on a background thread with a 120 s
        # timeout, as do the connection tests of the other platforms. If the
        # settings are closed meanwhile, the callback fired on a destroyed wx
        # object: "RuntimeError: wrapped C/C++ object of type StaticText has
        # been deleted". The same pattern as in the device dialog
        # (_safe_call_after + _is_destroyed) prevents that.
        self._is_destroyed = False
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_window_destroy)
        self._create_ui()
        if focus_platform:
            self._focus_platform(focus_platform)
        self.CenterOnScreen()

    def _password_ctrl(self, platform):
        """The secret input field of a platform (None if there is none)."""
        return {
            'meross': getattr(self, 'merossPasswordCtrl', None),
            'netatmo': getattr(self, 'netatmoSecretCtrl', None),
            'vesync': getattr(self, 'vesyncPasswordCtrl', None),
            'cozytouch': getattr(self, 'cozytouchPasswordCtrl', None),
        }.get(platform)

    def _focus_platform(self, platform):
        """Opens the platform's tab and puts the focus in its password field.

        Used after a failed login: the credentials can be typed again right
        away instead of having to find the tab first.
        """
        tab = self._PLATFORM_TABS.get(platform)
        if tab is None:
            return
        try:
            self.notebook.SetSelection(tab)
            ctrl = self._password_ctrl(platform)
            if ctrl:
                ctrl.SetFocus()
        except Exception as e:
            log.debug(f"Could not focus the {platform} tab: {e}")

    # ---- Password fields: placeholder logic ----
    # Stored passwords/secrets are NOT decrypted into the TextCtrl on opening
    # (TE_PASSWORD only masks the display; the plain text would otherwise sit
    # in memory for the lifetime of the dialog and be readable via GetValue).
    # The field stays empty instead, and an empty field means "keep the stored
    # password" when saving and testing.

    @staticmethod
    def _password_field_name(field, has_saved):
        """Accessible name of a password field, noting the stored value."""
        if has_saved:
            # Translators: Accessible name of a password field when a password
            # is already stored. {field} = field name.
            return _("{field} (saved - leave empty to keep it)").format(field=field)
        return field

    @staticmethod
    def _effective_secret(ctrl, stored):
        """The entered value, or the stored one if the field is empty."""
        value = ctrl.GetValue().strip()
        return value if value else (stored or "")

    def _on_window_destroy(self, event):
        # EVT_WINDOW_DESTROY also fires for child windows - only the
        # dialog's own destruction counts.
        if event.GetEventObject() is self:
            self._is_destroyed = True
        event.Skip()

    def _safe_call_after(self, func, *args, **kwargs):
        """wx.CallAfter that does nothing once the dialog has closed.

        Two checks, one when queueing and one when running: the dialog can be
        closed in between, and exactly that window causes the RuntimeError.
        """
        if self._is_destroyed:
            return

        def _run():
            if self._is_destroyed:
                return
            try:
                func(*args, **kwargs)
            except RuntimeError:
                # wx object destroyed between the check and the call.
                pass
        try:
            wx.CallAfter(_run)
        except Exception as e:
            log.debug(f"Ignored error in _safe_call_after: {e}")

    # =========================================================================
    # =
    # Build the UI
    # =========================================================================
    # =
    def _create_ui(self):
        mainSizer = wx.BoxSizer(wx.VERTICAL)

        # Notebook (tab control)
        self.notebook = wx.Notebook(self)
        # Translators: Accessible name for the tab switching control.
        self.notebook.SetName(_("View"))

        # ---- Tab 1: General ----
        self.tab_general = wx.Panel(self.notebook)
        self._create_general_tab(self.tab_general)
        # Translators: Tab name "General" (& marks the accelerator).
        self.notebook.AddPage(self.tab_general, _("&General"))

        # ---- Tab 2: Meross ----
        self.tab_meross = wx.Panel(self.notebook)
        self._create_meross_tab(self.tab_meross)
        # Translators: Tab name "Meross" (brand name, & marks the accelerator).
        self.notebook.AddPage(self.tab_meross, _("&Meross"))

        # ---- Tab 3: Netatmo ----
        self.tab_netatmo = wx.Panel(self.notebook)
        self._create_netatmo_tab(self.tab_netatmo)
        # Translators: Tab name "Netatmo".
        self.notebook.AddPage(self.tab_netatmo, _("&Netatmo"))

        # ---- Tab 4: VeSync (Levoit) ----
        self.tab_vesync = wx.Panel(self.notebook)
        self._create_vesync_tab(self.tab_vesync)
        # Translators: Tab name "VeSync".
        self.notebook.AddPage(self.tab_vesync, _("&VeSync"))

        # ---- Tab 5: Cozytouch (Atlantic / Austria Email) ----
        self.tab_cozytouch = wx.Panel(self.notebook)
        self._create_cozytouch_tab(self.tab_cozytouch)
        # Translators: Tab name "Cozytouch". Brand name, do not translate;
        # the parenthesis marks the platform as experimental.
        self.notebook.AddPage(self.tab_cozytouch, _("&Cozytouch (experimental)"))

        # ---- Tab 5: Notifications ----
        self.tab_notifications = wx.Panel(self.notebook)
        self._create_notifications_tab(self.tab_notifications)
        # Translators: Tab name "Notifications".
        self.notebook.AddPage(self.tab_notifications, _("&Notifications"))

        mainSizer.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 5)

        # ---- Status ----
        self.statusText = wx.StaticText(self, label="")
        self.statusText.SetName(_("Status"))
        mainSizer.Add(self.statusText, flag=wx.ALL, border=10)

        # ---- Buttons ----
        buttonSizer = wx.BoxSizer(wx.HORIZONTAL)

        # Translators: "Save" button in the settings dialog.
        okBtn = wx.Button(self, wx.ID_OK, _("&Save"))
        okBtn.Bind(wx.EVT_BUTTON, self.on_ok)
        okBtn.SetDefault()
        buttonSizer.Add(okBtn, flag=wx.RIGHT, border=5)

        # Translators: "Cancel" button in the settings dialog.
        cancelBtn = wx.Button(self, wx.ID_CANCEL, _("&Cancel"))
        # Own handler instead of the default behaviour: a credential check
        # started by "Save" may still be running, and its result must not
        # save anything after a cancel. Escape reaches this handler too - wx
        # sends the click of the wxID_CANCEL button for it.
        cancelBtn.Bind(wx.EVT_BUTTON, self.on_cancel)
        self.okBtn = okBtn
        buttonSizer.Add(cancelBtn)

        mainSizer.Add(buttonSizer, flag=wx.ALIGN_RIGHT | wx.ALL, border=10)

        self.SetSizer(mainSizer)
        self.notebook.SetFocus()

    # -------------------------------------------------------------------------
    # -
    # Tab: General
    # -------------------------------------------------------------------------
    # -
    def _create_general_tab(self, panel):
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Platform selection
        # Translators: Group label for selecting the active platforms.
        platformBox = wx.StaticBox(panel, label=_("Active platforms"))
        platformSizer = wx.StaticBoxSizer(platformBox, wx.VERTICAL)

        # Translators: Checkbox label for enabling the Meross platform.
        self.chkMeross = wx.CheckBox(panel, label=_("Use &Meross"))
        self.chkMeross.SetValue(self.plugin.use_meross)
        # Dynamically refresh the notifications tab when a platform is toggled
        self.chkMeross.Bind(wx.EVT_CHECKBOX, self._on_platform_toggle)
        platformSizer.Add(self.chkMeross, flag=wx.ALL, border=5)

        # Translators: Checkbox label for enabling the Netatmo platform.
        self.chkNetatmo = wx.CheckBox(panel, label=_("Use &Netatmo"))
        self.chkNetatmo.SetValue(self.plugin.use_netatmo)
        self.chkNetatmo.Bind(wx.EVT_CHECKBOX, self._on_platform_toggle)
        platformSizer.Add(self.chkNetatmo, flag=wx.ALL, border=5)

        # Translators: Checkbox label for enabling the VeSync platform (covers
        # Levoit, Cosori, Etekcity).
        self.chkVesync = wx.CheckBox(panel, label=_("Use &VeSync (Levoit, "
                                                    "Cosori, Etekcity)"))
        self.chkVesync.SetValue(getattr(self.plugin, 'use_vesync', False))
        self.chkVesync.Bind(wx.EVT_CHECKBOX, self._on_platform_toggle)
        platformSizer.Add(self.chkVesync, flag=wx.ALL, border=5)

        # Translators: Checkbox label for enabling the Cozytouch platform
        # (Atlantic / Austria Email hot water heat pumps). Marked as
        # experimental - only one device model has been tested so far.
        self.chkCozytouch = wx.CheckBox(panel, label=_(
            "Use &Cozytouch (Atlantic, Austria Email) - experimental"))
        self.chkCozytouch.SetValue(getattr(self.plugin, 'use_cozytouch', False))
        self.chkCozytouch.Bind(wx.EVT_CHECKBOX, self._on_platform_toggle)
        platformSizer.Add(self.chkCozytouch, flag=wx.ALL, border=5)

        sizer.Add(platformSizer, flag=wx.EXPAND | wx.ALL, border=10)

        # Auto login checkbox
        # Translators: Checkbox for enabling automatic login at NVDA start.
        self.autoLoginCheckbox = wx.CheckBox(panel, label=_("&Log in "
                                                            "automatically "
                                                            "when NVDA starts"))
        self.autoLoginCheckbox.SetValue(self.plugin.auto_login)
        sizer.Add(self.autoLoginCheckbox, flag=wx.ALL, border=10)

        # Start tab: which tab is active when the menu opens.
        startTabSizer = wx.BoxSizer(wx.HORIZONTAL)
        # Translators: Label before the start tab selection.
        startTabLabel = wx.StaticText(panel, label=_("Show on &open:"))
        startTabSizer.Add(startTabLabel, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        # Translators: The two choices for the start tab (device list / favorites).
        self._startTabValues = ['devices', 'favorites']
        self.startTabChoice = wx.Choice(
            panel, choices=[_("All devices"), _("Favorites")])
        current_tab = getattr(self.plugin, 'start_tab', 'devices')
        self.startTabChoice.SetSelection(
            1 if current_tab == 'favorites' else 0)
        startTabSizer.Add(self.startTabChoice, flag=wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(startTabSizer, flag=wx.ALL, border=10)

        from .constants import (
            FAV_LAYER_SWITCH_WINDOW_DEFAULT, FAV_LAYER_SWITCH_WINDOW_MIN,
            FAV_LAYER_SWITCH_WINDOW_MAX,
        )
        favSizer = wx.BoxSizer(wx.HORIZONTAL)
        # Translators: Label of the input field for how long a favorites
        # layer digit may still be pressed a second time to switch.
        favLabel = wx.StaticText(panel, label=_("Favorites layer: &switching "
                                                "press valid for (seconds):"))
        self.favSwitchWindowCtrl = wx.TextCtrl(
            panel, value=str(getattr(self.plugin, 'fav_layer_switch_window',
                                     FAV_LAYER_SWITCH_WINDOW_DEFAULT)))
        self.favSwitchWindowCtrl.SetName(_("Favorites layer switching window "
                                           "in seconds"))
        # Translators: Tooltip for the favorites layer switching window.
        self.favSwitchWindowCtrl.SetToolTip(_(
            "In the favorites layer a digit announces the status, and the "
            "same digit pressed again switches the device. This is how long "
            "after the announcement that second press still switches. Later "
            "presses announce the status again instead, so a digit pressed "
            "by accident long afterwards cannot switch anything off. "
            "Between {low} and {high}, default {default}."
        ).format(low=FAV_LAYER_SWITCH_WINDOW_MIN,
                 high=FAV_LAYER_SWITCH_WINDOW_MAX,
                 default=FAV_LAYER_SWITCH_WINDOW_DEFAULT))
        favSizer.Add(favLabel, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        favSizer.Add(self.favSwitchWindowCtrl)
        sizer.Add(favSizer, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        # Note
        # Translators: Hint text in the General tab.
        hintText = wx.StaticText(panel, label=_(
            "Note: Meross, Netatmo, VeSync and Cozytouch can be used "
            "individually or in combination.\nDisabled platforms are skipped "
            "during login.\nA successful connection test in the respective "
            "tab enables the platform automatically.\nCozytouch is "
            "experimental - only one hot water heat pump has been tested so "
            "far."
        ))
        sizer.Add(hintText, flag=wx.ALL, border=10)

        panel.SetSizer(sizer)

    def _on_platform_toggle(self, event):
        """Called when the user enables/disables Meross/Netatmo/VeSync.

        Rebuilds the notifications tab so disabled platforms immediately show
        the "not enabled" hint instead of the checkboxes.
        """
        if event is not None:
            event.Skip()
        try:
            # Remove the existing children of the notifications tab
            sizer = self.tab_notifications.GetSizer()
            if sizer:
                sizer.Clear(delete_windows=True)
                self.tab_notifications.SetSizer(None, deleteOld=True)
            self._notify_checkboxes = {}
            # Rebuild
            self._create_notifications_tab(self.tab_notifications)
            self.tab_notifications.Layout()
        except Exception as e:
            log.debug(f"Could not rebuild the notifications tab: {e}")

    def _auto_enable_platform(self, checkbox, label):
        """After a successful test/connect, enables the corresponding
        'use' checkbox (if still off) and announces it.

        Fixes the most common pitfall: the user enters credentials in a
        platform tab and tests successfully but forgets to enable the platform
        in the General tab. A platform that is not enabled would neither be
        logged in on save nor shown in the device tree. This way every
        successful test directly becomes an activation - several platforms can
        be tested one after the other and are all logged in together.

        Must run on the main thread (wx controls) - callers use wx.CallAfter.
        """
        try:
            if checkbox.GetValue():
                return  # already active - nothing to do
            checkbox.SetValue(True)
            # The notifications tab mirrors the newly active platform.
            self._on_platform_toggle(None)
            # Translators: Note that a platform was enabled automatically after
            # a successful test. {platform} = brand name (Meross etc.).
            ui.message(_("{platform} enabled – login will happen on save").format(
                platform=label))
        except Exception as e:
            log.debug(f"Auto-enabling the platform failed: {e}")

    # -------------------------------------------------------------------------
    # -
    # Tab: Meross
    # -------------------------------------------------------------------------
    # -
    def _create_meross_tab(self, panel):
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Translators: Explanatory text in the Meross tab.
        infoText = wx.StaticText(panel, label=_(
            "Meross account credentials.\nThese are stored encrypted locally "
            "on this computer."
        ))
        sizer.Add(infoText, flag=wx.ALL, border=10)

        formSizer = wx.FlexGridSizer(cols=2, hgap=10, vgap=10)
        formSizer.AddGrowableCol(1)

        # Email
        # Translators: Label of the email input field (Meross).
        emailLabel = wx.StaticText(panel, label=_("&Email:"))
        self.merossEmailCtrl = wx.TextCtrl(panel, value=self.plugin.email)
        self.merossEmailCtrl.SetName(_("Meross email address"))
        formSizer.Add(emailLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.merossEmailCtrl, flag=wx.EXPAND)

        # Password
        # Translators: Label of the password input field (Meross).
        passwordLabel = wx.StaticText(panel, label=_("&Password:"))
        self.merossPasswordCtrl = wx.TextCtrl(
            panel, value="", style=wx.TE_PASSWORD)
        self.merossPasswordCtrl.SetName(self._password_field_name(
            _("Meross password"), bool(self.plugin.password)))
        formSizer.Add(passwordLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.merossPasswordCtrl, flag=wx.EXPAND)

        sizer.Add(formSizer, flag=wx.EXPAND | wx.ALL, border=10)

        # Test button
        # Translators: Button for testing the Meross connection.
        self.merossTestBtn = wx.Button(panel, label=_("&Test connection"))
        self.merossTestBtn.SetName(_("Test Meross connection"))
        self.merossTestBtn.Bind(wx.EVT_BUTTON, self.on_test_meross)
        sizer.Add(self.merossTestBtn, flag=wx.ALL, border=10)

        # Announce external changes (Meross-relevant only)
        # Translators: Checkbox: NVDA should announce external switching
        # (Alexa, app).
        self.announceExternalCheckbox = wx.CheckBox(panel, label=_(
            "&Announce external changes (Alexa, Meross app, etc.)"
        ))
        self.announceExternalCheckbox.SetValue(
            getattr(self.plugin, 'announce_external_changes', True))
        # Translators: Tooltip explanation of the external changes checkbox.
        self.announceExternalCheckbox.SetToolTip(_(
            "If enabled, NVDA will announce when a Meross device is switched "
            "via Alexa, the Meross app or other external sources."
        ))
        sizer.Add(self.announceExternalCheckbox, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        panel.SetSizer(sizer)

    # -------------------------------------------------------------------------
    # -
    # Tab: Netatmo
    # -------------------------------------------------------------------------
    # -
    def _create_netatmo_tab(self, panel):
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Translators: Explanatory text in the Netatmo tab. Mentions
        # dev.netatmo.com.
        infoText = wx.StaticText(panel, label=_(
            "Netatmo OAuth2 credentials.\nCreate an app at "
            "https://dev.netatmo.com,\nenter the client ID and secret here, "
            "and register the redirect URI\nshown below with the Netatmo app "
            "(exactly identical)."
        ))
        sizer.Add(infoText, flag=wx.ALL, border=10)

        formSizer = wx.FlexGridSizer(cols=2, hgap=10, vgap=10)
        formSizer.AddGrowableCol(1)

        # Translators: Label of the client ID field for Netatmo OAuth2.
        idLabel = wx.StaticText(panel, label=_("Client &ID:"))
        self.netatmoIdCtrl = wx.TextCtrl(
            panel, value=getattr(self.plugin, 'netatmo_client_id', ''))
        self.netatmoIdCtrl.SetName(_("Netatmo client ID"))
        formSizer.Add(idLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.netatmoIdCtrl, flag=wx.EXPAND)

        # Translators: Label of the client secret field for Netatmo OAuth2.
        secretLabel = wx.StaticText(panel, label=_("Client &secret:"))
        self.netatmoSecretCtrl = wx.TextCtrl(
            panel, value="", style=wx.TE_PASSWORD)
        self.netatmoSecretCtrl.SetName(self._password_field_name(
            _("Netatmo client secret"),
            bool(getattr(self.plugin, 'netatmo_client_secret', ''))))
        formSizer.Add(secretLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.netatmoSecretCtrl, flag=wx.EXPAND)

        # Translators: Label of the port field for the local OAuth2 callback.
        portLabel = wx.StaticText(panel, label=_("Redirect &port:"))
        self.netatmoPortCtrl = wx.SpinCtrl(
            panel, min=1024, max=65535,
            initial=int(getattr(self.plugin, 'netatmo_redirect_port', NETATMO_REDIRECT_PORT)))
        self.netatmoPortCtrl.SetName(_("Netatmo redirect port"))
        self.netatmoPortCtrl.Bind(wx.EVT_SPINCTRL, self._on_netatmo_port_changed)
        self.netatmoPortCtrl.Bind(wx.EVT_TEXT, self._on_netatmo_port_changed)
        formSizer.Add(portLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.netatmoPortCtrl, flag=wx.EXPAND)

        sizer.Add(formSizer, flag=wx.EXPAND | wx.ALL, border=10)

        # Display of the redirect URI to register exactly (read-only, focusable
        # via TE_READONLY so the screen reader can read/copy it).
        # Translators: Label before the displayed redirect URI.
        uriLabel = wx.StaticText(panel, label=_(
            "Register this redirect URI at dev.netatmo.com:"))
        sizer.Add(uriLabel, flag=wx.LEFT | wx.TOP, border=10)
        self.netatmoUriCtrl = wx.TextCtrl(
            panel,
            value=netatmo_redirect_uri(self.netatmoPortCtrl.GetValue()),
            style=wx.TE_READONLY)
        self.netatmoUriCtrl.SetName(_("Netatmo redirect URI"))
        sizer.Add(self.netatmoUriCtrl, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        # OAuth connect button
        # Translators: Button starts the OAuth2 flow in the browser.
        self.netatmoConnectBtn = wx.Button(panel, label=_("Connect to "
                                                          "&Netatmo (OAuth2)"))
        self.netatmoConnectBtn.SetName(_("Connect to Netatmo"))
        self.netatmoConnectBtn.Bind(wx.EVT_BUTTON, self.on_connect_netatmo)
        sizer.Add(self.netatmoConnectBtn, flag=wx.ALL, border=10)

        # Connection status
        has_tokens = (
            getattr(self.plugin, 'netatmo_refresh_token', '') != ''
            and getattr(self.plugin, 'netatmo_access_token', '') != ''
        )
        # Translators: Netatmo status display "Connected" / "Not connected".
        status_label = _("Status: connected") if has_tokens else _("Status: "
                                                                   "not "
                                                                   "connected")
        self.netatmoStatusLabel = wx.StaticText(panel, label=status_label)
        self.netatmoStatusLabel.SetName(_("Netatmo connection status"))
        sizer.Add(self.netatmoStatusLabel, flag=wx.ALL, border=10)

        # Test button
        self.netatmoTestBtn = wx.Button(panel, label=_("&Test connection"))
        self.netatmoTestBtn.SetName(_("Test Netatmo connection"))
        self.netatmoTestBtn.Bind(wx.EVT_BUTTON, self.on_test_netatmo)
        sizer.Add(self.netatmoTestBtn, flag=wx.ALL, border=10)

        panel.SetSizer(sizer)

    def _on_netatmo_port_changed(self, event):
        """Keeps the displayed redirect URI in sync with the chosen port."""
        try:
            self.netatmoUriCtrl.SetValue(
                netatmo_redirect_uri(self.netatmoPortCtrl.GetValue()))
        except Exception as e:
            log.debug(f"Ignored error in _on_netatmo_port_changed: {e}")
        event.Skip()

    # -------------------------------------------------------------------------
    # -
    # Tab: VeSync (Levoit, Cosori, Etekcity)
    # -------------------------------------------------------------------------
    # -
    def _create_vesync_tab(self, panel):
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Translators: Explanatory text in the VeSync tab.
        infoText = wx.StaticText(panel, label=_(
            "VeSync account credentials (e.g. Levoit, Cosori, "
            "Etekcity).\nCurrently supported: Levoit Core "
            "200S/300S/400S/500S/600S air purifiers and Levoit tower "
            "fans.\nData is stored encrypted locally on this computer."
        ))
        sizer.Add(infoText, flag=wx.ALL, border=10)

        formSizer = wx.FlexGridSizer(cols=2, hgap=10, vgap=10)
        formSizer.AddGrowableCol(1)

        # Translators: Label of the email field for VeSync.
        emailLabel = wx.StaticText(panel, label=_("V&eSync email:"))
        self.vesyncEmailCtrl = wx.TextCtrl(
            panel, value=getattr(self.plugin, 'vesync_email', ''))
        self.vesyncEmailCtrl.SetName(_("VeSync email address"))
        formSizer.Add(emailLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.vesyncEmailCtrl, flag=wx.EXPAND)

        # Translators: Label of the password field for VeSync.
        passwordLabel = wx.StaticText(panel, label=_("VeSync pass&word:"))
        self.vesyncPasswordCtrl = wx.TextCtrl(
            panel, value="", style=wx.TE_PASSWORD)
        self.vesyncPasswordCtrl.SetName(self._password_field_name(
            _("VeSync password"),
            bool(getattr(self.plugin, 'vesync_password', ''))))
        formSizer.Add(passwordLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.vesyncPasswordCtrl, flag=wx.EXPAND)

        # Translators: Label of the country code field (ISO 3166-1 alpha-2).
        countryLabel = wx.StaticText(panel, label=_("&Country code (ISO):"))
        self.vesyncCountryCtrl = wx.TextCtrl(
            panel,
            value=getattr(self.plugin, 'vesync_country_code', 'DE') or 'DE')
        self.vesyncCountryCtrl.SetName(_("VeSync country code"))
        # Translators: Tooltip for the country code field.
        self.vesyncCountryCtrl.SetToolTip(_(
            "Two-letter country code (ISO 3166-1 alpha-2). Examples: DE, AT, "
            "CH, US, GB, FR. Automatically determines the VeSync server "
            "region (EU or US)."
        ))
        formSizer.Add(countryLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.vesyncCountryCtrl, flag=wx.EXPAND)

        # Translators: Label of the input field for the filter warning
        # threshold in percent.
        filterLabel = wx.StaticText(panel, label=_("&Filter warning threshold "
                                                   "(%):"))
        self.vesyncFilterThresholdCtrl = wx.TextCtrl(
            panel, value=str(getattr(self.plugin, 'vesync_filter_threshold', 15)))
        self.vesyncFilterThresholdCtrl.SetName(_("VeSync filter warning "
                                                 "threshold in percent"))
        # Translators: Tooltip for the filter warning threshold field.
        self.vesyncFilterThresholdCtrl.SetToolTip(_(
            "If an air purifier's remaining filter life drops to or below "
            "this percentage, a filter warning appears at the top of the "
            "device dialog and is announced once when crossing the threshold. "
            "Default: 15."
        ))
        formSizer.Add(filterLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.vesyncFilterThresholdCtrl, flag=wx.EXPAND)

        sizer.Add(formSizer, flag=wx.EXPAND | wx.ALL, border=10)

        # Translators: "Test VeSync connection" button.
        self.vesyncTestBtn = wx.Button(panel, label=_("Test VeSync &connection"))
        self.vesyncTestBtn.SetName(_("Test VeSync connection"))
        self.vesyncTestBtn.Bind(wx.EVT_BUTTON, self.on_test_vesync)
        sizer.Add(self.vesyncTestBtn, flag=wx.ALL, border=10)

        panel.SetSizer(sizer)

    # -------------------------------------------------------------------------
    # -
    # Tab: Cozytouch (Atlantic / Austria Email)
    # -------------------------------------------------------------------------
    # -
    def _create_cozytouch_tab(self, panel):
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Translators: Explanatory text in the Cozytouch tab. Leading line
        # marks the platform as experimental.
        infoText = wx.StaticText(panel, label=_(
            "EXPERIMENTAL: the Cozytouch integration is reverse-engineered "
            "and has so far only been tested with one hot water heat pump "
            "(Austria Email Revolution Evo 3).\nOther Cozytouch devices "
            "(radiators, air conditioners) may be detected or presented "
            "incorrectly; individual functions can stop working without "
            "warning.\n\nCozytouch account credentials (Atlantic group, e.g. "
            "Austria Email).\nSame credentials as in the Cozytouch app. Data "
            "is stored encrypted locally on this computer."
        ))
        sizer.Add(infoText, flag=wx.ALL, border=10)

        formSizer = wx.FlexGridSizer(cols=2, hgap=10, vgap=10)
        formSizer.AddGrowableCol(1)

        # Translators: Label of the email field for Cozytouch.
        emailLabel = wx.StaticText(panel, label=_("Cozytouch &email:"))
        self.cozytouchEmailCtrl = wx.TextCtrl(
            panel, value=getattr(self.plugin, 'cozytouch_email', ''))
        self.cozytouchEmailCtrl.SetName(_("Cozytouch email address"))
        formSizer.Add(emailLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.cozytouchEmailCtrl, flag=wx.EXPAND)

        # Translators: Label of the password field for Cozytouch.
        passwordLabel = wx.StaticText(panel, label=_("Cozytouch pass&word:"))
        self.cozytouchPasswordCtrl = wx.TextCtrl(
            panel, value="", style=wx.TE_PASSWORD)
        self.cozytouchPasswordCtrl.SetName(self._password_field_name(
            _("Cozytouch password"),
            bool(getattr(self.plugin, 'cozytouch_password', ''))))
        formSizer.Add(passwordLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.cozytouchPasswordCtrl, flag=wx.EXPAND)

        # Translators: Label for the optional rated capacity of the hot water
        # tank.
        capacityLabel = wx.StaticText(panel, label=_("Rated &capacity "
                                                     "(liters, 0 = off):"))
        self.cozytouchCapacityCtrl = wx.TextCtrl(
            panel, value=str(getattr(self.plugin, 'cozytouch_capacity_liters', 0) or 0))
        self.cozytouchCapacityCtrl.SetName(_("Rated capacity of the hot water "
                                             "tank in liters"))
        # Translators: Tooltip for the rated capacity field.
        self.cozytouchCapacityCtrl.SetToolTip(_(
            "Total rated tank capacity in liters (e.g. 300). Used to estimate "
            "an approximate liter amount from the hot water percentage. 0 = "
            "no liter display."
        ))
        formSizer.Add(capacityLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.cozytouchCapacityCtrl, flag=wx.EXPAND)

        sizer.Add(formSizer, flag=wx.EXPAND | wx.ALL, border=10)

        # Translators: "Test Cozytouch connection" button.
        self.cozytouchTestBtn = wx.Button(panel, label=_("Test Cozytouch "
                                                         "&connection"))
        self.cozytouchTestBtn.SetName(_("Test Cozytouch connection"))
        self.cozytouchTestBtn.Bind(wx.EVT_BUTTON, self.on_test_cozytouch)
        sizer.Add(self.cozytouchTestBtn, flag=wx.ALL, border=10)

        panel.SetSizer(sizer)

    # -------------------------------------------------------------------------
    # -
    # Tab: Notifications (dynamic, depends on the enabled platforms)
    # -------------------------------------------------------------------------
    # -
    def _create_notifications_tab(self, panel):
        """Builds the notifications tab.

        Also called again on a platform toggle (General tab) so disabled
        platforms immediately show the hint text instead of the checkboxes.
        self._notify_checkboxes is reset in the process.
        """
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Translators: Explanatory text at the top of the notifications tab.
        info = wx.StaticText(panel, label=_(
            "Sets which external changes NVDA announces. The choice applies "
            "to all devices of that platform.\nNote: only sections for "
            "platforms enabled in the \"General\" tab are shown as active."
        ))
        sizer.Add(info, flag=wx.ALL, border=10)

        self._notify_checkboxes = {}

        def add_section(box_label, platform_short, items, platform_active):
            """Adds a platform box with checkboxes or a hint.

            Args:
                box_label: label of the StaticBox (e.g. "Meross").
                platform_short: short platform name for the disabled hint.
                items: list of (attr_name, label, default) tuples.
                platform_active: True if the platform is enabled.
            """
            box = wx.StaticBox(panel, label=box_label)
            box_sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
            if not platform_active:
                # Translators: Note in the notifications tab that a disabled
                # platform offers no configurable notifications. {name} =
                # platform name (Meross / Netatmo / VeSync).
                hint = wx.StaticText(panel, label=_(
                    "{name} is not enabled. Enable the platform in the "
                    "\"General\" tab to configure its notifications."
                ).format(name=platform_short))
                box_sizer.Add(hint, flag=wx.ALL, border=5)
            else:
                for attr_name, label, default in items:
                    cb = wx.CheckBox(panel, label=label)
                    cb.SetValue(getattr(self.plugin, attr_name, default))
                    box_sizer.Add(cb, flag=wx.ALL, border=4)
                    self._notify_checkboxes[attr_name] = cb
            sizer.Add(box_sizer, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        # Meross
        add_section(
            "Meross",
            "Meross",
            [
                ('notify_meross_toggle',
                 # Translators: Notification option: announce Meross on/off
                 # switching.
                 _("Announce on/off switching (Alexa, Meross app, ...)"),
                 True),
                ('notify_meross_water',
                 # Translators: Notification option: announce the water sensor
                 # alarm.
                 _("Announce water alarm of the water sensors"),
                 True),
            ],
            self.chkMeross.GetValue(),
        )

        # Netatmo
        add_section(
            "Netatmo",
            "Netatmo",
            [
                ('notify_netatmo_mode',
                 # Translators: Announce Netatmo heating mode changes.
                 _("Announce heating mode changes (schedule / away / frost "
                   "guard)"),
                 True),
                ('notify_netatmo_setpoint',
                 # Translators: Announce Netatmo target temperature changes.
                 _("Announce target temperature changes"),
                 True),
                ('notify_netatmo_boiler',
                 # Translators: Announce Netatmo heating start/stop.
                 _("Announce heating start and stop (boiler status)"),
                 True),
                ('notify_netatmo_open_window',
                 # Translators: Announce Netatmo "open window" detection.
                 _("Announce open window detection"),
                 True),
                ('notify_netatmo_anticipation',
                 # Translators: Announce Netatmo pre-heating (default: off).
                 _("Announce pre-heating (may occur frequently)"),
                 False),
            ],
            self.chkNetatmo.GetValue(),
        )

        # VeSync
        add_section(
            "VeSync (Levoit)",
            "VeSync",
            [
                ('notify_vesync_toggle',
                 # Translators: Announce VeSync on/off switching.
                 _("Announce on/off switching"),
                 True),
                ('notify_vesync_mode',
                 # Translators: Announce VeSync mode changes.
                 _("Announce mode changes (auto / manual / sleep / ...)"),
                 True),
                ('notify_vesync_fan_speed',
                 # Translators: Announce VeSync fan level changes.
                 _("Announce fan speed changes"),
                 True),
                ('notify_vesync_air_quality',
                 # Translators: Announce VeSync air quality changes.
                 _("Announce air quality changes (air purifiers)"),
                 True),
                ('notify_vesync_filter',
                 # Translators: Announce the VeSync filter life warning.
                 _("Announce filter life warnings"),
                 True),
            ],
            self.chkVesync.GetValue(),
        )

        # Cozytouch (Atlantic / Austria Email)
        add_section(
            # Translators: Group box for the Cozytouch notifications. Brand
            # name plus the experimental marker.
            _("Cozytouch (experimental)"),
            "Cozytouch",
            [
                ('notify_cozytouch_power',
                 # Translators: Announce Cozytouch hot water operation on/off.
                 _("Announce on/off switching (hot water operation)"),
                 True),
                ('notify_cozytouch_temp',
                 # Translators: Announce Cozytouch target temperature changes.
                 _("Announce target temperature changes"),
                 True),
                ('notify_cozytouch_mode',
                 # Translators: Announce Cozytouch heating mode changes.
                 _("Announce heating mode changes (manual / Eco+ / program)"),
                 True),
                ('notify_cozytouch_boost',
                 # Translators: Announce Cozytouch boost switching.
                 _("Announce boost on/off"),
                 True),
                ('notify_cozytouch_away',
                 # Translators: Announce the Cozytouch away mode.
                 _("Announce away mode on/off"),
                 True),
            ],
            self.chkCozytouch.GetValue(),
        )

        panel.SetSizer(sizer)

    # =========================================================================
    # =
    # Event handlers
    # =========================================================================
    # =
    def on_test_meross(self, event):
        """Tests the Meross connection."""
        email = self.merossEmailCtrl.GetValue().strip()
        password = self._effective_secret(self.merossPasswordCtrl, self.plugin.password)

        if not email or not password:
            # Translators: Validation error in the Meross tab.
            self._set_status(_("Please enter Meross email and password"), error=True)
            ui.message(_("Please enter email and password"))
            return

        def test_task(_email, _password):
            try:
                # Translators: Status while the connection test is running.
                self._safe_call_after(self._set_status, _("Testing Meross "
                                                          "connection..."))
                self._safe_call_after(self._safe_button_disable, self.merossTestBtn)
                # Translators: Speech output during the connection test.
                self._safe_call_after(ui.message, _("Connecting to Meross..."))

                from .meross_api import MerossAPI

                api = MerossAPI()
                try:
                    api.login(_email, _password)
                finally:
                    _password = None

                self._safe_call_after(ui.message, _("Loading devices..."))
                devices = api.get_devices()
                api.logout()

                self._safe_call_after(
                    self._set_status,
                    # Translators: Success message of the Meross connection
                    # test. {count}=count.
                    _("Meross: connected – {count} device(s)").format(count=len(devices)),
                    error=False,
                )
                self._safe_call_after(self._auto_enable_platform, self.chkMeross, "Meross")

            except Exception as e:
                log.error(f"Meross connection test failed: {type(e).__name__}: {e}")
                error_msg = str(e)[:100]
                self._safe_call_after(
                    self._set_status,
                    # Translators: Error message of the Meross connection test.
                    # {error}=detail.
                    _("Meross error: {error}").format(error=error_msg), error=True)
                self._safe_call_after(
                    ui.message,
                    _("Meross error: {error}").format(error=error_msg))
            finally:
                _password = None
                self._safe_call_after(self._safe_button_enable, self.merossTestBtn)

        threading.Thread(target=test_task, args=(email, password), daemon=True).start()
        # Release the local password in the outer scope
        password = None
        del password

    def on_connect_netatmo(self, event):
        """Starts the Netatmo OAuth2 flow."""
        client_id = self.netatmoIdCtrl.GetValue().strip()
        client_secret = self._effective_secret(
            self.netatmoSecretCtrl, getattr(self.plugin, 'netatmo_client_secret', ''))
        # Read the port on the main thread (wx controls are not thread-safe);
        # pass it on to the OAuth coroutine in the background thread. Store it
        # in the plugin immediately so the token about to be saved belongs to
        # the same port - even if the user later leaves the dialog with Cancel.
        redirect_port = self.netatmoPortCtrl.GetValue()
        self.plugin.netatmo_redirect_port = redirect_port

        if not client_id or not client_secret:
            # Translators: Validation error in the Netatmo OAuth2 tab.
            self._set_status(_("Please enter Netatmo client ID and secret"), error=True)
            ui.message(_("Please enter client ID and secret"))
            return

        # Confirmation dialog before redirecting to the browser
        confirm = wx.MessageDialog(
            self,
            # Translators: Confirmation dialog before the OAuth2 browser flow.
            _("The Netatmo sign-in now opens in the browser.\n\nSign in there "
              "and authorise the app.\nAfter authorisation the redirect "
              "happens automatically.\n\nContinue?"),
            # Translators: Title of the OAuth2 confirmation dialog.
            _("Netatmo authorization"),
            wx.YES_NO | wx.ICON_INFORMATION,
        )
        # Translators: "Continue" button in the OAuth2 confirmation dialog.
        # Translators: "Cancel" button in the OAuth2 confirmation dialog.
        confirm.SetYesNoLabels(_("&Continue"), _("&Cancel"))
        # ESC confirms "Cancel" - protects against accidentally opening the
        # browser.
        try:
            confirm.SetEscapeId(wx.ID_NO)
        except Exception as e:
            log.debug(f"Ignored error in on_connect_netatmo: {e}")
        result = confirm.ShowModal()
        confirm.Destroy()

        if result != wx.ID_YES:
            # Translators: Announcement when the user cancels the OAuth2 flow.
            ui.message(_("Netatmo connection cancelled"))
            return

        def oauth_task():
            try:
                self._safe_call_after(
                    self._set_status,
                    # Translators: Status while the browser is opened for
                    # OAuth2.
                    _("Opening browser for Netatmo authorization..."),
                )
                self._safe_call_after(self._safe_button_disable, self.netatmoConnectBtn)
                self._safe_call_after(ui.message, _(
                    "Opening browser. Please sign in to Netatmo and authorize."))

                from .netatmo_api import NetatmoAPI

                api = NetatmoAPI(client_id, client_secret, redirect_port=redirect_port)
                api.start_oauth_flow(timeout=120)

                # Store the tokens in the plugin
                tokens = api.get_tokens()
                self.plugin.netatmo_access_token = tokens['access_token']
                self.plugin.netatmo_refresh_token = tokens['refresh_token']
                self.plugin.netatmo_token_expiry = tokens['token_expiry']

                self._safe_call_after(self._set_status, _("Netatmo: connected"), error=False)
                self._safe_call_after(self.netatmoStatusLabel.SetLabel, _("Status: "
                                                                          "connected"))
                self._safe_call_after(self._auto_enable_platform, self.chkNetatmo, "Netatmo")
                self._safe_call_after(ui.message, _("Netatmo connected"))

            except Exception as e:
                log.error(f"Netatmo OAuth failed: {type(e).__name__}: {e}")
                error_msg = str(e)[:100]
                self._safe_call_after(
                    self._set_status,
                    _("Netatmo error: {error}").format(error=error_msg), error=True)
                self._safe_call_after(
                    ui.message,
                    # Translators: Speech output on OAuth2 error.
                    _("Netatmo: connection failed – {error}").format(error=error_msg))
            finally:
                self._safe_call_after(self._safe_button_enable, self.netatmoConnectBtn)

        threading.Thread(target=oauth_task, daemon=True).start()

    def on_test_netatmo(self, event):
        """Tests the Netatmo connection with the existing tokens."""
        if not getattr(self.plugin, 'netatmo_access_token', ''):
            # Translators: Validation hint: not yet connected via OAuth2.
            self._set_status(_("Please connect to Netatmo first"), error=True)
            ui.message(_("Please connect to Netatmo first (OAuth2)"))
            return

        client_id = self.netatmoIdCtrl.GetValue().strip()
        client_secret = self._effective_secret(
            self.netatmoSecretCtrl, getattr(self.plugin, 'netatmo_client_secret', ''))

        def test_task():
            try:
                self._safe_call_after(self._set_status, _("Testing Netatmo "
                                                          "connection..."))
                self._safe_call_after(self._safe_button_disable, self.netatmoTestBtn)
                self._safe_call_after(ui.message, _("Testing Netatmo..."))

                from .netatmo_api import NetatmoAPI

                api = NetatmoAPI(client_id, client_secret)
                api.set_tokens(
                    self.plugin.netatmo_access_token,
                    self.plugin.netatmo_refresh_token,
                    getattr(self.plugin, 'netatmo_token_expiry', 0),
                )
                # The test can trigger a token renewal, and Netatmo rotates
                # refresh tokens. Without this callback the new token would
                # die with this throwaway API object while the stored one is
                # already invalid - the test would break the connection it is
                # supposed to check.
                api.set_token_update_callback(
                    self.plugin._on_netatmo_tokens_renewed)

                devices = api.get_devices()

                self._safe_call_after(
                    self._set_status,
                    _("Netatmo: {count} device(s) found").format(count=len(devices)),
                    error=False)
                self._safe_call_after(self._auto_enable_platform, self.chkNetatmo, "Netatmo")
                self._safe_call_after(
                    ui.message,
                    _("Netatmo: {count} device(s)").format(count=len(devices)))

            except Exception as e:
                log.error(f"Netatmo test failed: {type(e).__name__}: {e}")
                error_msg = str(e)[:100]
                self._safe_call_after(
                    self._set_status,
                    _("Netatmo error: {error}").format(error=error_msg), error=True)
                self._safe_call_after(
                    ui.message,
                    _("Netatmo error: {error}").format(error=error_msg))
            finally:
                self._safe_call_after(self._safe_button_enable, self.netatmoTestBtn)

        threading.Thread(target=test_task, daemon=True).start()

    def on_test_vesync(self, event):
        """Tests the VeSync connection."""
        email = self.vesyncEmailCtrl.GetValue().strip()
        password = self._effective_secret(
            self.vesyncPasswordCtrl, getattr(self.plugin, 'vesync_password', ''))
        country = (self.vesyncCountryCtrl.GetValue() or "DE").strip().upper()

        if not email or not password:
            # Translators: Validation error in the VeSync tab.
            self._set_status(_("Please enter VeSync email and password"), error=True)
            ui.message(_("Please enter email and password"))
            return

        if len(country) != 2:
            # Translators: Validation error for the country code (must be 2
            # letters).
            self._set_status(_("Country code must be two letters (e.g. DE)"), error=True)
            ui.message(_("Invalid country code"))
            return

        def test_task(_email, _password, _country):
            try:
                self._safe_call_after(self._set_status, _("Testing VeSync "
                                                          "connection..."))
                self._safe_call_after(self._safe_button_disable, self.vesyncTestBtn)
                self._safe_call_after(ui.message, _("Connecting to VeSync..."))

                from .vesync_api import VeSyncAPI

                api = VeSyncAPI(country_code=_country)
                try:
                    api.login(_email, _password)
                finally:
                    _password = None

                self._safe_call_after(ui.message, _("Loading VeSync devices..."))
                devices = api.get_devices()

                # Save the tokens even without an OK click so the login works
                # faster
                creds = api.get_credentials()
                if creds["token"] and creds["account_id"]:
                    self.plugin.vesync_token = creds["token"]
                    self.plugin.vesync_account_id = creds["account_id"]
                    self.plugin.vesync_country_code = creds["country_code"]
                    self.plugin.vesync_region = creds["region"]
                supported, total, unsupported = api.device_summary()

                # Devices that are shown but not yet operated: ask them, once,
                # which status calls they answer. Only with debug logging on -
                # somebody who set that level and pressed "Test" is
                # diagnosing; for everyone else these would be a handful of
                # pointless round trips on every test.
                import logging
                if log.isEnabledFor(logging.DEBUG):
                    for dev in devices:
                        if dev.get_status_method() is not None:
                            continue
                        self._safe_call_after(
                            self._set_status,
                            # Translators: Status while an unsupported device
                            # is asked what it reports. {name} = device name.
                            _("Asking {name} what it reports...").format(
                                name=dev.name))
                        api.probe_status_methods(dev)
                api.logout()

                # An account whose devices are all of an unknown type used to
                # come out as "0 devices" - which is what a wrong password
                # looks like too, and sends the reader off hunting in the
                # wrong place. Naming the count and the model says what
                # actually happened.
                if unsupported:
                    # Translators: Result of the connection test when the
                    # account holds devices the add-on does not support.
                    # {supported}/{total} = number of devices, {types} =
                    # model designations, comma separated.
                    message = _("VeSync: connected – {supported} of {total} "
                                "device(s) supported. Not shown: {types}").format(
                        supported=supported, total=total,
                        types=", ".join(unsupported))
                else:
                    message = _("VeSync: connected – {count} device(s)").format(
                        count=len(devices))
                # _set_status speaks the text itself; a second ui.message
                # said the same thing again right afterwards.
                self._safe_call_after(self._set_status, message, error=False)
                self._safe_call_after(self._auto_enable_platform, self.chkVesync, "VeSync")

            except Exception as e:
                log.error(f"VeSync connection test failed: {type(e).__name__}: {e}")
                error_msg = str(e)[:100]
                self._safe_call_after(
                    self._set_status,
                    _("VeSync error: {error}").format(error=error_msg), error=True)
                self._safe_call_after(
                    ui.message,
                    _("VeSync error: {error}").format(error=error_msg))
            finally:
                _password = None
                self._safe_call_after(self._safe_button_enable, self.vesyncTestBtn)

        threading.Thread(target=test_task, args=(email, password, country), daemon=True).start()
        password = None
        del password

    def on_test_cozytouch(self, event):
        """Tests the Cozytouch connection."""
        email = self.cozytouchEmailCtrl.GetValue().strip()
        password = self._effective_secret(
            self.cozytouchPasswordCtrl, getattr(self.plugin, 'cozytouch_password', ''))

        if not email or not password:
            # Translators: Validation error in the Cozytouch tab.
            self._set_status(_("Please enter Cozytouch email and password"), error=True)
            ui.message(_("Please enter email and password"))
            return

        def test_task(_email, _password):
            try:
                self._safe_call_after(self._set_status, _("Testing Cozytouch "
                                                          "connection..."))
                self._safe_call_after(self._safe_button_disable, self.cozytouchTestBtn)
                self._safe_call_after(ui.message, _("Connecting to "
                                                    "Cozytouch..."))

                from .cozytouch_api import CozytouchAPI

                api = CozytouchAPI()
                try:
                    api.login(_email, _password)
                finally:
                    _password = None

                self._safe_call_after(ui.message, _("Loading Cozytouch "
                                                    "devices..."))
                devices = api.get_devices()

                # Save the token even without an OK click so the login works
                # faster
                creds = api.get_credentials()
                if creds.get("token"):
                    self.plugin.cozytouch_token = creds["token"]
                api.logout()

                self._safe_call_after(
                    self._set_status,
                    _("Cozytouch: connected – {count} device(s)").format(count=len(devices)),
                    error=False,
                )
                self._safe_call_after(self._auto_enable_platform, self.chkCozytouch, "Cozytouch")

            except Exception as e:
                log.error(f"Cozytouch connection test failed: {type(e).__name__}: {e}")
                error_msg = str(e)[:100]
                self._safe_call_after(
                    self._set_status,
                    _("Cozytouch error: {error}").format(error=error_msg), error=True)
                self._safe_call_after(
                    ui.message,
                    _("Cozytouch error: {error}").format(error=error_msg))
            finally:
                _password = None
                self._safe_call_after(self._safe_button_enable, self.cozytouchTestBtn)

        threading.Thread(target=test_task, args=(email, password), daemon=True).start()
        password = None
        del password

    # =========================================================================
    # =
    # Save
    # =========================================================================
    # =
    def on_ok(self, event):
        """Saves all settings."""
        # Meross validation
        email = self.merossEmailCtrl.GetValue().strip()
        # Empty password field = keep the stored password (the fields are
        # no longer filled with the plain text on opening).
        password = self._effective_secret(self.merossPasswordCtrl, self.plugin.password)
        use_meross = self.chkMeross.GetValue()

        if use_meross:
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not email:
                self._set_status(_("Meross: please enter an email address"), error=True)
                ui.message(_("Meross: please enter an email address"))
                self.notebook.SetSelection(self._PLATFORM_TABS['meross'])
                self.merossEmailCtrl.SetFocus()
                return

            if not re.match(email_pattern, email):
                # Translators: Validation error for invalid email syntax.
                self._set_status(_("Invalid email address"), error=True)
                ui.message(_("Invalid email address"))
                self.notebook.SetSelection(self._PLATFORM_TABS['meross'])
                self.merossEmailCtrl.SetFocus()
                return

            if not password:
                self._set_status(_("Meross: please enter a password"), error=True)
                ui.message(_("Meross: please enter a password"))
                self.notebook.SetSelection(self._PLATFORM_TABS['meross'])
                self.merossPasswordCtrl.SetFocus()
                return

            if len(password) < 6:
                # Translators: Validation error for a too short password.
                self._set_status(_("Password too short (min. 6 characters)"), error=True)
                ui.message(_("Password too short"))
                self.notebook.SetSelection(self._PLATFORM_TABS['meross'])
                self.merossPasswordCtrl.SetFocus()
                return

        # Netatmo validation
        use_netatmo = self.chkNetatmo.GetValue()
        netatmo_client_id = self.netatmoIdCtrl.GetValue().strip()
        netatmo_client_secret = self._effective_secret(
            self.netatmoSecretCtrl, getattr(self.plugin, 'netatmo_client_secret', ''))

        if use_netatmo and (not netatmo_client_id or not netatmo_client_secret):
            self._set_status(_("Netatmo: please enter client ID and secret"), error=True)
            ui.message(_("Netatmo: please enter client ID and secret"))
            self.notebook.SetSelection(self._PLATFORM_TABS['netatmo'])
            self.netatmoIdCtrl.SetFocus()
            return

        # VeSync validation
        use_vesync = self.chkVesync.GetValue()
        vesync_email = self.vesyncEmailCtrl.GetValue().strip()
        vesync_password = self._effective_secret(
            self.vesyncPasswordCtrl, getattr(self.plugin, 'vesync_password', ''))
        vesync_country = (self.vesyncCountryCtrl.GetValue() or "DE").strip().upper()

        if use_vesync:
            if not vesync_email:
                self._set_status(_("VeSync: please enter an email address"), error=True)
                ui.message(_("VeSync: please enter an email address"))
                self.notebook.SetSelection(self._PLATFORM_TABS['vesync'])
                self.vesyncEmailCtrl.SetFocus()
                return

            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, vesync_email):
                self._set_status(_("VeSync: invalid email address"), error=True)
                ui.message(_("VeSync: invalid email address"))
                self.notebook.SetSelection(self._PLATFORM_TABS['vesync'])
                self.vesyncEmailCtrl.SetFocus()
                return

            if not vesync_password:
                self._set_status(_("VeSync: please enter a password"), error=True)
                ui.message(_("VeSync: please enter a password"))
                self.notebook.SetSelection(self._PLATFORM_TABS['vesync'])
                self.vesyncPasswordCtrl.SetFocus()
                return

            if len(vesync_country) != 2 or not vesync_country.isalpha():
                self._set_status(_("VeSync: country code must be two letters"), error=True)
                ui.message(_("VeSync: invalid country code"))
                self.notebook.SetSelection(self._PLATFORM_TABS['vesync'])
                self.vesyncCountryCtrl.SetFocus()
                return

        # Cozytouch validation
        use_cozytouch = self.chkCozytouch.GetValue()
        cozytouch_email = self.cozytouchEmailCtrl.GetValue().strip()
        cozytouch_password = self._effective_secret(
            self.cozytouchPasswordCtrl, getattr(self.plugin, 'cozytouch_password', ''))

        if use_cozytouch:
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not cozytouch_email or not re.match(email_pattern, cozytouch_email):
                self._set_status(_("Cozytouch: invalid email address"), error=True)
                ui.message(_("Cozytouch: invalid email address"))
                self.notebook.SetSelection(self._PLATFORM_TABS['cozytouch'])
                self.cozytouchEmailCtrl.SetFocus()
                return
            if not cozytouch_password:
                self._set_status(_("Cozytouch: please enter a password"), error=True)
                ui.message(_("Cozytouch: please enter a password"))
                self.notebook.SetSelection(self._PLATFORM_TABS['cozytouch'])
                self.cozytouchPasswordCtrl.SetFocus()
                return

        # At least one platform must be enabled
        if not use_meross and not use_netatmo and not use_vesync and not use_cozytouch:
            # Translators: Validation error: no platform selected.
            self._set_status(_("At least one platform must be enabled"), error=True)
            ui.message(_("Enable at least one platform"))
            self.notebook.SetSelection(0)
            return

        # ---- Which platforms got new credentials? ----
        # An unchanged password was accepted when it was stored, so it is not
        # probed again - saving stays instant for everything except a real
        # credential change. A changed one has to prove itself first: the
        # running session keeps working no matter what is typed here, so a
        # wrong password would be stored silently and only surface at the next
        # NVDA start, with no hint where it came from.
        changed = set()
        if email != self.plugin.email or password != self.plugin.password:
            changed.add('meross')
        if (netatmo_client_id != getattr(self.plugin, 'netatmo_client_id', '')
                or netatmo_client_secret != getattr(
                    self.plugin, 'netatmo_client_secret', '')):
            changed.add('netatmo')
        if (vesync_email != getattr(self.plugin, 'vesync_email', '')
                or vesync_password != getattr(self.plugin, 'vesync_password', '')
                or vesync_country != getattr(
                    self.plugin, 'vesync_country_code', 'DE')):
            changed.add('vesync')
        if (cozytouch_email != getattr(self.plugin, 'cozytouch_email', '')
                or cozytouch_password != getattr(
                    self.plugin, 'cozytouch_password', '')):
            changed.add('cozytouch')

        # Everything read from the controls in one place: the check runs in the
        # background, and the fields must not be read again afterwards (they
        # can be edited meanwhile).
        # Filter warning threshold (%) - parse tolerantly, clamp to 1..100,
        # invalid/empty = default 15.
        try:
            threshold = int(self.vesyncFilterThresholdCtrl.GetValue().strip() or "15")
        except ValueError:
            threshold = 15
        # Favorites layer switching window (seconds) - same tolerant parse,
        # clamped to the range the layer accepts.
        from .constants import (
            FAV_LAYER_SWITCH_WINDOW_DEFAULT, FAV_LAYER_SWITCH_WINDOW_MIN,
            FAV_LAYER_SWITCH_WINDOW_MAX,
        )
        try:
            fav_window = int(self.favSwitchWindowCtrl.GetValue().strip()
                             or str(FAV_LAYER_SWITCH_WINDOW_DEFAULT))
        except ValueError:
            fav_window = FAV_LAYER_SWITCH_WINDOW_DEFAULT
        fav_window = max(FAV_LAYER_SWITCH_WINDOW_MIN,
                         min(FAV_LAYER_SWITCH_WINDOW_MAX, fav_window))
        # Rated capacity (liters) - parse tolerantly, invalid/empty = 0 (off)
        try:
            capacity = max(0, int(
                self.cozytouchCapacityCtrl.GetValue().strip() or "0"))
        except ValueError:
            capacity = 0
        try:
            start_tab = self._startTabValues[self.startTabChoice.GetSelection()]
        except (IndexError, AttributeError):
            start_tab = 'devices'

        pending = {
            'changed': changed,
            'email': email,
            'password': password,
            'use_meross': use_meross,
            'use_netatmo': use_netatmo,
            'netatmo_client_id': netatmo_client_id,
            'netatmo_client_secret': netatmo_client_secret,
            'netatmo_redirect_port': self.netatmoPortCtrl.GetValue(),
            # Only a new client ID invalidates the stored tokens - see _commit.
            'netatmo_new_app': (netatmo_client_id != getattr(
                self.plugin, 'netatmo_client_id', '')),
            'use_vesync': use_vesync,
            'vesync_email': vesync_email,
            'vesync_password': vesync_password,
            'vesync_country': vesync_country,
            'vesync_filter_threshold': max(1, min(100, threshold)),
            'fav_layer_switch_window': fav_window,
            'use_cozytouch': use_cozytouch,
            'cozytouch_email': cozytouch_email,
            'cozytouch_password': cozytouch_password,
            'cozytouch_capacity_liters': capacity,
            'auto_login': self.autoLoginCheckbox.GetValue(),
            'announce_external_changes': self.announceExternalCheckbox.GetValue(),
            'start_tab': start_tab,
            # Tokens the check earned on the way - see _verify_credentials.
            'vesync_creds': None,
            'cozytouch_token': None,
        }

        # A password login can be proven here; Netatmo's OAuth cannot - that
        # authorisation runs in the browser via "Connect to Netatmo".
        to_verify = [name for name in ('meross', 'vesync', 'cozytouch')
                     if name in changed and pending['use_' + name]]
        if to_verify:
            self._verify_then_commit(pending, to_verify)
        else:
            self._commit(pending)

    def on_cancel(self, event):
        """Cancel: save nothing, and drop a check that is still running."""
        self._cancelled = True
        self.EndModal(wx.ID_CANCEL)

    def _verify_then_commit(self, pending, to_verify):
        """Proves the new credentials in the background, then saves.

        Only the login is attempted, not the device list: the login answers
        the question ("are these credentials valid?") in a fraction of a
        second, while reading the devices takes up to fifteen seconds for a
        large Meross account - too long to wait on a Save button.
        """
        self._safe_button_disable(self.okBtn)
        # Translators: Status while the newly entered credentials are being
        # checked at the platform.
        self._set_status(_("Checking the credentials..."))
        threading.Thread(
            target=self._verify_credentials,
            args=(pending, to_verify),
            daemon=True,
        ).start()

    def _verify_credentials(self, pending, to_verify):
        """Thread body of the credential check. Saves or reports."""
        for platform in to_verify:
            if self._cancelled or self._is_destroyed:
                return
            if len(to_verify) > 1:
                # Several platforms in a row can take half a minute on a slow
                # connection. Naming each one keeps the wait from sounding
                # like a hang. With only one there is nothing to add to the
                # message already spoken.
                self._safe_call_after(
                    self._set_status,
                    # Translators: Status while one platform is being checked.
                    # {platform} = brand name (Meross etc.).
                    _("Checking {platform}...").format(
                        platform=PLATFORM_LABELS[platform]))
            try:
                if platform == 'meross':
                    from .meross_api import MerossAPI
                    api = MerossAPI()
                    try:
                        api.login(pending['email'], pending['password'])
                    finally:
                        # Only a check: the session is closed again and the
                        # login for real use happens afterwards. Without the
                        # logout the event loop thread and the MQTT connection
                        # of this probe would stay alive.
                        api.logout()
                elif platform == 'vesync':
                    from .vesync_api import VeSyncAPI
                    api = VeSyncAPI(country_code=pending['vesync_country'])
                    api.login(pending['vesync_email'],
                              pending['vesync_password'])
                    # The token from the check is kept: it saves the following
                    # login a second password round trip (and VeSync may have
                    # switched the region in the meantime).
                    creds = api.get_credentials()
                    if creds.get("token") and creds.get("account_id"):
                        pending['vesync_creds'] = creds
                    api.logout()
                elif platform == 'cozytouch':
                    from .cozytouch_api import CozytouchAPI
                    api = CozytouchAPI()
                    api.login(pending['cozytouch_email'],
                              pending['cozytouch_password'])
                    creds = api.get_credentials()
                    if creds.get("token"):
                        pending['cozytouch_token'] = creds["token"]
                    api.logout()
            except Exception as e:
                log.error(f"{PLATFORM_LABELS[platform]} credential check "
                          f"failed: {type(e).__name__}: {e}")
                self._safe_call_after(self._verification_failed, platform, e)
                return
            log.info(f"{PLATFORM_LABELS[platform]}: new credentials verified")

        self._safe_call_after(self._commit, pending)

    def _verification_failed(self, platform, error):
        """Keeps the dialog open at the platform whose credentials failed."""
        self._safe_button_enable(self.okBtn)
        message = login_error_message(platform, error)
        # Set the status line without speech and jump into the password field
        # first: NVDA cancels running speech on a focus change, so a reason
        # spoken now would be cut off by the field announcement. It follows
        # once the field has been announced - the same pattern as the filter
        # warning when the device menu opens.
        self._set_status(message, error=True, speak=False)
        self._focus_platform(platform)
        try:
            wx.CallLater(500, self._speak_verification_failure, message)
        except Exception as e:
            log.debug(f"Ignored error in _verification_failed: {e}")

    def _speak_verification_failure(self, message):
        """Announces reason and consequence in ONE utterance.

        In one, not two: a second ui.message would overwrite the reason on
        the braille display.
        """
        if self._is_destroyed:
            return
        # Translators: Announcement after a refused credential check. {reason}
        # = why the platform refused, e.g. "Meross: email address or password
        # not accepted".
        ui.message(_("{reason}. Nothing saved - please enter the credentials "
                     "again").format(reason=message))

    def _commit(self, pending):
        """Writes the checked values into the plugin and closes the dialog."""
        if self._is_destroyed or self._cancelled:
            # Cancelled while the check was running - save nothing.
            return

        self.plugin.email = pending['email']
        self.plugin.password = pending['password']
        self.plugin.auto_login = pending['auto_login']
        self.plugin.announce_external_changes = pending['announce_external_changes']
        self.plugin.start_tab = pending['start_tab']
        self.plugin.use_meross = pending['use_meross']
        self.plugin.use_netatmo = pending['use_netatmo']
        self.plugin.use_vesync = pending['use_vesync']
        self.plugin.netatmo_client_id = pending['netatmo_client_id']
        self.plugin.netatmo_client_secret = pending['netatmo_client_secret']
        self.plugin.netatmo_redirect_port = pending['netatmo_redirect_port']

        # A new client ID means the stored tokens were issued to another app
        # registration and can no longer be renewed. Keeping them would fail
        # at the next refresh with an "invalid_client" nobody can place. A
        # corrected secret is different: the tokens belong to the client ID,
        # not to the secret, so they stay valid and the authorisation in the
        # browser does not have to be repeated.
        if pending['netatmo_new_app']:
            self.plugin.netatmo_access_token = ""
            self.plugin.netatmo_refresh_token = ""
            self.plugin.netatmo_token_expiry = 0
            self.plugin.netatmo_api = None

        self.plugin.vesync_email = pending['vesync_email']
        self.plugin.vesync_password = pending['vesync_password']
        self.plugin.vesync_country_code = pending['vesync_country']
        self.plugin.vesync_filter_threshold = pending['vesync_filter_threshold']
        self.plugin.fav_layer_switch_window = pending['fav_layer_switch_window']
        if pending['vesync_creds']:
            creds = pending['vesync_creds']
            self.plugin.vesync_token = creds["token"]
            self.plugin.vesync_account_id = creds["account_id"]
            self.plugin.vesync_country_code = creds["country_code"]
            self.plugin.vesync_region = creds["region"]
        elif 'vesync' in pending['changed']:
            # Changed credentials without a fresh token (platform disabled, so
            # not checked): the old tokens belong to the old account.
            self.plugin.vesync_token = ""
            self.plugin.vesync_account_id = ""
            self.plugin.vesync_region = ""

        self.plugin.use_cozytouch = pending['use_cozytouch']
        self.plugin.cozytouch_email = pending['cozytouch_email']
        self.plugin.cozytouch_password = pending['cozytouch_password']
        self.plugin.cozytouch_capacity_liters = pending['cozytouch_capacity_liters']
        if pending['cozytouch_token']:
            self.plugin.cozytouch_token = pending['cozytouch_token']
        elif 'cozytouch' in pending['changed']:
            self.plugin.cozytouch_token = ""

        # Write the notification checkboxes from the "Notifications" tab back
        # into the plugin. Platforms whose section was not visible in the
        # current session (platform disabled) stay unchanged - their values are
        # not overwritten.
        for attr_name, checkbox in getattr(self, '_notify_checkboxes', {}).items():
            try:
                setattr(self.plugin, attr_name, checkbox.GetValue())
            except Exception as e:
                log.debug(f"Ignored error in on_ok: {e}")

        self.plugin.save_settings()
        # The caller re-logs in exactly these platforms - a running session
        # would otherwise keep the old credentials.
        self.changed_platforms = set(pending['changed'])
        # The plugin keeps the passwords encrypted from here on; the plain
        # text of the check does not have to wait for the garbage collector.
        for key in ('password', 'vesync_password', 'cozytouch_password',
                    'netatmo_client_secret'):
            pending[key] = None

        log.info("Smart Home settings saved")
        # Translators: Success message after saving the settings.
        ui.message(_("Settings saved"))
        self.EndModal(wx.ID_OK)

    # =========================================================================
    # =
    # Helpers
    # =========================================================================
    # =
    def _set_status(self, text, error=False, speak=True):
        """Updates the status line AND speaks the text via ui.message.

        Previously the StaticText was often only set via SetLabel; NVDA does
        not announce StaticText changes automatically. Now a ui.message output
        happens in parallel so blind users are always informed.

        ``speak=False`` is for the one case where a focus change follows
        immediately: NVDA cancels running speech on a focus change, so the
        text would be cut off. It is then spoken afterwards instead.
        """
        # `if not self or not self.statusText` was the wrong test: on an
        # already destroyed wx object the mere attribute access raises, not
        # just the method. Hence the destroy flag first.
        if self._is_destroyed:
            return
        try:
            if not self.statusText:
                return
            self.statusText.SetLabel(text or "")
            self.statusText.SetForegroundColour(
                wx.RED if error else wx.Colour(0, 128, 0))
            self.Layout()
            # Synchronous ui.message: NVDA announces the new status
            # immediately.
            if text and speak:
                try:
                    ui.message(text)
                except Exception as e:
                    log.debug(f"Ignored error in _set_status: {e}")
        except RuntimeError:
            pass

    def _safe_button_enable(self, button):
        try:
            if button:
                button.Enable()
        except RuntimeError:
            pass

    def _safe_button_disable(self, button):
        try:
            if button:
                button.Disable()
        except RuntimeError:
            pass


def offer_credential_reentry(parent, plugin, platform, error, on_saved=None):
    """Offers to enter the credentials of a platform again right away.

    Called after a login that failed on the credentials. Without this the
    only remaining path was: hear the error, find the settings, find the tab,
    find the field. Answering "Enter again" opens the settings straight at
    that tab with the focus in the password field.

    Runs on the main thread (wx dialogs) - callers use wx.CallAfter.

    Args:
        parent: window the dialogs belong to.
        plugin: the GlobalPlugin instance.
        platform: platform key ('meross', 'vesync', ...).
        error: the exception the login failed with.
        on_saved: called with the set of changed platforms after a save.
    """
    label = PLATFORM_LABELS.get(platform, platform)
    ui.message(login_error_message(platform, error))
    if platform not in PASSWORD_PLATFORMS:
        # Netatmo: the way back is the browser authorisation, not a password
        # field. Its own message already says so.
        return
    ask = wx.MessageDialog(
        parent,
        # Translators: Question after a failed login. {platform} = brand name.
        _("The {platform} login was refused.\n\nEnter the credentials "
          "again?").format(platform=label),
        # Translators: Title of the dialog after a failed login. {platform} =
        # brand name.
        _("{platform} login failed").format(platform=label),
        wx.YES_NO | wx.ICON_EXCLAMATION,
    )
    # Translators: Button that opens the settings for another attempt.
    # Translators: Button that dismisses the failed login without a new attempt.
    ask.SetYesNoLabels(_("&Enter again"), _("&Later"))
    try:
        ask.SetEscapeId(wx.ID_NO)
    except Exception as e:
        log.debug(f"Ignored error in offer_credential_reentry: {e}")
    answer = ask.ShowModal()
    ask.Destroy()
    if answer != wx.ID_YES:
        return

    dlg = SmartHomeSettingsDialog(parent, plugin, focus_platform=platform)
    try:
        if dlg.ShowModal() == wx.ID_OK and on_saved:
            on_saved(dlg.changed_platforms)
    finally:
        dlg.Destroy()


# ============================================================================
# Backward compatibility: old name -> new dialog
# ============================================================================
MerossSettingsDialog = SmartHomeSettingsDialog
