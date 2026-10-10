# -*- coding: utf-8 -*-
"""Testet, ob ein Ausfall bemerkt wird - und ob geschwiegen wird, wo nichts ist.

Anlass sind zwei Zeilen aus dem Log vom 9. September.

Die erste: die Netatmo-Wetterstation antwortete mit 503, die Heizung
antwortete normal, und das Add-on sagte nichts. Der Grund steht in
``get_devices``: die beiden Hälften der Cloud werden getrennt geholt, und
solange eine durchkommt, gilt die Abfrage als erfolgreich. Die Geräteliste
kommt dann ohne die Wettermodule zurück, und die zuletzt gelesenen Werte
werden weiter vorgelesen, als wären sie eben angekommen. Kurz darauf fielen
beide Hälften aus - null Geräte kamen zurück - und gemeldet wurde trotzdem
nur „Heizung antwortet nicht", weil ``_refresh_netatmo`` auch dann True
lieferte.

Die zweite: ein Meross-Gerät von 22 lief bei etwa jeder zweiten Abfrage in
seine Zeitüberschreitung. Angesagt wurde „1 von 22 Geräten antworten nicht",
„Meross wieder verbunden", und das dreimal in neun Minuten. Ein einzelnes
Gerät ist keine Störung der Plattform: es gehört in eine Liste, die man lesen
kann, nicht in die Sprachausgabe und nicht in die Beschriftung des
Plattformknotens - die würde den Baum alle paar Minuten neu aufbauen.

Geprüft wird deshalb beides: dass ein echter Ausfall ankommt, und dass ein
Gerät, das eine Abfrage verpasst, still bleibt.
"""
import importlib.util
import io
import os
import re
import sys
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


