# -*- coding: utf-8 -*-
"""Testet die Meross-Anbindung gegen die mitgelieferte meross_iot.

Die anderen Pruefungen arbeiten mit Attrappen, und eine Attrappe antwortet
genau das, was der Test von ihr erwartet. In dieser Luecke lebten zwei
Fehler, bis zur Version 26.10.2:

* Der Wassersensor fragte ``is_triggered`` ab. Das gibt es in keiner Version
  von meross_iot - der Zustand heisst dort ``is_leaking``. Ein Leck wurde nie
  angesagt, nie in die Chronik geschrieben, und der Geraetebaum zeigte
  "kein Wasser". wateralarmtest.py bemerkte es nicht: seine Attrappe
  lieferte ``is_water_detected`` selbst.
* Nach dem ersten Abruf zeigte jeder Sensor eines Hubs auf den HUB. Alle
  tragen dessen uuid, und die Suche nach uuid liefert den Hub zuerst. Ein
  MS100 las danach den ersten Sensor des Hubs statt sich selbst.

Dazu, was ein Abruf die Cloud kostet: meross_iot setzt eine Geraeteklasse aus
Mixins zusammen, und jedes mit eigenem ``async_update`` schickt eine
Nachricht mehr. Gezaehlt wurde immer eine.

Geladen wird meross_iot aus lib/, ohne C-Erweiterungen (die Variablen
*_NO_EXTENSIONS) - so laeuft es auf jedem Python, auch auf dem 3.11 des
Workflows, fuer das lib/ keine passenden .pyd hat. Weder NVDA noch ein Konto
noch das Netz werden gebraucht: der Manager ist eine Attrappe, die Geraete
und ihre Klassen sind die echten.
"""
import asyncio
import importlib
import os
import sys
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.environ.get(
    'SHC', os.path.join(ROOT, 'globalPlugins', 'SmartHomeControl'))
LIB = os.path.join(ROOT, 'lib')

FAILED = []


def check(name, cond, detail=''):
    print(f"  {'OK  ' if cond else 'FEHL'}   {name}" + (f'  ({detail})' if detail else ''))
    if not cond:
        FAILED.append(name)


def load():
    """meross_iot aus lib/, das Add-on mit den NVDA-Modulen als Attrappen."""
    for var in ('AIOHTTP_NO_EXTENSIONS', 'MULTIDICT_NO_EXTENSIONS',
                'YARL_NO_EXTENSIONS', 'FROZENLIST_NO_EXTENSIONS',
                'PROPCACHE_NO_EXTENSIONS'):
        os.environ[var] = '1'
    # Die reinen Python-Quellen liegen in beiden Architekturordnern; welcher
    # es ist, spielt ohne Erweiterungen keine Rolle.
    for arch in ('cp313-amd64', 'cp311-win32'):
        path = os.path.join(LIB, '_arch', arch)
        if os.path.isdir(path):
            sys.path.append(path)
            break
    sys.path.append(LIB)

    class _Log:
        def __getattr__(self, name):
            return lambda *a, **k: None

    log_mod = types.ModuleType('logHandler')
    log_mod.log = _Log()
    sys.modules['logHandler'] = log_mod
    addon_mod = types.ModuleType('addonHandler')
    addon_mod.initTranslation = lambda: None
    sys.modules['addonHandler'] = addon_mod
    pkg = types.ModuleType('shc')
    pkg.__path__ = [BASE]
    sys.modules['shc'] = pkg
    devices = importlib.import_module('shc.meross_devices')
    if not devices.load_meross():
        raise SystemExit('meross_iot aus lib/ laesst sich nicht laden')
    api = importlib.import_module('shc.meross_api')
    return devices, api


class Manager:
    """Die Registry des Managers - so viel, wie die Geraete brauchen."""

    def __init__(self):
        self.devs = []

    def find_devices(self, device_uuids=None, **kwargs):
        return [d for d in self.devs
                if device_uuids is None or d.uuid in device_uuids]


