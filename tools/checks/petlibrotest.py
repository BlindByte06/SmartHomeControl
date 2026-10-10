# -*- coding: utf-8 -*-
"""Testet die Petlibro-Anbindung (Futterautomat und Trinkbrunnen).

Die Messwerte unten stammen aus einer echten Abfrage (Air Smart Feeder
PLAF108, Dockstream 2 Cordless PLWF116, 22.09.2026), ohne persoenliche Daten.

Was hier vor allem abgesichert wird, ist das, was ein Tier trifft:

- "Jetzt füttern" schickt den Befehl genau EINMAL - auch wenn die Antwort
  ausbleibt, auch wenn die Cloud "Gerät antwortet nicht" (2020) meldet. Ein
  zweiter Befehl wäre eine zweite Mahlzeit.
- Befehle werden nach einem Netzwerkfehler nicht wiederholt, Abfragen schon.
- Bestätigt ist eine Fütterung erst durch einen NEUEN Eintrag im Protokoll
  des Automaten, nicht durch die Antwort der Cloud.
- Eine Favoriten-Taste füttert nie, und die Erinnerungen (Trockenmittel,
  Filter, Reinigung) werden nur nach einer Rückfrage mit "Abbrechen" als
  Vorauswahl zurückgesetzt.
"""
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


def load():
    """Laedt petlibro_devices und petlibro_api als Paket mit NVDA-Attrappen."""
    for name in ('logHandler', 'addonHandler'):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
    sys.modules['logHandler'].log = type('L', (), {
        'debug': staticmethod(lambda *a, **k: None),
        'info': staticmethod(lambda *a, **k: None),
        'warning': staticmethod(lambda *a, **k: None),
        'error': staticmethod(lambda *a, **k: None)})()
    sys.modules['addonHandler'].initTranslation = lambda: None
    # petlibro_api imports requests. NVDA brings its own, the build machine
    # does not - the first build with this check failed on exactly that. The
    # add-on's copy in lib/ is the one that runs on an NVDA without one, and
    # it is appended to the path, as the add-on does.
    try:
        import requests  # noqa: F401
    except ImportError:
        sys.path.append(os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), 'lib'))
    pkg = types.ModuleType('shcprobe')
    pkg.__path__ = [BASE]
    sys.modules['shcprobe'] = pkg
    devices = importlib.import_module('shcprobe.petlibro_devices')
    api = importlib.import_module('shcprobe.petlibro_api')
    errors = importlib.import_module('shcprobe.errors')
    # Warten kostet im Test nur Zeit.
    devices.time.sleep = lambda s: None
    return devices, api, errors


FEEDER_LIST = {
    "deviceSn": "AF0000000001", "name": "Air Smart Feeder",
    "productIdentifier": "PLAF108", "productName": "Air Smart Feeder",
    "online": True, "surplusGrain": True, "powerType": 2,
    "electricQuantity": 50, "batteryState": "high",
    "enableFeedingPlan": True, "nextFeedingTime": "22:00",
    "nextFeedingQuantity": 2, "maxFeedingCup": 16, "deviceShareState": 1,
    "secondCategoryNameI18n": "Feeder", "timezone": "Europe/Vienna",
}
FEEDER_REAL = {
    "online": True, "surplusGrain": True, "powerType": 2, "powerState": "USING",
    "electricQuantity": 50, "batteryState": "high", "lightSwitch": True,
    "changeDesiccantFrequency": 14, "remainingDesiccantDays": -27,
    "desiccantState": "OVER_DUE", "errorState": False, "exceptionMessage": "",
    "doorErrorState": "NORMAL", "wifiRssi": -75, "softwareVersion": "2.0.14",
    # Felder, die der Futterautomat auch liefert, die aber zum Brunnen
    # gehoeren - sie duerfen nichts anzeigen.
    "remainingReplacementDays": 14, "weightPercent": 0, "weight": 0.0,
}
FEEDER_GRAIN = {"todayFeedingQuantities": [3, 2, 3],
                "todayFeedingQuantity": 8, "todayFeedingTimes": 3}
FEEDER_TODAY = {"plans": [
    {"planId": 1, "time": "07:00", "grainNum": 3, "state": 3, "repeat": True},
    {"planId": 2, "time": "12:30", "grainNum": 2, "state": 3, "repeat": True},
    {"planId": 3, "time": "17:00", "grainNum": 3, "state": 3, "repeat": True},
    {"planId": 4, "time": "22:00", "grainNum": 2, "state": 1, "repeat": True},
], "allExpired": False, "allSkipped": False}
FEEDER_PLANS = [
    {"id": 3, "executionTime": "17:00", "repeatDay": "[7,1,2,3,4,5,6]",
     "label": "Abend", "enable": True, "grainNum": 3},
]


def record(rid, ts, kind="GRAIN_OUTPUT_SUCCESS", event="FEEDING_PLAN_SUCCESS",
           amount=3, plan=3, alarm=False):
    return {"id": rid, "type": kind, "eventType": event,
            "expectGrainNum": amount, "actualGrainNum": amount,
            "recordTime": ts, "alarmTag": alarm,
            "params": '{"planId":%d,"actualGrainNum":%d}' % (plan, amount)}


FOUNTAIN_LIST = {
    "deviceSn": "WF0000000001", "name": "Dockstream 2 Smart  Kitchen",
    "productIdentifier": "PLWF116",
    "productName": "Dockstream 2 Smart Cordless Fountain",
    "online": True, "secondCategoryNameI18n": "Fountain",
}
FOUNTAIN_REAL = {
    "online": True, "weightPercent": 73, "weight": 2101.0, "lowWater": 1100,
    "useWaterType": 2, "radarSensingLevel": "NearTrigger",
    "waterStopSwitch": False, "useWaterInterval": 15, "useWaterDuration": 15,
    "remainingReplacementDays": 12, "filterReplacementFrequency": 14,
    "remainingCleaningDays": 12, "machineCleaningFrequency": 14,
    "electricQuantity": 100, "powerType": 2, "powerState": "USING",
    "batteryState": "high", "lightSwitch": True, "todayTotalMl": 35,
}
FOUNTAIN_DRINK = {"todayTotalMl": 35, "todayTotalTimes": 4,
                  "yesterdayTotalMl": 43, "yesterdayTotalTimes": 4}


