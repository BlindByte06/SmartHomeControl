# -*- coding: utf-8 -*-
"""Tests that every setting is declared before it is read.

NVDA keeps its configuration in an ini file, and a key without an entry in
CONFSPEC comes back as the TEXT that stands there - there is no
declaration to validate it against. A bool written as True is read back as
"True", and wx.CheckBox.SetValue raises a TypeError on a string.

That is not a theory. A notification flag shipped without its CONFSPEC
entry took the entire settings dialog down - not the tab it was on, the
whole dialog - and it did so a day late: on the first run the key does not
exist yet, so the Python default is used and everything works. Only once
the settings have been saved does the value turn into text.

A second key, the favorites layer's switching window, was missing in
exactly the same way and survived purely because the code that reads it
happens to wrap an int() in a try.

Reads the shipped source with ast, so it needs neither NVDA nor an account.
"""
import ast
import io
import os

BASE = os.environ.get(
    'SHC',
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'globalPlugins', 'SmartHomeControl'))

FAILED = []


def check(name, cond, detail=''):
    print("  " + ('OK  ' if cond else 'FEHL') + "   " + name
          + (("  (" + str(detail) + ")") if detail else ''))
    if not cond:
        FAILED.append(name)


def _source(filename):
    return io.open(os.path.join(BASE, filename), encoding='utf-8').read()


def _tree(filename):
    return ast.parse(_source(filename), filename)


def conf_keys_used(tree):
    """Every key the code reads from or writes to the config section."""
    read, written = set(), set()
    for node in ast.walk(tree):
        # conf.get("key", default)
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'get'
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == 'conf'
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            read.add(node.args[0].value)
        # conf["key"] = value
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == 'conf'
                        and isinstance(target.slice, ast.Constant)
                        and isinstance(target.slice.value, str)):
                    written.add(target.slice.value)
    return read, written


def confspec(tree):
    """The declared keys and their declarations."""
    for node in tree.body:
        if (isinstance(node, ast.Assign) and node.targets
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == 'CONFSPEC'
                and isinstance(node.value, ast.Dict)):
            out = {}
            for key, value in zip(node.value.keys, node.value.values):
                if (isinstance(key, ast.Constant)
                        and isinstance(value, ast.Constant)):
                    out[key.value] = value.value
            return out
    return {}


def notification_attributes(tree):
    """The attribute names the notifications tab builds checkboxes from.

    They arrive as the first element of an (attr, label, default) tuple
    inside the list handed to add_section.
    """
    names = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == 'add_section'):
            for arg in node.args:
                if not isinstance(arg, ast.List):
                    continue
                for element in arg.elts:
                    if (isinstance(element, ast.Tuple) and element.elts
                            and isinstance(element.elts[0], ast.Constant)
                            and isinstance(element.elts[0].value, str)):
                        names.add(element.elts[0].value)
    return names


def self_attributes_assigned(tree):
    """Every self.X the plugin assigns anywhere."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == 'self'):
                    names.add(target.attr)
    return names


def _secret_keys():
    """SECRET_KEYS from credential_store.py, read out of the source."""
    tree = _tree('credential_store.py')
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and node.targets
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == 'SECRET_KEYS'):
            return {e.value for e in node.value.elts
                    if isinstance(e, ast.Constant)}
    return set()


def main():
    plugin = _tree('__init__.py')
    consts = _tree('constants.py')
    panel = _tree('settings_panel.py')

    spec = confspec(consts)
    read, written = conf_keys_used(plugin)
    used = read | written

    # The credential store reads and clears its keys through a loop over
    # SECRET_KEYS, so no static scan can see them in __init__.py. They must
    # stay declared all the same: the store still reads them out of the
    # configuration when it moves them into its own file, and an undeclared
    # key comes back as raw text instead of a string - the very trap this
    # file exists for. Dropping them would break the move for everyone
    # updating from 26.8.2 or older.
    secret_keys = _secret_keys()
    used |= secret_keys

    print('== every setting the code touches is declared ==')
    check('CONFSPEC was found', bool(spec), str(len(spec)) + ' keys')
    # The one that cost a settings dialog: undeclared means the value comes
    # back as text as soon as it has been saved once.
    undeclared = sorted(used - set(spec))
    check('nothing is read or written without a declaration',
          not undeclared, ', '.join(undeclared))
    missing_secrets = sorted(secret_keys - set(spec))
    check('every credential key is still declared', not missing_secrets,
          ', '.join(missing_secrets) or 'the migration reads them')
    orphans = sorted(set(spec) - used)
    check('nothing is declared that nobody uses', not orphans,
          ', '.join(orphans))
    unread = sorted(written - read)
    check('everything written is also read again', not unread,
          ', '.join(unread))

    print('== a checkbox can only ever be handed a bool ==')
    wrong = sorted(k for k, v in spec.items()
                   if k.startswith('notify') and not v.startswith('boolean'))
    check('every notify setting is declared boolean', not wrong,
          ', '.join(wrong))

    print('== the notifications tab and the settings agree ==')
    attrs = notification_attributes(panel)
    assigned = self_attributes_assigned(plugin)
    check('the tab was read', bool(attrs), str(len(attrs)) + ' checkboxes')
    # A checkbox whose attribute the plugin never sets falls back to the
    # tuple's default and silently stops reflecting the setting.
    missing = sorted(a for a in attrs if a not in assigned)
    check('every checkbox has an attribute the plugin sets', not missing,
          ', '.join(missing))

    print('== the coercion that stands underneath it ==')
    src = _source('settings_panel.py')
    function = None
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == '_as_bool':
            function = ast.get_source_segment(src, node)
    if function is None:
        check('_as_bool exists', False)
    else:
        env = {}
        exec(compile(ast.parse(function), 'settings_panel.py', 'exec'), env)
        as_bool = env['_as_bool']
        # The trap this walks around: a bare bool("False") is True.
        check('the text False is False', as_bool('False') is False)
        check('the text True is True', as_bool('True') is True)
        check('a real bool passes through',
              as_bool(True) is True and as_bool(False) is False)
        check('None falls back to the default',
              as_bool(None, True) is True and as_bool(None, False) is False)
        check('nonsense falls back to the default',
              as_bool('lawnmower', True) is True)

    print()
    if FAILED:
        print('FEHLGESCHLAGEN: ' + str(len(FAILED)) + ' -> ' + str(FAILED))
        return 1
    print('GESAMT: ALLE TESTS OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
