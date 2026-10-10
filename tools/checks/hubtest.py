# -*- coding: utf-8 -*-
"""Testet, dass die Sensoren eines Hubs nicht verschwinden, wenn er schweigt.

Jeder Sensor an einem Meross-Hub trägt die **UUID des Hubs**; erst
``unique_id`` hängt die Subdevice-ID an. Daran hing gleich mehreres:

* Die Rettungsschleife in ``_refresh_devices_impl`` schlüsselte nach ``uuid``.
  Sobald der Hub in der neuen Liste stand, galt seine UUID als bekannt - und
  jeder Sensor der alten Liste fiel durch.
* Eine Entdeckung ersetzt die Geräteliste als Ganzes. Antwortet der Hub gerade
  nicht, liefert ``get_subdevices()`` nichts, und die Sensoren fehlten, ohne
  dass jemand zwischen „aus dem Konto entfernt" und „hat nicht geantwortet"
  unterscheiden konnte.
* Zurück kamen sie erst beim nächsten NVDA-Start: jede Neuentdeckung hängt an
  „es gibt überhaupt kein Meross-Gerät mehr", und der Hub ist ja da.

In den Favoriten stand danach „(nicht verfügbar)" - gespeichert blieb der
Eintrag, benutzbar war er nicht.

Geprüft wird die Übernahme selbst (sie darf nichts wiederbeleben, was wirklich
weg ist) und die Verdrahtung der drei Stellen.
"""
import ast
import io
import os
import sys

BASE = os.environ.get(
    'SHC',
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'globalPlugins', 'SmartHomeControl'))

FAILED = []


def check(name, cond, detail=''):
    print(f"  {'OK  ' if cond else 'FEHL'}   {name}" + (f'  ({detail})' if detail else ''))
    if not cond:
        FAILED.append(name)


def source(name):
    return io.open(os.path.join(BASE, name + '.py'), encoding='utf-8').read()


def grab(name, func_name):
    """Holt eine Funktion aus der echten Quelldatei - getestet wird, was
    ausgeliefert wird, ohne NVDA importieren zu muessen."""
    tree = ast.parse(source(name))
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == func_name)
    env = {}
    exec(compile(ast.Module([node], []), '<probe>', 'exec'), env)
    return env[func_name]


class Dev:
    """Ein Meross-Geraet, so viel davon wie die Uebernahme anfasst."""

    def __init__(self, uuid, name, subdevice_id=None, is_hub=False):
        self.uuid = uuid
        self.name = name
        self._subdevice_id = subdevice_id
        self.is_hub = is_hub
        self.is_offline = False

    @property
    def unique_id(self):
        return f"{self.uuid}_{self._subdevice_id}" if self._subdevice_id \
            else self.uuid

    def __repr__(self):
        return f"<{self.name}>"