def test_readings(dev_mod):
    print('== Was die Geraete melden ==')
    feeder = dev_mod.wrap_device(dict(FEEDER_LIST), api=None)
    feeder.apply_real(FEEDER_REAL)
    feeder.apply_grain_status(FEEDER_GRAIN)
    feeder.apply_today(FEEDER_TODAY)
    feeder.apply_plans(FEEDER_PLANS)
    check('PLAF108 ist ein Futterautomat', feeder.is_feeder and not feeder.is_fountain)
    check('die Modellnummer steht in der Klammer', feeder.get_type_display() == 'PLAF108')
    check('Plattform-Schluessel mit Praefix', feeder.uuid == 'petlibro_AF0000000001')
    check('Futter reicht', feeder.food_low is False)
    check('naechste Fuetterung aus der Geraeteliste', feeder.next_feed == ('22:00', 2))
    check('hoechstens 16 Portionen', feeder.max_portions == 16)
    check('heute 3 Fuetterungen, 8 Portionen',
          feeder.today_count == 3 and feeder.today_portions == 8)
    check('3 von 4 Mahlzeiten gegeben', feeder.meals_done_text() == '3 of 4 given',
          feeder.meals_done_text())
    check('Trockenmittel seit 27 Tagen faellig',
          feeder.desiccant_days == -27 and feeder.desiccant_due is True)
    check('Akku 50 Prozent ohne Netzteil (vom Tester bestaetigt)',
          feeder.battery_percent == 50 and feeder.on_mains is False)
    check('der Futterautomat ist kein Brunnen - keine Wasserwerte',
          not hasattr(feeder, 'water_percent'))

    fountain = dev_mod.wrap_device(dict(FOUNTAIN_LIST), api=None)
    fountain.apply_real(FOUNTAIN_REAL)
    fountain.apply_drink(FOUNTAIN_DRINK)
    check('PLWF116 ist ein Brunnen', fountain.is_fountain)
    check('doppelte Leerzeichen im Namen werden zu einem',
          fountain.name == 'Dockstream 2 Smart Kitchen', fountain.name)
    check('Wasserstand 73 Prozent, 2101 ml',
          fountain.water_percent == 73 and fountain.water_ml == 2101)
    check('ueber der Warnschwelle von 1100 ml', fountain.water_low is False)
    check('Modus Sensor nah', fountain.water_mode == 'sensor_near')
    check('auf Akku, mit Sensor: weder Dauerbetrieb (wie in der App) noch '
          'Intervall', fountain.water_modes() == ['off', 'sensor_near', 'sensor_far'],
          str(fountain.water_modes()))
    fountain.apply_real({"powerType": 3})
    check('am Netzteil: Dauerbetrieb wird angeboten',
          fountain.water_modes() == ['off', 'constant', 'sensor_near', 'sensor_far'],
          str(fountain.water_modes()))
    fountain.apply_real({"powerType": 2})
    fountain.apply_real({"useWaterType": 0})
    check('Dauerbetrieb gespeichert, aber auf Akku: als "laeuft nicht" erkannt '
          '(Petlibro: auf Akku nur per Sensor)', fountain.constant_idle is True)
    check('... und nicht erneut angeboten', 'constant' not in fountain.water_modes(),
          str(fountain.water_modes()))
    fountain.apply_real({"useWaterType": 2})
    check('Air Smart Feeder: kein Essensruf (kein Lautsprecher, Petlibro-Hilfe)',
          feeder.has_meal_call is False)
    check('Filter und Reinigung in 12 Tagen',
          fountain.filter_days == 12 and fountain.cleaning_days == 12
          and not fountain.filter_due and not fountain.cleaning_due)
    check('heute 35 ml in 4 Trinkvorgaengen',
          fountain.drink_today_ml == 35 and fountain.drink_today_count == 4)
    fountain.apply_real({"waterStopSwitch": True})
    check('Pumpe aus schlaegt jeden Modus', fountain.water_mode == 'off')

    other = dev_mod.wrap_device({"deviceSn": "LB1", "productIdentifier": "PLLB001",
                                 "productName": "Luma Smart Litter Box"}, api=None)
    check('unbekanntes Produkt: weder Futterautomat noch Brunnen',
          not other.is_feeder and not other.is_fountain and other.is_petlibro)


def test_records(dev_mod):
    print('== Fuetterungsprotokoll ==')
    days = [{"workRecords": [record("b", 2000, event="MANUAL_FEEDING_SUCCESS",
                                    amount=1, plan=0)]},
            {"workRecords": [record("a", 1000),
                             record("o", 1500, kind="OFFLINE", event="OFFLINE",
                                    alarm=True)]}]
    flat = dev_mod.flatten_records(days)
    check('neueste zuerst, ueber alle Tage', [r["id"] for r in flat] == ["b", "o", "a"])
    feeder = dev_mod.wrap_device(dict(FEEDER_LIST), api=None)
    feeder.apply_records(days)
    last = feeder.last_feed
    check('letzte Fuetterung: von Hand, 1 Portion',
          last and last["manual"] and last["portions"] == 1)
    plan_feed = dev_mod.feed_from_record(record("a", 1000, plan=3))
    check('die Plan-Nummer kommt aus params', plan_feed["plan_id"] == 3)


class FeedApi:
    """Cloud-Attrappe fuer "Jetzt füttern"."""

    def __init__(self, api_mod, fail=None, write_record=True, failed_record=False,
                 food_low_warning=False, planned_meal=False):
        self.api_mod = api_mod
        self.fail = fail
        self.write_record = write_record
        self.failed_record = failed_record
        self.food_low_warning = food_low_warning
        self.planned_meal = planned_meal
        self.feeds = 0
        self.records = [record("old", 1000)]

    def fetch_records(self, serial, size=20, days=7):
        return [{"workRecords": list(self.records)}]

    def fetch_grain_status(self, serial):
        return {}

    def manual_feed(self, serial, portions):
        self.feeds += 1
        if self.planned_meal:
            # A planned meal of 2 portions in the same half minute.
            self.records.insert(0, record("plan", 9_999_999_999_998, amount=2,
                                          plan=3))
        if self.write_record:
            if self.failed_record:
                self.records.insert(0, record("new", 9_999_999_999_999,
                                              kind="GRAIN_OUTLET_BLOCKED",
                                              event="GRAIN_OUTLET_BLOCKED_OVERTIME",
                                              alarm=True))
            else:
                self.records.insert(0, record("new", 9_999_999_999_999,
                                              event="MANUAL_FEEDING_SUCCESS",
                                              amount=portions, plan=0))
                if self.food_low_warning:
                    # As on 2026-09-23: the food-low warning, with the alarm
                    # flag, written right after the meal and newer than it.
                    self.records.insert(0, record(
                        "warn", 9_999_999_999_999 + 1,
                        kind="SURPLUS_GRAIN_NOT_ENOUGH",
                        event="SURPLUS_GRAIN_NOT_ENOUGH", alarm=True))
        if self.fail == 'network':
            raise self.api_mod.requests.ConnectionError("lost")
        if self.fail is not None:
            raise self.api_mod.PetlibroError("/device/device/manualFeeding", self.fail)
        return 12345