def build(cls_name, abilities, base, uuid, manager, **kwargs):
    """Eine Geraeteklasse, wie meross_iot sie aus den Faehigkeiten baut."""
    from meross_iot.device_factory import _build_cached_type
    cls = _build_cached_type(cls_name, abilities, base)
    return cls(device_uuid=uuid, manager=manager, domain='x', port=1,
               dev_name=kwargs.get('name', uuid),
               device_type=kwargs.get('type', 'mss310'), channels=[])


def count_messages(device, answers):
    """Zaehlt, was ein async_update() an die Cloud schickt."""
    sent = []

    async def execute(method, namespace, payload, timeout=None, **kwargs):
        sent.append(namespace)
        return answers.get(namespace, {})

    device._execute_command = execute
    asyncio.run(device.async_update())
    return sent


def test_water(md):
    from meross_iot.controller.subdevice import Ms405Sensor
    from meross_iot.controller.device import HubDevice
    from meross_iot.model.enums import Namespace
    print('== Der Wassersensor liest den Zustand, den meross_iot fuehrt ==')
    manager = Manager()
    hub = HubDevice(device_uuid='HUB', manager=manager, domain='x', port=1,
                    dev_name='Hub', device_type='msh300', channels=[])
    manager.devs.append(hub)
    leak = Ms405Sensor('HUB', 'w1', manager, subDeviceType='ms405',
                       subDeviceName='Bad')
    wrapper = md.MerossDevice(leak)
    check('ein Wassersensor', wrapper.is_water_sensor)
    check('ohne Meldung: unbekannt, nicht "trocken"',
          wrapper.is_water_detected() is None,
          repr(wrapper.is_water_detected()))

    def report(wet, ts):
        asyncio.run(leak.async_handle_subdevice_notification(
            Namespace.HUB_SENSOR_ALL,
            {'id': 'w1', 'online': {'status': 1},
             'waterLeak': {'latestWaterLeak': 1 if wet else 0,
                           'latestSampleTime': ts}}))

    now = int(time.time())
    report(True, now)
    check('meross_iot fuehrt das Leck unter is_leaking', leak.is_leaking is True)
    check('das Add-on sieht das Leck', wrapper.is_water_detected() is True,
          repr(wrapper.is_water_detected()))
    report(False, now + 60)
    check('und wieder trocken', wrapper.is_water_detected() is False,
          repr(wrapper.is_water_detected()))


def make_api(api_mod):
    """MerossAPI ohne Anmeldung, mit eigener Ereignisschleife."""
    api = object.__new__(api_mod.MerossAPI)
    api._running = True
    api._msg_budget = {}
    api._budget_lock = threading.Lock()
    api._last_metrics_fetch = {}
    api._on_throttle_callback = None
    api._last_throttle_notify = 0.0
    api.loop = asyncio.new_event_loop()
    threading.Thread(target=api.loop.run_forever, daemon=True).start()
    return api


