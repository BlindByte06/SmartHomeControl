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
