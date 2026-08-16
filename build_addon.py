# -*- coding: utf-8 -*-
"""
Smart Home Control – Build-Skript

Erzeugt reproduzierbar das Lib-Bundle und das fertige .nvda-addon-Paket.
Läuft mit jedem normalen Python 3.9+ (getestet unter Windows), braucht nur
pip im PATH und Internet für das "libs"-Kommando.

Verwendung (in diesem Ordner ausführen):
    python build_addon.py pack          Paket bauen -> dist/SmartHomeControl-<version>.nvda-addon
    python build_addon.py libs         lib/ und lib/_arch/ aus requirements-bundle.txt neu erzeugen
    python build_addon.py all          libs + pack
    python build_addon.py pack --out "C:\\Users\\hasel_bg\\SynologyDrive\\NVDA-Addons"

Ins Paket kommen NUR: manifest.ini, globalPlugins/, lib/
Ausgeschlossen werden: __pycache__, *.pyc/*.pyo, *.log, _old_*-Ordner,
Spikes, Upstream-Quellpakete, .git*, .claude, Berichte, dieses Skript.
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

# Architektur-Ziele: (Ordnername, pip-Platform, Python-Version)
# NVDA 2025.x = 32-Bit Python 3.11; NVDA 2026.1+ = 64-Bit Python 3.13.
ARCH_TARGETS = [
    ("cp311-win32", "win32", "3.11"),
    ("cp313-amd64", "win_amd64", "3.13"),
]

# Nur diese Top-Level-Einträge gehören ins Paket.
# doc/, locale/ und LICENSE sind optional (übersprungen, falls nicht vorhanden).
INCLUDE_TOP = ("manifest.ini", "globalPlugins", "lib", "doc", "locale", "LICENSE")
OPTIONAL_TOP = {"doc", "locale", "LICENSE"}

# "SelfTest" ist die Testsuite von pycryptodomex: 196 Dateien, 2,8 MB
# entpackt, die zur Laufzeit nie importiert werden (geprüft: kein Treffer im
# Add-on-Code und in meross_iot). Dasselbe gilt fuer aiohttps test_utils.
EXCLUDE_DIR_NAMES = {"__pycache__", "SelfTest"}
EXCLUDE_DIR_GLOBS = ["_old_*"]
EXCLUDE_FILE_GLOBS = ["*.pyc", "*.pyo", "*.log", "*.tmp", "*.bak",
                      "test_utils.py"]


def read_requirements():
    """Liest requirements-bundle.txt und trennt [pure]- und [arch]-Pakete."""
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
                raise SystemExit(f"Zeile außerhalb einer Sektion: {line}")
            current.append(line)
    return pure, arch


def pip_download(dest, packages, platform=None, python_version=None):
    """Lädt Wheels für die angegebenen Pakete (ohne Abhängigkeiten –
    die Requirements-Datei pinnt bewusst ALLE benötigten Pakete explizit)."""
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
                f"pip download fehlgeschlagen für {pkg}:\n{result.stderr[-800:]}")


def extract_wheels(wheel_dir, target_dir):
    """Entpackt alle Wheels eines Ordners in das Zielverzeichnis."""
    os.makedirs(target_dir, exist_ok=True)
    for name in sorted(os.listdir(wheel_dir)):
        if name.endswith(".whl"):
            with zipfile.ZipFile(os.path.join(wheel_dir, name)) as z:
                z.extractall(target_dir)
            print(f"  entpackt: {name}")


def cmd_libs():
    """Erzeugt lib/ und lib/_arch/ frisch aus requirements-bundle.txt."""
    pure, arch = read_requirements()
    print(f"[libs] {len(pure)} pure + {len(arch)} arch Pakete")

    with tempfile.TemporaryDirectory() as tmp:
        # 1. Pure-Python-Pakete -> lib/
        pure_dl = os.path.join(tmp, "pure")
        pip_download(pure_dl, pure)

        # 2. Arch-Pakete je Ziel
        arch_dls = {}
        for arch_name, plat, pyver in ARCH_TARGETS:
            d = os.path.join(tmp, arch_name)
            pip_download(d, arch, platform=plat, python_version=pyver)
            arch_dls[arch_name] = d

        # 3. Erst NACH erfolgreichem Download die alten Ordner ersetzen
        #    (ein fehlgeschlagener Download lässt das Bundle unangetastet).
        staging = os.path.join(tmp, "staging_lib")
        extract_wheels(pure_dl, staging)
        for arch_name, _, _ in ARCH_TARGETS:
            extract_wheels(arch_dls[arch_name],
                           os.path.join(staging, "_arch", arch_name))

        # README im _arch-Ordner erhalten, falls vorhanden
        old_readme = os.path.join(LIB_DIR, "_arch", "README.md")
        if os.path.isfile(old_readme):
            shutil.copy2(old_readme, os.path.join(staging, "_arch", "README.md"))

        backup = LIB_DIR + ".old"
        if os.path.isdir(backup):
            shutil.rmtree(backup)
        if os.path.isdir(LIB_DIR):
            os.replace(LIB_DIR, backup)
        shutil.move(staging, LIB_DIR)
        print(f"[libs] fertig. Vorherige Version liegt in {backup} "
              f"(nach Funktionstest löschen).")


def read_version():
    """Liest die Versionsnummer aus manifest.ini."""
    with open(os.path.join(ROOT, "manifest.ini"), encoding="utf-8") as f:
        content = f.read()
    m = re.search(r'^version\s*=\s*"?([^"\r\n]+)"?', content, re.MULTILINE)
    if not m:
        raise SystemExit("Version nicht in manifest.ini gefunden")
    return m.group(1).strip()


def cmd_relnotes(version=None, out_path=None):
    """Schneidet den Abschnitt einer Version aus CHANGELOG.md heraus.

    Die GitHub-Releases tragen den Changelog der jeweiligen Version. Es gibt
    genau eine Changelog-Datei, und zwar auf Englisch - so halten es auch die
    offizielle Add-on-Vorlage und die groesseren NVDA-Add-ons; Release-Seiten
    werden international gelesen.

    Ohne --version wird die Version aus manifest.ini genommen; im Workflow
    kommt sie aus dem Tag. Fehlt der Abschnitt, ist das ein Fehler und kein
    stiller Rückfall auf einen Platzhaltertext: ein Release ohne Changelog
    fiele erst auf, wenn es veröffentlicht ist.
    """
    version = version or read_version()
    path = os.path.join(ROOT, "CHANGELOG.md")
    if not os.path.exists(path):
        raise SystemExit(f"[relnotes] {path} fehlt")
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    # Abschnitt = von "## <version>" bis zur nächsten "## "-Überschrift.
    start = None
    for i, line in enumerate(lines):
        if re.match(r'^##\s+' + re.escape(version) + r'(\s|$)', line):
            start = i
            break
    if start is None:
        raise SystemExit(
            f"[relnotes] Kein Abschnitt '## {version}' in CHANGELOG.md - "
            f"vor dem Taggen den Changelog-Eintrag ergaenzen")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break

    body = "\n".join(lines[start + 1:end]).strip()
    if not body:
        raise SystemExit(f"[relnotes] Abschnitt '## {version}' ist leer")

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
        print(f"[relnotes] {version} -> {out_path} ({len(body.splitlines())} Zeilen)")
    else:
        # Die Konsole ist unter Windows oft cp1252; der Changelog enthält
        # aber Zeichen wie CO₂. Ohne Umstellung bricht die Ausgabe hier ab.
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
    """Hält die Version im Fenstertitel der Hilfe-Dateien aktuell.

    NVDA zeigt beim Öffnen der Hilfe den <title> im Browserfenster an –
    Konvention der Community-Add-ons ist "Name Version" (z.B.
    "Smart Home Control 26.07.1"). Wird bei jedem pack aus manifest.ini
    synchronisiert, damit die Version nie veraltet.
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
            print(f"[pack] Titel aktualisiert: doc/{lang_dir}/readme.html -> {version}")


