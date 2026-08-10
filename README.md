# Smart Home Control

- Autor: Philipp Hasel
- NVDA-Kompatibilität: ab NVDA 2025.1 und neuer
- Lizenz: GNU General Public License, Version 2
- [Internetseite und Quellcode](https://github.com/BlindByte06/SmartHomeControl)

Mit dieser Erweiterung lassen sich Smart-Home-Geräte direkt aus NVDA
steuern – über ein einfaches Menü. Es können Geräte ein- und ausgeschaltet
sowie Helligkeit, Farbe, Heizung und Luftreiniger gesteuert und Sensorwerte
abgefragt werden, ganz ohne die teils schwer zugänglichen Hersteller-Apps.

Die Anmeldung erfolgt einmalig mit den Zugangsdaten des jeweiligen
Hersteller-Kontos – eine zusätzliche Server- oder Hintergrund-Einrichtung
ist nicht nötig. Die Zugangsdaten werden lokal auf dem Rechner verschlüsselt
gespeichert.

---

## Inhalt

- [Tastenkürzel](#tastenkürzel)
- [Unterstützte Plattformen und Geräte](#unterstützte-plattformen-und-geräte)
- [Einrichtung](#einrichtung)
- [Netatmo: Redirect-URI und Port](#netatmo-redirect-uri-und-port)
- [Bedienung](#bedienung)
- [Benachrichtigungen bei Änderungen](#benachrichtigungen-bei-änderungen)
- [Hinweise zu Cloud-Limits](#hinweise-zu-cloud-limits)
- [Reifegrad und Hinweise](#reifegrad-und-hinweise)
- [Datenschutz und Sicherheit](#datenschutz-und-sicherheit)
- [Fehlerbehebung](#fehlerbehebung)
- [Lizenz und verwendete Komponenten](#lizenz-und-verwendete-komponenten)

---

## Tastenkürzel

- **NVDA + Umschalt + H**: Smart-Home-Menü öffnen (Geräteübersicht)
- **NVDA + Strg + Umschalt + P**: Status aller Geräte ansagen

Der Einstellungs-Dialog ist über die Schaltfläche „Einstellungen (Alt+E)"
im Geräte-Menü erreichbar.

Alle Befehle lassen sich mit eigenen Tastenkürzeln belegen; auch die
Standard-Belegung kann geändert werden: **NVDA-Menü → Optionen →
Tastenbefehle → Kategorie „Smart Home Control"**.

Folgende Befehle haben bewusst **keine Standard-Belegung** und werden bei
Bedarf dort mit einem eigenen Kürzel versehen:

- **Energieverbrauch ansagen** – Tages- und 7-Tage-Verbrauch der
  Messsteckdosen in Kilowattstunden. Die Werte kommen bevorzugt direkt vom
  Verbrauchszähler im Gerät – der zählt auch weiter, wenn NVDA nicht
  läuft. Nur wenn ein Gerät diese Abfrage nicht unterstützt, wird auf im
  Hintergrund gesammelte Stichproben zurückgegriffen (dann als „geschätzt"
  gekennzeichnet, da sie nur die NVDA-Laufzeit abdecken).
- **Verbindungsdiagnose** – sagt pro Plattform den Verbindungsstatus an,
  dazu Netzwerkstatus und die Restlaufzeit des Netatmo-Tokens.
- **Favorit 1–9 umschalten** und **Status von Favorit 1–9 ansagen** –
  schaltet das jeweilige Favoriten-Gerät bzw. sagt dessen Status an, ohne
  das Menü zu öffnen. Die Nummer entspricht der Reihenfolge im
  Favoriten-Tab des Geräte-Menüs (Nummer 1 ist der oberste Eintrag).
  Favoriten legt man im Geräte-Menü an: Gerät auswählen und den Eintrag
  „Zu Favoriten hinzufügen" aktivieren.

---

## Unterstützte Plattformen und Geräte

Die Erweiterung unterstützt vier Smart-Home-Plattformen. Jede lässt sich
einzeln aktivieren; es wird nur benötigt, was tatsächlich verwendet wird.

### Meross

Die Anmeldung erfolgt mit E-Mail und Passwort des Meross-Kontos. Auch
externe Änderungen (z. B. Schalten über die Meross-App, Alexa oder den
Schalter am Gerät) werden angesagt.

#### Steckdosen und Steckdosenleisten

- **MSS210** – Schaltbare Steckdose (Ein/Aus)
- **MSS310** – Steckdose mit Energiemessung (Leistung, Spannung, Strom)
- **MSS315** – Steckdose mit Energiemessung (Leistung, Spannung, Strom)
- **MSS425**, **MSS425E**, **MSS425F** – Steckdosenleisten; jede Steckdose
  ist einzeln schaltbar. Die USB-Anschlüsse dieser Leisten bilden einen
  gemeinsamen Ausgang und lassen sich daher nur zusammen schalten. Ausgänge,
  die in der Meross-App einen eigenen Namen haben, erscheinen mit diesem
  Namen.
- **MSS620** – Outdoor-Doppelsteckdose; beide Ausgänge einzeln schaltbar
  (die Taste am Gerät selbst schaltet immer beide gemeinsam)
- **MOP320** – Outdoor-Doppelsteckdose mit Energiemessung; beide Ausgänge
  einzeln schaltbar

#### Lampen und LED-Strips

- **MSL320** (LED-Strip), **MSL450**, **MSL610**
- Weitere MSL-Modelle werden automatisch erkannt und erhalten dieselben
  Funktionen, soweit das jeweilige Modell sie unterstützt: Ein/Aus,
  Helligkeit, RGB-Farbe, Farbtemperatur und Weiß-Voreinstellungen.

#### Aroma-Diffuser

- **MOD150** – Sprühsteuerung: aus, schwaches oder starkes Sprühen
  (die Licht-Funktion des Geräts wird von der Erweiterung nicht gesteuert)

#### Hubs und Sensoren

Die Sensoren verbinden sich über einen Meross-Hub. Beide Hub-Generationen
(**MSH300** und **MSH450**) werden automatisch erkannt; welcher Hub welchen
Sensor aufnehmen kann, legt Meross fest und steht in der Meross-App.

Unterstützte Sensoren:

- **MS100** und **MS100F** – Temperatur- und Feuchtesensor
- **MS130** – Temperatur- und Feuchtesensor mit Display
- **MS400**, **MS405** – Wasserleck-Sensoren

### Netatmo

Heizungssteuerung und Wetterstations-Anzeige über das Netatmo-Konto.

#### Heizung

- **NATherm1** – Raum-Thermostat
- **NRV** – Smartes Heizkörperventil
- **NAPlug** – Relais/Gateway (Bridge, für NATherm1 erforderlich)

Thermostate und Heizkörperventile werden nach den in der Netatmo-App
vergebenen Räumen gruppiert; der Raumname wird auch im Gerätenamen
angesagt.

Einstellbar sind: Solltemperatur (manuell, mit wählbarer Dauer), die
Betriebsmodi **Zeitplan**, **Abwesend** und **Frostschutz** sowie der
Wechsel des aktiven Heizprogramms. Angezeigt werden außerdem:
Ist-Temperatur, aktueller Modus (inklusive „Maximum", falls über die App
gesetzt), Kessel-/Brenner-Status, aktive Zeitplan-Zone, Vorheizen
(Anticipation), Batteriestand sowie die Fenster-offen-Erkennung (diese
Funktion hat nur das Heizkörperventil NRV, nicht das Raum-Thermostat).

#### Wetter und Raumluft (nur Anzeige)

- **Wetterstation NAMain** mit Außen-, Wind-, Regen- und
  Zusatz-Innenmodulen
- **Raumluft-Monitor NHC**

Angezeigt werden: Temperatur, Luftfeuchtigkeit, CO₂, Lautstärke, Luftdruck,
Regen und Wind – reine Anzeige, keine Steuerung.

### VeSync / Levoit

Die Anmeldung erfolgt mit E-Mail, Passwort und Länder-Code des
VeSync-Kontos.

#### Luftreiniger (Levoit Core)

- **Core200S**, **Core300S**, **Core400S**, **Core500S**, **Core600S**
- ebenso die regionalen Varianten dieser Baureihen, die VeSync als
  **LAP-C201S**, **LAP-C202S**, **LAP-C301S**, **LAP-C302S**, **LAP-C401S**,
  **LAP-C501S** und **LAP-C601S** meldet – unabhängig vom Länderkürzel am
  Ende der Modellbezeichnung (z. B. `-WEU`, `-WUSR`, `-WJP`).

Verfügbare Funktionen: Ein/Aus, Modus (Manuell und Schlaf; Auto bei allen
Modellen außer dem Core200S), Lüfterstufe, Display, Kindersicherung,
Auto-Profil (Standard/Effizient/Leise), Luftqualität sowie
Filter-Restlebensdauer mit Warnung bei niedrigem Wert. Der **Core200S**
(und seine Varianten LAP-C201S/C202S) hat zusätzlich ein steuerbares
**Nachtlicht** (Ein/Aus/Gedimmt).

#### Tower-Ventilatoren (Levoit)

- **LTF-F422S**-Serie, ebenfalls unabhängig vom Länderkürzel
  (getestet: KEU, WUSR, WJP, WUS)

Verfügbare Funktionen: Ein/Aus, Modus (Normal, Auto, Turbo, Schlaf),
Lüfterstufe, Oszillation, Stummschaltung und Display.

Andere Gerätetypen im VeSync-Konto (z. B. Steckdosen, Lampen oder
Luftbefeuchter) werden derzeit nicht angezeigt.

### Cozytouch / Atlantic

Die Anmeldung erfolgt mit E-Mail und Passwort des Cozytouch-/
Atlantic-Kontos.

- **Warmwasser-Wärmepumpe** (getestet: Austria Email Revolution Evo 3; das
  genaue Modell wird im Geräte-Menü angezeigt)

Verfügbare Funktionen: Warmwasser-Erzeugung ein/aus, Zieltemperatur
(inklusive des tatsächlichen Heizziels bei Eco/Boost; eine gemessene
Wassertemperatur liefert die Cloud bei diesem Modell nicht), Heizmodus,
Boost-Funktion (inklusive experimentell einstellbarer Boost-Laufzeit),
Abwesenheits-Modus mit planbarem Zeitraum, verfügbarer
Warmwasservorrat in Prozent sowie Anzeige der heute programmierten
Heizzeiten und des Status von Elektro-Heizstab und Niedertarif. Die
Nennkapazität (in Litern) lässt sich in den Einstellungen hinterlegen.

Die drei Heizmodi:

- **Manuell** – heizt dauerhaft auf die eingestellte Zieltemperatur.
- **Eco+** – heizt energiesparend mit abgesenktem Heizziel; das
  tatsächliche Heizziel wird im Geräteeintrag angezeigt.
- **Programm** – heizt nur innerhalb der Zeitfenster, die in der
  Cozytouch-App festgelegt wurden (bis zu drei pro Tag, z. B. für
  Niedertarif-Zeiten). Die Zeitfenster selbst lassen sich nur in der
  Cozytouch-App bearbeiten; die Erweiterung zeigt die heute programmierten
  Heizzeiten im Geräteeintrag an.

---

## Einrichtung

Voraussetzung: Die Geräte müssen vorab einmal mit der jeweiligen
Hersteller-App (Meross, Netatmo, VeSync, Cozytouch) eingerichtet worden
sein – die Erweiterung übernimmt sie dann aus dem Konto.

Das Menü wird mit **NVDA + Umschalt + H** geöffnet. Ohne bestehende
Anmeldung öffnet sich automatisch der Einstellungs-Dialog. Dort die
gewünschten Plattformen aktivieren und die Zugangsdaten eintragen – Details
pro Plattform in den folgenden Abschnitten. Zum Schluss: optional
„Automatische Anmeldung" aktivieren (die Verbindung wird dann bei jedem
NVDA-Start automatisch aufgebaut) und speichern. Die Geräte werden geladen
und stehen sofort im Menü zur Verfügung – ein NVDA-Neustart ist nicht
nötig.

### Meross einrichten

E-Mail und Passwort des Meross-Kontos eintragen – fertig.

### Netatmo einrichten

Netatmo verwendet eine Browser-Anmeldung (OAuth2) statt E-Mail/Passwort in
der Erweiterung. Einmalig nötig:

1. Eine eigene (kostenlose) App auf [dev.netatmo.com](https://dev.netatmo.com)
   anlegen; dort werden eine **Client-ID** und ein **Client-Secret**
   ausgestellt.
2. Beides im Netatmo-Tab der Erweiterung eintragen.
3. Die im Tab angezeigte **Redirect-URI** bei der Netatmo-App hinterlegen –
   Details und was es mit dem Port auf sich hat, erklärt der Abschnitt
   [Netatmo: Redirect-URI und Port](#netatmo-redirect-uri-und-port).
4. Auf „Mit Netatmo verbinden (OAuth2)" gehen, im Browser anmelden und
   bestätigen.

### VeSync / Levoit einrichten

E-Mail, Passwort und Länder-Code des VeSync-Kontos eintragen.

### Cozytouch / Atlantic einrichten

E-Mail und Passwort des Cozytouch-Kontos eintragen (dieselben wie in der
Cozytouch-App). Optional: die Nennkapazität des Warmwasserspeichers in
Litern, damit der Vorrat zusätzlich in Litern geschätzt wird.

---

## Netatmo: Redirect-URI und Port

### Was ist die Redirect-URI?

Bei der Browser-Anmeldung schickt Netatmo die Freigabe an eine vorher
festgelegte Adresse zurück – die Redirect-URI. Die Erweiterung zeigt sie im
Netatmo-Tab an. Standardmäßig lautet sie **exakt**:

```
http://localhost:8474/callback
```

Genau diese Adresse muss in der Netatmo-App auf dev.netatmo.com im Feld
„redirect URI" eingetragen werden – am besten unverändert aus der
Erweiterung kopieren.

### Warum muss die Adresse exakt stimmen?

Netatmo vergleicht die registrierte Redirect-URI Zeichen für Zeichen mit
der tatsächlich verwendeten. Schon ein Unterschied bei Schema (`http`),
Host (`localhost` ist nicht dasselbe wie `127.0.0.1`), Port oder Pfad führt
zur Fehlermeldung `redirect_uri mismatch`.

### Wozu der Port und warum 8474?

Während der Anmeldung startet die Erweiterung kurzzeitig einen kleinen
lokalen Webserver, der die Freigabe von Netatmo entgegennimmt. Der Port
(Standard **8474**) legt fest, auf welchem „Kanal" dieser Server lauscht.
Er ist nur lokal und nur für den Moment der Anmeldung aktiv; nach außen ist
nichts geöffnet. 8474 ist bewusst ein unauffälliger, selten belegter Port.

Ist der Port bereits belegt (die Anmeldung schlägt mit einer Port-Meldung
fehl), im Netatmo-Tab einfach den **Redirect-Port** auf einen freien Wert
ändern – und die dann neu angezeigte Redirect-URI wieder bei
dev.netatmo.com eintragen, damit beide Seiten übereinstimmen.

---

## Bedienung

Im Geräte-Menü sind die Geräte nach Plattform und Typ in einer Baumansicht
gruppiert. Navigiert wird mit den Pfeiltasten; mit Eingabe oder Leertaste
wird geschaltet oder ein Wert geändert. Häufig genutzte Geräte können als
Favoriten markiert werden (Eintrag „Zu Favoriten hinzufügen" am Gerät); sie
erscheinen dann zusätzlich im Favoriten-Tab. In den Einstellungen lässt sich
festlegen, welcher Tab (alle Geräte oder Favoriten) beim Öffnen des Menüs
zuerst erscheint.

Jede Aktion gibt eine sofortige Sprach- und Signalton-Rückmeldung. Bei
geöffnetem Menü werden die Geräte häufiger aktualisiert, damit die Werte
stets aktuell sind.

---

## Benachrichtigungen bei Änderungen

Auch externe Änderungen werden angesagt – zum Beispiel, wenn ein Gerät über
die Hersteller-App, Alexa oder den Schalter am Gerät geschaltet wird. Im
Tab „Benachrichtigungen" lässt sich pro Plattform und Ereignistyp
festlegen, was angesagt wird (z. B. Schalten, Modus, Lüfterstufe,
Luftqualität, Filter, Thermostat-Soll, Kessel-Status). Eigene Aktionen im
Dialog werden nicht doppelt gemeldet.

---

## Hinweise zu Cloud-Limits

Meross begrenzt die Cloud auf **200 Nachrichten pro Stunde und Gerät** – laut
Auskunft des Meross-Supports eine Schutzmaßnahme gegen Server-Überlastung.
Wird das Limit dauerhaft überschritten, verschickt Meross zunächst eine
Warnung („cloud termination notice"). Sendet dasselbe Gerät drei Tage nach
dieser Warnung weiterhin zu viele Nachrichten, wird **dieses Gerät** für
24 Stunden gesperrt. Andere Geräte und das Konto selbst sind davon nicht
betroffen.

Die Erweiterung bleibt automatisch darunter: die regelmäßige Abfrage ist
bewusst zurückhaltend eingestellt, Ein/Aus-Änderungen kommen ohnehin in
Echtzeit per Push, und zusätzlich begrenzt die Erweiterung die Anfragen pro
Gerät selbst. Sollte ein einzelnes Gerät die Obergrenze doch einmal
erreichen, wird es vorübergehend seltener abgefragt; eine Meldung nennt dann
das betroffene Gerät. Alle anderen Geräte laufen normal weiter.

---

## Reifegrad und Hinweise

Einzig Netatmo bietet eine offizielle, dokumentierte
Programmierschnittstelle. Meross, VeSync und Cozytouch sind nachgebaute
(reverse-engineerte) Cloud-Anbindungen ohne offizielle Schnittstelle – für
Meross hat der Hersteller-Support auf Anfrage ausdrücklich bestätigt, dass es
keine offizielle Programmierschnittstelle gibt. Sie funktionieren zuverlässig
mit den getesteten Geräten, können aber bei Server-Änderungen der Hersteller
vorübergehend ausfallen.

Die Plattformen sind unterschiedlich weit erprobt:

- **Meross** und **Netatmo** gelten als stabil.
- **VeSync/Levoit:** Unterstützt werden ausschließlich Levoit-Luftreiniger
  der Core-Reihe und Levoit-Tower-Ventilatoren. Andere VeSync-Geräte
  (Steckdosen, Lampen, Luftbefeuchter, Küchengeräte) werden nicht
  angezeigt.
- **Cozytouch/Atlantic (experimentell):** Getestet wurde bisher nur die
  Warmwasser-Wärmepumpe Austria Email Revolution Evo 3. Andere
  Cozytouch-Geräte (z. B. Heizkörper, Klimageräte) werden derzeit fälschlich
  ebenfalls als Warmwasser-Wärmepumpe dargestellt und sind nicht nutzbar.

---

## Datenschutz und Sicherheit

- Die Zugangsdaten werden ausschließlich **lokal auf dem Rechner** und
  **verschlüsselt** gespeichert. Passwörter liegen nie im Klartext in der
  Konfiguration.
- Die Kommunikation läuft direkt mit den jeweiligen Hersteller-Clouds – es
  werden keine Daten an Dritte gesendet.

---

## Fehlerbehebung

- **Keine Geräte sichtbar:** Zugangsdaten und aktivierte Plattform im
  Einstellungs-Dialog prüfen; danach speichern (die Anmeldung läuft im
  Hintergrund).
- **Gerät offline:** Prüfen, ob das Gerät in der Hersteller-App erreichbar
  ist.
- **Eine Plattform meldet „nicht erreichbar":** meist eine vorübergehende
  Cloud-/Netzwerkstörung – die Erweiterung verbindet sich automatisch
  wieder und sagt an, sobald die Plattform wieder verbunden ist.

---

## Lizenz und verwendete Komponenten

Smart Home Control steht unter der **GNU General Public License, Version 2**
(siehe Datei `LICENSE`).

Die Erweiterung bringt die benötigten Python-Bibliotheken mit, damit keine
Zusatzinstallation nötig ist. Alle Pakete werden unverändert von PyPI
übernommen; ihre vollständigen Lizenztexte liegen im Add-on-Paket unter
`lib/`.

| Komponente | Zweck | Lizenz |
|---|---|---|
| meross-iot | Meross-Cloud und MQTT | MIT |
| paho-mqtt | MQTT-Protokoll | EPL-2.0 / **EDL-1.0** |
| requests, urllib3, idna, certifi, charset-normalizer | HTTPS-Aufrufe | Apache-2.0, MIT, MPL-2.0 |
| aiohttp, yarl, multidict, frozenlist, propcache, aiosignal, aiohappyeyeballs, attrs | asynchrone HTTP-Aufrufe | Apache-2.0, MIT |
| pycryptodomex | AES-Fallback der Zugangsdaten-Verschlüsselung | BSD-2 / Public Domain |
| typing-extensions, pycparser | Hilfsbibliotheken | PSF, BSD |

Eine vollständige Liste mit den exakten Versionsnummern steht in
`THIRD_PARTY_LICENSES.md`. Sie wird mit
`python build_addon.py licenses --write` direkt aus den
`*.dist-info/METADATA`-Feldern der gebündelten Pakete erzeugt und kann
deshalb nicht veralten.

Zwei Pakete verdienen einen ausdrücklichen Hinweis:

- **`paho-mqtt` 2.1.0** ist dual lizenziert. Die Paket-Metadaten geben
  `EPL-2.0 OR BSD-3-Clause` an, die beiliegende `LICENSE.txt` nennt dieselbe
  Wahl in der Eclipse-Schreibweise: Eclipse Public License 2.0 **oder**
  Eclipse Distribution License 1.0. Die EPL-2.0 ist mit der GPL-2.0 nicht
  vereinbar; für die Verwendung in dieser Erweiterung gilt daher die
  **EDL-1.0 / BSD-3-Clause**-Option, die es ist.
- **`certifi`** steht unter der **MPL-2.0**. Das ist ein dateiweises
  Copyleft, das sich mit der GPL kombinieren lässt, solange die Datei selbst
  unverändert bleibt – sie wird hier unverändert übernommen.

---

*Smart Home Control ist eine Community-Erweiterung und steht in keiner
Verbindung zu Meross, Netatmo, VeSync/Levoit oder Atlantic/Cozytouch. Alle
genannten Marken- und Produktnamen gehören den jeweiligen Eigentümern.*
