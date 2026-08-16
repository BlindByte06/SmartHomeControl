# -*- coding: utf-8 -*-
"""
Smart Home Control - build script

Produces the lib bundle and the finished .nvda-addon package reproducibly.
Runs on any ordinary Python 3.9+ (tested on Windows); it only needs pip on
the PATH, and an internet connection for the "libs" command.

Usage (run inside this folder):
    python build_addon.py pack     build the package into dist/
    python build_addon.py libs     rebuild lib/ and lib/_arch/ from requirements-bundle.txt
    python build_addon.py all      libs + pack
    python build_addon.py pot      rebuild the translation template from the source
    python build_addon.py mo       compile every .po under locale/ into its .mo

Only these go into the package: manifest.ini, globalPlugins/, lib/, doc/,
locale/ and LICENSE. Excluded are __pycache__, *.pyc/*.pyo, *.log, _old_*
folders, upstream source packages, .git*, working notes and this script.
"""

import argparse
import fnmatch
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
REQUIREMENTS = os.path.join(ROOT, "requirements-bundle.txt")
LIB_DIR = os.path.join(ROOT, "lib")
DIST_DIR = os.path.join(ROOT, "dist")

# Architecture targets: (folder name, pip platform, Python version)
# NVDA 2025.x = 32-bit Python 3.11; NVDA 2026.1+ = 64-bit Python 3.13.
ARCH_TARGETS = [
    ("cp311-win32", "win32", "3.11"),
    ("cp313-amd64", "win_amd64", "3.13"),
]

# Only these top-level entries belong in the package.
# doc/, locale/ and LICENSE are optional (skipped when absent).
INCLUDE_TOP = ("manifest.ini", "globalPlugins", "lib", "doc", "locale", "LICENSE")
OPTIONAL_TOP = {"doc", "locale", "LICENSE"}

# "SelfTest" is the test suite of pycryptodomex: 196 files, 2.8 MB
# unpacked, never imported at runtime (verified: no hit in the add-on code
# nor in meross_iot). The same goes for aiohttp's test_utils.
EXCLUDE_DIR_NAMES = {"__pycache__", "SelfTest"}
EXCLUDE_DIR_GLOBS = ["_old_*"]
EXCLUDE_FILE_GLOBS = ["*.pyc", "*.pyo", "*.log", "*.tmp", "*.bak",
                      "test_utils.py"]


