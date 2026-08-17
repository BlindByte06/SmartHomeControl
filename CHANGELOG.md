# Changelog – Smart Home Control

The GitHub release notes are built from the section of the released version.

## 26.8.1beta1 (August 2026)

Beta for testing. Everything below is new since 26.7.5.

### A wrong password was accepted

- Once a platform was connected, the settings took **any** password without
  complaint. A running session does not care what is stored: it keeps working
  with the credentials it logged in with. The typo was saved, the device list
  stayed complete, everything looked right - and at the next NVDA start the
  login failed for a reason that was no longer anywhere near the mistake.
- Changed credentials are now proven before they are stored: saving logs in at
  the platform first, and only then writes.
- A refusal saves nothing. The dialog stays open, says why, and puts the focus
  back in the password field.
- Only the login is attempted, not the device list - that answers the question
  in a fraction of a second, while reading 41 Meross devices takes fifteen.
- Unchanged credentials are not probed, so saving stays as fast as it was for
  everything except a real change.
- The VeSync and Cozytouch token the check earns on the way is kept and saves
  the following login a second round trip.

### Changed credentials reach a running session

- A platform used to be connected only when it had no connection yet. A new
  password therefore reached the configuration but never the platform.
- Platforms whose credentials changed are now logged in again right away.
- The old session is closed, and its devices are replaced rather than joined:
  a device that was renamed or removed in the account used to stay in the
  tree, and every device of a swapped account appeared twice.

### Entering a password again, without the way back

- A failed login used to leave an announcement and nothing else. What followed
  was: find the settings, find the tab, find the field.
- A question now offers the new attempt directly and opens the settings at the
  tab that failed, with the focus in the password field.
- Only when the credentials themselves were refused. A timeout or a missing
  network is announced and nothing more - a password dialog would be the wrong
  answer to it.
- The automatic login at NVDA start never asks; there the network is often
  simply not up yet. Only a login that follows a save does.
- Netatmo is exempt: its authorisation is granted in the browser, so the way
  back there is the "Connect to Netatmo" button, not a password field.

### One login per platform, and the old session really ends

- A Meross login with 41 devices takes fifteen seconds, and the connection only
  becomes visible when it is through. A second Save in that window therefore
  saw "no connection yet" and started a second login. Both ran to the end, and
  both stayed.
- Two MQTT sessions then polled the same account: every push notification
  arrived twice, and the hourly message budget was spent twice over.
- A login now claims its platform for its duration - a second one is skipped
  instead of started.
- The replaced session is closed in every case, not only after a credential
  change: an abandoned session does not notice that it has been replaced.

### Other

- A corrected Netatmo **client secret** no longer costs the browser
  authorisation. The tokens belong to the client ID, not to the secret, so they
  stay valid. Only a new client ID discards them - they were issued to another
  app registration then and would fail at the next refresh with an
  "invalid_client" nobody can place.
- The refresh button in the device menu carried a German label after the first
  refresh, on every interface language - and moved its accelerator from Alt+R
  to Alt+K along with it.
- A Meross connection error appeared in German regardless of the interface
  language.
- Whether a login failed on the credentials or on the network is now decided by
  the error type instead of its wording. The wording is translated, so a check
  for "login" would have worked in English and been wrong in German.

### Known in this beta

- After a refresh, and after a platform is connected late, the selected entry
  in the device tree is announced two or three times, once with the wrong
  level; the NVDA log notes an error in its tree handling. The cause is the
  tree being rebuilt while it holds the focus. Nothing is lost, and switching
  devices is unaffected.
- The same rebuild puts the selection back on the first entry - a position
  further down the list is not kept across a refresh.
- Switching a platform off in the settings stops the polling immediately, but
  its devices stay in the tree until NVDA is restarted.

## 26.7.5 (August 2026)

### Dates follow the region

Every date in the history was written German: `%d.%m.%Y`, regardless of who
was reading. For a tester in the USA that is not merely unfamiliar but
ambiguous – `08.09.2026` could be either day. Dates and times now follow the
system region, which NVDA already sets: `16.08.2026 16:47` in German,
`8/16/2026 4:47 PM` in the USA, `16/08/2026 16:47` in Spain. Regions that
write the time with AM/PM get it that way, the others keep the 24-hour form.

The timestamp in the CSV export deliberately stays ISO 8601 – it is meant to
be language-independent and sortable.

The away scheduling of the Cozytouch heat pump keeps the German format for
now: there the same pattern also parses what is typed in and is quoted in the
hint text, so display, input and hint have to move together. The device is
sold in Europe only.