def test_feed_now(dev_mod, api_mod):
    print('== Jetzt fuettern ==')

    def run(**kwargs):
        api = FeedApi(api_mod, **kwargs)
        feeder = dev_mod.wrap_device(dict(FEEDER_LIST), api=api)
        result = feeder.feed_now(3, wait=0.3, pause=0)
        return api, result

    api, result = run()
    check('bestaetigt durch den neuen Protokolleintrag',
          result["status"] == "confirmed" and result["portions"] == 3, str(result))
    check('genau ein Befehl', api.feeds == 1)

    api, result = run(fail='network')
    check('Netzwerkfehler: trotzdem nur ein Befehl', api.feeds == 1)
    check('... und der Eintrag bestaetigt, was wirklich geschah',
          result["status"] == "confirmed", str(result))

    api, result = run(fail='network', write_record=False)
    check('keine Antwort, kein Eintrag: unklar, nicht wiederholt',
          result["status"] == "unconfirmed" and result["unclear"]
          and api.feeds == 1, str(result))

    api, result = run(fail=api_mod.CODE_DEVICE_TIMEOUT, write_record=False)
    check('2020 (Geraet antwortet nicht): unklar statt "nicht gefuettert"',
          result["status"] == "unconfirmed" and result["unclear"]
          and api.feeds == 1, str(result))

    api, result = run(fail=api_mod.CODE_ALREADY_FEEDING, write_record=False)
    check('2028 (fuettert gerade): abgelehnt, nichts nachgeschickt',
          result["status"] == "refused" and api.feeds == 1, str(result))

    api, result = run(failed_record=True)
    check('ein Stoerungseintrag meldet das Scheitern',
          result["status"] == "failed", str(result))

    api, result = run(planned_meal=True)
    check('eine geplante Mahlzeit im selben Moment ist nicht die Bestaetigung '
          '- es zaehlt der Handeintrag',
          result["status"] == "confirmed" and result["portions"] == 3
          and result.get("record_id") == "new", str(result))
    api, result = run(planned_meal=True, write_record=False)
    check('kommt nur die geplante, bleibt die Fuetterung unbestaetigt',
          result["status"] == "unconfirmed", str(result))

    api, result = run(food_low_warning=True)
    check('Futter-knapp-Warnung neben der Fuetterung: trotzdem bestaetigt '
          '(Log vom 23.09.)', result["status"] == "confirmed"
          and result.get("record_id") == "new", str(result))

    kinds = {k: dev_mod.record_kind(record("x", 1, kind=k, event=e, alarm=True))
             for k, e in (("SURPLUS_GRAIN_NOT_ENOUGH", "SURPLUS_GRAIN_NOT_ENOUGH"),
                          ("GRAIN_OUTLET_BLOCKED", "GRAIN_OUTLET_BLOCKED_OVERTIME"),
                          ("OFFLINE", "OFFLINE"),
                          ("GRAIN_OUTPUT_SUCCESS", "FEEDING_PLAN_SUCCESS"))}
    check('Warnung ist keine Stoerung, Blockade schon',
          kinds == {"SURPLUS_GRAIN_NOT_ENOUGH": "notice",
                    "GRAIN_OUTLET_BLOCKED": "failed",
                    "OFFLINE": "connection",
                    "GRAIN_OUTPUT_SUCCESS": "fed"}, str(kinds))


def test_api_rules(api_mod, errors_mod):
    print('== Regeln der Cloud-Anbindung ==')
    src = io.open(os.path.join(BASE, 'petlibro_api.py'), encoding='utf-8').read()
    command = src[src.index('    def _command'):]
    command = command[:command.index('\n    def ', 10)]
    check('Befehle ohne Netzwerk-Wiederholung', 'retry_network=False' in command)
    for name in ('manual_feed', 'set_feeding_plan_enabled', 'reset_desiccant',
                 'set_water_mode', 'set_low_water', 'set_light', 'add_plan',
                 'delete_plan', 'set_today_all', 'set_today_single',
                 'set_sensing_duration'):
        body = src[src.index(f'    def {name}('):]
        body = body[:body.index('\n    def ', 10)] if '\n    def ' in body[10:] else body
        uses_post = 'self._post(' in body
        check(f'{name} geht ueber _command', not uses_post and (
            'self._command(' in body or 'self._reset(' in body))
    logout = src[src.index('    def logout'):]
    logout = logout[:logout.index('\n    def ', 10)]
    check('Abmelden schickt nichts an die Cloud (/member/auth/logout wuerde '
          'das gespeicherte Token ungueltig machen)',
          '_post(' not in logout and '_command(' not in logout
          and 'session.post' not in logout)
    check('Etc-Zonen zaehlen andersherum',
          api_mod._local_timezone().startswith(('Etc/GMT', 'UTC')))

    class Resp:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    class Session:
        def __init__(self, payload):
            self.payload = payload

        def post(self, *a, **k):
            return Resp(self.payload)

    for code, name in ((1102, 'falsches Passwort'), (1101, 'unbekanntes Konto')):
        api = api_mod.PetlibroAPI()
        api._session = Session({"code": code, "msg": "x", "data": None})
        try:
            api.login("a@b.cd", "secret")
            ok = False
        except errors_mod.CredentialsRejected:
            ok = True
        except Exception:
            ok = False
        check(f'{name} ({code}) gilt als abgelehnte Zugangsdaten', ok)
    api = api_mod.PetlibroAPI()
    api._session = Session({"code": 0, "data": {"token": "T"}})
    api.login("a@b.cd", "secret")
    check('Anmeldung merkt sich das Token', api.get_credentials()["token"] == "T")