def cmd_check_min_python(strict=True):
    """Prüft den Code gegen die Python-Version der ältesten NVDA-Fassung.

    minimumNVDAVersion 2025.1 bedeutet Python 3.11. Dort ist es ein
    SyntaxError, wenn ein Ausdruck IM f-String dasselbe Anführungszeichen
    benutzt wie der f-String selbst - erst 3.12 erlaubt das (PEP 701).

    Genau daran ist das Add-on schon einmal beim Nutzer gescheitert, während
    es auf dem Entwicklungsrechner (Python 3.12) fehlerfrei übersetzte:

        f"{device.name}: {_("Error")}. "   ->  auf 3.11 SyntaxError

    Ein `python -m compileall` mit einer neueren Version findet das NICHT,
    deshalb diese eigene Prüfung. Sie läuft bei `pack` mit, damit ein Paket
    gar nicht erst entsteht, das auf der Mindestversion nicht startet.
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
                        f"{os.path.basename(path)}:{node.lineno} - Ausdruck im "
                        f"f-String nutzt {quote} wie der f-String selbst "
                        f"(auf Python 3.11 ein SyntaxError): {seg[:70]}")
                    break
                # Zweite Falle derselben Art: Backslashes im Ausdruck erlaubt
                # ebenfalls erst 3.12.
                if "\\" in expr:
                    problems.append(
                        f"{os.path.basename(path)}:{node.lineno} - Backslash im "
                        f"f-String-Ausdruck (auf Python 3.11 ein SyntaxError): "
                        f"{seg[:70]}")
                    break

    if problems:
        print("[py311] " + "\n[py311] ".join(problems))
        if strict:
            raise SystemExit(
                "Code ist auf der Mindest-NVDA-Version nicht lauffähig - "
                "im f-String das andere Anführungszeichen verwenden")
        return False
    print("[py311] OK - keine verschachtelten Anführungszeichen in f-Strings")
    return True


def _translatable_literals():
    """Alle `_("...")`-Literale im Quellcode, mit Fundstelle.

    Rein statisch über den AST - kein xgettext nötig, das unter Windows
    ohnehin selten installiert ist. Erfasst werden nur Aufrufe mit einem
    String-Literal als erstem Argument; f-Strings und Variablen sind
    ohnehin nicht übersetzbar.
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
    """Wie `_translatable_literals`, zusätzlich mit dem Translators-Kommentar.

    Der Kommentar über dem `_()`-Aufruf ist für Übersetzer oft wichtiger als
    der Text selbst: er sagt, ob "Off" ein Gerätezustand, ein Menüeintrag
    oder eine Schaltfläche ist. Er wird ab der Zeile über dem Aufruf nach
    oben eingesammelt, solange dort Kommentarzeilen stehen.
    """
    import ast
    eintraege = {}
    pattern = os.path.join(ROOT, "globalPlugins", "SmartHomeControl", "*.py")
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as f:
            quelle = f.read()
        zeilen = quelle.splitlines()
        tree = ast.parse(quelle, path)
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
                zeile = zeilen[i].strip()
                if zeile.startswith("#"):
                    block.insert(0, zeile.lstrip("# ").rstrip())
                else:
                    break
            kommentar = " ".join(block)
            # Ab dem Marker schneiden, nicht auf ihn prüfen: über der
            # Translators-Zeile steht oft noch ein normaler Codekommentar,
            # und ein "startswith" würde den ganzen Hinweis verwerfen.
            marke = kommentar.find("Translators")
            if marke > 0:
                kommentar = kommentar[marke:]
            eintrag = eintraege.setdefault(
                arg.value, {"occurrences": [], "comment": ""})
            eintrag["occurrences"].append(
                (os.path.basename(path), str(node.lineno)))
            if kommentar.startswith("Translators") and not eintrag["comment"]:
                eintrag["comment"] = kommentar
    return eintraege


