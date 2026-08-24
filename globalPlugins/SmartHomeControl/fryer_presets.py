# -*- coding: utf-8 -*-
"""
Smart Home Control - cooking programmes an air fryer has shown us.

To start a programme by name, the cloud wants the ``recipeId`` the
appliance uses for it. Those ids are not derivable: on a CAF-P583S they
run 1, 2, 3, 5, 6, 9, 13, 14, 15, 16, 17 - with gaps, which means the id
space belongs to the whole Cosori range rather than to one model. A table
written into the code would therefore be right for exactly one appliance
and quietly wrong for the next.

So the appliance teaches us instead. Every status reply that carries a
loaded programme names it together with its id, its set temperature and
its duration, and that is all a later ``startCook`` needs. Selecting a
programme on the appliance is enough - it does not have to be cooked.

Only language-neutral values are stored: the appliance's English ``mode``
as the key, never ``recipeName``, which arrives in the language of the
VeSync app (a German app sends "Huhn" where mode says "Chicken"). The
display name is looked up from ``mode`` when it is shown.

Persistent as JSON in the NVDA addons folder, on the same level as the
favourites and the history - that path survives an add-on update, because
NVDA only replaces the add-on subfolder.
"""

import json
import os

try:
    from logHandler import log
except ImportError:  # outside NVDA (checks)
    import logging
    log = logging.getLogger(__name__)

_ADDON_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_ADDONS_DIR = os.path.dirname(_ADDON_DIR)
PRESETS_FILE = os.path.join(_ADDONS_DIR, "SmartHomeControl_fryer_presets.json")

# Fields taken from a step. Anything else the appliance sends is ignored:
# what is not needed to start the programme again is not worth storing.
_FIELDS = ("recipe_id", "recipe_type", "cook_temp", "cook_set_time")


class FryerPresets:
    """Programmes seen per device, keyed by the device's unique id.

    ``unique_id`` and not ``uuid``: on a hub every subdevice carries the
    hub's uuid, and two appliances would share one set of programmes.
    """

    def __init__(self):
        self._devices = {}
        self._load()

    # ------------------------------------------------------------ storage --
    def _load(self):
        try:
            if os.path.exists(PRESETS_FILE):
                with open(PRESETS_FILE, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    self._devices = data
                    log.debug(f"Fryer programmes loaded for "
                              f"{len(self._devices)} device(s)")
        except Exception as e:
            # A damaged file must not stop the add-on: without programmes
            # the fryer is merely back to being display-only.
            log.error(f"Could not load the fryer programmes: {e}")
            self._devices = {}

    def _save(self):
        """Writes atomically, as the favourites do."""
        try:
            tmp = PRESETS_FILE + ".tmp"
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(self._devices, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, PRESETS_FILE)
        except Exception as e:
            log.error(f"Could not save the fryer programmes: {e}")

    # ------------------------------------------------------------ learning --
    def remember(self, device_key, mode, recipe_id, recipe_type,
                 cook_temp=None, cook_set_time=None, trust_settings=True):
        """Notes one programme. True when something actually changed.

        Called from the status path, which runs on every poll, so the
        common case has to be cheap and silent: a programme already known
        with the same values writes nothing and touches no file.

        ``trust_settings`` decides whether the temperature and the duration
        are taken over. They must only be believed while the appliance has
        just loaded the programme and nothing has been changed yet - the
        'ready' state. A one-off adjustment during a cook is reported in
        exactly the same fields, and taking that over would quietly
        redefine the programme: change Veggies to 180 degrees for ten
        minutes once, and "Veggies" would mean that from then on, including
        in the two-keystroke start.

        The id is taken over regardless. It identifies the programme and
        does not change with a setting.
        """
        if not device_key or not mode or recipe_id is None:
            return False

        known = self._devices.setdefault(device_key, {})
        previous = known.get(mode)

        if previous and not trust_settings:
            # Keep the settings that were learned when the programme was
            # loaded; only the id may be refreshed.
            cook_temp = previous.get("cook_temp")
            cook_set_time = previous.get("cook_set_time")

        entry = {
            "recipe_id": recipe_id,
            "recipe_type": recipe_type,
            "cook_temp": cook_temp,
            "cook_set_time": cook_set_time,
        }
        if previous == entry:
            return False
        # A changed id means the appliance means something else by this
        # name, which is worth saying out loud in the log.
        if previous and previous.get("recipe_id") != recipe_id:
            log.info(f"Fryer programme {mode!r} changed its id: "
                     f"{previous.get('recipe_id')} -> {recipe_id}")
        known[mode] = entry
        self._save()
        if previous is None:
            log.info(f"Fryer programme learned: {mode!r} (id {recipe_id})")
        return True

    # ------------------------------------------------------------- reading --
    def modes_for(self, device_key):
        """The programme keys known for a device, in a stable order.

        Sorted by id rather than by name: the name is translated when it
        is displayed, so sorting by it would reorder the list from one
        interface language to the next, and a list that reorders itself is
        a list nobody learns.
        """
        known = self._devices.get(device_key) or {}
        return sorted(known, key=lambda m: (known[m].get("recipe_id") or 0, m))

    def get(self, device_key, mode):
        """The stored fields of one programme, or None."""
        entry = (self._devices.get(device_key) or {}).get(mode)
        if not entry:
            return None
        return dict(entry)

    def count_for(self, device_key):
        return len(self._devices.get(device_key) or {})

    def forget_device(self, device_key):
        """Drops everything known about one device."""
        if device_key in self._devices:
            del self._devices[device_key]
            self._save()
            return True
        return False


_instance = None


def get_fryer_presets():
    """The global store (singleton)."""
    global _instance
    if _instance is None:
        _instance = FryerPresets()
    return _instance
