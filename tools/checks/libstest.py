# -*- coding: utf-8 -*-
"""Tests that the bundled libraries are the ones that were pinned.

The add-on ships its dependencies - requests, urllib3, certifi, aiohttp,
meross_iot and the rest - inside the package, so nothing here is updated
by pip on a user's machine. Whatever is in ``lib/`` at build time is what
runs on every installation until the next release. That makes three
things worth asserting, none of which needs a network:

* **The files are what the wheel said they are.** Every ``.dist-info``
  carries a RECORD with a SHA256 of each unpacked file. Verifying it
  catches a library that was edited after unpacking - and the far more
  likely accident, a line-ending conversion, which is exactly why
  ``.gitattributes`` marks ``lib/**`` as ``-text``. A silent mangling
  there is hard to see and breaks the compiled packages.
* **The versions match the pins.** ``requirements-bundle.txt`` is the
  file a security update is made in; if the bundle does not follow it,
  the update did not happen. A leftover from an earlier version counts
  as a mismatch too - one sat in ``lib/`` for a whole release.
* **Both architectures are complete.** The compiled packages exist once
  per NVDA Python (``cp311-win32`` and ``cp313-amd64``). There is no
  pure-Python fallback: if one side is missing a package, Meross support
  is gone on that architecture, not slower.

What this does NOT do is ask the internet whether a newer version exists
or whether a CVE has been published. That is a decision with a release
attached to it, and it belongs to whoever makes the release - not to a
check that has to pass offline.
"""
import base64
import csv
import hashlib
import io
import os
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.environ.get('SHC', os.path.join(ROOT, 'globalPlugins', 'SmartHomeControl'))
# lib/ sits next to globalPlugins/, so it follows from BASE even when that
# was overridden.
LIB = os.path.join(os.path.dirname(os.path.dirname(BASE)), 'lib') \
    if os.environ.get('SHC') else os.path.join(ROOT, 'lib')
REQUIREMENTS = os.path.join(ROOT, 'requirements-bundle.txt')

# Same targets as build_addon.py - kept here as a literal on purpose: the
# check is about what was SHIPPED, so importing the build script's idea of
# it would only confirm the build agrees with itself.
ARCH_DIRS = ('cp311-win32', 'cp313-amd64')

FAILED = []


def check(name, cond, detail=''):
    print(f"  {'OK  ' if cond else 'FEHL'}   {name}" + (f'  ({detail})' if detail else ''))
    if not cond:
        FAILED.append(name)


