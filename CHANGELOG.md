# Changelog – Smart Home Control

## 26.7.4 (unveröffentlicht)

### Favoriten-Tastenbefehle – neu gedacht

Die bisherigen 18 Einzelbefehle („Favorit 1–9 umschalten", „Status von
Favorit 1–9 ansagen") hatten zwei Probleme. Erstens tauchten sie wegen
eines Fehlers im Dialog Tastenbefehle überhaupt nicht auf und ließen sich
deshalb nie belegen – die im Handbuch beschriebene Funktion war schlicht
unerreichbar (NVDA schrieb dazu bei jedem Start 18 Warnungen ins Log, ohne
dass die Oberfläche etwas zeigte). Zweitens wäre selbst die reparierte
Fassung unbequem gewesen: 18 Kürzel einzeln vergeben, und „Favorit 1" war
nur die Listenposition – ein neuer Favorit konnte verschieben, welches
Gerät ein gemerktes Kürzel schaltet.

Beides ersetzt jetzt die **Favoriten-Ebene** mit einem einzigen Kürzel.
Es ist wie die übrigen Zusatzbefehle **ohne Standard-Belegung** – eine
mitgelieferte Vorgabe ließe sich nicht verlässlich kollisionsfrei wählen
(Tastaturlayout, andere Add-ons, eigene Zuweisungen), und ein Kürzel, das
eine bestehende Belegung überschreibt, wäre schlimmer als gar keins. Zu
vergeben unter **NVDA-Menü → Optionen → Tastenbefehle → Kategorie „Smart
Home Control"**. Danach:

- Nach dem Kürzel meldet sich die Ebene mit **„Favorit wählen: Ziffer 1
  bis 9“** – sie sagt damit, dass sie wartet und was sie erwartet. Ein
  bloßes „Favoriten“ klang nach einer erledigten Aktion.
- **Ziffer 1–9 sagt sofort den Status** des Favoriten mit dieser Nummer an.
- **Dieselbe Ziffer ein zweites Mal** kurz danach **schaltet** ihn –
  dasselbe Doppeldruck-Muster wie bei NVDAs eigenen Befehlen, mit der in
  NVDA eingestellten Doppeldruck-Zeit. Die harmlose Auskunft kommt also
  sofort, das folgenreiche Schalten verlangt den bewussten zweiten Druck;
  ein Vertippen sagt nur etwas an, statt ein Gerät zu schalten.
- **Nicht schaltbare Favoriten** (Sensoren, Netatmo-Geräte) melden beim
  zweiten Druck „nicht schaltbar – im Geräte-Menü einstellbar". Bisher
  wiederholten sie an dieser Stelle wortlos den Status, was wirkte, als
  sei der Tastendruck ins Leere gegangen.
- **0 liest die Belegung vor** („1: Steckdose, 2: Ventilator, …"),
  **Escape bricht ab**.
- **Die Nummer gehört jetzt dem Gerät, nicht der Listenposition.** Sie
  wird beim Hinzufügen einmal vergeben und angesagt („Als Favorit 3
  hinzugefügt"), steht im Favoriten-Tab vor dem Gerätenamen und bleibt
  erhalten, wenn andere Favoriten entfernt werden. Bestehende Favoriten
  bekommen ihre Nummern beim ersten Start in der bisherigen Reihenfolge –
  es verschiebt sich also nichts.

## 26.7.3 (August 2026)

- **Cozytouch/Atlantic ist jetzt überall als experimentell gekennzeichnet.**
  Bisher stand der Hinweis nur im Abschnitt „Reifegrad" der Dokumentation –
  wer die Plattform im Einstellungsdialog einschaltete, bekam ihn nie zu
  sehen. Jetzt tragen der Reiter, das Kontrollkästchen zum Aktivieren, der
  Benachrichtigungs-Bereich und der Plattform-Knoten im Geräte-Menü die
  Kennzeichnung, und der Cozytouch-Reiter erklärt einleitend, was
  „experimentell" konkret bedeutet: getestet ist bisher nur eine
  Warmwasser-Wärmepumpe, andere Gerätetypen können falsch dargestellt
  werden. Auch die Add-on-Beschreibung im Store nennt es.
- **GitHub-Releases tragen jetzt den Changelog der jeweiligen Version** statt
  eines immer gleichen Textbausteins – auf Englisch, weil Release-Seiten
  international gelesen werden. Grundlage ist die neue Datei
  `CHANGELOG.en.md`; `build_addon.py relnotes` schneidet den passenden
  Abschnitt heraus. Fehlt er, bricht der Release-Lauf ab, statt ein Release
  ohne Changelog zu veröffentlichen; ebenso, wenn der Tag nicht zur Version
  in `manifest.ini` passt.
- **Die Startseite des Repositorys ist jetzt englisch.** Sie ist der
  „Homepage"-Link im Add-on-Store, und dort kommen Nutzer aus der ganzen
  NVDA-Welt an – die Erweiterung selbst spricht ja ohnehin beide Sprachen.
  Die deutsche Fassung steht unverändert in `README.de.md`; beide verlinken
  in der ersten Zeile aufeinander.

## 26.7.2 (August 2026)

### Verlauf – neu aufgebaut

Der Verlauf hat bisher zwei Dinge vermischt, die gegensätzliche
Anforderungen haben: Schaltvorgänge sind selten und einzeln wichtig,
Messwerte sind häufig und nur als Verlauf interessant. Beide lagen in
derselben Liste mit 5.000 Plätzen, weshalb die Messwerte nach und nach genau
das verdrängten, was man später sucht.

- **Schalten über die Favoriten-Tastenbefehle wird jetzt protokolliert.**
  Bisher landete nur im Verlauf, was über das Geräte-Menü geschaltet wurde –
  dasselbe Gerät über einen Favoriten-Befehl geschaltet, tauchte gar nicht
  auf. Ebenso ergänzt: das Umstellen des Diffusor-Modus.
- **Externe Schaltungen stehen jetzt im Verlauf.** Wird ein Gerät über die
  Hersteller-App, einen Sprachassistenten oder den Taster am Gerät
  geschaltet, wurde das zwar angesagt, aber nirgends festgehalten. Damit
  konnte der Verlauf ausgerechnet die Frage nicht beantworten, für die man
  ihn aufschlägt.
- **Jedes Ereignis zeigt seine Herkunft:** „ich", „extern" oder
  „automatisch". Bei externen Schaltungen steht bewusst nur „extern" – ob es
  die App, ein Sprachassistent oder der Taster war, geht aus der Meldung der
  Cloud nicht hervor.
- **Messwerte werden nur noch bei echten Änderungen gespeichert** (Temperatur
  ab 0,3 Grad, Luftfeuchte ab 2 %, CO₂ ab 50 ppm, Luftdruck ab 1 mbar), und
  spätestens stündlich einmal, damit ein gleichbleibender Wert nicht wie eine
  Datenlücke aussieht. Vorher wurde bei jedem Öffnen des Geräte-Menüs für
  jedes Gerät ein vollständiger Satz Werte geschrieben, auch wenn sich nichts
  geändert hatte.
- **Messwerte werden jetzt im Hintergrund erfasst**, nicht mehr nur beim
  Öffnen des Menüs. Die vorgesehene Sperre von 15 Minuten wirkte nie über das
  Schließen des Fensters hinaus – fünfmal Menü öffnen ergab fünf identische
  Einträge pro Gerät. Erst durch die Erfassung im Hintergrund ist der Verlauf
  ein Verlauf und nicht ein Protokoll der Menüöffnungen.
- **Ereignisse und Messwerte werden getrennt aufbewahrt:** Ereignisse ein
  Jahr, Messwerte 90 Tage. Ein Schaltvorgang kann damit nicht mehr von
  Messwerten verdrängt werden.
- **Der Verlaufs-Dialog hat zwei Ansichten.** „Ereignisse" zeigt die
  Schaltvorgänge mit Herkunft, nach Tagen gruppiert („Heute", „Gestern",
  danach das Datum) – das erspart bei der Sprachausgabe das Datum in jeder
  einzelnen Zeile. „Messwerte" zeigt je Gerät und Größe eine Zeile mit
  kleinstem, größtem, mittlerem und aktuellem Wert; die einzelnen Änderungen
  stehen im Detailfenster (Eingabetaste). Der Mittelwert ist zeitgewichtet,
  damit eine kurze unruhige Phase ihn nicht gegenüber einer langen ruhigen
  verzerrt.
- Beim Öffnen des Verlaufs wird die Trefferzahl nicht mehr angesagt; sie
  steht in der Statuszeile.
- **Vorhandene Verläufe werden einmalig umgestellt:** Alle bisherigen
  Schaltvorgänge bleiben erhalten, die gespeicherten Messwerte laufen
  rückwirkend durch denselben Änderungsfilter. Verworfen werden
  ausschließlich Wiederholungen; wie viele es waren, steht im NVDA-Log.

### Meross

- **Weniger Rauschen im NVDA-Log.** Bricht die Verbindung zur Meross-Cloud
  kurz ab – ein WLAN-Wechsel genügt –, meldet die mitgelieferte
  Meross-Bibliothek anschließend für *jedes* Gerät zwei Warnungen („Updating
  status for device …", „… changed its online status while manager was
  offline"). Bei zehn Geräten sind das zwanzig Zeilen auf einmal, obwohl
  nichts kaputt ist: die Bibliothek stellt damit nur den Status wieder her.
  Diese und einige weitere routinemäßige Bibliotheksmeldungen erscheinen jetzt
  als Debug-Eintrag statt als Warnung.
- Meldungen, die auf ein echtes Problem hinweisen können – fehlgeschlagenes
  Abonnieren, ungültige Signaturen, unbekannte Nachrichtenarten oder ein Push
  für ein nicht bekanntes Gerät – bleiben ausdrücklich sichtbar.

### Netatmo

- **Das Aufklappen eines Thermostats blockiert die Oberfläche nicht mehr.**
  Um den Namen des aktiven Heizprogramms anzuzeigen, wurde bisher direkt beim
  Aufklappen eine Anfrage an die Netatmo-Cloud geschickt – im selben Thread,
  der das Fenster bedient. Antwortete Netatmo mit „Service temporarily
  unavailable" (kommt auf deren Seite gelegentlich vor), stand das Fenster
  rund sieben Sekunden still, bei hängender Verbindung deutlich länger.
- **Heizprogramme und Raumaufteilung werden fünf Minuten zwischengespeichert.**
  Sie ändern sich nur, wenn man in der Netatmo-App etwas umbaut. Vorher löste
  jedes Auf- und Zuklappen eines Thermostats eine vollständige Abfrage aus –
  das war der schnellste Weg, Netatmos Anfragelimit näherzukommen. Nach einem
  Programmwechsel durch die Erweiterung wird der Speicher sofort verworfen,
  und die regelmäßige Geräteabfrage geht bewusst weiter direkt an die Cloud,
  damit ein echter Ausfall auch als solcher erkannt wird.
- **Vorübergehende Netatmo-Serverzustände** (503, 500, Anfragelimit) erscheinen
  im NVDA-Log nicht mehr als Fehler, sondern als Warnung mit einer Erklärung,
  was der Zustand bedeutet – sie sind kein Problem der Erweiterung oder der
  eigenen Einstellungen.

### Behobene Fehler

- **Steckdosenleisten konnten komplett aus dem Menü verschwinden,** wenn die
  Meross-Cloud die Kanalliste ohne den führenden Sammelkanal lieferte – dann
  galt das Gerät als einkanalig und beide Ausgänge fielen weg. Online- und
  Offline-Erkennung leiten die Ausgänge jetzt über denselben Weg ab.
- **Im selben Fall wurde die Statusmeldung des ersten Ausgangs verschluckt.**
  Ob ein Kanal ein Ausgang ist, wird nicht mehr an seiner Nummer festgemacht.
- **Kurz nach der Anmeldung** konnten Statusmeldungen einer Steckdosenleiste
  einander überschreiben und mit dem Gerätenamen statt dem Ausgangsnamen
  angesagt werden.
- **Der Verbrauch „heute"** war an den beiden Zeitumstellungstagen im Jahr um
  eine Stunde verschoben.
- **Beim Schließen der Einstellungen** während eines laufenden
  Verbindungstests oder der Netatmo-Anmeldung (bis zu 120 Sekunden) konnte
  ein Fehler im NVDA-Log erscheinen.
- **Favoriten behielten den Namen von damals,** wenn das Gerät später in der
  Hersteller-App umbenannt wurde. Sichtbar war das überall dort, wo auf den
  gespeicherten Namen zurückgegriffen wird – etwa bei nicht erreichbaren
  Geräten und in den Ansagen der Favoriten-Befehle.
- **Beim ersten Verschlüsseln der Zugangsdaten ohne Windows-DPAPI** blitzte
  kurz ein Konsolenfenster auf und zog den Fokus.
- Die Statuszeile der Meross-Hub-Sensoren und der Hinweis auf ein unbekanntes
  Cozytouch-Modell erschienen auch in der englischen Oberfläche auf Deutsch.

### Intern

- Der Hintergrund-Scheduler wacht nicht mehr jede Sekunde auf, sondern
  schläft bis zur nächsten fälligen Abfrage und wird beim Öffnen des
  Geräte-Menüs gezielt geweckt. Der Abstand zwischen zwei Abfragen wird
  außerdem ab dem Ende der vorherigen gerechnet – bei langsamer Verbindung
  rückten die Abfragen sonst zusammen, ausgerechnet bei Meross mit seinem
  Nachrichtenlimit.
- Alle Module haben jetzt einen Rückfall für die Übersetzungsfunktion; bisher
  hätte ein Fehlschlag beim Laden der Übersetzungen zu einem Folgefehler
  mitten im Dialogaufbau geführt.
- `build_addon.py` prüft beim Paketieren, ob alle Texte der Oberfläche in der
  Übersetzungsdatei stehen und ob die kompilierte Fassung dazu passt; ein
  Paket ohne Übersetzungsdatei wird abgelehnt. Neu außerdem
  `build_addon.py licenses`, das die Übersicht der mitgelieferten
  Fremdkomponenten aus deren Metadaten erzeugt.
- `.gitignore` schloss die kompilierte Übersetzungsdatei aus – aus einem
  frischen Klon des Repos wäre ein Add-on ohne englische Oberfläche
  entstanden.

### Behobene Fehler bei Mehrfach-Steckdosen

- **Steckdosenleisten mit mehr als zwei Ausgängen** (MSS425, MSS425E,
  MSS425F) zeigten im Offline-Zustand nur zwei Ausgänge – und die falschen:
  was als „Ausgang 1" angesagt wurde, war physisch ein anderer. Die Ausgänge
  werden jetzt aus den Kanaldaten der Meross-Cloud abgeleitet statt geraten,
  damit online und offline dieselben Ausgänge in derselben Reihenfolge
  erscheinen.
- **Die in der Meross-App vergebenen Ausgangsnamen** (z. B. „Pumpe" statt
  „Ausgang 1") werden jetzt auch bei offline Geräten angezeigt, nicht mehr
  nur im Online-Zustand.
- **Favoriten auf einzelnen Ausgängen** blieben nicht erhalten, wenn das
  Gerät zwischenzeitlich offline war, weil die Kennung des Ausgangs offline
  eine andere war als online. Beide Wege verwenden nun dieselbe Kennung.
- **Ausgänge konnten dauerhaft den falschen Status anzeigen:** Nach der
  ersten Statusmeldung über die Meross-Cloud wurde für diesen Ausgang nicht
  mehr nachgefragt. Wurde danach über die Meross-App oder am Gerät selbst
  geschaltet, blieb die Anzeige für den Rest der NVDA-Sitzung falsch – auch
  nach dem Aktualisieren. Der Status wird jetzt wieder regelmäßig abgeglichen.
- **Nach einer Verbindungsunterbrechung** lasen die Ausgänge ihren Status aus
  einer veralteten Verbindung und froren ein.
- **Außensteckdose MOP320** wurde im Offline-Zustand nicht als Steckdose mit
  Verbrauchsmessung erkannt.
- Offline **MSL-Lampen** konnten beim Abfragen des Farbmodus einen Fehler im
  NVDA-Log auslösen.
- Abfrage des Stromverbrauchs an einem Ausgang eines offline Mehrfachgeräts
  schlug fehl.

### Weitere Geräte

- **Luftreiniger und Ventilatoren mit europäischer Modellkennung** (z. B.
  `LAP-C201S-WEU`) wurden bisher gar nicht angezeigt, weil nur bestimmte
  Länder-Varianten namentlich hinterlegt waren. Die Modellerkennung
  berücksichtigt jetzt die Baureihe unabhängig vom Länderkürzel – betrifft
  die Levoit Core 200S, 300S und 400S sowie künftige Regionsvarianten der
  Tower-Ventilatoren.

### Dokumentation

- Readmes auf sachliche Richtigkeit geprüft und korrigiert: die
  Hub-Zuordnungstabelle (MSH300/MSH450) entfiel, da sie weder dem Verhalten
  der Erweiterung noch den Herstellerangaben entsprach; MSS425E und MSS425F
  sind jetzt namentlich genannt, inklusive des Hinweises, dass die
  USB-Anschlüsse dieser Leisten nur gemeinsam schaltbar sind; die Angaben zum
  Meross-Nachrichtenlimit wurden nach schriftlicher Support-Auskunft
  präzisiert (Vorwarnung, dreitägige Frist, danach 24-Stunden-Sperre des
  betroffenen Geräts); Lizenz und mitgelieferte Fremdkomponenten sind jetzt
  aufgeführt.

### Sicherheit

- **CSV-Export:** Gerätenamen kommen aus der Hersteller-Cloud. Begann ein Name
  mit einem Formelzeichen (`=`, `+`, `-`, `@`), wertete Excel bzw. LibreOffice
  die Zelle beim Öffnen als Formel statt als Text aus. Die Textspalten des
  Exports werden jetzt entschärft; die Zahlenspalten bleiben als Zahlen
  nutzbar.

### Intern

- Modelllisten für Steckdosen und Verbrauchsmessung liegen jetzt zentral an
  einer Stelle, statt in Online- und Offline-Pfad doppelt geführt zu werden.
- Absicherung der Übersetzungs-Initialisierung im Hauptmodul (ein Fehlschlag
  hätte den Import des gesamten Add-ons verhindert statt nur die
  Übersetzungen).

## 26.07.1 (Juli 2026)

### Neue Funktionen

- **Energie-Auswertung:** Ein neuer, frei belegbarer Befehl sagt den
  Verbrauch der Messsteckdosen von heute und der letzten 7 Tage in
  Kilowattstunden an. Primäre Quelle ist der Verbrauchszähler im Gerät
  selbst (zählt auch, wenn NVDA nicht läuft); als Fallback dienen im
  Hintergrund gesammelte Leistungs-Stichproben, gekennzeichnet als
  „geschätzt".
- **Favoriten-Direktgesten:** Je neun frei belegbare Befehle „Favorit N
  umschalten" und „Status von Favorit N ansagen" – schalten bzw. abfragen
  ohne geöffnetes Menü. Alle ohne Standard-Belegung (zuweisbar unter
  NVDA-Menü → Optionen → Tastenbefehle).
- **Verbindungsdiagnose:** Frei belegbarer Befehl, der pro Plattform den
  Verbindungsstatus, den Netzwerkstatus und die Restlaufzeit des
  Netatmo-Tokens ansagt.
- **Cozytouch Boost-Laufzeit:** Die Boost-Dauer lässt sich jetzt im
  Geräteeintrag ändern (experimentell – ob die Cloud den Schreibzugriff
  akzeptiert, wird am tatsächlichen Gerätewert überprüft). Bei aktivem
  Boost ohne gesetzte Laufzeit wird „keine Zeitbegrenzung gesetzt"
  angezeigt.
- **Netatmo-Räume:** Thermostate und Heizkörperventile werden im
  Geräte-Menü nach den Räumen aus der Netatmo-App gruppiert; der Raumname
  steht zusätzlich im Gerätenamen.

### Bedienung & Mehrkanalgeräte

- Kanäle von Steckdosenleisten/Doppelsteckdosen heißen jetzt einheitlich
  „Gerätename: Ausgang X" (z. B. „Garten: Ausgang 1" oder – bei benanntem
  Ausgang – „Garten: Ausgang Pumpe"); das frühere doppelte „Kanal:"-Präfix
  entfällt.
- Offline-Meross-Geräte werden jetzt schon im Gerätenamen als „offline"
  gekennzeichnet (auch Mehrkanalgeräte), und die einzelnen Ausgänge eines
  offline Mehrkanalgeräts lassen sich aufklappen (zeigen „Status: Offline").
- Neue Einstellung „Beim Öffnen anzeigen": Es kann festgelegt werden, ob
  beim Öffnen des Geräte-Menüs der Tab „Alle Geräte" oder „Favoriten"
  aktiv ist.

### Geräteunterstützung

- Meross MSS425 (Mehrfach-Steckdosenleiste) und MOP320 (Outdoor-Doppelsteckdose
  mit Energiemessung) werden jetzt korrekt als Steckdosen erkannt (vorher
  Kategorie „Andere Geräte" bzw. nicht erkannt).
- Korrektur der Sensor-Zuordnung: Als Wassersensoren werden MS400 und
  MS405 erkannt.
- Cozytouch: Das genaue Gerätemodell wird jetzt im Geräte-Menü angezeigt
  (wie bei den anderen Plattformen); unbekannte Modelle zeigen die Modell-ID.
- Cozytouch: WLAN-Signal und WLAN-Netz stehen jetzt gemeinsam mit der
  Firmware-Version im Technik-Block am Ende des Geräteeintrags.

### Weitere Korrekturen

- Cozytouch: Beim Umstellen des Heizmodus (v.a. auf „Programm") kam
  fälschlich ein Fehlerton, obwohl die Umstellung funktionierte – und die
  eigene Änderung wurde anschließend als externe Änderung gemeldet. Die
  Atlantic-Cloud meldet solche Befehle teils als „fehlgeschlagen"
  (Ausführungs-Status 4), obwohl das Gerät sie übernimmt. Die Erweiterung
  verlässt sich deshalb nicht mehr auf diese Meldung, sondern prüft den
  tatsächlich am Gerät angekommenen Wert nach.
- Cozytouch: Weicht das tatsächliche Heizziel (Eco+/Boost) vom Soll ab,
  zeigt schon der eingeklappte Geräteeintrag beides an (z. B. „Ziel 58°C,
  aktuelles Heizziel 53,2°C") – vorher wirkte die Soll-Anzeige irreführend.
- Gerätelisten korrigiert (nach Recherche): MSH450 unterstützt den MS100F
  (nicht den originalen MS100, der braucht den MSH300);
  Fenster-offen-Erkennung besitzt nur das Heizkörperventil NRV, nicht das
  NATherm1; die unterstützten Levoit-Core-Reiniger haben weder Turbo- noch
  Haustier-Modus (Auto fehlt beim Core200S); das Meross-Cloud-Limit sperrt
  für 24 Stunden. Klargestellt: Nur Netatmo bietet eine offizielle API –
  Meross, VeSync und Cozytouch sind reverse-engineert.

### Dokumentation

- Beide Hilfe-Dateien (Deutsch/Englisch) und die README überarbeitet:
  Du-Form, klarere Gliederung der unterstützten Geräte, Tastenkürzel als
  Liste statt Tabelle, strukturierte Einrichtung pro Plattform und ein
  ausführlich erklärter Netatmo-Abschnitt (Redirect-URI und Port).

### Sicherheit & Bibliotheken

Alle gebündelten Bibliotheken aktualisiert, insbesondere wegen bekannter
Sicherheitslücken: urllib3 2.5.0 → 2.7.0 (CVE-2025-66418, CVE-2025-66471,
CVE-2026-21441, CVE-2026-44432) und aiohttp 3.13.1 → 3.14.1 (u.a.
CVE-2026-34993). Außerdem: requests 2.34.2, certifi 2026.6.17, idna 3.18,
attrs 26.1.0, aiohappyeyeballs 2.7.1, typing_extensions 4.16.0,
multidict 6.7.1, yarl 1.24.2, propcache 0.5.2, charset_normalizer 3.4.9 –
jeweils für beide NVDA-Architekturen (32-Bit/Python 3.11 und
64-Bit/Python 3.13).

### Fehlerbehebungen

- Log-Meldungen der Favoriten- und Verlaufs-Module erscheinen jetzt korrekt
  im NVDA-Log (vorher gingen sie verloren, auch Fehler beim Speichern).
- Netatmo: Token-Erneuerung ist jetzt thread-sicher. Vorher konnten zwei
  gleichzeitige Erneuerungen (Hintergrund-Aktualisierung + Dialog-Aktion)
  im ungünstigsten Fall die gespeicherte Anmeldung ungültig machen.
- Verlauf: Einträge werden jetzt gesammelt gespeichert (spätestens alle
  30 Sekunden bzw. nach 20 Einträgen) statt bei jedem Eintrag die gesamte
  Datei neu zu schreiben. Beim Beenden von NVDA wird der Rest gesichert.
- VeSync: HTTP-Verbindungen werden jetzt wiederverwendet (schnellere
  Reaktionszeiten beim Gerätedialog, weniger Netzwerklast).
- CSV-Export des Verlaufs: Datei wird in jedem Fehlerfall sauber geschlossen.

### Intern

- Die Hauptdialog-Klasse heißt jetzt `SmartHomeControlDialog` (vorher
  `MerossDeviceDialog` – ein Überbleibsel aus der Meross-only-Anfangszeit).
- Großes Modul-Refactoring (Verhalten unverändert): Der Geräte-Dialog wurde
  in eigenständige Module aufgeteilt (Verlaufs-Dialog, Kontexthilfe,
  Favoriten-Ansicht), das Plugin-Hauptmodul ebenso (Passwort-Verwaltung,
  Polling-Scheduler, Änderungserkennung). Kein Modul überschreitet mehr
  3000 Zeilen; die Aufteilung folgt dem bestehenden Mixin-Muster.

### Sonstiges

- Neues Build-System: `build_addon.py` + `requirements-bundle.txt` erzeugen
  das Lib-Bundle und das Add-on-Paket reproduzierbar.
- Lizenz (GPL v2 oder später) und Changelog ergänzt.
- Interne Aufräumarbeiten (BOM-Zeichen entfernt, .gitignore).

## 26.07 (Juli 2026)

- Englische Übersetzung (Oberfläche und Dokumentation).
- Benutzerhandbuch (readme) auf Deutsch und Englisch integriert.

## 26.05 (Mai 2026)

- Unterstützung für Cozytouch/Atlantic-Warmwasser-Wärmepumpen
  (z.B. Austria Email Revolution) inkl. Boost, Abwesenheit, Zieltemperatur.
- Vier Plattformen: Meross, Netatmo, VeSync/Levoit, Cozytouch/Atlantic.
- Favoriten, Verlauf mit CSV-Export, externe Änderungserkennung,
  Hintergrund-Aktualisierung mit einheitlichem Scheduler.
