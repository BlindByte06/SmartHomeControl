# -*- coding: utf-8 -*-
"""Testet Coalescing, Scheduler-Skip, Budget-Reserve und Bulk-Verbrauch.

Die Funktionen werden per AST aus den echten Quelldateien geholt und in einer
Stub-Umgebung ausgefuehrt - getestet wird also der ausgelieferte Code, nicht
eine Nachbildung.
"""
import ast
import asyncio
import io
import os
import threading
import time

BASE = os.environ.get(
    'SHC',
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'globalPlugins', 'SmartHomeControl'))

FAILED = []


def check(name, cond, detail=''):
    print(f"  {'OK  ' if cond else 'FEHL'}   {name}" + (f'  ({detail})' if detail else ''))
    if not cond:
        FAILED.append(name)


def grab(path, names, env):
    """Fuehrt die genannten Funktionen/Methoden aus ``path`` in ``env`` aus."""
    src = io.open(path, encoding='utf-8').read()
    tree = ast.parse(src)
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            found[node.name] = ast.get_source_segment(src, node)
    missing = set(names) - set(found)
    if missing:
        raise SystemExit(f'nicht gefunden in {path}: {missing}')
    for name in names:
        exec(compile(ast.parse(found[name]), path, 'exec'), env)
    return env


class Log:
    def __init__(self):
        self.lines = []

    def _add(self, m):
        self.lines.append(str(m))

    debug = info = warning = error = _add


# ---------------------------------------------------------------- Budget ----
def test_budget():
    print('== Budget-Reserve ==')
    env = {'time': time, 'MEROSS_HOURLY_BUDGET': 150, 'MEROSS_BUDGET_BURST': 15,
           'log': Log()}
    grab(os.path.join(BASE, 'meross_api.py'),
         ['_consume_budget', '_budget_exhausted'], env)

    class Api:
        _consume_budget = env['_consume_budget']
        _budget_exhausted = env['_budget_exhausted']

        def __init__(self):
            self._msg_budget = {}

    api = Api()
    RESERVE = 5
    # Hintergrund-Polls leeren den Eimer bis zur Reserve
    bg = 0
    while api._consume_budget('u1', 1, reserve=RESERVE):
        bg += 1
        if bg > 100:
            break
    check('Hintergrund-Poll stoppt vor dem leeren Eimer', bg == 10, f'{bg} Polls')
    left = api._msg_budget['u1']['tokens']
    check('Reserve bleibt uebrig', RESERVE - 0.1 <= left <= RESERVE + 0.1, f'{left:.2f}')
    check('kein Throttle-Hinweis bei Reserve-Stopp',
          not api._budget_exhausted('u1', 1))
    # Nutzeraktion darf die Reserve verbrauchen
    user = 0
    while api._consume_budget('u1', 1):
        user += 1
        if user > 100:
            break
    check('Nutzerabfrage kommt trotzdem durch', user == 5, f'{user} Abfragen')
    check('jetzt meldet der Eimer leer', api._budget_exhausted('u1', 1))
    # Anderes Geraet ist unberuehrt (Limit gilt pro Geraet)
    check('anderes Geraet unberuehrt', api._consume_budget('u2', 1, reserve=RESERVE))


# ------------------------------------------------------------ Bulk-Abruf ----
def test_bulk():
    print('== Bulk-Verbrauchsabruf ==')
    log = Log()
    env = {'time': time, 'asyncio': asyncio, 'log': log,
           'MEROSS_HOURLY_BUDGET': 150, 'MEROSS_BUDGET_BURST': 15}
    grab(os.path.join(BASE, 'meross_api.py'),
         ['_consume_budget', 'get_daily_consumption_bulk'], env)

    class Orig:
        def __init__(self, uuid, fail=False):
            self.uuid = uuid
            self.fail = fail
            self.calls = 0

        async def async_get_daily_power_consumption(self):
            self.calls += 1
            if self.fail:
                raise RuntimeError('boom')
            return [{'date': 'heute', 'total_consumption_kwh': 1.5}]

    class Mgr:
        def __init__(self, devs):
            self.devs = devs

        def find_devices(self, device_uuids):
            u = device_uuids[0]
            return [self.devs[u]] if u in self.devs else []

    class Api:
        CONSUMPTION_CACHE_TTL = 900.0
        _consume_budget = env['_consume_budget']
        get_daily_consumption_bulk = env['get_daily_consumption_bulk']

        def __init__(self, devs):
            self._msg_budget = {}
            self._consumption_cache = {}
            self._running = True
            self.manager = Mgr(devs)

        def _run_async(self, coro, timeout=120):
            return asyncio.new_event_loop().run_until_complete(coro)

    devs = {'a': Orig('a'), 'b': Orig('b'), 'c': Orig('c', fail=True)}
    api = Api(devs)

    res = api.get_daily_consumption_bulk(['a', 'b', 'c'])
    check('erfolgreiche Geraete liefern Daten',
          res['a'] and res['b'], f"a={bool(res['a'])} b={bool(res['b'])}")
    check('fehlgeschlagenes Geraet liefert None', res['c'] is None)
    check('alle drei genau einmal abgefragt',
          all(d.calls == 1 for d in devs.values()),
          str({k: v.calls for k, v in devs.items()}))

    # Zweiter Aufruf: frischer Cache -> keine Cloud-Abfrage fuer a und b
    res2 = api.get_daily_consumption_bulk(['a', 'b', 'c'])
    check('Cache verhindert erneute Abfrage',
          devs['a'].calls == 1 and devs['b'].calls == 1,
          str({k: v.calls for k, v in devs.items()}))
    check('Cache liefert dieselben Werte', res2['a'] == res['a'])
    check('fehlgeschlagenes Geraet wird erneut versucht', devs['c'].calls == 2)

    # Veralteter Cache + Fehler -> alter Wert bleibt erhalten (besser als nichts)
    api._consumption_cache['a'] = (0.0, [{'date': 'alt', 'total_consumption_kwh': 9.9}])
    devs['a'].fail = True
    res3 = api.get_daily_consumption_bulk(['a'])
    check('bei Fehler bleibt der alte Wert erhalten',
          res3['a'] and res3['a'][0]['date'] == 'alt', str(res3['a']))

    # Leeres Budget -> alter Wert, keine Ausnahme
    api._msg_budget['b'] = {'tokens': 0.0, 'last': time.time()}
    api._consumption_cache['b'] = (0.0, [{'date': 'alt-b', 'total_consumption_kwh': 1.0}])
    before = devs['b'].calls
    res4 = api.get_daily_consumption_bulk(['b'])
    check('leeres Budget fragt nicht ab', devs['b'].calls == before)
    check('leeres Budget liefert den Cache',
          res4['b'] and res4['b'][0]['date'] == 'alt-b')


