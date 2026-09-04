# -*- coding: utf-8 -*-
"""Tests that sensor dropouts never reach the interface or the history.

A Levoit Core 300S reports -1 for its particulate sensor when it has
nothing to say. One unit did that on roughly every second hourly poll for
days while an identical one beside it never did, so this is not a startup
transient that can be waited out.

Without a filter the value was announced as "PM2.5: -1 µg/m³", went onto
the braille display, and was written into the measurement series as if it
were a reading: 32 of 190 stored PM2.5 values were dropouts.

Two behaviours are checked, and they are deliberately different:

  * the DISPLAY keeps the last good reading, because a line that vanishes
    and comes back every hour is worse to navigate than a slightly old
    number;
  * the HISTORY records nothing at all, because repeating the previous
    reading would invent a measurement that was never taken.
"""
import ast
import importlib
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


def load():
    pkg = types.ModuleType('shc')
    pkg.__path__ = [BASE]
    sys.modules['shc'] = pkg

    class _Log:
        def __getattr__(self, name):
            return lambda *a, **k: None

    log_mod = types.ModuleType('logHandler')
    log_mod.log = _Log()
    sys.modules['logHandler'] = log_mod
    addon_mod = types.ModuleType('addonHandler')
    addon_mod.initTranslation = lambda: None
    sys.modules['addonHandler'] = addon_mod
    return importlib.import_module('shc.vesync_devices')


def make_purifier(vd):
    config, _matched = vd.resolve_device_config('Core300S', vd.VESYNC_PURIFIER_TYPES)
    if config is None:
        raise SystemExit('Core300S not found in VESYNC_PURIFIER_TYPES')
    return vd.VeSyncPurifier(
        {'deviceName': 'Kitchen', 'deviceType': 'Core300S', 'cid': 'c',
         'uuid': 'u', 'deviceStatus': 'on', 'connectionStatus': 'online'},
        object(), config)


def feed_status(dev, **fields):
    dev.apply_status_response({'code': 0, 'result': {'code': 0, 'result': fields}})


# ---------------------------------------------------------------- tests ----
def test_plausible_reading(vd):
    print('== what counts as a reading ==')
    f = vd._plausible_reading
    for value in (0, 1, 3, 250, 1.5):
        check(f'{value!r} is kept', f(value) == value)
    for value in (-1, -0.5, -99):
        check(f'{value!r} is rejected', f(value) is None)
    for value in (None, '1', '', {}, []):
        check(f'{value!r} is rejected', f(value) is None)
    # isinstance(True, int) is true in Python; a flag in a numeric field
    # would otherwise be stored as 1.
    check('True is rejected, not stored as 1', f(True) is None)
    check('levels below 1 are rejected when a minimum is given',
          f(0, minimum=1) is None)
    check('level 1 passes that minimum', f(1, minimum=1) == 1)


def test_dropout_does_not_reach_the_display(vd):
    print('== a dropout leaves the displayed value standing ==')
    dev = make_purifier(vd)
    feed_status(dev, air_quality=1, air_quality_value=1)
    check('a real reading is taken', dev.air_quality_value == 1)
    check('and counts as current', dev.air_quality_value_fresh)

    feed_status(dev, air_quality=1, air_quality_value=-1)
    check('the dropout is not displayed', dev.air_quality_value == 1,
          str(dev.air_quality_value))
    check('and is not passed off as current',
          not dev.air_quality_value_fresh)

    feed_status(dev, air_quality=1, air_quality_value=3)
    check('the next real reading gets through', dev.air_quality_value == 3)
    check('and counts as current again', dev.air_quality_value_fresh)


def test_dropout_does_not_reach_the_history(vd):
    print('== a dropout is not recorded ==')
    dev = make_purifier(vd)
    feed_status(dev, air_quality_value=1)
    check('a real reading is offered for recording', dev.get_pm25() == 1)
    feed_status(dev, air_quality_value=-1)
    check('a dropout offers nothing', dev.get_pm25() is None,
          str(dev.get_pm25()))
    check('and the displayed value is still there', dev.air_quality_value == 1)


def test_devicelist_source_filters_too(vd):
    print('== the device list carries the same -1 ==')
    dev = make_purifier(vd)
    # The method takes the whole device record and reads its "extension".
    ext = lambda **f: {'extension': f}
    dev.apply_devicelist_extension(ext(airQualityLevel=1, airQuality=2))
    check('a real reading is taken', dev.air_quality_value == 2)
    dev.apply_devicelist_extension(ext(airQualityLevel=1, airQuality=-1))
    check('the dropout does not overwrite it', dev.air_quality_value == 2,
          str(dev.air_quality_value))
    check('and is not recorded', dev.get_pm25() is None)


def test_the_logged_sequence(vd):
    print('== replay of the sequence from the log ==')
    # Every second hourly poll a dropout, as one kitchen unit actually
    # behaved between 18 and 22 August.
    dev = make_purifier(vd)
    recorded = []
    for value in (1, -1, 1, -1, -1, 1, -1, 1, 1, -1):
        feed_status(dev, air_quality=1, air_quality_value=value)
        recorded.append(dev.get_pm25())
    check('nothing negative was ever offered for recording',
          all(v is None or v >= 0 for v in recorded), str(recorded))
    check('the five dropouts recorded nothing',
          recorded.count(None) == 5, str(recorded.count(None)))
    check('the five readings were recorded',
          [v for v in recorded if v is not None] == [1, 1, 1, 1, 1])
    check('the display never showed a negative number',
          dev.air_quality_value == 1, str(dev.air_quality_value))


def test_history_reads_the_getter():
    print('== the history path uses the getter, not the attribute ==')
    path = os.path.join(os.path.dirname(BASE.rstrip(os.sep)),
                        'SmartHomeControl', 'scheduler.py')
    src = io.open(path, encoding='utf-8').read()
    tree = ast.parse(src)
    entry = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Tuple) and len(node.elts) == 2
                and isinstance(node.elts[0], ast.Constant)
                and node.elts[0].value == 'pm25'):
            entry = node.elts[1]
            break
    if entry is None:
        check('the pm25 source entry was found', False)
        return
    names = [e.value for e in entry.elts if isinstance(e, ast.Constant)]
    check('get_pm25 is the source', 'get_pm25' in names, str(names))
    # _read_sensor takes the first name that yields anything, so a plain
    # attribute listed as a fallback would undo the whole filter.
    check('the raw attribute is not a fallback',
          'air_quality_value' not in names, str(names))


def main():
    vd = load()
    test_plausible_reading(vd)
    test_dropout_does_not_reach_the_display(vd)
    test_dropout_does_not_reach_the_history(vd)
    test_devicelist_source_filters_too(vd)
    test_the_logged_sequence(vd)
    test_history_reads_the_getter()
    print()
    if FAILED:
        print(f'FEHLGESCHLAGEN: {len(FAILED)} -> {FAILED}')
        return 1
    print('GESAMT: ALLE TESTS OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