def cmd_pot():
    """Schreibt locale/SmartHomeControl.pot neu aus dem Quellcode.

    Ohne dieses Kommando veraltet die Vorlage still bei jeder Code-Änderung -
    Übersetzer bekämen dann eine Datei, in der neue Texte fehlen. Vorhandene
    Übersetzungen werden NICHT angefasst; sie holen sich die Neuerungen über
    "Aus POT-Datei aktualisieren" (Poedit) bzw. msgmerge.
    """
    import polib
    from datetime import datetime, timezone
    eintraege = _translatable_entries()
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
    # Vorhandene Hinweise nicht verlieren: manche stehen als gewöhnlicher
    # Codekommentar da ("Device filter") und tragen trotzdem Bedeutung für
    # Übersetzer. Was der Quellcode hergibt, hat Vorrang; was er nicht
    # hergibt, wird aus der bisherigen Vorlage übernommen.
    bisher = {}
    if os.path.isfile(pot_path):
        bisher = {e.msgid: e.comment for e in polib.pofile(pot_path)
                  if e.msgid and e.comment}
    uebernommen = 0
    for msgid, daten in eintraege.items():
        kommentar = daten["comment"] or bisher.get(msgid, "")
        if not daten["comment"] and kommentar:
            uebernommen += 1
        pot.append(polib.POEntry(
            msgid=msgid, msgstr="",
            comment=kommentar,
            occurrences=daten["occurrences"]))
    pot.sort(key=lambda e: e.msgid)
    pot.save(pot_path)
    mit = sum(1 for e in pot if e.comment)
    print(f"[pot] {len(pot)} Texte geschrieben, {mit} mit Hinweis "
          f"({uebernommen} aus der bisherigen Vorlage übernommen) -> {pot_path}")
    return pot_path


