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

import addonHandler
try:
    addonHandler.initTranslation()
except Exception as e:
    log.debug(f"Ignorierter Fehler in <module>: {e}")
if "_" not in globals():  # Fallback, falls initTranslation() scheitert
    # Ohne diesen Fallback bleibt `_` undefiniert und der erste `_()`-Aufruf
    # wirft einen NameError mitten im Dialogaufbau statt beim Import.
    def _(s):
        return s


class SmartHomeSettingsDialog(wx.Dialog):
    """Settings dialog with tabs for Meross, Netatmo and VeSync credentials.

    The notifications tab is rebuilt dynamically on every platform toggle
    (Meross/Netatmo/VeSync on/off) so platform changes become visible
    immediately without closing and reopening the dialog (used to be static).
    """

    def __init__(self, parent, plugin):
        super().__init__(
            parent,
            # Translators: Title of the settings dialog.
            title=_("Smart Home Control - Einstellungen"),
            size=(620, 600),
        )

        self.plugin = plugin
        self._notify_checkboxes = {}
        # Der Netatmo-OAuth-Flow läuft mit 120 s Timeout im Hintergrundthread;
        # die Verbindungstests der anderen Plattformen ebenfalls. Schließt der
        # Nutzer in der Zeit die Einstellungen, feuerte der Callback auf ein
        # zerstörtes wx-Objekt: "RuntimeError: wrapped C/C++ object of type
        # StaticText has been deleted". Dasselbe Muster wie im Geräte-Dialog
        # (_safe_call_after + _is_destroyed) verhindert das.
        self._is_destroyed = False
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_window_destroy)
        self._create_ui()
        self.CenterOnScreen()

    # ---- Passwort-Felder: Platzhalter-Logik ----
    # Gespeicherte Passwörter/Secrets werden beim Öffnen NICHT entschlüsselt
    # und ins TextCtrl gelegt (TE_PASSWORD maskiert nur die Anzeige, der
    # Klartext läge sonst für die Lebensdauer des Dialogs im Speicher und wäre
    # per GetValue auslesbar). Stattdessen bleibt das Feld leer; ein leeres
    # Feld heißt beim Speichern und Testen "gespeichertes Passwort behalten".

    @staticmethod
    def _password_field_name(field, has_saved):
        """Accessible-Name eines Passwortfelds, mit Hinweis auf den
        gespeicherten Wert."""
        if has_saved:
            # Translators: Accessible name of a password field when a password
            # is already stored. {field} = field name.
            return _("{field} (gespeichert - leer lassen zum Beibehalten)").format(field=field)
        return field

    @staticmethod
    def _effective_secret(ctrl, stored):
        """Eingegebener Wert; bei leerem Feld der gespeicherte."""
        value = ctrl.GetValue().strip()
        return value if value else (stored or "")

    def _on_window_destroy(self, event):
        # EVT_WINDOW_DESTROY feuert auch für Kindfenster - nur die Zerstörung
        # des Dialogs selbst zählt.
        if event.GetEventObject() is self:
            self._is_destroyed = True
        event.Skip()

    def _safe_call_after(self, func, *args, **kwargs):
        """wx.CallAfter, das nach dem Schließen des Dialogs nichts mehr tut.

        Zwei Prüfungen: einmal beim Einreihen und einmal beim Ausführen -
        dazwischen kann der Dialog geschlossen worden sein, und genau dieses
        Fenster ist der Grund für den RuntimeError.
        """
        if self._is_destroyed:
            return

        def _run():
            if self._is_destroyed:
                return
            try:
                func(*args, **kwargs)
            except RuntimeError:
                # wx-Objekt wurde zwischen Prüfung und Aufruf zerstört.
                pass
        try:
            wx.CallAfter(_run)
        except Exception as e:
            log.debug(f"Ignorierter Fehler in _safe_call_after: {e}")

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
        self.notebook.SetName(_("Ansicht"))

        # ---- Tab 1: General ----
        self.tab_general = wx.Panel(self.notebook)
        self._create_general_tab(self.tab_general)
        # Translators: Tab name "General" (& marks the accelerator).
        self.notebook.AddPage(self.tab_general, _("&Allgemein"))

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
        # Translators: Tab name "Cozytouch". Brand name, do not translate.
        self.notebook.AddPage(self.tab_cozytouch, _("&Cozytouch"))

        # ---- Tab 5: Notifications ----
        self.tab_notifications = wx.Panel(self.notebook)
        self._create_notifications_tab(self.tab_notifications)
        # Translators: Tab name "Notifications".
        self.notebook.AddPage(self.tab_notifications, _("&Benachrichtigungen"))

        mainSizer.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 5)

        # ---- Status ----
        self.statusText = wx.StaticText(self, label="")
        self.statusText.SetName(_("Status"))
        mainSizer.Add(self.statusText, flag=wx.ALL, border=10)

        # ---- Buttons ----
        buttonSizer = wx.BoxSizer(wx.HORIZONTAL)

        # Translators: "Save" button in the settings dialog.
        okBtn = wx.Button(self, wx.ID_OK, _("&Speichern"))
        okBtn.Bind(wx.EVT_BUTTON, self.on_ok)
        okBtn.SetDefault()
        buttonSizer.Add(okBtn, flag=wx.RIGHT, border=5)

        # Translators: "Cancel" button in the settings dialog.
        cancelBtn = wx.Button(self, wx.ID_CANCEL, _("&Abbrechen"))
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
        platformBox = wx.StaticBox(panel, label=_("Aktive Plattformen"))
        platformSizer = wx.StaticBoxSizer(platformBox, wx.VERTICAL)

        # Translators: Checkbox label for enabling the Meross platform.
        self.chkMeross = wx.CheckBox(panel, label=_("&Meross verwenden"))
        self.chkMeross.SetValue(self.plugin.use_meross)
        # Dynamically refresh the notifications tab when a platform is toggled
        self.chkMeross.Bind(wx.EVT_CHECKBOX, self._on_platform_toggle)
        platformSizer.Add(self.chkMeross, flag=wx.ALL, border=5)

        # Translators: Checkbox label for enabling the Netatmo platform.
        self.chkNetatmo = wx.CheckBox(panel, label=_("&Netatmo verwenden"))
        self.chkNetatmo.SetValue(self.plugin.use_netatmo)
        self.chkNetatmo.Bind(wx.EVT_CHECKBOX, self._on_platform_toggle)
        platformSizer.Add(self.chkNetatmo, flag=wx.ALL, border=5)

        # Translators: Checkbox label for enabling the VeSync platform (covers
        # Levoit, Cosori, Etekcity).
        self.chkVesync = wx.CheckBox(panel, label=_("&VeSync verwenden (Levoit, Cosori, Etekcity)"))
        self.chkVesync.SetValue(getattr(self.plugin, 'use_vesync', False))
        self.chkVesync.Bind(wx.EVT_CHECKBOX, self._on_platform_toggle)
        platformSizer.Add(self.chkVesync, flag=wx.ALL, border=5)

        # Translators: Checkbox label for enabling the Cozytouch platform
        # (Atlantic / Austria Email hot water heat pumps).
        self.chkCozytouch = wx.CheckBox(panel, label=_("&Cozytouch verwenden (Atlantic, Austria Email)"))
        self.chkCozytouch.SetValue(getattr(self.plugin, 'use_cozytouch', False))
        self.chkCozytouch.Bind(wx.EVT_CHECKBOX, self._on_platform_toggle)
        platformSizer.Add(self.chkCozytouch, flag=wx.ALL, border=5)

        sizer.Add(platformSizer, flag=wx.EXPAND | wx.ALL, border=10)

        # Auto login checkbox
        # Translators: Checkbox for enabling automatic login at NVDA start.
        self.autoLoginCheckbox = wx.CheckBox(panel, label=_("&Automatisch beim NVDA-Start anmelden"))
        self.autoLoginCheckbox.SetValue(self.plugin.auto_login)
        sizer.Add(self.autoLoginCheckbox, flag=wx.ALL, border=10)

        # Start-Tab-Auswahl: welcher Reiter beim Öffnen des Menüs aktiv ist.
        startTabSizer = wx.BoxSizer(wx.HORIZONTAL)
        # Translators: Label before the start tab selection.
        startTabLabel = wx.StaticText(panel, label=_("Beim Öffnen &anzeigen:"))
        startTabSizer.Add(startTabLabel, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
        # Translators: The two choices for the start tab (device list / favorites).
        self._startTabValues = ['devices', 'favorites']
        self.startTabChoice = wx.Choice(
            panel, choices=[_("Alle Geräte"), _("Favoriten")])
        current_tab = getattr(self.plugin, 'start_tab', 'devices')
        self.startTabChoice.SetSelection(
            1 if current_tab == 'favorites' else 0)
        startTabSizer.Add(self.startTabChoice, flag=wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(startTabSizer, flag=wx.ALL, border=10)

        # Note
        # Translators: Hint text in the General tab.
        hintText = wx.StaticText(panel, label=_(
            "Hinweis: Sie können Meross, Netatmo, VeSync und Cozytouch einzeln "
            "oder kombiniert verwenden.\nDeaktivierte Plattformen werden beim "
            "Login übersprungen.\nEin erfolgreicher Verbindungstest im jeweiligen "
            "Tab aktiviert die Plattform automatisch."
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
            log.debug(f"Notifications-Tab konnte nicht neu aufgebaut werden: {e}")

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
            ui.message(_("{platform} aktiviert – beim Speichern wird angemeldet").format(
                platform=label))
        except Exception as e:
            log.debug(f"Auto-Aktivierung der Plattform fehlgeschlagen: {e}")

    # -------------------------------------------------------------------------
    # -
    # Tab: Meross
    # -------------------------------------------------------------------------
    # -
    def _create_meross_tab(self, panel):
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Translators: Explanatory text in the Meross tab.
        infoText = wx.StaticText(panel, label=_(
            "Meross Account Zugangsdaten.\n"
            "Diese werden lokal auf diesem Rechner verschlüsselt gespeichert."
        ))
        sizer.Add(infoText, flag=wx.ALL, border=10)

        formSizer = wx.FlexGridSizer(cols=2, hgap=10, vgap=10)
        formSizer.AddGrowableCol(1)

        # Email
        # Translators: Label of the email input field (Meross).
        emailLabel = wx.StaticText(panel, label=_("&E-Mail:"))
        self.merossEmailCtrl = wx.TextCtrl(panel, value=self.plugin.email)
        self.merossEmailCtrl.SetName(_("Meross E-Mail-Adresse"))
        formSizer.Add(emailLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.merossEmailCtrl, flag=wx.EXPAND)

        # Password
        # Translators: Label of the password input field (Meross).
        passwordLabel = wx.StaticText(panel, label=_("&Passwort:"))
        self.merossPasswordCtrl = wx.TextCtrl(
            panel, value="", style=wx.TE_PASSWORD)
        self.merossPasswordCtrl.SetName(self._password_field_name(
            _("Meross Passwort"), bool(self.plugin.password)))
        formSizer.Add(passwordLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.merossPasswordCtrl, flag=wx.EXPAND)

        sizer.Add(formSizer, flag=wx.EXPAND | wx.ALL, border=10)

        # Test button
        # Translators: Button for testing the Meross connection.
        self.merossTestBtn = wx.Button(panel, label=_("Verbindung &testen"))
        self.merossTestBtn.SetName(_("Meross-Verbindung testen"))
        self.merossTestBtn.Bind(wx.EVT_BUTTON, self.on_test_meross)
        sizer.Add(self.merossTestBtn, flag=wx.ALL, border=10)

        # Announce external changes (Meross-relevant only)
        # Translators: Checkbox: NVDA should announce external switching
        # (Alexa, app).
        self.announceExternalCheckbox = wx.CheckBox(panel, label=_(
            "Externe Änderungen &ansagen (Alexa, Meross App, etc.)"
        ))
        self.announceExternalCheckbox.SetValue(
            getattr(self.plugin, 'announce_external_changes', True))
        # Translators: Tooltip explanation of the external changes checkbox.
        self.announceExternalCheckbox.SetToolTip(_(
            "Wenn aktiviert, wird NVDA ansagen wenn ein Meross-Gerät über "
            "Alexa, die Meross App oder andere externe Quellen geschaltet wird."
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
            "Netatmo OAuth2 Zugangsdaten.\n"
            "Erstellen Sie eine App auf https://dev.netatmo.com,\n"
            "geben Sie Client-ID und Secret ein und tragen Sie die unten\n"
            "angezeigte Redirect-URI in Ihrer Netatmo-App ein (exakt gleich)."
        ))
        sizer.Add(infoText, flag=wx.ALL, border=10)

        formSizer = wx.FlexGridSizer(cols=2, hgap=10, vgap=10)
        formSizer.AddGrowableCol(1)

        # Translators: Label of the client ID field for Netatmo OAuth2.
        idLabel = wx.StaticText(panel, label=_("Client-&ID:"))
        self.netatmoIdCtrl = wx.TextCtrl(
            panel, value=getattr(self.plugin, 'netatmo_client_id', ''))
        self.netatmoIdCtrl.SetName(_("Netatmo Client-ID"))
        formSizer.Add(idLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.netatmoIdCtrl, flag=wx.EXPAND)

        # Translators: Label of the client secret field for Netatmo OAuth2.
        secretLabel = wx.StaticText(panel, label=_("Client-&Secret:"))
        self.netatmoSecretCtrl = wx.TextCtrl(
            panel, value="", style=wx.TE_PASSWORD)
        self.netatmoSecretCtrl.SetName(self._password_field_name(
            _("Netatmo Client-Secret"),
            bool(getattr(self.plugin, 'netatmo_client_secret', ''))))
        formSizer.Add(secretLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.netatmoSecretCtrl, flag=wx.EXPAND)

        # Translators: Label of the port field for the local OAuth2 callback.
        portLabel = wx.StaticText(panel, label=_("Redirect-&Port:"))
        self.netatmoPortCtrl = wx.SpinCtrl(
            panel, min=1024, max=65535,
            initial=int(getattr(self.plugin, 'netatmo_redirect_port', NETATMO_REDIRECT_PORT)))
        self.netatmoPortCtrl.SetName(_("Netatmo Redirect-Port"))
        self.netatmoPortCtrl.Bind(wx.EVT_SPINCTRL, self._on_netatmo_port_changed)
        self.netatmoPortCtrl.Bind(wx.EVT_TEXT, self._on_netatmo_port_changed)
        formSizer.Add(portLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.netatmoPortCtrl, flag=wx.EXPAND)

        sizer.Add(formSizer, flag=wx.EXPAND | wx.ALL, border=10)

        # Display of the redirect URI to register exactly (read-only, focusable
        # via TE_READONLY so the screen reader can read/copy it).
        # Translators: Label before the displayed redirect URI.
        uriLabel = wx.StaticText(panel, label=_(
            "Diese Redirect-URI bei dev.netatmo.com eintragen:"))
        sizer.Add(uriLabel, flag=wx.LEFT | wx.TOP, border=10)
        self.netatmoUriCtrl = wx.TextCtrl(
            panel,
            value=netatmo_redirect_uri(self.netatmoPortCtrl.GetValue()),
            style=wx.TE_READONLY)
        self.netatmoUriCtrl.SetName(_("Netatmo Redirect-URI"))
        sizer.Add(self.netatmoUriCtrl, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=10)

        # OAuth connect button
        # Translators: Button starts the OAuth2 flow in the browser.
        self.netatmoConnectBtn = wx.Button(panel, label=_("Mit Netatmo &verbinden (OAuth2)"))
        self.netatmoConnectBtn.SetName(_("Mit Netatmo verbinden"))
        self.netatmoConnectBtn.Bind(wx.EVT_BUTTON, self.on_connect_netatmo)
        sizer.Add(self.netatmoConnectBtn, flag=wx.ALL, border=10)

        # Connection status
        has_tokens = (
            getattr(self.plugin, 'netatmo_refresh_token', '') != ''
            and getattr(self.plugin, 'netatmo_access_token', '') != ''
        )
        # Translators: Netatmo status display "Connected" / "Not connected".
        status_label = _("Status: Verbunden") if has_tokens else _("Status: Nicht verbunden")
        self.netatmoStatusLabel = wx.StaticText(panel, label=status_label)
        self.netatmoStatusLabel.SetName(_("Netatmo-Verbindungsstatus"))
        sizer.Add(self.netatmoStatusLabel, flag=wx.ALL, border=10)

        # Test button
        self.netatmoTestBtn = wx.Button(panel, label=_("Verbindung &testen"))
        self.netatmoTestBtn.SetName(_("Netatmo-Verbindung testen"))
        self.netatmoTestBtn.Bind(wx.EVT_BUTTON, self.on_test_netatmo)
        sizer.Add(self.netatmoTestBtn, flag=wx.ALL, border=10)

        panel.SetSizer(sizer)

    def _on_netatmo_port_changed(self, event):
        """Keeps the displayed redirect URI in sync with the chosen port."""
        try:
            self.netatmoUriCtrl.SetValue(
                netatmo_redirect_uri(self.netatmoPortCtrl.GetValue()))
        except Exception as e:
            log.debug(f"Ignorierter Fehler in _on_netatmo_port_changed: {e}")
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
            "VeSync Account-Zugangsdaten (z. B. Levoit, Cosori, Etekcity).\n"
            "Aktuell unterstützt: Levoit Core 200S/300S/400S/500S/600S "
            "Luftreiniger und Levoit Tower-Ventilatoren.\n"
            "Daten werden lokal auf diesem Rechner verschlüsselt gespeichert."
        ))
        sizer.Add(infoText, flag=wx.ALL, border=10)

        formSizer = wx.FlexGridSizer(cols=2, hgap=10, vgap=10)
        formSizer.AddGrowableCol(1)

        # Translators: Label of the email field for VeSync.
        emailLabel = wx.StaticText(panel, label=_("V&eSync E-Mail:"))
        self.vesyncEmailCtrl = wx.TextCtrl(
            panel, value=getattr(self.plugin, 'vesync_email', ''))
        self.vesyncEmailCtrl.SetName(_("VeSync E-Mail-Adresse"))
        formSizer.Add(emailLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.vesyncEmailCtrl, flag=wx.EXPAND)

        # Translators: Label of the password field for VeSync.
        passwordLabel = wx.StaticText(panel, label=_("VeSync Pass&wort:"))
        self.vesyncPasswordCtrl = wx.TextCtrl(
            panel, value="", style=wx.TE_PASSWORD)
        self.vesyncPasswordCtrl.SetName(self._password_field_name(
            _("VeSync Passwort"),
            bool(getattr(self.plugin, 'vesync_password', ''))))
        formSizer.Add(passwordLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.vesyncPasswordCtrl, flag=wx.EXPAND)

        # Translators: Label of the country code field (ISO 3166-1 alpha-2).
        countryLabel = wx.StaticText(panel, label=_("&Ländercode (ISO):"))
        self.vesyncCountryCtrl = wx.TextCtrl(
            panel,
            value=getattr(self.plugin, 'vesync_country_code', 'DE') or 'DE')
        self.vesyncCountryCtrl.SetName(_("VeSync Ländercode"))
        # Translators: Tooltip for the country code field.
        self.vesyncCountryCtrl.SetToolTip(_(
            "Zwei-Buchstaben-Ländercode (ISO 3166-1 Alpha-2). "
            "Beispiele: DE, AT, CH, US, GB, FR. "
            "Bestimmt automatisch die VeSync-Server-Region (EU oder US)."
        ))
        formSizer.Add(countryLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.vesyncCountryCtrl, flag=wx.EXPAND)

        # Translators: Label of the input field for the filter warning
        # threshold in percent.
        filterLabel = wx.StaticText(panel, label=_("&Filter-Warnschwelle (%):"))
        self.vesyncFilterThresholdCtrl = wx.TextCtrl(
            panel, value=str(getattr(self.plugin, 'vesync_filter_threshold', 15)))
        self.vesyncFilterThresholdCtrl.SetName(_("VeSync Filter-Warnschwelle in Prozent"))
        # Translators: Tooltip for the filter warning threshold field.
        self.vesyncFilterThresholdCtrl.SetToolTip(_(
            "Sinkt die Filter-Restlebensdauer eines Luftreinigers auf oder unter "
            "diesen Prozentwert, erscheint im Geräte-Dialog oben eine Filterwarnung "
            "und es wird beim Unterschreiten einmal angesagt. Standard: 15."
        ))
        formSizer.Add(filterLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.vesyncFilterThresholdCtrl, flag=wx.EXPAND)

        sizer.Add(formSizer, flag=wx.EXPAND | wx.ALL, border=10)

        # Translators: "Test VeSync connection" button.
        self.vesyncTestBtn = wx.Button(panel, label=_("VeSync Verbindung &prüfen"))
        self.vesyncTestBtn.SetName(_("VeSync-Verbindung prüfen"))
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

        # Translators: Explanatory text in the Cozytouch tab.
        infoText = wx.StaticText(panel, label=_(
            "Cozytouch Account-Zugangsdaten (Atlantic-Gruppe, z. B. Austria Email).\n"
            "Aktuell unterstützt: Warmwasser-Wärmepumpe (getestet: Austria Email Revolution Evo 3).\n"
            "Dieselben Zugangsdaten wie in der Cozytouch-App. Daten werden "
            "lokal auf diesem Rechner verschlüsselt gespeichert."
        ))
        sizer.Add(infoText, flag=wx.ALL, border=10)

        formSizer = wx.FlexGridSizer(cols=2, hgap=10, vgap=10)
        formSizer.AddGrowableCol(1)

        # Translators: Label of the email field for Cozytouch.
        emailLabel = wx.StaticText(panel, label=_("Cozytouch &E-Mail:"))
        self.cozytouchEmailCtrl = wx.TextCtrl(
            panel, value=getattr(self.plugin, 'cozytouch_email', ''))
        self.cozytouchEmailCtrl.SetName(_("Cozytouch E-Mail-Adresse"))
        formSizer.Add(emailLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.cozytouchEmailCtrl, flag=wx.EXPAND)

        # Translators: Label of the password field for Cozytouch.
        passwordLabel = wx.StaticText(panel, label=_("Cozytouch Pass&wort:"))
        self.cozytouchPasswordCtrl = wx.TextCtrl(
            panel, value="", style=wx.TE_PASSWORD)
        self.cozytouchPasswordCtrl.SetName(self._password_field_name(
            _("Cozytouch Passwort"),
            bool(getattr(self.plugin, 'cozytouch_password', ''))))
        formSizer.Add(passwordLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.cozytouchPasswordCtrl, flag=wx.EXPAND)

        # Translators: Label for the optional rated capacity of the hot water
        # tank.
        capacityLabel = wx.StaticText(panel, label=_("Nenn&kapazität (Liter, 0 = aus):"))
        self.cozytouchCapacityCtrl = wx.TextCtrl(
            panel, value=str(getattr(self.plugin, 'cozytouch_capacity_liters', 0) or 0))
        self.cozytouchCapacityCtrl.SetName(_("Nennkapazität des Warmwasserspeichers in Liter"))
        # Translators: Tooltip for the rated capacity field.
        self.cozytouchCapacityCtrl.SetToolTip(_(
            "Gesamt-Nennkapazität des Speichers in Litern (z. B. 300). Wird genutzt, "
            "um aus dem prozentualen Warmwasservorrat eine ungefähre Litermenge zu "
            "schätzen. 0 = keine Liter-Anzeige."
        ))
        formSizer.Add(capacityLabel, flag=wx.ALIGN_CENTER_VERTICAL)
        formSizer.Add(self.cozytouchCapacityCtrl, flag=wx.EXPAND)

        sizer.Add(formSizer, flag=wx.EXPAND | wx.ALL, border=10)

        # Translators: "Test Cozytouch connection" button.
        self.cozytouchTestBtn = wx.Button(panel, label=_("Cozytouch Verbindung &prüfen"))
        self.cozytouchTestBtn.SetName(_("Cozytouch-Verbindung prüfen"))
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
            "Hier legen Sie fest, über welche externen Änderungen NVDA "
            "Sie informieren soll. Die Auswahl gilt für alle Geräte der "
            "jeweiligen Plattform.\n"
            "Hinweis: Aktiv sichtbar sind nur Sektionen für Plattformen, "
            "die im Tab \"Allgemein\" eingeschaltet sind."
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
                    "{name} ist nicht aktiviert. "
                    "Aktivieren Sie die Plattform im Tab \"Allgemein\", "
                    "um die zugehörigen Benachrichtigungen einzustellen."
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
                 _("Ein-/Ausschaltungen ansagen (Alexa, Meross-App, ...)"),
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
                 _("Heizmodus-Wechsel ansagen (Zeitplan / Abwesend / Frostschutz)"),
                 True),
                ('notify_netatmo_setpoint',
                 # Translators: Announce Netatmo target temperature changes.
                 _("Soll-Temperatur-Änderungen ansagen"),
                 True),
                ('notify_netatmo_boiler',
                 # Translators: Announce Netatmo heating start/stop.
                 _("Heizungsstart und -stopp ansagen (Boiler-Status)"),
                 True),
                ('notify_netatmo_open_window',
                 # Translators: Announce Netatmo "open window" detection.
                 _("Offenes Fenster erkannt ansagen"),
                 True),
                ('notify_netatmo_anticipation',
                 # Translators: Announce Netatmo pre-heating (default: off).
                 _("Vorausheizen ansagen (kann häufig auftreten)"),
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
                 _("Ein-/Ausschaltungen ansagen"),
                 True),
                ('notify_vesync_mode',
                 # Translators: Announce VeSync mode changes.
                 _("Modus-Wechsel ansagen (Auto / Manuell / Schlaf / ...)"),
                 True),
                ('notify_vesync_fan_speed',
                 # Translators: Announce VeSync fan level changes.
                 _("Lüftergeschwindigkeit-Änderungen ansagen"),
                 True),
                ('notify_vesync_air_quality',
                 # Translators: Announce VeSync air quality changes.
                 _("Luftqualitäts-Änderungen ansagen (Luftreiniger)"),
                 True),
                ('notify_vesync_filter',
                 # Translators: Announce the VeSync filter life warning.
                 _("Filter-Lebensdauer-Warnungen ansagen"),
                 True),
            ],
            self.chkVesync.GetValue(),
        )

        # Cozytouch (Atlantic / Austria Email)
        add_section(
            "Cozytouch",
            "Cozytouch",
            [
                ('notify_cozytouch_power',
                 # Translators: Announce Cozytouch hot water operation on/off.
                 _("Ein-/Ausschaltungen ansagen (Warmwasser-Betrieb)"),
                 True),
                ('notify_cozytouch_temp',
                 # Translators: Announce Cozytouch target temperature changes.
                 _("Zieltemperatur-Änderungen ansagen"),
                 True),
                ('notify_cozytouch_mode',
                 # Translators: Announce Cozytouch heating mode changes.
                 _("Heizmodus-Wechsel ansagen (Manuell / Eco+ / Programm)"),
                 True),
                ('notify_cozytouch_boost',
                 # Translators: Announce Cozytouch boost switching.
                 _("Boost ein-/ausschalten ansagen"),
                 True),
                ('notify_cozytouch_away',
                 # Translators: Announce the Cozytouch away mode.
                 _("Abwesenheit ein-/ausschalten ansagen"),
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
            self._set_status(_("Bitte Meross E-Mail und Passwort eingeben"), error=True)
            ui.message(_("Bitte E-Mail und Passwort eingeben"))
            return

        def test_task(_email, _password):
            try:
                # Translators: Status while the connection test is running.
                self._safe_call_after(self._set_status, _("Teste Meross Verbindung..."))
                self._safe_call_after(self._safe_button_disable, self.merossTestBtn)
                # Translators: Speech output during the connection test.
                self._safe_call_after(ui.message, _("Verbinde mit Meross..."))

                from .meross_api import MerossAPI

                api = MerossAPI()
                try:
                    api.login(_email, _password)
                finally:
                    _password = None

                self._safe_call_after(ui.message, _("Lade Geräte..."))
                devices = api.get_devices()
                api.logout()

                self._safe_call_after(
                    self._set_status,
                    # Translators: Success message of the Meross connection
                    # test. {count}=count.
                    _("Meross: Verbunden – {count} Gerät(e)").format(count=len(devices)),
                    error=False,
                )
                self._safe_call_after(self._auto_enable_platform, self.chkMeross, "Meross")
                self._safe_call_after(
                    ui.message,
                    _("Meross: {count} Gerät(e) gefunden").format(count=len(devices)))

            except Exception as e:
                log.error(f"Meross Verbindungstest fehlgeschlagen: {type(e).__name__}: {e}")
                error_msg = str(e)[:100]
                self._safe_call_after(
                    self._set_status,
                    # Translators: Error message of the Meross connection test.
                    # {error}=detail.
                    _("Meross Fehler: {error}").format(error=error_msg), error=True)
                self._safe_call_after(
                    ui.message,
                    _("Meross Fehler: {error}").format(error=error_msg))
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
            self._set_status(_("Bitte Netatmo Client-ID und Secret eingeben"), error=True)
            ui.message(_("Bitte Client-ID und Secret eingeben"))
            return

        # Confirmation dialog before redirecting to the browser
        confirm = wx.MessageDialog(
            self,
            # Translators: Confirmation dialog before the OAuth2 browser flow.
            _("Sie werden jetzt zum Netatmo-Login im Browser weitergeleitet.\n\n"
              "Bitte melden Sie sich dort an und autorisieren Sie die App.\n"
              "Nach der Autorisierung werden Sie automatisch zurückgeleitet.\n\n"
              "Möchten Sie fortfahren?"),
            # Translators: Title of the OAuth2 confirmation dialog.
            _("Netatmo-Autorisierung"),
            wx.YES_NO | wx.ICON_INFORMATION,
        )
        # Translators: "Continue" button in the OAuth2 confirmation dialog.
        # Translators: "Cancel" button in the OAuth2 confirmation dialog.
        confirm.SetYesNoLabels(_("&Fortfahren"), _("&Abbrechen"))
        # ESC confirms "Cancel" - protects against accidentally opening the
        # browser.
        try:
            confirm.SetEscapeId(wx.ID_NO)
        except Exception as e:
            log.debug(f"Ignorierter Fehler in on_connect_netatmo: {e}")
        result = confirm.ShowModal()
        confirm.Destroy()

        if result != wx.ID_YES:
            # Translators: Announcement when the user cancels the OAuth2 flow.
            ui.message(_("Netatmo-Verbindung abgebrochen"))
            return

        def oauth_task():
            try:
                self._safe_call_after(
                    self._set_status,
                    # Translators: Status while the browser is opened for
                    # OAuth2.
                    _("Öffne Browser für Netatmo-Autorisierung..."),
                )
                self._safe_call_after(self._safe_button_disable, self.netatmoConnectBtn)
                self._safe_call_after(ui.message, _(
                    "Browser wird geöffnet. Bitte bei Netatmo anmelden und autorisieren."))

                from .netatmo_api import NetatmoAPI

                api = NetatmoAPI(client_id, client_secret, redirect_port=redirect_port)
                api.start_oauth_flow(timeout=120)

                # Store the tokens in the plugin
                tokens = api.get_tokens()
                self.plugin.netatmo_access_token = tokens['access_token']
                self.plugin.netatmo_refresh_token = tokens['refresh_token']
                self.plugin.netatmo_token_expiry = tokens['token_expiry']

                self._safe_call_after(self._set_status, _("Netatmo: Verbunden"), error=False)
                self._safe_call_after(self.netatmoStatusLabel.SetLabel, _("Status: Verbunden"))
                self._safe_call_after(self._auto_enable_platform, self.chkNetatmo, "Netatmo")
                self._safe_call_after(ui.message, _("Netatmo verbunden"))

            except Exception as e:
                log.error(f"Netatmo OAuth fehlgeschlagen: {type(e).__name__}: {e}")
                error_msg = str(e)[:100]
                self._safe_call_after(
                    self._set_status,
                    _("Netatmo Fehler: {error}").format(error=error_msg), error=True)
                self._safe_call_after(
                    ui.message,
                    # Translators: Speech output on OAuth2 error.
                    _("Netatmo: Verbindung fehlgeschlagen – {error}").format(error=error_msg))
            finally:
                self._safe_call_after(self._safe_button_enable, self.netatmoConnectBtn)

        threading.Thread(target=oauth_task, daemon=True).start()

    def on_test_netatmo(self, event):
        """Tests the Netatmo connection with the existing tokens."""
        if not getattr(self.plugin, 'netatmo_access_token', ''):
            # Translators: Validation hint: not yet connected via OAuth2.
            self._set_status(_("Bitte zuerst mit Netatmo verbinden"), error=True)
            ui.message(_("Bitte zuerst mit Netatmo verbinden (OAuth2)"))
            return

        client_id = self.netatmoIdCtrl.GetValue().strip()
        client_secret = self._effective_secret(
            self.netatmoSecretCtrl, getattr(self.plugin, 'netatmo_client_secret', ''))

        def test_task():
            try:
                self._safe_call_after(self._set_status, _("Teste Netatmo Verbindung..."))
                self._safe_call_after(self._safe_button_disable, self.netatmoTestBtn)
                self._safe_call_after(ui.message, _("Teste Netatmo..."))

                from .netatmo_api import NetatmoAPI

                api = NetatmoAPI(client_id, client_secret)
                api.set_tokens(
                    self.plugin.netatmo_access_token,
                    self.plugin.netatmo_refresh_token,
                    getattr(self.plugin, 'netatmo_token_expiry', 0),
                )

                devices = api.get_devices()

                self._safe_call_after(
                    self._set_status,
                    _("Netatmo: {count} Gerät(e) gefunden").format(count=len(devices)),
                    error=False)
                self._safe_call_after(self._auto_enable_platform, self.chkNetatmo, "Netatmo")
                self._safe_call_after(
                    ui.message,
                    _("Netatmo: {count} Gerät(e)").format(count=len(devices)))

            except Exception as e:
                log.error(f"Netatmo Test fehlgeschlagen: {type(e).__name__}: {e}")
                error_msg = str(e)[:100]
                self._safe_call_after(
                    self._set_status,
                    _("Netatmo Fehler: {error}").format(error=error_msg), error=True)
                self._safe_call_after(
                    ui.message,
                    _("Netatmo Fehler: {error}").format(error=error_msg))
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
            self._set_status(_("Bitte VeSync E-Mail und Passwort eingeben"), error=True)
            ui.message(_("Bitte E-Mail und Passwort eingeben"))
            return

        if len(country) != 2:
            # Translators: Validation error for the country code (must be 2
            # letters).
            self._set_status(_("Ländercode muss zwei Buchstaben haben (z. B. DE)"), error=True)
            ui.message(_("Ungültiger Ländercode"))
            return

        def test_task(_email, _password, _country):
            try:
                self._safe_call_after(self._set_status, _("Teste VeSync-Verbindung..."))
                self._safe_call_after(self._safe_button_disable, self.vesyncTestBtn)
                self._safe_call_after(ui.message, _("Verbinde mit VeSync..."))

                from .vesync_api import VeSyncAPI

                api = VeSyncAPI(country_code=_country)
                try:
                    api.login(_email, _password)
                finally:
                    _password = None

                self._safe_call_after(ui.message, _("Lade VeSync-Geräte..."))
                devices = api.get_devices()

                # Save the tokens even without an OK click so the login works
                # faster
                creds = api.get_credentials()
                if creds["token"] and creds["account_id"]:
                    self.plugin.vesync_token = creds["token"]
                    self.plugin.vesync_account_id = creds["account_id"]
                    self.plugin.vesync_country_code = creds["country_code"]
                    self.plugin.vesync_region = creds["region"]
                api.logout()

                self._safe_call_after(
                    self._set_status,
                    _("VeSync: Verbunden – {count} Gerät(e)").format(count=len(devices)),
                    error=False,
                )
                self._safe_call_after(self._auto_enable_platform, self.chkVesync, "VeSync")
                self._safe_call_after(
                    ui.message,
                    _("VeSync: {count} Gerät(e) gefunden").format(count=len(devices)))

            except Exception as e:
                log.error(f"VeSync Verbindungstest fehlgeschlagen: {type(e).__name__}: {e}")
                error_msg = str(e)[:100]
                self._safe_call_after(
                    self._set_status,
                    _("VeSync Fehler: {error}").format(error=error_msg), error=True)
                self._safe_call_after(
                    ui.message,
                    _("VeSync Fehler: {error}").format(error=error_msg))
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
            self._set_status(_("Bitte Cozytouch E-Mail und Passwort eingeben"), error=True)
            ui.message(_("Bitte E-Mail und Passwort eingeben"))
            return

        def test_task(_email, _password):
            try:
                self._safe_call_after(self._set_status, _("Teste Cozytouch-Verbindung..."))
                self._safe_call_after(self._safe_button_disable, self.cozytouchTestBtn)
                self._safe_call_after(ui.message, _("Verbinde mit Cozytouch..."))

                from .cozytouch_api import CozytouchAPI

                api = CozytouchAPI()
                try:
                    api.login(_email, _password)
                finally:
                    _password = None

                self._safe_call_after(ui.message, _("Lade Cozytouch-Geräte..."))
                devices = api.get_devices()

                # Save the token even without an OK click so the login works
                # faster
                creds = api.get_credentials()
                if creds.get("token"):
                    self.plugin.cozytouch_token = creds["token"]
                api.logout()

                self._safe_call_after(
                    self._set_status,
                    _("Cozytouch: Verbunden – {count} Gerät(e)").format(count=len(devices)),
                    error=False,
                )
                self._safe_call_after(self._auto_enable_platform, self.chkCozytouch, "Cozytouch")
                self._safe_call_after(
                    ui.message,
                    _("Cozytouch: {count} Gerät(e) gefunden").format(count=len(devices)))

            except Exception as e:
                log.error(f"Cozytouch Verbindungstest fehlgeschlagen: {type(e).__name__}: {e}")
                error_msg = str(e)[:100]
                self._safe_call_after(
                    self._set_status,
                    _("Cozytouch Fehler: {error}").format(error=error_msg), error=True)
                self._safe_call_after(
                    ui.message,
                    _("Cozytouch Fehler: {error}").format(error=error_msg))
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
        # Leeres Passwortfeld = gespeichertes Passwort behalten (die Felder
        # werden beim Öffnen nicht mehr mit dem Klartext befüllt).
        password = self._effective_secret(self.merossPasswordCtrl, self.plugin.password)
        use_meross = self.chkMeross.GetValue()

        if use_meross:
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not email:
                self._set_status(_("Meross: Bitte E-Mail eingeben"), error=True)
                ui.message(_("Meross: Bitte E-Mail eingeben"))
                self.notebook.SetSelection(1)  # Meross tab
                self.merossEmailCtrl.SetFocus()
                return

            if not re.match(email_pattern, email):
                # Translators: Validation error for invalid email syntax.
                self._set_status(_("Ungültige E-Mail-Adresse"), error=True)
                ui.message(_("Ungültige E-Mail-Adresse"))
                self.notebook.SetSelection(1)
                self.merossEmailCtrl.SetFocus()
                return

            if not password:
                self._set_status(_("Meross: Bitte Passwort eingeben"), error=True)
                ui.message(_("Meross: Bitte Passwort eingeben"))
                self.notebook.SetSelection(1)
                self.merossPasswordCtrl.SetFocus()
                return

            if len(password) < 6:
                # Translators: Validation error for a too short password.
                self._set_status(_("Passwort zu kurz (min. 6 Zeichen)"), error=True)
                ui.message(_("Passwort zu kurz"))
                self.notebook.SetSelection(1)
                self.merossPasswordCtrl.SetFocus()
                return

        # Netatmo validation
        use_netatmo = self.chkNetatmo.GetValue()
        netatmo_client_id = self.netatmoIdCtrl.GetValue().strip()
        netatmo_client_secret = self._effective_secret(
            self.netatmoSecretCtrl, getattr(self.plugin, 'netatmo_client_secret', ''))

        if use_netatmo and (not netatmo_client_id or not netatmo_client_secret):
            self._set_status(_("Netatmo: Bitte Client-ID und Secret eingeben"), error=True)
            ui.message(_("Netatmo: Bitte Client-ID und Secret eingeben"))
            self.notebook.SetSelection(2)  # Netatmo tab
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
                self._set_status(_("VeSync: Bitte E-Mail eingeben"), error=True)
                ui.message(_("VeSync: Bitte E-Mail eingeben"))
                self.notebook.SetSelection(3)  # VeSync tab
                self.vesyncEmailCtrl.SetFocus()
                return

            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, vesync_email):
                self._set_status(_("VeSync: Ungültige E-Mail-Adresse"), error=True)
                ui.message(_("VeSync: Ungültige E-Mail-Adresse"))
                self.notebook.SetSelection(3)
                self.vesyncEmailCtrl.SetFocus()
                return

            if not vesync_password:
                self._set_status(_("VeSync: Bitte Passwort eingeben"), error=True)
                ui.message(_("VeSync: Bitte Passwort eingeben"))
                self.notebook.SetSelection(3)
                self.vesyncPasswordCtrl.SetFocus()
                return

            if len(vesync_country) != 2 or not vesync_country.isalpha():
                self._set_status(_("VeSync: Ländercode muss aus zwei Buchstaben bestehen"), error=True)
                ui.message(_("VeSync: Ungültiger Ländercode"))
                self.notebook.SetSelection(3)
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
                self._set_status(_("Cozytouch: Ungültige E-Mail-Adresse"), error=True)
                ui.message(_("Cozytouch: Ungültige E-Mail-Adresse"))
                self.notebook.SetSelection(4)  # Cozytouch tab
                self.cozytouchEmailCtrl.SetFocus()
                return
            if not cozytouch_password:
                self._set_status(_("Cozytouch: Bitte Passwort eingeben"), error=True)
                ui.message(_("Cozytouch: Bitte Passwort eingeben"))
                self.notebook.SetSelection(4)
                self.cozytouchPasswordCtrl.SetFocus()
                return

        # At least one platform must be enabled
        if not use_meross and not use_netatmo and not use_vesync and not use_cozytouch:
            # Translators: Validation error: no platform selected.
            self._set_status(_("Mindestens eine Plattform muss aktiv sein"), error=True)
            ui.message(_("Mindestens eine Plattform aktivieren"))
            self.notebook.SetSelection(0)
            return

        # All OK - save
        self.plugin.email = email
        self.plugin.password = password
        self.plugin.auto_login = self.autoLoginCheckbox.GetValue()
        self.plugin.announce_external_changes = self.announceExternalCheckbox.GetValue()
        try:
            self.plugin.start_tab = self._startTabValues[self.startTabChoice.GetSelection()]
        except (IndexError, AttributeError):
            self.plugin.start_tab = 'devices'
        self.plugin.use_meross = use_meross
        self.plugin.use_netatmo = use_netatmo
        self.plugin.use_vesync = use_vesync
        self.plugin.netatmo_client_id = netatmo_client_id
        self.plugin.netatmo_client_secret = netatmo_client_secret
        self.plugin.netatmo_redirect_port = self.netatmoPortCtrl.GetValue()

        # If the VeSync credentials change, discard the existing tokens
        old_vesync_email = getattr(self.plugin, 'vesync_email', '')
        old_vesync_password = getattr(self.plugin, 'vesync_password', '')
        old_vesync_country = getattr(self.plugin, 'vesync_country_code', 'DE')
        credentials_changed = (
            vesync_email != old_vesync_email
            or vesync_password != old_vesync_password
            or vesync_country != old_vesync_country
        )
        self.plugin.vesync_email = vesync_email
        self.plugin.vesync_password = vesync_password
        self.plugin.vesync_country_code = vesync_country
        # Filter warning threshold (%) - parse tolerantly, clamp to 1..100,
        # invalid/empty = default 15.
        try:
            threshold = int(self.vesyncFilterThresholdCtrl.GetValue().strip() or "15")
        except ValueError:
            threshold = 15
        self.plugin.vesync_filter_threshold = max(1, min(100, threshold))
        if credentials_changed:
            self.plugin.vesync_token = ""
            self.plugin.vesync_account_id = ""
            self.plugin.vesync_region = ""

        # Save Cozytouch; discard the token when the credentials changed
        self.plugin.use_cozytouch = use_cozytouch
        old_cozytouch_email = getattr(self.plugin, 'cozytouch_email', '')
        old_cozytouch_password = getattr(self.plugin, 'cozytouch_password', '')
        cozytouch_changed = (
            cozytouch_email != old_cozytouch_email
            or cozytouch_password != old_cozytouch_password
        )
        self.plugin.cozytouch_email = cozytouch_email
        self.plugin.cozytouch_password = cozytouch_password
        if cozytouch_changed:
            self.plugin.cozytouch_token = ""
        # Rated capacity (liters) - parse tolerantly, invalid/empty = 0 (off)
        try:
            self.plugin.cozytouch_capacity_liters = max(0, int(
                self.cozytouchCapacityCtrl.GetValue().strip() or "0"))
        except ValueError:
            self.plugin.cozytouch_capacity_liters = 0

        # Write the notification checkboxes from the "Notifications" tab back
        # into the plugin. Platforms whose section was not visible in the
        # current session (platform disabled) stay unchanged - their values are
        # not overwritten.
        for attr_name, checkbox in getattr(self, '_notify_checkboxes', {}).items():
            try:
                setattr(self.plugin, attr_name, checkbox.GetValue())
            except Exception as e:
                log.debug(f"Ignorierter Fehler in on_ok: {e}")

        self.plugin.save_settings()

        log.info("Smart Home Einstellungen gespeichert")
        # Translators: Success message after saving the settings.
        ui.message(_("Einstellungen gespeichert"))
        self.EndModal(wx.ID_OK)

    # =========================================================================
    # =
    # Helpers
    # =========================================================================
    # =
    def _set_status(self, text, error=False):
        """Updates the status line AND speaks the text via ui.message.

        Previously the StaticText was often only set via SetLabel; NVDA does
        not announce StaticText changes automatically. Now a ui.message output
        happens in parallel so blind users are always informed.
        """
        # `if not self or not self.statusText` war der falsche Test: bei einem
        # bereits zerstoerten wx-Objekt wirft schon der Zugriff, nicht erst die
        # Methode. Deshalb das Destroy-Flag zuerst.
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
            if text:
                try:
                    ui.message(text)
                except Exception as e:
                    log.debug(f"Ignorierter Fehler in _set_status: {e}")
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


# ============================================================================
# Backward compatibility: old name -> new dialog
# ============================================================================
MerossSettingsDialog = SmartHomeSettingsDialog