def test_ui_rules():
    print('== Bedienung ==')
    plugin = io.open(os.path.join(BASE, '__init__.py'), encoding='utf-8').read()
    fav = plugin[plugin.index('    def _favorite_toggle'):]
    fav = fav[:fav.index('\n    def ', 10)]
    check('eine Favoriten-Taste schaltet Petlibro nicht',
          "is_petlibro" in fav)
    toggle = plugin[plugin.index('    def toggle_device'):]
    toggle = toggle[:toggle.index('\n    def ', 10)]
    check('und auch toggle_device lehnt ab', "is_petlibro" in toggle)

    dialog = io.open(os.path.join(BASE, 'dialog_petlibro.py'), encoding='utf-8').read()
    confirm = dialog[dialog.index('    def _petlibro_confirm'):]
    confirm = confirm[:confirm.index('\n    def ', 10)]
    check('die Rueckfrage hat Abbrechen vorausgewaehlt', 'wx.NO_DEFAULT' in confirm)
    check('Escape bricht ab', 'SetEscapeId(wx.ID_NO)' in confirm)
    run = dialog[dialog.index('    def _run_petlibro_action'):]
    run = run[:run.index('\n    def ', 10)]
    check('eine Aktion beginnt erst nach der Pruefung, ob schon eine laeuft',
          run.index('self._begin_cloud_action()') < run.index('on_start()'))
    feed = dialog[dialog.index('    def _handle_petlibro_feed'):]
    feed = feed[:feed.index('\n    def ', 10)]
    started = feed[feed.index('def started'):feed.index('def on_result')]
    check('"Jetzt fuettern" gilt erst als unterwegs, wenn es wirklich '
          'hinausgeht - sonst schluckte es zehn Minuten lang jede Fuetterung '
          'von anderswo',
          '_begin_petlibro_feed(device)' in started
          and 'on_start=started' in feed
          and feed.count('_begin_petlibro_feed(') == 1)
    reset = dialog[dialog.index('    def _petlibro_reset'):]
    reset = reset[:reset.index('\n    def ', 10)]
    check('zurueckgesetzt wird nur nach der Rueckfrage',
          reset.index('_petlibro_confirm') < reset.index('_run_petlibro_action'))
    for action in ('petlibro_feed', 'petlibro_plan', 'petlibro_meals',
                   'petlibro_desiccant', 'petlibro_filter', 'petlibro_cleaning',
                   'petlibro_water_mode', 'petlibro_low_water', 'petlibro_light',
                   'petlibro_drinks', 'petlibro_flow_duration'):
        tree = io.open(os.path.join(BASE, 'device_dialog.py'), encoding='utf-8').read()
        helptext = io.open(os.path.join(BASE, 'dialog_help.py'), encoding='utf-8').read()
        check(f'{action}: im Baum, in der Tabelle und in der F1-Hilfe',
              f"'{action}')" in dialog and f"'{action}':" in tree
              and f"action == '{action}'" in helptext)


def test_settings_enable():
    """Zugangsdaten eingetragen und gespeichert, ohne vorher zu testen: die
    Plattform blieb aus, und nichts erschien im Baum (gemessen am
    Petlibro-Reiter - E-Mail und Passwort gespeichert, kein usePetlibro)."""
    print('== Einstellungen: neue Zugangsdaten schalten die Plattform ein ==')
    import ast
    src = io.open(os.path.join(BASE, 'settings_panel.py'), encoding='utf-8').read()
    tree = ast.parse(src)
    env = {'PLATFORM_LABELS': {'meross': 'Meross', 'vesync': 'VeSync',
                               'cozytouch': 'Cozytouch', 'petlibro': 'Petlibro'}}
    for node in ast.walk(tree):
        if (isinstance(node, ast.FunctionDef)
                and node.name == '_enable_platforms_with_new_credentials'):
            exec(compile(ast.Module([node], []), 'settings_panel.py', 'exec'), env)
    func = env.get('_enable_platforms_with_new_credentials')
    check('die Pruefung gibt es', func is not None)
    if func is None:
        return
    check('on_ok ruft sie auf, bevor irgendetwas geprueft wird',
          src.index('self._enable_platforms_with_new_credentials()')
          < src.index('# Meross validation'))

    class Ctrl:
        def __init__(self, value=''):
            self.value = value

        def GetValue(self):
            return self.value

    class Box(Ctrl):
        def SetValue(self, value):
            self.value = value

    def dialog(petlibro_email='', petlibro_typed='', stored_email='',
               stored_password='', ticked=False, toggled=False):
        d = types.SimpleNamespace()
        d.plugin = types.SimpleNamespace(
            email='', password='', vesync_email='', vesync_password='',
            cozytouch_email='', cozytouch_password='',
            petlibro_email=stored_email, petlibro_password=stored_password)
        for name in ('Meross', 'Vesync', 'Cozytouch'):
            setattr(d, f'chk{name}', Box(False))
        for name in ('meross', 'vesync', 'cozytouch'):
            setattr(d, f'{name}EmailCtrl', Ctrl())
            setattr(d, f'{name}PasswordCtrl', Ctrl())
        d.chkPetlibro = Box(ticked)
        d.petlibroEmailCtrl = Ctrl(petlibro_email)
        d.petlibroPasswordCtrl = Ctrl(petlibro_typed)
        d._user_toggled = {d.chkPetlibro} if toggled else set()
        d.enabled = []

        def auto_enable(checkbox, label):
            checkbox.SetValue(True)
            d.enabled.append(label)
        d._auto_enable_platform = auto_enable
        func(d)
        return d

    d = dialog('a@b.cd', 'secret')
    check('neu eingetragen: Petlibro wird eingeschaltet',
          d.chkPetlibro.value is True and d.enabled == ['Petlibro'], str(d.enabled))
    d = dialog('a@b.cd', 'secret', toggled=True)
    check('von Hand abgewaehlt: bleibt aus', d.chkPetlibro.value is False)
    d = dialog('a@b.cd', '', stored_email='a@b.cd', stored_password='x')
    check('nichts geaendert: bleibt, wie es ist', d.chkPetlibro.value is False)
    d = dialog('a@b.cd', '')
    check('ohne Passwort: nicht eingeschaltet', d.chkPetlibro.value is False)
    d = dialog('', '')
    check('leer: keine andere Plattform wird angefasst', d.enabled == [])


class WaterApi:
    """Cloud-Attrappe fuer den Brunnen: zeichnet die Befehle auf."""

    def __init__(self, api_mod, fail=None):
        self.api_mod = api_mod
        self.fail = fail
        self.calls = []
        self.real = dict(FOUNTAIN_REAL)

    def set_radar(self, serial, mode):
        self.calls.append(('radar', mode))
        self.real["radarSensingLevel"] = ("FarTrigger" if mode == "sensor_far"
                                          else "NearTrigger")

    def set_water_mode(self, serial, mode, interval=None, duration=None,
                       sensing=None, currently_off=False):
        self.calls.append(('mode', mode))
        if self.fail:
            raise self.api_mod.PetlibroError("/device/device/waterModeSetting",
                                             self.fail)

    def set_sensing_duration(self, serial, seconds, interval=None,
                             duration=None):
        self.calls.append(('duration', seconds))
        if self.fail:
            raise self.api_mod.PetlibroError("/device/device/waterModeSetting",
                                             self.fail)
        self.real["sensingWaterDuration"] = seconds

    def fetch_real_info(self, serial):
        return dict(self.real)


