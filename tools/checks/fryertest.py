# -*- coding: utf-8 -*-
"""Tests what a Cosori air fryer reports and how it is presented.

The status records below are verbatim from the log of a complete Steak
programme on a CAF-P583S-KEU. They are the evidence for three things the
code would otherwise only assume: that totalTimeRemaining counts seconds,
that currentTemp is a measurement which goes stale in standby, and that the
programme is in stepArray rather than in cookMode.

Unlike the sibling scripts this one imports the wrapper instead of lifting
functions out with ast: the behaviour under test is spread over a class with
state that carries from one status to the next, and replaying the records
through the real object is both shorter and closer to what NVDA runs. The
two NVDA-only modules are stubbed, so it still needs neither NVDA nor an
account.
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


# ------------------------------------------------------------- fixtures ----
def _step(last, temp):
    return {'cookSetTime': 480, 'cookTemp': 205, 'mode': 'Steak',
            'cookLastTime': last, 'shakeTime': 0, 'cookEndTime': 0,
            'recipeName': 'Steak', 'recipeId': 1, 'recipeType': 3}


def _status(cook_status, remaining, temp, with_step=True):
    """One getAirfryerStatus result as the appliance sends it."""
    return {
        'stepArray': [_step(remaining, temp)] if with_step else [],
        'cookMode': 'normal', 'tempUnit': 'c', 'stepIndex': 0,
        'cookStatus': cook_status, 'preheatSetTime': 0, 'preheatLastTime': 0,
        'preheatEndTime': 0, 'preheatTemp': 0, 'startTime': 1787318291,
        'totalTimeRemaining': remaining, 'currentTemp': temp,
        'shakeStatus': 0, 'linkageStatus': 0,
    }


# The temperatures are the real series: climb, overshoot past the set value
# of 205, then the oscillation while the appliance holds temperature.
CLIMB = [61, 68, 88, 98, 121, 132, 154, 163, 182, 191, 206, 213, 217]
PLATEAU = [213, 201, 190, 189, 189, 191, 195, 196, 192, 195, 194, 195, 193]


def load():
    """Imports the wrapper with the NVDA modules stubbed out."""
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


def make_device(vd):
    return vd.VeSyncAirFryer(
        {'deviceName': 'fryer', 'deviceType': 'CAF-P583S-KEU', 'cid': 'c',
         'uuid': 'u', 'deviceStatus': 'on', 'connectionStatus': 'online'},
        object(), {'alias': 'Cosori Dual Blaze'})


def feed(dev, result):
    dev.apply_status_response({'code': 0, 'result': {'code': 0, 'result': result}})


# ---------------------------------------------------------------- tests ----
def test_seconds(vd):
    print('== totalTimeRemaining counts seconds ==')
    dev = make_device(vd)
    feed(dev, _status('cooking', 432, 53))
    text = dev.remaining_time_display()
    check('432 -> 7 minutes 12 seconds', '7' in text and '12' in text, text)
    feed(dev, _status('cooking', 480, 60))
    check('480 -> 8 minutes, no stray seconds',
          '8' in dev.remaining_time_display()
          and '0' not in dev.remaining_time_display().replace('8', ''),
          dev.remaining_time_display())
    feed(dev, _status('cooking', 42, 195))
    check('42 -> under a minute, no minutes shown',
          '42' in dev.remaining_time_display()
          and 'min' not in dev.remaining_time_display().lower(),
          dev.remaining_time_display())
    feed(dev, _status('standby', 0, 189, with_step=False))
    check('0 -> nothing at all', dev.remaining_time_display() == '')


def test_temperature_is_measured(vd):
    print('== currentTemp is a measurement, and goes stale in standby ==')
    dev = make_device(vd)
    feed(dev, _status('cooking', 300, 217))
    check('217 above the set value of 205 is shown, not clamped',
          '217' in dev.temperature_display(), dev.temperature_display())
    check('the set temperature is shown separately',
          '205' in dev.target_temperature_display(),
          dev.target_temperature_display())
    # 172 degrees held for 35 minutes on an idle appliance: the value stops
    # being a measurement, which is where the 181 on a cold fryer came from.
    feed(dev, _status('standby', 0, 172, with_step=False))
    check('standby drops the stale temperature', dev.temperature_display() == '')
    check('standby drops the set temperature too',
          dev.target_temperature_display() == '')


def test_programme_source(vd):
    print('== the programme comes from stepArray, not from cookMode ==')
    dev = make_device(vd)
    feed(dev, _status('cooking', 300, 190))
    check("cookMode is 'normal' on the wire", dev.cook_mode == 'normal')
    check('the displayed programme is not that',
          'normal' not in dev.programme_display().lower(),
          dev.programme_display())
    check('the displayed programme is the step mode',
          dev.programme_display() != '' and dev.programme == 'Steak',
          dev.programme_display())
    feed(dev, _status('standby', 0, 172, with_step=False))
    check('standby clears it instead of keeping the last one',
          dev.programme_display() == '')


# Every programme name the appliance actually sent, read verbatim off a
# CAF-P583S that was asked to load each of its twelve functions in turn.
# The spellings are not the obvious ones: no space in 'AirFry', 'Veggies'
# rather than 'Vegetables', and fries arrive as 'French fries' - the one
# that a guessed key missed, so it reached the interface as raw English
# while the other ten were translated.
DEVICE_MODES = (
    ('Steak', 1), ('Chicken', 2), ('Seafood', 3), ('Frozen', 5),
    ('French fries', 6), ('Bake', 9), ('Roast', 13), ('AirFry', 14),
    ('Veggies', 15), ('Reheat', 16), ('Broil', 17),
    # Not yet seen on a device; the manual's wording.
    ('Keep warm', None),
)


def test_programme_names_cover_the_device(vd):
    print('== every programme the appliance sends has a name ==')
    table = importlib.import_module('shc.constants').VESYNC_FRYER_PROGRAMME_NAMES
    dev = make_device(vd)
    for mode, _recipe_id in DEVICE_MODES:
        # Membership, not the displayed string: without a catalogue loaded
        # `_` is the identity, so "Chicken" displaying as "Chicken" would
        # look identical whether it was translated or passed through raw.
        key = mode.replace(' ', '').replace('_', '').lower()
        check(f'{mode!r} is known', key in table, key)
    dev.programme = 'Sous vide surprise'
    check('an unknown programme is passed through unchanged',
          dev.programme_display() == 'Sous vide surprise')


def test_cook_states(vd):
    print('== cooking states are translated, unknown ones pass through ==')
    states = importlib.import_module('shc.constants').VESYNC_FRYER_COOK_STATES
    dev = make_device(vd)
    # Not "the shown text differs from the key": without a catalogue loaded
    # `_` is the identity, so "cooking" legitimately displays as "cooking"
    # and the two are indistinguishable that way. What has to hold is that
    # the state is known to the table at all, and that the display comes
    # from there.
    for state in ('standby', 'ready', 'cooking', 'cookEnd'):
        feed(dev, _status(state, 100, 150))
        shown = dev.cook_status_display()
        check(f'{state} is a known state', state.lower() in states, shown)
        check(f'{state} is displayed from the table',
              shown == states[state.lower()], shown)
    feed(dev, _status('pullOut', 100, 150))
    check('an unseen state is passed through unchanged',
          dev.cook_status_display() == 'pullOut', dev.cook_status_display())
    feed(dev, _status('standby', 0, 150, with_step=False))
    check('standby is idle', not dev.is_running)
    feed(dev, _status('cookEnd', 0, 192))
    check('cookEnd still counts as running, so the state gets announced',
          dev.is_running)


def test_hysteresis(vd):
    print('== the displayed temperature is damped ==')
    dev = make_device(vd)
    feed(dev, _status('ready', 480, 60))

    def spoken(values, status='cooking'):
        """How often the line would change, i.e. be read out.

        Counted against what was already on screen before the series, so
        an unchanged first reading does not show up as an announcement.
        """
        changes = 0
        previous = dev.display_temp
        for value in values:
            feed(dev, _status(status, 300, value))
            if dev.display_temp != previous:
                changes += 1
            previous = dev.display_temp
        return changes

    climb = spoken(CLIMB)
    plateau = spoken(PLATEAU)
    check('the climb still gets through step by step',
          climb >= len(CLIMB) - 3, f'{climb} of {len(CLIMB)}')
    check('holding temperature goes quiet',
          plateau <= 3, f'{plateau} of {len(PLATEAU)}')
    before = dev.display_temp
    feed(dev, _status('cookEnd', 0, before + 1))
    check('a state change gets through regardless of the step size',
          dev.display_temp == before + 1, str(dev.display_temp))


class RecordingAPI:
    """Records every bypassV2 call and answers with success."""

    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    def call_bypass_v2(self, device, method, data=None, fast=False):
        self.calls.append((method, data))
        return {'code': 0, 'result': {'code': 0 if self.ok else -1}}


def test_end_cook(vd):
    print('== stopping a programme ==')
    api = RecordingAPI()
    dev = vd.VeSyncAirFryer(
        {'deviceName': 'fryer', 'deviceType': 'CAF-P583S-KEU', 'cid': 'c',
         'uuid': 'u', 'deviceStatus': 'on', 'connectionStatus': 'online'},
        api, {'alias': 'Cosori Dual Blaze'})

    for state, expected in (('standby', False), ('ready', True),
                            ('cooking', True), ('cookEnd', False),
                            ('cookStop', True), ('pullOut', True)):
        feed(dev, _status(state, 200, 180, with_step=state != 'standby'))
        check(f'{state}: offer to stop = {expected}',
              dev.can_end_cook is expected)

    feed(dev, _status('cooking', 200, 180))
    dev.end_cook()
    check('exactly one call goes out', len(api.calls) == 1, str(api.calls))
    method, data = api.calls[0]
    check('the call is endCook', method == 'endCook', method)
    check('with an empty payload', data == {}, str(data))
    # The appliance is hot; claiming standby before it says so would be the
    # one kind of wrong worth avoiding.
    check('the cooking state is not faked afterwards',
          dev.cook_status == 'cooking', str(dev.cook_status))

    failing = RecordingAPI(ok=False)
    dev2 = vd.VeSyncAirFryer(
        {'deviceName': 'fryer', 'deviceType': 'CAF-P583S-KEU', 'cid': 'c',
         'uuid': 'u', 'deviceStatus': 'on', 'connectionStatus': 'online'},
        failing, {'alias': 'a'})
    feed(dev2, _status('cooking', 200, 180))
    try:
        dev2.end_cook()
    except RuntimeError:
        check('a refused call raises instead of reporting success', True)
    else:
        check('a refused call raises instead of reporting success', False)

    # Only the two commands that are meant to exist. preheatCook and
    # setTimeOrTemp are documented elsewhere but have never been tried
    # against a Dual Blaze, and setSwitch would be a plain on/off on an
    # appliance where that is not what anyone means.
    src = io.open(os.path.join(BASE, 'vesync_devices.py'), encoding='utf-8').read()
    start = src.index('class VeSyncAirFryer')
    body = src[start:]
    end = body.find('\nclass ', 1)
    if end > 0:
        body = body[:end]
    unexpected = [m for m in ('preheatCook', 'startMultiCook', 'setSwitch',
                              'startStepCook') if m in body]
    check('no unconfirmed command is sent from the air fryer',
          not unexpected, str(unexpected))
    check('exactly three commands exist: start, adjust, stop',
          body.count('call_bypass_v2') == 3, str(body.count('call_bypass_v2')))


def _isolated_presets():
    """Points the preset store at a scratch file, empty."""
    import tempfile
    presets = importlib.import_module('shc.fryer_presets')
    handle, path = tempfile.mkstemp(suffix='.json', prefix='fryerpresets-')
    os.close(handle)
    os.unlink(path)
    presets.PRESETS_FILE = path
    presets._instance = None
    return presets, path


def test_learning(vd):
    print('== the appliance teaches its programmes ==')
    presets, path = _isolated_presets()
    try:
        dev = make_device(vd)
        check('nothing is known to begin with', dev.known_programmes() == [])

        # Selecting a programme is enough - state 'ready', not cooking.
        for mode, rid, temp, secs in (('Veggies', 15, 195, 360),
                                      ('Steak', 1, 205, 480),
                                      ('French fries', 6, 195, 1200)):
            step = {'cookSetTime': secs, 'cookTemp': temp, 'mode': mode,
                    'cookLastTime': secs, 'shakeTime': 0, 'cookEndTime': 0,
                    'recipeName': mode, 'recipeId': rid, 'recipeType': 3}
            dev.apply_status_response({'code': 0, 'result': {'code': 0, 'result': {
                'stepArray': [step], 'cookMode': 'normal', 'tempUnit': 'c',
                'cookStatus': 'ready', 'totalTimeRemaining': secs,
                'currentTemp': 60, 'preheatTemp': 0}}})

        known = dev.known_programmes()
        check('all three were learned', len(known) == 3, str(known))
        # Sorted by id, not by name: the name is translated on display, so
        # sorting by it would reorder the list per interface language.
        check('sorted by recipe id', known == ['Steak', 'French fries', 'Veggies'],
              str(known))
        details = dev.programme_details('French fries')
        check('the id was kept', details.get('recipe_id') == 6, str(details))
        check('the set temperature was kept', details.get('cook_temp') == 195)
        check('the duration was kept in seconds',
              details.get('cook_set_time') == 1200)

        # A second store reading the same file sees them: learning survives
        # a restart, which is the whole point.
        presets._instance = None
        again = presets.get_fryer_presets()
        check('written to disk, not just remembered',
              again.count_for(dev.unique_id) == 3,
              str(again.count_for(dev.unique_id)))

        print('  -- a one-off change must not redefine the programme --')
        # The same fields carry a mid-cook adjustment. Taking that over
        # would make "Veggies" mean 180 degrees for ten minutes from then
        # on, including in the two-keystroke start where nobody looks.
        running = {'cookSetTime': 600, 'cookTemp': 180, 'mode': 'Veggies',
                   'cookLastTime': 400, 'shakeTime': 0, 'cookEndTime': 0,
                   'recipeName': 'Veggies', 'recipeId': 15, 'recipeType': 3}
        dev.apply_status_response({'code': 0, 'result': {'code': 0, 'result': {
            'stepArray': [running], 'cookMode': 'normal', 'tempUnit': 'c',
            'cookStatus': 'cooking', 'totalTimeRemaining': 400,
            'currentTemp': 190, 'preheatTemp': 0}}})
        after = dev.programme_details('Veggies')
        check('the stored temperature survived the change',
              after.get('cook_temp') == 195, str(after.get('cook_temp')))
        check('the stored duration survived it too',
              after.get('cook_set_time') == 360, str(after.get('cook_set_time')))

        # Loading it again with different settings IS the appliance's own
        # notion changing, and that is followed.
        reloaded = dict(running, cookLastTime=600)
        dev.apply_status_response({'code': 0, 'result': {'code': 0, 'result': {
            'stepArray': [reloaded], 'cookMode': 'normal', 'tempUnit': 'c',
            'cookStatus': 'ready', 'totalTimeRemaining': 600,
            'currentTemp': 60, 'preheatTemp': 0}}})
        after = dev.programme_details('Veggies')
        check('a freshly loaded programme does update it',
              after.get('cook_temp') == 180 and after.get('cook_set_time') == 600,
              str(after))
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_start_cook(vd):
    print('== starting a programme ==')
    presets, path = _isolated_presets()
    try:
        api = RecordingAPI()
        api.account_id = 'acct'
        dev = vd.VeSyncAirFryer(
            {'deviceName': 'fryer', 'deviceType': 'CAF-P583S-KEU', 'cid': 'c',
             'uuid': 'u', 'deviceStatus': 'on', 'connectionStatus': 'online'},
            api, {'alias': 'Cosori Dual Blaze'})
        feed(dev, _status('standby', 0, 172, with_step=False))
        check('idle means a programme can be started', dev.can_start_cook)
        feed(dev, _status('cooking', 300, 190))
        check('a running programme cannot be started over',
              not dev.can_start_cook)
        # An unknown state is NOT startable - the opposite way round from
        # stopping, because the appliance heats.
        feed(dev, _status('pullOut', 300, 190))
        check('an unknown state is not startable', not dev.can_start_cook)
        # A pause holds a half-cooked meal; starting over it would bin it.
        feed(dev, _status('cookStop', 300, 190))
        check('a paused programme is not startable', not dev.can_start_cook)

        feed(dev, _status('standby', 0, 172, with_step=False))
        dev.start_cook('Veggies', 195, 360, recipe_id=15, recipe_type=3)
        method, data = api.calls[-1]
        check('the call is startCook', method == 'startCook', method)
        check('the programme key goes as mode', data.get('mode') == 'Veggies')
        check('the learned id is used', data.get('recipeId') == 15)
        # The single most expensive mistake available here: the appliance
        # counts in seconds, so minutes on the wire would cook for a
        # sixtieth of the time asked for.
        check('the duration is sent in seconds',
              data['startAct'].get('cookSetTime') == 360,
              str(data['startAct'].get('cookSetTime')))
        check('the temperature is sent', data['startAct'].get('cookTemp') == 195)
        check('the account id is filled in', data.get('accountId') == 'acct')
        check('no preheat is asked for', data.get('hasPreheat') == 0)

        # The free start that used to stand here is gone: the appliance
        # refused it every time, sent as mode 'custom' with recipe id 1.
        # What replaced it is in test_start_needs_an_identifier.

        print('  -- values the appliance would refuse --')
        before = len(api.calls)
        for temp in (79, 206, 1000, -5, 'hot', None, 20.5):
            try:
                dev.start_cook('Veggies', temp, 600, recipe_id=15)
            except ValueError:
                pass
            except Exception as e:
                check(f'temperature {temp!r} raises ValueError', False, repr(e))
        for secs in (30, 0, 60 * 60 + 1, None, 'ten'):
            try:
                dev.start_cook('custom', 180, secs)
            except ValueError:
                pass
            except Exception as e:
                check(f'time {secs!r} raises ValueError', False, repr(e))
        check('nothing left the add-on for any of them',
              len(api.calls) == before, str(len(api.calls) - before))

        check('the range follows the unit',
              dev.temperature_range() == dev.TEMP_RANGE_C)
        feed(dev, _status('standby', 0, 172, with_step=False))
        dev.temp_unit = 'f'
        check('and switches with it',
              dev.temperature_range() == dev.TEMP_RANGE_F)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_adjust_running_cook(vd):
    print('== changing a loaded programme ==')
    api = RecordingAPI()
    api.account_id = 'acct'
    dev = vd.VeSyncAirFryer(
        {'deviceName': 'fryer', 'deviceType': 'CAF-P583S-KEU', 'cid': 'c',
         'uuid': 'u', 'deviceStatus': 'on', 'connectionStatus': 'online'},
        api, {'alias': 'Cosori Dual Blaze'})

    # 'ready' is measured, not assumed: both a time and a temperature
    # change were refused there with the cloud's code 11017000, while the
    # same changes went through while cooking.
    for state, expected in (('standby', False), ('ready', False),
                            ('cooking', True), ('cookStop', True),
                            ('cookEnd', False)):
        feed(dev, _status(state, 200, 180, with_step=state != 'standby'))
        check(f'{state}: offer to adjust = {expected}',
              dev.can_adjust_cook is expected)

    feed(dev, _status('cooking', 200, 180))
    dev.set_time_or_temp(temperature=190)
    method, data = api.calls[-1]
    check('the call is setTimeOrTemp', method == 'setTimeOrTemp', method)
    # startCook spells it cookTemp inside startAct; this call wants
    # cookSetTemp at the top level. Sending the wrong one would be read as
    # "no temperature given".
    # Both fields go out together. A payload of only cookSetTemp was
    # refused four times on a real appliance while only cookSetTime went
    # through, and the one documented shape carries both.
    check('the temperature key is cookSetTemp',
          data.get('cookSetTemp') == 190, str(data))
    check('the unchanged time is sent alongside',
          data.get('cookSetTime') == 200, str(data))

    # Nothing beyond the two. Enriching this payload with tempUnit,
    # cookTempDECP and accountId - the three startCook sends with its own
    # temperature - had the appliance refuse the call outright, including
    # for a pure time change, which had worked four times before that.
    check('nothing rides along besides the two fields',
          set(data) == {'cookSetTemp', 'cookSetTime'}, str(sorted(data)))

    dev.set_time_or_temp(seconds=600)
    _method, data = api.calls[-1]
    check('the time is sent in seconds',
          data.get('cookSetTime') == 600, str(data))
    check('the unchanged temperature is sent alongside',
          data.get('cookSetTemp') == 205, str(data))

    # One or the other, never both and never neither - the appliance is
    # given exactly the change that was asked for.
    for kwargs in ({}, {'temperature': 190, 'seconds': 600}):
        try:
            dev.set_time_or_temp(**kwargs)
        except ValueError:
            pass
        else:
            check(f'{kwargs} is refused', False)

    before = len(api.calls)
    for temp in (79, 206, None if False else 'hot'):
        try:
            dev.set_time_or_temp(temperature=temp)
        except ValueError:
            pass
    for secs in (30, 60 * 60 + 1, 'ten'):
        try:
            dev.set_time_or_temp(seconds=secs)
        except ValueError:
            pass
    check('nothing out of range left the add-on',
          len(api.calls) == before, str(len(api.calls) - before))


def test_tree_rows_are_keyed():
    print('== every air fryer tree row carries a stable key ==')
    path = os.path.join(BASE, 'dialog_vesync.py')
    tree = ast.parse(io.open(path, encoding='utf-8').read())
    branch = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (isinstance(test, ast.Compare)
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == 'VeSyncAirFryer'):
            branch = node
            break
    if branch is None:
        check('the air fryer branch was found', False)
        return
    dicts = [n for n in ast.walk(branch) if isinstance(n, ast.Dict)
             and any(isinstance(k, ast.Constant) and k.value == 'kind'
                     for k in n.keys)]
    check('rows were found', len(dicts) >= 5, f'{len(dicts)} rows')
    keys = []
    for d in dicts:
        names = [k.value for k in d.keys if isinstance(k, ast.Constant)]
        if 'key' not in names:
            check('a row without a key', False, ast.dump(d)[:60])
            return
        for k, v in zip(d.keys, d.values):
            if isinstance(k, ast.Constant) and k.value == 'key':
                keys.append(getattr(v, 'value', None))
    check('every row carries a key', True, ', '.join(str(k) for k in keys))
    # Deliberately NOT "all distinct across the branch": the remaining-time
    # and duration lines share one key on purpose, because they are the
    # same line under two labels and the focus should stay on it when a
    # programme starts counting down. What has to hold is that no two rows
    # of ONE rendered list collide, and that is checked on real lists in
    # test_row_sets_per_state.
    check('no key is used more than twice',
          all(keys.count(k) <= 2 for k in set(keys)),
          ', '.join(sorted({k for k in keys if keys.count(k) > 1})) or 'none')
    # The rebuild has to use them, otherwise the keys are decoration and the
    # focus lands on whatever moved into the old position.
    src = io.open(path, encoding='utf-8').read()
    check("the rebuild restores by key", 'focused_key' in src)


class FakeItem:
    """One tree entry. ``ok=False`` stands for wx's invalid item."""

    def __init__(self, text='', data=None, ok=True):
        self.text = text
        self.data = data
        self.ok = ok

    def IsOk(self):
        return self.ok


