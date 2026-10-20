# Changelog – Smart Home Control

The GitHub release notes are built from the section of the released version.

## 26.10.2 (October 2026)

### Important notes

- With "Save configuration when exiting NVDA" switched off, the settings of the add-on are saved with NVDA+Ctrl+C like all other settings.

### Security Fixes

- Credentials are no longer sent along when a cloud redirects to another host.
- A call of the local Netatmo sign-in address with a wrong state is turned away and no longer ends the sign-in.
- The VeSync account ID no longer appears in the NVDA log.
- The rights of the credential file are set with icacls from the Windows system folder, for the account NVDA runs as.

### Changes

- The settings of the add-on are stored in the base configuration, not in the active profile.
- Renewed sign-in tokens no longer write NVDA's whole configuration to disk.
- A sign-in that keeps failing for a reason other than the network is no longer retried for good.
- The settings open only once at a time.
- Meross garage door openers and roller shutters are shown but not switched.
- Shortcuts with Alt, Control or Windows end the favourites layer and work as usual.
- The help of the favourites tab (F1) opens as a list.
- A Cozytouch change the cloud accepted but the appliance has not shown yet is announced as sent and confirmed once it arrives, or reported after ten minutes if it never does.
- Dates of the Cozytouch away mode follow the notation of the region, also in the history.

### Bug Fixes

- NVDA no longer crashes when the device menu is closed after a device was switched from the favourites tab.
- Enter or Space on a VeSync or Cozytouch device no longer freezes NVDA.
- A leak sensor that already detects water when NVDA starts is now announced.
- The Netatmo device menu offers "Add to favorites" again.
- A setting changed in the VeSync or Cozytouch device menu is no longer recorded a second time as changed elsewhere.
- A Meross device switched here is no longer recorded as switched elsewhere.
- Cozytouch hot water switched from the favourites layer is no longer recorded as switched elsewhere.
- A Cozytouch command that the appliance takes over late is no longer reported as failed.
- A network drop while a Cozytouch command is followed no longer reports an error for a command that went through.
- A Cozytouch command whose answer comes too late is not sent a second time.
- A Cozytouch boost that runs out after an NVDA restart is recorded as "Boost ended", automatic.
- A Cozytouch boost duration that was just set is no longer overwritten by the next update.
- Cozytouch and Petlibro changes, the start of a boost included, reach the history even when the device menu is closed first.
- A Meross account with two-factor authentication is named as the reason the sign-in fails.
- A wrong Meross password no longer plays NVDA's error sound.
- Testing the Netatmo connection no longer invalidates the running sign-in.
- Netatmo tokens are renewed only when Netatmo rejects the token itself.
- A Cozytouch server fault at sign-in is retried.
- Switching the Cozytouch away mode no longer signs in to Cozytouch again.
- NVDA exits faster on a slow network.
- Damaged data files are kept as "unreadable" instead of being overwritten.
- The Meross connection test always closes its session.
- A failed CSV export no longer plays NVDA's error sound twice.
- Names that Netatmo does not report are no longer shown in German in other languages.

### Changes for Developers

- The build workflow runs with read access; only the release job may write.
- Actions are pinned to commits, the build tools to hashes (`.github/requirements-ci.txt`).
- `python build_addon.py lock` records the SHA-256 of every bundled wheel. `libs` refuses any other file, and `pack` fails when the lock is out of date.
- Each release carries the SHA-256 checksum of the package.

## 26.10.1 (October 2026)

### Petlibro (new, experimental)

- Feeders and water fountains from Petlibro, tested with the Air Smart Feeder (PLAF108) and the Dockstream 2 Cordless (PLWF116).
- Feed now. The command is sent only once, also after a network error.
- Feeding plan and meals: skip for today, switch off, change, add and delete.
- Reminders for desiccant, filter and cleaning.
- Fountain: water level, today's and yesterday's intake, single drinks, water mode, low water warning and light.
- Fountain: flow duration of the sensor modes, 45, 60, 75 or 90 seconds (firmware 0.3.25 or later).
- Announcements for failed feedings, feedings from elsewhere, low food or water, maintenance, low battery and settings changed elsewhere.
- A change the fountain confirms later, on its battery, is announced as on its way and confirmed once it arrives.
- A favourites digit announces a Petlibro device but never switches it.
- Petlibro allows one sign-in per account at a time. A second Petlibro account with the devices shared to it keeps the Petlibro app signed in.

### New Features

- Platform status (Ctrl+P in the device menu): faults since signing in and devices that did not answer.
- The heating times of the Cozytouch heat pump can be changed, for several weekdays at once, and are read back from the appliance.
- NVDA+Ctrl+Shift+P announces a summary of all devices. Pressed twice, it shows every device in a reading window.
- Which platform faults are announced can be set in the notifications tab.

