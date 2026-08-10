# -*- coding: utf-8 -*-
"""
Secure storage of credentials using Windows DPAPI.

Windows DPAPI (Data Protection API) encrypts data bound to the
current Windows user. Only the same user on the same machine can
decrypt the data again.

Fallback: AES-256-GCM with a per-installation random salt
(PBKDF2-HMAC-SHA256, 200k iterations) when DPAPI is not available.

Format versions:
  - DPAPI:<b64>           : Windows DPAPI (preferred)
  - AES:<b64>             : legacy AES format (machine identity as KDF input)
  - AESV2:<b64>           : current AES format with per-installation salt + AAD
  - B64:<b64>             : legacy plain base64 (migrated on next save)
"""

import ctypes
import ctypes.wintypes
import base64
import hashlib
import os
import getpass
import platform
from logHandler import log

try:
    import addonHandler
    addonHandler.initTranslation()
except Exception:
    pass
if "_" not in globals():  # fallback outside of NVDA (e.g. tests)
    def _(s):
        return s

# AES fallback via PyCryptodome (bundled in the lib/ folder)
try:
    from Cryptodome.Cipher import AES
    _HAS_AES = True
except ImportError:
    _HAS_AES = False
    log.warning("PyCryptodome nicht verfügbar – AES-Fallback deaktiviert")


# ============================================================
# Per-installation random salt for the AES fallback (AESV2).
# Created once and stored in a file in the NVDA config path so the
# derived key is unique per installation and cannot be reproduced
# from publicly readable machine data (user/host).
# ============================================================
_AES_AAD = b"SmartHomeControl_v2_GCM"  # associated data for GCM authenticity
_SALT_FILE_NAME = "smarthomecontrol_salt.bin"


def _salt_path_candidates():
    """Candidate paths for the salt file, in priority order.

    1. NVDA config path (preferred)
    2. user home (fallback if the config path is not writable)

    Reading AND writing use the same list: a salt written to the
    fallback earlier is found again on the next start.
    """
    paths = []
    try:
        import globalVars  # type: ignore
        cfg_dir = getattr(globalVars.appArgs, 'configPath', None)
        if cfg_dir:
            paths.append(os.path.join(cfg_dir, _SALT_FILE_NAME))
    except Exception:
        log.debug("globalVars.appArgs.configPath nicht verfügbar")
    paths.append(os.path.join(os.path.expanduser("~"), "." + _SALT_FILE_NAME))
    return paths


def _restrict_file_acl(path):
    """Restrict the Windows ACL to the current user – best effort."""
    try:
        import subprocess
        # CREATE_NO_WINDOW: ohne dieses Flag blitzt unter Windows kurz ein
        # Konsolenfenster auf und zieht den Fokus - für Screenreader-Nutzer
        # besonders störend, weil NVDA dann den Fenstertitel ansagt. Das Flag
        # gibt es nur unter Windows, deshalb getattr mit Default 0.
        no_window = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        subprocess.run(
            ['icacls', path, '/inheritance:r', '/grant:r',
             f'{getpass.getuser()}:F'],
            check=False, capture_output=True, timeout=5,
            creationflags=no_window,
        )
    except Exception as e:
        log.debug(f"ACL-Einschränkung für Salt-Datei fehlgeschlagen: {e}")


def _load_or_create_salt():
    """Loads the per-installation salt or creates it on first use.

    If the salt cannot be persisted ANYWHERE, AESV2-encrypted
    credentials cannot be decrypted after an NVDA restart (a new
    random salt would be created on every start). This is therefore
    logged loudly as an ERROR.
    """
    candidates = _salt_path_candidates()

    # 1. Look for an existing salt (check all candidates)
    for path in candidates:
        try:
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    salt = f.read()
                if len(salt) >= 16:
                    return salt
                log.warning(f"Salt-Datei zu kurz, wird neu erzeugt: {path}")
        except Exception as e:
            log.warning(f"Salt-Datei konnte nicht gelesen werden: {e}")

    # 2. Create a new one and store it at the first writable location
    salt = os.urandom(32)
    for path in candidates:
        try:
            with open(path, "wb") as f:
                f.write(salt)
            _restrict_file_acl(path)
            return salt
        except Exception as e:
            log.warning(f"Salt-Datei konnte nicht nach {path} geschrieben werden: {e}")

    log.error(
        "Salt konnte an keinem Ort gespeichert werden! AES-verschlüsselte "
        "Credentials (AESV2-Fallback ohne DPAPI) überleben damit KEINEN "
        "NVDA-Neustart und müssen dann neu eingegeben werden."
    )
    return salt