def read_requirements():
    """Reads requirements-bundle.txt, splitting [pure] and [arch] packages."""
    pure, arch = [], []
    current = None
    with open(REQUIREMENTS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "[pure]" in line:
                current = pure
                continue
            if "[arch]" in line:
                current = arch
                continue
            if not line or line.startswith("#"):
                continue
            if current is None:
                raise SystemExit(f"Line outside any section: {line}")
            current.append(line)
    return pure, arch


def pip_download(dest, packages, platform=None, python_version=None):
    """Downloads wheels for the given packages (without dependencies - the
    requirements file deliberately pins EVERY needed package explicitly)."""
    for pkg in packages:
        cmd = [sys.executable, "-m", "pip", "download", "--only-binary=:all:",
               "--no-deps", "-d", dest, pkg]
        if platform:
            cmd += ["--platform", platform]
        if python_version:
            cmd += ["--python-version", python_version]
        print(f"  pip download {pkg}" + (f" [{platform}/py{python_version}]" if platform else ""))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise SystemExit(
                f"pip download failed for {pkg}:\n{result.stderr[-800:]}")


def extract_wheels(wheel_dir, target_dir):
    """Unpacks every wheel of a folder into the target directory."""
    os.makedirs(target_dir, exist_ok=True)
    for name in sorted(os.listdir(wheel_dir)):
        if name.endswith(".whl"):
            with zipfile.ZipFile(os.path.join(wheel_dir, name)) as z:
                z.extractall(target_dir)
            print(f"  unpacked: {name}")


def cmd_libs():
    """Rebuilds lib/ and lib/_arch/ from requirements-bundle.txt."""
    pure, arch = read_requirements()
    print(f"[libs] {len(pure)} pure + {len(arch)} arch packages")

    with tempfile.TemporaryDirectory() as tmp:
        # 1. Pure Python packages -> lib/
        pure_dl = os.path.join(tmp, "pure")
        pip_download(pure_dl, pure)

        # 2. Arch packages per target
        arch_dls = {}
        for arch_name, plat, pyver in ARCH_TARGETS:
            d = os.path.join(tmp, arch_name)
            pip_download(d, arch, platform=plat, python_version=pyver)
            arch_dls[arch_name] = d

        # 3. Replace the old folders only AFTER a successful download, so a
        #    failed one leaves the bundle untouched.
        staging = os.path.join(tmp, "staging_lib")
        extract_wheels(pure_dl, staging)
        for arch_name, _, _ in ARCH_TARGETS:
            extract_wheels(arch_dls[arch_name],
                           os.path.join(staging, "_arch", arch_name))

        # Keep the README in the _arch folder if there is one
        old_readme = os.path.join(LIB_DIR, "_arch", "README.md")
        if os.path.isfile(old_readme):
            shutil.copy2(old_readme, os.path.join(staging, "_arch", "README.md"))

        backup = LIB_DIR + ".old"
        if os.path.isdir(backup):
            shutil.rmtree(backup)
        if os.path.isdir(LIB_DIR):
            os.replace(LIB_DIR, backup)
        shutil.move(staging, LIB_DIR)
        print(f"[libs] done. The previous version is in {backup} "
              f"(delete it after a functional test).")


def read_version():
    """Reads the version number from manifest.ini."""
    with open(os.path.join(ROOT, "manifest.ini"), encoding="utf-8") as f:
        content = f.read()
    m = re.search(r'^version\s*=\s*"?([^"\r\n]+)"?', content, re.MULTILINE)
    if not m:
        raise SystemExit("No version found in manifest.ini")
    return m.group(1).strip()


def cmd_relnotes(version=None, out_path=None):
    """Cuts the section of one version out of CHANGELOG.md.

    A GitHub release carries the changelog of its version. There is exactly
    one changelog file and it is English - the official add-on template and
    the larger NVDA add-ons do the same, since release pages are read
    internationally.

    Without --version the version comes from manifest.ini; in the workflow it
    comes from the tag. A missing section is an error rather than a silent
    fallback to placeholder text: a release without a changelog would only be
    noticed once it is published.
    """
    version = version or read_version()
    path = os.path.join(ROOT, "CHANGELOG.md")
    if not os.path.exists(path):
        raise SystemExit(f"[relnotes] {path} is missing")
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    # Section = from "## <version>" up to the next "## " heading.
    start = None
    for i, line in enumerate(lines):
        if re.match(r'^##\s+' + re.escape(version) + r'(\s|$)', line):
            start = i
            break
    if start is None:
        raise SystemExit(
            f"[relnotes] No section '## {version}' in CHANGELOG.md - "
            f"add the changelog entry before tagging")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break

    body = "\n".join(lines[start + 1:end]).strip()
    if not body:
        raise SystemExit(f"[relnotes] Section '## {version}' is empty")

    text = (f"## Smart Home Control {version}\n\n{body}\n\n"
            "---\n\n"
            "### Installation\n\n"
            "1. Download the `.nvda-addon` file below\n"
            "2. Open the file with NVDA\n"
            "3. Confirm the installation\n"
            "4. Restart NVDA\n\n"
            "See the [README](README.md) for setup instructions per "
            "platform.\n")

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[relnotes] {version} -> {out_path} ({len(body.splitlines())} lines)")
    else:
        # The console is often cp1252 on Windows while the changelog holds
        # characters such as CO2 with a subscript. Without switching, printing
        # would abort here.
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
        print(text)


def _dir_excluded(name):
    return name in EXCLUDE_DIR_NAMES or any(
        fnmatch.fnmatch(name, g) for g in EXCLUDE_DIR_GLOBS)


def _file_excluded(name):
    return any(fnmatch.fnmatch(name, g) for g in EXCLUDE_FILE_GLOBS)


def sync_doc_titles(version):
    """Keeps the version in the window title of the help files current.

    NVDA shows the <title> in the browser window when the help is opened; the
    convention among community add-ons is "Name Version" (for example
    "Smart Home Control 26.7.5"). Synchronised from manifest.ini on every
    pack so the version can never go stale.
    """
    for lang_dir in sorted(os.listdir(os.path.join(ROOT, "doc"))) if os.path.isdir(os.path.join(ROOT, "doc")) else []:
        path = os.path.join(ROOT, "doc", lang_dir, "readme.html")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            content = f.read()
        new_content = re.sub(
            r"<title>Smart Home Control[^<]*</title>",
            f"<title>Smart Home Control {version}</title>",
            content,
        )
        if new_content != content:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"[pack] title updated: doc/{lang_dir}/readme.html -> {version}")