def test_hub_group(md, api_mod):
    from meross_iot.controller.subdevice import Ms100Sensor, Ms405Sensor
    from meross_iot.controller.device import HubDevice
    from meross_iot.model.enums import Namespace
    print('== Jeder Sensor am Hub behaelt nach dem Abruf sein eigenes Objekt ==')
    manager = Manager()
    hub = build('msh300:test', {
        Namespace.SYSTEM_ALL.value: {},
        Namespace.HUB_SENSOR_ALL.value: {},
        Namespace.HUB_SUBDEVICELIST.value: {},
    }, HubDevice, 'HUB', manager, name='Hub', type='msh300')
    manager.devs.append(hub)
    leak = Ms405Sensor('HUB', 'w1', manager, subDeviceType='ms405',
                       subDeviceName='Bad')
    cellar = Ms100Sensor('HUB', 't1', manager, subDeviceType='ms100',
                         subDeviceName='Keller')
    attic = Ms100Sensor('HUB', 't2', manager, subDeviceType='ms100',
                        subDeviceName='Dach')
    for sub in (leak, cellar, attic):
        manager.devs.append(sub)
        hub.register_subdevice(sub)

    # Was der Hub auf HUB_SENSOR_ALL antwortet - fuer alle drei Sensoren.
    sensor_all = {'all': [
        {'id': 'w1', 'online': {'status': 1},
         'waterLeak': {'latestWaterLeak': 1,
                       'latestSampleTime': int(time.time())}},
        {'id': 't1', 'online': {'status': 1},
         'temperature': {'latest': 185}, 'humidity': {'latest': 610}},
        {'id': 't2', 'online': {'status': 1},
         'temperature': {'latest': 231}, 'humidity': {'latest': 420}},
    ]}
    answers = {Namespace.SYSTEM_ALL: {'all': {}},
               Namespace.HUB_SENSOR_ALL: sensor_all}
    sent = []

    async def execute(method, namespace, payload, timeout=None, **kwargs):
        sent.append(namespace)
        return answers.get(namespace, {})

    hub._execute_command = execute
    asyncio.run(hub.async_update())
    sent.clear()

    w_hub, w_leak, w_cellar, w_attic = (
        md.MerossDevice(d) for d in (hub, leak, cellar, attic))
    before = (w_cellar.get_temperature(), w_attic.get_temperature())
    check('vor dem Abruf: jeder Sensor seine Temperatur', before == (18.5, 23.1),
          str(before))

    api = make_api(api_mod)
    api.manager = manager
    # Die Reihenfolge ist Absicht: ein Sensor vor dem Hub. Vertreter der
    # Gruppe soll trotzdem der Hub sein.
    group = [w_cellar, w_hub, w_leak, w_attic]
    api.update_device_status(group)

    pairs = ((w_hub, hub), (w_leak, leak), (w_cellar, cellar), (w_attic, attic))
    check('kein Sensor zeigt danach auf den Hub',
          all(w._device is obj for w, obj in pairs),
          str([type(w._device).__name__ for w, _obj in pairs]))
    after = (w_cellar.get_temperature(), w_attic.get_temperature())
    check('nach dem Abruf: weiter jeder seine eigene', after == (18.5, 23.1),
          str(after))
    check('das Leck bleibt sichtbar', w_leak.is_water_detected() is True,
          repr(w_leak.is_water_detected()))
    # Die Batterieabfrage laeuft daneben, mit eigenem Takt und eigener
    # Buchung (_poll_hub_batteries).
    updates = [n for n in sent if n != Namespace.HUB_BATTERY]
    expected = api_mod._update_cost(hub)
    check('der Hub wird pro Abruf einmal gefragt, nicht zweimal',
          len(updates) == expected == 2, f'{len(updates)} Nachrichten: {updates}')
    tokens = api._msg_budget.get('HUB', {}).get('tokens')
    check('und das Budget zaehlt, was gesendet wurde',
          tokens is not None and abs(
              tokens - (api_mod.MEROSS_BUDGET_BURST - len(sent))) < 0.1,
          f'{tokens}')

    print('== Ein uebernommener Sensor kommt zurueck, wenn sein Hub ihn nennt ==')
    # Bei einer Neuanmeldung, bei der der Hub schwieg, wird der Sensor aus
    # der alten Liste uebernommen und als offline markiert. Er blieb das bis
    # zur naechsten Anmeldung, auch wenn der Hub ihn laengst wieder meldete.
    stale = md.MerossDevice(attic)
    old_object = object()
    stale._device = old_object
    stale.is_offline = True
    api._msg_budget.clear()
    api.update_device_status([w_hub, w_cellar, w_leak, stale])
    check('wieder online', stale.is_offline is False)
    check('mit dem Objekt der neuen Sitzung', stale._device is attic,
          type(stale._device).__name__)
    report = api.last_update_report
    check('und nicht zusaetzlich als neu gemeldet',
          not report.get('discovered'), str(report.get('discovered')))
    api.loop.call_soon_threadsafe(api.loop.stop)


