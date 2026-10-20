# -*- coding: utf-8 -*-
"""Testet die Verlaufsanzeige: keine Fremdsprache, keine Redundanz.

Die Funktionen werden per AST aus history.py geholt, die Modus-Tabellen aus
constants.py und cozytouch_devices.py - getestet wird der ausgelieferte Code.
"""
import ast
import builtins
import io
import os
import sys
import types

# A Windows console is cp1252 by default, and this script prints readings
# with their units - the micro of ug/m3 and the subscript of CO2 among them.
# Without this the run dies on an UnicodeEncodeError halfway through and
# exits non-zero, which reads exactly like a failed check while the checks
# after it never ran at all. The build workflow sets PYTHONIOENCODING for
# the same reason; this makes the plain call in the README work too.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE = os.environ.get(
    'SHC',
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'globalPlugins', 'SmartHomeControl'))

FAILED = []


def check(name, cond, detail=''):
    print(f"  {'OK  ' if cond else 'FEHL'}   {name}" + (f'  ({detail})' if detail else ''))
    if not cond:
        FAILED.append(name)


def _dicts_from(path):
    """Nur die Dict-Zuweisungen einer Datei uebernehmen (keine Aufrufe)."""
    mod = types.ModuleType('m')
    mod.__dict__['_'] = lambda s: s
    tree = ast.parse(io.open(path, encoding='utf-8').read())
    # Dicts and tuples: the name tables are dicts, the weekday names a tuple.
    keep = [n for n in tree.body
            if isinstance(n, ast.Assign)
            and isinstance(n.value, (ast.Dict, ast.Tuple))]
    exec(compile(ast.Module(keep, []), path, 'exec'), mod.__dict__)
    return mod