class FakeTree:
    """Just enough of wx.TreeCtrl for the rebuild to run."""

    INVALID = FakeItem(ok=False)

    def __init__(self, rows):
        self.parent = FakeItem('device')
        self.children = [FakeItem(t, d) for t, d in rows]
        self.focused = self.INVALID
        self.next_rows = []

    # --- navigation ---
    def GetFocusedItem(self):
        return self.focused

    def GetFirstChild(self, parent):
        if not self.children:
            return self.INVALID, 0
        return self.children[0], 0

    def GetNextChild(self, parent, cookie):
        idx = cookie + 1
        if idx >= len(self.children):
            return self.INVALID, idx
        return self.children[idx], idx

    # --- content ---
    def GetItemData(self, item):
        return item.data

    def GetItemText(self, item):
        return item.text

    def SetItemText(self, item, text):
        item.text = text

    def DeleteChildren(self, parent):
        self.children = []

    def SelectItem(self, item):
        self.focused = item

    def SetFocusedItem(self, item):
        self.focused = item


class FakeDialog:
    """The two collaborators the rebuild calls out to."""

    def __init__(self, tree, new_rows):
        self.tree = tree
        self.new_rows = new_rows
        self._suppress_tree_focus_event = False

    def _compute_vesync_device_label(self, device):
        return 'device'

    def _fill_vesync_device_children(self, device_item, device):
        self.tree.children = [FakeItem(t, d) for t, d in self.new_rows]


