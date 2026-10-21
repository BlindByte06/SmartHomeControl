# -*- coding: utf-8 -*-
"""Testet die Anmeldung an den Plattformen.

Drei Fehler, die alle im selben Ablauf steckten:

1. Die Plattformen meldeten sich nacheinander an - gemessen am 01.10.2026
   dreissig Sekunden, Meross allein zwanzig, und so lange blieb das
   Geraetemenue zu.
2. Scheiterte eine Anmeldung am Netz - ein Laptop, der NVDA vor dem WLAN
   startet -, versuchte niemand es wieder. Die Plattform fehlte bis zum
   naechsten Speichern der Einstellungen oder NVDA-Neustart.
3. Jedes Speichern der Einstellungen meldete ALLE Plattformen neu an, und
   die Geraeteliste wurde als Ganzes ersetzt: eine Plattform, deren
   Neuanmeldung an einem 503 scheiterte, verlor ihre Geraete, waehrend ihre
   alte Sitzung weiterlief.

Die Methoden werden per AST aus der echten Quelle geholt.
"""
import ast
import io
import os
import sys
import textwrap
import threading
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


def source(name):
    return io.open(os.path.join(BASE, name + '.py'), encoding='utf-8').read()


def grab(module, names, env):
    src = source(module)
    tree = ast.parse(src)
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            found.setdefault(node.name, textwrap.dedent(
                ast.get_source_segment(src, node)))
    missing = set(names) - set(found)
    if missing:
        raise SystemExit(f'nicht gefunden in {module}: {missing}')
    for name in names:
        exec(compile(ast.parse(found[name]), module, 'exec'), env)
    return env


class Log:
    def __init__(self):
        self.lines = []

    def _add(self, *a, **k):
        self.lines.append(' '.join(str(x) for x in a))

    debug = info = warning = error = _add


class CredentialsRejected(ValueError):
    pass


class Dev:
    def __init__(self, uid, platform):
        self.uuid = uid
        self.unique_id = uid
        self.name = uid
        self.is_hub = False
        self.is_offline = False
        for p in ('netatmo', 'vesync', 'cozytouch', 'petlibro'):
            setattr(self, f'is_{p}', platform == p)


PLATFORMS = ('meross', 'netatmo', 'vesync', 'cozytouch', 'petlibro')
METHODS = ('_platform_has_credentials', '_platforms_to_login', '_platform_api',
           '_do_login', '_login_one', '_take_platform_devices',
           '_schedule_login_retry', 'replace_platform_devices',
           'begin_platform_login', 'end_platform_login', '_platform_enabled',
           '_apply_saved_settings', 'drop_disabled_platform_devices',
           '_close_replaced_session', '_notify_menu_of_login')