def test_flow_duration(dev_mod, api_mod):
    """Petlibro-Support, 01.10.2026: im Sensor-Modus laesst sich die
    Laufzeit auf 45, 60, 75 oder 90 Sekunden stellen, ab Firmware 0.3.25.
    Gemessen am 23.09.: der PLWF116 hat 0.3.59 und meldet
    sensingWaterDuration 45 - den Standardwert der App."""
    print('== Brunnen: Laufzeit im Sensor-Modus ==')
    real = dict(FOUNTAIN_REAL, sensingWaterDuration=45, softwareVersion='0.3.59')
    api = WaterApi(api_mod)
    api.real = dict(real)
    fountain = dev_mod.wrap_device(dict(FOUNTAIN_LIST), api=api)
    fountain.apply_real(real)
    check('die Laufzeit wird gelesen', fountain.sensing_duration == 45)
    check('Firmware 0.3.59 kann sie aendern',
          fountain.sensing_duration_supported is True)
    check('die Auswahl der App', dev_mod.SENSING_DURATIONS == (45, 60, 75, 90))
    ok, actual = fountain.set_sensing_duration(60)
    check('ein Befehl, nur die Laufzeit - kein Modus, keine Reichweite',
          api.calls == [('duration', 60)], str(api.calls))
    check('und zurueckgelesen', ok and actual == 60, f'{ok} {actual}')

    api = WaterApi(api_mod, fail=api_mod.CODE_DEVICE_TIMEOUT)
    api.real = dict(real)
    fountain = dev_mod.wrap_device(dict(FOUNTAIN_LIST), api=api)
    fountain.apply_real(real)
    try:
        fountain.set_sensing_duration(90)
        pending = None
    except dev_mod.PetlibroPending as e:
        pending = e
    check('auf dem Akku: unterwegs, nicht fehlgeschlagen - und beobachtet',
          pending is not None and pending.key == 'sensing_duration'
          and pending.expected == 90, repr(pending))

    old = dev_mod.wrap_device(dict(FOUNTAIN_LIST), api=None)
    old.apply_real(dict(real, softwareVersion='0.3.20'))
    check('Firmware 0.3.20 ist zu alt', old.sensing_duration_supported is False)
    unknown = dev_mod.wrap_device(dict(FOUNTAIN_LIST), api=None)
    unknown.apply_real(dict(real, softwareVersion=''))
    check('ohne Firmwareangabe wird es angeboten (die Cloud sagt nein, wenn '
          'nicht)', unknown.sensing_duration_supported is None)

    src = io.open(os.path.join(BASE, 'petlibro_api.py'), encoding='utf-8').read()
    body = src[src.index('    def set_sensing_duration'):]
    body = body[:body.index('\n    def ', 10)]
    check('der Befehl ist der Sensor-Modus mit seiner Laufzeit',
          '"useWaterType": 2' in body and '"sensingWaterDuration"' in body
          and 'set_radar' not in body)
    dialog = io.open(os.path.join(BASE, 'dialog_petlibro.py'),
                     encoding='utf-8').read()
    items = dialog[dialog.index('    def _compute_petlibro_fountain_items'):]
    items = items[:items.index('\n    def ', 10)]
    check('die Zeile steht nur in den Sensor-Modi - sonst wuerde das Senden '
          'den Modus umstellen',
          'if mode in ("sensor_near", "sensor_far"):' in items)
    handler = dialog[dialog.index('    def _handle_petlibro_flow_duration'):]
    handler = handler[:handler.index('\n    def ', 10)]
    check('und der Handler prueft es noch einmal',
          'if device.water_mode not in ("sensor_near", "sensor_far"):' in handler)


def test_cloud_lags_behind(dev_mod, api_mod):
    """03.10.2026, PLWF116 am Netzteil: Dauerbetrieb um 17:46:33 gestellt
    und sofort so zurueckgelesen - ab 17:48:58 meldete die Cloud wieder
    "Sensor, fern", erst ab 17:54:47 wieder Dauerbetrieb. Beide Spruenge
    wurden als Aenderung von anderswo angesagt und gebucht, waehrend der
    Brunnen die ganze Zeit Dauerbetrieb lief."""
    print('== Brunnen: die Cloud hinkt nach ==')
    import time as _t

    class LaggingApi(WaterApi):
        def set_water_mode(self, serial, mode, interval=None, duration=None,
                           sensing=None, currently_off=False):
            super().set_water_mode(serial, mode, interval, duration, sensing,
                                   currently_off)
            self.real["useWaterType"] = 0 if mode == "constant" else 2
            if mode in ("sensor_near", "sensor_far"):
                self.real["radarSensingLevel"] = (
                    "FarTrigger" if mode == "sensor_far" else "NearTrigger")

    before = dict(FOUNTAIN_REAL, radarSensingLevel="FarTrigger",
                  sensingWaterDuration=60, softwareVersion='0.3.59')
    api = LaggingApi(api_mod)
    api.real = dict(before)
    fountain = dev_mod.wrap_device(dict(FOUNTAIN_LIST), api=api)
    fountain.apply_real(before)
    ok, actual = fountain.set_water_mode('constant')
    check('Dauerbetrieb gestellt und zurueckgelesen', ok and actual == 'constant',
          str(actual))
    fountain.apply_real(dict(before))           # 17:48:58
    check('der Modus von vorher aus der Cloud nimmt ihn nicht zurueck',
          fountain.water_mode == 'constant', str(fountain.water_mode))
    fountain.apply_real(dict(before, useWaterType=0))   # 17:54:47
    check('die Cloud holt auf: weiter Dauerbetrieb',
          fountain.water_mode == 'constant')

    # Ein dritter Wert ist jemand anderes - und danach darf der alte zurueck.
    fountain.apply_real(dict(before, useWaterType=1))
    check('ein dritter Wert wird sofort uebernommen',
          fountain.water_mode == 'intermittent', str(fountain.water_mode))
    fountain.apply_real(dict(before))
    check('... und danach auch der von vorher', fountain.water_mode == 'sensor_far',
          str(fountain.water_mode))

    # Zurueck auf den alten Modus, hier gestellt: das Zuruecklesen darf nicht
    # als Nachhinken verworfen werden.
    api = LaggingApi(api_mod)
    api.real = dict(before)
    fountain = dev_mod.wrap_device(dict(FOUNTAIN_LIST), api=api)
    fountain.apply_real(before)
    fountain.set_water_mode('constant')
    ok, actual = fountain.set_water_mode('sensor_far')
    check('hier zurueckgestellt wird bestaetigt, nicht verworfen',
          ok and actual == 'sensor_far', str(actual))

    # Nach Ablauf der Sperre gilt wieder, was die Cloud sagt.
    api = LaggingApi(api_mod)
    api.real = dict(before)
    fountain = dev_mod.wrap_device(dict(FOUNTAIN_LIST), api=api)
    fountain.apply_real(before)
    fountain.set_water_mode('constant')
    for key, (old, _until) in list(fountain._reverts.items()):
        fountain._reverts[key] = (old, _t.time() - 1)
    fountain.apply_real(dict(before))
    check('nach der Sperrzeit wird der alte Wert wieder uebernommen',
          fountain.water_mode == 'sensor_far', str(fountain.water_mode))
    check('die Sperre ist laenger als das gemessene Nachhinken (8 Minuten)',
          dev_mod.REVERT_HOLD > 8 * 60)

    # Die Laufzeit geht ueber denselben Befehl.
    api = LaggingApi(api_mod)
    api.real = dict(before)
    fountain = dev_mod.wrap_device(dict(FOUNTAIN_LIST), api=api)
    fountain.apply_real(before)
    ok, _actual = fountain.set_sensing_duration(45)
    fountain._protected.clear()                 # zwei Minuten spaeter ...
    fountain.apply_real(dict(before))           # ... wieder 60 aus der Cloud
    check('die Laufzeit von vorher nimmt die neue nicht zurueck',
          ok and fountain.sensing_duration == 45, str(fountain.sensing_duration))