def _rows(*specs):
    """(text, key) pairs into the item data the tree really carries."""
    return [(text, {'type': 'info', 'device': None, 'key': key})
            for text, key in specs]


def test_row_sets_per_state(vd):
    print('== the rows offered in each state ==')
    path = os.path.join(BASE, 'dialog_vesync.py')
    src = io.open(path, encoding='utf-8').read()
    tree = ast.parse(src)
    func = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.FunctionDef)
                and node.name == '_compute_vesync_items'):
            func = ast.get_source_segment(src, node)
            break
    if func is None:
        check('the row builder was found', False)
        return

    env = {'_': lambda s: s}
    exec(compile(ast.parse(func), path, 'exec'), env)
    compute = env['_compute_vesync_items']

    class FakeDialog:
        def _vesync_filter_is_low(self, device):
            return False

        def _compute_vesync_favorite_item(self, device, is_favorite_view):
            return {'text': 'favorite', 'kind': 'action',
                    'action': 'favorite_add', 'key': 'favorite'}

    dlg = FakeDialog()
    dev = make_device(vd)
    expected = {
        # state -> the actions that must be on offer
        'standby': {'vesync_start_cook'},
        # Adjusting is refused before the programme runs, so only the
        # stop is offered - plus a line saying why.
        'ready': {'vesync_end_cook'},
        # No temperature: six attempts in two payload shapes never once
        # landed one, so the control was taken out and a line naming the
        # reason put in its place.
        'cooking': {'vesync_set_cook_time', 'vesync_end_cook'},
        'cookEnd': {'vesync_start_cook'},
        # cookStop is a PAUSE, not an ending - the tester at the
        # appliance said so. A paused programme is still in there, so it
        # can be stopped and adjusted, and nothing new may be started
        # over the top of it.
        'cookStop': {'vesync_set_cook_time', 'vesync_end_cook'},
    }
    for state, wanted in expected.items():
        feed(dev, _status(state, 200, 180,
                          with_step=state not in ('standby',)))
        items = compute(dlg, dev)
        keys = [i.get('key') for i in items]
        actions = {i['action'] for i in items if i['kind'] == 'action'}
        actions.discard('favorite_add')
        check(f'{state}: keys are distinct',
              len(keys) == len(set(keys)), ', '.join(str(k) for k in keys))
        check(f'{state}: offers {sorted(wanted)}', actions == wanted,
              str(sorted(actions)))

    # Before the first reply nothing is known, and an entry with no actions
    # and no explanation reads like a defect.
    fresh = make_device(vd)
    items = compute(dlg, fresh)
    check('with no status yet, the tree says so',
          any(i.get('key') == 'fryer_waiting' for i in items),
          str([i.get('key') for i in items]))

    # A programme selected but not started reports its whole duration, not
    # a countdown - so the line must not call it remaining time.
    feed(dev, _status('ready', 360, 60))
    texts = [i['text'] for i in compute(dlg, dev)]
    check('a programme not yet running shows a duration',
          any(t.startswith('Duration') for t in texts), str(texts))
    feed(dev, _status('cooking', 200, 180))
    texts = [i['text'] for i in compute(dlg, dev)]
    check('a running one shows the remaining time',
          any(t.startswith('Remaining time') for t in texts), str(texts))


