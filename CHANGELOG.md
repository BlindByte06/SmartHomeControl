# Changelog – Smart Home Control

The GitHub release notes are built from the section of the released version.

## 26.8.1beta2 (August 2026)

Beta for testing. The air fryer support in here was built against a tester's
appliance over several rounds; what is confirmed and what is not is said
plainly at each point.

### The favorites layer no longer switches on a much later second press

- A digit announced a status and the SAME digit switched, with no time limit at
  all: press a digit, get distracted, press it again minutes later, and a device
  switched. On a power strip carrying a computer that is lost work rather than a
  nuisance.
- The switching press now has to follow the announcement within a window. Five
  seconds by default, adjustable from one to thirty in the settings.
- **Five and not two**, although two is what feels right for a lamp. A purifier
  announces mode, fan level, air quality and filter life, which takes four to
  five seconds to speak - a two-second window would expire while the very
  announcement it is meant to be a reaction to is still running. That is exactly
  the failure the layer was built to remove, and it is why it had no window in
  the first place.
- An expired window is never a dead end: the same digit then announces again and
  opens a fresh one, so switching stays two quick presses away. A short window
  costs a keystroke, never the ability to switch.
- The check that asserted the opposite ("a long pause switches anyway") was
  rewritten rather than deleted, and the layer's test clock is now controllable
  so the window is measured to the second instead of assumed.

### Ctrl+B in the favorites tab hit the power strip, not the outlet

- A favourite outlet could not be removed from the favorites tab. Ctrl+B on it
  answered "is not a favorite" for an entry plainly listed on screen: an outlet
  is stored under the strip's uuid plus a "_ch1" suffix, and the handler looked
  the strip up instead.
- The worse half of the same mistake: when the strip happened to be a favourite
  too, Ctrl+B on an outlet removed the STRIP and announced the strip's name -
  which sounds entirely plausible until the outlet is still sitting there
  afterwards.
- The devices tab had been right about this for a while, with a comment saying
  why. The fix simply never travelled to the second tab. A check now asserts
  both handlers read the outlet before the device, so it cannot drift apart
  again.

### The add-on writes its version into the log

- The startup block now carries an "Add-on version:" line. NVDA logs a version
  as well, but a problem report is read by searching the add-on's own block, and
  having the version next to the rest of the evidence answers the first question
  of every report in one line. Two test rounds were spent on a build that turned
  out not to be the one meant.

### Cosori air fryers are shown

- The **CAF-P583S** series (Dual Blaze) appears in the device tree with its
  name, its switching state and whether it is online, regardless of the country
  code. Nothing about it can be switched or set - what the device list reports
  is everything there is - and the entry says so instead of leaving an empty
  branch behind.
- Reason to show it at all: an account holding only such a device came out as
  "no devices", which reads exactly like a refused login. A tester spent a mail
  round on that.
- No status call is made for such a device. Previously a device without one
  would have been asked anyway, failed, and been marked stale after a few
  rounds.

### The air fryer reports its programme

- The status call this device family answers is `getAirfryerStatus`. Found by
  asking: of seven candidates it was the only one the appliance accepted, the
  other six replied "method unknown". Note the lower case f - `getAirFryerStatus`
  is one of the six that do not work.
- The device tree now shows the programme state, the programme, the remaining
  time and both temperatures - the measured one and the one the programme was
  set to - in the unit the appliance itself reports, Celsius or Fahrenheit.
- A log of a complete Steak programme settled the three fields that were
  guesswork before:
  - `totalTimeRemaining` counts **seconds**. Over the run the counter fell by
    456 while 456 seconds passed on the clock, exactly one to one, and a
    `cookSetTime` of 480 belonged to a programme set to eight minutes. It is
    displayed as minutes and seconds.
  - `currentTemp` is the **measured** air temperature, not a target: it climbed
    to 217 degrees against a set value of 205, and 205 is the highest this model
    can be set to at all. In standby it stops being a measurement - 172 degrees
    unchanged across 48 readings over 35 minutes - so it is left out there.
    That is where the 181 degrees on a cold appliance came from.
  - The programme is in `stepArray`, not in `cookMode`. The latter reads
    `normal` whatever is running, and the tree used to show "Programme: normal"
    while a Steak programme was going.
