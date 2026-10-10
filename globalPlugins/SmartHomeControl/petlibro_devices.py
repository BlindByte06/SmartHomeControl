# -*- coding: utf-8 -*-
"""Smart Home Control - Petlibro device wrappers (feeders and water fountains).

What the fields mean was measured on an Air Smart Feeder (PLAF108) and a
Dockstream 2 Cordless fountain (PLWF116) on 2026-09-22, not taken on trust:

- Amounts come as portions (feeder) and millilitres (fountain), whatever unit
  the account has chosen in the app. Switching the app from cups and ounces to
  grams and millilitres changed nothing in the answers but the unit ids, so
  the add-on speaks in portions and millilitres and ignores those ids.
- The Air Smart Feeder's portion is half the usual one, and it gives at most
  ``maxFeedingCup`` portions at a time (16 there).
- ``/data/data/realInfo`` carries everything the status needs. The shorter
  ``/device/device/realInfo`` answered 0 ml for the fountain's intake of the
  day while this one had the real value, so it is not used.
- The fountain's ``weight`` is its water in grams, i.e. millilitres; the
  ``lowWater`` threshold is in millilitres too.
- The reminder counters (desiccant, filter, cleaning) run on the server. They
  keep counting while a device is switched off and know nothing about whether
  anything was replaced - they only restart when they are reset.

Protocol knowledge from the community integration jjjonesjr33/petlibro
(GPL-3); no code is taken from it, the implementation here is our own.
"""

import re
import time

from logHandler import log

import addonHandler
try:
    addonHandler.initTranslation()
except Exception as e:
    log.debug(f"initTranslation failed: {e}")
if "_" not in globals():  # fallback if initTranslation() fails
    # Without this fallback `_` stays undefined and the first `_()` call
    # raises a NameError instead of failing at import time.
    def _(s):
        return s


# How long a value set from here outranks what the cloud reports. The first
# poll after a write regularly still carries the old value; without this the
# tree flips back and forth once and the flip is announced as a change.
PROTECT_WINDOW = 20.0

# After a change made here has been confirmed, the cloud can still hand back
# the value from before for minutes. Measured on 2026-10-03 on the PLWF116
# on its power supply: constant flow set at 17:46:33 and read back as such at
# once, then "sensor, far" from 17:48:58 on, and constant again only at
# 17:54:47 - both jumps announced and written to the history as changes made
# elsewhere, while the fountain ran constant flow all along. For this long
# the value from before is not taken back; any other value is.
REVERT_HOLD = 15 * 60.0

# The fields the water mode request (waterModeSetting) carries.
WATER_SETTING_KEYS = ("useWaterType", "radarSensingLevel", "waterStopSwitch",
                      "sensingWaterDuration")

# States of a meal in /device/feedingPlan/todayNew (community mapping, the
# first and the third confirmed on the PLAF108: passed meals carry 3, the
# coming one 1).
PLAN_PENDING = 1
PLAN_TO_BE_SKIPPED = 2
PLAN_DISPENSED = 3
PLAN_SKIPPED = 4

# Work record types and event types seen in /device/workRecord/list.
RECORD_FEED_OK = "GRAIN_OUTPUT_SUCCESS"
EVENT_MANUAL_PREFIX = "MANUAL_FEEDING"
RECORD_DRINK = "DRINK"
CONNECTION_RECORDS = ("ONLINE", "OFFLINE")

# What in a record's type marks a failed feeding or a fault. Not "alarmTag":
# the cloud sets it on warnings too - SURPLUS_GRAIN_NOT_ENOUGH, the food-low
# warning, came with it on 2026-09-23 and was announced as a failed feeding
# while the feeder had given the meal. The words match the notice switches
# the feeder has (GrainOutletBlocked, NoGrainOut, VacuumFailed) and the
# failure events the community has seen (GRAIN_OUTLET_BLOCKED_OVERTIME,
# FOOD_OUTLET_DOOR_FAILED_CLOSE, VACUUM_FAILED).
FAILURE_MARKERS = ("FAIL", "BLOCK", "NO_GRAIN", "STUCK", "JAM", "ERROR")


def record_kind(record):
    """'fed', 'failed', 'connection' or 'notice' for one work record."""
    kind = str(record.get("type") or "").upper()
    event = str(record.get("eventType") or "").upper()
    if kind == RECORD_FEED_OK:
        return "fed"
    if kind in CONNECTION_RECORDS or event in CONNECTION_RECORDS:
        return "connection"
    if any(marker in kind or marker in event for marker in FAILURE_MARKERS):
        return "failed"
    return "notice"


class PetlibroPending(Exception):
    """A change the cloud accepted but the device has not confirmed yet.

    The cloud answers 2020 ("device response timeout") when the device does
    not reply within a few seconds. For the Dockstream 2 Cordless on its
    battery that is the normal case, and the change still arrives: measured
    on 2026-09-22, "constant flow" was answered with 2020 and was in force at
    the next poll two minutes later. So it is neither a success nor a
    failure - ``key`` and ``expected`` say what to watch for (the field of
    the change snapshot and its value), or ``key`` is None when there is
    nothing to watch.
    """

    def __init__(self, key=None, expected=None):
        super().__init__(f"pending: {key}={expected!r}")
        self.key = key
        self.expected = expected