def read_pins():
    """{package: version} from requirements-bundle.txt, split by section."""
    pure, arch, section = {}, {}, None
    with io.open(REQUIREMENTS, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line.startswith('#'):
                if '[pure]' in line:
                    section = pure
                elif '[arch]' in line:
                    section = arch
                continue
            m = re.match(r'^([A-Za-z0-9_.\-]+)==([0-9][^\s]*)$', line)
            if m and section is not None:
                section[_normalise(m.group(1))] = m.group(2)
    return pure, arch


def _normalise(name):
    """PyPI treats - and _ alike, and so do the dist-info folder names."""
    return name.lower().replace('-', '_')


def installed_versions(directory):
    """{package: version} from the .dist-info folders of one directory."""
    found = {}
    if not os.path.isdir(directory):
        return found
    for entry in os.listdir(directory):
        m = re.match(r'^(.+?)-([0-9][^-]*)\.dist-info$', entry)
        if m:
            found[_normalise(m.group(1))] = m.group(2)
    return found


# ---------------------------------------------------------------- tests ----
def test_pins_are_what_is_bundled():
    print('== the bundle follows requirements-bundle.txt ==')
    pure, arch = read_pins()
    check('the requirements file lists both sections',
          bool(pure) and bool(arch), f'{len(pure)} pure, {len(arch)} arch')

    have = installed_versions(LIB)
    for name, version in sorted(pure.items()):
        check(f'{name} {version}', have.get(name) == version,
              f'bundled: {have.get(name) or "MISSING"}')

    for arch_dir in ARCH_DIRS:
        path = os.path.join(LIB, '_arch', arch_dir)
        check(f'{arch_dir} exists', os.path.isdir(path))
        if not os.path.isdir(path):
            continue
        have_arch = installed_versions(path)
        missing = [n for n in arch if n not in have_arch]
        check(f'{arch_dir} carries every compiled package', not missing,
              f'missing: {missing}' if missing else f'{len(have_arch)} packages')
        wrong = {n: have_arch[n] for n, v in arch.items()
                 if n in have_arch and have_arch[n] != v}
        check(f'{arch_dir} versions match the pins', not wrong, str(wrong))


def test_no_leftovers_from_earlier_versions():
    print('== nothing from an earlier version is left lying around ==')
    pure, arch = read_pins()
    expected = dict(pure)
    expected.update(arch)
    stale = []
    for dirpath, dirnames, filenames in os.walk(LIB):
        for entry in list(dirnames):
            m = re.match(r'^(.+?)-([0-9][^-]*)\.(dist-info|data)$', entry)
            if not m:
                continue
            name, version = _normalise(m.group(1)), m.group(2)
            if name in expected and version != expected[name]:
                stale.append(os.path.relpath(os.path.join(dirpath, entry), LIB))
    check('no folder of a version that is no longer pinned', not stale,
          str(stale[:4]))


def test_record_hashes():
    print('== every file is the one the wheel shipped ==')
    checked = mismatched = missing = 0
    offenders = []
    for dirpath, dirnames, filenames in os.walk(LIB):
        if not dirpath.endswith('.dist-info'):
            continue
        record = os.path.join(dirpath, 'RECORD')
        if not os.path.isfile(record):
            offenders.append(('no RECORD', os.path.relpath(dirpath, LIB)))
            missing += 1
            continue
        package_root = os.path.dirname(dirpath)
        with io.open(record, encoding='utf-8', newline='') as fh:
            for row in csv.reader(fh):
                if len(row) < 2 or not row[1].startswith('sha256='):
                    continue
                target = os.path.join(package_root, row[0].replace('/', os.sep))
                if not os.path.isfile(target):
                    missing += 1
                    offenders.append(('missing', row[0]))
                    continue
                with open(target, 'rb') as fh2:
                    digest = hashlib.sha256(fh2.read()).digest()
                actual = 'sha256=' + base64.urlsafe_b64encode(digest).rstrip(b'=').decode()
                checked += 1
                if actual != row[1]:
                    mismatched += 1
                    offenders.append(('changed', row[0]))

    check('there is something to check at all', checked > 100, f'{checked} files')
    check('no file differs from its recorded hash', mismatched == 0,
          f'{mismatched} changed: {[o[1] for o in offenders if o[0] == "changed"][:3]}')
    check('no file listed in a RECORD is missing', missing == 0,
          f'{missing}: {[o[1] for o in offenders if o[0] != "changed"][:3]}')


def test_compiled_extensions_are_present():
    print('== the compiled extensions are actually there ==')
    for arch_dir in ARCH_DIRS:
        path = os.path.join(LIB, '_arch', arch_dir)
        if not os.path.isdir(path):
            continue
        pyds = []
        for dirpath, _dirnames, filenames in os.walk(path):
            pyds += [f for f in filenames if f.endswith('.pyd')]
        # Cryptodome alone brings dozens; a handful would mean the wheel
        # for this target was the pure-Python one.
        check(f'{arch_dir} has compiled modules', len(pyds) > 20,
              f'{len(pyds)} .pyd')


def main():
    if not os.path.isdir(LIB):
        print(f'  FEHL   lib/ not found at {LIB}')
        return 1
    test_pins_are_what_is_bundled()
    test_no_leftovers_from_earlier_versions()
    test_record_hashes()
    test_compiled_extensions_are_present()
    print()
    if FAILED:
        print(f'FEHLGESCHLAGEN: {len(FAILED)} -> {FAILED}')
        return 1
    print('GESAMT: ALLE TESTS OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
