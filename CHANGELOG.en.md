# Changelog – Smart Home Control

English translation of [CHANGELOG.md](CHANGELOG.md). The German file is the
original; this one is kept in step with it and is what the GitHub release
notes are built from.

## 26.7.3 (August 2026)

- **Cozytouch/Atlantic is now marked as experimental everywhere it is
  visible.** The caveat used to live only in the "maturity" section of the
  documentation, so anyone who enabled the platform in the settings dialog
  never saw it. The tab, the checkbox that enables the platform, the
  notifications group box and the platform node in the device menu now carry
  the marker, and the Cozytouch tab opens with a short explanation of what
  "experimental" means in practice: only one hot water heat pump has been
  tested so far, and other device types may be presented incorrectly. The
  add-on description shows it too, so it is visible in the add-on store.
- **GitHub releases now carry the changelog of the version being released**
  instead of one fixed boilerplate text – in English, because release pages are
  read internationally. The source is the new `CHANGELOG.en.md`;
  `build_addon.py relnotes` cuts out the matching section. If that section is
  missing the release run fails rather than publishing a release without a
  changelog, and likewise if the tag does not match the version in
  `manifest.ini`.
- **The repository landing page is now in English.** It is the "homepage" link
  in the add-on store, where users from all over the NVDA world arrive – and
  the add-on itself speaks both languages anyway. The German version is
  unchanged in `README.de.md`; each links to the other on its first line.

## 26.7.2 (August 2026)

### History – rebuilt

The history used to mix two things with opposite requirements: switching
events are rare and individually important, readings are frequent and only
interesting as a trend. Both shared one list of 5,000 slots, so the readings
gradually pushed out exactly what you go looking for later.

- **Switching through the favourite gestures is now recorded.** Only what you
  switched through the device menu used to appear; the same device switched
  through a favourite command showed up nowhere. Changing the diffuser mode
  was added as well.
- **External switching now appears in the history.** When a device is switched
  through the manufacturer app, a voice assistant or the button on the device,
  it was announced but never recorded – so the history could not answer the
  one question you open it for.
- **Every event shows where it came from:** "me", "external" or "automatic".
  External switching deliberately says only "external" – whether it was the
  app, a voice assistant or the button is not something the cloud reports.
- **Readings are only stored on real changes** (temperature from 0.3 degrees,
  humidity from 2%, CO₂ from 50 ppm, air pressure from 1 mbar), and at least
  once an hour so a steady value does not look like a gap in the data.
  Previously a full set of values was written for every device each time the
  device menu was opened, even when nothing had changed.
- **Readings are now collected in the background**, not only when the menu is
  opened. The intended 15-minute lockout never survived closing the window –
  opening the menu five times produced five identical entries per device. Only
  with background collection is the history a history rather than a log of
  menu openings.
- **Events and readings are kept separately:** events for one year, readings
  for 90 days. A switching event can no longer be pushed out by readings.
- **The history dialog has two views.** "Events" shows the switching events
  with their origin, grouped by day ("Today", "Yesterday", then the date) –
  which spares the speech output the date on every single line. "Readings"
  shows one line per device and quantity with the lowest, highest, average and
  current value; the individual changes are in the detail window (Enter). The
  average is time-weighted so a short turbulent phase does not skew it against
  a long quiet one.
- Opening the history no longer announces the number of matches; it is in the
  status line.
- **Existing histories are migrated once:** all previous switching events are
  kept, and the stored readings are run through the same change filter
  retroactively. Only repetitions are discarded; how many is recorded in the
  NVDA log.

### Meross