# The water flow durations of the pet-sensing mode the Petlibro app offers,
# in seconds, and the firmware they need (Petlibro support, 2026-10-01).
SENSING_DURATIONS = (45, 60, 75, 90)
SENSING_DURATION_FIRMWARE = (0, 3, 25)


def _version_tuple(text):
    """ "0.3.59" -> (0, 3, 59); None when it is no version number."""
    try:
        return tuple(int(part) for part in str(text).strip().split("."))
    except ValueError:
        return None


# Feeders without a speaker, hence without a meal call. Petlibro support on
# the Air Smart Feeder: "does not have a microphone or speaker, so it does not
# support meal call settings". Other models keep the choice.
NO_MEAL_CALL_MODELS = ("PLAF108",)

# Fallback when a feeder does not say how many portions it gives at most.
# Deliberately small: it only bounds a choice list, and the cloud refuses an
# amount the feeder cannot give anyway.
DEFAULT_MAX_PORTIONS = 10


def _as_int(value):
    """An int from the API, or None - numbers come as int, float or text."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def portions_text(count):
    """ "1 portion" / "3 portions"."""
    if count == 1:
        # Translators: Amount of food, exactly one portion.
        return _("{count} portion").format(count=count)
    # Translators: Amount of food in portions. {count} = number (not 1).
    return _("{count} portions").format(count=count)


def ml_text(ml):
    # Translators: A volume of water in millilitres. {ml} = number.
    return _("{ml} ml").format(ml=ml)


def days_text(days):
    if days == 1:
        # Translators: A span of exactly one day.
        return _("{count} day").format(count=days)
    # Translators: A span of days. {count} = number (not 1).
    return _("{count} days").format(count=days)


def record_time_text(ts):
    """Time of a work record: "17:00", "yesterday 22:00" or a full date.

    The full date is ISO: it reads the same in every language and cannot be
    mistaken for a day and month the other way round.
    """
    if not ts:
        return ""
    local = time.localtime(ts)
    today = time.localtime()
    clock = time.strftime("%H:%M", local)
    if local[:3] == today[:3]:
        return clock
    yesterday = time.localtime(time.time() - 86400)
    if local[:3] == yesterday[:3]:
        # Translators: Time of an event on the previous day. {time} = HH:MM.
        return _("yesterday {time}").format(time=clock)
    return time.strftime("%Y-%m-%d %H:%M", local)


class PetlibroDevice:
    """Common part of every Petlibro device.

    Also used on its own for a product the add-on does not know (a litter
    box, a camera feeder): it then shows what every device reports - online,
    battery, firmware - and nothing it cannot vouch for.
    """

    is_petlibro = True
    is_feeder = False
    is_fountain = False

    def __init__(self, raw, api):
        self._api = api
        self.serial = str(raw.get("deviceSn") or "")
        self._list = {}
        self._real = {}
        # field -> (value, protected until)
        self._protected = {}
        # key -> (value before a confirmed change, until), see REVERT_HOLD
        self._reverts = {}
        self.is_offline = False
        self.last_update = 0.0
        # Newest first, flattened from the day groups of the work records.
        # The feeder writes its meals there, the fountain its drinks and its
        # faults (WATER_BOX_LEAVE_ERROR, measured 2026-09-23).
        self.records = []
        self.records_loaded_at = 0.0
        # Polls that still fetch the records although nothing moved: a
        # record can show up in the list seconds after the counter that
        # announced it (the 12:30 meal of 2026-09-23 did).
        self.records_follow_up = 0
        self.apply_list(raw)

    # ---------------- Identity ----------------
    @property
    def uuid(self):
        # Prefixed like Cozytouch: the history and the favourites can tell
        # the platform from the key alone, and a serial can never collide
        # with a Meross uuid.
        return "petlibro_" + self.serial

    @property
    def unique_id(self):
        return self.uuid

    @property
    def name(self):
        # Names typed in the app carry stray double spaces ("Dockstream 2
        # Smart  Kitchen"); a screen reader pauses at each one.
        raw = self._list.get("name") or self._list.get("productName") or self.serial
        return " ".join(str(raw).split())

    @property
    def type(self):
        return self.model_name or self.model or ""

    @property
    def model(self):
        return str(self._list.get("productIdentifier") or "")

    @property
    def model_name(self):
        return str(self._list.get("productName") or "")

    def get_type_display(self):
        # The model number, as asked for: it is what stands on the device
        # and in the support pages.
        return self.model or self.model_name

    def get_channels(self):
        return []

    @property
    def is_on(self):
        # Nothing to switch on or off as a whole - see _favorite_toggle.
        return None

    @property
    def shared(self):
        """True when the account only has the device shared with it."""
        return self._list.get("deviceShareState") == 1

    # ---------------- Data ----------------
    def apply_list(self, entry):
        """Takes over a device list entry (names, online, next feeding ...)."""
        if not isinstance(entry, dict):
            return
        self._list.update(entry)
        if "online" in entry:
            self.is_offline = not bool(entry.get("online"))

    def apply_real(self, data):
        """Takes over /data/data/realInfo, keeping protected values."""
        if not isinstance(data, dict):
            return
        now = time.time()
        for key, value in data.items():
            held = self._protected.get(key)
            if held is not None:
                held_value, until = held
                if now < until and value != held_value:
                    continue
                self._protected.pop(key, None)
            revert = self._reverts.get(key)
            if revert is not None:
                old_value, until = revert
                if (now < until and value == old_value
                        and self._real.get(key) != old_value):
                    log.debug(f"Petlibro: {self.name}: the cloud still reports "
                              f"{key}={value!r} from before the change")
                    continue
                # Run out, or a third value - somebody else's change, after
                # which the value from before may come back like any other.
                if now >= until or value != self._real.get(key):
                    self._reverts.pop(key, None)
            self._real[key] = value
        if "online" in data:
            self.is_offline = not bool(data.get("online"))
        self.last_update = now

    def apply_records(self, days):
        self.records = flatten_records(days)
        self.records_loaded_at = time.time()

    def _protect(self, key, value):
        """Holds a value set from here until the cloud reports it too."""
        self._real[key] = value
        self._protected[key] = (value, time.time() + PROTECT_WINDOW)

    def _release_reverts(self, keys):
        """A new change of these fields: the old holds no longer apply.

        Otherwise the read-back of a change back to the earlier value would
        be refused as the cloud lagging.
        """
        for key in keys:
            self._reverts.pop(key, None)

    def _hold_off_revert(self, before):
        """After a confirmed change: the values from before are not taken
        back for REVERT_HOLD. ``before``: {key: value before the change}."""
        until = time.time() + REVERT_HOLD
        for key, old in before.items():
            if old is not None and self._real.get(key) != old:
                self._reverts[key] = (old, until)

    def _get(self, key, default=None):
        if key in self._real:
            return self._real[key]
        return self._list.get(key, default)

    # ---------------- Common readings ----------------
    @property
    def battery_percent(self):
        return _as_int(self._get("electricQuantity"))

    @property
    def battery_low(self):
        state = self._get("batteryState")
        return str(state).lower() == "low" if state else None

    @property
    def on_mains(self):
        """True on the power supply, False on the battery, None if unknown.

        powerType 2 was the battery on both devices measured (the feeder was
        confirmed unplugged), 3 the supply according to the community.
        """
        power_type = self._get("powerType")
        if power_type == 3:
            return True
        if power_type == 2:
            return False
        return None

    @property
    def charge_state(self):
        """'charging', 'charged', 'discharging' or None."""
        state = str(self._get("powerState") or "").upper()
        return {"CHARGING": "charging", "CHARGED": "charged",
                "USING": "discharging"}.get(state)

    @property
    def light_on(self):
        value = self._get("lightSwitch")
        return None if value is None else bool(value)

    @property
    def wifi_signal(self):
        return _as_int(self._get("wifiRssi"))

    @property
    def firmware(self):
        return str(self._get("softwareVersion") or "")

    @property
    def fault(self):
        """True when the device reports a fault of its own."""
        if self._get("errorState") or self._get("deviceStoppedWorking"):
            return True
        door = self._get("doorErrorState")
        return bool(door) and str(door).upper() != "NORMAL"

    @property
    def fault_message(self):
        """The cloud's own fault text (English), or "" - see fault_text()."""
        return str(self._get("exceptionMessage") or "").strip()

    def battery_text(self):
        """ "50 percent, on battery" - None when nothing is known."""
        percent = self.battery_percent
        if percent is None:
            return None
        mains = self.on_mains
        charge = self.charge_state
        if mains is True and charge == "charging":
            # Translators: Battery of a Petlibro device while it charges.
            # {percent} = charge level.
            return _("{percent} percent, charging").format(percent=percent)
        if mains is True:
            # Translators: Battery of a Petlibro device on the power supply.
            return _("{percent} percent, on the power supply").format(
                percent=percent)
        if mains is False:
            # Translators: Battery of a Petlibro device running on its battery,
            # without the power supply.
            return _("{percent} percent, on battery").format(percent=percent)
        # Translators: Battery charge of a Petlibro device. {percent} = level.
        return _("{percent} percent").format(percent=percent)

    def get_status_summary(self):
        if self.is_offline:
            # Translators: Status of a device without connection.
            return _("offline")
        parts = []
        battery = self.battery_text()
        if battery:
            # Translators: Part of a status announcement. {battery} = e.g.
            # "50 percent, on battery".
            parts.append(_("battery {battery}").format(battery=battery))
        return ", ".join(parts)

    # ---------------- Actions ----------------
    def _send(self, call, key=None, expected=None):
        """Runs one command; a 2020 becomes PetlibroPending (see there)."""
        from .petlibro_api import PetlibroError, CODE_DEVICE_TIMEOUT
        try:
            return call()
        except PetlibroError as e:
            if e.code == CODE_DEVICE_TIMEOUT:
                log.info(f"Petlibro: {self.name} did not answer in time, "
                         f"waiting for {key}={expected!r}")
                raise PetlibroPending(key, expected)
            raise

    def set_light(self, on):
        """Switches the indicator light. Returns (ok, actual state)."""
        self._send(lambda: self._api.set_light(self.serial, on),
                   'light', bool(on))
        actual = self._read_back("lightSwitch", bool(on))
        if actual == bool(on):
            self._protect("lightSwitch", bool(on))
        return actual == bool(on), actual

    def _read_back(self, key, expected, attempts=3, pause=1.5):
        """Reads a field back after a write until it shows ``expected``.

        The cloud answers a write before the device has taken it; the first
        read right after often still has the old value. Returns what the
        device reports last (None when it could not be read).
        """
        actual = None
        for _attempt in range(attempts):
            time.sleep(pause)
            try:
                data = self._api.fetch_real_info(self.serial)
            except Exception as e:
                log.debug(f"Petlibro: read-back of {key} failed: {e}")
                continue
            self.apply_real(data)
            actual = data.get(key) if isinstance(data, dict) else None
            if isinstance(expected, bool) and actual is not None:
                actual = bool(actual)
            if actual == expected:
                return actual
        return actual

    def _update_status(self):
        """Safety net for generic refresh paths; the scheduler uses the API."""
        try:
            self._api.update_device_status([self])
        except Exception as e:
            log.debug(f"Petlibro: _update_status failed for {self.name}: {e}")