def cmd_check_min_python(strict=True):
    """Checks the code against the Python version of the oldest NVDA.

    minimumNVDAVersion 2025.1 means Python 3.11. There it is a SyntaxError
    when an expression INSIDE an f-string uses the same quote as the f-string
    itself - only 3.12 allows that (PEP 701).

    The add-on has already failed at a user for exactly this reason while
    compiling without complaint on the development machine (Python 3.12):

        f"{device.name}: {_("Error")}. "   ->  a SyntaxError on 3.11

    A `python -m compileall` with a newer version does NOT find it, which is
    why this check exists. It runs as part of `pack`, so a package that will
    not start on the minimum version is never produced in the first place.
    """
    import ast
    problems = []
    pattern = os.path.join(ROOT, "globalPlugins", "SmartHomeControl", "*.py")
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for node in ast.walk(ast.parse(src, path)):
            if not isinstance(node, ast.JoinedStr):
                continue
            seg = ast.get_source_segment(src, node)
            if not seg:
                continue
            m = re.match(r'^[a-zA-Z]*("""|\'\'\'|"|\')', seg)
            if not m:
                continue
            quote = m.group(1)
            inner = seg[len(m.group(0)):]
            if inner.endswith(quote):
                inner = inner[:-len(quote)]
            for expr in re.findall(r"\{([^{}]*)\}", inner):
                if quote in expr:
                    problems.append(
                        f"{os.path.basename(path)}:{node.lineno} - expression "
                        f"in the f-string uses {quote} like the f-string itself "
                        f"(a SyntaxError on Python 3.11): {seg[:70]}")
                    break
                # Second trap of the same kind: backslashes in the
                # expression are also allowed only from 3.12.
                if "\\" in expr:
                    problems.append(
                        f"{os.path.basename(path)}:{node.lineno} - backslash in "
                        f"the f-string expression (a SyntaxError on Python 3.11): "
                        f"{seg[:70]}")
                    break

    if problems:
        print("[py311] " + "\n[py311] ".join(problems))
        if strict:
            raise SystemExit(
                "The code will not run on the minimum NVDA version - use "
                "the other quote inside the f-string")
        return False
    print("[py311] OK - no nested quotes inside f-strings")
    return True


def _translatable_literals():
    """Every `_("...")` literal in the source, with where it was found.

    Purely static, over the AST - no xgettext needed, which is rarely
    installed on Windows anyway. Only calls with a string literal as their
    first argument are collected; f-strings and variables cannot be
    translated in any case.
    """
    import ast
    found = {}
    pattern = os.path.join(ROOT, "globalPlugins", "SmartHomeControl", "*.py")
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), path)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_" and node.args):
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.setdefault(
                    arg.value, f"{os.path.basename(path)}:{node.lineno}")
    return found


def _translatable_entries():
    """Like `_translatable_literals`, plus the Translators comment.

    The comment above the `_()` call is often more important to a translator
    than the text itself: it says whether "Off" is a device state, a menu
    entry or a button. It is collected upwards from the line above the call
    for as long as comment lines are found there.
    """
    import ast
    entries = {}
    pattern = os.path.join(ROOT, "globalPlugins", "SmartHomeControl", "*.py")
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as f:
            source = f.read()
        lines = source.splitlines()
        tree = ast.parse(source, path)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_" and node.args):
                continue
            arg = node.args[0]
            if not (isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)):
                continue
            block = []
            for i in range(node.lineno - 2, max(-1, node.lineno - 10), -1):
                line = lines[i].strip()
                if line.startswith("#"):
                    block.insert(0, line.lstrip("# ").rstrip())
                else:
                    break
            comment = " ".join(block)
            # Cut from the marker rather than testing for it: an ordinary
            # code comment often sits above the Translators line, and a
            # "startswith" would discard the whole hint.
            marker = comment.find("Translators")
            if marker > 0:
                comment = comment[marker:]
            entry = entries.setdefault(
                arg.value, {"occurrences": [], "comment": ""})
            entry["occurrences"].append(
                (os.path.basename(path), str(node.lineno)))
            if comment.startswith("Translators") and not entry["comment"]:
                entry["comment"] = comment
    return entries


