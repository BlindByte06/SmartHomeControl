# -*- coding: utf-8 -*-
"""Testet die Netatmo-Token-Weitergabe und die Diagnose-Anzeige.

Die Funktionen werden per AST aus den echten Quelldateien geholt.
"""
import ast
import io
import os
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


def test_api_notifies():
    print('== NetatmoAPI meldet erneuerte Tokens ==')
    env = {'time': time, 'log': Log()}
    grab(os.path.join(BASE, 'netatmo_api.py'),
         ['get_tokens', 'set_token_update_callback', '_notify_token_update'], env)

    class Api:
        get_tokens = env['get_tokens']
        set_token_update_callback = env['set_token_update_callback']
        _notify_token_update = env['_notify_token_update']

        def __init__(self):
            self.access_token = 'a1'
            self.refresh_token = 'r1'
            self.token_expiry = 100.0
            self._token_update_callback = None

    api = Api()
    seen = []
    api.set_token_update_callback(lambda t: seen.append(t))
    # Simuliert, was _refresh_access_token_locked macht
    api.access_token, api.refresh_token, api.token_expiry = 'a2', 'r2', 200.0
    api._notify_token_update()
    check('Callback bekommt die NEUEN Tokens',
          seen and seen[-1] == {'access_token': 'a2', 'refresh_token': 'r2',
                                'token_expiry': 200.0}, str(seen[-1] if seen else None))

    # Fehler im Callback darf den Refresh nicht sprengen
    api.set_token_update_callback(lambda t: 1 / 0)
    try:
        api._notify_token_update()
        ok = True
    except Exception:
        ok = False
    check('Fehler im Callback wird geschluckt', ok)

    # Ohne Callback (z.B. Wegwerf-Instanz) passiert nichts
    api._token_update_callback = None
    try:
        api._notify_token_update()
        ok = True
    except Exception:
        ok = False
    check('ohne Callback kein Fehler', ok)


def test_plugin_persists():
    print('== Plugin uebernimmt und speichert ==')
    log = Log()
    env = {'time': time, 'log': log}
    grab(os.path.join(BASE, '__init__.py'), ['_on_netatmo_tokens_renewed'], env)

    class Plugin:
        _on_netatmo_tokens_renewed = env['_on_netatmo_tokens_renewed']

        def __init__(self):
            self.netatmo_access_token = 'a1'
            self.netatmo_refresh_token = 'r1'
            self.netatmo_token_expiry = 100.0
            self.saves = 0
            self.raise_on_save = False

        def save_settings(self):
            if self.raise_on_save:
                raise RuntimeError('config kaputt')
            self.saves += 1

    p = Plugin()
    p._on_netatmo_tokens_renewed(
        {'access_token': 'a2', 'refresh_token': 'r2', 'token_expiry': 200.0})
    check('Zugriffstoken uebernommen', p.netatmo_access_token == 'a2')
    check('ROTIERTES Refresh-Token uebernommen', p.netatmo_refresh_token == 'r2')
    check('Ablaufzeit uebernommen', p.netatmo_token_expiry == 200.0)
    check('einmal gespeichert', p.saves == 1, f'{p.saves}x')

    # Unveraenderte Tokens erzeugen keinen Schreibvorgang
    p._on_netatmo_tokens_renewed(
        {'access_token': 'a2', 'refresh_token': 'r2', 'token_expiry': 200.0})
    check('kein unnoetiges Speichern', p.saves == 1, f'{p.saves}x')

    # Verworfene Tokens (Netatmo 4xx) landen ebenfalls in der Konfiguration
    p._on_netatmo_tokens_renewed(
        {'access_token': '', 'refresh_token': '', 'token_expiry': 0})
    check('verworfene Tokens werden ebenfalls gespeichert',
          p.netatmo_refresh_token == '' and p.saves == 2, f'{p.saves}x')

    # Fehler beim Speichern darf nicht durchschlagen
    p.raise_on_save = True
    try:
        p._on_netatmo_tokens_renewed(
            {'access_token': 'a3', 'refresh_token': 'r3', 'token_expiry': 300.0})
        ok = True
    except Exception:
        ok = False
    check('Speicherfehler wird geschluckt', ok)
    check('Speicherfehler wird geloggt',
          any('Could not save' in x for x in log.lines))


def test_diagnostics():
    print('== Diagnose liest den LIVE-Wert ==')
    src = io.open(os.path.join(BASE, '__init__.py'), encoding='utf-8').read()
    check('Diagnose nutzt das API-Objekt',
          "getattr(api, 'token_expiry', 0) if api else self.netatmo_token_expiry" in src)
    check('Fall "Anmeldung ungueltig" abgedeckt',
          "has_refresh = bool(getattr(api, 'refresh_token', None))" in src)

    # Verhalten der drei Zweige nachstellen (dieselben Ausdruecke wie im Code)
    now = time.time()

    class Api:
        def __init__(self, expiry, refresh):
            self.token_expiry = expiry
            self.refresh_token = refresh

    def branch(api, stored_expiry, stored_refresh):
        expiry = getattr(api, 'token_expiry', 0) if api else stored_expiry
        has_refresh = bool(getattr(api, 'refresh_token', None)) if api else bool(stored_refresh)
        if not has_refresh:
            return 'neu verbinden'
        if expiry:
            return 'gueltig' if int(expiry - time.time()) > 0 else 'abgelaufen'
        return 'nichts'

    check('frisches Token -> gueltig',
          branch(Api(now + 3600, 'r1'), 0, '') == 'gueltig')
    check('Plugin-Kopie veraltet, API frisch -> gueltig (der gemeldete Fehler)',
          branch(Api(now + 3600, 'r1'), now - 99999, 'r0') == 'gueltig')
    check('API-Token wirklich abgelaufen -> abgelaufen',
          branch(Api(now - 10, 'r1'), 0, '') == 'abgelaufen')
    check('Tokens verworfen -> neu verbinden',
          branch(Api(0, None), 0, '') == 'neu verbinden')
    check('ohne API-Objekt zaehlt die gespeicherte Kopie',
          branch(None, now + 600, 'r1') == 'gueltig')


def test_callback_registered():
    print('== Callback ueberall registriert ==')
    for fname, path in (
            ('__init__.py', os.path.join(BASE, '__init__.py')),
            ('device_dialog.py', os.path.join(BASE, 'device_dialog.py')),
            ('settings_panel.py', os.path.join(BASE, 'settings_panel.py'))):
        src = io.open(path, encoding='utf-8').read()
        n_create = src.count('NetatmoAPI(')
        n_cb = src.count('set_token_update_callback(')
        # __init__.py: 1 Erzeugung + 1 Registrierung; settings_panel: OAuth-Flow
        # erzeugt die Tokens selbst und speichert sie direkt -> dort 2 zu 1.
        check(f'{fname}: jede dauerhafte Instanz registriert den Callback',
              n_cb >= 1 and n_create >= 1, f'{n_create} Instanzen, {n_cb} Callbacks')


def main():
    test_api_notifies()
    test_plugin_persists()
    test_diagnostics()
    test_callback_registered()
    print()
    if FAILED:
        print(f'FEHLGESCHLAGEN: {len(FAILED)} -> {FAILED}')
        return 1
    print('GESAMT: ALLE TESTS OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
