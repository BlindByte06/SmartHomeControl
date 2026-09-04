# Smart Home Control

An NVDA add-on for controlling smart home devices — plugs, lights, heating,
air purifiers and sensors — directly from a menu inside the screen reader,
without the manufacturer apps.

- Author: Philipp Hasel
- License: GNU General Public License, version 2
- NVDA 2025.1 and newer

## What it does

Four platforms, each usable on its own: **Meross** (plugs, power strips,
lights, LED strips, aroma diffusers, hubs with temperature and water
sensors), **Netatmo** (thermostats and valves, weather station and indoor air
as a display), **VeSync / Levoit** (air purifiers and tower fans, Cosori air
fryers with start and stop for cooking programmes) and
**Cozytouch / Atlantic** (hot water heat pump, **experimental** — only one
device model tested so far).

Everything is reached from one tree view: navigate with the arrow keys,
switch with Enter, with speech and tone feedback for every action. Frequently
used devices — down to a single outlet of a power strip — get a digit in the
favorites layer and are switched with two keystrokes, without opening the
menu. Changes made elsewhere (manufacturer app, voice assistant, the button
on the device) are announced as well. A history records switching actions and
sensor readings and exports them as CSV.

Signing in happens once, with the credentials of the manufacturer account. No
additional server and no background setup are required; the credentials are
stored encrypted, locally on the computer.

## Installation

Install the `.nvda-addon` file from the
[releases](https://github.com/BlindByte06/SmartHomeControl/releases) and
restart NVDA. **NVDA + Shift + H** opens the menu; without a sign-in yet, the
settings dialog opens by itself.

## Documentation

The full manual — setup per platform, all keyboard commands, the favorites
layer, the history, cloud limits, privacy — ships with the add-on and NVDA
opens it in the reader's language. In this repository it lives under
[doc/en/readme.html](doc/en/readme.html) and
[doc/de/readme.html](doc/de/readme.html).

[CHANGELOG.md](CHANGELOG.md) lists what changed per version.

## Translating

The source language is English; an interface without a matching translation
stays English rather than turning German. A new language needs one file:

1. Take the template
   [locale/SmartHomeControl.pot](locale/SmartHomeControl.pot). Every one of
   its texts carries a `# Translators:` comment saying what it is and where
   it appears - the short line beginnings matter, because the F1 help finds
   its text by the start of the tree line and the two have to match.
2. Translate it with Poedit or any gettext tool into
   `locale/<language>/LC_MESSAGES/nvda.po`.
3. Send it as a pull request or attach it to an issue.

Three of the texts are the name, the description and the "What's new" of the
add-on store; translating them is enough to make the store show the add-on in
that language. The manual (`doc/<language>/readme.html`) is optional - NVDA
falls back to the English one.

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

**Before sending, please check the log for personal data.** It contains
device and home names as given in the manufacturer app. NVDA also writes the
complete configuration into the log at startup, which includes the email
address in plain text and the stored passwords and tokens — those in
encrypted form, readable only on the machine and user account they were
saved on. The add-on itself logs none of that, but error messages passed
through from the manufacturer libraries are beyond its control, so a quick
look is worth it.

---

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

---

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
