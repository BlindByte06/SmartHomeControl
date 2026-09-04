# Checks

Ten scripts that verify invariants which are easy to break and expensive
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
refresh as well as when the settings are saved.

**credentialstest.py** - the credential store. NVDA writes its complete
configuration into the log at startup, so anything in `nvda.ini` travels with
every log a user sends in; the credentials therefore live in a file of their
own. The dangerous part is the one-time move: clear the configuration before
the file is really written and the user has to type everything again. These
checks cover the move, the second start, saving, a broken file - and the case
that matters most, a failing write, where the values have to stay in the
configuration.

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
announcement depends on.

**sensortest.py** — sensor dropouts. A Levoit purifier reports -1 for its
particulate sensor when it has no reading, and one unit did so on roughly
every second hourly poll. Checks that such a value never reaches the
interface (the last good reading stays on the line instead) and never
reaches the history (which records nothing rather than repeating the
previous value), from both the bypassV2 response and the device list.

**tokentest.py** — the Netatmo token handling: that a rotated refresh token
is persisted, and that the diagnostics read the live value rather than a
copy taken at sign-in.

## Why these and not a test framework

They assert the things that actually went wrong in this add-on, and they do
it against the source that ships, without needing a running NVDA, an account
or a device. A regression here is visible in seconds. Adding `pytest` and
mocks for the cloud APIs would cover more in theory and less in practice.
