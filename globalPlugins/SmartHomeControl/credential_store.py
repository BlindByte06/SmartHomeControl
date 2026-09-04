# -*- coding: utf-8 -*-
"""Smart Home Control - credentials outside NVDA's configuration.

NVDA writes its complete configuration into the log when it starts. Anything
kept in ``nvda.ini`` therefore travels with every log a user sends in - and
the manual had to say so: the email address stood there in plain text, next
to the encrypted passwords and tokens.

These values live in a file of their own instead, beside the history and the
favourites. The log never sees it. What is stored does not change: passwords
and tokens stay DPAPI-encrypted exactly as before, only their location moves.

The move happens once, by itself: as long as the file does not exist, the
values are taken out of the configuration, written here, and only then
removed there. If writing fails, nothing is removed - a credential that
cannot be saved must not be lost either.
"""

import json
import os

try:
    from logHandler import log
except ImportError:  # outside of NVDA (checks, build)
    import logging
    log = logging.getLogger(__name__)

# Same derivation as history.py and favorites.py: the file lives NEXT TO the
# add-on folder, so an update or a reinstall does not take it along.
_ADDON_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_ADDONS_DIR = os.path.dirname(_ADDON_DIR)
CREDENTIALS_FILE = os.path.join(_ADDONS_DIR, "SmartHomeControl_credentials.json")

# Everything that identifies a person or opens a door. Deliberately NOT in
# here: ports, country codes, regions, the token expiry - they say nothing
# about anyone and are easier to support when they stay visible in the log.
SECRET_KEYS = (
    "email",
    "password",
    "netatmoClientId",
    "netatmoClientSecret",
    "netatmoAccessToken",
    "netatmoRefreshToken",
    "vesyncEmail",
    "vesyncPassword",
    "vesyncToken",
    "vesyncAccountId",
    "cozytouchEmail",
    "cozytouchPassword",
    "cozytouchToken",
)


def _read_file():
    """The stored values, or None when there is no file yet."""
    if not os.path.isfile(CREDENTIALS_FILE):
        return None
    try:
        with open(CREDENTIALS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            log.error("Credential store is not a JSON object - ignored")
            return None
        return {k: v for k, v in data.items() if k in SECRET_KEYS}
    except Exception as e:
        # A broken file must not cost the settings dialog: the add-on then
        # starts without credentials and says so, rather than not starting.
        log.error(f"Could not read the credential store: {e}")
        return None


def _write_file(values):
    """Writes the values; returns True only when they are really on disk."""
    try:
        temporary = CREDENTIALS_FILE + ".tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump({k: values.get(k, "") for k in SECRET_KEYS}, f,
                      indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, CREDENTIALS_FILE)
        return True
    except Exception as e:
        log.error(f"Could not write the credential store: {e}")
        return False


def load(conf):
    """Returns the credentials and moves them out of the configuration once.

    Args:
        conf: the add-on's section of NVDA's configuration.

    Returns:
        dict with every key of SECRET_KEYS, missing ones as an empty string.
    """
    stored = _read_file()
    if stored is not None:
        return {k: stored.get(k, "") for k in SECRET_KEYS}

    # No file yet: either a fresh installation - then there is nothing to
    # take over - or an update from a version that kept everything in
    # nvda.ini.
    from_conf = {}
    for key in SECRET_KEYS:
        try:
            value = conf.get(key, "")
        except Exception:
            value = ""
        from_conf[key] = value if isinstance(value, str) else ""
    if not any(from_conf.values()):
        return from_conf

    if _write_file(from_conf):
        cleared = clear_from_conf(conf)
        log.info(f"Credentials moved out of the configuration "
                 f"({cleared} entries) into {os.path.basename(CREDENTIALS_FILE)}")
    else:
        log.warning("Credentials stay in the configuration - the store could "
                    "not be written")
    return from_conf


def save(conf, values):
    """Writes the credentials and keeps the configuration free of them."""
    if not _write_file(values):
        return False
    clear_from_conf(conf)
    return True


def clear_from_conf(conf):
    """Empties the secret keys in the configuration; returns how many."""
    cleared = 0
    for key in SECRET_KEYS:
        try:
            if conf.get(key, ""):
                conf[key] = ""
                cleared += 1
        except Exception as e:
            log.debug(f"Could not clear {key} in the configuration: {e}")
    return cleared