def cmd_pot():
    """Rewrites locale/SmartHomeControl.pot from the source.

    Without this command the template ages silently with every change to the
    code, and translators are handed a file in which new texts are simply
    absent. Existing translations are NOT touched; they pick up the additions
    through "Update from POT file" (Poedit) or msgmerge.
    """
    import polib
    from datetime import datetime, timezone
    entries = _translatable_entries()
    pot_path = os.path.join(ROOT, "locale", "SmartHomeControl.pot")

    pot = polib.POFile()
    pot.metadata = {
        "Project-Id-Version": "SmartHomeControl",
        "Report-Msgid-Bugs-To": "blindbyte06@gmail.com",
        "POT-Creation-Date": datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M+0000"),
        "MIME-Version": "1.0",
        "Content-Type": "text/plain; charset=UTF-8",
        "Content-Transfer-Encoding": "8bit",
        "Language-Team": "LANGUAGE <LL@li.org>",
    }
    # Do not lose existing hints: some sit there as an ordinary code comment
    # ("Device filter") and still carry meaning for a translator. What the
    # source yields wins; what it does not yield is carried over from the
    # previous template.
    previous = {}
    if os.path.isfile(pot_path):
        previous = {e.msgid: e.comment for e in polib.pofile(pot_path)
                    if e.msgid and e.comment}
    carried = 0
    for msgid, data in entries.items():
        comment = data["comment"] or previous.get(msgid, "")
        if not data["comment"] and comment:
            carried += 1
        pot.append(polib.POEntry(
            msgid=msgid, msgstr="",
            comment=comment,
            occurrences=data["occurrences"]))
    pot.sort(key=lambda e: e.msgid)
    pot.save(pot_path)
    with_hint = sum(1 for e in pot if e.comment)
    print(f"[pot] {len(pot)} texts written, {with_hint} with a hint "
          f"({carried} carried over from the previous template) "
          f"-> {pot_path}")
    return pot_path


def cmd_mo():
    """Compiles every .po under locale/ into the .mo beside it.

    NVDA loads the .mo and nothing else. A translation delivered as a .po
    alone - from a web-based tool, say - would otherwise have no effect
    anywhere.
    """
    import polib
    pattern = os.path.join(ROOT, "locale", "*", "LC_MESSAGES", "nvda.po")
    po_files = sorted(glob.glob(pattern))
    if not po_files:
        print("[mo] no .po files found under locale/")
        return
    for po_path in po_files:
        po = polib.pofile(po_path)
        mo_path = po_path[:-3] + ".mo"
        po.save_as_mofile(mo_path)
        lang = os.path.basename(os.path.dirname(os.path.dirname(po_path)))
        missing = sum(1 for e in po if not e.msgstr)
        print(f"[mo] {lang}: {len(po)} texts, {missing} untranslated "
              f"-> {os.path.basename(mo_path)}")