def test_focus_survives_a_vanishing_line():
    print('== the focus does not move to an unrelated line ==')
    import types as _types

    path = os.path.join(BASE, 'dialog_vesync.py')
    src = io.open(path, encoding='utf-8').read()
    tree_ast = ast.parse(src)
    func = None
    for node in ast.walk(tree_ast):
        if (isinstance(node, ast.FunctionDef)
                and node.name == '_rebuild_vesync_children_preserving_focus'):
            func = ast.get_source_segment(src, node)
            break
    if func is None:
        check('the rebuild was found', False)
        return

    wx_stub = _types.SimpleNamespace(CallAfter=lambda *a, **k: None)
    env = {'wx': wx_stub}
    exec(compile(ast.parse(func), path, 'exec'), env)
    rebuild = env['_rebuild_vesync_children_preserving_focus']

    # While cooking, with the reader parked on the temperature.
    cooking = _rows(
        ('Status: on', 'fryer_switch'),
        ('Programme state: cooking', 'fryer_state'),
        ('Programme: Steak', 'fryer_programme'),
        ('Remaining time: 12 sec', 'fryer_remaining'),
        ('Temperature: 192 °C', 'fryer_temp'),
        ('Set temperature: 205 °C', 'fryer_target'),
        ('Cannot be operated yet (CAF-P583S-KEU)', 'fryer_no_control'),
        ('Add to favorites - Enter', 'favorite'),
    )
    # cookEnd: the remaining time is gone, the temperature is not.
    cook_end = _rows(
        ('Status: on', 'fryer_switch'),
        ('Programme state: finished', 'fryer_state'),
        ('Programme: Steak', 'fryer_programme'),
        ('Temperature: 192 °C', 'fryer_temp'),
        ('Set temperature: 205 °C', 'fryer_target'),
        ('Cannot be operated yet (CAF-P583S-KEU)', 'fryer_no_control'),
        ('Add to favorites - Enter', 'favorite'),
    )
    # standby: programme, times and temperatures are all gone.
    standby = _rows(
        ('Status: on', 'fryer_switch'),
        ('Programme state: standby', 'fryer_state'),
        ('Cannot be operated yet (CAF-P583S-KEU)', 'fryer_no_control'),
        ('Add to favorites - Enter', 'favorite'),
    )

    def run(before, after, focus_on):
        tree = FakeTree(before)
        tree.focused = next(c for c in tree.children
                            if c.data['key'] == focus_on)
        dlg = FakeDialog(tree, after)
        rebuild(dlg, tree.parent, object())
        return tree.focused.text

    landed = run(cooking, cook_end, 'fryer_temp')
    check('a line that survives is found again by key',
          landed.startswith('Temperature'), landed)

    landed = run(cooking, cook_end, 'fryer_remaining')
    check('a vanished line hands over to the one above it, not below',
          landed.startswith('Programme:'), landed)

    landed = run(cook_end, standby, 'fryer_temp')
    # The old bug: position 3 of the shorter list is the favorites entry,
    # and position 4 used to be "Cannot be operated yet".
    check('the end of a programme does not land on "cannot be operated"',
          'operated' not in landed, landed)
    check('the end of a programme lands on the programme state',
          landed.startswith('Programme state'), landed)

    # A device whose lines carry no keys must behave exactly as before.
    plain = [('one', {'type': 'info'}), ('two', {'type': 'info'}),
             ('three', {'type': 'info'})]
    tree = FakeTree(plain)
    tree.focused = tree.children[1]
    dlg = FakeDialog(tree, plain)
    rebuild(dlg, tree.parent, object())
    check('unkeyed lines still restore by position',
          tree.focused.text == 'two', tree.focused.text)