def load(name):
    spec = importlib.util.spec_from_file_location(
        name + '_probe', os.path.join(BASE, name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def source(name):
    return io.open(os.path.join(BASE, name + '.py'), encoding='utf-8').read()


def load_package_module(name):
    """Imports a module of the add-on as part of a package, NVDA stubbed."""
    import importlib
    import types
    for stub in ('logHandler', 'addonHandler'):
        sys.modules.setdefault(stub, types.ModuleType(stub))
    sys.modules['logHandler'].log = type('L', (), {
        level: staticmethod(lambda *a, **k: None)
        for level in ('debug', 'info', 'warning', 'error')})()
    sys.modules['addonHandler'].initTranslation = lambda: None
    if 'requests' not in sys.modules:
        try:
            import requests  # noqa: F401
        except ImportError:
            sys.modules['requests'] = types.ModuleType('requests')
    pkg = types.ModuleType('shchealth')
    pkg.__path__ = [BASE]
    sys.modules['shchealth'] = pkg
    return importlib.import_module('shchealth.' + name)


def grab_method(module, name, env=None):
    """Holt eine Methode aus der echten Quelldatei und macht sie aufrufbar."""
    import ast
    node = next(n for n in ast.walk(ast.parse(source(module)))
                if isinstance(n, ast.FunctionDef) and n.name == name)
    scope = dict(env or {})
    exec(compile(ast.Module([node], []), '<probe>', 'exec'), scope)
    return scope[name]


def announce_probe(ph):
    """_announce_platform_state mit Attrappen fuer Sprache und Protokoll."""
    spoken = []
    env = {
        'platform_health': ph,
        'ui': type('U', (), {'message': staticmethod(spoken.append)}),
        'wx': type('W', (), {'CallAfter': staticmethod(lambda fn, *a: fn(*a))}),
        'log': type('L', (), {'info': staticmethod(lambda *a: None),
                              'warning': staticmethod(lambda *a: None)}),
        'PLATFORM_LABELS': {'netatmo': 'Netatmo'},
        '_': lambda t: t,
    }
    return spoken, grab_method('scheduler', '_announce_platform_state', env)


def main():
    ph = load('platform_health')

    print('== Der Satz je Plattform ==')
    health = ph.PlatformHealth()
    check('die erste Abfrage sagt nichts', health.update('netatmo', True) is None)
    check('und merkt sich trotzdem den Zustand',
          health.get('netatmo')['state'] == ph.OK)
    check('ein Ausfall wird gemeldet', health.update('netatmo', False) == ph.DOWN)
    check('ein zweiter Fehlschlag schweigt',
          health.update('netatmo', False) is None)
    check('die Rueckkehr wird gemeldet', health.update('netatmo', True) == ph.OK)
    check('eine ausgelassene Abfrage aendert nichts',
          health.update('netatmo', None) is None
          and health.get('netatmo')['state'] == ph.OK)

    print('== Der Fall vom 9. September: halb da ist nicht in Ordnung ==')
    health = ph.PlatformHealth()
    health.update('netatmo', True)
    health.note_parts('netatmo', ('weather',))
    state = health.update('netatmo', True)
    check('eine gelungene Abfrage ohne die Wetterstation faellt auf',
          state == ph.PARTIAL, str(state))
    check('und es steht dabei, welche Haelfte fehlt',
          'weather' in health.get('netatmo')['parts'])
    health.note_parts('netatmo', ())
    check('kommt sie zurueck, wird das gesagt',
          health.update('netatmo', True) == ph.OK)

    print('== Ein einzelnes Geraet ist keine Stoerung der Plattform ==')
    health = ph.PlatformHealth()
    health.note_devices('meross', [('a', 'Steckdose 1'), ('b', 'Steckdose 2')], [])
    health.update('meross', True)
    health.note_devices('meross', [('a', 'Steckdose 1')], [('b', 'Steckdose 2')])
    check('ein verpasster Abruf wird nicht angesagt',
          health.update('meross', True) is None)
    check('und die Plattform bleibt in Ordnung',
          health.get('meross')['state'] == ph.OK)
    check('am Plattformknoten steht deshalb nichts - sonst baut der Baum '
          'alle paar Minuten neu auf',
          ph.status_suffix(health.get('meross')) == '')

    print('== Aber es wird aufgeschrieben ==')
    rows = health.device_rows('meross')
    check('genau das eine Geraet steht in der Liste',
          len(rows) == 1 and rows[0]['name'] == 'Steckdose 2', str(rows))
    check('es gilt als nicht antwortend', rows[0]['ok'] is False)
    check('mit einem Zeitpunkt, seit wann', rows[0]['since'] > 0)
    check('und einem Zeitpunkt der letzten Antwort', rows[0]['last_ok'] > 0)
    health.note_devices('meross', [('a', 'Steckdose 1')], [('b', 'Steckdose 2')])
    rows = health.device_rows('meross')
    check('zwei Ausfaelle hintereinander werden gezaehlt',
          rows[0]['misses'] == 2 and rows[0]['total_misses'] == 2,
          f"{rows[0]['misses']}/{rows[0]['total_misses']}")
    health.note_devices('meross', [('a', 'A'), ('b', 'Steckdose 2')], [])
    rows = health.device_rows('meross')
    check('antwortet es wieder, bleibt es sichtbar - die Frage ist ja, was '
          'vorhin los war',
          len(rows) == 1 and rows[0]['ok'] is True)
    check('die Ausfaelle in Folge sind zurueckgesetzt', rows[0]['misses'] == 0)
    check('die Summe bleibt', rows[0]['total_misses'] == 2)
    check('ein Geraet, das immer geantwortet hat, steht nicht in der Liste',
          all(row['name'] != 'A' for row in rows))

    print('== Die Reihenfolge: was noch aussteht, zuerst ==')
    health = ph.PlatformHealth()
    health.note_devices('vesync', [], [('x', 'Alt'), ('y', 'Neu')])
    health.note_devices('vesync', [('x', 'Alt')], [('y', 'Neu')])
    rows = health.device_rows('vesync')
    check('das noch ausgefallene Geraet steht vorne',
          rows[0]['name'] == 'Neu', [row['name'] for row in rows])

    print('== Die Ereignisliste ==')
    health = ph.PlatformHealth()
    health.update('meross', True)
    health.update('meross', False)
    health.update('meross', True)
    events = health.events()
    check('drei Zustaende, drei Eintraege', len(events) == 3, str(len(events)))
    check('der neueste steht oben', events[0]['state'] == ph.OK)
    check('die erste Beobachtung ist als solche gekennzeichnet',
          events[-1]['first'] is True)
    check('jeder Eintrag hat eine Zeit', all(e['time'] > 0 for e in events))
    check('nach Plattform gefiltert bleibt nichts Fremdes uebrig',
          health.events('netatmo') == [])
    labels = {'meross': 'Meross'}
    check('und die Zeile ist lesbar',
          ph.event_text(events[-1], labels) == 'Meross: connected',
          ph.event_text(events[-1], labels))
    check('ein Ausfall auch',
          'Meross' in ph.event_text(events[1], labels),
          ph.event_text(events[1], labels))

    print('== Nach dem Abmelden ==')
    health = ph.PlatformHealth()
    health.update('vesync', True)
    health.update('vesync', False)
    health.reset()
    check('faengt der Satz von vorne an',
          health.get('vesync')['state'] is None)
    check('die Ereignisse sind fort', health.events() == [])
    check('und die naechste Anmeldung meldet keine Rueckkehr',
          health.update('vesync', True) is None)

    print('== Was vorgelesen wird ==')
    health = ph.PlatformHealth()
    health.update('netatmo', True)
    health.update('netatmo', False)
    entry = health.get('netatmo')
    text = ph.status_suffix(entry)
    check('ein Ausfall nennt die Uhrzeit', bool(re.search(r'\d\d:\d\d', text)), text)
    check('ist alles in Ordnung, steht nichts dahinter',
          ph.status_suffix({'state': ph.OK}) == '')
    text = ph.status_suffix({'state': ph.PARTIAL, 'parts': ('weather',)})
    check('ein Teilausfall nennt die Haelfte',
          ph.PART_NAMES['weather'] in text, text)
    check('und weiss auch ohne Einzelheiten etwas zu sagen',
          ph.status_suffix({'state': ph.PARTIAL, 'parts': ()}) != '')

    print('== Alte Werte werden als alt gekennzeichnet ==')
    now = time.time()
    check('frische Werte bleiben unkommentiert',
          ph.values_age_suffix({'state': ph.DOWN, 'last_ok': now}) == '')
    text = ph.values_age_suffix({'state': ph.DOWN, 'last_ok': now - 3600})
    check('eine Stunde alte Werte nicht',
          bool(re.search(r'\d\d:\d\d', text)), text)
    check('bei erreichbarer Plattform steht nie etwas dabei',
          ph.values_age_suffix({'state': ph.OK, 'last_ok': now - 3600}) == '')

    print('== Die Zusammenfassung der Statuszeile ==')
    labels = {'meross': 'Meross', 'netatmo': 'Netatmo'}
    health = ph.PlatformHealth()
    health.update('meross', True)
    health.update('netatmo', True)
    check('laeuft alles, steht nichts in der Zeile',
          ph.summary_text(health, ['meross', 'netatmo'], labels) == '')
    health.update('netatmo', False)
    text = ph.summary_text(health, ['meross', 'netatmo'], labels)
    check('sonst stehen beide darin - die gute Plattform als Gegenprobe',
          'Meross' in text and 'Netatmo' in text, text)
    check('ohne Satz bleibt sie leer',
          ph.summary_text(None, ['meross'], labels) == '')

    print('== Meross meldet, wer geantwortet hat ==')
    meross = source('meross_api')
    check('der Bericht wird geschrieben',
          "'answered': answered, 'missing': missing" in meross)
    check('und vor jeder Abfrage zurueckgesetzt - sonst gelten nach einer '
          'Zeitueberschreitung die Zahlen der vorigen Abfrage',
          "'devices_ok': None, 'devices_total': None" in meross)
    check('die Namen kommen aus der Reihenfolge der Aufgaben',
          'zip(uuid_groups.values(), results)' in meross)
    check('ein wegen des Kontingents uebersprungenes Geraet gilt nicht als '
          'Fehler - sonst meldet eine volle Stunde einen Ausfall',
          'return (None, None)' in meross and 'if success is None:' in meross)

    print('== Netatmo meldet, welche Haelfte fehlt ==')
    netatmo = source('netatmo_api')
    check('die fehlenden Haelften werden festgehalten',
          'self.last_failed_parts = tuple(' in netatmo)
    check('nur Haelften, die dieses Konto wirklich hat - ohne Thermostat darf '
          'nie eine ausgefallene Heizung gemeldet werden',
          "self._known_parts.add('energy')" in netatmo
          and 'key in self._known_parts' in netatmo)
    check('die Wettermodule werden dafuer gezaehlt',
          'weather_count = len(devices)' in netatmo
          and 'weather_count = 0' in netatmo)
    check('faellt alles aus, sagt die Abfrage das auch - sie wirft keine '
          'Ausnahme und galt deshalb als gelungen',
          'self.last_poll_ok = bool(weather_ok or energy_ok)' in netatmo)

    print('== Netatmo fragt sparsam ==')
    # Der Zeitplaner rief bei jeder Abfrage get_devices() - drei Anfragen,
    # gemessen rund 110 Mal pro Stunde, bei offenem Menue alle 15 Sekunden.
    # Die sparsame Abfrage gab es, sie wurde nie aufgerufen. Und
    # /getstationsdata ging bei einem Konto ohne Wetterstation jedes Mal
    # mit hinaus.
    netatmo_mod = load_package_module('netatmo_api')
    api = netatmo_mod.NetatmoAPI('id', 'secret')
    api._ensure_valid_token = lambda: None
    calls = []
    homes = {'body': {'homes': [{
        'id': 'h1', 'name': 'Zuhause', 'rooms': [{'id': 'r1', 'name': 'Bad'}],
        'modules': [{'id': 'm1', 'type': 'NATherm1', 'room_id': 'r1',
                     'name': 'Thermostat'}]}]}}
    status = {'body': {'home': {
        'rooms': [{'id': 'r1', 'therm_setpoint_temperature': 21.0,
                   'therm_setpoint_mode': 'manual',
                   'therm_measured_temperature': 20.5}],
        'modules': [{'id': 'm1', 'reachable': True}]}}}

    def homes_data(gateway_types=None, max_age=None, cached_only=False):
        calls.append('homesdata (cache)' if cached_only else 'homesdata')
        return homes

    api.get_stations_data = lambda: calls.append('stations') or {
        'body': {'devices': []}}
    api.get_homes_data = homes_data
    api.get_home_status = lambda home_id=None: calls.append('homestatus') or status
    devices = api.get_devices()
    check('die Anmeldung findet den Thermostat', len(devices) == 1)
    calls.clear()
    for _round in range(10):
        api.update_device_status(devices)
    asked = [c for c in calls if c != 'homesdata (cache)']
    check('zehn Abfragen kosten zehn Anfragen, nicht dreissig',
          asked == ['homestatus'] * 10, str(asked))
    check('und lesen den Wert trotzdem',
          devices[0].get_setpoint_temp() == 21.0)
    check('die Abfrage gilt als gelungen', api.last_poll_ok is True)
    calls.clear()
    api._last_full_status_refresh -= netatmo_mod.NETATMO_FULL_REFRESH_SECONDS + 1
    api.update_device_status(devices)
    check('alle fuenf Minuten ein voller Durchgang',
          'homesdata' in calls and 'homestatus' in calls, str(calls))
    check('ohne Wetterstation wird nicht mehr nach ihr gefragt',
          'stations' not in calls, str(calls))
    api.get_home_status = lambda home_id=None: (_ for _ in ()).throw(
        RuntimeError('Netatmo API (503): Service temporarily unavailable'))
    api.update_device_status(devices)
    check('antwortet die Heizung nicht, ist das ein Ausfall',
          api.last_poll_ok is False and 'energy' in api.last_failed_parts,
          f'{api.last_poll_ok} {api.last_failed_parts}')
    sched_src = source('scheduler')
    check('der Zeitplaner benutzt die sparsame Abfrage',
          'self.netatmo_api.update_device_status(netatmo_devs)' in sched_src
          and 'self.netatmo_api.get_devices()' not in sched_src)

    print('== Der Zeitplaner fuettert den Satz ==')
    sched = source('scheduler')
    for platform in ('meross', 'vesync', 'cozytouch'):
        check(f'{platform} meldet, welche Geraete geantwortet haben',
              re.search(r"note_devices\(\s*'%s', report\.get\('answered'\)"
                        % platform, sched) is not None)
    check('netatmo meldet seine Haelften',
          re.search(r"note_parts\(\s*'netatmo'", sched) is not None)
    check('netatmo reicht das Ergebnis der Abfrage weiter',
          "getattr(self.netatmo_api, 'last_poll_ok', True)" in sched)
    check('bei Meross ist "kein einziges Geraet" ein Ausfall der Plattform',
          "if total and not report.get('devices_ok'):" in sched)
    check('die Ansage kommt aus dem Satz und kennt daher Teilausfaelle',
          'self.platform_health.update(platform_name, ok)' in sched
          and 'platform_health.announcement(' in sched)
    check('die alte Ja/Nein-Anzeige bleibt fuer die Diagnose in Takt',
          'setattr(self, f"_{platform_name}_connected"' in sched)
    check('beim Abmelden wird der Satz verworfen',
          'self.platform_health.reset()' in sched)
    body = sched.partition('def _announce_platform_state')[2].partition(
        '    def _fault_announcement_wanted')[0]
    check('die Einstellung sitzt VOR der Sprachausgabe, nicht vor dem Satz - '
          'sonst wuesste hinterher niemand mehr, was war',
          body.index('self.platform_health.update(')
          < body.index('_fault_announcement_wanted('))

    print('== Der Baum und die Statuszeile lesen ihn ==')
    dialog = source('device_dialog')
    for platform in ('meross', 'netatmo', 'vesync', 'cozytouch'):
        check(f'der {platform}-Knoten traegt den Zustand',
              re.search(r"_platform_node_label\(\s*'%s'" % platform, dialog)
              is not None)
    check('der Zustand gehoert zur Struktur - sonst bleibt die Beschriftung '
          'stehen, bis sich sonst etwas aendert',
          'def _tree_signature' in dialog
          and '_platform_node_label(name, \'\')' in dialog)
    check('die Statuszeile fasst zusammen',
          'platform_health.summary_text(' in dialog)

    print('== Der Dialog zum Nachsehen ==')
    check('es gibt eine Schaltflaeche', '_show_platform_status_dialog' in dialog)
    check('und ein Kuerzel', "keycode == ord('P')" in dialog)
    status = source('dialog_platform_status')
    check('er hat zwei Ansichten',
          'VIEW_EVENTS' in status and 'VIEW_DEVICES' in status)
    check('die Ereignisliste kommt aus dem Satz', '.events()' in status)
    check('die Geraeteliste auch', 'device_rows(' in status)
    check('Enter zeigt die Einzelheiten - eine Spalte mehr waere eine Spalte '
          'zu viel zum Vorlesen',
          '_DetailDialog' in status and 'EVT_LIST_ITEM_ACTIVATED' in status)
    check('keine Ansicht hat mehr als vier Spalten',
          max(len(re.findall(r'_\("', block))
              for block in re.findall(r'columns = \[(.*?)\]', status,
                                      re.S)) <= 4)
    check('die Liste traegt einen Namen - sonst meldet sie sich nur als '
          '"Liste"',
          'self.list_ctrl.SetName(' in status)
    check('und das Auswahlfeld auch', 'self.view_choice.SetName(' in status)
    for name, src in (('Plattform-Status', status),
                      ('Verlauf', source('dialog_history'))):
        body = src.partition('def _on_view_changed')[2].partition('    def ')[0]
        check(f'{name}: der Ansichtswechsel nimmt den Fokus nicht weg - '
              'wx.Choice meldet jede Pfeiltaste, und der Fokus sprang dann '
              'weg, bevor die Auswahl vorgelesen war',
              bool(body) and 'SetFocus' not in body)

    print('== Stoerungsmeldungen lassen sich abschalten ==')
    wanted = grab_method('scheduler', '_fault_announcement_wanted',
                         {'platform_health': ph})

    class Settings:
        notify_faults = True
        notify_fault_unreachable = True
        notify_fault_partial = True
        notify_fault_recovered = True

    cfg = Settings()
    check('voreingestellt wird alles gesagt',
          all(wanted(cfg, state) for state in (ph.OK, ph.PARTIAL, ph.DOWN)))
    cfg.notify_fault_partial = False
    check('ein Teilausfall laesst sich einzeln abschalten',
          not wanted(cfg, ph.PARTIAL) and wanted(cfg, ph.DOWN))
    cfg.notify_fault_recovered = False
    check('die Rueckkehr auch', not wanted(cfg, ph.OK))
    cfg = Settings()
    cfg.notify_faults = False
    check('der Hauptschalter schweigt ueber alles',
          not any(wanted(cfg, state)
                  for state in (ph.OK, ph.PARTIAL, ph.DOWN)))

    print('== Kurze Ausfaelle unterbrechen nicht ==')
    # 01.10.2026: sieben Ausfaelle an einem Tag, 36 bis 67 Sekunden lang,
    # jeder bei der naechsten Abfrage vorbei - und vierzehn Ansagen.
    health = ph.PlatformHealth()
    health.update('netatmo', True)
    check('die erste Beobachtung sagt nichts',
          health.announcement_due('netatmo') is None)
    health.update('netatmo', False)
    since = health.get('netatmo')['since']
    check('ein frischer Ausfall wird noch nicht gesagt',
          health.announcement_due('netatmo', now=since + 60) is None)
    check('der Baum zeigt ihn trotzdem sofort',
          ph.status_suffix(health.get('netatmo')) != '')
    health.update('netatmo', True)
    check('vorbei, bevor er gesagt wurde: auch keine Rueckkehr',
          health.announcement_due('netatmo') is None)
    check('in der Ereignisliste steht er trotzdem',
          len(health.events('netatmo')) == 3, str(len(health.events('netatmo'))))
    health.update('netatmo', False)
    since = health.get('netatmo')['since']
    check('nach der Frist wird er gesagt',
          health.announcement_due('netatmo', now=since + ph.FAULT_ANNOUNCE_GRACE)
          == ph.DOWN)
    check('und nur einmal',
          health.announcement_due('netatmo', now=since + 600) is None)
    health.update('netatmo', True)
    check('dann wird auch die Rueckkehr gesagt',
          health.announcement_due('netatmo') == ph.OK)
    health = ph.PlatformHealth()
    health.update('meross', False)
    check('ein Ausfall schon bei der Anmeldung wird nicht nachgetragen',
          health.announcement_due('meross', now=time.time() + 3600) is None)

    spoken, announce = announce_probe(ph)

    class Probe:
        notify_faults = True
        notify_fault_unreachable = True
        notify_fault_partial = True
        notify_fault_recovered = True

    probe = Probe()
    probe.platform_health = ph.PlatformHealth()
    probe._netatmo_connected = None
    probe._fault_announcement_wanted = lambda state: wanted(probe, state)
    announce(probe, 'netatmo', True, True)
    announce(probe, 'netatmo', False, True)
    announce(probe, 'netatmo', True, True)
    check('der Ablauf im Zeitplaner bleibt bei einem kurzen Ausfall still',
          spoken == [], str(spoken))

    print('== Abgeschaltet heisst still, nicht blind ==')
    spoken, announce = announce_probe(ph)

    class Sched:
        pass

    sched = Sched()
    sched.platform_health = ph.PlatformHealth()
    sched._netatmo_connected = None
    sched.notify_faults = False
    sched.notify_fault_unreachable = False
    sched.notify_fault_partial = False
    sched.notify_fault_recovered = False
    sched._fault_announcement_wanted = lambda state: wanted(sched, state)
    announce(sched, 'netatmo', True, True)
    announce(sched, 'netatmo', False, True)
    check('es wird nichts gesagt', spoken == [], str(spoken))
    check('aber der Zustand steht im Satz',
          sched.platform_health.get('netatmo')['state'] == ph.DOWN)
    check('und das Ereignis ist da - der Dialog kann es nachher zeigen',
          len(sched.platform_health.events()) == 2)

    print('== Die Verbindungsdiagnose ==')
    init = source('__init__')
    check('das Plugin haelt den Satz',
          'self.platform_health = PlatformHealth()' in init)
    check('und die Diagnose sagt, seit wann und was fehlt',
          'platform_health.status_suffix(entry)' in init)

    print('== Ein Ausfall ist kein Fehler im Log ==')
    # Ein ERROR laesst NVDA den Fehlerton spielen, wenn "Ton bei
    # protokollierten Fehlern" an ist - am 24. September achtmal an einem
    # Vormittag, jedes Mal fuer ein 503 der Netatmo-Wetterstation.
    for name in ('netatmo_api', 'vesync_api'):
        body = source(name).partition('def _log_network_error')[2]
        body = body.partition('\n    def ')[0]
        branch = body.partition('if self._is_network_error(exception):')[2]
        branch = branch.partition('\n        else:')[0]
        check(f'{name}: ein Netzwerkfehler wird als WARNING geloggt',
              'log.warning(' in branch and 'log.error(' not in branch)
    net = source('netatmo_api')
    check('Netatmo: die vier Abrufe nehmen den Weg mit der Erklaerung',
          net.count('self._log_api_exception("Netatmo ') >= 4
          and 'self._log_network_error("Netatmo ' not in net)
    check('eine 503 bekommt den Klartext und eine Warnung',
          'log.warning(f"{context}: {hint}. Response: {exception}")' in net)
    vs = source('vesync_api')
    pattern = re.search(r'return bool\(re\.search\(r"(.+?)", error_msg\)\)', vs)
    check('VeSync erkennt die eigene Meldung "VeSync API HTTP 503"',
          pattern is not None
          and re.search(pattern.group(1), 'vesync api http 503') is not None
          and re.search(pattern.group(1), 'vesync api http 404') is None)
    check('Meross: eine Anmeldung ohne Netz ist eine Warnung',
          'log.warning(f"Login failed - network error: {e}")' in source('meross_api'))

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