def test_cost(api_mod):
    from meross_iot.controller.device import BaseDevice
    from meross_iot.model.enums import Namespace
    print('== Was ein Abruf kostet, steht in der Klasse ==')
    manager = Manager()
    plain = build('mss210:test', {
        Namespace.SYSTEM_ALL.value: {},
        Namespace.CONTROL_TOGGLEX.value: {},
    }, BaseDevice, 'P1', manager)
    runtime = build('mss310:test', {
        Namespace.SYSTEM_ALL.value: {},
        Namespace.SYSTEM_RUNTIME.value: {},
        Namespace.CONTROL_TOGGLEX.value: {},
    }, BaseDevice, 'P2', manager)
    answers = {Namespace.SYSTEM_ALL: {'all': {}},
               Namespace.SYSTEM_RUNTIME: {'runtime': {}}}
    for device, label in ((plain, 'Steckdose ohne Runtime'),
                          (runtime, 'Steckdose mit Runtime')):
        sent = count_messages(device, answers)
        cost = api_mod._update_cost(device)
        check(f'{label}: {len(sent)} Nachricht(en), so viele werden gezaehlt',
              cost == len(sent), f'gezaehlt {cost}, gesendet {sent}')
    check('mit Runtime sind es zwei - gezaehlt wurde frueher eine',
          api_mod._update_cost(runtime) == 2)


def test_switching_charged(api_mod):
    print('== Schalten kostet das Budget wie jede andere Nachricht ==')
    api = make_api(api_mod)

    class Plug:
        uuid = 'P9'
        name = 'Lampe'
        type = 'mss210'

        async def async_turn_on(self, channel=0):
            return None

        async def async_turn_off(self, channel=0):
            return None

    manager = Manager()
    manager.devs.append(Plug())
    api.manager = manager
    api.set_device_state('P9', True)
    tokens = api._msg_budget.get('P9', {}).get('tokens')
    check('ein Schaltbefehl zieht eine Nachricht ab',
          tokens is not None
          and abs(tokens - (api_mod.MEROSS_BUDGET_BURST - 1)) < 0.1, f'{tokens}')
    for _i in range(3 * api_mod.MEROSS_BUDGET_BURST):
        api._charge_budget('P9', 1)
    check('der Benutzer wird nie abgewiesen, das Budget geht nur ins Minus',
          api._msg_budget['P9']['tokens'] >= -api_mod.MEROSS_BUDGET_BURST - 0.1,
          f"{api._msg_budget['P9']['tokens']}")
    check('und die Abfragen warten dann',
          not api._consume_budget('P9', 1, reserve=api_mod.MEROSS_BUDGET_RESERVE))
    api.loop.call_soon_threadsafe(api.loop.stop)


def test_outlet_push(api_mod):
    print('== Eine fremd geschaltete Steckdose wird als Steckdose gemeldet ==')
    # Der Kanal ging bei der Weitergabe verloren; die Chronik schrieb die
    # Schaltung unter die ganze Leiste.
    api = object.__new__(api_mod.MerossAPI)
    api._device_state_cache = {}
    api._wrapped_devices_lock = threading.Lock()
    api.manager = None
    heard = []

    class Outlet:
        def __init__(self, index, name):
            self.channel_index = index
            self.name = name

        def mark_push_update(self, state):
            pass

    class Strip:
        uuid = 'S1'
        name = 'Garten'
        is_multi_channel = True

        def get_channels(self):
            return [Outlet(1, 'Garten: Pumpe'), Outlet(2, 'Garten: Licht')]

    api._wrapped_devices = [Strip()]
    api._on_device_state_changed_callback = (
        lambda *args: heard.append(args))
    target = types.SimpleNamespace(uuid='S1', name='Garten')
    api._process_toggle_notification(
        'S1', {'togglex': [{'channel': 2, 'onoff': 1}]}, [target])
    check('eine Meldung', len(heard) == 1, str(heard))
    check('mit Name und Nummer der Steckdose',
          heard and heard[0][0] == 'Garten: Licht' and heard[0][4] == 2,
          str(heard))


def main():
    md, api_mod = load()
    test_water(md)
    test_hub_group(md, api_mod)
    test_cost(api_mod)
    test_switching_charged(api_mod)
    test_outlet_push(api_mod)
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