# The Roast programme of 2026-08-24, verbatim. Its point is the one call
# in the middle: 180 degrees and 508 seconds went out together and were
# accepted, the appliance took the seconds and kept its 205 degrees.
def _roast(cook_status, set_time, last, temp, cook_temp=205, with_step=True):
    step = {'cookSetTime': set_time, 'cookTemp': cook_temp, 'mode': 'Roast',
            'cookLastTime': last, 'shakeTime': 0, 'cookEndTime': 0,
            'recipeName': 'Roast', 'recipeId': 13, 'recipeType': 3}
    return {
        'stepArray': [step] if with_step else [],
        'cookMode': 'normal', 'tempUnit': 'c', 'stepIndex': 0,
        'cookStatus': cook_status, 'preheatSetTime': 0, 'preheatLastTime': 0,
        'preheatEndTime': 0, 'preheatTemp': 0, 'startTime': 1787581689,
        'totalTimeRemaining': last, 'currentTemp': temp,
        'shakeStatus': 0, 'linkageStatus': 0,
    }


def _fryer_with_api(vd):
    api = RecordingAPI()
    api.account_id = 'acct'
    dev = vd.VeSyncAirFryer(
        {'deviceName': 'fryer', 'deviceType': 'CAF-P583S-KEU', 'cid': 'c',
         'uuid': 'u', 'deviceStatus': 'off', 'connectionStatus': 'online'},
        api, {'alias': 'Cosori Dual Blaze'})
    return api, dev


