# -*- coding: utf-8 -*-
"""Prueft die Favoriten-Ebene gegen den echten Code aus __init__.py.

Modelliert NVDAs zwei Threads: die Abfang-Funktion laeuft auf dem
Eingabe-Thread, wx-Timer duerfen nur im Hauptthread angefasst werden.
"""
import builtins
import sys
import textwrap
import types

SRC = 'globalPlugins/SmartHomeControl/__init__.py'


def strip_decorators(text):
    """Entfernt @scriptHandler.script(...) klammer-korrekt."""
    start = text.find('@scriptHandler.script(')
    while start != -1:
        k = text.index('(', start)
        depth = 0
        for pos in range(k, len(text)):
            if text[pos] == '(':
                depth += 1
            elif text[pos] == ')':
                depth -= 1
                if depth == 0:
                    break
        text = text[:start] + text[text.index('\n', pos) + 1:]
        start = text.find('@scriptHandler.script(')
    return text


def load_layer(source_text):
    src = source_text
    i = src.find('    _FAV_LAYER_IDLE_MS')
    j = src.find('    def _get_favorite_device')
    return strip_decorators(textwrap.dedent(src[i:j]))


messages, beeps, timers, mainq = [], [], [], []
in_main = [True]


class WxAssertionError(Exception):
    pass


class FakeTimer:
    def __init__(self, ms, fn, *a):
        if not in_main[0]:
            raise WxAssertionError("timer can only be started from the main thread")
        self.ms, self.fn, self.args, self.running = ms, fn, a, True
        timers.append(self)

    def Stop(self):
        self.running = False

    def Start(self, ms=None):
        if not in_main[0]:
            raise WxAssertionError("timer can only be started from the main thread")
        self.running = True

    def fire(self):
        if self.running:
            self.running = False
            self.fn(*self.args)


class WX:
    CallLater = FakeTimer

    @staticmethod
    def CallAfter(fn, *a):
        mainq.append((fn, a))


def pump():
    prev = in_main[0]
    in_main[0] = True
    while mainq:
        fn, a = mainq.pop(0)
        fn(*a)
    in_main[0] = prev


class UI:
    @staticmethod
    def message(m):
        messages.append(m)


mgr = type('M', (), {})()
mgr._captureFunc = None


