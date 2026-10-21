# Checks

Seventeen scripts that verify invariants which are easy to break and expensive
to notice late. They need no NVDA and no cloud account: most of them read the
shipped source with `ast` and check properties of it, while `fryertest.py` and
`sensortest.py` import a module with the two NVDA-only modules stubbed. Either way they
run anywhere Python does.

```bash
python tools/checks/historytest.py
python tools/checks/layertest.py
python tools/checks/refreshtest.py
python tools/checks/tokentest.py
python tools/checks/fryertest.py
python tools/checks/sensortest.py
python tools/checks/configtest.py
python tools/checks/lighttest.py
python tools/checks/treetest.py
python tools/checks/credentialstest.py
python tools/checks/scheduletest.py
python tools/checks/healthtest.py
python tools/checks/hubtest.py
python tools/checks/wateralarmtest.py
python tools/checks/libstest.py
python tools/checks/petlibrotest.py
python tools/checks/logintest.py
```

Each prints one line per check and ends with `GESAMT: ALLE TESTS OK` or a
list of failures, and exits non-zero on failure. They look for the add-on
next to themselves; `SHC=<path to globalPlugins/SmartHomeControl>` overrides
that.

`polib` is required (`pip install polib`).

## What each one covers

**historytest.py** — the history and its export. Among other things: that no
quantity is half-added (thresholds, units, decimals, labels and order must
carry the same keys), that stored details are never translated text, that
readings keep the device's unique id rather than the hub uuid, that the CSV
writes only columns it can fill, uses an ISO timestamp with the "T" and the
region's separators, and that no German string has crept back into the code.
Also that what a device does by itself is written as "automatic": the
boost of 2026-10-02, switched on here, set to 65 minutes and booked as
"external" when it ran out, is replayed through the real detection, and
so is the Netatmo week schedule moving to its night zone at 22:00. Since
2026-10-18 also: that a boost running across an NVDA restart still ends as
"automatic" (start and duration come back from the history), that a change
the appliance shows only later is confirmed as the user's own rather than
written as "external", and that a setting changed here - a VeSync fan level,
a Cozytouch temperature - is not written a second time as "external" by a
poll that comes after the thirty-second window.

**layertest.py** — the favorites layer: which keys end it, that a digit only
announces and the same digit switches, and that devices which cannot be
switched say so.

**configtest.py** - the settings. That every key the code reads or writes is
declared in `CONFSPEC`, because an undeclared one comes back as the text
standing in `nvda.ini` rather than as a bool - which took the whole settings
dialog down once, a day after the build, since the value only turns into text
after the settings have been saved. Also that every notification setting is
declared boolean, that every checkbox in the notifications tab has an
attribute the plugin actually sets, and that the coercion underneath it reads
the text "False" as False rather than as a non-empty string.

**lighttest.py** - what the add-on believes about a lamp's white/colour mode.
A mode set from here is cached and consulted before the lamp's own capacity
value, because the lamp lags behind a change. The cache used never to expire,
so a mode remembered from an earlier action outranked the device for good and
a white set at the lamp or in the Meross app never became visible - an MSL450
switched on in white mode was announced as being in colour mode. These checks
hold the cache to its window and assert that the lamp is still consulted at
all.

**treetest.py** - the device tree, against three faults that stood under
"known" for a whole pre-release. That an entry keeps an identity a rebuild
cannot change (`unique_id` plus channel and action, not the label - it carries
the values and the counts), so the selection can be put back where it stood
instead of on the first entry, falling back to the device when the line itself
is gone. That the structure signature reacts to what the tree is built from -
a device added, removed, renamed, gone offline, another filter - and to
nothing else, because that is what decides whether a refresh rebuilds at all:
a rebuild reports a selection twice and NVDA reads the entry two and three
times. And that a platform switched off really loses its devices, in the
refresh as well as when the settings are saved. Also that a Netatmo device
keeps its favorites row when it is expanded and when a thermostat is
updated - it lost it both ways, and only Ctrl+B was left.

**credentialstest.py** - the credential store. NVDA writes its complete
configuration into the log at startup, so anything in `nvda.ini` travels with
every log a user sends in; the credentials therefore live in a file of their
own. The dangerous part is the one-time move: clear the configuration before
the file is really written and the user has to type everything again. These
checks cover the move, the second start, saving, a broken file - and the case
that matters most, a failing write, where the values have to stay in the
configuration. Since then two more ways to lose a value that was already
stored: two threads writing at once, which used to fight over one fixed
temporary file and drop the newer value with nothing but a log line (a
rotated token is exactly what goes missing that way), and a value this
machine cannot decrypt, which came back as "" and was written over a
ciphertext that is still good on the machine it was made on. Also that the
section an early version wrote under the add-on's first name,
`[merossSmartControl]`, is removed from nvda.ini and the configuration
written out at once: it held the Netatmo password in plain text, and NVDA
logs its configuration at every start.