def test_start_needs_an_identifier(vd):
    print('== a programme without its identifier is refused, not guessed ==')
    api, dev = _fryer_with_api(vd)
    feed(dev, _roast('standby', 0, 0, 60, with_step=False))
    before = len(api.calls)
    try:
        dev.start_cook('Veggies', 190, 600, recipe_id=None)
    except ValueError:
        check('a missing id refuses', True)
    else:
        check('a missing id refuses', False)
    # The point of refusing: id 1 is Steak. Falling back to it would have
    # sent one programme under another one's name, to an appliance that
    # heats.
    check('and nothing was sent', len(api.calls) == before, str(api.calls[before:]))

    # A missing TYPE is different - every programme this appliance ever
    # reported came back as type 3, so filling that in is safe.
    dev.start_cook('Veggies', 190, 600, recipe_id=15, recipe_type=None)
    _method, data = api.calls[-1]
    check('a known id goes through', data.get('recipeId') == 15, str(data))
    check('a missing type is filled in', data.get('recipeType') == 3, str(data))


def test_temperature_verdict(vd):
    print('== a temperature the appliance accepted and did not apply ==')

    # The real sequence: cooking at 205 with 508 seconds left, 180 sent,
    # next status carries the new cookSetTime and the old cookTemp.
    _api, dev = _fryer_with_api(vd)
    feed(dev, _roast('cooking', 600, 508, 124))
    dev.set_time_or_temp(temperature=180)
    check('nothing is judged from the answer to the call alone',
          dev.take_temperature_verdict() is None)
    feed(dev, _roast('cooking', 508, 505, 140))
    check('the appliance keeping 205 is noticed',
          dev.take_temperature_verdict() == (180, 205))

    # Said once. The poll that produced it comes round every fifteen
    # seconds, and a kitchen does not need to hear it four times a minute.
    _api, dev = _fryer_with_api(vd)
    feed(dev, _roast('cooking', 600, 508, 124))
    dev.set_time_or_temp(temperature=180)
    feed(dev, _roast('cooking', 508, 505, 140))
    dev.take_temperature_verdict()
    feed(dev, _roast('cooking', 508, 490, 158))
    check('and said only once', dev.take_temperature_verdict() is None)

    # A cached answer carries the old cookSetTime and looks exactly like a
    # refused temperature. It has to wait rather than accuse the appliance.
    _api, dev = _fryer_with_api(vd)
    feed(dev, _roast('cooking', 600, 508, 124))
    dev.set_time_or_temp(temperature=180)
    feed(dev, _roast('cooking', 600, 508, 124))
    check('a response from before the command is not a verdict',
          dev.take_temperature_verdict() is None)
    feed(dev, _roast('cooking', 508, 505, 140))
    check('the next one that carries the new time is',
          dev.take_temperature_verdict() == (180, 205))

    # A temperature that does land says nothing at all.
    _api, dev = _fryer_with_api(vd)
    feed(dev, _roast('cooking', 600, 508, 124))
    dev.set_time_or_temp(temperature=180)
    feed(dev, _roast('cooking', 508, 505, 140, cook_temp=180))
    check('an applied temperature is silent',
          dev.take_temperature_verdict() is None)

    # Changing the time is not a temperature change, whatever comes back.
    _api, dev = _fryer_with_api(vd)
    feed(dev, _roast('cooking', 600, 508, 124))
    dev.set_time_or_temp(seconds=300)
    feed(dev, _roast('cooking', 300, 298, 140))
    check('a time change never judges the temperature',
          dev.take_temperature_verdict() is None)

    # The programme ended before the verdict arrived. An appliance that is
    # done has no temperature worth complaining about.
    _api, dev = _fryer_with_api(vd)
    feed(dev, _roast('cooking', 600, 508, 124))
    dev.set_time_or_temp(temperature=180)
    feed(dev, _roast('standby', 0, 0, 196, with_step=False))
    check('a finished programme drops the question',
          dev.take_temperature_verdict() is None)