### Translating made possible without the author

Two commands were missing, and their absence was quiet rather than loud:

- `python build_addon.py pot` rebuilds the template from the source. Without
  it the template aged with every change to the code, and translators were
  handed a file in which new texts were simply absent.
- `python build_addon.py mo` compiles every `.po` under `locale/` into the
  `.mo` that NVDA actually loads. A translation delivered as a `.po` alone
  used to have no effect at all.

Extracting the translator hints also got two fixes: a hint is now found even
when an ordinary code comment sits above it, and a hint that exists only in
the previous template is carried over instead of being dropped. 654 of 1021
texts now carry one.

### Other

- The author's e-mail address is in the manifest, as it is with most add-ons.

## 26.7.4 (August 2026)

### Favourite gestures – rethought

The previous 18 individual commands ("toggle favourite 1–9", "announce
status of favourite 1–9") had two problems. First, due to a bug they did
not appear in the Input Gestures dialog at all, so a shortcut could never
be assigned to them — the feature described in the manual was simply
unreachable (NVDA logged 18 warnings about it on every start, with nothing
showing in the interface). Second, even the repaired version would have
been inconvenient: 18 shortcuts to assign one by one, and "favourite 1"
was merely the list position — adding a favourite could shift which device
a memorised shortcut switches.

Both are now replaced by the **favorites layer** with a single shortcut.
Like the other extra commands it comes **without a default gesture** — a
shipped default cannot be chosen free of conflicts with any confidence
(keyboard layout, other add-ons, the user's own assignments), and a
shortcut that overrides an existing binding would be worse than none.
Assign it under **NVDA menu → Preferences → Input gestures → category "Smart
Home Control"**. After that:

- The layer prompts with **"Choose a favorite: digit 1 to 9"** – saying
  that it is waiting and what it expects.
- **Digits 1–9 immediately announce the status** of the favourite with
  that number.
- **The same digit again toggles it**, with no time pressure: the layer
  stays open until you have decided, so you can listen to the whole status
  first. A different digit announces its status and becomes the remembered
  one, so "1, 2, 1" switches nothing.
- **Individual outlets of a power strip are now favourites in their own
  right.** On the MSS425/E/F, MSS620 and MOP320 an outlet can get its own
  digit ("pump" rather than "all outlets at once") – expand the strip in
  the device menu, select the outlet and press Ctrl+B. The whole device
  remains available as a separate favourite and still switches all outlets
  together.
- **The favourites row in the device menu now shows the new state at
  once.** After "add to favorites" it kept the old label, so pressing
  Enter again only reported "already in favorites". Every device was
  affected; it was most visible on the power strips. Label and stored
  action are now updated in place, for Ctrl+B as well, without losing the
  focus.
- **Favourites that cannot be switched** report "cannot be switched –
  adjustable in the device menu" on the second press. That covers Meross
  sensors and hubs plus every Netatmo device; switchable are Meross plugs
  (individual outlets too), lights and diffusers, Levoit air purifiers and
  fans, and the Cozytouch hot water heat pump.
- **0 reads the assignments** ("1: plug, 2: fan, …"), **Escape cancels**,
  and so does any other key.
- **The number now belongs to the device, not to the list position.** It
  is assigned once when the device is added and announced right away
  ("… added to favorites, digit 3"), shown in front of the device name in
  the favourites tab, and kept when other favourites are removed. Existing
  favourites get their numbers on first start in their previous order, so
  nothing shifts.

- **All interface texts now use impersonal wording.** In German, 18 dialogs
  and messages still addressed the user formally with "Sie" while one used
  the informal "du" – the add-on spoke in two different registers. They are
  now uniformly neutral. This affects the settings, the colour, brightness,
  mode and fan dialogs, the filter and history confirmations and two Netatmo
  messages. The German documentation was reworded to match.

- **The German README has been dropped.** The translations now live only
  where NVDA ships them anyway, under `doc/de/` and `doc/en/`. That is what
  the other add-ons in the store do (sample: none of seven checked has a
  language-specific README at the repository root; their translations sit
  under `addon/doc/<language>/`). GitHub never displayed a second README on
  its own, and it made the same text need maintaining a fourth time.

### Favourite announcements completed

- **Sensors no longer report "off".** A Meross temperature sensor has no
  on/off state, yet the announcement read out its internal "off" anyway.
  Temperature and humidity sensors now give their readings, water sensors
  say "dry" or "water detected", hubs report how many sensors they carry —
  and all of them add the battery level.
- **Levoit air purifiers and fans now announce everything** the device menu
  shows: mode, fan level, air quality, filter life. Previously only "on" or
  "off" came out, while Netatmo thermostats had long been giving their full
  summary.
- **The Cozytouch heat pump now also names the current heating target**
  (under Eco+/boost it differs from the setpoint) and the available hot
  water.
- **The switching tone matches the device menu:** a high tone when
  switching on, a low one when switching off. Previously the same success
  tone played either way, so the sound did not tell you which direction it
  went.

### History

- **The history no longer speaks the language of the day.** The detail of an
  entry was stored as ready-translated text, so entries written while the
  interface was German stayed German for good – an English interface showed
  "Switched off: Aus". Where the action already says everything ("switched
  off", "mute switched on") the detail is now dropped entirely, and where it
  carries information it is stored language-neutrally: a fan level as the bare
  number, a mode as its key, a colour as its RGB values. That also repairs
  entries already on disk.
- **Readings tell you when they were measured.** The condensed view showed
  numbers without a single time: with the "all time" filter there was no way
  to tell which period they covered. Each row now has the time of the latest
  reading, the status line names the whole period, and the detail view repeats
  it per series. The column "Points" – whose meaning nobody could guess – is
  now "Readings", next to "Latest value" and "Latest reading".
- **More sensors are recorded.** Besides temperature, humidity, CO2 and air
  pressure, the history now keeps the particulate values of the Levoit air
  purifiers (PM2.5 and PM10), the noise level of the Netatmo indoor module and
  the temperature measured by the Levoit tower fans.
- **The last German texts are gone.** Two of them were audible: exception
  texts are embedded into announcements, so a Meross timeout or a failed
  Netatmo token request spoke German in an English interface. Fifteen further
  log messages were German and showed up in every log a tester sends. The
  internal names of the light colours followed – "tageslicht" is not a word
  in an English code base; the German ones stay readable in favourites and
  history that were written with them.
- **The log is quiet again.** Two Netatmo lines were logged on every poll,
  roughly every 30 seconds: they alone made up 87% of the add-on's entire log
  output at info level and buried the lines that matter. They are debug now.
- **The CSV export was incomplete and half raw.** The particulate and noise
  columns were missing entirely, so the newly recorded quantities never
  reached the file. The action stood in it as its internal key
  ("toggle_off") while everything around it was translated, and details kept
  by older versions still arrived in their original language. Actions and
  details are now prepared exactly as in the dialog; the raw key stays as a
  column of its own, so the file remains usable for evaluation.
- **A CSV only contains columns it can fill.** The export used one fixed set
  of columns for both views, so a file of switching events carried seven
  empty measurement columns and a "Type" column reading "action" in every
  row – eight cells per row to skip past for nothing. The columns now come
  from the data: events get seven, readings only the quantities that
  actually occur.
- **The export is now written the way a spreadsheet reads it.** Two findings
  from measuring against Excel 16:
  - The timestamp was recognised as a date, the cell reformatted, and
    because the column is too narrow it showed nothing but "##########" –
    which is exactly what the screen reader read out. As ISO 8601 with the
    "T" it stays text, is readable in full and still sorts chronologically.
  - Decimal numbers were worse than unreadable: "28.5" is not a number in a
    German Excel but matches the day.month pattern, so the temperature
    28.5 °C silently became the date "28 May". The list and decimal
    separators now follow the system region, which is what a spreadsheet
    expects – and the hard-coded semicolon had put every line of an English
    Excel into a single cell.
- **The suggested file name says which view is exported.** Events and
  readings both leave through the same button; the file did not reveal which
  of the two was inside.
- **The origin of an event is "you" instead of "me".** The text is spoken by
  the add-on, so "me" read as though the add-on had done the switching.
- **All sensors of one hub shared a single row.** Meross sensors carry the
  UUID of their hub; the wrapper offers a separate identity for exactly that
  reason, but the history used the plain UUID. Two sensors on the same hub
  therefore merged into one series – one of them disappeared from the view
  entirely, and the minimum, maximum and average of the remaining one mixed
  the values of both rooms. The history now uses the unique identity, and the
  view separates entries already stored by name, so the readings recorded so
  far become visible again. Entries written under the old key are moved once
  to the new one as soon as the device list is known – without that step
  every sensor would show up twice, once with the readings up to the change
  and once with those after it.
- **The water sensors are finally heard from.** An MS400/MS405 reports no
  measured value but a state, and that state was shown in the device menu
  only – it was never announced and never recorded. Whoever did not happen to
  open the menu learned nothing about a leak. A change is now announced with
  an error tone and lands in the history as an event ("water alarm" / "no
  water detected any more"). It can be switched off under Notifications →
  Meross.
- **Temperature sensors with a display (MS130) could stay empty.** Their
  values were read exclusively from push messages; before the first message
  arrived, the sensor delivered nothing at all and was missing from the
  history entirely. The generic paths are now tried as well. Sensors that
  deliver no reading in a pass are named in the log, so a gap can be
  identified instead of guessed.
- **The readings list is readable again.** It had grown to eight columns, and
  a screen reader reads every column of the focused row on each arrow key – so
  one row became a long sentence. The list now holds device, quantity, latest
  value and time of the latest reading; lowest, highest, average and the
  number of readings are one Enter away.
- **The detail view can be read line by line.** It used to be a message box,
  which hands its whole content to the screen reader as a single block –
  twenty lines of readings arrived as one utterance that could not be
  navigated or copied in parts. It is now a read-only text field: arrow keys
  move line by line, the braille display follows, and Ctrl+C works.
- **The Netatmo relay no longer appears as a second sensor.** As a gateway it
  has no sensor of its own, but it was given the room temperature of the
  thermostat in the same room – and thus showed up as its own series with
  identical values (101 of 101 timestamps were the same).

### Documentation

- **The manual describes the device history.** A whole tool – two views,
  filters, detail view, CSV export, deletion – was reachable only through
  Ctrl+H inside the device menu and appeared in neither manual: the word
  "history" did not occur once. The same went for Ctrl+T, Ctrl+F, Ctrl+B, F1
  and F5, which now have their own list.
- **The English manual named a menu that does not exist.** It pointed to "NVDA
  menu → Options"; the English NVDA calls it **Preferences** ("Options" is the
  German translation of it).
- **The texts no longer address the reader.** German had already been reworded;
  the English manual and README followed, in 30 places.
- **The package no longer carries 196 files of foreign test code.**
  pycryptodomex ships its own test suite, which is never imported at runtime.
  Without it the package drops from 6.1 to 5.4 MB.

### Faster device menu, gentler on the cloud

- **Opening the device menu no longer waits for the same query twice.** The
  menu fetched the device status itself while the background poll was doing
  exactly the same – both over one connection, so they queued up behind each
  other. In the log this was visible as up to three complete rounds for the
  same 41 devices within 13 seconds. Whichever of the two starts first now
  does the work, the other waits for it and uses its result.
- **The cache was expiring just before every poll.** It counted as fresh for
  45 seconds while the background poll ran every 45 seconds *plus* the several
  seconds the poll itself takes – so the menu almost always found it stale and
  queried again. Additionally the cache time was only refreshed after Meross
  polls; with Meross switched off it never counted as fresh at all.
- **Energy consumption is available more reliably.** With the device menu open,
  a metering plug spent its entire hourly cloud budget on the routine polling,
  so the consumption query – one message per plug every 15 minutes – was the
  one that kept getting dropped. The routine polling now stops short of a small
  reserve that only queries you ask for yourself may use. The consumption of
  all plugs is also fetched in one parallel query instead of one blocking
  query per plug, which used to run into the timeout from the second plug on.


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
- **Renewed access is now stored.** Netatmo issues a new refresh token every
  time it renews the access – and invalidates the previous one. That new token
  only ever lived in memory, so the configuration kept the one from the last
  sign-in. After restarting NVDA hours later, an already invalidated token was
  restored and Netatmo had to be authorised again. The same applied to the
  "test Netatmo" button in the settings: the test itself could invalidate the
  stored access it was meant to check.
- **The connection diagnostics reported the token state correctly again.** It
  read a copy that was only ever written when signing in, so from about three
  hours after the sign-in it permanently claimed "expired, will be renewed on
  the next request" – a state that never changed, because the token had long
  been renewed elsewhere. It now reads the live value and, if the
  authorisation really is gone, says that it has to be renewed in the settings
  instead of promising an automatic renewal that cannot happen.

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
- **Changing the filter or the sort order could freeze NVDA** as long as the
  device list was still empty – after a failed sign-in, for instance, or while
  there was no network. The list was reloaded from the cloud on the same
  thread that serves the window; that now happens in the background like every
  other load.

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
  menu → Preferences → Input gestures).
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
