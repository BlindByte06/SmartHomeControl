# Smart Home Control

*The manual ships with the add-on in English and German. NVDA opens it in
the reader's language from the add-on store entry; in this repository it
lives under
[doc/en/readme.html](doc/en/readme.html) and
[doc/de/readme.html](doc/de/readme.html).*

- Author: Philipp Hasel
- NVDA compatibility: NVDA 2025.1 and newer
- License: GNU General Public License, version 2
- [Website and source code](https://github.com/BlindByte06/SmartHomeControl)

This add-on controls smart home devices directly from NVDA, through a simple
menu. Devices can be switched on and off, with brightness, colour,
heating and air purifiers, and read sensor values — without the manufacturer
apps, which are often hard to use with a screen reader.

Signing in happens once, with the credentials of the respective manufacturer
account;
no additional server or background setup is required. The credentials are
stored encrypted, locally on the computer.

---

## Contents

- [Keyboard shortcuts](#keyboard-shortcuts)
- [Supported platforms and devices](#supported-platforms-and-devices)
  (Cozytouch/Atlantic is **experimental**)
- [Setup](#setup)
- [Netatmo: redirect URI and port](#netatmo-redirect-uri-and-port)
- [Usage](#usage)
- [Device history](#device-history)
- [Change announcements](#change-announcements)
- [Notes on cloud limits](#notes-on-cloud-limits)
- [Maturity and notes](#maturity-and-notes)
- [Privacy and security](#privacy-and-security)
- [Troubleshooting](#troubleshooting)
- [Reporting a problem](#reporting-a-problem)
- [Translating](#translating)
- [Building it yourself](#building-it-yourself)
- [License and bundled components](#license-and-bundled-components)

---

## Keyboard shortcuts

- **NVDA + Shift + H**: open the smart home menu (device overview)
- **NVDA + Ctrl + Shift + P**: announce the status of all devices

The settings dialog is reachable through the "Settings (Alt+E)" button in the
device menu.

Inside the open device menu these keys work as well:

- **Ctrl + H**: device history (see below)
- **Ctrl + T**: repeat the status line
- **Ctrl + F**: search for a device
- **Ctrl + B**: add the selected device or outlet to the favourites, or remove
  it
- **F1**: help for the selected device, listing what it can do
- **F5**: refresh the device list

Every command can be given a custom shortcut, and the defaults can be
changed:
**NVDA menu → Preferences → Input gestures → category "Smart Home Control"**.

The following commands deliberately have **no default gesture** and are given
one there when needed:

- **Announce energy consumption** — today's and the last 7 days' consumption
  of the metering plugs in kilowatt hours. The values preferably come straight
  from the meter inside the device, which keeps counting even while NVDA is
  not running. Only if a device does not support that query are background
  samples used instead (marked as "estimated", since they only cover the time
  NVDA was running).
- **Connection diagnostics** — announces the connection state per platform,
  plus the network state and the remaining lifetime of the Netatmo token.
- **Open the settings** — opens the Smart Home settings directly, without the
  detour through the device menu.
- **Favorites layer** — announce status and switch favourites without opening
  the menu (see below). The most rewarding one to assign for everyday use.

### Favorites layer

After the assigned shortcut, the add-on prompts with “Choose a favorite:
digit 1 to 9” and waits for a digit.

- **Digits 1 to 9** immediately announce the status of the favourite with
  that number.
- **The same digit again** toggles it. There is no time limit for this — the
  layer stays open until a decision is made, so it is possible to hear the
  whole
  status announcement first and only then switch.
- **A different digit** announces its status and becomes the remembered one,
  so "1, 2, 1" switches nothing.
- **0** reads out which digit belongs to which device.
- **Escape** leaves the layer, and so does any other key. After a longer
  pause it closes by itself.

The split is deliberate: the harmless enquiry happens at once, while the
consequential switching requires a second, deliberate press. A mistyped digit
therefore only announces something instead of switching a device.

#### What can be switched

Pressing a digit twice on a device that cannot be switched reports "cannot be
switched – adjustable in the device menu".

- **Switchable:** Meross plugs, lights and LED strips, aroma diffusers;
  Levoit air purifiers and tower fans; the Cozytouch hot water heat pump
  (hot water production on/off).
- **On multi-outlet strips** (MSS425/E/F, MSS620, MOP320) it is possible to
  make
  either **a single outlet** or **the whole device** a favourite – each
  with its own digit. The outlet switches only itself, the device
  switches all outlets together. For a single outlet, expand the strip
  in the device menu, select the outlet and press **Ctrl+B**. This works
  while the device is **offline** too – the digit stays the same once it
  is reachable again.
- **Not switchable:** Meross sensors (temperature/humidity, water leak) and
  Meross hubs; every Netatmo device. Thermostats and radiator valves can be
  adjusted but not switched on and off; the weather station and the indoor
  air monitor are display only. Their status is announced on the first press
  as usual — they are adjusted in the device menu.

#### Adding favourites and the digits

In the device menu, select the device and press **Ctrl+B** — or activate the
"Add to favorites" entry. **Ctrl+B** removes a favourite again as well.

When it is added, the device gets its fixed digit, announced right away,
for example "living room lamp: added to favorites, digit 3". In the
favourites tab the digit is shown in front of the device name. It stays with
the device permanently, even when other favourites are removed. From the
tenth favourite on, the digits are used up; those devices remain reachable
through the device menu and move up as soon as a digit becomes free.

---

## Supported platforms and devices

The add-on supports four smart home platforms. Each can be enabled
individually; only the ones actually in use are needed.
**Cozytouch/Atlantic is experimental** — only a single device model has been
tested there so far.

### Meross

Sign in with the email address and password of the Meross account. External
changes are announced too, for example switching through the Meross app, Alexa
or the button on the device.

#### Plugs and power strips

- **MSS210** — switchable plug (on/off)
- **MSS310** — plug with energy monitoring (power, voltage, current)
- **MSS315** — plug with energy monitoring (power, voltage, current)
- **MSS425**, **MSS425E**, **MSS425F** — power strips; every mains outlet can
  be switched individually. The USB ports of these strips form a single shared
  outlet and can therefore only be switched together. Outlets that have their
  own name in the Meross app appear under that name.
- **MSS620** — outdoor dual plug; both outlets switchable individually (the
  button on the device itself always switches both together)
- **MOP320** — outdoor dual plug with energy monitoring; both outlets
  switchable individually

#### Lights and LED strips

- **MSL320** (LED strip), **MSL450**, **MSL610**
- Other MSL models are detected automatically and get the same functions as
  far as the model supports them: on/off, brightness, RGB colour, colour
  temperature and white presets.

#### Aroma diffusers

- **MOD150** — spray control: off, light or strong spray (the light function
  of the device is not controlled by the add-on)

#### Hubs and sensors

The sensors connect through a Meross hub. Both hub generations (**MSH300** and
**MSH450**) are detected automatically; which hub accepts which sensor is
defined by Meross and shown in the Meross app.

Supported sensors:

- **MS100** and **MS100F** — temperature and humidity sensor
- **MS130** — temperature and humidity sensor with display
- **MS400**, **MS405** — water leak sensors

### Netatmo

Heating control and weather station display through the Netatmo account.

#### Heating

- **NATherm1** — room thermostat
- **NRV** — smart radiator valve
- **NAPlug** — relay/gateway (bridge, required for the NATherm1)

Thermostats and radiator valves are grouped by the rooms assigned in the
Netatmo app; the room name is also announced as part of the device name.

Adjustable: target temperature (manual, with a selectable duration), the
operating modes **schedule**, **away** and **frost guard**, and switching the
active heating schedule. Also displayed: measured temperature, current mode
(including "maximum" if set through the app), boiler/burner status, active
schedule zone, pre-heating (anticipation), battery level, and open window
detection (only the NRV radiator valve has this feature, not the room
thermostat).

#### Weather and indoor air (display only)

- **NAMain weather station** with outdoor, wind, rain and additional indoor
  modules
- **NHC indoor air quality monitor**

Displayed: temperature, humidity, CO₂, noise, air pressure, rain and wind —
display only, no control.

### VeSync / Levoit

Sign in with the email address, password and country code of the VeSync
account.

#### Air purifiers (Levoit Core)

- **Core200S**, **Core300S**, **Core400S**, **Core500S**, **Core600S**
- and the regional variants of these series, which VeSync reports as
  **LAP-C201S**, **LAP-C202S**, **LAP-C301S**, **LAP-C302S**, **LAP-C401S**,
  **LAP-C501S** and **LAP-C601S** — regardless of the country suffix at the
  end of the model name (e.g. `-WEU`, `-WUSR`, `-WJP`).

Available functions: on/off, mode (manual and sleep; auto on all models except
the Core200S), fan level, display, child lock, auto profile
(default/efficient/quiet), air quality, and remaining filter life with a
warning at a low value. The **Core200S** (and its LAP-C201S/C202S variants)
additionally has a controllable **night light** (on/off/dimmed).

#### Tower fans (Levoit)

- **LTF-F422S** series, likewise regardless of the country suffix (tested:
  KEU, WUSR, WJP, WUS)

Available functions: on/off, mode (normal, auto, turbo, sleep), fan level,
oscillation, mute and display.

Other device types in the VeSync account (plugs, lights or humidifiers, for
example) are currently not shown.

### Cozytouch / Atlantic (experimental)

> **Experimental.** Of the Cozytouch devices, only one hot water heat pump has
> been tested so far. Other device types may be detected or presented
> incorrectly, and individual functions can stop working if Atlantic changes
> its cloud interface. Details under [Maturity and
> notes](#maturity-and-notes).

Sign in with the email address and password of the Cozytouch/Atlantic
account.

- **Hot water heat pump** (tested: Austria Email Revolution Evo 3; the exact
  model is shown in the device menu)

Available functions: hot water production on/off, target temperature
(including the actual heating target under Eco/boost; the cloud does not
report a measured water temperature for this model), heating mode, boost
(including an experimentally adjustable boost duration), away mode with a
schedulable period, available hot water in percent, and a display of today's
programmed heating times and the status of the electric heating element and
the off-peak tariff. The rated capacity (in litres) can be entered in the
settings.

The three heating modes:

- **Manual** — heats continuously to the configured target temperature.
- **Eco+** — heats economically with a lowered heating target; the actual
  target is shown in the device entry.
- **Schedule** — heats only within the time windows configured in the
  Cozytouch app (up to three per day, e.g. for off-peak hours). The time
  windows themselves can only be edited in the Cozytouch app; the add-on shows
  today's programmed heating times in the device entry.

---

## Setup

Prerequisite: the devices must have been set up once with the respective
manufacturer app (Meross, Netatmo, VeSync, Cozytouch) — the add-on then picks
them up from the account.

Open the menu with **NVDA + Shift + H**. Without a sign-in yet, the settings
dialog opens automatically. Enable the platforms in use and enter
the credentials — details per platform in the following sections. Finally:
optionally enable "Automatic login" (the connection is then established at
every NVDA start) and save. The devices are loaded and immediately available
in the menu; no NVDA restart is required.

### Set up Meross

Enter the email address and password of the Meross account — done.

### Set up Netatmo

Netatmo uses a browser sign-in (OAuth2) instead of an email address and
password inside the add-on. Required once:

1. Create a (free) app on [dev.netatmo.com](https://dev.netatmo.com); it
   provides a **client ID** and a **client secret**.
2. Enter both in the Netatmo tab of the add-on.
3. Register the **redirect URI** shown in the tab with the Netatmo app —
   details, and what the port is about, are explained in the section
   [Netatmo: redirect URI and port](#netatmo-redirect-uri-and-port).
4. Choose "Connect with Netatmo (OAuth2)", sign in in the browser and confirm.

### Set up VeSync / Levoit

Enter the email address, password and country code of the VeSync account.

### Set up Cozytouch / Atlantic (experimental)

Enter the email address and password of the Cozytouch account (the same as in
the Cozytouch app). Optional: the rated capacity of the hot water tank in
litres, so the available hot water is additionally estimated in litres. The
tab is labelled "experimental" in the settings dialog — see [Maturity and
notes](#maturity-and-notes).

---

## Netatmo: redirect URI and port

### What is the redirect URI?

During the browser sign-in, Netatmo sends the authorisation back to a
previously registered address — the redirect URI. The add-on shows it in the
Netatmo tab. By default it is **exactly**:

```
http://localhost:8474/callback
```

Exactly this address has to be entered in the "redirect URI" field of the
Netatmo app on dev.netatmo.com — best copied unchanged from the add-on.

### Why does the address have to match exactly?

Netatmo compares the registered redirect URI character by character with the
one actually used. Even a difference in scheme (`http`), host (`localhost` is
not the same as `127.0.0.1`), port or path leads to the error
`redirect_uri mismatch`.

### What is the port for, and why 8474?

During sign-in the add-on briefly starts a small local web server that
receives the authorisation from Netatmo. The port (default **8474**)
determines the "channel" this server listens on. It is only local and only
active for the moment of signing in; nothing is opened to the outside. 8474
was chosen as an inconspicuous, rarely used port.

If the port is already taken (the sign-in fails with a port message), simply
change the **redirect port** in the Netatmo tab to a free value — and register
the newly shown redirect URI with dev.netatmo.com again, so both sides match.

---

## Usage

In the device menu the devices are grouped by platform and type in a tree
view. Navigate with the arrow keys; switch or change a value with Enter or
Space. Frequently used devices can be marked as favourites (the "Add to
favorites" entry on a device); they then also appear in the favourites tab. In
the settings it can be chosen which tab — all devices or favourites — is
shown
first when the menu opens.

Every action gives immediate speech and tone feedback. While the menu is open
the devices are refreshed more frequently, so the values stay up to date.

---

## Device history

**Ctrl + H** in the device menu opens the history. It answers two different
questions, and the "View" combo box switches between them.

**Events** — one row per action, newest first and grouped by day ("Today",
"Yesterday", then weekday and date). The columns are time, device, platform,
event and source. The source column names where the action came from: "you"
for an action through this add-on, "external" for the manufacturer app, a
voice assistant or the button on the device, and "automatic" for entries the
add-on records by itself, such as a water alarm.

**Measurements** — one row per device and quantity instead of thousands of
single values: device, quantity, latest value and time of the latest reading.
Enter on a row opens the details — lowest and highest value, the time-weighted
average, the period covered, the number of stored readings and the most recent
changes. The detail window is a read-only text field, so it can be read line by
line and copied with Ctrl+C.

The filters above the list narrow it down by device, platform and period (all
time, last hour, last 24 hours, last 7 days, last 30 days); the "Filter" button
applies them.

### What is recorded

Readings are kept as change points, not as a complete series: a value is stored
when it differs from the last stored one by more than a threshold — 0.3 K for
temperature, 2 % for humidity, 50 ppm for CO2, 1 mbar for air pressure,
3 µg/m³ for particulates and 5 dB for noise. Regardless of that, one value per
hour is kept, so an unchanged reading does not look like a gap and the averages
stay correct. Recorded are temperature, humidity, CO2, air pressure, PM2.5 and
PM10 of the Levoit air purifiers, the noise level of the Netatmo indoor module
and the temperature measured by the Levoit tower fans.

Events are kept for one year or 2000 entries, readings for 90 days or 20000
change points — whichever limit is reached first. Both files live in the NVDA
configuration folder next to the add-on, as `SmartHomeControl_history.json` and
`SmartHomeControl_measurements.json`. "Delete history" empties both after a
confirmation prompt.

### CSV export

"Export as CSV" writes what the current view shows, with the active filters.
The columns follow the content: a file of events carries no measurement
columns, and a file of readings only the quantities that actually occur.

Timestamps are written in ISO 8601 with the "T" (`2026-08-14T22:20:49`). That
is not cosmetic: with a space instead, a spreadsheet recognises a date,
reformats the cell and then shows nothing but "##########" because the column
is too narrow — which is exactly what a screen reader reads out. List and
decimal separators follow the regional settings, so numbers arrive as numbers.
Without that, a German Excel reads the temperature "28.5" as the date "28 May".

---

## Change announcements

External changes are announced as well — for example when a device is switched
through the manufacturer app, Alexa or the button on the device. In the
"Notifications" tab, what is announced can be set per platform and event
type
announced (switching, mode, fan level, air quality, filter, thermostat
setpoint, boiler status, and so on). Own actions in the dialog are not
reported twice.

The water sensors are a special case: an MS400 or MS405 reports a state, not a
measured value. A change is announced with an error tone and recorded in the
history as an event ("water alarm" or "no water detected any more"). Since a
leak sensor that stays silent is pointless, this is on by default; it can be
switched off under Notifications → Meross → "Announce water alarm of the water
sensors".

---

## Notes on cloud limits

Meross limits its cloud to **200 messages per hour and device** — according to
Meross support, a safety measure against server flooding. If the limit is
exceeded persistently, Meross first sends a warning (a "cloud termination
notice"). If the same device still sends too many messages three days after
that warning, **that device** is blocked for 24 hours. Other devices and the
account itself are not affected.

The add-on automatically stays below the limit: the periodic polling is
deliberately conservative, on/off changes arrive in real time via push anyway,
and the add-on additionally caps the requests per device itself. Should a
single device reach the ceiling nonetheless, it is temporarily polled less
often, and a message names the affected device. All other devices continue
normally.

---

## Maturity and notes

Only Netatmo offers an official, documented API. Meross, VeSync and Cozytouch
are reverse-engineered cloud integrations without an official interface — for
Meross, the manufacturer's support explicitly confirmed on request that no
official API exists. They work reliably with the tested devices but can fail
temporarily when the manufacturers change their servers.

The platforms differ in how well they are field-tested:

- **Meross** and **Netatmo** are considered stable.
- **VeSync/Levoit:** only Levoit Core series air purifiers and Levoit tower
  fans are supported. Other VeSync devices (plugs, lights, humidifiers,
  kitchen appliances) are not shown.
- **Cozytouch/Atlantic (experimental):** only the Austria Email Revolution
  Evo 3 hot water heat pump has been tested so far. Other Cozytouch devices
  (radiators or air conditioners, for example) are currently shown incorrectly
  as hot water heat pumps as well and are not usable.

---

## Privacy and security

- The credentials are stored exclusively **locally on the computer** and
  **encrypted**. Passwords are never stored in plain text in the
  configuration.
- Communication goes directly to the respective manufacturer clouds — no data
  is sent to third parties.

---

## Troubleshooting

- **No devices visible:** check the credentials and the enabled platform in
  the settings dialog, then save (the sign-in runs in the background).
- **Device offline:** check whether the device is reachable in the
  manufacturer app.
- **A platform reports "unreachable":** usually a temporary cloud or network
  issue — the add-on reconnects automatically and announces when the platform
  is connected again.

---

## Reporting a problem

Problem reports are welcome — please open an
[issue](https://github.com/BlindByte06/SmartHomeControl/issues).

What helps most is the NVDA log. To produce a useful one:

1. **NVDA menu → Preferences → Settings → General → Logging level: "Debug"**, then
   restart NVDA. Without this level the add-on's own entries are missing.
2. Reproduce the problem.
3. **NVDA menu → Tools → View log**, then save the text (Ctrl+S) or copy the
   part around the problem.

Please include in the report:

- what was done, what was expected and what happened instead
- the platform and, if known, the device model (e.g. Meross MSS425F)
- the add-on version (NVDA menu → Tools → Add-on store → installed add-ons)
  and the NVDA version

**Before sending, please check the log for personal data.** It contains device
names and home names as given in the manufacturer app. The add-on
itself logs neither passwords nor tokens nor the email address — but error
messages passed through from the manufacturer libraries are beyond its
control, so a quick look is worth it.

---

## Translating

The interface language of the source is **English**, so an interface without a
matching translation stays English. Translations live under `locale/<lang>/`:

- `locale/SmartHomeControl.pot` is the template — start from a copy of it as
  `locale/<lang>/LC_MESSAGES/nvda.po`.
- The compiled `nvda.mo` next to it is what NVDA loads at runtime, so it has to
  be regenerated after every change (`msgfmt nvda.po -o nvda.mo`, or
  `polib`). `python build_addon.py i18n` checks that `.po`, `.mo` and the code
  agree and names what is missing.
- `locale/<lang>/manifest.ini` translates the summary and description shown in
  the add-on store (see `locale/de/manifest.ini` for the format).

German (`locale/de/`) is complete and can serve as an example.

---

## Building it yourself

This add-on does **not** use the SCons template (`buildVars.py`/`sconstruct`)
of the official NVDA add-on template, but a build script of its own — which is
necessary because of the bundled binary packages for two Python architectures
(`lib/_arch/cp311-win32` and `cp313-amd64`). That is acceptable for the NVDA
add-on store; the store reviews the finished `.nvda-addon` package, not the
build system.

```bash
python build_addon.py pack
```

produces `dist/SmartHomeControl-<version>.nvda-addon`. It checks the
translations along the way (`.po` and `.mo` in agreement) and the package
integrity (no `__pycache__`, manifest present, `.mo` included), and
synchronises the version number into the documentation titles.
`python build_addon.py libs` rebuilds `lib/` reproducibly from
`requirements-bundle.txt`. The GitHub Actions pipeline
(`.github/workflows/build.yml`) builds on every push to `main` and, for tags
(`v*`), attaches the package to a GitHub release together with the changelog
section of that version.

## License and bundled components

Smart Home Control is licensed under the **GNU General Public License,
version 2** (see the `LICENSE` file).

The add-on ships the Python libraries it needs, so no extra installation is
required. All packages are taken unmodified from PyPI; their full license
texts are included in the add-on package under `lib/`.

| Component | Purpose | License |
|---|---|---|
| meross-iot | Meross cloud and MQTT | MIT |
| paho-mqtt | MQTT protocol | EPL-2.0 / **EDL-1.0** |
| requests, urllib3, idna, certifi, charset-normalizer | HTTPS requests | Apache-2.0, MIT, MPL-2.0 |
| aiohttp, yarl, multidict, frozenlist, propcache, aiosignal, aiohappyeyeballs, attrs | asynchronous HTTP requests | Apache-2.0, MIT |
| pycryptodomex | AES fallback for credential encryption | BSD-2 / Public Domain |
| typing-extensions, pycparser | helper libraries | PSF, BSD |

A complete list with the exact version numbers is in
`THIRD_PARTY_LICENSES.md`. It is generated by
`python build_addon.py licenses --write` directly from the
`*.dist-info/METADATA` fields of the bundled packages, so it cannot go stale.

Two packages deserve an explicit note:

- **`paho-mqtt` 2.1.0** is dual-licensed. The package metadata states
  `EPL-2.0 OR BSD-3-Clause`; the bundled `LICENSE.txt` names the same choice in
  Eclipse wording: Eclipse Public License 2.0 **or** Eclipse Distribution
  License 1.0. The EPL-2.0 is not compatible with the GPL-2.0, so for use in
  this add-on the **EDL-1.0 / BSD-3-Clause** option applies, which is.
- **`certifi`** is under the **MPL-2.0**. That is a file-level copyleft which
  can be combined with the GPL as long as the file itself stays unmodified —
  and it is taken over unmodified here.

---

*Smart Home Control is a community add-on and is not affiliated with Meross,
Netatmo, VeSync/Levoit or Atlantic/Cozytouch. All brand and product names
belong to their respective owners.*