def cmd_check_translations(strict=True):
    """Checks every translation under locale/ against the source.

    The source language is English: the `_()` texts in the code ARE the
    msgids, and each language under locale/ is a translation of them. A user
    without a matching translation therefore sees English, not the language
    of the author.

    Four failure modes, all of which have happened:

    1. A `_()` string is missing from a .po -> English appears there although
       the interface runs in that language.
    2. The .mo is older than the .po -> NVDA loads the .mo, so the new
       translations have no effect.
    3. Untranslated or fuzzy entries.
    4. The .pot no longer matches the code - translators would be handed a
       stale template.

    Runs automatically as part of `pack`.
    """
    try:
        import polib
    except ImportError:
        print("[i18n] polib not installed - check skipped "
              "(pip install polib)")
        return True

    locale_dir = os.path.join(ROOT, "locale")
    literals = _translatable_literals()
    problems = []

    # The .pot is the template for translators and has to mirror the code.
    pot_path = os.path.join(locale_dir, "SmartHomeControl.pot")
    if not os.path.isfile(pot_path):
        problems.append(f"{pot_path} is missing - translators have no template")
    else:
        pot_ids = {e.msgid for e in polib.pofile(pot_path) if e.msgid}
        pot_missing = sorted(set(literals) - pot_ids)
        if pot_missing:
            problems.append(
                f"{len(pot_missing)} strings in the code are missing from "
                f"the .pot (e.g. {literals[pot_missing[0]]}) - run "
                f"'build_addon.py pot'")

    langs = sorted(d for d in os.listdir(locale_dir)
                   if os.path.isdir(os.path.join(locale_dir, d)))
    if not langs:
        print("[i18n] no translations present (source language is English)")
        return True

    counts = []
    for lang in langs:
        po_path = os.path.join(locale_dir, lang, "LC_MESSAGES", "nvda.po")
        mo_path = os.path.join(locale_dir, lang, "LC_MESSAGES", "nvda.mo")
        if not os.path.isfile(po_path):
            problems.append(f"{lang}: nvda.po is missing")
            continue
        if not os.path.isfile(mo_path):
            problems.append(f"{lang}: nvda.mo is missing - at runtime NVDA "
                            f"loads the .mo, not the .po")
            continue

        po = polib.pofile(po_path)
        mo = polib.mofile(mo_path)
        po_ids = {e.msgid for e in po}

        missing = sorted(set(literals) - po_ids)
        if missing:
            problems.append(f"{lang}: {len(missing)} strings in the code are "
                            f"missing from the .po:")
            for msgid in missing[:15]:
                problems.append(f"    {literals[msgid]:<28} {msgid[:60]!r}")
            if len(missing) > 15:
                problems.append(f"    ... and {len(missing) - 15} more")

        drift = po_ids ^ {e.msgid for e in mo}
        if drift:
            problems.append(f"{lang}: the .mo differs from the .po "
                            f"({len(drift)} entries) - run 'build_addon.py mo'")
        if po.untranslated_entries():
            problems.append(f"{lang}: {len(po.untranslated_entries())} "
                            f"untranslated entries")
        if po.fuzzy_entries():
            problems.append(f"{lang}: {len(po.fuzzy_entries())} fuzzy entries")
        counts.append(f"{lang}={len(po)}")

    if problems:
        print("[i18n] " + "\n[i18n] ".join(problems))
        if strict:
            raise SystemExit("Translation check failed")
        return False

    print(f"[i18n] OK - {len(literals)} strings in the code (English), "
          f"translations: {', '.join(counts)}")
    return True