### Important notes

- An early version stored the Netatmo password in plain text in nvda.ini, and NVDA writes nvda.ini into its log. Anyone who has passed on such a log should change the Netatmo password.

### Security Fixes

- Settings of an early version, including the Netatmo password, are removed from nvda.ini.
- Email addresses are now stored encrypted, like passwords and tokens.
- Removing the add-on deletes its stored credentials. Updates keep them.
- The bundled multidict is updated to 6.9.1 (GHSA-54p9-h82j-f925).

### Changes

- The platforms sign in at the same time.
- A sign-in that failed because of the network is retried automatically.
- A platform fault is announced only once it has lasted two minutes.
- An unreachable platform is shown in the device tree, the status line and the connection diagnostics.
- A platform that answers only in part, or not at all, counts as a fault.
- A single device that misses a poll is no longer announced; the platform status lists it.
- Changes made outside the add-on are recorded in the history for all platforms, also with their announcement switched off.
- What a device does on its own, such as a heating schedule or a boost running out, is recorded as "automatic".
- Decimal numbers use the separator of the interface language.
- New credentials switch their platform on when the settings are saved.
- Saving the settings signs in only the platforms that changed.
- The Cozytouch line "Electric heating element active" is now "Currently heating".
- A new Petlibro meal starts with no weekday ticked.
- NVDA starts faster: the Meross libraries load at the Meross sign-in.
- Netatmo is polled with far fewer requests.
- Network and server faults are logged as warnings, without NVDA's error sound.

### Bug Fixes

- Ctrl+F in the device menu now finds devices, not only platforms and categories.
- NVDA no longer freezes while a VeSync, Netatmo or Meross lamp command is sent.
- An own action is no longer recorded a second time as an external change.
- A VeSync cooking programme is never started twice after a network error.
- The VeSync fan level in automatic mode is no longer recorded as an external change.
- Sensors of a Meross hub no longer disappear when the hub does not answer.
- F5 updates the Netatmo values in the device tree again.
- A Netatmo HTTP 429 or 408 no longer discards the sign-in tokens.
- A Petlibro setting confirmed by the device no longer flips back while the cloud lags behind.
- Petlibro "Feed now" is confirmed only by a manual feeding, not by a planned meal.
- The history shows the boost duration and a changed time program as readable values.
- Netatmo setpoints are no longer stored in the history with German text.
- History entries are saved within 30 seconds, even when no other entry follows.
- The view selector of the history keeps the focus.
- Validation messages in the settings are no longer cut off.
- The connection diagnostics say "Last refresh" instead of "Last Meross refresh".

### Changes for Developers

- NVDA's own requests and urllib3 take precedence over the bundled copies. The log names the copy in use after each sign-in.
- Every build checks the bundled libraries against the OSV vulnerability database (`python build_addon.py audit`).
- A duplicated version heading in the changelog stops the build.

## 26.9.2 (September 2026)

### Fixed

- **Every leak sensor on a hub is told apart from the others.** Two or more
  of them on one Meross hub - MS400, MS405, in any combination - shared a
  single alarm state, so an alarm repeated on every poll pass while another
  sensor was announced as dry in the same breath, both of them landing in
  the history each time. A single sensor was never affected. The same
  identity now also keeps a sensor from dropping out of a refresh that runs
  while another one is still going, which covers the MS100 and MS130 as
  well.
- **Credentials survive two things writing at once.** Saving also runs on
  background threads, and two of them writing together lost the newer
  value - a token the cloud had just renewed is exactly what went missing.
  Writing is serialised now, a failed write says so instead of passing in
  silence, and no stray file with an email address in it is left behind.
- **A credential that cannot be decrypted is no longer overwritten.**
  Passwords and tokens can be read back only on the machine and user
  account they were saved on, and the file travels beside the add-on - a
  portable NVDA on a stick reaches a second machine that way. Such a value
  is now left as it stands instead of being replaced with nothing; the
  Netatmo client ID and secret are typed in by hand and no fresh sign-in
  can fetch them again.

### Security

- **The bundled libraries carry three fixed CVEs.** urllib3 goes from 2.7.0
  to 2.8.0, which closes two high-severity issues and one medium one: the
  TLS configuration of an HTTPS proxy could be ignored or overridden, a
  chunk-size line could be buffered without a limit, and chunked deflate
  streaming could enter an infinite loop. The last two are denial of
  service from a server that answers badly - and a denial of service in a
  screen reader takes away the screen. aiohttp goes from 3.14.1 to 3.14.3,
  which stops sensitive headers being carried into a cross-origin redirect
  when more than one of them is present, and picks up a newer HTTP parser.
  certifi is current again, which drops a withdrawn root certificate
  (Atos TrustedRoot 2011) and adds four; idna 3.20 rejects A-labels that
  are not the canonical encoding of their name. multidict, yarl, propcache
  and charset-normalizer follow the same stack.
