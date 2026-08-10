# Smart Home Control

*English · [Deutsch](README.de.md)*

- Author: Philipp Hasel
- NVDA compatibility: NVDA 2025.1 and newer
- License: GNU General Public License, version 2
- [Website and source code](https://github.com/BlindByte06/SmartHomeControl)

This add-on lets you control smart home devices directly from NVDA, through a
simple menu. You can switch devices on and off, control brightness, colour,
heating and air purifiers, and read sensor values — without the manufacturer
apps, which are often hard to use with a screen reader.

You sign in once with the credentials of the respective manufacturer account;
no additional server or background setup is required. The credentials are
stored encrypted, locally on your computer.

---

## Contents

- [Keyboard shortcuts](#keyboard-shortcuts)
- [Supported platforms and devices](#supported-platforms-and-devices)
  (Cozytouch/Atlantic is **experimental**)
- [Setup](#setup)
- [Netatmo: redirect URI and port](#netatmo-redirect-uri-and-port)
- [Usage](#usage)
- [Change announcements](#change-announcements)
- [Notes on cloud limits](#notes-on-cloud-limits)
- [Maturity and notes](#maturity-and-notes)
- [Privacy and security](#privacy-and-security)
- [Troubleshooting](#troubleshooting)
- [Building it yourself](#building-it-yourself)
- [License and bundled components](#license-and-bundled-components)

---

## Keyboard shortcuts

- **NVDA + Shift + H**: open the smart home menu (device overview)
- **NVDA + Ctrl + Shift + P**: announce the status of all devices

The settings dialog is reachable through the "Settings (Alt+E)" button in the
device menu.

You can assign your own shortcuts to every command, and change the defaults:
**NVDA menu → Options → Input gestures → category "Smart Home Control"**.

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
- **Toggle favourite 1–9** and **announce status of favourite 1–9** — switches
  the respective favourite device, or announces its status, without opening
  the menu. The number matches the order in the favourites tab of the device
  menu (number 1 is the top entry). You add favourites in the device menu:
  select a device and activate the "Add to favorites" entry.

---

## Supported platforms and devices

The add-on supports four smart home platforms. Each can be enabled
individually; you only need the ones you actually use.
**Cozytouch/Atlantic is experimental** — only a single device model has been
tested there so far.

### Meross

Sign in with the email address and password of your Meross account. External
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

Heating control and weather station display through your Netatmo account.

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

Sign in with the email address, password and country code of your VeSync
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

Other device types in your VeSync account (plugs, lights or humidifiers, for
example) are currently not shown.

### Cozytouch / Atlantic (experimental)

> **Experimental.** Of the Cozytouch devices, only one hot water heat pump has
> been tested so far. Other device types may be detected or presented
> incorrectly, and individual functions can stop working if Atlantic changes
> its cloud interface. Details under [Maturity and
> notes](#maturity-and-notes).

Sign in with the email address and password of your Cozytouch/Atlantic
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
them up from your account.

Open the menu with **NVDA + Shift + H**. If you are not signed in yet, the
settings dialog opens automatically. Enable the platforms you use and enter
the credentials — details per platform in the following sections. Finally:
optionally enable "Automatic login" (the connection is then established at
every NVDA start) and save. The devices are loaded and immediately available
in the menu; no NVDA restart is required.

### Set up Meross

Enter the email address and password of your Meross account — done.

### Set up Netatmo

Netatmo uses a browser sign-in (OAuth2) instead of an email address and
password inside the add-on. Required once:

1. Create your own (free) app on [dev.netatmo.com](https://dev.netatmo.com);
   you receive a **client ID** and a **client secret**.
2. Enter both in the Netatmo tab of the add-on.
3. Register the **redirect URI** shown in the tab with your Netatmo app —
   details, and what the port is about, are explained in the section
   [Netatmo: redirect URI and port](#netatmo-redirect-uri-and-port).
4. Choose "Connect with Netatmo (OAuth2)", sign in in the browser and confirm.

### Set up VeSync / Levoit

Enter the email address, password and country code of your VeSync account.

### Set up Cozytouch / Atlantic (experimental)

Enter the email address and password of your Cozytouch account (the same as in
the Cozytouch app). Optional: the rated capacity of your hot water tank in
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

You must enter exactly this address in the "redirect URI" field of your
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
the settings you can choose which tab — all devices or favourites — is shown
first when the menu opens.

Every action gives immediate speech and tone feedback. While the menu is open
the devices are refreshed more frequently, so the values stay up to date.

---

## Change announcements

External changes are announced as well — for example when a device is switched
through the manufacturer app, Alexa or the button on the device. In the
"Notifications" tab you configure per platform and event type what is
announced (switching, mode, fan level, air quality, filter, thermostat
setpoint, boiler status, and so on). Your own actions in the dialog are not
reported twice.

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

- Your credentials are stored exclusively **locally on your computer** and
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