def cmd_licenses(write=False):
    """Reads the licences of the bundled packages from their METADATA.

    Background: the add-on is GPL-2.0-or-later, and at least one bundled
    package (paho-mqtt) is dual licensed with only one half compatible with
    the GPL. Maintaining this overview by hand goes wrong as soon as a
    version changes - hence it is generated.
    """
    rows = {}
    for meta in glob.glob(os.path.join(ROOT, "lib", "**", "*.dist-info",
                                       "METADATA"), recursive=True):
        name = version = license_name = ""
        classifiers = []
        with open(meta, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    break  # end of the header
                if line.startswith("Name: "):
                    name = line[6:].strip()
                elif line.startswith("Version: "):
                    version = line[9:].strip()
                elif line.startswith("License-Expression: "):
                    license_name = line[20:].strip()
                elif line.startswith("License: ") and not license_name:
                    value = line[9:].strip()
                    if len(value) < 60:  # long values are the full licence text
                        license_name = value
                elif line.startswith("Classifier: License ::"):
                    classifiers.append(line.split("::")[-1].strip())
        if not license_name and classifiers:
            license_name = " / ".join(dict.fromkeys(classifiers))
        if name:
            rows[name.lower()] = (name, version, license_name or "?")

    lines = ["| Package | Version | Licence |", "|---|---|---|"]
    for key in sorted(rows):
        name, version, license_name = rows[key]
        lines.append(f"| `{name}` | {version} | {license_name} |")
    table = "\n".join(lines)
    print(table)
    print(f"\n[licenses] {len(rows)} packages")
    if write:
        out = os.path.join(ROOT, "THIRD_PARTY_LICENSES.md")
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write("# Bundled third-party components\n\n"
                    "Generated with `python build_addon.py licenses --write` "
                    "from the `*.dist-info/METADATA` fields of the bundled "
                    "packages.\n\n" + table + "\n")
        print(f"[licenses] written: {out}")
    return rows


def cmd_pack(out_dir=None):
    """Builds the .nvda-addon package."""
    version = read_version()
    # First whether it runs on the minimum version - nobody needs a package
    # that will not start there.
    cmd_check_min_python()
    cmd_check_translations()
    sync_doc_titles(version)
    os.makedirs(DIST_DIR, exist_ok=True)
    out_path = os.path.join(DIST_DIR, f"SmartHomeControl-{version}.nvda-addon")

    count = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for top in INCLUDE_TOP:
            p = os.path.join(ROOT, top)
            if os.path.isfile(p):
                z.write(p, top)
                count += 1
                continue
            if not os.path.isdir(p):
                if top in OPTIONAL_TOP:
                    continue
                raise SystemExit(f"Mandatory entry missing: {top}")
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if not _dir_excluded(d)]
                for fname in files:
                    if _file_excluded(fname):
                        continue
                    full = os.path.join(root, fname)
                    z.write(full, os.path.relpath(full, ROOT))
                    count += 1

    size_mb = os.path.getsize(out_path) / 1024 / 1024
    print(f"[pack] {out_path}  ({count} files, {size_mb:.1f} MB)")

    # Integrity check
    with zipfile.ZipFile(out_path) as z:
        bad = z.testzip()
        names = z.namelist()
    assert bad is None, f"Corrupt file in the ZIP: {bad}"
    assert "manifest.ini" in names, "manifest.ini is missing from the package"
    forbidden = [n for n in names if "__pycache__" in n or "_old_" in n
                 or n.endswith((".pyc", ".log"))]
    assert not forbidden, f"Forbidden files in the package: {forbidden[:5]}"
    # The .mo is what NVDA loads at runtime. Without it the add-on is
    # silently monolingual, with no error message. That happens exactly when
    # a .gitignore rule excludes `*.mo` and the package is built from a fresh
    # clone.
    mo_files = [n for n in names if n.endswith(".mo")]
    assert mo_files, ("No .mo in the package - the translations are missing "
                      "(check .gitignore for a *.mo rule)")
    print(f"[pack] integrity OK ({len(mo_files)} translation file(s))")

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        dest = os.path.join(out_dir, os.path.basename(out_path))
        shutil.copy2(out_path, dest)
        print(f"[pack] copied to: {dest}")


def main():
    parser = argparse.ArgumentParser(description="Smart Home Control Build")
    parser.add_argument("command",
                        choices=["libs", "pack", "all", "i18n", "licenses",
                                 "relnotes", "py311", "pot", "mo"])
    parser.add_argument("--out", help="additional target folder for the "
                        "finished package; with 'relnotes': target file for "
                        "the text")
    parser.add_argument("--write", action="store_true",
                        help="with 'licenses': write THIRD_PARTY_LICENSES.md")
    parser.add_argument("--version",
                        help="with 'relnotes': version instead of the one in "
                        "manifest.ini")
    args = parser.parse_args()

    if args.command == "py311":
        cmd_check_min_python()
        return
    if args.command == "i18n":
        cmd_check_translations()
        return
    if args.command == "pot":
        cmd_pot()
        return
    if args.command == "mo":
        cmd_mo()
        return
    if args.command == "licenses":
        cmd_licenses(write=args.write)
        return
    if args.command == "relnotes":
        cmd_relnotes(version=args.version, out_path=args.out)
        return
    if args.command in ("libs", "all"):
        cmd_libs()
    if args.command in ("pack", "all"):
        cmd_pack(args.out)


if __name__ == "__main__":
    main()