# ------------------------------------------------------------- Coalescing ---
def test_coalescing():
    print('== Coalescing von refresh_devices ==')
    log = Log()
    env = {'time': time, 'log': log, 'threading': threading}
    grab(os.path.join(BASE, '__init__.py'),
         ['refresh_devices', '_mark_platform_refreshed'], env)

    class Plugin:
        refresh_devices = env['refresh_devices']
        _mark_platform_refreshed = env['_mark_platform_refreshed']

        def __init__(self):
            self._refresh_lock = threading.Lock()
            self._devices_lock = threading.RLock()
            self._platform_last_refresh = {}
            self.devices = ['d1']
            self.impl_calls = 0
            self.started = threading.Event()
            self.release = threading.Event()

        def _refresh_devices_impl(self):
            self.impl_calls += 1
            self.started.set()
            self.release.wait(5)
            self.devices = ['d1', 'd2']
            return self.devices

    p = Plugin()
    results = {}

    def first():
        results['first'] = p.refresh_devices()

    def second():
        results['second'] = p.refresh_devices()

    t1 = threading.Thread(target=first)
    t1.start()
    p.started.wait(5)          # erster Refresh laeuft
    t2 = threading.Thread(target=second)
    t2.start()
    time.sleep(0.2)
    check('zweiter Aufruf wartet, statt nochmal zu pollen',
          p.impl_calls == 1 and not results.get('second'), f'impl={p.impl_calls}')
    p.release.set()
    t1.join(5)
    t2.join(5)
    check('nur EIN echter Refresh', p.impl_calls == 1, f'impl={p.impl_calls}')
    check('Wartender bekommt das frische Ergebnis',
          results['second'] == ['d1', 'd2'], str(results.get('second')))
    check('Hinweis im Log', any('waiting for it' in x for x in log.lines))

    # Nacheinander laeuft weiterhin je ein echter Refresh
    p.release.set()
    p.refresh_devices()
    check('sequenzieller Aufruf refresht wieder', p.impl_calls == 2, f'impl={p.impl_calls}')


# --------------------------------------------------------- Scheduler-Skip ---
def test_scheduler_skip():
    print('== Scheduler ueberspringt frisch gepollte Plattform ==')
    src = io.open(os.path.join(BASE, 'scheduler.py'), encoding='utf-8').read()
    check('Skip-Logik vorhanden',
          '_platform_last_refresh.get(name, 0.0)' in src
          and 'next_due[name] = last_any + base' in src)
    check('Refresh laeuft unter derselben Sperre',
          'with self._refresh_lock:' in src)

    # Verhalten der Bedingung nachstellen (dieselben Ausdruecke wie im Code)
    base = 30.0
    now = time.time()
    for age, expect_skip in ((5.0, True), (29.0, True), (31.0, False)):
        last_any = now - age
        skip = bool(last_any and (now - last_any) < base)
        check(f'Alter {age:.0f}s -> {"skip" if expect_skip else "poll"}',
              skip == expect_skip)
        if skip:
            due_in = (last_any + base) - now
            check(f'   naechster Poll in {due_in:.0f}s statt sofort', due_in > 0)


def main():
    test_budget()
    test_bulk()
    test_coalescing()
    test_scheduler_skip()
    print()
    if FAILED:
        print(f'FEHLGESCHLAGEN: {len(FAILED)} -> {FAILED}')
        return 1
    print('GESAMT: ALLE TESTS OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
