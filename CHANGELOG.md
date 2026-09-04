# Changelog – Smart Home Control

The GitHub release notes are built from the section of the released version.

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
