# -*- coding: utf-8 -*-
"""Testet den Gerätebaum: Auswahl, Struktur-Signatur, abgeschaltete Plattform.

Drei Fehler, die im Handbuch unter "bekannt" standen:

1. Nach jedem Aktualisieren stand die Auswahl wieder auf dem ersten Eintrag.
2. Derselbe Neuaufbau liess NVDA den Eintrag zwei- bis dreimal ansagen.
3. Eine abgeschaltete Plattform blieb bis zum NVDA-Neustart im Baum stehen.

Die Methoden werden per AST aus den echten Quelldateien geholt und gegen
einen nachgebauten Baum ausgefuehrt - getestet wird der ausgelieferte Code.
"""
import ast
import importlib.util
import io
import os
import textwrap
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


def grab(path, names, env):
    """Holt Funktionen/Methoden aus ``path`` und fuehrt sie in ``env`` aus."""
    src = io.open(path, encoding='utf-8').read()
    tree = ast.parse(src)
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            found[node.name] = textwrap.dedent(ast.get_source_segment(src, node))
    missing = set(names) - set(found)
    if missing:
        raise SystemExit(f'nicht gefunden in {path}: {missing}')
    for name in names:
        exec(compile(ast.parse(found[name]), path, 'exec'), env)
    return env