def test_fountain_commands(dev_mod, api_mod):
    print('== Brunnen: Wassermodus ==')
    api = WaterApi(api_mod)
    fountain = dev_mod.wrap_device(dict(FOUNTAIN_LIST), api=api)
    fountain.apply_real(FOUNTAIN_REAL)
    ok, actual = fountain.set_water_mode('sensor_far')
    check('nah -> fern schickt nur die Reichweite (der Modus davor lief in 2020)',
          api.calls == [('radar', 'sensor_far')], str(api.calls))
    check('... und liest fern zurueck', ok and actual == 'sensor_far', str(actual))

    api = WaterApi(api_mod, fail=api_mod.CODE_DEVICE_TIMEOUT)
    fountain = dev_mod.wrap_device(dict(FOUNTAIN_LIST), api=api)
    fountain.apply_real(FOUNTAIN_REAL)
    try:
        fountain.set_water_mode('constant')
        pending = None
    except dev_mod.PetlibroPending as e:
        pending = e
    check('2020 heisst "unterwegs", nicht "fehlgeschlagen"',
          pending is not None and pending.key == 'water_mode'
          and pending.expected == 'constant', repr(pending))
    check('... und es bleibt bei einem Befehl', api.calls == [('mode', 'constant')],
          str(api.calls))

    src = io.open(os.path.join(BASE, 'dialog_petlibro.py'), encoding='utf-8').read()
    check('Warnschwelle wie in der App: 650 bis 3000 ml', '650 <= ml <= 3000' in src)
    check('Wochentage als einzelne Kontrollkaestchen, keine CheckListBox',
          'wx.CheckListBox(' not in src and src.count('wx.CheckBox(') >= 2)


def test_review(dev_mod, api_mod):
    """Was die Durchsicht vom 23.09. gefunden hat."""
    print('== Durchsicht 23.09. ==')
    import time as _t
    now_ms = int(_t.time() * 1000)
    old = record("alt", now_ms - 3_600_000)
    check('leere Liste vorher: ein alter Eintrag ist trotzdem nicht neu',
          not dev_mod._is_new_record(old, set(), _t.time()))
    check('ein junger Eintrag ohne Liste vorher ist neu',
          dev_mod._is_new_record(record("neu", now_ms), None, _t.time()))
    low = record("m", now_ms, event="MANUAL_FEEDING_GRAIN_NOT_ENOUGH", amount=1, plan=0)
    check('Handfuetterung bei knappem Futter bleibt eine Handfuetterung',
          dev_mod.feed_from_record(low)["manual"] is True)
    planned_low = record("p", now_ms, event="FEEDING_PLAN_GRAIN_NOT_ENOUGH")
    check('Plan-Mahlzeit bei knappem Futter ist eine ausgegebene Mahlzeit',
          dev_mod.record_kind(planned_low) == "fed"
          and dev_mod.feed_from_record(planned_low)["manual"] is False)
    check('Tank leer/abgenommen ist eine Stoerung',
          dev_mod.record_kind({"type": "WATER_BOX_LEAVE_ERROR",
                               "eventType": "WATER_BOX_LEAVE_ERROR"}) == "failed")
    drinks = dev_mod.drinks_from_records([
        {"type": "DRINK", "recordTime": 2000, "totalMl": 4, "drinkTime": 1},
        {"type": "DEVICE_ATTR", "recordTime": 1500},
        {"type": "DRINK", "recordTime": 1000, "totalMl": 12, "drinkTime": 245}])
    check('einzelne Trinkvorgaenge, aelteste zuerst (Messwerte 23.09.)',
          drinks == [(1.0, 12, 245), (2.0, 4, 1)], str(drinks))

    # Wassermodus aus dem ausgeschalteten Zustand: alle Schritte gehen raus,
    # auch wenn der erste in 2020 laeuft.
    api = api_mod.PetlibroAPI()
    sent = []

    def command(path, body, timeout=None):
        sent.append((path, dict(body)))
        if path.endswith('waterModeSetting') and body.get('waterStopSwitch') is False:
            raise api_mod.PetlibroError(path, api_mod.CODE_DEVICE_TIMEOUT)
    api._command = command
    try:
        api.set_water_mode('SN', 'sensor_near', 15, 15, sensing=45,
                           currently_off=True)
        raised = None
    except api_mod.PetlibroError as e:
        raised = e.code
    paths = [p for p, _b in sent]
    check('aus -> Sensor: einschalten, Reichweite und Modus werden alle gesendet',
          len(sent) == 3 and paths[1].endswith('updateRadarSetting'), str(paths))
    check('... und die 2020 kommt am Ende an', raised == api_mod.CODE_DEVICE_TIMEOUT)

    # Nach einer Zaehler-Bewegung wird das Protokoll noch zweimal geholt.
    api = api_mod.PetlibroAPI()
    fetched = []
    api.fetch_records = lambda serial, size=20, days=7: fetched.append(1) or []
    feeder = dev_mod.wrap_device(dict(FEEDER_LIST), api=api)
    feeder.records_loaded_at = _t.time()
    api._maybe_fetch_records(feeder, full=False, moved=True)
    api._maybe_fetch_records(feeder, full=False, moved=False)
    api._maybe_fetch_records(feeder, full=False, moved=False)
    api._maybe_fetch_records(feeder, full=False, moved=False)
    check('Zaehler bewegt: jetzt und bei den zwei naechsten Abfragen',
          len(fetched) == 3, str(len(fetched)))