class PetlibroFeeder(PetlibroDevice):
    """A dry food feeder (PLAF...)."""

    is_feeder = True

    def __init__(self, raw, api):
        super().__init__(raw, api)
        self._grain = {}
        self._today = {}
        self.plans = []
        self.plans_loaded_at = 0.0

    # ---------------- Readings ----------------
    @property
    def food_low(self):
        surplus = self._get("surplusGrain")
        return None if surplus is None else not bool(surplus)

    @property
    def feeding_plan_enabled(self):
        value = self._get("enableFeedingPlan")
        return None if value is None else bool(value)

    @property
    def next_feed(self):
        """(time "HH:MM", portions) of the next planned meal, or None."""
        when = self._list.get("nextFeedingTime")
        amount = _as_int(self._list.get("nextFeedingQuantity"))
        if not when or amount is None or not self.feeding_plan_enabled:
            return None
        return str(when), amount

    @property
    def has_meal_call(self):
        return self.model.upper() not in NO_MEAL_CALL_MODELS

    @property
    def max_portions(self):
        value = _as_int(self._list.get("maxFeedingCup"))
        return value if value and value > 0 else DEFAULT_MAX_PORTIONS

    @property
    def today_count(self):
        return _as_int(self._grain.get("todayFeedingTimes"))

    @property
    def today_portions(self):
        return _as_int(self._grain.get("todayFeedingQuantity"))

    @property
    def today_plans(self):
        """Today's meals, sorted by time: dicts with planId, time, grainNum, state."""
        plans = self._today.get("plans") or []
        return sorted((p for p in plans if isinstance(p, dict)),
                      key=lambda p: str(p.get("time") or ""))

    @property
    def today_all_skipped(self):
        return bool(self._today.get("allSkipped"))

    @property
    def desiccant_days(self):
        return _as_int(self._get("remainingDesiccantDays"))

    @property
    def desiccant_cycle(self):
        return _as_int(self._get("changeDesiccantFrequency"))

    @property
    def desiccant_due(self):
        days = self.desiccant_days
        if days is not None:
            return days <= 0
        state = self._get("desiccantState")
        return None if state is None else str(state).upper() == "OVER_DUE"

    def apply_grain_status(self, data):
        if isinstance(data, dict):
            self._grain = dict(data)

    def apply_today(self, data):
        if isinstance(data, dict):
            self._today = dict(data)

    def apply_plans(self, plans):
        if isinstance(plans, list):
            self.plans = [p for p in plans if isinstance(p, dict)]
            self.plans_loaded_at = time.time()

    def plan(self, plan_id):
        for plan in self.plans:
            if plan.get("id") == plan_id:
                return plan
        return None

    def plan_label(self, plan_id):
        plan = self.plan(plan_id)
        return str(plan.get("label") or "").strip() if plan else ""

    @property
    def last_feed(self):
        """The newest dispensing: dict time, portions, manual, plan_id - or None."""
        for record in self.records:
            if record.get("type") != RECORD_FEED_OK:
                continue
            return feed_from_record(record)
        return None

    def meals_done_text(self):
        """ "3 of 4 done" for today's meals, or None without a plan."""
        plans = self.today_plans
        if not plans:
            return None
        done = sum(1 for p in plans if p.get("state") == PLAN_DISPENSED)
        # Translators: How many of today's meals of the feeding plan have
        # been given. {done} = given so far, {total} = meals planned for today.
        return _("{done} of {total} given").format(done=done, total=len(plans))

    def get_status_summary(self):
        if self.is_offline:
            return _("offline")
        parts = []
        nxt = self.next_feed
        if nxt:
            # Translators: Part of a feeder's status. {time} = HH:MM,
            # {amount} = e.g. "2 portions".
            parts.append(_("next feeding {time}, {amount}").format(
                time=nxt[0], amount=portions_text(nxt[1])))
        elif self.feeding_plan_enabled is False:
            # Translators: Part of a feeder's status: the feeding plan is off.
            parts.append(_("feeding plan off"))
        if self.food_low:
            # Translators: Part of a feeder's status: the food container is
            # almost empty.
            parts.append(_("food low"))
        if self.desiccant_due:
            # Translators: Part of a feeder's status: the desiccant should be
            # replaced.
            parts.append(_("desiccant due"))
        if self.fault:
            # Translators: Part of a device's status: it reports a fault.
            parts.append(_("fault"))
        base = PetlibroDevice.get_status_summary(self)
        if base:
            parts.append(base)
        return ", ".join(parts)

    # ---------------- Actions ----------------
    def set_feeding_plan(self, enabled):
        """Switches the whole feeding plan. Returns (ok, actual)."""
        self._send(lambda: self._api.set_feeding_plan_enabled(self.serial, enabled),
                   'plan_enabled', bool(enabled))
        actual = None
        for _attempt in range(3):
            time.sleep(1.5)
            try:
                entry = self._api.fetch_list_entry(self.serial)
            except Exception as e:
                log.debug(f"Petlibro: read-back of the feeding plan failed: {e}")
                continue
            if entry:
                self.apply_list(entry)
                actual = self.feeding_plan_enabled
                if actual == bool(enabled):
                    break
        return actual == bool(enabled), actual

    def reset_desiccant(self):
        """Restarts the desiccant reminder. Returns (ok, remaining days)."""
        self._send(lambda: self._api.reset_desiccant(self.serial))
        return _read_back_reset(self, "remainingDesiccantDays",
                                self.desiccant_cycle)

    def refresh_today(self):
        try:
            self.apply_today(self._api.fetch_today_plans(self.serial))
        except Exception as e:
            log.debug(f"Petlibro: today's meals of {self.name}: {e}")

    def refresh_plans(self):
        """Reloads the plan list; True when it could be read."""
        try:
            self.apply_plans(self._api.fetch_plans(self.serial))
            return True
        except Exception as e:
            log.debug(f"Petlibro: feeding plans of {self.name}: {e}")
            return False

    def feed_now(self, portions, wait=30.0, pause=3.0):
        """Gives ``portions`` now and waits for the feeder to confirm.

        Sends the command exactly once, whatever happens: a second command
        is a second meal. The confirmation is the work record the feeder
        writes after dispensing - a record id that was not there before, so
        a clock that differs between here and the cloud cannot fake one.

        Returns a dict:
            status: 'confirmed', 'refused', 'failed' or 'unconfirmed'
            portions: what the feeder reports as dispensed (confirmed)
            code: the cloud's error code (refused)
            unclear: True when the command may or may not have arrived
        """
        known = None
        try:
            known = {r.get("id") for r in flatten_records(
                self._api.fetch_records(self.serial, size=10))}
        except Exception as e:
            # Without the list the confirmation falls back to the time of
            # the record; the feeding itself does not depend on it.
            log.debug(f"Petlibro: records before feeding not readable: {e}")
        started = time.time()

        from .petlibro_api import PetlibroError, CODE_DEVICE_TIMEOUT
        try:
            self._api.manual_feed(self.serial, portions)
        except PetlibroError as e:
            if e.code != CODE_DEVICE_TIMEOUT:
                return {"status": "refused", "code": e.code, "unclear": False}
            # 2020: the cloud did not hear back from the feeder. The feeder
            # may still have got the command - look for its record.
            log.warning(f"Petlibro: feeder did not answer the cloud "
                        f"({self.name}), looking for its record")
            unclear = True
        except Exception as e:
            # A network error on the way back leaves it open whether the
            # feeder got the command. Still look for a record below, but
            # never send again.
            log.warning(f"Petlibro: feeding command without an answer: "
                        f"{type(e).__name__}: {e}")
            unclear = True
        else:
            unclear = False

        deadline = started + wait
        while time.time() < deadline:
            time.sleep(pause)
            try:
                records = flatten_records(
                    self._api.fetch_records(self.serial, size=10))
            except Exception as e:
                log.debug(f"Petlibro: records after feeding not readable: {e}")
                continue
            fresh = [r for r in records if _is_new_record(r, known, started)]
            for record in fresh:
                log.info(f"Petlibro: new record on {self.name}: "
                         f"{record.get('type')}/{record.get('eventType')}")
            # A dispensing record wins over anything written beside it: a
            # food-low warning right after the meal is no failed meal.
            # Only a MANUAL one confirms this command: a planned meal that
            # falls into the same half minute used to be taken for it - its
            # portions announced as given by hand, its record marked as seen
            # and so never written as the planned meal it was, and the real
            # confirmation that followed announced as a feeding from
            # elsewhere.
            fed = [r for r in fresh if record_kind(r) == "fed"
                   and feed_from_record(r)["manual"]]
            if fed:
                self.records = records
                feed = feed_from_record(fed[0])
                self._refresh_grain()
                return {"status": "confirmed",
                        "portions": feed.get("portions"),
                        "record_id": feed.get("id"),
                        "unclear": False}
            failed = [r for r in fresh if record_kind(r) == "failed"]
            if failed:
                self.records = records
                kind = failed[0].get("eventType") or failed[0].get("type")
                log.warning(f"Petlibro: feeding failed on {self.name}: {kind}")
                return {"status": "failed", "unclear": False,
                        "record_id": failed[0].get("id"), "record": kind}
        self._refresh_grain()
        return {"status": "unconfirmed", "unclear": unclear}

    def _refresh_grain(self):
        try:
            self.apply_grain_status(self._api.fetch_grain_status(self.serial))
        except Exception as e:
            log.debug(f"Petlibro: grain status of {self.name}: {e}")