- Five cooking states are now known and translated: standby, ready to start,
  cooking, finished and paused. A state nobody has seen is still passed through
  unchanged rather than guessed at - which is exactly how the fifth one arrived:
  `cookStop` turned up raw in a tester's log, and what it means came from the
  tester standing at the appliance. It is the PAUSE button, not a stop. The name
  reads like an ending and is not one, so a paused programme now stays
  stoppable and adjustable, and no new programme is offered over the top of a
  meal that is still in there.
- The time is labelled for what it is. Before a programme runs, the number the
  appliance sends is its whole duration, not a countdown - one logged appliance
  sat in standby reporting Frozen with twelve minutes, which is what that
  programme takes. That now reads "Duration"; "Remaining time" is used only
  while it is actually falling.
- The programme name is taken from the appliance's English `mode`, not from
  `recipeName` - that one arrives in the language of the VeSync app and would
  put foreign text into the interface.
- The displayed temperature is damped. While the appliance holds its
  temperature the reading wobbles by a few degrees, which had the line re-read
  aloud every fifteen seconds without saying anything new. It now follows only
  a change of five degrees or more (nine in Fahrenheit); the climb to the set
  temperature and the reading at the end of a programme still come through.

### A cooking programme can be started, and the appliance supplies the list

- **Start programme** offers the programmes the appliance has reported, plus a
  free start with a temperature and a time of one's own (`startCook`). For a
  preset, the temperature and duration start out at the values the appliance
  holds for it and can be changed for the one cook.
- **The programme list is learned, not written down.** Starting a programme by
  name needs the `recipeId` the appliance uses, and those cannot be worked out:
  on a CAF-P583S they run 1, 2, 3, 5, 6, 9, 13, 14, 15, 16, 17. The gaps say the
  id space belongs to the whole Cosori range rather than to one model, so a
  table in the code would have been right for exactly one appliance and quietly
  wrong for the next. Instead every status reply that carries a loaded programme
  is noted, with its id, set temperature and duration. Selecting a programme on
  the appliance is enough - it does not have to be cooked - and a TwinFry or a
  P585S teaches itself the same way without a line of code.
- Only the appliance's English `mode` is stored as the key, never `recipeName`,
  which arrives in the language of the VeSync app.
- The temperature and duration of a programme are only learned while it has
  just been loaded and not yet started. A one-off adjustment during a cook is
  reported in exactly the same fields, and taking that over would quietly
  redefine the programme: changing Veggies to 180 degrees for ten minutes once
  would have made "Veggies" mean that from then on, including in the
  two-keystroke start where the values are read out but nobody is listening for
  them. Adjusting a programme on the appliance and loading it afresh still
  updates it - that is the appliance's own notion changing, and it is followed.
- The duration goes out in **seconds**, as the appliance counts. Sending minutes
  would have cooked for a sixtieth of the time asked for, which is why a check
  asserts it rather than trusting the comment above it.
- Temperature and time are validated against the appliance's own range (80 to
  205 °C, 1 to 60 minutes) before anything is sent. The cloud would refuse an
  impossible value too, but as a bare error code that helps nobody.
- Starting is offered only from a state known to be idle - deliberately the
  opposite way round from stopping. Stopping something unknown is safe; starting
  into a state nobody has seen is not.
- Preheating stays out. Its command is documented elsewhere, but nothing about
  it has been confirmed against a Dual Blaze, and a preheat that silently does
  nothing is worse than none.