def make_plugin_class(spoken, asked, log):
    env = {
        'threading': threading, 'time': time, 'log': log,
        '_': lambda s: s,
        'PLATFORMS': PLATFORMS,
        'PLATFORM_LABELS': {p: p.capitalize() for p in PLATFORMS},
        'PASSWORD_PLATFORMS': ('meross', 'vesync', 'cozytouch', 'petlibro'),
        'ui': type('ui', (), {'message': staticmethod(spoken.append)}),
        'wx': type('wx', (), {'CallAfter': staticmethod(
            lambda fn, *a, **k: fn(*a, **k))}),
        'is_credentials_error': lambda e: isinstance(e, CredentialsRejected),
        'is_transient': lambda e: isinstance(e, (ConnectionError, TimeoutError)),
        'login_error_message': lambda p, e: f'{p} failed: {e}',
        '_safe_log_error': lambda msg, e: log.warning(msg, e),
        '_log_library_origins': lambda: None,
    }
    env['platform_of'] = lambda d: next(
        (p for p in PLATFORMS[1:] if getattr(d, f'is_{p}', False)), 'meross')
    grab('meross_devices', ['carry_over_hub_subdevices'], env)
    grab('__init__', METHODS, env)
    names = dict(env)

    class Plugin:
        _LOGIN_RETRY_DELAYS = (0.05,)
        _CLOSE_REPLACED_SESSIONS = ('vesync', 'cozytouch', 'petlibro')

        def __init__(self):
            self._active_dialog = None
            self.devices = []
            self._devices_lock = threading.RLock()
            self._login_lock = threading.Lock()
            self._logging_in = set()
            self._login_retry_pending = set()
            self._login_retry_stop = threading.Event()
            self.is_logged_in = False
            self.is_loading = False
            self.api = self.netatmo_api = self.vesync_api = None
            self.cozytouch_api = self.petlibro_api = None
            self.email, self._encrypted_password = 'a@b.c', 'ENC'
            self.netatmo_client_id, self.netatmo_refresh_token = 'id', 'rt'
            self.netatmo_access_token, self.netatmo_token_expiry = 'at', 0
            self.vesync_email = self._encrypted_vesync_password = ''
            self.vesync_token = self.vesync_account_id = ''
            self.cozytouch_email = self._encrypted_cozytouch_password = ''
            self.petlibro_email = self._encrypted_petlibro_password = ''
            self.use_meross = self.use_netatmo = True
            self.use_vesync = self.use_cozytouch = self.use_petlibro = False
            self.scheduler_started = 0
            self._previous_vesync_states = {}
            self._previous_cozytouch_states = {}

        def _start_background_refresh(self):
            self.scheduler_started += 1

        def _snapshot_vesync_state(self, device):
            return {}

        def _snapshot_cozytouch_state(self, device):
            return {}

        def _seed_petlibro_state(self, device):
            pass

        def _ask_credential_reentry(self, platform, error):
            asked.append(platform)

        def _start_auto_login(self, interactive=False, platforms=None):
            self.started_with = (interactive, platforms)

    for name in METHODS:
        setattr(Plugin, name, names[name])
    return Plugin