class PetlibroFountain(PetlibroDevice):
    """A water fountain (PLWF...)."""

    is_fountain = True

    def __init__(self, raw, api):
        super().__init__(raw, api)
        self._drink = {}

    # ---------------- Readings ----------------
    @property
    def water_percent(self):
        return _as_int(self._get("weightPercent"))

    @property
    def water_ml(self):
        return _as_int(self._get("weight"))

    @property
    def low_water_ml(self):
        return _as_int(self._get("lowWater"))

    @property
    def water_low(self):
        water, limit = self.water_ml, self.low_water_ml
        if water is None or not limit:
            return None
        return water < limit

    @property
    def supports_sensor(self):
        return self._get("radarSensingLevel") is not None

    @property
    def water_mode(self):
        """'off', 'constant', 'intermittent', 'sensor_near', 'sensor_far' or None."""
        if self._get("waterStopSwitch"):
            return "off"
        kind = _as_int(self._get("useWaterType"))
        if kind == 0:
            return "constant"
        if kind == 1:
            return "intermittent"
        if kind == 2:
            radar = str(self._get("radarSensingLevel") or "")
            if radar == "FarTrigger":
                return "sensor_far"
            if radar == "NearTrigger":
                return "sensor_near"
        return None

    def water_modes(self):
        """The modes this fountain offers, in display order.

        A fountain with a presence sensor offers the two sensor ranges, one
        without offers the interval mode instead - the pattern of the models
        known so far (PLWF116 has the sensor, PLWF105/106 the interval).

        Constant flow only on the power supply. Petlibro: "Plugged In:
        Continuous and Pet-Sensing Flow - On battery Mode: Pet-Sensing Flow
        Only" (the page comparing the two Dockstream 2 models). The cloud
        takes constant flow on the battery all the same and stores it - and
        the fountain then does not run it (tester, 2026-09-22), see
        constant_idle.
        """
        modes = ["off"]
        if self.on_mains is not False:
            modes.append("constant")
        if self.supports_sensor:
            modes += ["sensor_near", "sensor_far"]
        if not self.supports_sensor or self.water_mode == "intermittent":
            modes.append("intermittent")
        return modes

    @property
    def constant_idle(self):
        """Constant flow stored while the fountain runs on its battery.

        The fountain gives no constant flow on its battery (see water_modes),
        so this mode does nothing there - no water at all until it is plugged
        in or the mode is changed. Said in the tree rather than shown as if
        it were running.
        """
        return self.water_mode == "constant" and self.on_mains is False

    @property
    def sensing_duration(self):
        """Seconds the water flows once the sensor noticed the pet, or None."""
        return _as_int(self._get("sensingWaterDuration"))

    @property
    def sensing_duration_supported(self):
        """True, False (firmware too old) or None (firmware not reported).

        Petlibro: the duration setting needs firmware 0.3.25 or later.
        """
        if self.sensing_duration is None or not self.supports_sensor:
            return False
        version = _version_tuple(self.firmware) if self.firmware else None
        if version is None:
            return None
        return version >= SENSING_DURATION_FIRMWARE

    @property
    def water_interval(self):
        return _as_int(self._get("useWaterInterval"))

    @property
    def water_duration(self):
        return _as_int(self._get("useWaterDuration"))

    @property
    def drink_today_ml(self):
        value = self._drink.get("todayTotalMl")
        return _as_int(value if value is not None else self._get("todayTotalMl"))

    @property
    def drink_today_count(self):
        return _as_int(self._drink.get("todayTotalTimes"))

    @property
    def drink_yesterday_ml(self):
        return _as_int(self._drink.get("yesterdayTotalMl"))

    @property
    def drink_yesterday_count(self):
        return _as_int(self._drink.get("yesterdayTotalTimes"))

    @property
    def filter_days(self):
        return _as_int(self._get("remainingReplacementDays"))

    @property
    def filter_cycle(self):
        return _as_int(self._get("filterReplacementFrequency"))

    @property
    def filter_due(self):
        days = self.filter_days
        return None if days is None else days <= 0

    @property
    def cleaning_days(self):
        return _as_int(self._get("remainingCleaningDays"))

    @property
    def cleaning_cycle(self):
        return _as_int(self._get("machineCleaningFrequency"))

    @property
    def cleaning_due(self):
        days = self.cleaning_days
        return None if days is None else days <= 0

    def apply_drink(self, data):
        if isinstance(data, dict):
            self._drink = dict(data)

    def water_level_text(self):
        """ "73 percent, about 2100 ml" - None when nothing is known."""
        percent, ml = self.water_percent, self.water_ml
        if percent is None and ml is None:
            return None
        if percent is not None and ml is not None:
            # Translators: Water level of a fountain. {percent} = fill level,
            # {ml} = water in millilitres, rounded to tens.
            return _("{percent} percent, about {ml} ml").format(
                percent=percent, ml=int(round(ml, -1)))
        if percent is not None:
            return _("{percent} percent").format(percent=percent)
        return ml_text(ml)

    def get_status_summary(self):
        if self.is_offline:
            return _("offline")
        parts = []
        level = self.water_level_text()
        if level:
            # Translators: Part of a fountain's status. {level} = e.g.
            # "73 percent, about 2100 ml".
            parts.append(_("water {level}").format(level=level))
        if self.water_low:
            # Translators: Part of a fountain's status: the water is below
            # the warning threshold.
            parts.append(_("water low"))
        mode = self.water_mode
        if mode == "off":
            # Translators: Part of a fountain's status: the pump is off.
            parts.append(_("pump off"))
        elif self.constant_idle:
            # Translators: Part of a fountain's status: constant flow is set,
            # but the fountain runs on its battery and does not run it.
            parts.append(_("constant flow set, but not running on the battery"))
        drunk = self.drink_today_ml
        if drunk is not None:
            # Translators: Part of a fountain's status. {ml} = e.g. "35 ml".
            parts.append(_("drunk today {ml}").format(ml=ml_text(drunk)))
        if self.filter_due:
            # Translators: Part of a fountain's status: the filter should be
            # replaced.
            parts.append(_("filter due"))
        if self.cleaning_due:
            # Translators: Part of a fountain's status: the fountain should be
            # cleaned.
            parts.append(_("cleaning due"))
        if self.fault:
            parts.append(_("fault"))
        base = PetlibroDevice.get_status_summary(self)
        if base:
            parts.append(base)
        return ", ".join(parts)

    # ---------------- Actions ----------------
    def set_water_mode(self, mode):
        """Switches the water mode. Returns (ok, actual mode).

        Between the two sensor ranges only the range changes - a setting the
        cloud keeps and answers at once. Sending the water mode again as well
        is what failed on 2026-09-22: the range was accepted, the repeated
        mode ran into 2020, and "far" was reported as not taken.
        """
        current = self.water_mode
        self._release_reverts(WATER_SETTING_KEYS)
        before = {key: self._real.get(key) for key in WATER_SETTING_KEYS}
        sensor = ("sensor_near", "sensor_far")
        if current in sensor and mode in sensor:
            self._send(lambda: self._api.set_radar(self.serial, mode),
                       'water_mode', mode)
        else:
            self._send(lambda: self._api.set_water_mode(
                self.serial, mode,
                interval=self.water_interval, duration=self.water_duration,
                sensing=_as_int(self._get("sensingWaterDuration")),
                currently_off=(current == "off")), 'water_mode', mode)
        actual = None
        for _attempt in range(3):
            time.sleep(1.5)
            try:
                self.apply_real(self._api.fetch_real_info(self.serial))
            except Exception as e:
                log.debug(f"Petlibro: read-back of the water mode failed: {e}")
                continue
            actual = self.water_mode
            if actual == mode:
                break
        if actual == mode:
            self._hold_off_revert(before)
        return actual == mode, actual

    def set_sensing_duration(self, seconds):
        """Sets how long the water flows in the pet-sensing mode.

        Returns (ok, actual seconds). Offered only while a sensor mode runs:
        the duration travels in the sensor mode's own request, and sending
        it in any other mode would switch the fountain to the sensor.
        """
        seconds = int(seconds)
        self._release_reverts(WATER_SETTING_KEYS)
        before = {key: self._real.get(key) for key in WATER_SETTING_KEYS}
        self._send(lambda: self._api.set_sensing_duration(
            self.serial, seconds, interval=self.water_interval,
            duration=self.water_duration), 'sensing_duration', seconds)
        actual = _as_int(self._read_back("sensingWaterDuration", seconds))
        if actual == seconds:
            self._protect("sensingWaterDuration", seconds)
            self._hold_off_revert(before)
        return actual == seconds, actual

    def set_low_water(self, ml):
        """Sets the low water threshold. Returns (ok, actual ml)."""
        self._send(lambda: self._api.set_low_water(self.serial, ml),
                   'low_water', int(ml))
        actual = _as_int(self._read_back("lowWater", int(ml)))
        if actual == int(ml):
            self._protect("lowWater", int(ml))
        return actual == int(ml), actual

    def reset_filter(self):
        self._send(lambda: self._api.reset_filter(self.serial))
        return _read_back_reset(self, "remainingReplacementDays",
                                self.filter_cycle)

    def reset_cleaning(self):
        self._send(lambda: self._api.reset_cleaning(self.serial))
        return _read_back_reset(self, "remainingCleaningDays",
                                self.cleaning_cycle)