# Load the salt lazily once (module level) so that not every encryption opens a
# file. If reading fails, _CACHED_SALT stays None and the AES fallback raises a
# clear error.
_CACHED_SALT = None


def _get_salt():
    global _CACHED_SALT
    if _CACHED_SALT is None:
        _CACHED_SALT = _load_or_create_salt()
    return _CACHED_SALT


def _derive_machine_key_v2():
    """Per-installation 256-bit AES key.

    Identity = `user@host@SYSTEMROOT` (uncritical, since the actual
    secret anchor is the per-installation random salt – see
    `_load_or_create_salt`).
    """
    default_sysroot = 'C:\\Windows'
    identity = f"{getpass.getuser()}@{platform.node()}@{os.environ.get('SYSTEMROOT', default_sysroot)}"
    salt = _get_salt()
    return hashlib.pbkdf2_hmac('sha256', identity.encode('utf-8'), salt, iterations=200_000)


def _derive_machine_key_legacy():
    """Legacy key derivation (for decrypting old AES: values).

    No longer used for new encryption.
    """
    default_sysroot = 'C:\\Windows'
    identity = f"{getpass.getuser()}@{platform.node()}@{os.environ.get('SYSTEMROOT', default_sysroot)}"
    salt = b"SmartHomeControl_AES_v1"
    return hashlib.pbkdf2_hmac('sha256', identity.encode('utf-8'), salt, iterations=200_000)


def _encrypt_aes_v2(plaintext):
    """Encrypts with AES-256-GCM + per-installation salt + AAD.

    Format: AESV2:<base64(nonce + tag + ciphertext)>
    """
    if not _HAS_AES:
        raise RuntimeError(_("AES-Verschlüsselung nicht verfügbar (PyCryptodome fehlt)"))
    key = _derive_machine_key_v2()
    nonce = os.urandom(12)  # 96-bit nonce for GCM
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    cipher.update(_AES_AAD)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode('utf-8'))
    payload = nonce + tag + ciphertext
    return "AESV2:" + base64.b64encode(payload).decode('ascii')


def _decrypt_aes_v2(encrypted_b64):
    """Decrypts the AESV2 format (per-installation salt, AAD)."""
    if not _HAS_AES:
        raise RuntimeError(_("AES-Entschlüsselung nicht verfügbar (PyCryptodome fehlt)"))
    raw = base64.b64decode(encrypted_b64)
    nonce = raw[:12]
    tag = raw[12:28]
    ciphertext = raw[28:]
    key = _derive_machine_key_v2()
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    cipher.update(_AES_AAD)
    return cipher.decrypt_and_verify(ciphertext, tag).decode('utf-8')


def _decrypt_aes_legacy(encrypted_b64):
    """Decrypts the legacy AES: format (no AAD, constant salt)."""
    if not _HAS_AES:
        raise RuntimeError(_("AES-Entschlüsselung nicht verfügbar (PyCryptodome fehlt)"))
    raw = base64.b64decode(encrypted_b64)
    nonce = raw[:12]
    tag = raw[12:28]
    ciphertext = raw[28:]
    key = _derive_machine_key_legacy()
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    return cipher.decrypt_and_verify(ciphertext, tag).decode('utf-8')


# ============================================================
# Windows DPAPI
# ============================================================
class _DATA_BLOB(ctypes.Structure):
    """Windows DATA_BLOB structure for CryptProtectData/CryptUnprotectData"""
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte))
    ]