- **Starting does not mean heating**, and that is now measured rather than
  suspected. On the tested appliance the programme, temperature and time are
  set and the fryer goes to "ready to start"; the button on the front begins the
  cooking. Cosori state their air fryers cannot be switched on remotely, to
  comply with a safety standard meant to stop an appliance cooking unattended.
  Nothing here promises heat, therefore: the prompt names only the programme,
  the temperature and the duration, and the confirmation says "set" rather than
  "started". The programme state says which it was one poll later, in words.

### Time and temperature of a running programme can be changed

- **Change temperature** and **Change cooking time** adjust a programme with
  `setTimeOrTemp`.
- **Only while it runs.** Before the programme starts, the appliance refuses
  both - a time change and a temperature change alike, with its own code
  11017000. The two entries therefore appear only while cooking or paused, and
  until then the tree says why rather than holding out a control that cannot
  work.
- Both quantities go out together, not just the one being changed: a payload of
  nothing but `cookSetTemp` was refused while one of nothing but `cookSetTime`
  went through, and the only shape anyone has documented carries both. Mind the
  spelling - `startCook` calls it `cookTemp` inside `startAct`, this call wants
  `cookSetTemp`. A check asserts both, because the wrong one is read as "no
  temperature given" and silently does nothing.
- A new time becomes the whole duration and the countdown starts again from it.
  Measured: a six-minute programme 25 seconds in was given 600 seconds and came
  back with 600 still to run. "Set the time to ten minutes" therefore means ten
  minutes from that moment - and it is why a temperature change sends the time
  **remaining** alongside, so a cook keeps what it had.
- **The temperature may not take effect.** On the tested appliance the cloud
  accepted the change and the measured temperature carried on climbing towards
  the original setting. One short run is not proof, so the entry stays; the
  measured temperature line is the honest place to see what the appliance is
  really doing.

### A refused command now says why

- A refused command writes the cloud's own answer and the payload that was sent
  into the log, and an accepted one writes its payload too. Before this a
  refusal arrived as nothing but the message shown to the user - "the change was
  not accepted" - which says that something went wrong and nothing about what,
  and two test rounds went into reconstructing what the calls that succeeded had
  actually sent.

### Starting a programme takes two keystrokes for the usual case

- Choosing a programme whose settings are already known now goes straight to the
  confirmation, with three buttons: start, change, cancel. Asking for a
  temperature and a time that were already right turned "the usual vegetables"
  into four dialogs, three of them confirmed unchanged.
- The free start and the "change" path still ask for both values, prefilled with
  the programme's own settings.

### Eleven programme names came back from the appliance

- Asking a CAF-P583S to load each of its functions in turn produced the names it
  actually uses, and they are not the obvious ones: `AirFry` has no space,
  `Veggies` is not "Vegetables", and fries arrive as **`French fries`**. That
  last one was the single guess that missed, and it showed up in the interface
  as raw English while the other ten were translated - invisible in an English
  interface, plainly wrong in a German one.
- Found by comparing against what the screen reader actually said, not against
  the add-on's own table, which would have agreed with itself.

### A running cooking programme can be stopped

- **Stop programme** in the device tree ends a programme that is running
  (`endCook`, empty payload). It appears only while there is something to stop
  - not in standby and not after a programme has finished.
- Confirmed before it is sent, with the programme and the remaining time in the
  question and Cancel preselected. Stopping cannot damage anything, but an
  eight-minute programme thrown away three minutes in by a mistyped Enter costs
  a meal.
- Deliberately no optimistic display afterwards, unlike the switches elsewhere
  in the add-on. Those flip a relay and are done; this appliance is hot, and
  showing "standby" a moment before it is true is the one kind of wrong worth
  avoiding here. Confirmed on a real appliance: the command went out at
  19:38:17 and the fryer reported standby on the next poll at 19:38:21.

### A sensor dropout is no longer announced or recorded as a measurement