# ---------------- Helpers ----------------
def flatten_records(days):
    """The work records of all day groups, newest first."""
    records = []
    for day in days or []:
        if not isinstance(day, dict):
            continue
        for record in day.get("workRecords") or []:
            if isinstance(record, dict):
                records.append(record)
    records.sort(key=lambda r: _as_int(r.get("recordTime")) or 0, reverse=True)
    return records


def feed_from_record(record):
    """What a dispensing record says, in the add-on's terms."""
    ts = (_as_int(record.get("recordTime")) or 0) / 1000.0
    plan_id = None
    params = record.get("params")
    if isinstance(params, str) and params.startswith("{"):
        try:
            import json
            plan_id = json.loads(params).get("planId")
        except ValueError:
            plan_id = None
    return {
        "id": record.get("id"),
        "time": ts,
        "portions": _as_int(record.get("actualGrainNum")),
        "expected": _as_int(record.get("expectGrainNum")),
        # MANUAL_FEEDING_SUCCESS - and, by the pattern of the planned
        # meal on low food (FEEDING_PLAN_GRAIN_NOT_ENOUGH, 2026-09-23),
        # presumably MANUAL_FEEDING_GRAIN_NOT_ENOUGH as well.
        "manual": str(record.get("eventType") or "").upper().startswith(
            EVENT_MANUAL_PREFIX),
        "plan_id": plan_id,
    }