def encrypt_dpapi(plaintext):
    """Encrypts a string with Windows DPAPI (bound to the Windows user).

    Fallback chain:
      1. DPAPI (preferred, OS-bound)
      2. AES-256-GCM with per-installation salt + AAD (AESV2)
      3. raise an error
    """
    if not plaintext:
        return ""

    try:
        data = plaintext.encode('utf-8')

        input_blob = _DATA_BLOB()
        input_blob.cbData = len(data)
        input_blob.pbData = (ctypes.c_byte * len(data))(*data)
        output_blob = _DATA_BLOB()

        # CryptProtectData - encrypts bound to the current Windows user
        success = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(input_blob),  # pDataIn
            None,                       # szDataDescr (optional)
            None,                       # pOptionalEntropy
            None,                       # pvReserved
            None,                       # pPromptStruct
            0,                          # dwFlags
            ctypes.byref(output_blob)   # pDataOut
        )

        if not success:
            raise ctypes.WinError()

        # Read out the encrypted bytes
        encrypted = bytes(ctypes.cast(
            output_blob.pbData,
            ctypes.POINTER(ctypes.c_byte * output_blob.cbData)
        ).contents)

        # Free the Windows-allocated memory
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)

        # Return as base64 with prefix
        return "DPAPI:" + base64.b64encode(encrypted).decode('ascii')

    except Exception as e:
        # No stack traces – the error repr may contain sensitive data
        log.warning(f"DPAPI Verschlüsselung fehlgeschlagen, verwende AES-Fallback: {type(e).__name__}")
        return _encrypt_aes_v2(plaintext)


def decrypt_dpapi(encrypted):
    """Decrypts an encrypted string.

    Detects the format automatically:
      - "DPAPI:..."  → Windows DPAPI decryption
      - "AESV2:..."  → AES-256-GCM with per-installation salt + AAD (current)
      - "AES:..."    → legacy AES-256-GCM (migrated on next save)
      - "B64:..."    → legacy base64 format (migrated on next save)
      - anything else → legacy plaintext/base64 heuristic (migration of old versions)
    """
    if not encrypted:
        return ""

    try:
        if encrypted.startswith("DPAPI:"):
            encrypted_bytes = base64.b64decode(encrypted[6:])

            input_blob = _DATA_BLOB()
            input_blob.cbData = len(encrypted_bytes)
            input_blob.pbData = (ctypes.c_byte * len(encrypted_bytes))(*encrypted_bytes)
            output_blob = _DATA_BLOB()

            success = ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(input_blob),
                None, None, None, None, 0,
                ctypes.byref(output_blob)
            )

            if not success:
                raise ctypes.WinError()

            decrypted = bytes(ctypes.cast(
                output_blob.pbData,
                ctypes.POINTER(ctypes.c_byte * output_blob.cbData)
            ).contents)

            ctypes.windll.kernel32.LocalFree(output_blob.pbData)

            return decrypted.decode('utf-8')

        elif encrypted.startswith("AESV2:"):
            return _decrypt_aes_v2(encrypted[6:])

        elif encrypted.startswith("AES:"):
            log.warning("Legacy AES-Credentials gelesen – werden beim nächsten Speichern auf AESV2/DPAPI migriert")
            return _decrypt_aes_legacy(encrypted[4:])

        elif encrypted.startswith("B64:"):
            log.warning("Legacy B64-Credentials gelesen – werden beim nächsten Speichern verschlüsselt")
            return base64.b64decode(encrypted[4:]).decode('utf-8')

        else:
            # Kein bekanntes Prefix: Wert stammt aus einer Vor-Release-Version.
            # Die frühere Heuristik ("sieht wie Base64 aus -> dekodieren") ist
            # abgekündigt: ein echtes Klartext-Passwort, das zufällig gültiges
            # Base64 ist, würde still verfälscht. Der Wert wird unverändert
            # als Klartext übernommen und beim nächsten Speichern
            # verschlüsselt.
            log.warning("Credentials ohne bekanntes Format gelesen – werden beim nächsten Speichern verschlüsselt")
            return encrypted

    except Exception as e:
        # No exc_info: could leak sensitive data
        log.error(f"Entschlüsselung fehlgeschlagen: {type(e).__name__}")
        # Return an empty string instead of the ciphertext: the caller would
        # otherwise send the ciphertext as the "password" to the cloud logins
        # and get cryptic API errors instead of a clear "no credentials"
        # handling. (This branch only affects values WITH a known prefix –
        # legacy plain text is returned unchanged in the else branch above.)
        return ""


def is_encrypted(value):
    """True if the string already carries a known encryption format.

    Used by the plugin to avoid double encryption in the setters.
    Important: user plain text that happens to start with ``"DPAPI:"`` is
    no longer misinterpreted as encrypted – the setters in the plugin
    should only use this function for values coming from the config,
    not for user input from UI fields.
    """
    if not isinstance(value, str) or not value:
        return False
    for prefix in ("DPAPI:", "AESV2:", "AES:", "B64:"):
        if value.startswith(prefix):
            return True
    return False