def main():
    carry = grab('meross_devices', 'carry_over_hub_subdevices')

    hub = Dev('hub-1', 'Hub', is_hub=True)
    sensor_a = Dev('hub-1', 'Schlafzimmer', subdevice_id='01')
    sensor_b = Dev('hub-1', 'Keller', subdevice_id='02')
    plug = Dev('plug-1', 'Steckdose')

    print('== Der Hub antwortet, nennt aber keine Sensoren ==')
    carried = carry([hub, sensor_a, sensor_b, plug], [hub, plug])
    check('beide Sensoren bleiben', len(carried) == 2, str(carried))
    check('und zwar als nicht erreichbar gekennzeichnet',
          all(d.is_offline for d in carried))
    check('die Steckdose wird NICHT wiederbelebt - ein normales Geraet, das '
          'fehlt, ist aus dem Konto entfernt',
          all(d.name != 'Steckdose' for d in carried))

    print('== Der Hub nennt einen davon ==')
    fresh_sensor = Dev('hub-1', 'Schlafzimmer', subdevice_id='01')
    carried = carry([hub, sensor_a, sensor_b], [hub, fresh_sensor])
    check('nur der fehlende wird uebernommen',
          [d.name for d in carried] == ['Keller'], str(carried))

    print('== Der Hub selbst ist weg ==')
    carried = carry([hub, sensor_a], [plug])
    check('dann sind seine Sensoren auch wirklich weg', carried == [],
          str(carried))

    print('== Ein anderes Konto ==')
    other_hub = Dev('hub-9', 'Fremder Hub', is_hub=True)
    carried = carry([hub, sensor_a, sensor_b], [other_hub])
    check('nichts aus dem alten Konto taucht wieder auf', carried == [],
          str(carried))

    print('== Nichts zu tun ==')
    check('leere Vorgeschichte ergibt nichts', carry([], [hub]) == [])
    check('und eine vollstaendige Liste auch',
          carry([hub, sensor_a], [hub, sensor_a]) == [])

    print('== Der Schluessel: unique_id, nicht uuid ==')
    init = source('__init__')
    check('die Rettungsschleife schluesselt nach der Identitaet, nicht nach '
          'der uuid',
          'known_keys = {_device_key(d) for d in new_list}' in init)
    check('die alte Fassung nach uuid ist fort',
          'known_uuids = {d.uuid for d in new_list}' not in init)
    # _device_key ist der gemeinsame Schluessel des Add-ons (history.py) und
    # nimmt unique_id vor uuid - genau das, woran die Schleife haengt.
    hist = source('history')
    body = hist.partition('def _device_key')[2][:600]
    check('und _device_key nimmt unique_id vor uuid',
          'unique_id' in body and body.index('unique_id') < body.index('uuid',
                                                                      body.index('unique_id') + 1))

    print('== Die Uebernahme ist verdrahtet ==')
    check('bei der Anmeldung', init.count('carry_over_hub_subdevices(') >= 2,
          f"{init.count('carry_over_hub_subdevices(')} Aufrufe")
    check('und beim Nachladen der Geraeteliste',
          'carry_over_hub_subdevices(\n                            self.devices'
          in init or 'all_devs = all_devs + carry_over_hub_subdevices(' in init)

    print('== Der Hub meldet, was er hat ==')
    meross = source('meross_api')
    body = meross.partition('async def update_single_hub')[2].partition(
        '\n                # Update all hubs')[0]
    check('die Untergeraete werden bei jeder Abfrage nachgesehen',
          'get_subdevices()' in body, 'in update_single_hub')
    check('nur bekannte Sensortypen', 'is_sensor_type(subdev_type)' in body)
    check('nichts wird doppelt angelegt', 'in known_ids' in body)
    check('die Funde stehen im Bericht',
          "'discovered': discovered" in meross)
    check('und der Bericht wird vorher geleert - sonst gelten nach einer '
          'Zeitueberschreitung die Funde der vorigen Abfrage',
          "'discovered': []" in meross)

    print('== Der Zeitplaner uebernimmt sie ==')
    sched = source('scheduler')
    check('es gibt eine Uebernahme', 'def _adopt_meross_devices' in sched)
    check('sie wird bei jeder Meross-Abfrage aufgerufen',
          "self._adopt_meross_devices(report.get('discovered'))" in sched)
    adopt = sched.partition('def _adopt_meross_devices')[2].partition(
        '\n    def ')[0]
    check('sie legt nichts doppelt an', 'unique_id' in adopt)
    check('und reicht die Liste an die Push-Zustellung weiter - sonst kaeme '
          'der Sensor zwar in den Baum, seine Meldungen aber nirgends an',
          'set_wrapped_devices' in adopt)

    print('== Der Favorit sagt, warum er fehlt ==')
    favs = source('dialog_favorites')
    check('es gibt eine gemeinsame Beschriftung',
          'def _unavailable_favorite_label' in favs)
    check('sie wird fuer alle fuenf Plattformen benutzt',
          favs.count('self._unavailable_favorite_label(') == 5,
          f"{favs.count('self._unavailable_favorite_label(')} Stellen")
    check('und es steht kein roher Text mehr im Baum',
          favs.count('_("{name} (not available)")') == 1,
          'nur noch in der gemeinsamen Beschriftung')
    check('der Grund kommt aus dem Plattform-Satz',
          'platform_health.status_suffix(' in favs)

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