def _is_new_record(record, known, started):
    """A record written after the command was sent.

    Both tests, not one: an id missing from the list read before is not
    enough - a list that came back empty without an error would make every
    old meal "new" and confirm a feeding that never happened. The time has a
    margin of two minutes for clocks that do not agree.
    """
    if known is not None and record.get("id") in known:
        return False
    ts = (_as_int(record.get("recordTime")) or 0) / 1000.0
    return ts >= started - 120


def _read_back_reset(device, key, cycle):
    """Reads a reminder counter back after a reset. Returns (ok, days)."""
    actual = None
    for _attempt in range(3):
        time.sleep(1.5)
        try:
            data = device._api.fetch_real_info(device.serial)
        except Exception as e:
            log.debug(f"Petlibro: read-back of {key} failed: {e}")
            continue
        device.apply_real(data)
        actual = _as_int(data.get(key)) if isinstance(data, dict) else None
        if actual is not None and actual > 0 and (cycle is None
                                                  or actual >= cycle - 1):
            return True, actual
    return False, actual


def duration_text(seconds):
    """ "45 seconds", "4 minutes 5 seconds" - words, since a screen reader
    speaks "4:05" as a time of day."""
    seconds = max(0, int(seconds))
    minutes, rest = divmod(seconds, 60)
    if minutes == 0:
        if rest == 1:
            # Translators: A duration of exactly one second.
            return _("{count} second").format(count=rest)
        # Translators: A duration in seconds. {count} = number (not 1).
        return _("{count} seconds").format(count=rest)
    if minutes == 1:
        # Translators: A duration of exactly one minute.
        text = _("{count} minute").format(count=minutes)
    else:
        # Translators: A duration in minutes. {count} = number (not 1).
        text = _("{count} minutes").format(count=minutes)
    if rest:
        text += " " + duration_text(rest)
    return text