def test_drinks(dev_mod):
    print('== Trinkmengen pro Stunde ==')
    data = {"xdate": ["%02d:00" % h for h in range(24)],
            "waterIntake": [0, 0, 5, 0, 0, 0, 0, 14] + [0] * 6 + [16] + [0] * 9,
            "drinkTimes": [0, 0, 1, 0, 0, 0, 0, 2] + [0] * 6 + [1] + [0] * 9,
            "avgDrinkDuration": [0, 0, 1, 0, 0, 0, 0, 14] + [0] * 6 + [239] + [0] * 9}
    rows = dev_mod.drink_rows(data)
    check('nur Stunden mit Trinken (Messwerte vom 22.09.)',
          [r[0] for r in rows] == ['02:00', '07:00', '14:00'], str(rows))
    check('Stunde mit Ende, Menge, Anzahl, Dauer',
          rows[1] == ('07:00', '08:00', 14, 2, 14), str(rows[1]))
    check('23 Uhr endet um 00:00',
          dev_mod.drink_rows({"xdate": ["23:00"], "waterIntake": [3],
                              "drinkTimes": [1], "avgDrinkDuration": [5]})[0][1]
          == '00:00')
    check('Dauer in Worten: 239 Sekunden', dev_mod.duration_text(239)
          == '3 minutes 59 seconds', dev_mod.duration_text(239))
    check('eine Minute genau', dev_mod.duration_text(60) == '1 minute')