def cmd_mo():
    """Kompiliert jede .po unter locale/ zu einer .mo neben ihr.

    NVDA lädt ausschließlich die .mo. Wer eine Übersetzung nur als .po
    schickt - etwa aus einem Weboberflächen-Werkzeug - hätte sonst eine
    Übersetzung, die nirgends wirkt.
    """
    import polib
    muster = os.path.join(ROOT, "locale", "*", "LC_MESSAGES", "nvda.po")
    gefunden = sorted(glob.glob(muster))
    if not gefunden:
        print("[mo] keine .po-Dateien unter locale/ gefunden")
        return
    for po_path in gefunden:
        po = polib.pofile(po_path)
        mo_path = po_path[:-3] + ".mo"
        po.save_as_mofile(mo_path)
        sprache = os.path.basename(os.path.dirname(os.path.dirname(po_path)))
        fehlend = sum(1 for e in po if not e.msgstr)
        print(f"[mo] {sprache}: {len(po)} Texte, {fehlend} unübersetzt "
              f"-> {os.path.basename(mo_path)}")


def cmd_check_translations(strict=True):
    """Prüft alle Übersetzungen unter locale/ gegen den Quellcode.

    Quellsprache ist Englisch: die `_()`-Texte im Code SIND die msgids, jede
    Sprache unter locale/ ist eine Übersetzung davon. Damit sehen Nutzer ohne
    passende Übersetzung Englisch und nicht die Sprache des Autors.

    Vier Fehlerbilder, die alle schon vorgekommen sind:

    1. Ein `_()`-String fehlt in einer .po -> dort erscheint Englisch,
       obwohl die Oberfläche in der jeweiligen Sprache läuft.
    2. Die .mo ist älter als die .po -> NVDA lädt die .mo, die neuen
       Übersetzungen wirken nicht.
    3. Nicht übersetzte oder fuzzy Einträge.
    4. Die .pot passt nicht mehr zum Code - Übersetzer bekämen eine
       veraltete Vorlage.

    Läuft automatisch bei `pack`.
    """
    try:
        import polib
    except ImportError:
        print("[i18n] polib nicht installiert - Prüfung übersprungen "
              "(pip install polib)")
        return True

    locale_dir = os.path.join(ROOT, "locale")
    literals = _translatable_literals()
    problems = []

    # Die .pot ist die Vorlage für Übersetzer und muss den Code abbilden.
    pot_path = os.path.join(locale_dir, "SmartHomeControl.pot")
    if not os.path.isfile(pot_path):
        problems.append(f"{pot_path} fehlt - Übersetzer haben keine Vorlage")
    else:
        pot_ids = {e.msgid for e in polib.pofile(pot_path) if e.msgid}
        pot_missing = sorted(set(literals) - pot_ids)
        if pot_missing:
            problems.append(
                f"{len(pot_missing)} Strings im Code fehlen in der .pot "
                f"(z.B. {literals[pot_missing[0]]}) - .pot neu erzeugen")

    langs = sorted(d for d in os.listdir(locale_dir)
                   if os.path.isdir(os.path.join(locale_dir, d)))
    if not langs:
        print("[i18n] keine Übersetzungen vorhanden (Quellsprache Englisch)")
        return True

    counts = []
    for lang in langs:
        po_path = os.path.join(locale_dir, lang, "LC_MESSAGES", "nvda.po")
        mo_path = os.path.join(locale_dir, lang, "LC_MESSAGES", "nvda.mo")
        if not os.path.isfile(po_path):
            problems.append(f"{lang}: nvda.po fehlt")
            continue
        if not os.path.isfile(mo_path):
            problems.append(f"{lang}: nvda.mo fehlt - NVDA lädt zur Laufzeit "
                            f"die .mo, nicht die .po!")
            continue

        po = polib.pofile(po_path)
        mo = polib.mofile(mo_path)
        po_ids = {e.msgid for e in po}

        missing = sorted(set(literals) - po_ids)
        if missing:
            problems.append(f"{lang}: {len(missing)} Strings im Code fehlen "
                            f"in der .po:")
            for msgid in missing[:15]:
                problems.append(f"    {literals[msgid]:<28} {msgid[:60]!r}")
            if len(missing) > 15:
                problems.append(f"    ... und {len(missing) - 15} weitere")

        drift = po_ids ^ {e.msgid for e in mo}
        if drift:
            problems.append(f"{lang}: .mo weicht von der .po ab "
                            f"({len(drift)} Einträge) - .mo neu kompilieren")
        if po.untranslated_entries():
            problems.append(f"{lang}: {len(po.untranslated_entries())} "
                            f"unübersetzte Einträge")
        if po.fuzzy_entries():
            problems.append(f"{lang}: {len(po.fuzzy_entries())} fuzzy Einträge")
        counts.append(f"{lang}={len(po)}")

    if problems:
        print("[i18n] " + "\n[i18n] ".join(problems))
        if strict:
            raise SystemExit("Übersetzungsprüfung fehlgeschlagen")
        return False

    print(f"[i18n] OK - {len(literals)} Strings im Code (englisch), "
          f"Übersetzungen: {', '.join(counts)}")
    return True