def drink_rows(data):
    """The hours of a day with drinking, as (start, end, ml, count, seconds).

    ``data`` is the answer of /data/deviceDrinkWater/history: 24 values each
    in waterIntake (ml), drinkTimes and avgDrinkDuration (seconds), the hours
    in xdate. An hour without a drink is left out.
    """
    hours = data.get("xdate") or []
    intake = data.get("waterIntake") or []
    times = data.get("drinkTimes") or []
    durations = data.get("avgDrinkDuration") or []
    rows = []
    for index, start in enumerate(hours):
        def value(values):
            try:
                return int(values[index] or 0)
            except (IndexError, TypeError, ValueError):
                return 0
        ml, count, seconds = value(intake), value(times), value(durations)
        if not ml and not count:
            continue
        try:
            end = "%02d:00" % ((int(str(start)[:2]) + 1) % 24)
        except ValueError:
            end = ""
        rows.append((str(start), end, ml, count, seconds))
    return rows


def drinks_from_records(records):
    """The single drinks among work records, oldest first: (ts, ml, seconds).

    A DRINK record carries "totalMl" and "drinkTime" (seconds) - measured on
    the PLWF116, whose six drinks of 2026-09-23 matched the six of the day's
    total.
    """
    drinks = []
    for record in records:
        if str(record.get("type") or "").upper() != RECORD_DRINK:
            continue
        ts = (_as_int(record.get("recordTime")) or 0) / 1000.0
        drinks.append((ts, _as_int(record.get("totalMl")),
                       _as_int(record.get("drinkTime"))))
    drinks.sort()
    return drinks


