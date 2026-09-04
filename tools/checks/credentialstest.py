# -*- coding: utf-8 -*-
"""Testet den Zugangsdaten-Speicher und vor allem die einmalige Umsiedlung.

NVDA schreibt beim Start die vollstaendige Konfiguration ins Protokoll. Was
in nvda.ini steht, reist also in jedem eingeschickten Log mit - die
E-Mail-Adresse stand dort im Klartext. Die Werte liegen deshalb in einer
eigenen Datei.

Der gefaehrliche Teil ist der Umzug: wird in der Konfiguration geloescht,
bevor die Datei wirklich geschrieben ist, sind die Zugangsdaten weg und der
Nutzer muss alles neu eintippen. Genau das pruefen diese Tests.
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile

BASE = os.environ.get(
    'SHC',
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'globalPlugins', 'SmartHomeControl'))

FAILED = []


def check(name, cond, detail=''):
    print(f"  {'OK  ' if cond else 'FEHL'}   {name}" + (f'  ({detail})' if detail else ''))
    if not cond:
        FAILED.append(name)


def fresh_module(directory):
    """Laedt credential_store neu und legt die Datei in ``directory``."""
    spec = importlib.util.spec_from_file_location(
        'credential_store_probe', os.path.join(BASE, 'credential_store.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.CREDENTIALS_FILE = os.path.join(directory, 'creds.json')
    return mod


class Conf(dict):
    """Verhaelt sich wie der Konfigurationsabschnitt des Add-ons."""


def main():
    print('== Frische Installation ==')
    tmp = tempfile.mkdtemp()
    try:
        cs = fresh_module(tmp)
        conf = Conf()
        values = cs.load(conf)
        check('ohne Datei und ohne Konfiguration bleibt alles leer',
              set(values) == set(cs.SECRET_KEYS) and not any(values.values()))
        check('es wird auch keine Datei angelegt',
              not os.path.exists(cs.CREDENTIALS_FILE))

        print('== Umzug aus der Konfiguration ==')
        conf = Conf({
            'email': 'test@example.com',
            'password': 'ENC:abc',
            'vesyncEmail': 'vs@example.com',
            'vesyncPassword': 'ENC:def',
            'cozytouchToken': 'ENC:ghi',
            'netatmoRedirectPort': 8474,      # kein Geheimnis, bleibt
        })
        values = cs.load(conf)
        check('die Werte kommen vollstaendig zurueck',
              values['email'] == 'test@example.com'
              and values['password'] == 'ENC:abc'
              and values['vesyncPassword'] == 'ENC:def')
        check('die Datei ist geschrieben', os.path.isfile(cs.CREDENTIALS_FILE))
        check('in der Konfiguration steht nichts mehr davon',
              conf['email'] == '' and conf['password'] == ''
              and conf['vesyncPassword'] == '')
        check('was kein Geheimnis ist, bleibt unberuehrt',
              conf['netatmoRedirectPort'] == 8474)
        on_disk = json.load(io.open(cs.CREDENTIALS_FILE, encoding='utf-8'))
        check('die Datei enthaelt genau die vorgesehenen Schluessel',
              set(on_disk) == set(cs.SECRET_KEYS))

        print('== Zweiter Start ==')
        conf2 = Conf({'email': '', 'password': ''})
        values2 = cs.load(conf2)
        check('gelesen wird jetzt aus der Datei',
              values2['email'] == 'test@example.com'
              and values2['cozytouchToken'] == 'ENC:ghi')

        print('== Speichern ==')
        conf3 = Conf({'email': 'alt@example.com'})
        ok = cs.save(conf3, dict(values2, email='neu@example.com'))
        check('Speichern meldet Erfolg', ok is True)
        check('und raeumt die Konfiguration mit auf', conf3['email'] == '')
        check('der neue Wert steht in der Datei',
              cs.load(Conf())['email'] == 'neu@example.com')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('== Wenn das Schreiben scheitert ==')
    tmp = tempfile.mkdtemp()
    try:
        cs = fresh_module(tmp)
        # Ein Verzeichnis anstelle der Datei: os.replace scheitert zuverlaessig.
        os.makedirs(cs.CREDENTIALS_FILE)
        conf = Conf({'email': 'test@example.com', 'password': 'ENC:abc'})
        values = cs.load(conf)
        check('die Werte gehen trotzdem nicht verloren',
              values['email'] == 'test@example.com')
        check('und bleiben in der Konfiguration stehen',
              conf['email'] == 'test@example.com',
              'sonst waeren sie weg')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('== Beschaedigte Datei ==')
    tmp = tempfile.mkdtemp()
    try:
        cs = fresh_module(tmp)
        io.open(cs.CREDENTIALS_FILE, 'w', encoding='utf-8').write('{kaputt')
        conf = Conf({'email': 'test@example.com'})
        values = cs.load(conf)
        check('das Add-on startet trotzdem', isinstance(values, dict))
        check('und faellt auf die Konfiguration zurueck',
              values['email'] == 'test@example.com')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('== Der Code benutzt den Speicher wirklich ==')
    src = io.open(os.path.join(BASE, '__init__.py'), encoding='utf-8').read()
    check('load_settings holt die Geheimnisse dort',
          'secrets = credential_store.load(conf)' in src)
    check('save_settings schreibt sie dorthin',
          'credential_store.save(conf, secrets)' in src)
    for key in ('email', 'password', 'vesyncPassword', 'cozytouchToken',
                'netatmoRefreshToken'):
        check(f'"{key}" wird nicht mehr in die Konfiguration geschrieben',
              f'conf["{key}"]' not in src)

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