- **A reading can no longer smuggle a formula into the export.** Free-text
  columns were already defused, the numeric ones relied on a check in a
  different module to keep anything but a number out. The guard now sits
  where the cell is written, so a stored value of `=cmd|...` reaches the
  spreadsheet as text - which matters because an export is made to be
  passed on to someone else.
- **The local sign-in callback answers only its own address.** The small
  web server that receives the Netatmo authorization runs on this machine
  for the two minutes the sign-in takes, and every page open in the browser
  can reach it in that time. It now replies to the registered callback path
  alone, so a request to any other address is a 404 and leaves the sign-in
  alone, and the comparison of the one-time value is a constant-time one.
- **The credentials file is taken off the inherited access rights** when it
  is written, the way the encryption key file already was. Its passwords
  and tokens are encrypted, but the email addresses are not - and they were
  the reason the file exists.

### Changed

- **The manual says which credentials are encrypted and which are not.**
  Passwords and tokens are, and only on the machine they were saved on; the
  email addresses are not, because the sign-in is addressed to them. The
  section used to call all of it encrypted, which promised more than the
  add-on delivers - and matters to anyone carrying the folder to a second
  computer.
- **Twelve checks instead of ten**, and the existing ones cover the faults
  above. A new one verifies the bundled libraries: every file against the
  checksum from its wheel, the bundled versions against the pinned ones,
  and both architectures for completeness. `historytest.py` also runs on a
  console that is not UTF-8 now, instead of dying halfway through on a unit
  symbol and looking like a failed check.

## 26.9.1 (September 2026)

### Fixed

- **The device menu no longer opens twice.** The menu is modal, but a global
  NVDA gesture reaches the add-on anyway, so pressing the shortcut again built
  a second dialog on top of the first. Two of them are not only confusing to
  listen to: the second one took over the reference the live update hangs on
  and cleared it when it closed, so the dialog left standing silently stopped
  being updated. The shortcut now brings the open menu to the front.

### Changed

- **Credentials are no longer kept in NVDA's configuration.** NVDA writes the
  complete configuration into the log when it starts, so everything stored
  there travels with every log a user sends in - the email address in plain
  text. Email addresses, passwords and tokens now live in a file of their own
  beside the add-on, which the log never sees. What is stored has not changed:
  passwords and tokens stay encrypted and readable only on the machine and
  user account they were saved on. Existing credentials move by themselves on
  the first start; if that file cannot be written, they stay where they were
  rather than being lost.
- **The checks are part of the repository now** and run in the build workflow.
  Ten scripts that assert what has already broken in this add-on once - a
  translation without context, a lamp that remembered its colour mode, a
  sensor dropout, a tree that lost its selection. They used to live on one
  disk and only ran when someone remembered them.
- **The readme says how to translate the add-on**: where the template is,
  what a `.po` file needs, and that the manual is optional.

## 26.8.2 (August 2026)

### Cosori air fryers

The CAF-P583S series (Dual Blaze) is supported, in any country variant.

- **What it shows:** the cooking state, the loaded programme, the remaining
  time and both temperatures - the measured one and the one the programme was
  set to, in the unit the appliance reports. The measured value is damped, so
  the line is not re-read every fifteen seconds without saying anything new,
  and it is left out in standby where the appliance stops measuring.
- **Start a programme** with the appliance's own temperature and time or with
  values chosen here, **change the cooking time** while it runs, and **stop
  it** - each after a prompt. The temperature cannot be changed during a cook;
  the appliance does not accept one then, and a line in the tree says where it
  can be set instead.
- **The programme list is learned, not written down.** Every programme loaded
  on the appliance is noted and can be started by name from then on. Merely
  selecting one there is enough.
- **The end of a programme is announced**, as are one starting to run and one
  being paused - with the device menu open or closed. One switch under
  Notifications turns all three off.
- Starting may not mean heating: Cosori state that their air fryers cannot be
  switched on remotely. The confirmation therefore says "set", and the
  programme state says within one poll whether the appliance began by itself
  or is waiting for its own start button.

### Fixed

- **A lamp remembered its colour mode for good.** A mode set from the add-on
  was cached and outranked the lamp itself indefinitely, so an MSL450 put into
  white mode at the lamp or in the Meross app went on being shown in colour
  mode. The cache now expires after a minute.
- **A sensor dropout is no longer announced or recorded.** A Levoit purifier
  reports -1 for its particulate sensor when it has no reading. That reached
  the interface as a measurement and the history as a data point; now the last
  good reading stays on the line and nothing is recorded.