- A Levoit purifier reports **-1** for its particulate sensor when it has
  nothing to say. That went straight through: the tree announced "PM2.5: -1
  µg/m³" and put it on the braille display, and the scheduler wrote it into the
  measurement series as though it were a reading. In one account 32 of 190
  stored PM2.5 values were dropouts - 17% of the series, all from a single unit,
  starting on one particular day while an identical purifier beside it never did
  it once.
- Display and history are now treated differently, on purpose. The line keeps
  the last good reading, because a row that vanishes and returns every hour is
  worse to navigate than a slightly old number. The history records nothing at
  all, because repeating the previous reading would invent a measurement that
  was never taken.
- Filtered at both sources - the bypassV2 response and the device list - rather
  than at the point of display, so nothing implausible enters the device object
  in the first place. The air quality level is filtered on the same grounds; a
  bad level has not been observed, but it would have been shown as a bare
  number.
- Existing dropouts already in a measurement file stay until they are removed
  by hand. They are only ever whole rows of their own, so nothing else is lost
  with them.

### One line, announced once

- Restoring the focus after a tree rebuild called `SelectItem` **and**
  `SetFocusedItem`. In a single-selection tree the first already moves the
  focus, so the second fired a second focus event for the same line: after a
  cooking programme ended, NVDA announced "Programme state: standby" twice,
  eight milliseconds apart. The focus is now only nudged if selecting did not
  already take it there, and a target that is already focused is left alone.
- Seen in the same log: the first of the two announcements also reported the
  wrong tree depth ("level 4" for a line at level 3), which corrected itself as
  soon as the line was arrowed to again. That part is NVDA's own view of the
  tree going briefly stale across the rebuild and is not fixed by this - halving
  the announcements only reduces how often it can be heard.

### The focus no longer moves to a different line by itself

- When a cooking programme ended, the remaining time and the temperature
  dropped out of the device tree, and the focus was restored by position - onto
  whatever line had moved into that slot. Someone standing on "Temperature:
  192 °C" was silently put on "Cannot be operated yet" and heard that read out,
  at the very moment the news was that the food was done.
- Tree lines that come and go now carry a stable key and are found again by it.
  The same applies to the favorites entry, which changes between "add" and
  "remove".

### Finding out what an unknown device answers

- The connection test can now ask a device that is shown but not operated which
  read-only status calls it responds to, and writes every answer into the log -
  the failures above all, because VeSync distinguishes an unknown method from
  wrong parameters, and the second one means the call was right.
- Strictly retrieving calls are sent, never anything that could set or start.
  These are appliances that heat.
- Only with debug logging on. Somebody who set that level and pressed "Test" is
  diagnosing; everyone else would pay for a handful of pointless round trips on
  every test.

### A VeSync account of unknown devices reported "no devices"

- A tester with a Cosori air fryer signed in successfully and was told the
  account was empty. It was not: the add-on recognises air purifiers and tower
  fans, dropped the one device it did not know, and reported the remainder -
  nothing. That is what a wrong password looks like as well, and it sent the
  report off in entirely the wrong direction.
- The connection test now says how many devices the account holds, how many of
  them are supported, and the model designation of the ones that are not.
- A device that is not supported is logged at INFO instead of debug, with its
  model designation. That string is what a report has to contain, and it is now
  in an ordinary log rather than only in one recorded at debug level.
- The complete record of an unsupported device goes into the debug log. It
  usually already holds the name, the switching state and the announced
  capabilities - enough to judge what could be reached before a single line is
  written for that model.

### Other

- The connection test announced its result twice: once as the status line and
  once again as a separate message with the same content. The second one is
  gone, for Meross, VeSync and Cozytouch alike.
- The manual said the add-on logs neither passwords nor tokens nor the email
  address. True of the add-on - but NVDA writes the whole configuration into
  the log at startup, and the email address stands there in plain text. Anyone
  who read that sentence and sent a log believed otherwise.

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