**wateralarmtest.py** - the leak sensors. Every sensor on a Meross hub
carries the hub's uuid, so two leak sensors on one hub (MS400, MS405, in
any combination) shared one entry in the alarm detection and overwrote
each other on every pass. Both then counted as
changed: the wet one alarmed again and the dry one beside it was announced
as dry "again" - every fifteen seconds with the device menu open, because
the check runs after each poll of any platform and reads cached state. These checks hold each sensor
to its own identity, and hold the alarm to being said once.

**libstest.py** - the bundled libraries. The add-on ships its
dependencies inside the package, so nothing here is ever updated by pip on
a user's machine: what is in `lib/` at build time runs until the next
release. The checks verify every file against the SHA256 in its wheel's
RECORD (which catches both a library edited after unpacking and the far
likelier accident, a line-ending conversion - the reason `.gitattributes`
marks `lib/**` as `-text`), that the bundled versions are the ones pinned
in `requirements-bundle.txt` with no folder of an earlier version left
behind, and that both architectures carry every compiled package. It does
not ask the internet about CVEs - it has to pass offline. `build_addon.py
audit` does, against the OSV database, and the build workflow runs it; this
check asserts that the audit reads the same pins, that the workflow still
runs it, and that the add-on logs which copy of requests and urllib3 ran
(NVDA's own come first on sys.path).

**scheduletest.py** - the time program of the hot water heat pump, the
only place where this add-on writes a schedule into a heating appliance. The
cloud confirms an execution, which the air fryer taught is not the same as the
appliance having done it, so the program is read back and compared. The trap
is the protection window: `apply_capabilities` skips fields that were just
changed locally, so a read-back through it would compare the request with
itself and always agree. These checks cover the parser, the validation
(reversed, overlapping and out-of-range windows), a refusing cloud, a cloud
that confirms while the appliance keeps its old program, an appliance that
stores something else - and that the protection window does not blind the
read-back. Also when a single command counts as taken, refused or still on
its way: a boost reported as "could not be toggled" was running a little
later, because one look 1.5 seconds after the execution decided everything.
The value is now read back several times, a dropped network while following
the execution or a late answer to the write leaves the decision to the
value, and a write is never sent a second time after a read timeout.

**healthtest.py** - whether an unreachable platform is noticed, and whether
the add-on stays quiet where nothing is wrong. The Netatmo cloud answers with
two services, the weather station and the heating; on 2026-09-09 the weather
half answered 503 while the heating was fine, and nothing was said, because a
poll counts as a success as long as one half comes through. Later both halves
failed, no device came back at all - and the platform was still reported as
merely incomplete, because `get_devices()` raises nothing of its own. The same
log shows the other direction: one Meross device out of twenty-two timed out on
roughly every second poll and produced a fault and a recovery three times in
nine minutes. These checks cover the record behind it, the texts it produces,
and the wiring, because a record nobody feeds reports "all fine" for ever. Held
down as well: a device skipped for its hourly budget is not a device that
failed, an account without a thermostat is never told its heating is
unreachable, and a single device that misses a poll is written into the device
list rather than announced. And that a network or server fault is logged as
a WARNING, not an ERROR - an ERROR plays NVDA's sound for logged errors, and
a morning of Netatmo 503s did that eight times.

**hubtest.py** - the sensors on a Meross hub, which all carry the HUB's
uuid; only `unique_id` appends the subdevice id. Three things hung on that: a
rescue loop keyed by `uuid` dropped every sensor as soon as its hub was in the
new list, a discovery while the hub was silent replaced the list without them,
and they only came back on the next NVDA start, because a rediscovery runs
only when no Meross device is left at all. In the favourites they then read
"(not available)". These checks cover the carry-over - which must not
resurrect a device that really left the account, nor anything from another
account - and the three places it is wired into.

**refreshtest.py** — the polling scheduler: that two refreshes of the same
platform coalesce instead of queueing, and that the cache lifetime stays
longer than the poll interval plus the poll itself.

**fryertest.py** — the Cosori air fryer, replaying status records from the
log of a complete programme: that the remaining time is read as seconds, that
the measured temperature is dropped in standby where it goes stale, that the
programme is taken from `stepArray` and not from `cookMode`, that the four
known cooking states are translated while an unknown one passes through, that
the displayed temperature is damped enough to stop the line chattering while
the appliance holds temperature, and that every tree line carries a key so
the focus survives a line appearing or disappearing. Since the Roast log of
2026-08-24 also: that a temperature the appliance accepts and does not carry
out is noticed and said once, that a cached reply is not mistaken for a
refusal, that nothing ever reads "off" while the appliance is cooking, and
that the end of a programme stays speakable - including the ordering the
announcement depends on. It also sends every VeSync command through a dead
network: startCook and endCook go out once, a command that sets a value may
go out twice, and the list of commands is read from the source so a new one
is covered without being added by hand.

**sensortest.py** — sensor dropouts. A Levoit purifier reports -1 for its
particulate sensor when it has no reading, and one unit did so on roughly
every second hourly poll. Checks that such a value never reaches the
interface (the last good reading stays on the line instead) and never
reaches the history (which records nothing rather than repeating the
previous value), from both the bypassV2 response and the device list.

**tokentest.py** — the Netatmo token handling: that a rotated refresh token
is persisted, and that the diagnostics read the live value rather than a
copy taken at sign-in. Also the local callback of the sign-in: a call with a
wrong state is turned away instead of ending the sign-in.

**petlibrotest.py** - the Petlibro feeder and water fountain, the first
devices here whose commands reach an animal. Replays the status of a real
Air Smart Feeder (PLAF108) and Dockstream 2 Cordless (PLWF116) and checks
what is read from it - portions and millilitres whatever unit the app shows,
the model number in the brackets, the desiccant overdue by 27 days, the water
modes a fountain with a presence sensor offers. Then "Feed now" against a
cloud stand-in: the command goes out exactly once whatever comes back - a
lost answer, "device did not answer" (2020), "already feeding" (2028) - and
a feeding counts as done only when the feeder writes a NEW record, not when
the cloud says yes. Also held down: commands are never retried after a
network error while reads are, logging out never voids the stored token, a
favourites digit never switches (and so never feeds), and the reminder resets
ask first with Cancel preselected. A "Feed now" counts as on its way only once
it is really sent - not while another action still runs - and only a manual
feeding confirms it, not a planned meal at the same minute. The flow duration
of the sensor modes (45 to 90 seconds, firmware 0.3.25 or later) goes out as
the sensor mode's own request without the radar range, and is offered only
while a sensor mode runs, because sending it in any other mode would switch
the fountain to the sensor. And that a change confirmed at once is not
taken back when the cloud still hands back the value from before - for six
minutes on 2026-10-03, announced twice as a change made elsewhere - while a
third value, somebody else's change, is taken over at once.

**logintest.py** - signing in to the platforms, where three faults sat in one
flow. The platforms signed in one after another - thirty seconds measured on
2026-10-01, twenty of them Meross, with the device menu closed all along. A
sign-in that failed on the network - a laptop starting NVDA before the
Wi-Fi - was never tried again, and the platform stayed away until the next
restart. And every save of the settings signed in all platforms again and
replaced the device list as a whole: a platform whose new sign-in ran into a
503 lost its devices while its old session went on working. These checks
cover the parallel sign-in, a partial failure that keeps the devices, the
retry after a network fault and its absence after a refused password, which
platforms a save signs in, the status command (a summary, and a list when
pressed twice) and the wiring of all of it into the plugin. Also that a
Netatmo login where the cloud did not answer counts as failed rather than
as an account without devices: with a valid token the login itself needs
no network, so on 2026-10-03 a start without Wi-Fi left Netatmo out of the
retry the other four platforms got.

The device menu used to sign in by itself after a save of the settings -
one copy of each login per platform, without the retry, and with Netatmo
taken without asking the cloud. It saves through the plugin now. The checks
cover that no dialog module builds a session or signs in on its own, that
the open menu rebuilds its tree after every login (before the announcement,
and not while an action still waits for the cloud or a child dialog is
open), that the replaced VeSync, Cozytouch and Petlibro sessions are closed
only once their devices are replaced, that the menu makes the offer to enter
a refused password again, and that the refusal is spoken once.

## Why these and not a test framework

They assert the things that actually went wrong in this add-on, and they do
it against the source that ships, without needing a running NVDA, an account
or a device. A regression here is visible in seconds. Adding `pytest` and
mocks for the cloud APIs would cover more in theory and less in practice.