def fault_key(value):
    """A record type or a fault text of the cloud in one comparable form.

    "WATER_BOX_LEAVE_ERROR" stays as it is, "Water tank irregularity"
    becomes WATER_TANK_IRREGULARITY, and another name for the same fault
    becomes that fault's key (PETLIBRO_FAULT_ALIASES).
    """
    from .constants import PETLIBRO_FAULT_ALIASES
    key = re.sub(r"[^A-Z0-9]+", "_", str(value or "").upper()).strip("_")
    return PETLIBRO_FAULT_ALIASES.get(key, key)


def fault_text(value):
    """What a fault is called in the current language, "" for none.

    A known fault by its name. An unknown record type as the device's code,
    an unknown text of the cloud as a quotation marked as Petlibro's - it is
    English, and it should be heard as the manufacturer's words rather than
    as the add-on's.
    """
    from .constants import PETLIBRO_FAULT_NAMES
    raw = str(value or "").strip()
    if not raw:
        return ""
    known = PETLIBRO_FAULT_NAMES.get(fault_key(raw))
    if known:
        return known
    if re.fullmatch(r"[A-Z0-9_]+", raw):
        # Translators: A Petlibro fault the add-on does not know yet, named by
        # the device's code. {code} = e.g. PUMP_BLOCK_ERROR.
        return _("device code {code}").format(code=raw)
    # Translators: A Petlibro fault the add-on does not know yet, in the
    # words of the Petlibro cloud - they are English. {message} = its text.
    return _("“{message}” (Petlibro's text)").format(message=raw)


def fault_alert(value):
    """How an announcement says a fault: a known one by its name alone."""
    from .constants import PETLIBRO_FAULT_NAMES
    if not str(value or "").strip():
        # Translators: Petlibro announcement: the device reports a fault
        # without saying which.
        return _("reports a fault")
    if fault_key(value) in PETLIBRO_FAULT_NAMES:
        return fault_text(value)
    # Translators: Petlibro announcement of a fault the add-on does not know
    # yet. {detail} = its code or Petlibro's own text.
    return _("fault, {detail}").format(detail=fault_text(value))


def wrap_device(raw, api):
    """The wrapper for a device list entry: feeder, fountain or generic."""
    ident = str(raw.get("productIdentifier") or "").upper()
    category = str(raw.get("secondCategoryNameI18n") or "").lower()
    if ident.startswith("PLAF") or category == "feeder":
        return PetlibroFeeder(raw, api)
    if ident.startswith("PLWF") or category == "fountain":
        return PetlibroFountain(raw, api)
    return PetlibroDevice(raw, api)
