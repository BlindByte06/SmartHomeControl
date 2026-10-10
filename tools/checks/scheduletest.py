# -*- coding: utf-8 -*-
"""Testet das Zeitprogramm der Warmwasser-Wärmepumpe.

Schreiben ist hier heikler als anderswo: die Cozytouch-Cloud bestätigt eine
Ausführung, und die Fritteuse hat gelehrt, dass das nicht dasselbe ist wie
"das Gerät hat es getan". Deshalb liest ``set_schedule_windows`` den Plan
zurück und vergleicht.

Die Falle dabei ist der Schutzzeitraum: ``apply_capabilities`` überspringt
Felder, die gerade lokal geändert wurden. Würde der Rückleseweg darüber
laufen, verglichen wir den Wunsch mit sich selbst und bekämen immer "hat
geklappt". Genau das prüft der letzte Test.
"""
import importlib.util
import io
import json
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


def load_devices():
    """Laedt cozytouch_devices.py mit gestellten NVDA-Modulen."""
    for name in ('logHandler', 'addonHandler', 'ui', 'tones', 'config', 'gui',
                 'wx'):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
    sys.modules['logHandler'].log = type('L', (), {
        'debug': staticmethod(lambda *a, **k: None),
        'info': staticmethod(lambda *a, **k: None),
        'warning': staticmethod(lambda *a, **k: None),
        'error': staticmethod(lambda *a, **k: None)})()
    sys.modules['addonHandler'].initTranslation = lambda: None
    # As a package, so its relative imports (formatting) resolve.
    pkg = types.ModuleType('shcschedule')
    pkg.__path__ = [BASE]
    sys.modules['shcschedule'] = pkg
    return importlib.import_module('shcschedule.cozytouch_devices')


class Api:
    """Cloud-Attrappe: nimmt an oder lehnt ab, und liefert beim Zurücklesen,
    was das Geraet angeblich wirklich haelt.

    ``later`` ist der Wert, den das Geraet ab dem zweiten Zurueckelesen
    meldet - so laesst sich die Cloud nachstellen, die die Ausfuehrung
    bestaetigt, bevor ihre eigene Kopie nachgezogen hat.
    """

    def __init__(self, accept=True, stored=None, later=None):
        self.accept = accept
        self.stored = stored          # was das Geraet nach dem Schreiben hat
        self.later = later
        self.written = []
        self.reads = 0

    def set_capability(self, device_id, capability_id, value):
        self.written.append((capability_id, value))
        if self.accept and self.stored is None:
            self.stored = value
        return self.accept

    def _fetch_capabilities(self, device_id):
        self.reads += 1
        if self.later is not None and self.reads >= 2:
            return [{'capabilityId': 245, 'value': self.later}]
        return [{'capabilityId': 245, 'value': self.stored}]


def make_device(mod, api, monday='[[420,1020],[0,0],[0,0]]'):
    device = mod.CozytouchWaterHeater.__new__(mod.CozytouchWaterHeater)
    import threading
    device._lock = threading.RLock()
    device._caps = {245: monday}
    device._last_field_action_ts = {}
    device._api = api
    device.device_id = 1
    device.name = 'Revolution'
    return device


