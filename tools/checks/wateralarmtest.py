# -*- coding: utf-8 -*-
"""Tests that a leak sensor is told apart from the one beside it.

Every sensor on a Meross hub carries the hub's uuid, leak sensors (MS400,
MS405) included. The alarm detection kept its previous state under that
uuid, so two of them on one hub - in any combination - shared a single
entry and overwrote each other on every pass. Both then counted as changed: the wet one raised its
alarm again, the dry one was announced as dry "again", and both went into
the history. Not once, but on every poll pass - every fifteen seconds
with the device menu open and every thirty to forty-five without it,
because the check runs after each poll of any platform and reads cached
state rather than the cloud.

For a leak sensor that is the worst shape the fault could take: an
all-clear that is wrong, and an alarm that repeats until nobody hears it.

The wrapper offers ``unique_id`` (hub uuid plus subdevice id) for exactly
this, and ``history._device_key`` is where that decision already lives -
the same fault had merged the measurement series of two rooms before.
"""
import ast
import io
import os
import sys
import types

BASE = os.environ.get(
    'SHC',
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'globalPlugins', 'SmartHomeControl'))

FAILED = []


def check(name, cond, detail=''):
    print(f"  {'OK  ' if cond else 'FEHL'}   {name}" + (f'  ({detail})' if detail else ''))
    if not cond:
        FAILED.append(name)


# ---------------------------------------------------------------- setup ----
SPOKEN = []
LOGGED = []


def load():
    """Imports change_detection with the NVDA-only modules stubbed."""
    pkg = types.ModuleType('shc')
    pkg.__path__ = [BASE]
    sys.modules['shc'] = pkg

    class _Log:
        def __getattr__(self, name):
            return lambda *a, **k: None

    def stub(name, **attrs):
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        sys.modules[name] = mod
        return mod

    stub('logHandler', log=_Log())
    stub('addonHandler', initTranslation=lambda: None)
    stub('ui', message=SPOKEN.append)
    # CallAfter runs straight away here: what matters is WHETHER something is
    # announced, not which thread says it.
    stub('wx', CallAfter=lambda fn, *a, **k: fn(*a, **k))
    stub('tones', beep=lambda *a, **k: None)
    stub('config', conf={})
    stub('globalVars', appArgs=types.SimpleNamespace(configPath=None))

    import importlib
    cd = importlib.import_module('shc.change_detection')

    # The history is replaced by a recorder: this is about which events are
    # raised, not about the file they end up in.
    hist = importlib.import_module('shc.history')

    class _Recorder:
        def log_action(self, device, action, details='', source=None):
            LOGGED.append((device.name, action))

    hist.get_history = lambda: _Recorder()
    return cd


class HubSensor:
    """A leak sensor on a hub: the hub's uuid, its own subdevice id.

    Mirrors ``MerossDevice``: ``uuid`` is the hub's and therefore shared,
    ``unique_id`` appends the subdevice id and is the sensor's own.
    """

    is_water_sensor = True

    def __init__(self, name, subdevice_id, hub_uuid='HUB-1'):
        self.name = name
        self.uuid = hub_uuid
        self._subdevice_id = subdevice_id
        self.wet = False

    @property
    def unique_id(self):
        return f'{self.uuid}_{self._subdevice_id}'

    def is_water_detected(self):
        return self.wet


def make_host(cd):
    class Host(cd._ChangeDetectionMixin):
        def __init__(self):
            self._previous_water_states = {}
            self.announce_external_changes = True
            self.notify_meross_water = True

    return Host()


def sweep(host, devices):
    """One poll pass. Returns what was said and what was recorded."""
    SPOKEN.clear()
    LOGGED.clear()
    host._detect_water_alarms(devices)
    return list(SPOKEN), list(LOGGED)