def test_one_state_line(vd):
    print('== the appliance is never "off" while it cooks ==')
    path = os.path.join(BASE, 'dialog_vesync.py')
    src = io.open(path, encoding='utf-8').read()
    tree = ast.parse(src)
    wanted = {'_compute_vesync_items': None, '_compute_vesync_device_label': None}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            wanted[node.name] = ast.get_source_segment(src, node)
    if not all(wanted.values()):
        check('both builders were found', False)
        return

    env = {'_': lambda s: s}
    for source in wanted.values():
        exec(compile(ast.parse(source), path, 'exec'), env)
    compute = env['_compute_vesync_items']
    label_of = env['_compute_vesync_device_label']

    class FakeDialog:
        def _vesync_filter_is_low(self, device):
            return False

        def _compute_vesync_favorite_item(self, device, is_favorite_view):
            return {'text': 'favorite', 'kind': 'action',
                    'action': 'favorite_add', 'key': 'favorite'}

    dlg = FakeDialog()
    # deviceStatus 'off' throughout, which is what the device list really
    # reported while this appliance was at 200 degrees.
    _api, dev = _fryer_with_api(vd)

    check('before the first status the tree still has a state line',
          any(i.get('key') == 'fryer_switch' for i in compute(dlg, dev)),
          str([i.get('key') for i in compute(dlg, dev)]))

    feed(dev, _roast('cooking', 508, 444, 193))
    items = compute(dlg, dev)
    texts = [i['text'] for i in items]
    check('no "Status: off" once the appliance says it is cooking',
          not any(t.startswith('Status:') for t in texts), str(texts))
    check('the state line is the cooking state',
          items[0].get('key') == 'fryer_state', str(texts[:1]))
    check('and there is exactly one of it',
          [i.get('key') for i in items].count('fryer_state') == 1)

    row = label_of(dlg, dev)
    check('the device row says what it is doing, not "off"',
          'cooking' in row and not row.endswith('- off'), row)

    # Idle again: the summary falls back to on/off, which is then true.
    feed(dev, _roast('standby', 0, 0, 196, with_step=False))
    check('in standby the row is allowed to say off',
          label_of(dlg, dev).endswith('- standby')
          or label_of(dlg, dev).endswith('- off'), label_of(dlg, dev))