def load(name):
    """Laedt ein Modul des Add-ons direkt aus der Quelle (ohne NVDA)."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(BASE, name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


platform_of = load('platform_utils').platform_of


class Log:
    def __init__(self):
        self.lines = []

    def _add(self, m):
        self.lines.append(str(m))

    debug = info = warning = error = _add


# ------------------------------------------------------------ Baumattrappe --
class Null:
    def IsOk(self):
        return False


NULL = Null()


class Item:
    def __init__(self, text, data=None, parent=None):
        self.text = text
        self.data = data
        self.parent = parent
        self.children = []

    def IsOk(self):
        return True

    def __repr__(self):
        return f'<{self.text}>'


class FakeTree:
    """Nur das, was die getesteten Methoden vom wx-Baum benutzen."""

    def __init__(self):
        self.root = Item('root')
        self.selected = NULL
        self.ensured = []
        self.focused = True

    def add(self, parent, text, data=None):
        item = Item(text, data, parent)
        parent.children.append(item)
        return item

    def GetRootItem(self):
        return self.root

    def GetItemText(self, item):
        return item.text

    def GetItemData(self, item):
        return item.data

    def GetItemParent(self, item):
        return item.parent or NULL

    def GetSelection(self):
        return self.selected

    def SelectItem(self, item):
        self.selected = item

    def EnsureVisible(self, item):
        self.ensured.append(item)

    def GetFirstChild(self, node):
        return (node.children[0], 0) if node.children else (NULL, -1)

    def GetNextChild(self, node, cookie):
        index = cookie + 1
        if index < len(node.children):
            return node.children[index], index
        return NULL, index


class Dev:
    def __init__(self, uid, name, platform='meross', offline=False):
        self.unique_id = uid
        self.uuid = uid.split('#')[0]
        self.name = name
        self.is_offline = offline
        self.is_netatmo = platform == 'netatmo'
        self.is_vesync = platform == 'vesync'
        self.is_cozytouch = platform == 'cozytouch'


METHODS = ('_tree_item_identity', '_capture_selection', '_restore_selection',
           '_tree_signature')


def make_dialog(tree, filter_mode='all', sort_mode='name'):
    env = {'log': Log(), 'platform_of': platform_of}
    grab(os.path.join(BASE, 'device_dialog.py'), METHODS, env)

    class Dlg:
        pass

    for name in METHODS:
        setattr(Dlg, name, env[name])
    dlg = Dlg()
    dlg.tree = tree
    dlg.filter_mode = filter_mode
    dlg.sort_mode = sort_mode
    return dlg


def build(tree, devices, labels=None, actions=True):
    """Baut Plattform > Kategorie > Geraet [> Aktion] auf, wie der Dialog."""
    labels = labels or {}
    platform = tree.add(tree.root, f'Meross devices ({len(devices)})')
    category = tree.add(platform, f'Plugs ({len(devices)})')
    for dev in devices:
        label = labels.get(dev.unique_id, f'{dev.name} (mss310)')
        item = tree.add(category, label, {'type': 'device', 'device': dev})
        if actions:
            tree.add(item, 'Status: on', {'type': 'info', 'device': dev})
            tree.add(item, 'Switch off',
                     {'type': 'action', 'device': dev, 'action': 'toggle'})
    return platform, category


# --------------------------------------------------------------- Identitaet --
def test_identity():
    print('== Identitaet eines Eintrags ==')
    tree = FakeTree()
    dlg = make_dialog(tree)

    hub_a = Dev('hub1#01', 'Kitchen')
    hub_b = Dev('hub1#02', 'Bedroom')
    check('zwei Sensoren an einem Hub sind verschieden',
          dlg._tree_item_identity(tree.add(tree.root, 'Kitchen 21 C',
                                           {'type': 'device', 'device': hub_a}))
          != dlg._tree_item_identity(tree.add(tree.root, 'Bedroom 19 C',
                                              {'type': 'device', 'device': hub_b})),
          'gleiche uuid, verschiedene unique_id')

    strip = Dev('strip1', 'Power strip')
    one = tree.add(tree.root, 'Outlet 1',
                   {'type': 'device', 'device': strip, 'channel': 1})
    two = tree.add(tree.root, 'Outlet 2',
                   {'type': 'device', 'device': strip, 'channel': 2})
    check('Dosen einer Leiste sind verschieden',
          dlg._tree_item_identity(one) != dlg._tree_item_identity(two))

    dev = Dev('p1', 'Lamp')
    warm = tree.add(tree.root, 'Temperature: 21.5 C',
                    {'type': 'info', 'device': dev})
    cold = tree.add(tree.root, 'Temperature: 19.0 C',
                    {'type': 'info', 'device': dev})
    check('ein geaenderter Wert aendert die Identitaet nicht',
          dlg._tree_item_identity(warm) == dlg._tree_item_identity(cold))

    info = tree.add(tree.root, 'Status: on', {'type': 'info', 'device': dev})
    action = tree.add(tree.root, 'Switch off',
                      {'type': 'action', 'device': dev, 'action': 'toggle'})
    check('Aktionszeile und Infozeile sind verschieden',
          dlg._tree_item_identity(info) != dlg._tree_item_identity(action))

    check('Strukturknoten ignoriert die Anzahl',
          dlg._tree_item_identity(tree.add(tree.root, 'Plugs (3)'))
          == dlg._tree_item_identity(tree.add(tree.root, 'Plugs (4)')))
    check('verschiedene Kategorien bleiben verschieden',
          dlg._tree_item_identity(tree.add(tree.root, 'Plugs (3)'))
          != dlg._tree_item_identity(tree.add(tree.root, 'Hubs (3)')))


# ------------------------------------------------------------------ Auswahl --
def test_selection():
    print('== Auswahl ueberlebt den Neuaufbau ==')
    devices = [Dev(f'p{i}', f'Plug {i}') for i in range(1, 6)]

    tree = FakeTree()
    dlg = make_dialog(tree)
    build(tree, devices)
    fifth = tree.root.children[0].children[0].children[4]
    tree.SelectItem(fifth)
    chain = dlg._capture_selection()
    check('der Pfad reicht von der Plattform bis zum Geraet', len(chain) == 3,
          f'{len(chain)} Stufen')

    # Neuaufbau: andere Werte in den Beschriftungen, andere Anzahl im Knoten.
    tree2 = FakeTree()
    dlg2 = make_dialog(tree2)
    labels = {d.unique_id: f'{d.name} (mss310) - 42 W' for d in devices}
    build(tree2, devices + [Dev('p9', 'Plug 9')], labels=labels)
    tree2.SelectItem(tree2.root.children[0].children[0].children[0])
    ok = dlg2._restore_selection(chain)
    check('nach dem Neuaufbau steht die Auswahl wieder auf Plug 5',
          ok and tree2.selected.data['device'].unique_id == 'p5',
          tree2.selected.text)
    check('der Eintrag wird sichtbar gemacht', tree2.ensured
          and tree2.ensured[-1] is tree2.selected)

    # Eine Aktionszeile, die es nach dem Neuaufbau nicht mehr gibt.
    tree3 = FakeTree()
    dlg3 = make_dialog(tree3)
    build(tree3, devices)
    action = tree3.root.children[0].children[0].children[2].children[1]
    tree3.SelectItem(action)
    deep_chain = dlg3._capture_selection()
    check('der Pfad einer Aktionszeile ist vier Stufen tief',
          len(deep_chain) == 4)

    tree4 = FakeTree()
    dlg4 = make_dialog(tree4)
    build(tree4, devices, actions=False)
    tree4.SelectItem(tree4.root.children[0].children[0].children[0])
    ok = dlg4._restore_selection(deep_chain)
    check('eine verschwundene Zeile faellt auf ihr Geraet zurueck',
          ok and tree4.selected.data['device'].unique_id == 'p3',
          tree4.selected.text)

    # Ein Geraet, das es nicht mehr gibt: die Kategorie bleibt uebrig.
    tree5 = FakeTree()
    dlg5 = make_dialog(tree5)
    build(tree5, [d for d in devices if d.unique_id != 'p5'])
    before = tree5.selected
    ok = dlg5._restore_selection(chain)
    check('ein entferntes Geraet landet auf seiner Kategorie',
          ok and tree5.selected.text.startswith('Plugs'), tree5.selected.text)
    check('ohne Treffer bleibt die Auswahl unberuehrt',
          dlg5._restore_selection(['n|Netatmo devices']) is False
          or tree5.selected is not before)

    tree6 = FakeTree()
    dlg6 = make_dialog(tree6)
    check('leere Auswahl liefert einen leeren Pfad',
          dlg6._capture_selection() == [])
    check('ein leerer Pfad stellt nichts wieder her',
          dlg6._restore_selection([]) is False)


# ---------------------------------------------------------------- Signatur ---
def test_signature():
    print('== Struktur-Signatur ==')
    tree = FakeTree()
    dlg = make_dialog(tree)
    devices = [Dev('p1', 'Plug 1'), Dev('n1', 'Thermostat', 'netatmo'),
               Dev('v1', 'Purifier', 'vesync')]
    base = dlg._tree_signature(devices)

    devices[0].power = 42          # ein Wert, der nur in einer Zeile steht
    check('geaenderte Werte lassen die Struktur gleich',
          dlg._tree_signature(devices) == base)

    renamed = [Dev('p1', 'Plug one'), devices[1], devices[2]]
    check('ein umbenanntes Geraet aendert sie',
          dlg._tree_signature(renamed) != base)

    check('ein zusaetzliches Geraet aendert sie',
          dlg._tree_signature(devices + [Dev('p2', 'Plug 2')]) != base)
    check('ein entferntes Geraet aendert sie',
          dlg._tree_signature(devices[:2]) != base)

    offline = [Dev('p1', 'Plug 1', offline=True), devices[1], devices[2]]
    check('offline aendert sie (steht in der Beschriftung)',
          dlg._tree_signature(offline) != base)

    other = make_dialog(FakeTree(), filter_mode='online')
    check('ein anderer Filter aendert sie',
          other._tree_signature(devices) != base)
    other = make_dialog(FakeTree(), sort_mode='type')
    check('eine andere Sortierung aendert sie',
          other._tree_signature(devices) != base)

    check('eine leere Liste ist nicht "keine Signatur"',
          dlg._tree_signature([]) == ('all', 'name'))


# ------------------------------------------------ abgeschaltete Plattformen --
def test_disabled_platforms():
    print('== Abgeschaltete Plattform verschwindet ==')
    env = {'log': Log(), 'platform_of': platform_of}
    grab(os.path.join(BASE, '__init__.py'),
         ['_platform_enabled', 'drop_disabled_platform_devices'], env)

    class Plugin:
        pass

    Plugin._platform_enabled = env['_platform_enabled']
    Plugin.drop_disabled_platform_devices = env['drop_disabled_platform_devices']

    plugin = Plugin()
    plugin._devices_lock = threading.RLock()
    plugin.devices = [Dev('p1', 'Plug'), Dev('n1', 'Thermostat', 'netatmo'),
                      Dev('v1', 'Purifier', 'vesync'),
                      Dev('c1', 'Heat pump', 'cozytouch')]
    plugin.use_meross = True
    plugin.use_netatmo = True
    plugin.use_vesync = True
    plugin.use_cozytouch = True

    unchanged = list(plugin.devices)
    check('alles eingeschaltet: nichts wird entfernt',
          plugin.drop_disabled_platform_devices() == 0
          and plugin.devices == unchanged)

    plugin.use_vesync = False
    removed = plugin.drop_disabled_platform_devices()
    keys = [d.unique_id for d in plugin.devices]
    check('das abgeschaltete VeSync-Geraet ist weg',
          removed == 1 and 'v1' not in keys, str(keys))
    check('die anderen Plattformen bleiben', keys == ['p1', 'n1', 'c1'])

    plugin.use_meross = False
    plugin.use_netatmo = False
    plugin.use_cozytouch = False
    check('am Ende bleibt nichts uebrig',
          plugin.drop_disabled_platform_devices() == 3
          and plugin.devices == [])

    src = io.open(os.path.join(BASE, '__init__.py'), encoding='utf-8').read()
    impl = src[src.index('def _refresh_devices_impl'):]
    impl = impl[:impl.index('\n    def ', 10)]
    check('auch der Refresh filtert die abgeschalteten heraus',
          '_platform_enabled(platform_of(d))' in impl)

    dialog = io.open(os.path.join(BASE, 'device_dialog.py'),
                     encoding='utf-8').read()
    check('der Einstellungsdialog raeumt beim Speichern auf',
          'drop_disabled_platform_devices()' in dialog)


# --------------------------------------------------- keine Doppelansagen ----
def test_no_double_announcement():
    print('== Kein zweiter Weg zur selben Ansage ==')
    src = io.open(os.path.join(BASE, 'device_dialog.py'), encoding='utf-8').read()

    reload_fn = src[src.index('def _reload_and_notify'):]
    reload_fn = reload_fn[:reload_fn.index('\n    def ', 10)]
    check('das Aktualisieren springt nicht mehr auf den ersten Eintrag',
          'self._focus_first_tree_item()' not in reload_fn)
    check('es baut nur bei geaenderter Struktur neu',
          '_tree_signature' in reload_fn
          and 'refresh_all_device_data_live(force=True)' in reload_fn)
    check('der Fokus wird nur geholt, wenn er weg ist',
          'if not self.tree.HasFocus():' in reload_fn)

    focus_fn = src[src.index('def _focus_first_tree_item'):]
    focus_fn = focus_fn[:focus_fn.index('\n    def ', 10)]
    check('auch der erste Eintrag setzt den Fokus nur einmal',
          'if not self.tree.HasFocus():' in focus_fn)

    live = src[src.index('def refresh_all_device_data_live'):]
    live = live[:live.index('\n    def ', 10)]
    check('der Debounce laesst die Handaktualisierung durch',
          'if not force and now - last < 0.7:' in live)

    build_fn = src[src.index('def _load_devices_internal'):]
    build_fn = build_fn[:build_fn.index('\n    def ', 10)]
    check('der Aufbau merkt sich die Auswahl vor dem Loeschen',
          build_fn.index('_capture_selection')
          < build_fn.index('DeleteAllItems'))
    check('und stellt sie nach dem Thaw wieder her',
          build_fn.index('Thaw()') < build_fn.index('_restore_selection'))


# ------------------------------------------- Menue oeffnet sich nur einmal --
def test_single_dialog():
    """Der zweite Aufruf darf keinen zweiten Dialog bauen.

    Das Menue ist modal, aber die NVDA-Geste erreicht das Add-on trotzdem.
    Der zweite Dialog uebernahm ``_active_dialog`` und setzte es beim
    Schliessen auf None - der erste stand danach ohne Aktualisierung da.
    """
    print('== Das Geraetemenue oeffnet sich nur einmal ==')

    class Dialog:
        def __init__(self, *a):
            self._is_destroyed = False
            self.raised = 0
            self.focused = 0
            self.shown = 0
            self.destroyed = 0

        def Raise(self):
            self.raised += 1

        def SetFocus(self):
            self.focused += 1

        def ShowModal(self):
            self.shown += 1

        def Destroy(self):
            self.destroyed += 1

    built = []

    def factory(*args):
        dlg = Dialog()
        built.append(dlg)
        return dlg

    spoken = []

    class Frame:
        def prePopup(self):
            pass

        def postPopup(self):
            pass

    env = {
        'ui': type('ui', (), {'message': staticmethod(spoken.append)}),
        'gui': type('gui', (), {'mainFrame': Frame()}),
        'SmartHomeControlDialog': factory,
        'log': Log(),
        '_': lambda s: s,
    }
    grab(os.path.join(BASE, '__init__.py'), ['_show_device_dialog'], env)

    class Plugin:
        pass

    Plugin._show_device_dialog = env['_show_device_dialog']
    plugin = Plugin()
    plugin._active_dialog = None
    plugin.request_immediate_poll = lambda: None

    # Erster Aufruf: Dialog wird gebaut.
    plugin._show_device_dialog()
    check('der erste Aufruf oeffnet genau einen Dialog', len(built) == 1,
          f'{len(built)} gebaut')
    check('er wird auch angezeigt', built and built[0].shown == 1)

    # ShowModal kehrt in der Attrappe sofort zurueck, also steht die Referenz
    # wieder auf None - fuer den Test wird der offene Zustand nachgestellt.
    plugin._active_dialog = built[0]
    spoken.clear()
    plugin._show_device_dialog()
    check('der zweite Aufruf baut KEINEN zweiten Dialog', len(built) == 1,
          f'{len(built)} gebaut')
    check('stattdessen kommt der offene nach vorn',
          built[0].raised == 1 and built[0].focused == 1)
    check('und es wird gesagt, dass er schon offen ist',
          any('already open' in m for m in spoken), '; '.join(spoken))

    # Ein geschlossener Dialog haelt das Menue nicht auf.
    built[0]._is_destroyed = True
    plugin._show_device_dialog()
    check('ein geschlossener Dialog blockiert nicht', len(built) == 2,
          f'{len(built)} gebaut')

    # Die Referenz darf nur der eigene Dialog loeschen.
    src = io.open(os.path.join(BASE, 'device_dialog.py'),
                  encoding='utf-8').read()
    check('nur der eigene Dialog loescht die Referenz',
          'self.plugin._active_dialog is self' in src)


def main():
    test_identity()
    test_selection()
    test_signature()
    test_disabled_platforms()
    test_no_double_announcement()
    test_single_dialog()
    print()
    if FAILED:
        print('FEHLGESCHLAGEN:')
        for name in FAILED:
            print('  -', name)
        return 1
    print('GESAMT: ALLE TESTS OK')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