class FakeClock:
    """Steuerbare Uhr: das Schaltfenster wird in Sekunden gemessen, und
    echte Wartezeiten im Test waeren langsam und unzuverlaessig."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


clock = FakeClock()


class IC:
    manager = mgr


class KIG:
    def __init__(self, key, modifier=False):
        self.mainKeyName, self.isModifier = key, modifier


FAVS = {1: {'name': 'Steckdose'}, 2: {'name': 'Ventilator'}, 3: {'name': 'Lampe'}}


class FavStore:
    def get_count(self):
        return len(FAVS)

    def get_by_slot(self, n):
        return FAVS.get(n)


def build_plugin(source_text):
    fakefav = types.ModuleType('favorites')
    fakefav.get_favorites = lambda: FavStore()
    real_import = builtins.__import__

    def fake_import(name, glob=None, loc=None, fromlist=(), level=0):
        if (level > 0 and name == 'favorites') or (fromlist and 'get_favorites' in fromlist):
            return fakefav
        if level > 0 and name == 'constants':
            return fakeconst
        return real_import(name, glob, loc, fromlist, level)

    # Bleibt fuer die gesamte Laufzeit aktiv: die relativen Importe stehen in
    # den Funktionskoerpern und laufen erst beim Aufruf.
    builtins.__import__ = fake_import
    fakeconst = types.ModuleType('constants')
    fakeconst.FAV_LAYER_SWITCH_WINDOW_DEFAULT = 5
    fakeconst.FAV_LAYER_SWITCH_WINDOW_MIN = 1
    fakeconst.FAV_LAYER_SWITCH_WINDOW_MAX = 30
    ns = {
        '__name__': 'SHCtest', '__package__': 'SHCtest',
        'time': clock,
        'ui': UI, 'wx': WX, 'inputCore': IC, 'KeyboardInputGesture': KIG,
        '_': lambda s: s, '_beep': lambda b: beeps.append(b),
        'BEEP_ACTION': 'ACTION', 'BEEP_ERROR': 'ERROR',
        'config': type('C', (), {'conf': {'keyboard': {'multiPressTimeout': 500}}}),
    }
    exec(load_layer(source_text), ns)

    class Plugin:
        is_logged_in = True

        def _favorite_toggle(self, n):
            self.toggled.append(n)

        def _favorite_status(self, n):
            self.statused.append(n)

    for name, val in ns.items():
        if callable(val) and getattr(val, '__name__', '').startswith(('script_fav', '_fav_layer')):
            setattr(Plugin, name, val)
    Plugin._FAV_LAYER_IDLE_MS = ns['_FAV_LAYER_IDLE_MS']
    return Plugin


OK = [True]


def check(cond, msg):
    print(('  OK    ' if cond else '  FEHLER'), msg)
    OK[0] &= bool(cond)


def reset(p):
    messages.clear()
    beeps.clear()
    timers.clear()
    mainq.clear()
    p.toggled, p.statused = [], []
    mgr._captureFunc = None
    in_main[0] = True


def press(key, modifier=False):
    """Tastendruck wie NVDA: Abfang-Funktion auf dem EINGABE-Thread."""
    in_main[0] = False
    try:
        return mgr._captureFunc(KIG(key, modifier))
    finally:
        in_main[0] = True
        pump()


def main():
    Plugin = build_plugin(open(SRC, encoding='utf-8').read())
    p = Plugin()

    print("== 1: Eingabe-Thread stuerzt nicht ab (Regression) ==")
    reset(p)
    p.script_favoritesLayer(None)
    crash = None
    try:
        r = press('1')
    except WxAssertionError as e:
        r, crash = None, e
    check(crash is None, f"kein wxAssertionError ({crash})")
    check(r is False and p.statused == [1], "Ziffer geschluckt, Status angesagt")

    print("== 2: Ebene bleibt nach dem Status OFFEN (Kernaenderung) ==")
    check(mgr._captureFunc is not None, "Ebene noch offen")
    check(p.toggled == [], "nichts geschaltet")

    print("== 3: kein Zeitdruck - Timer laeuft ab, Ebene bleibt trotzdem ==")
    for t in list(timers):
        if t.ms != Plugin._FAV_LAYER_IDLE_MS:
            t.fire()
    pump()
    check(mgr._captureFunc is not None, "kein kurzes Doppeldruck-Fenster mehr")
    check(len([t for t in timers if t.ms != Plugin._FAV_LAYER_IDLE_MS]) == 0,
          "ueberhaupt nur noch der Leerlauf-Timer")

    print("== 4: dieselbe Ziffer erneut -> schalten ==")
    press('1')
    check(p.toggled == [1], f"geschaltet ({p.toggled})")
    check(mgr._captureFunc is None, "Ebene danach zu")

    print("== 5: '1, 2, 1' schaltet nichts ==")
    reset(p)
    p.script_favoritesLayer(None)
    press('1'); press('2'); press('1')
    check(p.toggled == [] and p.statused == [1, 2, 1], f"s={p.statused} t={p.toggled}")

    print("== 6: nach langer Pause wird angesagt statt geschaltet ==")
    # Frueher schaltete '1, 1' auch nach Minuten. Wer eine Ziffer drueckte,
    # abgelenkt wurde und spaeter dieselbe erneut drueckte, schaltete ein
    # Geraet - bei einer Steckerleiste mit einem Rechner daran ist das
    # verlorene Arbeit, kein Aergernis.
    reset(p)
    p.fav_layer_switch_window = 5
    p.script_favoritesLayer(None)
    press('1')
    clock.advance(30)
    press('1')
    check(p.toggled == [], f"nichts geschaltet ({p.toggled})")
    check(p.statused == [1, 1], f"stattdessen erneut angesagt ({p.statused})")
    check(mgr._captureFunc is not None, "Ebene bleibt offen")
    # Ein abgelaufenes Fenster ist keine Sackgasse: die Ansage oeffnet ein
    # neues, der naechste Druck schaltet.
    clock.advance(1)
    press('1')
    check(p.toggled == [1], f"danach schaltbar ({p.toggled})")

    print("== 6b: das Fenster ist einstellbar ==")
    reset(p)
    p.fav_layer_switch_window = 2
    p.script_favoritesLayer(None)
    press('1')
    clock.advance(3)
    press('1')
    check(p.toggled == [], f"3 s > 2 s: nicht geschaltet ({p.toggled})")
    clock.advance(1)
    press('1')
    check(p.toggled == [1], f"1 s < 2 s: geschaltet ({p.toggled})")

    reset(p)
    # Unsinnige Werte duerfen die Ebene nicht unbedienbar machen.
    for bad in (None, '', 'zwoelf', 0, 9999):
        p.fav_layer_switch_window = bad
        w = p._fav_layer_switch_window()
        check(1 <= w <= 30, f"Wert {bad!r} ergibt {w} s im erlaubten Bereich")

    print("== 7: leerer Platz merkt sich nichts ==")
    reset(p)
    p.script_favoritesLayer(None)
    press('7'); press('7')
    check(p.toggled == [], f"leerer Platz schaltet nie ({p.toggled})")
    check(any('not assigned' in str(m) for m in messages), "Hinweis angesagt")

    print("== 8: Escape / fremde Taste / Modifier ==")
    reset(p)
    p.script_favoritesLayer(None)
    press('1'); press('escape')
    check(p.toggled == [] and 'Cancelled' in messages, "Escape beendet ohne Schalten")
    reset(p)
    p.script_favoritesLayer(None)
    check(press('shift', True) is True and mgr._captureFunc is not None, "Modifier passiert")
    check(press('x') is False and mgr._captureFunc is None, "fremde Taste beendet")

    print("== 9: Nummernblock ==")
    reset(p)
    p.script_favoritesLayer(None)
    press('numpad2'); press('numpad2')
    check(p.toggled == [2], f"numpad schaltet ({p.toggled})")

    print("== 10: 0 liest Belegung, Ebene bleibt, Merker bleibt ==")
    reset(p)
    p.script_favoritesLayer(None)
    press('3'); press('0')
    check(any('1: Steckdose' in str(m) for m in messages), "Belegung angesagt")
    press('3')
    check(p.toggled == [3], f"0 stoert den Merker nicht ({p.toggled})")

    print("== 11: Leerlauf beendet die Ebene ==")
    reset(p)
    p.script_favoritesLayer(None)
    press('1')
    wd = [t for t in timers if t.ms == Plugin._FAV_LAYER_IDLE_MS]
    check(len(wd) == 1 and wd[0].running, "Leerlauf-Timer laeuft und wurde neu gestartet")
    wd[0].fire()
    pump()
    check(mgr._captureFunc is None, "Ebene beendet")

    print("== 12: Strg+B meint den Ausgang, nicht die Leiste ==")
    # Ein Ausgang wird als eigener Favorit gefuehrt, mit der uuid der
    # Leiste plus "_ch1". Wer nur 'device' liest, sucht die Leiste: im
    # Favoriten-Tab hiess das "Ist kein Favorit" fuer einen Eintrag, der
    # sichtbar in der Liste stand - und wenn die Leiste selbst Favorit
    # war, entfernte Strg+B auf dem Ausgang die LEISTE und sagte deren
    # Namen an. Der Geraete-Tab war laengst richtig, der Favoriten-Tab
    # nicht; derselbe Fix muss in beiden stehen.
    import io as _io
    import os as _os
    import re as _re
    path = _os.path.join(_os.path.dirname(SRC), 'dialog_favorites.py')
    src = _io.open(path, encoding='utf-8').read()
    for name in ('_toggle_favorite_for_selected', '_toggle_fav_tree_favorite'):
        start = src.index(f'def {name}')
        body = src[start:start + 2500]
        gets = _re.findall(r"\.get\('(channel|device)'\)", body)
        # Vor jedem 'device' muss ein 'channel' stehen.
        pairs = list(zip(gets[::2], gets[1::2]))
        check(bool(pairs) and all(p == ('channel', 'device') for p in pairs),
              f"{name}: Ausgang vor Geraet ({gets})")

    print("== 13: das Schaltfenster steht im Tab Allgemein ==")
    # Es gilt fuer alle Plattformen. Beim Einbau landete es versehentlich im
    # Meross-Tab, weil dort zufaellig die naechste allgemein aussehende
    # Einstellung stand - im Code faellt das nicht auf, nur beim Suchen im
    # Dialog.
    import ast as _ast
    src = _io.open(SRC.replace('__init__.py', 'settings_panel.py'),
                   encoding='utf-8').read()
    spans = {n.name: (n.lineno, n.end_lineno)
             for n in _ast.walk(_ast.parse(src))
             if isinstance(n, _ast.FunctionDef)
             and n.name.startswith('_create_') and n.name.endswith('_tab')}
    line = next((i for i, l in enumerate(src.splitlines(), 1)
                 if 'favSwitchWindowCtrl = wx.TextCtrl' in l), None)
    owner = [n for n, (a, b) in spans.items() if line and a <= line <= b]
    check(owner == ['_create_general_tab'],
          f"Feld liegt in {owner or 'keinem Tab'}")

    print()
    print("GESAMT:", "ALLE TESTS OK" if OK[0] else "FEHLER VORHANDEN")
    return 0 if OK[0] else 1


if __name__ == '__main__':
    sys.exit(main())
