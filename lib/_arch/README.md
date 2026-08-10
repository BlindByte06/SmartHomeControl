# lib/_arch — architektur-spezifische Pakete (Dual-Arch-Bundle)

NVDA wechselt mit 2026.1 die Laufzeit. Kompilierte C-Extensions (`.pyd`) müssen
**exakt** zu Python-Version *und* Bitness des Interpreters passen:

| NVDA-Reihe | Bitness | Python | Ordner            |
|------------|---------|--------|-------------------|
| 2025.x     | 32-Bit  | 3.11   | `cp311-win32/`    |
| 2026.1+    | 64-Bit  | 3.13   | `cp313-amd64/`    |

Reine Python-Pakete (requests, urllib3, idna, certifi, meross_iot, paho,
attrs, aiosignal, aiohappyeyeballs, typing_extensions, …) liegen **nicht** hier,
sondern direkt in `../` und werden von beiden Architekturen geteilt.

## Auswahl zur Laufzeit
`globalPlugins/SmartHomeControl/__init__.py` → `_select_arch_dir()` hängt den
passenden Ordner anhand der Bitness an `sys.path`. Passt kein Ordner exakt zur
Python-Version, greifen für die aiohttp-Pakete die mitgelieferten
Pure-Python-Fallbacks (langsamer, aber lauffähig). Cryptodome ist `abi3`
(eine Binärdatei je Bitness, gültig für alle Python-3.x).

## Hier enthaltene Pakete (mit kompilierten Extensions)
aiohttp, multidict, yarl, frozenlist, propcache, charset_normalizer, Cryptodome

## Bewusst NICHT gebündelt (optionale aiohttp-„speedups")
aiodns/pycares, cffi, Brotli, backports.zstd — werden von meross_iot/requests
nicht benötigt. aiohttp nutzt automatisch `ThreadedResolver` statt aiodns und
verzichtet still auf br/zstd-Content-Encoding. (Verifiziert per Import-Test.)
Die alten Versionen liegen reversibel unter `../_unused_backup/`.

## Neu bauen / Versionen aktualisieren
Mit einem beliebigen Python + pip (Cross-Download, ohne Installation):

```powershell
# 64-Bit / Python 3.13  -> cp313-amd64
pip download --only-binary=:all: --platform win_amd64 --python-version 313 --abi cp313 --no-deps -d wheels_amd64 `
  aiohttp==3.13.1 multidict==6.7.0 yarl==1.22.0 frozenlist==1.8.0 propcache==0.4.1 charset-normalizer==3.4.4 pycryptodomex==3.23.0

# 32-Bit / Python 3.11  -> cp311-win32
pip download --only-binary=:all: --platform win32 --python-version 311 --abi cp311 --no-deps -d wheels_win32 `
  aiohttp==3.13.1 multidict==6.7.0 yarl==1.22.0 frozenlist==1.8.0 propcache==0.4.1 charset-normalizer==3.4.4 pycryptodomex==3.23.0
```

Anschließend jedes Wheel (= ZIP) entpacken und die Paket-Verzeichnisse
(ohne `*.dist-info`/`*.data`) in den jeweiligen Ordner kopieren.

> Wichtig: pycryptodomex wird als Paket `Cryptodome` importiert (so wie im Code
> `from Cryptodome.Cipher import AES`).
