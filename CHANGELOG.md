# Changelog – Smart Home Control

The GitHub release notes are built from the section of the released version.

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