def _formatting():
    """The real formatting module; without NVDA it writes a point."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'shc_formatting', os.path.join(BASE, 'formatting.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_history_funcs():
    sys.modules['shc.constants'] = _dicts_from(os.path.join(BASE, 'constants.py'))
    sys.modules['shc.cozytouch_devices'] = _dicts_from(
        os.path.join(BASE, 'cozytouch_devices.py'))
    real = builtins.__import__

    def fake(name, g=None, l=None, fromlist=(), level=0):
        if level == 1 and name in ('constants', 'cozytouch_devices'):
            return sys.modules[f'shc.{name}']
        return real(name, g, l, fromlist, level)
    builtins.__import__ = fake

    fm = _formatting()
    env = {'_': lambda s: s, 'fmt_decimal': fm.fmt_decimal,
           'fmt_number': fm.fmt_number}
    src = io.open(os.path.join(BASE, 'history.py'), encoding='utf-8').read()
    wanted = {'_format_action_text', '_detail_is_redundant', '_detail_text',
              '_temperature_detail_text'}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            exec(compile(ast.Module([node], []), 'history.py', 'exec'), env)
    return env



def test_measurements():
    """Messwert-Ansicht: Groessen konsistent, Zeitraum vorhanden."""
    import re
    hist = io.open(os.path.join(BASE, 'history.py'), encoding='utf-8').read()
    fm_mod = _formatting()
    env = {'_': lambda s: s, 'fmt_decimal': fm_mod.fmt_decimal}
    tree = ast.parse(hist)
    keep = [n for n in tree.body
            if (isinstance(n, ast.Assign) and isinstance(n.value, (ast.Dict, ast.Tuple)))
            or (isinstance(n, ast.FunctionDef)
                and n.name in ('_measurement_labels', 'format_measurement',
                               '_time_weighted_average'))]
    exec(compile(ast.Module(keep, []), 'history.py', 'exec'), env)

    print('== Alle Groessen in ALLEN Tabellen ==')
    order = set(env['MEASUREMENT_ORDER'])
    tables = {
        'THRESHOLDS': set(env['MEASUREMENT_THRESHOLDS']),
        'UNITS': set(env['MEASUREMENT_UNITS']),
        'DIGITS': set(env['_MEASUREMENT_DIGITS']),
        'LABELS': set(env['_measurement_labels']()),
    }
    for name, keys in tables.items():
        check(f'{name} deckt sich mit MEASUREMENT_ORDER',
              keys == order, f'nur hier: {keys ^ order}')

    print('== Neue Groessen sind dabei ==')
    for q in ('pm25', 'pm10', 'noise'):
        check(f'{q} aufgenommen', q in order)

    print('== Formatierung mit Einheit ==')
    fm = env['format_measurement']
    check('PM2.5', fm('pm25', 7.2) == '7 µg/m³', fm('pm25', 7.2))
    check('Lautstaerke', fm('noise', 42.4) == '42 dB', fm('noise', 42.4))

    print('== Zeitraum: summarize_measurements liefert first_ts/last_ts ==')
    check('first_ts wird gesetzt', "rec['first_ts'] = points[0][0]" in hist)
    check('last_ts wird gesetzt', "rec['last_ts'] = points[-1][0]" in hist)

    dlg = io.open(os.path.join(BASE, 'dialog_history.py'), encoding='utf-8').read()
    sched = io.open(os.path.join(BASE, 'scheduler.py'), encoding='utf-8').read()
    print('== Anzeige: Zeitraum ==')
    check('Zeitraum in der Statuszeile', '_period_text(self._current_entries)' in dlg)
    check('Zeitraum im Detaildialog', '_("Period: {period}")' in dlg)

    print('== Liste bleibt vorlesbar (wenige Spalten) ==')
    import re as _re
    block = _re.search(r'if self\._current_view\(\) == VIEW_MEASUREMENTS:(.*?)else:',
                       dlg, _re.S).group(1)
    cols = _re.findall(r'_\("([^"]+)"\), \d+', block)
    check('hoechstens vier Spalten', len(cols) <= 4, f'{len(cols)}: {cols}')
    for gone in ('Lowest', 'Highest', 'Average', 'Readings'):
        check(f'"{gone}" nicht mehr in der Liste', gone not in cols)
    for kept in ('Device', 'Quantity', 'Latest value', 'Latest reading'):
        check(f'"{kept}" bleibt', kept in cols)
    print('== Verschobenes steht in der Detailansicht ==')
    for moved in ('Lowest value: {value}', 'Highest value: {value}',
                  'Readings stored: {count}'):
        check(f'{moved!r} im Detail', f'_("{moved}")' in dlg)

    print('== Detailansicht zeilenweise navigierbar ==')
    # Kurze Meldungen (Export-Erfolg, Loeschabfrage) duerfen weiter
    # MessageDialog sein - nur die Detailansicht nicht.
    import re as _re2
    activated = _re2.search(r'def _on_item_activated.*?(?=\n    def )',
                            dlg, _re2.S).group(0)
    check('Detailansicht ohne MessageDialog', 'MessageDialog' not in activated)
    check('Detailansicht nutzt den eigenen Dialog', '_DetailDialog(' in activated)
    check('eigener Detaildialog', 'class _DetailDialog(wx.Dialog)' in dlg)
    check('schreibgeschuetztes Mehrzeilenfeld',
          'wx.TE_MULTILINE | wx.TE_READONLY' in dlg)
    check('Fokus liegt im Text, nicht auf OK', 'self.text_ctrl.SetFocus()' in dlg)
    check('Escape schliesst', 'wx.WXK_ESCAPE' in dlg)

    print('== Keine doppelte Messreihe durch Gateways ==')
    net = io.open(os.path.join(BASE, 'netatmo_api.py'), encoding='utf-8').read()
    check('Relay bekommt keine Raumtemperatur',
          net.count('if not device.is_relay:') == 2,
          f'{net.count("if not device.is_relay:")} Stellen')
    check('Rekorder ueberspringt Relays',
          "getattr(device, 'is_relay', False)" in sched)

    print('== Wassersensor: Zustandswechsel wird gemeldet ==')
    cd = io.open(os.path.join(BASE, 'change_detection.py'), encoding='utf-8').read()
    check('Erkennung vorhanden', 'def _detect_water_alarms' in cd)
    check('nur bei Wechsel', 'if previous == wet:' in cd)
    # Der erste Messwert ist nur dann still, wenn er trocken ist - ein Leck,
    # das schon beim Start besteht, wird gemeldet (wateralarmtest.py).
    check('ein erster trockener Wert bleibt still',
          'if previous is None and not wet:' in cd)
    check('Verlaufseintrag als Ereignis', "'water_detected' if wet else 'water_cleared'" in cd)
    check('Alarm mit Fehlerton', 'wx.CallAfter(_beep, BEEP_ERROR)' in cd)
    check('abschaltbar ueber die Einstellungen',
          "getattr(self, 'notify_meross_water', True)" in cd)
    check('Scheduler ruft die Erkennung auf', '_detect_water_alarms(' in sched)
    hist = io.open(os.path.join(BASE, 'history.py'), encoding='utf-8').read()
    for action in ('water_detected', 'water_cleared'):
        check(f'Anzeigetext fuer {action}', f"'{action}': _(" in hist)
    con = io.open(os.path.join(BASE, 'constants.py'), encoding='utf-8').read()
    check('Einstellung in der Konfiguration', '"notifyMerossWater"' in con)
    sp = io.open(os.path.join(BASE, 'settings_panel.py'), encoding='utf-8').read()
    check('Kontrollkaestchen im Dialog', "'notify_meross_water'" in sp)

    print('== Sensoren eines Hubs bleiben getrennt ==')
    check('Verlauf schreibt unique_id, nicht uuid',
          'def _device_key(device)' in hist
          and "getattr(device, 'unique_id', None) or device.uuid" in hist)
    check('log_action nutzt den Schluessel', '"device_uuid": _device_key(device),' in hist)
    check('log_sensor nutzt den Schluessel', 'uuid = _device_key(device)' in hist)
    check('Anzeige trennt zusaetzlich nach Namen',
          "series.setdefault((uuid, entry.get('device_name', ''), quantity)" in hist)
    check('Wassersensor loest keine Diagnose aus',
          "not getattr(device, 'is_water_sensor', False)" in sched)

    print('== Altbestand wird auf den neuen Schluessel gehoben ==')
    check('Migration vorhanden', 'def migrate_device_keys' in hist)
    check('Abbildung ueber alte UUID UND Name',
          "mapping[(uuid, name)] = unique" in hist)
    check('nur wo sich der Schluessel wirklich aendert',
          'unique and unique != uuid' in hist)
    check('beide Speicher werden umgeschrieben',
          'for store in (self._events, self._measurements):' in hist)
    check('Aenderungspunkt-Filter wird neu gesetzt',
          hist.count('self._seed_last_written()') >= 2)
    check('Scheduler ruft sie genau einmal',
          "getattr(self, '_history_keys_migrated', False)" in sched
          and 'history.migrate_device_keys(devices)' in sched)

    print('== CSV-Export: vollstaendig und lesbar ==')
    # Die Kopfzeilen werden aus Label + Einheit erzeugt, nicht mehr
    # einzeln hingeschrieben - geprueft wird also die Erzeugung.
    envh = {'_': lambda s: s}
    import ast as _a4
    for node in _a4.parse(hist).body:
        if (isinstance(node, _a4.Assign) and isinstance(node.value, (_a4.Dict, _a4.Tuple)))            or (isinstance(node, _a4.FunctionDef)
               and node.name in ('_measurement_labels', '_csv_quantity_header')):
            try:
                exec(compile(_a4.Module([node], []), 'h', 'exec'), envh)
            except Exception:
                pass
    qh = envh['_csv_quantity_header']
    check('Kopfzeile PM2.5 mit Einheit',
          qh('pm25') == 'Particulate matter PM2.5 (µg/m³)', qh('pm25'))
    check('Kopfzeile Lautstaerke mit Einheit', qh('noise') == 'Noise (dB)', qh('noise'))
    for q in envh['MEASUREMENT_ORDER']:
        check(f'Kopfzeile fuer {q} traegt eine Einheit', '(' in qh(q), qh(q))
    check('alle Groessen werden geschrieben',
          "sensor.get(q, '')" in hist and 'for q in quantities' in hist)
    check('Aktion lesbar statt Rohschluessel',
          '_format_action_text(action)' in hist)
    check('Rohschluessel bleibt als eigene Spalte', '_("Action key")' in hist)
    check('Detail wird wie in der Anzeige aufbereitet',
          "_detail_is_redundant(action)" in hist and '_detail_text(action, details)' in hist)
    # In Excel gemessen: mit Leerzeichen erkennt Excel ein Datum, formatiert
    # die Zelle und zeigt "##########", weil die Spalte zu schmal ist. Der
    # Screenreader liest genau diese Rauten. Mit dem T bleibt es Text.
    check('ISO-Zeitstempel MIT T', "strftime('%Y-%m-%dT%H:%M:%S')" in hist)
    check('kein Leerzeichen im Zeitstempel',
          "strftime('%Y-%m-%d %H:%M:%S')" not in hist)
    check('kein deutsches Datumsformat mehr',
          "strftime('%d.%m.%Y %H:%M:%S')" not in hist)

    print('== CSV enthaelt nur Spalten, die es fuellen kann ==')
    check('Spalten kommen aus den Daten',
          "quantities = [q for q in MEASUREMENT_ORDER if q in present]" in hist)
    check('Aktionsspalten nur bei Ereignissen', 'if has_actions:' in hist)
    check('Typ-Spalte nur bei gemischtem Export',
          'if has_actions and has_sensors:' in hist)
    check('keine feste Spaltenliste mehr',
          '_("Temperature (°C)"), _("Humidity (%)")' not in hist)
    check('Kopfzeile je Groesse aus den Tabellen',
          'def _csv_quantity_header(quantity)' in hist)
    # Gegenprobe: reine Ereignisse duerfen keine Messwertspalte bekommen
    import ast as _a3
    env3 = {'_': lambda s: s}
    for node in _a3.parse(hist).body:
        if isinstance(node, _a3.Assign) and isinstance(node.value, (_a3.Tuple, _a3.List)):
            try: exec(compile(_a3.Module([node], []), 'h', 'exec'), env3)
            except Exception: pass
    ereignisse = [{'event_type': 'action'}, {'event_type': 'action'}]
    present = {q for e in ereignisse for q in (e.get('sensor_data') or {})}
    check('Ereignis-Export ohne Messwertspalten', present == set())
    messwerte = [{'event_type': 'sensor', 'sensor_data': {'temperature': 21.0}}]
    present2 = {q for e in messwerte for q in (e.get('sensor_data') or {})}
    check('Messwert-Export nur mit vorkommenden Groessen',
          present2 == {'temperature'})

    print('== CSV folgt dem Gebietsschema ==')
    check('Dezimaltrennzeichen aus dem Gebietsschema',
          "locale.localeconv().get('decimal_point')" in hist)
    check('Listentrennzeichen passend dazu',
          "delimiter = ';' if decimal_point == ',' else ','" in hist)
    check('kein hartkodiertes Semikolon mehr', "delimiter=';'" not in hist)
    check('Zahlen laufen durch die Aufbereitung',
          "_csv_number(sensor.get(q, ''), decimal_point)" in hist)
    env2 = {}
    import ast as _ast
    for node in _ast.parse(hist).body:
        if (isinstance(node, _ast.FunctionDef)
                and node.name in ('_csv_number', '_csv_safe_text')):
            exec(compile(_ast.Module([node], []), 'h', 'exec'), env2)
    cn = env2['_csv_number']
    check('28.5 wird zu 28,5 (sonst Datum "28. Mai")', cn(28.5, ',') == '28,5', cn(28.5, ','))
    check('ganze Zahl bleibt', cn(812, ',') == '812')
    check('leer bleibt leer', cn('', ',') == '' and cn(None, ',') == '')
    check('Punkt-Gebietsschema unveraendert', cn(28.5, '.') == '28.5')

    print('== Keine Formel kommt durch den Export ==')
    # Die Freitextspalten waren entschaerft, die Zahlenspalte verliess sich
    # auf eine Pruefung in einer anderen Funktion. Ein Export wird
    # weitergegeben; eine Zelle "=cmd|..." waere dann eine lebende Formel
    # in der Tabelle dessen, der sie oeffnet.
    safe = env2.get('_csv_safe_text')
    check('_csv_safe_text gibt es', callable(safe))
    GEFAEHRLICH = ('=', '+', '-', '@', '\t', '\r')
    ANGRIFFE = ['=HYPERLINK("http://x","k")', "=cmd|'/c calc'!A1", '+1+1',
                '@SUM(1:9)', '\tTab', '\rCR']
    if callable(safe):
        for roh in ANGRIFFE:
            aus = safe(roh)
            check(f'Freitext entschaerft: {roh[:20]!r}',
                  bool(aus) and aus[0] not in GEFAEHRLICH, repr(aus))
    for roh in ANGRIFFE:
        aus = cn(roh, ',')
        check(f'Zahlenspalte entschaerft: {roh[:20]!r}',
              bool(aus) and aus[0] not in GEFAEHRLICH, repr(aus))
    # Echte Zahlen muessen Zahlen bleiben, sonst rechnet die Tabelle nicht.
    for roh, erwartet in ((22.5, '22,5'), (0, '0'), (-1, '-1'),
                          ('28.5', '28,5'), ('-3.5', '-3,5')):
        check(f'Zahl bleibt Zahl: {roh!r}', cn(roh, ',') == erwartet,
              repr(cn(roh, ',')))
    check('die Regel steht an genau einer Stelle',
          'return _csv_safe_text(value)' in hist,
          'sonst driften Freitext- und Zahlenspalte auseinander')
    check('Dateiname nennt die Ansicht',
          '_("smart-home-readings.csv")' in dlg and '_("smart-home-events.csv")' in dlg)
    check('kein hartkodierter deutscher Dateiname',
          'smart_home_verlauf.csv' not in dlg)

    print('== Wortwahl der Herkunft ==')
    check('"you" statt "me"', '_("you")' in hist and '_("me")' not in hist)
    check('extern bleibt', '_("external")' in hist)
    check('automatisch bleibt', '_("automatic")' in hist)

    print('== Keine deutschen Texte mehr im Code ==')
    import glob as _g, ast as _a
    DE_WORDS = ('fehlgeschlagen', 'aufgerufen', 'Starte ', 'Initialisierung',
                'Verlauf (', 'Anfrage', 'abgebrochen (Timeout)')
    reste = []
    for p in sorted(_g.glob(os.path.join(BASE, '*.py'))):
        src = io.open(p, encoding='utf-8').read()
        for node in _a.walk(_a.parse(src, p)):
            if isinstance(node, _a.Constant) and isinstance(node.value, str):
                for w in DE_WORDS:
                    if w in node.value:
                        reste.append(f'{os.path.basename(p)}:{node.lineno} {node.value[:40]!r}')
    check('keine deutschen Meldungen', not reste, '; '.join(reste[:3]))

    print('== Weisston-Schluessel auf Englisch ==')
    con = io.open(os.path.join(BASE, 'constants.py'), encoding='utf-8').read()
    check("kanonisch 'daylight'/'cool'",
          "'daylight': _(" in con and "'cool': _(" in con)
    check('kein deutscher Schluessel mehr',
          "'tageslicht': _(" not in con and "'kalt': _(" not in con)
    check('Altlast-Tabelle vorhanden', 'MEROSS_WHITE_PRESET_LEGACY' in con)
    dev_dlg = io.open(os.path.join(BASE, 'device_dialog.py'), encoding='utf-8').read()
    check('Dialog schreibt die englischen Schluessel',
          "{0: 'warm', 1: 'daylight', 2: 'cool'}" in dev_dlg)
    mer = io.open(os.path.join(BASE, 'meross_api.py'), encoding='utf-8').read()
    check('API nimmt alte deutsche Namen weiter an',
          '"tageslicht": 50' in mer and '"kalt": 100' in mer)
    check('Verlauf uebersetzt alte Schluessel',
          'MEROSS_WHITE_PRESET_LEGACY.get(value)' in hist)

    print('== Kein periodisches INFO-Rauschen ==')
    net = io.open(os.path.join(BASE, 'netatmo_api.py'), encoding='utf-8').read()
    check('Wetterstationszahl nur noch debug',
          'log.debug(f"Netatmo: {len(devices)} weather station' in net)
    check('Energiegeraetezahl nur noch debug',
          'log.debug(f"Netatmo: {energy_count} energy device' in net)

    print('== MS130 ohne Push-Ereignis ==')
    md = io.open(os.path.join(BASE, 'meross_devices.py'), encoding='utf-8').read()
    check('Temperatur faellt auf die allgemeinen Pfade zurueck',
          md.count("value = self._get_ms130_value('temperature'") == 1
          and 'if value is not None:' in md)
    check('Feuchte ebenso',
          md.count("value = self._get_ms130_value('humidity'") == 1)
    check('kein vorzeitiges return mehr',
          "return self._get_ms130_value(" not in md)
    check('stumme Sensoren werden im Log benannt',
          'Sensors without a reading in this pass' in sched)

    print('== Rekorder liest Getter UND Attribute ==')
    for q in ('pm25', 'pm10', 'noise'):
        check(f'{q} wird erfasst', f"'{q}'," in sched)
    # Frueher stand hier, dass 'air_quality_value' als Quelle auftaucht.
    # Genau dieser Zugriff war der Fehler: das Attribut behaelt fuer die
    # Anzeige den letzten guten Wert, und _read_sensor nimmt den ersten
    # Namen, der etwas liefert - der Ausfall (-1) landete so als Messwert
    # im Verlauf. PM2.5 kommt jetzt ueber den Getter, der einen Ausfall
    # zurueckhaelt; siehe sensortest.py. Dass auch reine Attribute gelesen
    # werden, zeigen pm10 und die Ventilator-Temperatur.
    check('PM2.5 nur ueber den Getter', "('get_pm25',)" in sched)
    check('kein Rohattribut als Rueckfallebene',
          "'air_quality_value'" not in sched)
    check('reine Attribute weiterhin beruecksichtigt', "('pm10',)" in sched)
    check('Ventilator-Temperatur beruecksichtigt',
          "('get_temperature', 'temperature')" in sched)


# ------------------------------------------- Aenderungen von aussen ---------
def test_external_changes():
    """Was ausserhalb des Add-ons passiert, muss auch in die Chronik.

    Lange tat das nur Meross - dessen Aenderungen kommen als Push-Nachricht,
    und dieser Weg schrieb mit. Die drei anderen Plattformen erkennen ihre
    Aenderungen beim Abfragen, sagten sie an und liessen sie dann fallen. In
    einer echten Chronik standen 206 externe Meross-Eintraege und null von
    VeSync, Netatmo und Cozytouch.
    """
    print('== Aenderungen von aussen landen in der Chronik ==')
    import ast
    import textwrap

    src = io.open(os.path.join(BASE, 'change_detection.py'),
                  encoding='utf-8').read()
    tree = ast.parse(src)

    # Die Tabelle und die Methode aus der echten Quelle holen.
    table_src = None
    method_src = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and node.targets
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == '_EXTERNAL_ACTIONS'):
            table_src = ast.get_source_segment(src, node)
        if (isinstance(node, ast.FunctionDef)
                and node.name == '_log_external_state_changes'):
            method_src = textwrap.dedent(ast.get_source_segment(src, node))
    check('die Tabelle ist da', table_src is not None)
    check('die Methode ist da', method_src is not None)
    if not (table_src and method_src):
        return

    written = []

    class History:
        def log_external_action(self, uuid, name, platform, action, detail=""):
            written.append((platform, action, detail))

    class Device:
        uuid = 'ct-1'
        unique_id = 'ct-1'
        name = 'Revolution'

    class Log:
        def _noop(self, *a, **k):
            pass
        debug = info = warning = error = _noop

    # Die Methode holt sich die Chronik ueber "from .history import ..." -
    # dafuer muss die Paketkette in sys.modules stehen.
    import types
    package = sys.modules.setdefault(
        'globalPlugins', types.ModuleType('globalPlugins'))
    package.__path__ = []
    addon = sys.modules.setdefault(
        'globalPlugins.SmartHomeControl',
        types.ModuleType('globalPlugins.SmartHomeControl'))
    addon.__path__ = []
    stub = types.ModuleType('globalPlugins.SmartHomeControl.history')
    stub.get_history = lambda: History()
    sys.modules['globalPlugins.SmartHomeControl.history'] = stub

    env = {'log': Log(),
           '__name__': 'globalPlugins.SmartHomeControl.change_detection',
           '__package__': 'globalPlugins.SmartHomeControl'}
    exec(compile(ast.parse(table_src), 'table', 'exec'), env)
    exec(compile(ast.parse(method_src), 'method', 'exec'), env)

    class Mixin:
        pass

    Mixin._log_external_state_changes = env['_log_external_state_changes']
    mixin = Mixin()

    def run(platform, prev, new):
        written.clear()
        Mixin._log_external_state_changes(mixin, platform, Device(), prev, new)
        return list(written)

    got = run('cozytouch',
              {'boost': False, 'is_on': True, 'away': False,
               'target_temp': 55, 'mode': 'auto'},
              {'boost': True, 'is_on': True, 'away': False,
               'target_temp': 55, 'mode': 'auto'})
    check('Boost ein wird geschrieben',
          got == [('cozytouch', 'boost_on', '')], str(got))

    got = run('cozytouch',
              {'boost': True, 'is_on': True, 'away': False,
               'target_temp': 55, 'mode': 'auto'},
              {'boost': False, 'is_on': True, 'away': False,
               'target_temp': 58, 'mode': 'auto'})
    check('Boost aus und neue Solltemperatur, beides',
          sorted(a for _p, a, _d in got) == ['boost_off', 'set_target_temp'],
          str(got))
    check('die Temperatur steht als Zahl dabei, nicht als Satz',
          ('cozytouch', 'set_target_temp', '58') in got, str(got))

    got = run('cozytouch',
              {'boost': False, 'is_on': True, 'away': False,
               'target_temp': 55, 'mode': 'auto'},
              {'boost': False, 'is_on': True, 'away': False,
               'target_temp': 55, 'mode': 'auto'})
    check('ohne Aenderung wird nichts geschrieben', got == [], str(got))

    got = run('cozytouch',
              {'boost': False, 'target_temp': 55},
              {'boost': None, 'target_temp': None})
    check('ein fehlender Wert schreibt nichts', got == [], str(got))

    got = run('vesync',
              {'is_on': True, 'mute_on': False, 'fan_level': 1},
              {'is_on': False, 'mute_on': True, 'fan_level': 3})
    actions = sorted(a for _p, a, _d in got)
    check('VeSync: Aus, stumm und Stufe',
          actions == ['mute_on', 'set_fan_speed', 'toggle_off'], str(actions))

    got = run('netatmo',
              {'setpoint': 20.0, 'mode': 'schedule', 'zone_name': 'Tag'},
              {'setpoint': 21.5, 'mode': 'manual', 'zone_name': 'Tag'})
    actions = sorted(a for _p, a, _d in got)
    check('Netatmo: Solltemperatur und Modus',
          actions == ['set_temp', 'therm_mode'], str(actions))

    # Und die Verdrahtung: alle drei Schleifen rufen es auch wirklich auf.
    for platform in ('netatmo', 'vesync', 'cozytouch'):
        check(f'die {platform}-Schleife ruft es auf',
              f"_log_external_state_changes('{platform}'" in src
              or f"_log_external_state_changes(\n                        '{platform}'" in src
              or f"'{platform}', dev," in src)

    # Jede Aktion der Tabelle muss die Anzeige auch benennen koennen.
    hist = io.open(os.path.join(BASE, 'history.py'), encoding='utf-8').read()
    unknown = []
    for entries in env['_EXTERNAL_ACTIONS'].values():
        for _field, keys, with_value in entries:
            for key in ((keys,) if with_value else keys):
                if f"'{key}':" not in hist:
                    unknown.append(key)
    check('jede Aktion hat eine Beschriftung in der Anzeige',
          not unknown, ', '.join(sorted(set(unknown))))


# --------------------------------------- Zeitprogramm in der Chronik --------
def test_schedule_detail():
    """Ein gespeichertes Zeitprogramm muss lesbar herauskommen.

    Gespeichert wird neutral - Tagesnummern und Minuten -, damit ein Eintrag
    nicht in der Sprache erstarrt, in der er entstanden ist. Angezeigt werden
    muss er dann aber auch: roh sah er aus wie
    "[0, 5] [[440,1070],[0,0],[0,0]]".
    """
    print('== Das Zeitprogramm in der Chronik ==')
    import ast
    import textwrap
    import types

    src = io.open(os.path.join(BASE, 'history.py'), encoding='utf-8').read()
    fn = None
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.FunctionDef)
                and node.name == '_schedule_detail_text'):
            fn = textwrap.dedent(ast.get_source_segment(src, node))
    check('der Formatierer ist da', fn is not None)
    if fn is None:
        return

    package = sys.modules.setdefault(
        'globalPlugins', types.ModuleType('globalPlugins'))
    package.__path__ = []
    addon = sys.modules.setdefault(
        'globalPlugins.SmartHomeControl',
        types.ModuleType('globalPlugins.SmartHomeControl'))
    addon.__path__ = []
    constants = types.ModuleType('globalPlugins.SmartHomeControl.constants')
    constants.WEEKDAY_NAMES = ('Monday', 'Tuesday', 'Wednesday', 'Thursday',
                               'Friday', 'Saturday', 'Sunday')
    sys.modules['globalPlugins.SmartHomeControl.constants'] = constants

    env = {'_': lambda text: text,
           '__name__': 'globalPlugins.SmartHomeControl.history',
           '__package__': 'globalPlugins.SmartHomeControl'}
    exec(compile(ast.parse(fn), 'schedule', 'exec'), env)
    render = env['_schedule_detail_text']

    check('ein Tag und ein Fenster',
          render('0,5 [[440,1070],[0,0],[0,0]]')
          == 'Monday, Saturday: 07:20-17:50',
          repr(render('0,5 [[440,1070],[0,0],[0,0]]')))
    check('zwei Fenster an einem Tag',
          render('1 [[360,600],[900,1200],[0,0]]')
          == 'Tuesday: 06:00-10:00, 15:00-20:00',
          repr(render('1 [[360,600],[900,1200],[0,0]]')))
    check('ein geleerter Tag sagt das auch',
          render('2 [[0,0],[0,0],[0,0]]') == 'Wednesday: no heating times',
          repr(render('2 [[0,0],[0,0],[0,0]]')))
    # Die ersten Eintraege trugen die Tage als Python-Liste, mit Leerzeichen
    # darin - an der ersten Luecke zu trennen zerriss sie.
    check('auch die erste Schreibweise wird noch gelesen',
          render('[0, 5] [[440,1070],[0,0],[0,0]]')
          == 'Monday, Saturday: 07:20-17:50',
          repr(render('[0, 5] [[440,1070],[0,0],[0,0]]')))
    check('fertiger Text von frueher bleibt unangetastet',
          render('Boost ein') is None)
    check('Unsinn bringt nichts durcheinander',
          render('') is None and render('7 kaputt') is None)

    # Und die Verdrahtung: der Zweig muss auch aufgerufen werden.
    check('die Anzeige benutzt ihn', "if action == 'set_schedule':" in src)
    dialog = io.open(os.path.join(BASE, 'dialog_cozytouch.py'),
                     encoding='utf-8').read()
    check('gespeichert wird weiterhin neutral',
          "','.join(str(d) for d in result['done'])" in dialog)


def _detection_mixin(names, clock, extra=None):
    """Die genannten Methoden von _ChangeDetectionMixin aus der echten
    Quelle, samt Tabelle und Toleranz, mit einer Uhr, die der Test stellt.
    ``extra`` ergaenzt die Namen, die die Methoden sonst importieren."""
    import ast
    import textwrap

    src = io.open(os.path.join(BASE, 'change_detection.py'),
                  encoding='utf-8').read()
    tree = ast.parse(src)
    lines = src.splitlines()
    module_parts, methods = [], []
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ('_EXTERNAL_ACTIONS',
                                           'BOOST_END_TOLERANCE')):
            module_parts.append(ast.get_source_segment(src, node))
        if isinstance(node, ast.ClassDef) and node.name == '_ChangeDetectionMixin':
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name in names:
                    first = min([d.lineno for d in item.decorator_list]
                                + [item.lineno])
                    methods.append(textwrap.dedent(
                        "\n".join(lines[first - 1:item.end_lineno])))
    missing = set(names) - {m.split('def ', 1)[1].split('(', 1)[0]
                            for m in methods}
    class_src = "class Mixin:\n" + "\n".join(
        textwrap.indent(m, '    ') for m in methods)

    class Log:
        def _noop(self, *a, **k):
            pass
        debug = info = warning = error = _noop

    env = {'log': Log(), 'time': clock, '_': lambda t: t,
           '__name__': 'globalPlugins.SmartHomeControl.change_detection',
           '__package__': 'globalPlugins.SmartHomeControl'}
    env.update(extra or {})
    for part in module_parts:
        exec(compile(part, 'module', 'exec'), env)
    exec(compile(class_src, 'mixin', 'exec'), env)
    return env['Mixin'], missing


def test_automatic_origin():
    """Was ein Geraet von sich aus tut, steht als "automatisch" im Verlauf.

    Am 02.10.2026 lief ein Boost, hier eingeschaltet und gleich danach auf
    65 Minuten gestellt, nach 65 Minuten ab - und stand als "Boost
    ausgeschaltet, extern" im Verlauf, wie alle acht Boosts seit dem 09.09.
    Die Dauer wurde beim Ende vom Geraet gelesen, und dort stand sie nicht
    mehr: gleich nach dem Einschalten las die Zeile "keine Zeitbegrenzung
    gesetzt". Gespielt wird genau dieser Ablauf, mit der Uhr des Tests und
    der echten Erkennung.
    """
    print('== "automatisch": Boost-Ende und Heizplan ==')
    import types

    class Clock:
        now = 0.0

        def time(self):
            return self.now

    clock = Clock()
    names = ('_log_external_state_changes', '_cozytouch_boost_runs',
             '_note_cozytouch_boost', '_note_local_cozytouch_boost',
             '_cozytouch_boost_from_history',
             '_cozytouch_boost_ran_out', '_record_local_cozytouch_action',
             '_is_recent_local_cozytouch_action', '_snapshot_cozytouch_state',
             '_expect_cozytouch', '_same_cozytouch_value',
             '_check_cozytouch_expectations', '_accept_local_change',
             '_detect_cozytouch_changes', '_normalize_end_time',
             '_record_local_netatmo_action', '_is_recent_local_netatmo_action',
             '_snapshot_netatmo_state', '_netatmo_change_by_schedule',
             '_detect_netatmo_changes',
             '_record_local_vesync_action', '_is_recent_local_vesync_action',
             '_snapshot_vesync_state', '_announce_fryer_temp_verdict',
             '_detect_vesync_changes')
    spoken = []

    class Wx:
        @staticmethod
        def CallAfter(func, *args):
            func(*args)

    class Ui:
        @staticmethod
        def message(text):
            spoken.append(text)

    Mixin, missing = _detection_mixin(names, clock, extra={
        'wx': Wx, 'ui': Ui, '_beep': lambda *a: None,
        'BEEP_ACTION': 1, 'BEEP_ERROR': 2, 'BEEP_ON': 3, 'BEEP_OFF': 4,
        'BEEP_EXTERNAL_CHANGE': 5})
    check('alle Teile der Erkennung gefunden', not missing, str(missing))
    if missing:
        return

    written = []
    # Was der Verlauf schon enthaelt - fuer die Erkennung nach einem Neustart.
    stored = []

    class History:
        def log_action(self, device, action, detail="", source=None):
            written.append((source, action, detail))

        def log_external_action(self, uuid, name, platform, action, detail=""):
            written.append(('extern', action, detail))

        def get_entries(self, device_uuid=None, event_type=None,
                        max_entries=200, since_hours=None, platform=None):
            found = [e for e in stored
                     if device_uuid is None or e['device_uuid'] == device_uuid]
            if since_hours:
                found = [e for e in found
                         if e['timestamp'] >= clock.now - since_hours * 3600]
            found.sort(key=lambda e: e['timestamp'], reverse=True)
            return found[:max_entries]

    package = sys.modules.setdefault(
        'globalPlugins', types.ModuleType('globalPlugins'))
    package.__path__ = []
    addon = sys.modules.setdefault(
        'globalPlugins.SmartHomeControl',
        types.ModuleType('globalPlugins.SmartHomeControl'))
    addon.__path__ = []
    hist = types.ModuleType('globalPlugins.SmartHomeControl.history')
    hist.get_history = lambda: History()
    hist.SOURCE_SYSTEM = 'system'
    sys.modules['globalPlugins.SmartHomeControl.history'] = hist
    devices_stub = types.ModuleType(
        'globalPlugins.SmartHomeControl.cozytouch_devices')
    devices_stub.COZYTOUCH_HEATING_MODE_NAMES = {}
    devices_stub._fmt_temp = str
    sys.modules['globalPlugins.SmartHomeControl.cozytouch_devices'] = devices_stub
    # load_history_funcs biegt "from .cozytouch_devices import" auf seine
    # Tabellen um - die brauchen hier noch die Temperaturformatierung.
    hooked = sys.modules.get('shc.cozytouch_devices')
    if hooked is not None and not hasattr(hooked, '_fmt_temp'):
        hooked._fmt_temp = str

    class Heater:
        uuid = unique_id = 'ct-1'
        name = 'Revolution'
        target_temperature = 55.0
        mode_value = '0'
        is_on = True
        away_on = False

        def __init__(self):
            self.boost_on = False
            self.boost_total_time = None

    def plugin():
        m = Mixin()
        m._previous_cozytouch_states = {}
        m._recent_cozytouch_actions = {}
        m._cozytouch_expected = {}
        m._previous_netatmo_therm_states = {}
        m._previous_vesync_states = {}
        m._recent_vesync_actions = {}
        m.announce_external_changes = False
        return m

    def poll(m, dev, at):
        clock.now = at
        m._detect_cozytouch_changes([dev])

    # Der Ablauf vom 02.10.: 15:55:54 erste Abfrage, 15:56:28 Enter auf
    # Boost, 15:56:34 bestaetigt, 15:56:52 Dauer 65 Minuten, 17:01:55 aus.
    m, dev = plugin(), Heater()
    poll(m, dev, 0)
    clock.now = 34
    m._record_local_cozytouch_action(dev.uuid)
    clock.now = 40
    dev.boost_on = True
    m._note_local_cozytouch_boost(dev.uuid, on=True)
    poll(m, dev, 50)
    clock.now = 58
    m._record_local_cozytouch_action(dev.uuid)
    m._note_local_cozytouch_boost(dev.uuid, minutes=65)
    # Die Cloud meldet die Dauer nie zurueck: Capability 232 ist nicht
    # geschuetzt, jede Abfrage ueberschreibt den eben gesetzten Wert, bevor
    # die Erkennung ihn liest.
    poll(m, dev, 73)
    t = 73
    while t < 40 + 65 * 60:
        t += 30
        poll(m, dev, t)
    written.clear()
    dev.boost_on = False
    poll(m, dev, 40 + 65 * 60 + 21)
    check('der Boost vom 02.10. lief ab: "Boost beendet", automatisch',
          written == [('system', 'boost_ended', '')], str(written))

    # Aus der App gestartet, die Cloud nennt 30 Minuten, solange er laeuft.
    m, dev = plugin(), Heater()
    poll(m, dev, 0)
    dev.boost_on, dev.boost_total_time = True, 30.0
    poll(m, dev, 30)
    dev.boost_total_time = None
    poll(m, dev, 60)
    written.clear()
    dev.boost_on = False
    poll(m, dev, 30 + 30 * 60 + 25)
    check('aus der App, Dauer nur waehrend des Laufs gemeldet: automatisch',
          written == [('system', 'boost_ended', '')], str(written))

    # Nach zehn von 65 Minuten hat ihn jemand ausgeschaltet.
    m, dev = plugin(), Heater()
    poll(m, dev, 0)
    clock.now = 10
    dev.boost_on = True
    m._note_local_cozytouch_boost(dev.uuid, on=True, minutes=65)
    poll(m, dev, 45)
    written.clear()
    dev.boost_on = False
    poll(m, dev, 10 + 10 * 60)
    check('vorzeitig ausgeschaltet bleibt extern',
          written == [('extern', 'boost_off', '')], str(written))

    # Lief schon bei der Anmeldung: wann er begann, weiss niemand.
    m, dev = plugin(), Heater()
    dev.boost_on, dev.boost_total_time = True, 60.0
    poll(m, dev, 0)
    written.clear()
    dev.boost_on = False
    poll(m, dev, 70 * 60)
    check('Beginn unbekannt: extern, nichts geraten',
          written == [('extern', 'boost_off', '')], str(written))

    # Eine Dauer, gesetzt ohne laufenden Boost, gilt nicht fuer den naechsten.
    m, dev = plugin(), Heater()
    poll(m, dev, 0)
    m._note_local_cozytouch_boost(dev.uuid, minutes=30)
    dev.boost_on = True
    poll(m, dev, 30)
    written.clear()
    dev.boost_on = False
    poll(m, dev, 30 + 45 * 60)
    check('eine Dauer ohne laufenden Boost wird nicht vorgemerkt',
          written == [('extern', 'boost_off', '')], str(written))

    # 18.10.: 13:18:18 Boost ein, 13:19:45 Dauer 120 Minuten, 15:19:48 aus -
    # als "extern" verbucht. Beginn und Dauer lebten nur im Speicher, ein
    # NVDA-Neustart dazwischen nahm sie mit. Der Verlauf hat beides.
    def entry(at, action, details=''):
        return {'timestamp': at, 'device_uuid': 'ct-1', 'action': action,
                'details': details}

    stored[:] = [entry(100, 'boost_on'),
                 entry(143, 'set_boost_duration', '115'),
                 entry(187, 'set_boost_duration', '120')]
    m, dev = plugin(), Heater()  # nach dem Neustart
    dev.boost_on = True
    poll(m, dev, 3600)
    poll(m, dev, 3630)
    written.clear()
    dev.boost_on = False
    poll(m, dev, 187 + 120 * 60 + 3)
    check('Neustart waehrend des Boosts: das Ende ist trotzdem "automatisch"',
          written == [('system', 'boost_ended', '')], str(written))

    stored[:] = [entry(0, 'boost_on'), entry(20 * 60, 'boost_off')]
    m, dev = plugin(), Heater()
    dev.boost_on = True
    poll(m, dev, 3600)
    poll(m, dev, 3630)
    written.clear()
    dev.boost_on = False
    poll(m, dev, 3 * 3600)
    check('ein im Verlauf schon beendeter Boost wird nicht wiederverwendet',
          written == [('extern', 'boost_off', '')], str(written))

    # Ein Boost von gestern, dessen Ende NVDA verpasst hat: der jetzt
    # laufende ist ein anderer.
    stored[:] = [entry(100, 'boost_on'),
                 entry(150, 'set_boost_duration', '30')]
    m, dev = plugin(), Heater()
    dev.boost_on = True
    poll(m, dev, 20 * 3600)
    poll(m, dev, 20 * 3600 + 30)
    written.clear()
    dev.boost_on = False
    poll(m, dev, 20 * 3600 + 10 * 60)
    check('ein laengst abgelaufener Boost aus dem Verlauf zaehlt nicht',
          written == [('extern', 'boost_off', '')], str(written))
    stored.clear()

    # Gesendet, die Cloud bestaetigt nicht, das Geraet nimmt ihn spaeter
    # doch an. Frueher: "Boost konnte nicht umgeschaltet werden", und der
    # spaeter laufende Boost stand als "extern" im Verlauf.
    m, dev = plugin(), Heater()
    poll(m, dev, 0)
    clock.now = 10
    m._record_local_cozytouch_action(dev.uuid)
    m._expect_cozytouch(dev, 'boost', True, confirm='Revolution: boost on',
                        fail='Revolution: Boost could not be toggled',
                        history_action='boost_on')
    poll(m, dev, 30)
    written.clear()
    spoken.clear()
    dev.boost_on = True
    poll(m, dev, 75)  # das Fenster fuer eigene Aenderungen ist vorbei
    check('spaet angekommener Boost: als eigener im Verlauf, nicht extern',
          written == [(None, 'boost_on', '')], str(written))
    check('und bestaetigt angesagt', spoken == ['Revolution: boost on'],
          str(spoken))
    written.clear()
    dev.boost_on = False
    poll(m, dev, 75 + 30 * 60)
    check('sein Ende danach ist wieder eine normale Aenderung',
          written == [('extern', 'boost_off', '')], str(written))

    m, dev = plugin(), Heater()
    poll(m, dev, 0)
    m._expect_cozytouch(dev, 'mode', '3', confirm='Revolution: mode Eco+',
                        fail='Revolution: Mode could not be set',
                        history_action='set_mode', detail='3')
    spoken.clear()
    poll(m, dev, 300)
    check('noch nicht da: still', spoken == [], str(spoken))
    poll(m, dev, 700)
    check('nie angekommen: nach zehn Minuten gesagt',
          spoken == ['Revolution: Mode could not be set'], str(spoken))

    # Eine bestaetigte eigene Aenderung wird die neue Vergleichsbasis: die
    # naechste Abfrage kann nach dem Fenster von 30 s kommen.
    m, dev = plugin(), Heater()
    poll(m, dev, 0)
    clock.now = 20
    dev.target_temperature = 50.0
    m._record_local_cozytouch_action(dev.uuid)
    m._accept_local_change('cozytouch', dev)
    written.clear()
    poll(m, dev, 90)
    check('Cozytouch: eigene Temperatur nach dem Fenster nicht extern',
          written == [], str(written))

    # VeSync am 18.10.: 18:48:36 Abfrage (Stufe 2), 18:48:51 Stufe 1 hier
    # gestellt, naechste Abfrage 18:49:38 - und "Stufe 1, extern" stand im
    # Verlauf neben "Stufe 1, du".
    # load_history_funcs leitet "from .constants import" auf seine Tabellen
    # um; dort fehlt, was keine Tabelle ist.
    consts = sys.modules.get('shc.constants') or sys.modules.setdefault(
        'globalPlugins.SmartHomeControl.constants',
        types.ModuleType('globalPlugins.SmartHomeControl.constants'))
    for name, value in (('VESYNC_PURIFIER_MODE_NAMES', {}),
                        ('VESYNC_FAN_MODE_NAMES', {}),
                        ('VESYNC_AIR_QUALITY_NAMES', {}),
                        ('VESYNC_NIGHTLIGHT_MODE_NAMES', {}),
                        ('VESYNC_AUTO_PREFERENCE_NAMES', {}),
                        ('VESYNC_PURIFIER_LEVEL_LABELS_3', {}),
                        ('VESYNC_FILTER_WARN_THRESHOLD', 10),
                        ('VESYNC_FRYER_COOK_ANNOUNCEMENTS', {})):
        if not hasattr(consts, name):
            setattr(consts, name, value)

    class VeSyncPurifier:
        uuid = unique_id = 'vs-1'
        name = 'Luftreiniger Philipp Zimmer'
        is_on = True
        is_offline = False
        mode = 'manual'
        fan_level = 2
        air_quality = 1
        filter_life = 92

    def vesync(m, dev, at):
        clock.now = at
        m._detect_vesync_changes([dev])

    m, purifier = plugin(), VeSyncPurifier()
    vesync(m, purifier, 0)          # 18:48:36
    clock.now = 15                  # 18:48:51
    m._record_local_vesync_action(purifier.uuid)
    purifier.fan_level = 1
    m._record_local_vesync_action(purifier.uuid)
    m._accept_local_change('vesync', purifier)
    written.clear()
    vesync(m, purifier, 62)         # 18:49:38
    check('VeSync: die eigene Stufe kommt nicht als "extern" zurueck',
          written == [], str(written))
    purifier.fan_level = 3
    vesync(m, purifier, 122)
    check('VeSync: eine Stufe von anderswo bleibt extern',
          written == [('extern', 'set_fan_speed', '3')], str(written))

    # Netatmo um 22:00: der Wochenplan wechselt in die Nachtzone - an
    # einem Abend in einer Abfrage, am 01.10. auf zwei verteilt.
    class Thermostat:
        uuid = unique_id = 'na-1'
        name = 'Thermostat'
        is_thermostat = True
        _therm_setpoint_end_time = None
        _boiler_status = None
        _anticipating = None
        _open_window = None

        def __init__(self, setpoint, mode, zone):
            self._therm_setpoint = setpoint
            self._therm_setpoint_mode = mode
            self._schedule_zone_raw = self._schedule_zone_name = zone

    def netatmo(m, therm, at):
        clock.now = at
        m._detect_netatmo_changes([therm])

    m = plugin()
    netatmo(m, Thermostat(20.0, 'schedule', 'Comfort'), 0)
    written.clear()
    netatmo(m, Thermostat(18.0, 'schedule', 'Night'), 30)
    check('22:00 in einer Abfrage: Zone und Temperatur, beide automatisch',
          sorted(written) == [('system', 'schedule_zone', 'Night'),
                              ('system', 'set_temp', '18.0')], str(written))

    m = plugin()
    netatmo(m, Thermostat(20.0, 'schedule', 'Comfort'), 0)
    written.clear()
    netatmo(m, Thermostat(20.0, 'schedule', 'Night'), 30)
    netatmo(m, Thermostat(18.0, 'schedule', 'Night'), 60)
    check('22:00 auf zwei Abfragen verteilt: beide automatisch',
          written == [('system', 'schedule_zone', 'Night'),
                      ('system', 'set_temp', '18.0')], str(written))

    m = plugin()
    netatmo(m, Thermostat(20.0, 'schedule', 'Comfort'), 0)
    written.clear()
    netatmo(m, Thermostat(21.5, 'manual', 'Comfort'), 30)
    check('in der App von Hand gestellt bleibt extern',
          sorted(written) == [('extern', 'set_temp', '21.5'),
                              ('extern', 'therm_mode', 'manual')], str(written))

    # Die Verdrahtung: der Dialog meldet Start und Dauer, und F5 laesst die
    # Buchfuehrung nicht aus.
    dialog = io.open(os.path.join(BASE, 'dialog_cozytouch.py'),
                     encoding='utf-8').read()
    check('der Dialog meldet den Start',
          '_note_local_cozytouch_boost(device.uuid, on=new_state)' in dialog)
    check('der Dialog meldet die Dauer',
          '_note_local_cozytouch_boost(device.uuid,\n' in dialog
          and 'minutes=minutes)' in dialog)
    init = io.open(os.path.join(BASE, '__init__.py'), encoding='utf-8').read()
    check('F5 fuehrt die Boost-Buchfuehrung mit',
          'self._note_cozytouch_boost(' in init)
    # Und dass die Dialoge die neue Basis wirklich setzen - und Cozytouch
    # seine Buchfuehrung nicht mehr an ein offenes Menue bindet.
    vesync_dialog = io.open(os.path.join(BASE, 'dialog_vesync.py'),
                            encoding='utf-8').read()
    check('der VeSync-Dialog nimmt die eigene Aenderung als Basis',
          "_accept_local_change('vesync', device)" in vesync_dialog)
    check('der Cozytouch-Dialog ebenso',
          "_accept_local_change('cozytouch', device)" in dialog)
    check('die Favoritenebene ebenso',
          "_accept_local_change('vesync', device)" in init
          and "_accept_local_change('cozytouch', device)" in init)
    check('Cozytouch: Verlauf und Boost auch bei geschlossenem Menue',
          dialog.count('worker_done=worker_done') >= 5)


def test_saved_without_a_next_entry():
    """Ein Eintrag landet auf der Platte, auch wenn kein weiterer folgt.

    Am 02.10.2026 stand "Fuetterungsplan ein" von 19:52:44 eine Viertelstunde
    spaeter noch nicht in der Datei - das "aus" von 19:52:35 schon. Die
    Entprellung sah nur beim NAECHSTEN Eintrag auf die Uhr.
    """
    print('== Der Verlauf wartet nicht auf den naechsten Eintrag ==')
    import ast

    src = io.open(os.path.join(BASE, 'history.py'), encoding='utf-8').read()
    tree = ast.parse(src)
    parts = [n for n in tree.body
             if (isinstance(n, ast.ClassDef) and n.name == '_EntryStore')
             or (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                 and n.targets[0].id.startswith('SAVE_DEBOUNCE'))]
    written = []

    class Clock:
        now = 1000.0

        def time(self):
            return self.now

    class Log:
        def _noop(self, *a, **k):
            pass
        debug = info = warning = error = _noop

    clock = Clock()
    env = {'time': clock, 'log': Log(), 'os': os, 'json': None,
           '_atomic_write_json': lambda path, entries: written.append(
               len(entries))}
    exec(compile(ast.Module(parts, []), 'history.py', 'exec'), env)
    store = env['_EntryStore']('x.json', 1000, 0, 'Events')
    check('die Methode ist da', hasattr(store, 'save_if_due'))
    if not hasattr(store, 'save_if_due'):
        return

    store.append({'timestamp': clock.now, 'action': 'feeding_plan_off'})
    check('der erste Eintrag wird gleich geschrieben', written == [1], str(written))
    clock.now += 7
    store.append({'timestamp': clock.now, 'action': 'feeding_plan_on'})
    check('der zweite, 7 s spaeter, wartet', written == [1], str(written))
    clock.now += 10
    store.save_if_due()
    check('nach 17 s ist er noch nicht faellig', written == [1], str(written))
    clock.now += 20
    store.save_if_due()
    check('nach 37 s steht er in der Datei, ohne dass etwas folgte',
          written == [1, 2], str(written))
    clock.now += 60
    store.save_if_due()
    check('ohne neue Eintraege wird nichts mehr geschrieben',
          written == [1, 2], str(written))

    sched = io.open(os.path.join(BASE, 'scheduler.py'), encoding='utf-8').read()
    check('der Scheduler ruft es bei jedem Durchlauf auf',
          'save_due_pending()' in sched)


def main():
    env = load_history_funcs()
    fmt = env['_format_action_text']

    print('== Altbestand: gespeichertes Deutsch verschwindet ==')
    # Genau der gemeldete Fall
    check('"Switched off: Aus" ist weg',
          fmt('toggle_off', 'Aus') == 'Switched off', fmt('toggle_off', 'Aus'))
    check('auch fuer Ein', fmt('toggle_on', 'Ein') == 'Switched on')
    check('und fuer bereits englische Altdaten',
          fmt('toggle_off', 'Off') == 'Switched off')
    check('alte Luefterstufe wird uebersetzt',
          fmt('set_fan_speed', 'Stufe 1') == 'Fan speed changed: Level 1',
          fmt('set_fan_speed', 'Stufe 1'))

    print('== Redundanz: Zustand steht schon in der Aktion ==')
    for action in ('toggle_on', 'toggle_off', 'mute_on', 'mute_off',
                   'display_on', 'display_off', 'child_lock_on',
                   'oscillation_off', 'boost_on', 'away_off'):
        check(f'{action} ohne Detail',
              ':' not in fmt(action, 'IRGENDWAS'), fmt(action, 'IRGENDWAS'))

    print('== Neue, sprachneutrale Werte ==')
    cases = [
        (('set_fan_speed', '2'), 'Fan speed changed: Level 2'),
        (('set_mode', 'sleep'), 'Mode changed: Sleep mode'),
        (('set_mode', 'auto'), 'Mode changed: Auto'),
        (('set_nightlight', 'dim'), 'Night light changed: Dimmed'),
        (('set_auto_preference', 'quiet'), 'Auto profile changed: Quiet'),
        (('therm_mode', 'away'), 'Mode changed: Away'),
    ]
    for (action, details), expected in cases:
        got = fmt(action, details)
        check(f'{action}={details}', got == expected, f'{got!r} statt {expected!r}')

    print('== Informative Details bleiben erhalten ==')
    check('Temperatur', fmt('set_temp', '21,5 °C') == 'Temperature set: 21,5 °C')
    check('Heizprogramm ohne deutsches Praefix',
          fmt('switch_schedule', 'Winter') == 'Heating schedule switched: Winter',
          fmt('switch_schedule', 'Winter'))
    check('unbekannter Schluessel wird durchgereicht',
          fmt('set_mode', 'etwas-unbekanntes') == 'Mode changed: etwas-unbekanntes')

    print('== Schreibseite legt nichts Uebersetztes mehr ab ==')
    import re
    bad = []
    for name in sorted(os.listdir(BASE)):
        if not name.endswith('.py'):
            continue
        src = io.open(os.path.join(BASE, name), encoding='utf-8').read()
        for m in re.finditer(r'log_(?:external_)?action\(([^;]{0,200}?)\)\n',
                             src, re.S):
            call = m.group(1)
            # Ein _("...") als Detail-Argument ist genau das Problem
            if re.search(r",\s*(#[^\n]*\n\s*)*_\(", call):
                bad.append(f'{name}: {call.strip()[:60]}')
    check('kein _()-Detail mehr in log_action', not bad, str(bad[:3]))

    test_measurements()
    test_external_changes()
    test_automatic_origin()
    test_saved_without_a_next_entry()
    test_schedule_detail()

    print()
    if FAILED:
        print(f'FEHLGESCHLAGEN: {len(FAILED)} -> {FAILED}')
        return 1
    print('GESAMT: ALLE TESTS OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