def test_cook_announcements():
    print('== what the end of a programme is allowed to say ==')
    env = {'_': lambda s: s}
    const_path = os.path.join(BASE, 'constants.py')
    const_src = io.open(const_path, encoding='utf-8').read()
    const_tree = ast.parse(const_src)
    for node in const_tree.body:
        if (isinstance(node, ast.Assign) and node.targets
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ('VESYNC_FRYER_COOK_STATES',
                                           'VESYNC_FRYER_COOK_ANNOUNCEMENTS')):
            exec(compile(ast.Module([node], []), const_path, 'exec'), env)
    states = env.get('VESYNC_FRYER_COOK_STATES')
    spoken = env.get('VESYNC_FRYER_COOK_ANNOUNCEMENTS')
    check('both cooking tables were found',
          isinstance(states, dict) and isinstance(spoken, dict))
    if not (states and spoken):
        return

    # The state that arrives is lower-cased before the lookup, so a key
    # spelled 'cookEnd' would never match and the end of a programme would
    # go quiet again without anything failing.
    check('every spoken state is keyed in lower case',
          all(k == k.lower() for k in spoken), str(sorted(spoken)))
    check('every spoken state is a state the appliance reports',
          set(spoken) <= set(states), str(sorted(set(spoken) - set(states))))
    check('the end of a programme is spoken', 'cookend' in spoken)
    # 'standby' is the appliance falling back to nothing after a cook and
    # 'ready' means somebody is standing at it turning the dial. Speaking
    # either turns a finished meal into four announcements.
    check('falling idle and being set up are not spoken',
          not ({'standby', 'ready'} & set(spoken)), str(sorted(spoken)))

    cd_path = os.path.join(BASE, 'change_detection.py')
    cd_src = io.open(cd_path, encoding='utf-8').read()
    cd_tree = ast.parse(cd_src)
    snapshot = detect = None
    for node in ast.walk(cd_tree):
        if isinstance(node, ast.FunctionDef):
            if node.name == '_snapshot_vesync_state':
                snapshot = node
            elif node.name == '_detect_vesync_changes':
                detect = node
    check('the snapshot carries the cooking state',
          snapshot is not None
          and any(isinstance(k, ast.Constant) and k.value == 'cook_status'
                  for n in ast.walk(snapshot) if isinstance(n, ast.Dict)
                  for k in n.keys if k is not None))

    # The verdict on a temperature the user sent has to be taken BEFORE
    # the local-action suppression, which exists to swallow exactly this
    # kind of event. Reordering the two would silence it again, and
    # nothing else in the tests would notice.
    if detect is not None:
        verdict_line = suppress_line = None
        for node in ast.walk(detect):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)):
                if node.func.attr == '_announce_fryer_temp_verdict':
                    verdict_line = node.lineno
                elif node.func.attr == '_is_recent_local_vesync_action':
                    suppress_line = node.lineno
        check('the verdict is taken before the local-action suppression',
              verdict_line is not None and suppress_line is not None
              and verdict_line < suppress_line,
              f'{verdict_line} < {suppress_line}')


def main():
    vd = load()
    test_seconds(vd)
    test_temperature_is_measured(vd)
    test_programme_source(vd)
    test_programme_names_cover_the_device(vd)
    test_cook_states(vd)
    test_hysteresis(vd)
    test_end_cook(vd)
    test_learning(vd)
    test_start_cook(vd)
    test_adjust_running_cook(vd)
    test_start_needs_an_identifier(vd)
    test_temperature_verdict(vd)
    test_one_state_line(vd)
    test_cook_announcements()
    test_tree_rows_are_keyed()
    test_row_sets_per_state(vd)
    test_focus_survives_a_vanishing_line()
    print()
    if FAILED:
        print(f'FEHLGESCHLAGEN: {len(FAILED)} -> {FAILED}')
        return 1
    print('GESAMT: ALLE TESTS OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