def test_expectations():
    print('== Ausstehende Aenderung wird nachgemeldet ==')
    spoken = []
    for name in ('wx', 'ui', 'tones'):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules['wx'].CallAfter = lambda f, *a, **k: f(*a, **k)
    sys.modules['ui'].message = lambda text: spoken.append(text)
    sys.modules['tones'].beep = lambda *a, **k: None
    cd = importlib.import_module('shcprobe.change_detection')

    class Plugin(cd._ChangeDetectionMixin):
        pass

    class Dev:
        uuid = 'petlibro_WF1'
        name = 'Brunnen'

    plugin = Plugin()
    plugin._petlibro_expected = {}
    plugin._expect_petlibro(Dev(), 'water_mode', 'constant', 'bestaetigt',
                            'nicht uebernommen')
    covered = plugin._check_petlibro_expectations(Dev(), {'water_mode': 'sensor_near'})
    # (the detection of a food-low warning runs further below)
    check('noch nicht da: Feld gedeckt, nichts angesagt',
          covered == {'water_mode'} and spoken == [], str(spoken))
    covered = plugin._check_petlibro_expectations(Dev(), {'water_mode': 'constant'})
    check('angekommen: Bestaetigung statt "anderswo geaendert"',
          spoken == ['bestaetigt'] and covered == {'water_mode'}, str(spoken))
    check('danach wird nicht mehr gewartet',
          plugin._petlibro_expected.get(Dev.uuid) == {})
    spoken.clear()
    plugin._expect_petlibro(Dev(), 'light', True, 'an', 'nicht geschaltet',
                            window=-1)
    plugin._check_petlibro_expectations(Dev(), {'light': False})
    check('nach Ablauf: gesagt, dass es nicht ankam',
          spoken == ['nicht geschaltet'], str(spoken))

    print('== Futter-knapp-Warnung im Hintergrund (Log vom 23.09.) ==')
    tones = []
    sys.modules['tones'].beep = lambda pitch, length, *a, **k: tones.append(pitch)
    dev_mod = sys.modules['shcprobe.petlibro_devices']
    hist = importlib.import_module('shcprobe.history')
    logged = []

    class History:
        def log_action(self, device, action, details="", source="local"):
            logged.append((action, details))

        def log_external_action(self, *a, **k):
            logged.append(a[3:])
    hist.get_history = lambda: History()
    plugin.announce_external_changes = True
    plugin._previous_petlibro_states = {}
    plugin._recent_petlibro_actions = {}
    plugin._petlibro_seen_records = {}
    for attr in ('feed_failed', 'feed_external', 'food_low', 'water_low',
                 'maintenance', 'battery', 'connection', 'settings'):
        setattr(plugin, f'notify_petlibro_{attr}', True)
    plugin.notify_petlibro_feed_planned = False
    feeder = dev_mod.wrap_device(dict(FEEDER_LIST), api=None)
    feeder.apply_real(FEEDER_REAL)
    feeder.apply_records([{"workRecords": [record("r1", 1000)]}])
    plugin._seed_petlibro_state(feeder)
    spoken.clear()
    feeder.apply_records([{"workRecords": [
        record("w", 3000, kind="SURPLUS_GRAIN_NOT_ENOUGH",
               event="SURPLUS_GRAIN_NOT_ENOUGH", alarm=True),
        record("r2", 2000, plan=2), record("r1", 1000)]}])
    feeder.apply_real({"surplusGrain": False})
    plugin._detect_petlibro_changes([feeder])
    check('keine "Fuetterung fehlgeschlagen"',
          not any('feeding failed' in t for t in spoken), str(spoken))
    check('die Mahlzeit steht im Verlauf, kein feed_failed',
          ('feed_planned', '3') in logged
          and not any(a == 'feed_failed' for a, *_ in logged), str(logged))
    check('"Futter fast leer" wird gesagt', any('food low' in t for t in spoken),
          str(spoken))
    check('... mit dem Hinweiston, nicht dem Fehlerton (200 Hz)',
          tones and 200 not in tones, str(tones))

    print('== Spaete Bestaetigung einer Handfuetterung ==')
    plugin._petlibro_feed_wait = {}
    spoken.clear()
    logged.clear()
    plugin._begin_petlibro_feed(feeder)
    plugin._end_petlibro_feed(feeder, confirmed=False)
    feeder.apply_records([{"workRecords": [
        record("m1", 5000, event="MANUAL_FEEDING_SUCCESS", amount=1, plan=0),
        record("w", 3000, kind="SURPLUS_GRAIN_NOT_ENOUGH",
               event="SURPLUS_GRAIN_NOT_ENOUGH", alarm=True),
        record("r2", 2000, plan=2), record("r1", 1000)]}])
    plugin._detect_petlibro_changes([feeder])
    check('als nachtraegliche Bestaetigung angesagt, nicht als "von anderswo"',
          any('confirmed after all' in t for t in spoken)
          and not any('elsewhere' in t for t in spoken), str(spoken))
    check('und im Verlauf als eigene Fuetterung',
          ('feed_manual', '1') in logged, str(logged))

    print('== Brunnen: Stoerung aus dem Protokoll ==')
    spoken.clear()
    fountain = dev_mod.wrap_device(dict(FOUNTAIN_LIST), api=None)
    fountain.apply_real(FOUNTAIN_REAL)
    fountain.apply_records([{"workRecords": [
        {"id": "d1", "type": "DRINK", "eventType": "DRINK_EVENT",
         "recordTime": 1000, "totalMl": 4, "drinkTime": 1}]}])
    plugin._seed_petlibro_state(fountain)
    fountain.apply_records([{"workRecords": [
        {"id": "e1", "type": "WATER_BOX_LEAVE_ERROR",
         "eventType": "WATER_BOX_LEAVE_ERROR", "alarmTag": True,
         "recordTime": 3000},
        {"id": "d2", "type": "DRINK", "eventType": "DRINK_EVENT",
         "recordTime": 2000, "totalMl": 12, "drinkTime": 40},
        {"id": "d1", "type": "DRINK", "eventType": "DRINK_EVENT",
         "recordTime": 1000, "totalMl": 4, "drinkTime": 1}]}])
    plugin._detect_petlibro_changes([fountain])
    check('Tank leer oder abgenommen wird angesagt, Trinken nicht',
          len(spoken) == 1 and 'water tank' in spoken[0], str(spoken))

    print('== Stoerung: benannt statt englisch, und einmal (Log vom 26.09.) ==')
    # 13:16 kam der Text der Cloud, "Water tank irregularity", und wurde so
    # vorgelesen; 13:20 kam der Eintrag WATER_BOX_LEAVE_ERROR zum selben
    # Ereignis und wurde ein zweites Mal angesagt und protokolliert.
    plugin._petlibro_recent_faults = {}
    spoken.clear()
    logged.clear()
    fountain = dev_mod.wrap_device(dict(FOUNTAIN_LIST), api=None)
    fountain.apply_real(dict(FOUNTAIN_REAL))
    fountain.apply_records([{"workRecords": [
        {"id": "d1", "type": "DRINK", "eventType": "DRINK_EVENT",
         "recordTime": 1000, "totalMl": 4, "drinkTime": 1}]}])
    plugin._seed_petlibro_state(fountain)
    fountain.apply_real({"errorState": True,
                         "exceptionMessage": "Water tank irregularity"})
    plugin._detect_petlibro_changes([fountain])
    check('der Zustand wird mit Namen angesagt, nicht mit dem Text der Cloud',
          len(spoken) == 1 and 'water tank empty or not seated' in spoken[0]
          and 'irregularity' not in spoken[0], str(spoken))
    check('und steht im Verlauf, so wie das Geraet ihn sandte',
          ('device_fault', 'Water tank irregularity') in logged, str(logged))
    spoken.clear()
    fountain.apply_records([{"workRecords": [
        {"id": "e2", "type": "WATER_BOX_LEAVE_ERROR",
         "eventType": "WATER_BOX_LEAVE_ERROR", "alarmTag": True,
         "recordTime": 5000},
        {"id": "d1", "type": "DRINK", "eventType": "DRINK_EVENT",
         "recordTime": 1000, "totalMl": 4, "drinkTime": 1}]}])
    plugin._detect_petlibro_changes([fountain])
    check('der Eintrag Minuten spaeter wird nicht noch einmal angesagt',
          spoken == [], str(spoken))
    check('und nicht noch einmal in den Verlauf geschrieben',
          sum(1 for action, *_ in logged if action == 'device_fault') == 1,
          str(logged))
    fountain.apply_real({"errorState": False, "exceptionMessage": ""})
    plugin._detect_petlibro_changes([fountain])
    spoken.clear()
    fountain.apply_real({"errorState": True, "exceptionMessage": "Pump blocked"})
    plugin._detect_petlibro_changes([fountain])
    check('eine unbekannte Stoerung: als Text von Petlibro gekennzeichnet',
          len(spoken) == 1
          and "fault, “Pump blocked” (Petlibro's text)" in spoken[0],
          str(spoken))

    print('== Die Chronik nennt Stoerungen beim Namen ==')
    detail = hist._detail_text
    check('WATER_BOX_LEAVE_ERROR',
          detail('device_fault', 'WATER_BOX_LEAVE_ERROR')
          == 'water tank empty or not seated on its base')
    check('der Text der Cloud fuer dasselbe',
          detail('device_fault', 'Water tank irregularity')
          == 'water tank empty or not seated on its base')
    check('die alte Warnung vom 23.09.',
          detail('feed_failed', 'SURPLUS_GRAIN_NOT_ENOUGH') == 'food low')
    check('ein unbekannter Code als Geraetecode',
          detail('device_fault', 'PUMP_BLOCK_ERROR') == 'device code PUMP_BLOCK_ERROR')
    check('keine Stoerung ohne Angabe erfunden', detail('device_fault', '') == '')

    print('== Eine Aenderung von hier kommt nicht als "anderswo" zurueck ==')
    # 26.09., 13:14 Wassermodus "Aus" hier gestellt, 13:16 als Aenderung von
    # anderswo angesagt: das Fenster fuer eigene Aenderungen ist 30 s, der
    # naechste Abruf im Hintergrund kam nach zwei Minuten.
    spoken.clear()
    logged.clear()
    fountain = dev_mod.wrap_device(dict(FOUNTAIN_LIST), api=None)
    fountain.apply_real(dict(FOUNTAIN_REAL))
    plugin._seed_petlibro_state(fountain)
    fountain.apply_real({"waterStopSwitch": True})  # das Zuruecklesen
    plugin._accept_local_petlibro_change(fountain)
    plugin._recent_petlibro_actions = {}  # die 30 s sind vorbei
    fountain.apply_real({"weight": 300.0, "weightPercent": 10})
    plugin._detect_petlibro_changes([fountain])
    check('der eigene Wassermodus wird nicht als Aenderung angesagt',
          not any('water mode' in t for t in spoken), str(spoken))
    check('und nicht als Aenderung von anderswo protokolliert',
          not logged or all(entry[0] != 'set_water_mode' for entry in logged),
          str(logged))
    check('was mit demselben Abruf kam, wird trotzdem gesagt',
          any('water low' in t for t in spoken), str(spoken))
    spoken.clear()
    fountain.apply_real({"waterStopSwitch": False,
                         "radarSensingLevel": "FarTrigger"})
    plugin._detect_petlibro_changes([fountain])
    check('eine echte Aenderung von anderswo wird weiter angesagt',
          any('water mode' in t for t in spoken), str(spoken))


def main():
    dev_mod, api_mod, errors_mod = load()
    test_fountain_commands(dev_mod, api_mod)
    test_flow_duration(dev_mod, api_mod)
    test_cloud_lags_behind(dev_mod, api_mod)
    test_review(dev_mod, api_mod)
    test_drinks(dev_mod)
    test_expectations()
    test_settings_enable()
    test_readings(dev_mod)
    test_records(dev_mod)
    test_feed_now(dev_mod, api_mod)
    test_api_rules(api_mod, errors_mod)
    test_ui_rules()
    print()
    if FAILED:
        print("FEHLGESCHLAGEN:")
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print("GESAMT: ALLE TESTS OK")
    return 0


if __name__ == '__main__':
    sys.exit(main())
