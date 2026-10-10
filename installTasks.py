# -*- coding: utf-8 -*-
"""Smart Home Control - what NVDA runs when the add-on is removed.

The credentials live in a file of their own next to the add-on folder
(credential_store.py), so an update does not take them along. Without this
module that also meant they outlived an uninstall: passwords and tokens
encrypted, but there, and the email addresses in plain text up to 26.10.0.

Removing the add-on therefore removes that file and the salt of the AES
fallback. The history, the favourites and the energy samples stay - they
are the user's own records, not keys to an account; the manual names where
they are.

NVDA runs onUninstall during an UPDATE as well: the store marks the old
version for removal and unpacks the new one into "<name>.pendingInstall";
at the next start the old one is removed - its onUninstall included - before
the new one is moved into place (read from NVDA 2026.2's addonHandler and
addonStore.install). Deleting the credentials then would make every update
cost the sign-in to five platforms and a new Netatmo authorisation in the
browser. So nothing is deleted while a new version of this add-on waits.
"""

import glob
import os

ADDON_NAME = "SmartHomeControl"


def _addons_dir():
    """The folder NVDA keeps its add-ons in (this file's grandparent)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _update_pending():
    """True when a new version of this add-on waits to replace this one."""
    if os.path.isdir(os.path.join(_addons_dir(), ADDON_NAME + ".pendingInstall")):
        return True
    try:
        import addonHandler
        pending = addonHandler.state[addonHandler.AddonStateCategory.PENDING_INSTALL]
        return ADDON_NAME in pending
    except Exception:
        # Unknown: keep the data. Losing it on an update is the worse mistake.
        return True


def _log(message):
    try:
        from logHandler import log
        log.info(f"Smart Home Control uninstall: {message}")
    except Exception:
        pass


def onUninstall():
    if _update_pending():
        _log("an update follows - the stored credentials stay")
        return
    addons = _addons_dir()
    paths = [os.path.join(addons, ADDON_NAME + "_credentials.json")]
    # Temporary files of an interrupted write (credential_store._write_file).
    paths += glob.glob(os.path.join(addons, ADDON_NAME + "_credentials.json.*.tmp"))
    try:
        import globalVars
        paths.append(os.path.join(globalVars.appArgs.configPath,
                                  "smarthomecontrol_salt.bin"))
    except Exception:
        pass
    paths.append(os.path.join(os.path.expanduser("~"), ".smarthomecontrol_salt.bin"))
    for path in paths:
        try:
            os.remove(path)
            _log(f"removed {os.path.basename(path)}")
        except FileNotFoundError:
            pass
        except Exception as e:
            _log(f"could not remove {os.path.basename(path)}: {e}")
