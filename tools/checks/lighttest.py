# -*- coding: utf-8 -*-
"""Tests what the add-on believes about a lamp's white/colour mode.

The mode is not simply readable: the lamp lags behind a change, so a mode
set from the add-on is cached and consulted BEFORE the lamp's own capacity
value. That part is deliberate.

What it cost: the cache never expired. A mode remembered from an earlier
action outranked the device for good, so a change made anywhere else - at
the lamp, in the Meross app, by a scene - never became visible. An MSL450
switched on while in white mode was announced as being in colour mode,
because a colour had been chosen here at some point.

The cache therefore has a window now, and these checks hold it to it.
Lifts the functions out of the source with ast, so it needs neither NVDA
nor an account nor the meross_iot library.
"""
import ast
import io
import os

BASE = os.environ.get(
    'SHC',
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'globalPlugins', 'SmartHomeControl'))

FAILED = []


def check(name, cond, detail=''):
    print("  " + ('OK  ' if cond else 'FEHL') + "   " + name
          + (("  (" + str(detail) + ")") if detail else ''))
    if not cond:
        FAILED.append(name)


def load_cache_functions(src):
    """The two cache functions plus the module state they need."""
    tree = ast.parse(src)
    wanted_names = {'_LIGHT_MODE_CACHE', '_light_lock', 'LIGHT_MODE_WINDOW'}
    wanted_funcs = {'get_light_mode', 'set_light_mode_cache'}
    pieces = []
    for node in tree.body:
        if (isinstance(node, ast.Assign) and node.targets
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in wanted_names):
            pieces.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_funcs:
            pieces.append(node)
    module = ast.Module(body=pieces, type_ignores=[])
    # One dict for globals and locals: the functions look their lock and
    # their cache up as globals, so splitting the two hides them.
    env = {'threading': __import__('threading'), 'time': __import__('time')}
    exec(compile(module, 'meross_devices.py', 'exec'), env)
    return env


def test_the_window(src):
    print('== a remembered light mode expires ==')
    env = load_cache_functions(src)
    for name in ('get_light_mode', 'set_light_mode_cache', 'LIGHT_MODE_WINDOW'):
        if name not in env:
            check(name + ' was found', False)
            return
    get = env['get_light_mode']
    put = env['set_light_mode_cache']
    window = env['LIGHT_MODE_WINDOW']

    check('an unknown lamp has no remembered mode', get('nobody') is None)

    put('lamp', 'white')
    check('a mode just set is returned', get('lamp') == 'white')
    put('lamp', 'rgb')
    check('and the newest one wins', get('lamp') == 'rgb')

    # The window is what the whole file is about: outside it the lamp's own
    # report has to win, or a white set at the lamp itself stays invisible.
    check('outside the window it is forgotten',
          get('lamp', window=-1) is None)
    check('inside the window it still counts',
          get('lamp', window=window) == 'rgb')

    # Long enough to cover a Meross poll (30 s foreground, 45 s background),
    # short enough that a change made elsewhere shows up while the user is
    # still looking at the tree.
    check('the window outlasts a poll interval', window >= 45, window)
    check('and does not last for ever', window <= 300, window)


def test_the_lamp_can_still_be_heard(src):
    print('== the lamp itself is still consulted ==')
    tree = ast.parse(src)
    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'is_in_rgb_mode':
            func = node
            break
    if func is None:
        check('is_in_rgb_mode was found', False)
        return
    body = ast.get_source_segment(src, func) or ''
    # If the caches were the only sources, nothing set outside the add-on
    # could ever be seen. The capacity value is the lamp's own statement.
    check('the capacity value is read', '_capacity' in body)
    check('the caches are consulted too',
          'get_light_mode' in body and '_light_mode' in body)
    check('the cached mode is asked for with the window',
          'get_light_mode(self.uuid)' in body, 'no explicit window override')

    # The local cache is cleared on every status refresh; the global one
    # expires. Neither may outlive the lamp.
    update = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == '_update_status':
            seg = ast.get_source_segment(src, node) or ''
            if '_light_mode' in seg:
                update = seg
                break
    check('a status refresh drops the local cache',
          update is not None and 'self._light_mode = None' in update)


def main():
    src = io.open(os.path.join(BASE, 'meross_devices.py'),
                  encoding='utf-8').read()
    test_the_window(src)
    test_the_lamp_can_still_be_heard(src)
    print()
    if FAILED:
        print('FEHLGESCHLAGEN: ' + str(len(FAILED)) + ' -> ' + str(FAILED))
        return 1
    print('GESAMT: ALLE TESTS OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