- **Ctrl+B in the favorites tab** removed the whole power strip when it was
  standing on a single outlet.
- **The favorites layer** now requires the switching press to follow the
  announcement within a few seconds, adjustable in the settings. A digit
  pressed by accident much later announces the status again instead of
  switching.
- **The selected line no longer moves by itself** when the tree is refreshed
  mid-programme, and a line is no longer announced twice.
- **The connection test** announced its result twice, once as the status line
  and once as a message with the same content.
- **The selection survives a refresh.** Every rebuild of the device tree put
  it back on the first entry: whoever stood at the fifth device started over
  at the top. The entry is found again by its identity - not by its label,
  which carries the values - and a line that has disappeared in the meantime
  falls back to its device.
- **A refresh is announced once, not two or three times.** It rebuilt the
  whole tree even when only values had changed, and deleting and re-adding
  the entries reported a selection each time, with the focus that followed
  reporting it once more. The structure is now compared first; an unchanged
  list is only written into.
- **A platform switched off in the settings leaves the tree.** Its polling
  stopped at once, but its devices stayed - offered for switching, answering
  nothing - until NVDA was restarted.
- **A VeSync account holding only unsupported devices** reported "no devices",
  which reads exactly like a refused sign-in. The types are now named in the
  log and the account is reported as connected.

### Changed

- **Credentials are proven at the platform before they are saved**, and they
  reach a session that is already running - a wrong password used to be
  accepted silently and surfaced only at the next NVDA start. A refused
  sign-in offers to enter them again, at the tab concerned. Two parallel
  sign-ins of the same platform can no longer happen.
- **The add-on writes its version into the log**, so a test report can be
  tied to the build that produced it.
- **Every text now carries a note for translators**, saying what it is and
  where it stands - a button, a status line, or one of the line beginnings
  the device tree and the F1 help have to agree on word for word. 365 of them
  had none, and the build now refuses a text without one.
- **The three texts the add-on store shows** - the name, the description and
  "What's new" - are translated through the catalogue as well, the way the
  official add-on template does it. The localised manifests in the package
  are generated from it, so a further language needs nothing but its `.po`
  file. "What's new" is markdown now: NVDA renders it and reads it line by
  line.
- **A refused command records the cloud's answer** and the payload that was
  sent, instead of only the message shown to the user.
- The manual claimed the add-on logs neither passwords nor tokens nor the
  email address. True of the add-on - but NVDA writes the whole configuration
  into the log at startup, and the email address stands there in plain text.

## 26.8.1beta1 (August 2026)

Initial pre-release, for testing. What the add-on does:

- **Four platforms, each usable on its own.** Meross (plugs, power strips,
  lights, LED strips, diffusers, hubs with temperature, humidity and water
  sensors), Netatmo (thermostats and valves, weather station and indoor air as
  a display), VeSync / Levoit (air purifiers and tower fans) and Cozytouch /
  Atlantic (hot water heat pump, **experimental** – one device model tested so
  far).
- **One tree view for everything.** NVDA+Shift+H opens it: arrow keys to
  navigate, Enter to switch, speech and tone feedback for every action.
- **A favorites layer.** Devices used often – down to a single outlet of a
  power strip – get a digit and are switched with two keystrokes, without
  opening the menu. One assignable shortcut, not eighteen separate gestures.
- **Changes made elsewhere are announced**, whether they came from the
  manufacturer app, a voice assistant or the button on the device itself.
- **A history** of switching actions and sensor readings, with a detail view
  that can be read line by line and a CSV export. Dates, times and separators
  follow the system region, and the timestamp survives a spreadsheet intact.
- **Sign-in once**, with the manufacturer account. New credentials are proven
  at the platform before they are stored, so a typo is caught while it can
  still be corrected. Storage is encrypted and local (Windows DPAPI).
- **Careful with the cloud budget.** Meross allows 200 messages per hour and
  device; background polling keeps a reserve that only user-initiated queries
  may spend.
- **English and German.** The source language is English, so an interface
  without a matching translation stays English rather than becoming German.
  Another language is a matter of one `.po` file.

### Known in this pre-release

- After a refresh, and after a platform is connected late, the selected entry
  in the device tree is announced two or three times, once with the wrong
  level; the NVDA log notes an error in its tree handling. The cause is the
  tree being rebuilt while it holds the focus. Nothing is lost, and switching
  devices is unaffected.
- The same rebuild puts the selection back on the first entry – a position
  further down the list is not kept across a refresh.
- Switching a platform off in the settings stops the polling immediately, but
  its devices stay in the tree until NVDA is restarted.