# ---------------------------------------------------------------- tests ----
def test_two_sensors_on_one_hub(cd):
    print('== two leak sensors on one hub ==')
    host = make_host(cd)
    cellar = HubSensor('Cellar', 'SUB-A')
    bath = HubSensor('Bathroom', 'SUB-B')
    devices = [cellar, bath]

    said, logged = sweep(host, devices)
    check('the first pass only remembers', not said and not logged)
    check('both sensors get an entry of their own',
          len(host._previous_water_states) == 2,
          str(sorted(host._previous_water_states)))

    said, logged = sweep(host, devices)
    check('a second quiet pass stays quiet', not said and not logged)

    cellar.wet = True
    said, logged = sweep(host, devices)
    check('the wet one raises the alarm',
          len(said) == 1 and 'Cellar' in said[0], str(said))
    check('the dry one beside it says nothing',
          not any('Bathroom' in m for m in said), str(said))
    check('and exactly one event is recorded',
          logged == [('Cellar', 'water_detected')], str(logged))

    for pass_number in (4, 5):
        said, logged = sweep(host, devices)
        check(f'pass {pass_number} repeats neither alarm nor all-clear',
              not said and not logged, str(said))

    cellar.wet = False
    said, logged = sweep(host, devices)
    check('the all-clear names the sensor that was wet',
          len(said) == 1 and 'Cellar' in said[0], str(said))
    check('and is recorded once',
          logged == [('Cellar', 'water_cleared')], str(logged))

    bath.wet = True
    said, logged = sweep(host, devices)
    check('the second sensor alarms on its own account',
          len(said) == 1 and 'Bathroom' in said[0], str(said))


def test_single_sensor_still_works(cd):
    print('== a single sensor is unaffected ==')
    host = make_host(cd)
    only = HubSensor('Kitchen', 'SUB-A')
    sweep(host, [only])
    only.wet = True
    said, logged = sweep(host, [only])
    check('it alarms', len(said) == 1 and 'Kitchen' in said[0], str(said))
    said, logged = sweep(host, [only])
    check('and does not repeat itself', not said, str(said))


def test_non_sensors_are_skipped(cd):
    print('== anything that is not a leak sensor is passed over ==')
    host = make_host(cd)

    class Plug:
        is_water_sensor = False
        name = 'Plug'
        uuid = 'HUB-1'

    said, _logged = sweep(host, [Plug()])
    check('a plug produces nothing', not said)
    check('and leaves no state behind', not host._previous_water_states)


def test_the_source_says_which_identity_it_uses():
    print('== the identity is taken from the shared helper ==')
    path = os.path.join(BASE, 'change_detection.py')
    tree = ast.parse(io.open(path, encoding='utf-8').read())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)
               and n.name == '_detect_water_alarms'), None)
    check('_detect_water_alarms exists', fn is not None)
    if fn is None:
        return
    body = '\n'.join(io.open(path, encoding='utf-8').read().splitlines()
                     [fn.lineno - 1:fn.end_lineno])
    check('it keys the state with _device_key', '_device_key(device)' in body)
    check('and not with the hub uuid any more',
          "getattr(device, 'uuid'" not in body)

    # _device_key is the one place that knows the rule - a second copy here
    # would drift away from it without anything noticing.
    hist = io.open(os.path.join(BASE, 'history.py'), encoding='utf-8').read()
    check('_device_key prefers unique_id',
          "getattr(device, 'unique_id', None) or device.uuid" in hist)


def test_refresh_keeps_sibling_sensors():
    print('== the refresh does not lose a sibling sensor ==')
    src = io.open(os.path.join(BASE, '__init__.py'), encoding='utf-8').read()
    check('the device merge no longer compares uuids',
          'known_uuids = {d.uuid for d in new_list}' not in src)
    check('it compares device keys instead',
          '_device_key(d) for d in new_list' in src)


def main():
    cd = load()
    test_two_sensors_on_one_hub(cd)
    test_single_sensor_still_works(cd)
    test_non_sensors_are_skipped(cd)
    test_the_source_says_which_identity_it_uses()
    test_refresh_keeps_sibling_sensors()
    print()
    if FAILED:
        print(f'FEHLGESCHLAGEN: {len(FAILED)} -> {FAILED}')
        return 1
    print('GESAMT: ALLE TESTS OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