def main():
    mod = load_devices()
    # Ohne das wartet jeder misslungene Schreibvorgang dreimal drei Sekunden.
    mod.SCHEDULE_READBACK_PAUSE = 0

    print('== Lesen und Pruefen ==')
    check('ein Fenster wird gelesen',
          mod._parse_schedule_windows('[[420,1020],[0,0],[0,0]]')
          == [[420, 1020], [0, 0], [0, 0]])
    check('eine kurze Liste wird auf drei Plaetze ergaenzt',
          mod._parse_schedule_windows('[[420,1020]]')
          == [[420, 1020], [0, 0], [0, 0]])
    check('Unsinn ergibt None', mod._parse_schedule_windows('kaputt') is None)
    check('gueltig meldet nichts',
          mod._schedule_windows_valid([[420, 1080], [0, 0], [0, 0]]) is None)
    check('Ende vor Beginn faellt auf',
          mod._schedule_windows_valid([[1080, 420], [0, 0], [0, 0]]) == 'order')
    check('Ueberschneidung faellt auf',
          mod._schedule_windows_valid([[420, 1020], [900, 1200], [0, 0]])
          == 'overlap')
    check('ausserhalb des Tages faellt auf',
          mod._schedule_windows_valid([[0, 1500], [0, 0], [0, 0]]) == 'range')
    check('zwei getrennte Fenster sind erlaubt',
          mod._schedule_windows_valid([[360, 600], [900, 1200], [0, 0]]) is None)

    print('== Schreiben: das Geraet nimmt es an ==')
    api = Api(accept=True)
    device = make_device(mod, api)
    ok, actual = device.set_schedule_windows(0, [[420, 1080], [0, 0], [0, 0]])
    check('es wird als erledigt gemeldet', ok is True)
    check('und der Plan steht auf dem neuen Wert',
          actual == [[420, 1080], [0, 0], [0, 0]], str(actual))
    check('geschrieben wurde die Faehigkeit des Montags',
          api.written and api.written[0][0] == 245, str(api.written[:1]))
    check('als kompakte Liste ohne Leerzeichen',
          api.written[0][1] == '[[420,1080],[0,0],[0,0]]', api.written[0][1])

    print('== Die Cloud lehnt ab ==')
    api = Api(accept=False, stored='[[420,1020],[0,0],[0,0]]')
    device = make_device(mod, api)
    ok, actual = device.set_schedule_windows(0, [[420, 1080], [0, 0], [0, 0]])
    check('es wird NICHT als erledigt gemeldet', ok is False)
    check('und der alte Plan wird zurueckgegeben',
          actual == [[420, 1020], [0, 0], [0, 0]], str(actual))

    print('== Die Cloud bestaetigt, das Geraet macht nichts ==')
    # Der Fall der Fritteuse: Ausfuehrung "erledigt", Wert unveraendert.
    api = Api(accept=True, stored='[[420,1020],[0,0],[0,0]]')
    device = make_device(mod, api)
    ok, actual = device.set_schedule_windows(0, [[420, 1080], [0, 0], [0, 0]])
    check('das faellt auf', ok is False)
    check('und es wird gesagt, was wirklich dasteht',
          actual == [[420, 1020], [0, 0], [0, 0]], str(actual))

    print('== Das Geraet speichert etwas Drittes ==')
    api = Api(accept=True, stored='[[420,1050],[0,0],[0,0]]')
    device = make_device(mod, api)
    ok, actual = device.set_schedule_windows(0, [[420, 1080], [0, 0], [0, 0]])
    check('auch das faellt auf', ok is False)
    check('und der tatsaechliche Wert kommt zurueck',
          actual == [[420, 1050], [0, 0], [0, 0]], str(actual))

    print('== Der Wert kommt erst beim zweiten Lesen ==')
    # Genau der Fall aus dem Protokoll vom 5. September: die Cloud meldet die
    # Ausfuehrung als erledigt, ihre Kopie der Faehigkeit hinkt aber nach.
    api = Api(accept=True, stored='[[420,1020],[0,0],[0,0]]',
              later='[[420,1080],[0,0],[0,0]]')
    device = make_device(mod, api)
    ok, actual = device.set_schedule_windows(0, [[420, 1080], [0, 0], [0, 0]])
    check('der zweite Versuch findet den neuen Wert', ok is True,
          f'ok={ok} nach {api.reads} Leseversuchen')
    check('und es wird nicht laenger gelesen als noetig', api.reads == 2,
          f'{api.reads} Leseversuche')

    print('== Bei Ablehnung wird mehrmals nachgesehen ==')
    api = Api(accept=True, stored='[[420,1020],[0,0],[0,0]]')
    device = make_device(mod, api)
    device.set_schedule_windows(0, [[420, 1080], [0, 0], [0, 0]])
    check('es wird bis zur festgelegten Grenze gelesen',
          api.reads == mod.SCHEDULE_READBACK_TRIES,
          f'{api.reads} von {mod.SCHEDULE_READBACK_TRIES}')

    print('== Der Schutzzeitraum darf das Zurueckelesen nicht blenden ==')
    api = Api(accept=True, stored='[[420,1020],[0,0],[0,0]]')
    device = make_device(mod, api)
    device._protect('schedule')          # als waere gerade lokal geaendert
    ok, actual = device.set_schedule_windows(0, [[420, 1080], [0, 0], [0, 0]])
    check('trotz aktivem Schutz wird der echte Wert gelesen',
          ok is False and actual == [[420, 1020], [0, 0], [0, 0]],
          f'ok={ok} actual={actual}')
    check('das Schutzfeld heisst schedule',
          device._field_for_cap(245) == 'schedule'
          and device._field_for_cap(251) == 'schedule')

    print('== Verdrahtung ==')
    dialog = io.open(os.path.join(BASE, 'dialog_cozytouch.py'),
                     encoding='utf-8').read()
    check('die Baumzeile ist bedienbar',
          "'action': 'cozytouch_schedule'" in dialog)
    check('es gibt einen Handler',
          'def _handle_cozytouch_schedule' in dialog)
    check('der Handler meldet auch den Fall "nicht uebernommen"',
          'did not take the change' in dialog)
    check('die Fenster tragen eine Nummer - zwei gleiche Eintraege klingen '
          'sonst, als taete die Pfeiltaste nichts',
          'Window {number}: unused' in dialog
          and 'Window {number}: {times}' in dialog)
    check('mehrere Tage lassen sich auswaehlen',
          'wx.CheckBox' in dialog and 'day_checks' in dialog)
    check('und zwar mit echten Kontrollkaestchen - eine CheckListBox sagt '
          'beim Durchgehen nichts',
          'wx.CheckListBox(' not in dialog)
    check('eine Ablehnung bricht die uebrigen Tage ab',
          'if not ok:' in dialog and 'break' in dialog)
    tree = io.open(os.path.join(BASE, 'device_dialog.py'),
                   encoding='utf-8').read()
    check('die Aktion wird verteilt',
          "'cozytouch_schedule': '_handle_cozytouch_schedule'" in tree)
    helptext = io.open(os.path.join(BASE, 'dialog_help.py'),
                       encoding='utf-8').read()
    check('F1 kennt sie', "action == 'cozytouch_schedule'" in helptext)
    hist = io.open(os.path.join(BASE, 'history.py'), encoding='utf-8').read()
    check('die Chronik kann sie benennen', "'set_schedule':" in hist)

    print()
    if FAILED:
        print('FEHLGESCHLAGEN:')
        for name in FAILED:
            print('  -', name)
        return 1
    print('GESAMT: ALLE TESTS OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