def cmd_licenses(write=False):
    """Liest die Lizenzen der gebündelten Pakete aus deren METADATA.

    Hintergrund: das Add-on steht unter GPL-2.0-or-later, und mindestens ein
    gebündeltes Paket (paho-mqtt) ist dual lizenziert, wobei nur die eine
    Hälfte GPL-verträglich ist. Diese Übersicht von Hand zu pflegen geht
    schief, sobald eine Version wechselt - deshalb generiert.
    """
    rows = {}
    for meta in glob.glob(os.path.join(ROOT, "lib", "**", "*.dist-info",
                                       "METADATA"), recursive=True):
        name = version = license_name = ""
        classifiers = []
        with open(meta, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    break  # Ende des Headers
                if line.startswith("Name: "):
                    name = line[6:].strip()
                elif line.startswith("Version: "):
                    version = line[9:].strip()
                elif line.startswith("License-Expression: "):
                    license_name = line[20:].strip()
                elif line.startswith("License: ") and not license_name:
                    value = line[9:].strip()
                    if len(value) < 60:  # lange Werte sind der volle Lizenztext
                        license_name = value
                elif line.startswith("Classifier: License ::"):
                    classifiers.append(line.split("::")[-1].strip())
        if not license_name and classifiers:
            license_name = " / ".join(dict.fromkeys(classifiers))
        if name:
            rows[name.lower()] = (name, version, license_name or "?")

    lines = ["| Paket | Version | Lizenz |", "|---|---|---|"]
    for key in sorted(rows):
        name, version, license_name = rows[key]
        lines.append(f"| `{name}` | {version} | {license_name} |")
    table = "\n".join(lines)
    print(table)
    print(f"\n[licenses] {len(rows)} Pakete")
    if write:
        out = os.path.join(ROOT, "THIRD_PARTY_LICENSES.md")
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write("# Gebündelte Fremdkomponenten\n\n"
                    "Erzeugt mit `python build_addon.py licenses --write` aus "
                    "den `*.dist-info/METADATA`-Feldern der gebündelten "
                    "Pakete.\n\n" + table + "\n")
        print(f"[licenses] geschrieben: {out}")
    return rows


def cmd_pack(out_dir=None):
    """Baut das .nvda-addon-Paket."""
    version = read_version()
    # Zuerst die Lauffaehigkeit auf der Mindestversion - ein Paket, das dort
    # nicht startet, braucht niemand.
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
                raise SystemExit(f"Pflicht-Eintrag fehlt: {top}")
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if not _dir_excluded(d)]
                for fname in files:
                    if _file_excluded(fname):
                        continue
                    full = os.path.join(root, fname)
                    z.write(full, os.path.relpath(full, ROOT))
                    count += 1

    size_mb = os.path.getsize(out_path) / 1024 / 1024
    print(f"[pack] {out_path}  ({count} Dateien, {size_mb:.1f} MB)")

    # Integritätsprüfung
    with zipfile.ZipFile(out_path) as z:
        bad = z.testzip()
        names = z.namelist()
    assert bad is None, f"Defekte Datei im ZIP: {bad}"
    assert "manifest.ini" in names, "manifest.ini fehlt im Paket!"
    forbidden = [n for n in names if "__pycache__" in n or "_old_" in n
                 or n.endswith((".pyc", ".log"))]
    assert not forbidden, f"Verbotene Dateien im Paket: {forbidden[:5]}"
    # Die .mo ist die Datei, die NVDA zur Laufzeit lädt. Fehlt sie, ist das
    # Add-on still einsprachig - ohne Fehlermeldung. Genau das kann passieren,
    # wenn eine .gitignore-Regel `*.mo` ausschließt und das Paket aus einem
    # frischen Klon gebaut wird.
    mo_files = [n for n in names if n.endswith(".mo")]
    assert mo_files, ("Keine .mo im Paket - die Übersetzungen fehlen! "
                      "(Prüfe .gitignore auf eine *.mo-Regel)")
    print(f"[pack] Integrität OK ({len(mo_files)} Übersetzungsdatei(en))")

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        dest = os.path.join(out_dir, os.path.basename(out_path))
        shutil.copy2(out_path, dest)
        print(f"[pack] kopiert nach: {dest}")


def main():
    parser = argparse.ArgumentParser(description="Smart Home Control Build")
    parser.add_argument("command",
                        choices=["libs", "pack", "all", "i18n", "licenses",
                                 "relnotes", "py311", "pot", "mo"])
    parser.add_argument("--out", help="Zusätzlicher Zielordner für das fertige Paket "
                        "(z.B. C:\\Users\\hasel_bg\\SynologyDrive\\NVDA-Addons); "
                        "bei 'relnotes': Zieldatei für den Text")
    parser.add_argument("--write", action="store_true",
                        help="bei 'licenses': THIRD_PARTY_LICENSES.md schreiben")
    parser.add_argument("--version",
                        help="bei 'relnotes': Version statt der aus manifest.ini")
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