def main():
    spoken, asked, log = [], [], Log()
    Plugin = make_plugin_class(spoken, asked, log)

    print('== Die Plattformen melden sich gleichzeitig an ==')
    plugin = Plugin()

    def slow(platform, count):
        def step(self):
            time.sleep(0.4)
            setattr(self, 'api' if platform == 'meross' else f'{platform}_api',
                    object())
            return [Dev(f'{platform}-{i}', platform) for i in range(count)]
        return step

    Plugin._LOGIN_STEPS = {'meross': slow('meross', 2),
                           'netatmo': slow('netatmo', 1)}
    began = time.time()
    plugin._do_login()
    took = time.time() - began
    check('zwei Anmeldungen von je 0,4 s dauern zusammen keine 0,8 s',
          took < 0.7, f'{took:.2f} s')
    check('alle Geraete sind da', len(plugin.devices) == 3,
          str(len(plugin.devices)))
    check('angemeldet, der Zeitplaner laeuft',
          plugin.is_logged_in and plugin.scheduler_started == 1)
    check('die Ansage nennt beide',
          any('3 devices ready' in m and 'Meross' in m and 'Netatmo' in m
              for m in spoken), str(spoken[-1:]))
    check('das Menue ist danach frei', plugin.is_loading is False)

    print('== Eine scheiternde Neuanmeldung behaelt ihre Geraete ==')
    spoken.clear()

    def network_down(self):
        raise ConnectionError('network is unreachable')

    Plugin._LOGIN_STEPS = {'meross': slow('meross', 2),
                           'netatmo': network_down}
    Plugin._LOGIN_RETRY_DELAYS = (30,)
    plugin._do_login()
    netatmo = [d for d in plugin.devices if d.is_netatmo]
    check('die Netatmo-Geraete bleiben in der Liste', len(netatmo) == 1,
          str([d.uuid for d in plugin.devices]))
    check('der Fehler wird gesagt',
          any('netatmo failed' in m for m in spoken), str(spoken))
    check('und dass es gleich noch einmal versucht wird',
          any('New attempt in 30 seconds' in m for m in spoken), str(spoken))
    check('ein zweiter Fehlschlag plant keinen zweiten Versuch',
          plugin._schedule_login_retry(['netatmo'], 0) == 0)
    plugin._login_retry_stop.set()  # der wartende Versuch endet hier

    print('== Ein Netzausfall beim Start wird spaeter nachgeholt ==')
    spoken.clear()
    plugin = Plugin()
    attempts = []

    def flaky(self):
        attempts.append(time.time())
        if len(attempts) == 1:
            raise TimeoutError('timed out')
        self.netatmo_api = object()
        return [Dev('netatmo-x', 'netatmo')]

    Plugin._LOGIN_STEPS = {'meross': network_down, 'netatmo': flaky}
    Plugin._LOGIN_RETRY_DELAYS = (0.05,)
    plugin._do_login()
    check('zunaechst nicht angemeldet', not plugin.is_logged_in)
    deadline = time.time() + 3
    while time.time() < deadline and not plugin.is_logged_in:
        time.sleep(0.02)
    check('der zweite Versuch kommt von selbst und meldet an',
          plugin.is_logged_in and len(attempts) == 2,
          f'{len(attempts)} Versuche')
    check('und sagt, was nun bereit ist',
          any('1 devices ready' in m for m in spoken), str(spoken))
    check('der neue Versuch wiederholt die Fehleransage nicht',
          sum('meross failed' in m for m in spoken) == 1, str(spoken))
    plugin._login_retry_stop.set()
    time.sleep(0.1)

    print('== Ein Fehler ohne Netzbezug wird nicht endlos wiederholt ==')
    # Ein Meross-Konto mit Zwei-Faktor-Anmeldung (MissingMFA) wurde alle fuenf
    # Minuten neu versucht, fuer immer, mit einem ERROR und dem Fehlerton.
    spoken.clear()
    plugin = Plugin()
    mfa_attempts = []

    class MfaRequired(Exception):
        pass

    def needs_mfa(self):
        mfa_attempts.append(time.time())
        raise MfaRequired()

    Plugin._LOGIN_STEPS = {'meross': needs_mfa, 'netatmo': slow('netatmo', 1)}
    Plugin._LOGIN_RETRY_DELAYS = (0.05,)
    plugin._do_login()
    time.sleep(0.5)
    check('so oft versucht, wie es Wartezeiten gibt, dann nicht mehr',
          len(mfa_attempts) == 2, f'{len(mfa_attempts)} Versuche')
    check('und im Log steht, dass es aufhoert',
          any('keeps failing' in line for line in log.lines),
          str(log.lines[-2:]))
    check('nichts wartet mehr', not plugin._login_retry_pending)
    plugin._login_retry_stop.set()

    print('== Die Meross-Fehler werden richtig eingeordnet ==')
    # meross_iot: MissingMFA und WrongMFA erben von BadLoginException, ihr
    # eigener Name enthaelt aber kein "badlogin"; HttpApiError hat keinen Text.
    sp = grab('settings_panel', ['_class_names', 'is_credentials_error',
                                 'login_error_message'],
              {'_': lambda s: s,
               'PLATFORM_LABELS': {p: p.capitalize() for p in PLATFORMS}})

    class BadLoginException(Exception):
        pass

    class MissingMFA(BadLoginException):
        pass

    class TooManyTokensException(Exception):
        pass

    class HttpApiError(Exception):
        def __init__(self, error_code):
            self._error_code = error_code

        @property
        def error_code(self):
            return self._error_code

    check('ein falsches Passwort ist ein Zugangsdatenfehler',
          sp['is_credentials_error'](BadLoginException('x')))
    check('MissingMFA ebenso - ueber die Klassenhierarchie',
          sp['is_credentials_error'](MissingMFA()))
    mfa_text = sp['login_error_message']('meross', MissingMFA())
    check('und die Meldung nennt die Zwei-Faktor-Anmeldung',
          'two-factor' in mfa_text, mfa_text)
    check('zu viele Sitzungen haben eine eigene Meldung',
          'too many open sessions' in sp['login_error_message'](
              'meross', TooManyTokensException('x')))
    api_text = sp['login_error_message']('meross', HttpApiError(1255))
    check('ein Fehlercode wird genannt', '1255' in api_text, api_text)

    class NoText(Exception):
        pass

    empty_text = sp['login_error_message']('meross', NoText())
    check('ein Fehler ohne Text endet nicht nach dem Doppelpunkt',
          not empty_text.rstrip().endswith(':') and 'NoText' in empty_text,
          empty_text)

    print('== Netatmo ohne Netz ist gescheitert, nicht leer ==')
    # 03.10.2026, 18:09: NVDA ohne Netz gestartet. Der Token galt noch, also
    # brauchte die Anmeldung selbst kein Netz; die Geraeteabfrage kam leer
    # zurueck, ohne zu werfen - "Netatmo: 0 device(s) found". Die anderen
    # vier Plattformen wurden nach 30 s neu versucht, Netatmo nicht.
    class NetatmoAPI:
        answers = False

        def __init__(self, *a, **k):
            self.last_poll_ok = True

        def set_tokens(self, *a):
            pass

        def set_token_update_callback(self, cb):
            pass

        def _ensure_valid_token(self):
            pass

        def get_tokens(self):
            return {'access_token': 'a'}

        def get_devices(self):
            self.last_poll_ok = NetatmoAPI.answers
            return []

    login_env = grab('__init__', ['_login_netatmo'],
                     {'NetatmoAPI': NetatmoAPI, '_': lambda s: s})

    class Owner:
        netatmo_client_id = netatmo_client_secret = 'x'
        netatmo_redirect_port = 0
        netatmo_access_token = 'a'
        netatmo_refresh_token = 'r'
        netatmo_token_expiry = 0
        netatmo_api = None

        def _on_netatmo_tokens_renewed(self, tokens):
            pass

    owner = Owner()
    try:
        login_env['_login_netatmo'](owner)
        raised = None
    except ConnectionError as e:
        raised = e
    check('keine Antwort der Cloud ist ein Netzfehler - und wird wiederholt',
          raised is not None, repr(raised))
    check('... und haengt keine halbe Sitzung ein', owner.netatmo_api is None)
    NetatmoAPI.answers = True
    devices = login_env['_login_netatmo'](owner)
    check('ein Konto ohne Netatmo-Geraete, das antwortet, bleibt angemeldet',
          devices == [] and owner.netatmo_api is not None)

    print('== Ein abgelehntes Passwort wird nicht wiederholt ==')
    spoken.clear()
    asked.clear()
    plugin = Plugin()

    def refused(self):
        raise CredentialsRejected('wrong password')

    Plugin._LOGIN_STEPS = {'meross': refused, 'netatmo': slow('netatmo', 1)}
    plugin._do_login(interactive=True)
    check('kein neuer Versuch geplant', not plugin._login_retry_pending)
    check('nach dem Speichern wird nach dem Passwort gefragt',
          asked == ['meross'], str(asked))
    plugin2 = Plugin()
    asked.clear()
    plugin2._do_login(interactive=False)
    check('beim NVDA-Start nicht - da poppte sonst ein Dialog auf',
          asked == [], str(asked))

    print('== Speichern meldet nur an, was es braucht ==')
    plugin = Plugin()
    plugin.is_logged_in = True
    plugin.api = object()
    plugin.netatmo_api = object()
    plugin.use_vesync = True
    plugin._apply_saved_settings({'meross'})
    check('Meross (neue Zugangsdaten) und VeSync (noch keine Sitzung)',
          plugin.started_with == (True, ['meross', 'vesync']),
          str(plugin.started_with))
    plugin.is_logged_in = False
    plugin._apply_saved_settings(set())
    check('ohne Sitzung meldet es alle an', plugin.started_with == (True, None),
          str(plugin.started_with))

    print('== Das offene Geraetemenue baut nach einer Anmeldung neu auf ==')
    # Der Baum haelt die Geraeteobjekte, aus denen er gebaut wurde. Nach einer
    # Anmeldung sind die der Plattform neue - die alten werden nicht mehr
    # abgefragt, und ihre Sitzung ist zu.
    class Menu:
        def __init__(self):
            self._is_destroyed = False
            self.rebuilt = 0
            self.offered = []

        def _rebuild_after_login(self):
            self.rebuilt += 1
            spoken.append('<neu aufgebaut>')

        def _offer_login_reentry(self, platform, error):
            self.offered.append(platform)

    spoken.clear()
    plugin = Plugin()
    menu = Menu()
    plugin._active_dialog = menu
    Plugin._LOGIN_STEPS = {'meross': slow('meross', 1),
                           'netatmo': slow('netatmo', 1)}
    plugin._do_login()
    check('das Menue baut einmal neu auf', menu.rebuilt == 1, str(menu.rebuilt))
    ready = [i for i, m in enumerate(spoken) if 'devices ready' in m]
    check('vor der Ansage, wie die eigenen Anmeldungen des Menues',
          ready and spoken.index('<neu aufgebaut>') < ready[0], str(spoken))

    menu = Menu()
    plugin = Plugin()
    plugin._active_dialog = menu
    Plugin._LOGIN_STEPS = {'meross': network_down, 'netatmo': network_down}
    Plugin._LOGIN_RETRY_DELAYS = (30,)
    plugin._do_login()
    check('ohne erfolgreiche Anmeldung kein Neuaufbau', menu.rebuilt == 0)
    plugin._login_retry_stop.set()

    menu = Menu()
    menu._is_destroyed = True
    plugin = Plugin()
    plugin._active_dialog = menu
    Plugin._LOGIN_STEPS = {'meross': slow('meross', 1),
                           'netatmo': slow('netatmo', 1)}
    plugin._do_login()
    check('ein geschlossenes Menue wird nicht angefasst', menu.rebuilt == 0)

    print('== Die ersetzte Sitzung wird geschlossen ==')
    # Die Anmeldungen des Menues schlossen die alte VeSync-, Cozytouch- und
    # Petlibro-Sitzung, die des Plugins nicht.
    class Session:
        def __init__(self, name):
            self.name = name
            self.closed = 0

        def logout(self):
            self.closed += 1

    def fresh(platform, count=1):
        def step(self):
            setattr(self, 'api' if platform == 'meross' else f'{platform}_api',
                    Session(f'{platform}-neu'))
            return [Dev(f'{platform}-{i}', platform) for i in range(count)]
        return step

    def empty(platform):
        def step(self):
            setattr(self, f'{platform}_api', Session(f'{platform}-neu'))
            return []
        return step

    plugin = Plugin()
    plugin.use_vesync = plugin.use_cozytouch = plugin.use_petlibro = True
    plugin.vesync_token, plugin.vesync_account_id = 't', 'a'
    plugin.cozytouch_email, plugin._encrypted_cozytouch_password = 'c', 'ENC'
    plugin.petlibro_email, plugin._encrypted_petlibro_password = 'p', 'ENC'
    old = {p: Session(f'{p}-alt') for p in PLATFORMS}
    plugin.api = old['meross']
    for p in PLATFORMS[1:]:
        setattr(plugin, f'{p}_api', old[p])
    plugin.devices = [Dev('petlibro-x', 'petlibro')]
    Plugin._LOGIN_STEPS = {'meross': fresh('meross'), 'netatmo': fresh('netatmo'),
                           'vesync': fresh('vesync'),
                           'cozytouch': lambda self: network_down(self),
                           'petlibro': empty('petlibro')}
    plugin._do_login()
    plugin._login_retry_stop.set()
    check('die alte VeSync-Sitzung ist zu', old['vesync'].closed == 1)
    check('die neue bleibt offen', plugin.vesync_api.closed == 0)
    check('Meross schliesst die eigene selbst - hier nicht ein zweites Mal',
          old['meross'].closed == 0)
    check('Netatmo bleibt, wie es immer war', old['netatmo'].closed == 0)
    check('eine gescheiterte Anmeldung laesst die laufende Sitzung offen',
          old['cozytouch'].closed == 0 and plugin.cozytouch_api is old['cozytouch'])
    check('eine leere Antwort behaelt die Geraete - und deren Sitzung',
          old['petlibro'].closed == 0
          and [d.uuid for d in plugin.devices if d.is_petlibro] == ['petlibro-x'])

    print('== Die abgelehnte Anmeldung wird einmal gesagt ==')
    # Das Angebot, die Zugangsdaten neu einzugeben, sagt den Grund selbst
    # (offer_credential_reentry). _do_login sagte ihn vorher auch.
    def refused_too(self):
        raise CredentialsRejected('wrong password')

    spoken.clear()
    asked.clear()
    plugin = Plugin()
    plugin.use_vesync = True
    plugin.vesync_token, plugin.vesync_account_id = 't', 'a'
    Plugin._LOGIN_STEPS = {'meross': refused, 'netatmo': slow('netatmo', 1),
                           'vesync': refused_too}
    plugin._do_login(interactive=True)
    check('nach dem Speichern sagt es das Angebot, nicht die Anmeldung',
          not any('meross failed' in m for m in spoken) and asked == ['meross'],
          f'{spoken} {asked}')
    check('eine zweite Ablehnung, die nicht angeboten wird, wird gesagt',
          sum('vesync failed' in m for m in spoken) == 1, str(spoken))
    spoken.clear()
    asked.clear()
    plugin = Plugin()
    Plugin._LOGIN_STEPS = {'meross': refused, 'netatmo': slow('netatmo', 1)}
    plugin._do_login(interactive=False)
    check('beim NVDA-Start wird sie gesagt - einmal, ohne Angebot',
          sum('meross failed' in m for m in spoken) == 1 and asked == [],
          f'{spoken} {asked}')

    print('== Bei offenem Menue fragt das Menue nach den Zugangsdaten ==')
    offers = []
    reentry = grab('__init__', ['_ask_credential_reentry'], {
        'ui': type('ui', (), {'message': staticmethod(spoken.append)}),
        'log': log, 'login_error_message': lambda p, e: f'{p} failed: {e}',
        'gui': type('gui', (), {'mainFrame': type('frame', (), {
            'prePopup': staticmethod(lambda: None),
            'postPopup': staticmethod(lambda: None)})}),
        'offer_credential_reentry': lambda parent, plugin, platform, error,
        on_saved=None: offers.append((parent, platform)),
    })['_ask_credential_reentry']

    class Owner:
        _settings_open = False

    owner = Owner()
    owner._active_dialog = Menu()
    reentry(owner, 'meross', CredentialsRejected('x'))
    check('das Menue macht das Angebot', owner._active_dialog.offered == ['meross']
          and offers == [], str(offers))
    owner._active_dialog._is_destroyed = True
    reentry(owner, 'meross', CredentialsRejected('x'))
    check('ein geschlossenes Menue nicht - dann NVDA selbst',
          len(offers) == 1 and owner._active_dialog.offered == ['meross'])
    owner._active_dialog = None
    reentry(owner, 'vesync', CredentialsRejected('x'))
    check('ohne Menue ebenso', len(offers) == 2 and offers[-1][1] == 'vesync')

    print('== Speichern: abgeschaltete Plattformen und der Netatmo-Hinweis ==')
    spoken.clear()
    plugin = Plugin()
    plugin.is_logged_in = True
    plugin.api = object()
    plugin.netatmo_api = object()
    plugin.devices = [Dev('meross-1', 'meross'), Dev('netatmo-1', 'netatmo')]
    plugin.use_netatmo = False
    dropped = plugin._apply_saved_settings(set())
    check('sagt, wie viele Geraete wegfielen - das Menue baut dafuer neu auf',
          dropped == 1 and [d.uuid for d in plugin.devices] == ['meross-1'],
          repr(dropped))
    plugin.use_netatmo = True
    plugin.netatmo_refresh_token = ''
    plugin._apply_saved_settings({'netatmo'})
    check('neue Netatmo-Daten ohne Freigabe: der Hinweis auf "Connect"',
          any('authorise again' in m for m in spoken), str(spoken))

    print('== Das Menue speichert ueber denselben Weg ==')

    class Later:
        calls = []

        def __init__(self, ms, fn, *args):
            Later.calls.append((ms, fn, args))

    menu_env = grab('device_dialog', ['_apply_settings_change',
                                      '_rebuild_after_login'], {
        'log': log, 'wx': type('wx', (), {'CallLater': Later})})

    class Saved:
        """The plugin as far as the menu's save sees it."""

        def __init__(self, dropped=0, raises=False):
            self.dropped, self.raises = dropped, raises
            self.saved_with = None
            self.is_logged_in = True
            self.devices = ['d']

        def _apply_saved_settings(self, changed):
            self.saved_with = changed
            if self.raises:
                raise RuntimeError('kaputt')
            return self.dropped

    class Tree:
        _apply_settings_change = menu_env['_apply_settings_change']
        _rebuild_after_login = menu_env['_rebuild_after_login']

        def __init__(self, plugin):
            self.plugin = plugin
            self._is_destroyed = False
            self._suppress_live_updates = False
            self.pending = False
            self.loaded = 0
            self.favorites = 0
            self.refreshed = 0

        def _action_callback_pending(self):
            return self.pending

        def _load_devices_internal(self, devices):
            self.loaded += 1

        def _refresh_favorites_tree(self):
            self.favorites += 1

        def _on_refresh(self, event):
            self.refreshed += 1

    tree = Tree(Saved())
    tree._apply_settings_change({'vesync'})
    check('die geaenderten Plattformen gehen an das Plugin',
          tree.plugin.saved_with == {'vesync'})
    check('ohne weggefallene Geraete kein Neuaufbau, aber die Aktualisierung',
          tree.loaded == 0 and tree.refreshed == 1)
    tree = Tree(Saved(dropped=2))
    tree._apply_settings_change(set())
    check('fallen Geraete weg, werden beide Baeume neu gebaut',
          tree.loaded == 1 and tree.favorites == 1)
    tree = Tree(Saved(raises=True))
    try:
        tree._apply_settings_change({'meross'})
        escaped = None
    except Exception as e:
        escaped = e
    check('ein Fehler beim Anwenden bricht das Menue nicht ab',
          escaped is None and tree.refreshed == 1, repr(escaped))

    print('== Der Neuaufbau wartet, bis nichts mehr am Baum haengt ==')
    Later.calls.clear()
    tree = Tree(Saved())
    tree.pending = True
    tree._rebuild_after_login()
    tree._rebuild_after_login()
    check('waehrend eine Aktion wartet: nicht jetzt, einmal vorgemerkt',
          tree.loaded == 0 and len(Later.calls) == 1, str(len(Later.calls)))
    ms, fn, args = Later.calls[-1]
    fn(*args)
    check('noch immer belegt: wieder vorgemerkt', tree.loaded == 0
          and len(Later.calls) == 2)
    tree.pending = False
    ms, fn, args = Later.calls[-1]
    fn(*args)
    check('dann genau ein Neuaufbau', tree.loaded == 1 and tree.favorites == 1
          and len(Later.calls) == 2)
    tree._rebuild_after_login()
    check('und danach wieder sofort', tree.loaded == 2)
    tree._suppress_live_updates = True
    tree._rebuild_after_login()
    check('ueber einem Unterdialog ebenso nicht', tree.loaded == 2
          and len(Later.calls) == 3)
    tree._is_destroyed = True
    ms, fn, args = Later.calls[-1]
    fn(*args)
    check('ein inzwischen geschlossenes Menue wird nicht angefasst',
          tree.loaded == 2 and len(Later.calls) == 3)

    print('== Eine Anmeldung, nicht sechs ==')
    for name in ('device_dialog', 'dialog_meross', 'dialog_netatmo',
                 'dialog_vesync', 'dialog_cozytouch', 'dialog_petlibro',
                 'dialog_favorites'):
        src = source(name)
        built = [api for api in ('MerossAPI(', 'NetatmoAPI(', 'VeSyncAPI(',
                                 'CozytouchAPI(', 'PetlibroAPI(')
                 if api in src]
        check(f'{name}: baut keine eigene Sitzung', not built, str(built))
        check(f'{name}: meldet sich nicht selbst an',
              'begin_platform_login(' not in src and '.login(' not in src)
    dialog = source('device_dialog')
    check('der Einstellungsknopf des Menues speichert ueber das Plugin',
          'self._apply_settings_change(changed)' in dialog
          and 'self.plugin._apply_saved_settings(changed)' in dialog)
    check('die erneute Eingabe ebenso',
          'on_saved=self._apply_settings_change' in dialog)
    init = source('__init__')
    check('das Plugin schliesst die ersetzten Sitzungen dieser drei',
          "_CLOSE_REPLACED_SESSIONS = ('vesync', 'cozytouch', 'petlibro')" in init)
    login = init[init.index('    def _do_login'):]
    login = login[:login.index('\n    def ', 10)]
    check('erst die Geraete, dann die alte Sitzung',
          login.index('self._take_platform_devices(p, results[p])')
          < login.index('self._close_replaced_session(p, before.get(p))'))

    print('== Die Statusansage: kurz, und zweimal gedrueckt eine Liste ==')
    # Sie sprach jedes Geraet nacheinander als EINE Ansage - bei fuenfzig
    # Geraeten zwei Minuten, weder navigierbar noch auf der Braillezeile
    # lesbar.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'shc_formatting_probe', os.path.join(BASE, 'formatting.py'))
    fmt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fmt)
    shown = []
    env = {'_': lambda s: s, 'log': Log(),
           'fmt_decimal': fmt.fmt_decimal, 'fmt_number': fmt.fmt_number,
           'PLATFORM_LABELS': {p: p.capitalize() for p in PLATFORMS},
           'ui': type('ui', (), {
               'message': staticmethod(spoken.append),
               'browseableMessage': staticmethod(
                   lambda text, title=None: shown.append((title, text)))})}
    env['split_by_platform'] = lambda devs: {
        p: [d for d in devs if (next((q for q in PLATFORMS[1:]
                                     if getattr(d, f'is_{q}', False)),
                                     'meross') == p)] for p in PLATFORMS}
    names = ('_announce_devices_status', '_status_summary',
             '_counts_as_switched_on', '_device_status_lines',
             '_meross_status_parts')
    grab('__init__', names, env)

    class Status:
        pass

    for name in names:
        func = env[name]
        if name in ('_counts_as_switched_on', '_meross_status_parts'):
            func = staticmethod(func)
        setattr(Status, name, func)

    class Plug(Dev):
        def __init__(self, uid, on, offline=False):
            super().__init__(uid, 'meross')
            self._on = on
            self.is_offline = offline
            self.is_sensor = self.is_temperature_sensor = False
            self.is_water_sensor = False

        @property
        def is_on(self):
            return self._on

        def get_power(self):
            return 12.5

    class Leak(Plug):
        def __init__(self, uid, wet):
            super().__init__(uid, False)
            self.is_sensor = self.is_water_sensor = True
            self._wet = wet

        def is_water_detected(self):
            return self._wet

    class Thermostat(Dev):
        def __init__(self, uid):
            super().__init__(uid, 'netatmo')

        @property
        def is_on(self):
            return True

        def get_status_summary(self):
            return 'Target: 21.0°C'

    devices = [Plug('Kaffee', True), Plug('Lampe', False),
               Plug('Radio', True, offline=True), Leak('Keller', True),
               Thermostat('Bad')]
    spoken.clear()
    Status()._announce_devices_status(devices)
    summary = spoken[-1] if spoken else ''
    check('die Zusammenfassung zaehlt und nennt die Plattformen',
          summary.startswith('5 devices (4 Meross, 1 Netatmo)'), summary)
    check('eingeschaltet zaehlen nur schaltbare Geraete, die erreichbar sind',
          '1 switched on' in summary, summary)
    check('was offline ist, steht mit Namen da',
          '1 offline' in summary and 'Offline: Radio.' in summary, summary)
    check('ein Wasseralarm wird nicht verschwiegen',
          'Water alarm: Keller' in summary, summary)
    check('die Geraete selbst werden nicht vorgelesen',
          'Lampe' not in summary, summary)
    Status()._announce_devices_status(devices, as_list=True)
    check('zweimal gedrueckt: eine Liste in einem Lesefenster',
          shown and shown[-1][0] == 'Smart home status')
    lines = shown[-1][1].split('\n') if shown else []
    check('eine Zeile pro Geraet, alphabetisch',
          [line.split(':')[0] for line in lines]
          == ['Bad', 'Kaffee', 'Keller', 'Lampe', 'Radio'], str(lines))
    check('mit Zustand und Leistung',
          'Kaffee: on, 12.5 watts' in lines, str(lines))
    init_src = source('__init__')
    check('der Befehl erkennt den zweiten Druck',
          'scriptHandler.getLastScriptRepeatCount() >= 1' in init_src)

    print('== Verdrahtung ==')
    init = source('__init__')
    show = init[init.index('    def _show_settings'):]
    show = show[:show.index('\n    def ', 10)]
    check('der Einstellungsbefehl geht ueber _apply_saved_settings',
          'self._apply_saved_settings(changed)' in show)
    check('und liest die geaenderten Plattformen vor dem Zerstoeren',
          show.index("changed_platforms") < show.index('dlg.Destroy()'))
    check('jede Plattform hat ihren Anmeldeschritt',
          all(f"'{p}': _login_{p}," in init for p in PLATFORMS))
    check('NVDA-Ende beendet einen wartenden Versuch',
          'self._login_retry_stop.set()' in init)
    check('save_settings ist gegen gleichzeitige Aufrufe gesperrt',
          'with self._save_lock:' in init)

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
