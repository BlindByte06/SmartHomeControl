# -*- coding: utf-8 -*-
"""Testet den Zugangsdaten-Speicher und vor allem die einmalige Umsiedlung.

NVDA schreibt beim Start die vollstaendige Konfiguration ins Protokoll. Was
in nvda.ini steht, reist also in jedem eingeschickten Log mit - die
E-Mail-Adresse stand dort im Klartext. Die Werte liegen deshalb in einer
eigenen Datei.

Der gefaehrliche Teil ist der Umzug: wird in der Konfiguration geloescht,
bevor die Datei wirklich geschrieben ist, sind die Zugangsdaten weg und der
Nutzer muss alles neu eintippen. Genau das pruefen diese Tests.

Dazu zwei Wege, auf denen ein Wert spaeter noch verloren gehen kann:

* Gleichzeitiges Schreiben. save_settings() laeuft auch aus
  Hintergrund-Threads - der Login-Thread und die Reauth-Rueckrufe von
  VeSync und Cozytouch rufen es auf. Zwei davon stritten sich um eine
  feste Datei "<name>.tmp"; unter Windows ist das ein gescheitertes
  Schreiben, kein kaputter Bestand, also blieb die Datei heil und der
  NEUE Wert fiel weg. Genau so verschwindet ein Token, das die Cloud
  gerade gedreht hat.
* Ein Schluessel, den values gar nicht mitbringt. Der galt frueher als
  leer und wurde ueberschrieben. Das ist die Ablage fuer einen Wert, den
  dieser Rechner nicht entschluesseln kann: er muss stehen bleiben, denn
  auf dem Rechner, auf dem er verschluesselt wurde, ist er gueltig.
* Und der Weg dorthin: decrypt_dpapi gibt bei einem Fehlschlag "" zurueck,
  damit kein Geheimtext als Passwort in die Cloud geht. Eine Ebene hoeher
  hiess das, dass load_settings alles leerte und das naechste
  save_settings diese Leere ueber den noch gueltigen Geheimtext schrieb.
  DPAPI scheitert, sobald Windows-Konto oder Rechner andere sind - ein
  portables NVDA auf einem Stick erreicht so jeden zweiten Rechner, und
  die Datei liegt neben dem Add-on-Ordner und reist mit. Die Passwoerter
  ueberstehen das (sie bleiben verschluesselt im Speicher und werden
  unveraendert zurueckgeschrieben), die Tokens nicht - und die
  Netatmo-Kennung samt Geheimnis ist von Hand aus dem Entwicklerportal
  abgetippt und durch keine Neuanmeldung zu ersetzen.
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import threading

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


def load_security():
    """Laedt security.py mit gestubbtem logHandler; None wenn das nicht geht.

    Braucht Windows fuer DPAPI - anderswo faellt das Modul auf AES zurueck,
    und der Test bleibt trotzdem gueltig: geprueft wird die Unterscheidung
    zwischen "leer", "Klartext" und "nicht zu entschluesseln".
    """
    import types
    if 'logHandler' not in sys.modules:
        class _Log:
            def __getattr__(self, name):
                return lambda *a, **k: None
        mod = types.ModuleType('logHandler')
        mod.log = _Log()
        sys.modules['logHandler'] = mod
    try:
        spec = importlib.util.spec_from_file_location(
            'security_probe', os.path.join(BASE, 'security.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        print(f'  (security.py nicht ladbar: {e})')
        return None


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

    print('== Ein fehlender Schluessel bleibt stehen ==')
    tmp = tempfile.mkdtemp()
    try:
        cs = fresh_module(tmp)
        cs.save(Conf(), {'email': 'a@example.com', 'vesyncToken': 'ENC:tok',
                         'cozytouchToken': 'ENC:ct'})
        # So sieht ein Speichern aus, bei dem vesyncToken nicht
        # entschluesselt werden konnte: der Schluessel wird weggelassen.
        cs.save(Conf(), {'email': 'b@example.com', 'cozytouchToken': 'ENC:ct'})
        after = cs.load(Conf())
        check('der weggelassene Wert ist unveraendert da',
              after['vesyncToken'] == 'ENC:tok', after['vesyncToken'])
        check('der uebergebene Wert wurde geschrieben',
              after['email'] == 'b@example.com')
        # Leeren muss weiterhin gehen, sonst liesse sich ein Konto nie loesen.
        cs.save(Conf(), {'email': 'b@example.com', 'vesyncToken': '',
                         'cozytouchToken': 'ENC:ct'})
        check('ein ausdrueckliches "" loescht trotzdem',
              cs.load(Conf())['vesyncToken'] == '')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('== Zwei Threads schreiben gleichzeitig ==')
    tmp = tempfile.mkdtemp()
    try:
        cs = fresh_module(tmp)
        first = {k: 'AAAA' for k in cs.SECRET_KEYS}
        second = {k: 'BBBB' for k in cs.SECRET_KEYS}
        cs._write_file(first)
        failures = []

        def writer(values):
            for _ in range(200):
                if not cs._write_file(values):
                    failures.append(values['email'])

        threads = [threading.Thread(target=writer, args=(first,)),
                   threading.Thread(target=writer, args=(second,))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        check('kein Schreibvorgang geht verloren', not failures,
              f'{len(failures)} von 400 gescheitert')
        stored = json.load(io.open(cs.CREDENTIALS_FILE, encoding='utf-8'))
        check('die Datei ist lesbar und aus einem Guss',
              len(set(stored.values())) == 1, str(sorted(set(stored.values()))))
        leftovers = [n for n in os.listdir(tmp) if n.endswith('.tmp')]
        check('keine temporaere Datei bleibt liegen', not leftovers,
              str(leftovers))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('== Eine gescheiterte Schreibung raeumt hinter sich auf ==')
    tmp = tempfile.mkdtemp()
    try:
        cs = fresh_module(tmp)
        os.makedirs(cs.CREDENTIALS_FILE)   # os.replace scheitert daran
        ok = cs._write_file({'email': 'klartext@example.com'})
        check('sie meldet den Fehlschlag', ok is False)
        leftovers = [n for n in os.listdir(tmp) if n.endswith('.tmp')]
        check('und laesst die E-Mail nicht im Klartext liegen',
              not leftovers, str(leftovers))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('== Was sich nicht entschluesseln laesst, wird erkannt ==')
    security = load_security()
    if security is None:
        check('security.py laesst sich laden', False, 'uebersprungen')
    else:
        plain, ok = security.decrypt_dpapi_checked('')
        check('leer ist kein Fehlschlag', plain == '' and ok is True)
        plain, ok = security.decrypt_dpapi_checked('klartext aus alter Version')
        check('Klartext ohne Praefix geht unveraendert durch',
              plain == 'klartext aus alter Version' and ok is True, plain)
        # Ein Wert, der wie DPAPI aussieht, aber keiner ist: genau das
        # Bild, das ein fremdes Windows-Konto abgibt.
        plain, ok = security.decrypt_dpapi_checked('DPAPI:bm90IHJlYWxseQ==')
        check('ein nicht entschluesselbarer Wert meldet sich',
              plain == '' and ok is False, f'ok={ok}')
        roundtrip = security.encrypt_dpapi('geheim')
        plain, ok = security.decrypt_dpapi_checked(roundtrip)
        check('ein hier verschluesselter Wert kommt heil zurueck',
              plain == 'geheim' and ok is True, plain)

    print('== Und wird nicht ueberschrieben ==')
    src = io.open(os.path.join(BASE, '__init__.py'), encoding='utf-8').read()
    check('load_settings merkt sich die unlesbaren Schluessel',
          '_unreadable_secrets.add(key)' in src)
    check('ein gescheitertes Laden sperrt gleich alle',
          '_unreadable_secrets = set(credential_store.SECRET_KEYS)' in src)
    check('save_settings laesst sie aus dem Speichern heraus',
          "for key in getattr(self, '_unreadable_secrets', ())" in src
          and 'secrets.pop(key, None)' in src)
    check('aber nur solange nichts Echtes drinsteht',
          'if not secrets.get(key):' in src)
    check('ein gescheitertes Speichern wird gemeldet',
          'elif not credential_store.save(conf, secrets):' in src)
    check('und ohne einen einzigen Wert wird gar nicht erst geschrieben',
          'if not secrets:' in src,
          'sonst leert der Aufruf die Konfiguration fuer nichts')

    print('== Die Datei bekommt eingeschraenkte Rechte ==')
    src_cs = io.open(os.path.join(BASE, 'credential_store.py'),
                     encoding='utf-8').read()
    check('die Rechte werden gesetzt', '_restrict_permissions(' in src_cs)
    check('und zwar vor dem Verschieben, nicht danach',
          src_cs.index('_restrict_permissions(temporary)')
          < src_cs.index('os.replace(temporary, CREDENTIALS_FILE)'),
          'os.replace uebernimmt die Rechte der Quelldatei')
    check('dieselbe Funktion wie fuer die Schluesseldatei',
          'from .security import _restrict_file_acl' in src_cs)
    check('ein Fehlschlag bleibt folgenlos (FAT32 kennt keine ACLs)',
          'log.debug(f"Could not restrict' in src_cs)

    print('== Der Abschnitt einer fruehen Version verschwindet ==')
    # [merossSmartControl] hielt das Netatmo-Passwort im Klartext, und NVDA
    # schreibt die Konfiguration bei jedem Start ins Log.
    tmp = tempfile.mkdtemp()
    try:
        cs = fresh_module(tmp)
        profile = {'merossSmartControl': {'netatmo_password': 'klartext'},
                   'smartHomeControl': {'useMeross': 'True'},
                   'general': {'language': 'de'}}
        removed = cs.drop_legacy_sections(profile)
        check('der alte Abschnitt ist weg', 'merossSmartControl' not in profile)
        check('er wird gemeldet', removed == ['merossSmartControl'], str(removed))
        check('der eigene Abschnitt bleibt', profile.get('smartHomeControl')
              == {'useMeross': 'True'})
        check('fremde Abschnitte bleiben', profile.get('general')
              == {'language': 'de'})
        check('ein zweiter Lauf findet nichts mehr',
              cs.drop_legacy_sections(profile) == [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('== Beim Entfernen des Add-ons gehen die Zugangsdaten mit ==')
    # Ohne installTasks ueberlebten sie die Deinstallation. NVDA ruft
    # onUninstall aber auch bei einem UPDATE auf - dann muessen sie bleiben,
    # sonst kostet jedes Update fuenf Anmeldungen und eine neue
    # Netatmo-Freigabe im Browser.
    import importlib.util
    import types as _types
    tmp = tempfile.mkdtemp()
    saved_handler = sys.modules.get('addonHandler')
    try:
        addon = os.path.join(tmp, 'SmartHomeControl')
        os.makedirs(addon)
        shutil.copy(os.path.join(os.path.dirname(os.path.dirname(BASE)),
                                 'installTasks.py'), addon)
        cred = os.path.join(tmp, 'SmartHomeControl_credentials.json')
        hist = os.path.join(tmp, 'SmartHomeControl_history.json')
        for path in (cred, hist):
            with open(path, 'w') as f:
                f.write('{}')
        pending = set()
        sys.modules['addonHandler'] = _types.SimpleNamespace(
            state={'pending': pending},
            AddonStateCategory=_types.SimpleNamespace(PENDING_INSTALL='pending'))
        spec = importlib.util.spec_from_file_location(
            'installTasks_probe', os.path.join(addon, 'installTasks.py'))
        tasks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tasks)
        os.makedirs(os.path.join(tmp, 'SmartHomeControl.pendingInstall'))
        tasks.onUninstall()
        check('bei einem Update bleiben sie (neue Version wartet als Ordner)',
              os.path.exists(cred))
        os.rmdir(os.path.join(tmp, 'SmartHomeControl.pendingInstall'))
        pending.add('SmartHomeControl')
        tasks.onUninstall()
        check('... oder im Zustand von NVDA', os.path.exists(cred))
        pending.clear()
        tasks.onUninstall()
        check('beim Entfernen werden sie geloescht', not os.path.exists(cred))
        check('der Verlauf bleibt - er gehoert dem Nutzer, kein Schluessel',
              os.path.exists(hist))
    finally:
        if saved_handler is None:
            sys.modules.pop('addonHandler', None)
        else:
            sys.modules['addonHandler'] = saved_handler
        shutil.rmtree(tmp, ignore_errors=True)

    print('== Die E-Mail-Adressen werden verschluesselt gespeichert ==')
    src = io.open(os.path.join(BASE, '__init__.py'), encoding='utf-8').read()
    for key, attr in (('email', 'email'), ('vesyncEmail', 'vesync_email'),
                      ('cozytouchEmail', 'cozytouch_email'),
                      ('petlibroEmail', 'petlibro_email')):
        check(f'{key} wird verschluesselt geschrieben',
              f'secrets["{key}"] = encrypt_dpapi(self.{attr})' in src)
        check(f'{key} wird verschluesselt UND alt im Klartext gelesen',
              f'self.{attr} = _maybe_secret("{key}")' in src)

    print('== Der Code benutzt den Speicher wirklich ==')
    src = io.open(os.path.join(BASE, '__init__.py'), encoding='utf-8').read()
    check('load_settings entfernt den alten Abschnitt aus nvda.ini',
          'credential_store.drop_legacy_sections(config.conf.profiles[0])' in src)
    after = src.split('credential_store.drop_legacy_sections(', 1)[-1][:600]
    check('und schreibt die Konfiguration gleich weg',
          'self._flush_config_to_disk()' in after)
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
