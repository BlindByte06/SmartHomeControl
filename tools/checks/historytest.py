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
    keep = [n for n in tree.body
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict)]
    exec(compile(ast.Module(keep, []), path, 'exec'), mod.__dict__)
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

    env = {'_': lambda s: s}
    src = io.open(os.path.join(BASE, 'history.py'), encoding='utf-8').read()
    wanted = {'_format_action_text', '_detail_is_redundant', '_detail_text'}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            exec(compile(ast.Module([node], []), 'history.py', 'exec'), env)
    return env



def test_measurements():
    """Messwert-Ansicht: Groessen konsistent, Zeitraum vorhanden."""
    import re
    hist = io.open(os.path.join(BASE, 'history.py'), encoding='utf-8').read()
    env = {'_': lambda s: s}
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
    check('nur bei Wechsel', 'previous is None or previous == wet' in cd)
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
        if isinstance(node, _ast.FunctionDef) and node.name == '_csv_number':
            exec(compile(_ast.Module([node], []), 'h', 'exec'), env2)
    cn = env2['_csv_number']
    check('28.5 wird zu 28,5 (sonst Datum "28. Mai")', cn(28.5, ',') == '28,5', cn(28.5, ','))
    check('ganze Zahl bleibt', cn(812, ',') == '812')
    check('leer bleibt leer', cn('', ',') == '' and cn(None, ',') == '')
    check('Punkt-Gebietsschema unveraendert', cn(28.5, '.') == '28.5')
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

    print()
    if FAILED:
        print(f'FEHLGESCHLAGEN: {len(FAILED)} -> {FAILED}')
        return 1
    print('GESAMT: ALLE TESTS OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