- **Less noise in the NVDA log.** If the connection to the Meross cloud drops
  briefly – a Wi-Fi change is enough – the bundled Meross library then reports
  two warnings for *every* device ("Updating status for device …", "… changed
  its online status while manager was offline"). With ten devices that is
  twenty lines at once even though nothing is broken: the library is just
  restoring the state. These and a few other routine library messages now
  appear as debug entries instead of warnings.
- Messages that can point to a real problem – failed subscriptions, invalid
  signatures, unknown message types or a push for an unknown device – stay
  visible on purpose.

### Netatmo

- **Expanding a thermostat no longer blocks the interface.** To show the name
  of the active heating schedule, a request was sent to the Netatmo cloud right
  when you expanded the entry – on the same thread that serves the window. If
  Netatmo answered "Service temporarily unavailable" (which happens on their
  side occasionally), the window stood still for about seven seconds, and
  considerably longer on a hanging connection.
- **Heating schedules and room layout are cached for five minutes.** They only
  change when you rearrange something in the Netatmo app. Previously every
  expand and collapse of a thermostat triggered a full query – the fastest way
  to approach Netatmo's request limit. After the add-on changes a schedule the
  cache is dropped immediately, and the regular device poll deliberately still
  goes straight to the cloud so a real outage is recognised as one.
- **Temporary Netatmo server conditions** (503, 500, request limit) no longer
  appear in the NVDA log as errors but as warnings with an explanation of what
  the condition means – they are not a problem with the add-on or your own
  settings.

### Fixed

- **Power strips could disappear from the menu entirely** when the Meross cloud
  returned the channel list without the leading aggregate channel – the device
  then counted as single-channel and both outlets were lost. Online and offline
  detection now derive the outlets the same way.
- **In the same case the status message of the first outlet was swallowed.**
  Whether a channel is an outlet is no longer decided by its number.
- **Shortly after signing in**, status messages of a power strip could overwrite
  each other and be announced with the device name instead of the outlet name.
- **Consumption "today"** was off by one hour on the two daylight saving
  changeover days of the year.
- **Closing the settings** during a running connection test or the Netatmo sign-in
  (up to 120 seconds) could produce an error in the NVDA log.
- **Favourites kept the name from back then** when the device was later renamed
  in the manufacturer app. This showed everywhere the stored name is used –
  for unreachable devices, for instance, and in the announcements of the
  favourite commands.
- **The first time credentials were encrypted without Windows DPAPI**, a console
  window flashed up briefly and stole the focus.
- The status line of the Meross hub sensors and the note about an unknown
  Cozytouch model appeared in German even in the English interface.

### Internal

- The background scheduler no longer wakes up every second but sleeps until the
  next poll is due, and is woken deliberately when the device menu is opened.
  The gap between two polls is also measured from the end of the previous one –
  on a slow connection the polls used to bunch up, of all places with Meross and
  its message limit.
- All modules now have a fallback for the translation function; previously a
  failure to load the translations would have caused a follow-up error in the
  middle of building the dialog.
- `build_addon.py` checks when packaging whether all interface texts are in the
  translation file and whether the compiled version matches; a package without a
  translation file is rejected. Also new is `build_addon.py licenses`, which
  generates the overview of bundled third-party components from their metadata.
- `.gitignore` excluded the compiled translation file – a fresh clone of the
  repository would have produced an add-on without an English interface.

### Fixed for multi-outlet plugs

- **Power strips with more than two outlets** (MSS425, MSS425E, MSS425F) showed
  only two outlets while offline – and the wrong ones: what was announced as
  "outlet 1" was physically a different one. The outlets are now derived from
  the Meross cloud's channel data instead of guessed, so online and offline show
  the same outlets in the same order.
- **The outlet names assigned in the Meross app** (e.g. "pump" instead of
  "outlet 1") are now shown for offline devices too, not only while online.
- **Favourites on individual outlets** were not kept when the device had been
  offline in between, because the outlet identifier differed between offline and
  online. Both paths now use the same identifier.
- **Outlets could show the wrong state permanently:** after the first status
  message through the Meross cloud, that outlet was never queried again. If it
  was then switched through the Meross app or on the device itself, the display
  stayed wrong for the rest of the NVDA session – even after refreshing. The
  state is regularly reconciled again now.
- **After a connection drop**, the outlets read their state from a stale
  connection and froze.
- **The MOP320 outdoor plug** was not recognised as a plug with energy
  monitoring while offline.
- Offline **MSL lights** could trigger an error in the NVDA log when their
  colour mode was queried.
- Querying the power consumption on an outlet of an offline multi-outlet device
  failed.

### Other devices

- **Air purifiers and fans with a European model identifier** (e.g.
  `LAP-C201S-WEU`) were not shown at all, because only certain country variants
  were listed by name. Model detection now considers the series regardless of
  the country suffix – this affects the Levoit Core 200S, 300S and 400S as well
  as future regional variants of the tower fans.

### Documentation

- The readmes were checked for factual accuracy and corrected: the hub
  assignment table (MSH300/MSH450) was dropped, as it matched neither the
  behaviour of the add-on nor the manufacturer's information; MSS425E and
  MSS425F are now named explicitly, including the note that the USB ports of
  these strips can only be switched together; the figures on the Meross message
  limit were made precise after a written support statement (advance warning,
  three-day grace period, then a 24-hour block of the affected device); the
  licence and the bundled third-party components are now listed.

### Security

- **CSV export:** device names come from the manufacturer cloud. If a name began
  with a formula character (`=`, `+`, `-`, `@`), Excel or LibreOffice evaluated
  the cell as a formula instead of text when opening it. The text columns of the
  export are now defused; the number columns stay usable as numbers.

### Internal

- Model lists for plugs and energy monitoring now live centrally in one place
  instead of being maintained twice, in the online and the offline path.
- The translation initialisation in the main module was made safe (a failure
  would have prevented the import of the entire add-on rather than just the
  translations).

## 26.07.1 (July 2026)

### New features

- **Energy report:** a new, freely assignable command announces the consumption
  of the metering plugs for today and the last 7 days in kilowatt hours. The
  primary source is the meter inside the device itself (which keeps counting
  while NVDA is not running); power samples collected in the background serve as
  a fallback, marked as "estimated".
- **Favourite gestures:** nine freely assignable commands each for "toggle
  favourite N" and "announce status of favourite N" – switching and querying
  without an open menu. All without a default gesture (assignable under NVDA
  menu → Options → Input gestures).
- **Connection diagnostics:** a freely assignable command that announces the
  connection state per platform, the network state and the remaining lifetime of
  the Netatmo token.
- **Cozytouch boost duration:** the boost duration can now be changed in the
  device entry (experimental – whether the cloud accepts the write is verified
  against the actual device value). With boost active and no duration set, "no
  time limit set" is shown.
- **Netatmo rooms:** thermostats and radiator valves are grouped in the device
  menu by the rooms from the Netatmo app; the room name is also part of the
  device name.

### Operation and multi-channel devices

- Channels of power strips and dual plugs are now consistently called "device
  name: outlet X" (e.g. "garden: outlet 1" or, for a named outlet, "garden:
  outlet pump"); the former duplicate "channel:" prefix is gone.
- Offline Meross devices are now marked as "offline" in the device name itself
  (multi-channel devices too), and the individual outlets of an offline
  multi-channel device can be expanded (showing "status: offline").
- New setting "show on opening": you can choose whether the "all devices" or the
  "favourites" tab is active when the device menu opens.

### Device support

- Meross MSS425 (multi-outlet power strip) and MOP320 (outdoor dual plug with
  energy monitoring) are now correctly recognised as plugs (previously in the
  "other devices" category, or not recognised at all).
- Corrected sensor assignment: MS400 and MS405 are recognised as water sensors.
- Cozytouch: the exact device model is now shown in the device menu (as with the
  other platforms); unknown models show the model ID.
- Cozytouch: Wi-Fi signal and Wi-Fi network are now shown together with the
  firmware version in the technical block at the end of the device entry.

### Further corrections

- Cozytouch: changing the heating mode (especially to "schedule") produced an
  error tone even though the change worked – and your own change was then
  reported as an external one. The Atlantic cloud reports such commands as
  "failed" (execution status 4) in some cases even though the device accepts
  them. The add-on therefore no longer relies on that report but verifies the
  value that actually arrived at the device.
- Cozytouch: if the actual heating target (Eco+/boost) differs from the setpoint,
  the collapsed device entry now shows both (e.g. "target 58°C, current heating
  target 53.2°C") – the setpoint display used to be misleading.
- Device lists corrected (after research): the MSH450 supports the MS100F (not
  the original MS100, which needs the MSH300); only the NRV radiator valve has
  open window detection, not the NATherm1; the supported Levoit Core purifiers
  have neither turbo nor pet mode (and the Core 200S has no auto mode); the
  Meross cloud limit blocks for 24 hours. Clarified: only Netatmo offers an
  official API – Meross, VeSync and Cozytouch are reverse-engineered.

### Documentation

- Both help files (German/English) and the README were reworked: informal
  address, clearer structure of the supported devices, keyboard shortcuts as a
  list instead of a table, structured setup per platform and a thoroughly
  explained Netatmo section (redirect URI and port).

### Security and libraries

All bundled libraries were updated, in particular because of known security
vulnerabilities: urllib3 2.5.0 → 2.7.0 (CVE-2025-66418, CVE-2025-66471,
CVE-2026-21441, CVE-2026-44432) and aiohttp 3.13.1 → 3.14.1 (including
CVE-2026-34993). Also: requests 2.34.2, certifi 2026.6.17, idna 3.18,
attrs 26.1.0, aiohappyeyeballs 2.7.1, typing_extensions 4.16.0,
multidict 6.7.1, yarl 1.24.2, propcache 0.5.2, charset_normalizer 3.4.9 –
each for both NVDA architectures (32-bit/Python 3.11 and 64-bit/Python 3.13).

### Bug fixes

- Log messages from the favourites and history modules now appear correctly in
  the NVDA log (previously they were lost, including errors while saving).
- Netatmo: token renewal is now thread-safe. Previously two simultaneous
  renewals (background refresh plus a dialog action) could in the worst case
  invalidate the stored sign-in.
- History: entries are now written in batches (at least every 30 seconds or
  after 20 entries) instead of rewriting the whole file for every entry. The
  remainder is saved when NVDA shuts down.
- VeSync: HTTP connections are now reused (faster response times in the device
  dialog, less network load).
- CSV export of the history: the file is closed cleanly in every error case.

### Internal

- The main dialog class is now called `SmartHomeControlDialog` (previously
  `MerossDeviceDialog` – a leftover from the Meross-only early days).
- Large module refactoring (behaviour unchanged): the device dialog was split
  into separate modules (history dialog, context help, favourites view), and so
  was the main plugin module (password management, polling scheduler, change
  detection). No module exceeds 3000 lines any more; the split follows the
  existing mixin pattern.

### Miscellaneous

- New build system: `build_addon.py` plus `requirements-bundle.txt` produce the
  lib bundle and the add-on package reproducibly.
- Licence (GPL v2 or later) and changelog added.
- Internal cleanup (BOM characters removed, .gitignore).

## 26.07 (July 2026)

- English translation (interface and documentation).
- User manual (readme) integrated in German and English.

## 26.05 (May 2026)

- Support for Cozytouch/Atlantic hot water heat pumps (e.g. Austria Email
  Revolution) including boost, away mode and target temperature.
- Four platforms: Meross, Netatmo, VeSync/Levoit, Cozytouch/Atlantic.
- Favourites, history with CSV export, external change detection, background
  refresh with a unified scheduler.
