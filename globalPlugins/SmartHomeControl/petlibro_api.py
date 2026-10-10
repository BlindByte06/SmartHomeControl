# -*- coding: utf-8 -*-
"""
Smart Home Control - Petlibro cloud API (feeders and water fountains).

Protocol knowledge from the community integration jjjonesjr33/petlibro
(GPL-3, read on 2026-09-22) and checked against a read-only probe of a real
account (Air Smart Feeder PLAF108, Dockstream 2 Cordless PLWF116). Only the
facts are taken - endpoints, field names, error codes - the code is our own,
since the add-on is GPL-2.

  - Login:    POST /member/auth/login  (password as MD5 hex)  -> token
  - Requests: POST with JSON, the token in a "token" header
  - Answer:   {"code": 0, "msg": ..., "data": ...}; code 1009 = token gone

Things that are easy to get wrong:

  - ONE session per account: a login here signs the phone app out of the
    same account. The settings say so and recommend a second account that
    the devices are shared with.
  - Only one host is known (api.us.petlibro.com). A European account works
    there with the country "US" at login - measured from Austria.
  - The community says passwords over 18 characters are refused with 1102.
    Measured otherwise: a password of 19 signs in, and the app allows 20
    when a password is set. So there is no length check here; 1102 is
    reported as a wrong password.
  - A command is never sent twice. Reads get one retry after a network
    error; a feeding command that got lost on the way back may still have
    reached the feeder, and a second one would be a second meal.

Deliberately follows the structure of cozytouch_api.py: synchronous via
``requests``, one _post helper with the network retry and the re-login on
an expired token, get_credentials/set_credentials, and
update_device_status returning a report dict.
"""

import hashlib
import threading
import time
import uuid as _uuid
from logHandler import log

import requests

import addonHandler
try:
    addonHandler.initTranslation()
except Exception as e:
    log.debug(f"Ignored error during translation setup: {e}")
if "_" not in globals():  # fallback if initTranslation() fails
    # Without this fallback `_` stays undefined and the first `_()` call
    # raises a NameError mid-dialog instead of at import time.
    def _(s):
        return s

from .errors import CredentialsRejected
from .petlibro_devices import wrap_device

API_BASE = "https://api.us.petlibro.com"
# Public constants of the Android app - the same for every user.
APP_ID = 1
APP_SN = "c35772530d1041699c87fe62348507a8"
APP_VERSION = "1.3.45"
# Sent at login for every region; see the module docstring.
LOGIN_COUNTRY = "US"

API_TIMEOUT = 15
# A command waits for the device: the cloud forwards it and answers when the
# device has (or has not: code 2020) replied.
COMMAND_TIMEOUT = 30

CODE_OK = 0
CODE_NOT_LOGGED_IN = 1009
CODE_MEMBER_NOT_EXIST = 1101
CODE_ILLEGAL_PASSWORD = 1102
CODE_DEVICE_TIMEOUT = 2020
CODE_ALREADY_FEEDING = 2028


class PetlibroError(RuntimeError):
    """The cloud answered with an error code (or no usable answer)."""

    def __init__(self, path, code, msg=None):
        self.path = path
        self.code = code
        self.msg = msg
        super().__init__(f"Petlibro {path}: code {code} ({msg})")


def _local_timezone():
    """An IANA name for the local UTC offset ("Etc/GMT-1" for UTC+1).

    Windows does not give Python the IANA name of its zone. The cloud wants
    one at login; after the device list the devices' own zone is used (see
    get_devices), so this only has to be valid, not exact.
    """
    try:
        offset = time.localtime().tm_gmtoff or 0
        hours = int(round(offset / 3600.0))
    except Exception:
        hours = 0
    if hours == 0:
        return "UTC"
    # The Etc zones count the other way round: UTC+1 is "Etc/GMT-1".
    return f"Etc/GMT{-hours:+d}"


def _request_id():
    return _uuid.uuid4().hex


class PetlibroAPI:
    """Petlibro cloud API handler."""

    def __init__(self):
        self._session = requests.Session()
        self._token = ""
        self._timezone = _local_timezone()
        self._reauth_callback = None
        self._reauth_lock = threading.Lock()
        # The last device list, by serial: the dialog and the read-backs
        # take a single entry from it without asking for the whole list.
        self._list_cache = {}

    # ---------------- Credentials / Reauth ----------------
    def is_authenticated(self):
        return bool(self._token)

    def set_credentials(self, token):
        self._token = token or ""

    def get_credentials(self):
        return {"token": self._token}

    def set_reauth_callback(self, callback):
        """The callback receives the API instance and returns True when a
        fresh login succeeded (it calls login() again)."""
        self._reauth_callback = callback

    # ---------------- HTTP ----------------
    def _headers(self, with_token=True):
        headers = {
            "source": "ANDROID",
            "language": "EN",
            "timezone": self._timezone,
            "version": APP_VERSION,
            "Content-Type": "application/json",
        }
        if with_token and self._token:
            headers["token"] = self._token
        return headers

    def _post(self, path, body, timeout=API_TIMEOUT, retry_network=True,
              _is_retry=False):
        """POST with the token; returns the "data" part of the answer.

        ``retry_network`` must be False for commands: see the module
        docstring. The re-login on code 1009 is safe for commands too - the
        cloud refused the request without passing it on.
        """
        url = API_BASE + path
        try:
            resp = self._session.post(url, json=body, headers=self._headers(),
                                      timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as e:
            if not retry_network or _is_retry:
                raise
            log.debug(f"Petlibro: network error ({type(e).__name__}) on "
                      f"{path}, one retry in 1s")
            time.sleep(1)
            resp = self._session.post(url, json=body, headers=self._headers(),
                                      timeout=timeout)
        if resp.status_code != 200:
            raise PetlibroError(path, None, f"HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError:
            raise PetlibroError(path, None, "no JSON")
        if not isinstance(payload, dict):
            raise PetlibroError(path, None, "unexpected answer")
        code = payload.get("code")

        if code == CODE_NOT_LOGGED_IN and not _is_retry and self._reauth_callback:
            if self._reauth_lock.acquire(blocking=False):
                try:
                    log.info("Petlibro: token no longer valid, logging in again")
                    try:
                        ok = bool(self._reauth_callback(self))
                    except Exception as e:
                        log.warning(f"Petlibro re-auth failed: {e}")
                        ok = False
                finally:
                    self._reauth_lock.release()
                if not ok:
                    raise PetlibroError(path, code, payload.get("msg"))
            else:
                # Another thread is logging in right now - wait for it.
                with self._reauth_lock:
                    pass
            return self._post(path, body, timeout=timeout,
                              retry_network=retry_network, _is_retry=True)

        if code != CODE_OK:
            raise PetlibroError(path, code, payload.get("msg"))
        return payload.get("data")

    def _command(self, path, body, timeout=COMMAND_TIMEOUT):
        """A request that changes something - never repeated."""
        log.info(f"Petlibro: command {path}")
        return self._post(path, body, timeout=timeout, retry_network=False)

    # ---------------- Login ----------------
    def login(self, email, password):
        """Logs in and keeps the token. The password is not stored here."""
        if not email or not password:
            # Translators: Validation error when email or password is missing.
            raise ValueError(_("Email and password required"))
        body = {
            "appId": APP_ID,
            "appSn": APP_SN,
            "country": LOGIN_COUNTRY,
            "email": email,
            "password": hashlib.md5(password.encode("utf-8")).hexdigest(),
            "phoneBrand": "",
            "phoneSystemVersion": "",
            "timezone": self._timezone,
            "thirdId": None,
            "type": None,
        }
        try:
            resp = self._session.post(
                API_BASE + "/member/auth/login", json=body,
                headers=self._headers(with_token=False), timeout=API_TIMEOUT)
        except (requests.ConnectionError, requests.Timeout) as e:
            # Translators: Error message on a Petlibro connection problem.
            # {error} = technical error type.
            raise ConnectionError(_("Petlibro connection error: {error}").format(
                error=type(e).__name__))
        try:
            payload = resp.json()
        except ValueError:
            # Translators: Error message of the Petlibro sign-in.
            raise RuntimeError(_("Petlibro: invalid response during login"))
        code = payload.get("code") if isinstance(payload, dict) else None
        if code == CODE_ILLEGAL_PASSWORD:
            raise CredentialsRejected(
                # Translators: Error of the Petlibro sign-in: wrong password.
                _("Petlibro: incorrect email or password"))
        if code == CODE_MEMBER_NOT_EXIST:
            raise CredentialsRejected(
                # Translators: Error of the Petlibro sign-in: the account is
                # unknown. Accounts made on the website instead of in the app
                # are reported this way too.
                _("Petlibro: account not found - an account created in the "
                  "Petlibro app is needed"))
        data = payload.get("data") if isinstance(payload, dict) else None
        token = data.get("token") if isinstance(data, dict) else None
        if code != CODE_OK or not isinstance(token, str) or not token:
            # Translators: Error of the Petlibro sign-in with the code of the
            # cloud.
            raise RuntimeError(_("Petlibro: login failed (code {code})").format(
                code=code))
        self._token = token
        log.info("Petlibro: login successful")
        return True

    def logout(self):
        """Closes the HTTP session.

        Deliberately no /member/auth/logout: that would void the stored
        token, and the next start would have to log in again - which signs
        out every other session of the account once more.
        """
        try:
            self._session.close()
        except Exception as e:
            log.debug(f"Ignored error in logout: {e}")

    # ---------------- Devices ----------------
    def fetch_device_list(self):
        data = self._post("/device/device/list", {})
        entries = [d for d in (data or []) if isinstance(d, dict)
                   and d.get("deviceSn")]
        self._list_cache = {str(d["deviceSn"]): d for d in entries}
        # The devices know their zone; "today" in the answers follows the
        # zone the requests name, so it should be theirs.
        for entry in entries:
            zone = entry.get("timezone")
            if isinstance(zone, str) and "/" in zone:
                self._timezone = zone
                break
        return entries

    def fetch_list_entry(self, serial):
        self.fetch_device_list()
        return self._list_cache.get(str(serial))

    def get_devices(self):
        """Reads the device list and fills every device once."""
        entries = self.fetch_device_list()
        devices = []
        for entry in entries:
            try:
                devices.append(wrap_device(entry, self))
            except Exception as e:
                log.warning(f"Petlibro: could not read a device: {e}")
        log.info(f"Petlibro: {len(devices)} device(s) found: "
                 + ", ".join(d.model or "?" for d in devices))
        if devices:
            self.update_device_status(devices, full=True, list_entries=entries)
        return devices

    def fetch_real_info(self, serial):
        return self._post("/data/data/realInfo",
                          {"id": serial, "deviceSn": serial}) or {}

    def fetch_grain_status(self, serial):
        return self._post("/device/data/grainStatus",
                          {"id": serial, "deviceSn": serial}) or {}

    def fetch_today_plans(self, serial):
        return self._post("/device/feedingPlan/todayNew",
                          {"id": serial, "deviceSn": serial}) or {}

    def fetch_plans(self, serial):
        data = self._post("/device/feedingPlan/list",
                          {"id": serial, "deviceSn": serial})
        return data if isinstance(data, list) else []

    def fetch_records(self, serial, size=20, days=7):
        """Work records of the last ``days`` days, grouped by day.

        The cloud ignores a type filter, and ``size`` counts records, not
        days (measured) - so a small size is enough for "the newest ones".
        """
        now = int(time.time() * 1000)
        data = self._post("/device/workRecord/list", {
            "deviceSn": serial,
            "startTime": now - days * 86400 * 1000,
            "endTime": now,
            "size": int(size),
        })
        return data if isinstance(data, list) else []

    def fetch_drink_today(self, serial):
        return self._post("/data/deviceDrinkWater/todayDrinkData",
                          {"id": serial, "deviceSn": serial}) or {}

    def update_device_status(self, devices, full=False, list_entries=None):
        """Refreshes the given devices; returns a report like Cozytouch's.

        One device list for all of them, then per device its status and -
        for a feeder - the counts of the day and today's meals, for a
        fountain the intake of the day. The plan list and the work records
        are fetched when ``full`` or when they are older than their period;
        the scheduler asks for records when the count of the day moved.
        """
        answered, missing = [], []
        targets = [d for d in devices if getattr(d, "is_petlibro", False)]
        if not targets:
            return {"devices_ok": 0, "devices_total": 0,
                    "answered": answered, "missing": missing}
        try:
            entries = (list_entries if list_entries is not None
                       else self.fetch_device_list())
        except Exception as e:
            log.debug(f"Petlibro: device list failed: {e}")
            for dev in targets:
                missing.append((dev.unique_id, dev.name))
            return {"devices_ok": 0, "devices_total": len(targets),
                    "answered": answered, "missing": missing}
        by_serial = {str(e.get("deviceSn")): e for e in entries}

        ok = 0
        for dev in targets:
            who = (dev.unique_id, dev.name)
            entry = by_serial.get(dev.serial)
            if entry is None:
                # Removed from the account or the share withdrawn.
                dev.is_offline = True
                missing.append(who)
                continue
            dev.apply_list(entry)
            try:
                dev.apply_real(self.fetch_real_info(dev.serial))
                if getattr(dev, "is_feeder", False):
                    before = dev.today_count
                    dev.apply_grain_status(self.fetch_grain_status(dev.serial))
                    dev.apply_today(self.fetch_today_plans(dev.serial))
                    stale_plans = time.time() - dev.plans_loaded_at > 1800
                    if full or stale_plans:
                        dev.apply_plans(self.fetch_plans(dev.serial))
                    moved = before is not None and dev.today_count != before
                    self._maybe_fetch_records(dev, full, moved)
                elif getattr(dev, "is_fountain", False):
                    before = dev.drink_today_count
                    dev.apply_drink(self.fetch_drink_today(dev.serial))
                    moved = (before is not None
                             and dev.drink_today_count != before)
                    self._maybe_fetch_records(dev, full, moved)
                ok += 1
                answered.append(who)
            except Exception as e:
                missing.append(who)
                log.debug(f"Petlibro: status of {dev.name} failed: {e}")
        return {"devices_ok": ok, "devices_total": len(targets),
                "answered": answered, "missing": missing}

    def _maybe_fetch_records(self, dev, full, moved):
        """Fetches the work records when something moved, and twice after.

        After the day's counter moves, the record behind it may not be in
        the list yet - on 2026-09-23 the 12:30 meal was not, and the next
        regular fetch was ten minutes away.
        """
        if moved:
            dev.records_follow_up = 2
        stale = time.time() - dev.records_loaded_at > 600
        if full or moved or stale or dev.records_follow_up > 0:
            if not moved and dev.records_follow_up > 0:
                dev.records_follow_up -= 1
            dev.apply_records(self.fetch_records(dev.serial, size=20))

    def fetch_day_records(self, serial, day, size=100):
        """The work records of one day ("YYYY-MM-DD", local), newest first."""
        import datetime as _dt
        start = _dt.datetime.strptime(day, "%Y-%m-%d")
        end = start + _dt.timedelta(days=1)
        data = self._post("/device/workRecord/list", {
            "deviceSn": serial,
            "startTime": int(start.timestamp() * 1000),
            "endTime": min(int(end.timestamp() * 1000), int(time.time() * 1000)),
            "size": int(size),
        })
        return data if isinstance(data, list) else []

    # ---------------- Commands ----------------
    def manual_feed(self, serial, portions):
        return self._command("/device/device/manualFeeding", {
            "deviceSn": serial,
            "grainNum": int(portions),
            "requestId": _request_id(),
        })

    def set_feeding_plan_enabled(self, serial, enabled):
        return self._command("/device/setting/updateFeedingPlanSwitch", {
            "deviceSn": serial, "enable": bool(enabled)})

    def set_today_all(self, serial, enabled):
        """enabled=False skips every meal of today, True gives them back."""
        return self._command("/device/feedingPlan/enableTodayAll", {
            "deviceSn": serial, "enable": bool(enabled)})

    def set_today_single(self, serial, plan_id, enabled):
        """enabled=False skips one meal today, True gives it back."""
        return self._command("/device/feedingPlan/enableTodaySingle", {
            "deviceSn": serial, "planId": plan_id, "enable": bool(enabled)})

    def set_plan_enabled(self, serial, plan_id, enabled):
        return self._command("/device/feedingPlan/enable", {
            "deviceSn": serial, "planId": plan_id, "enable": bool(enabled)})

    @staticmethod
    def _plan_body(serial, plan, plan_id):
        return {
            "id": plan_id,
            "deviceSn": serial,
            "executionTime": plan["executionTime"],
            "repeatDay": plan.get("repeatDay") or "[]",
            "label": plan.get("label") or "",
            "enable": bool(plan.get("enable", True)),
            "enableAudio": bool(plan.get("enableAudio", False)),
            "audioTimes": int(plan.get("audioTimes") or 2),
            "grainNum": int(plan["grainNum"]),
            "petIds": [],
        }

    def add_plan(self, serial, plan):
        return self._command("/device/feedingPlan/add",
                             self._plan_body(serial, plan, 0))

    def update_plan(self, serial, plan):
        return self._command("/device/feedingPlan/update",
                             self._plan_body(serial, plan, plan["id"]))

    def delete_plan(self, serial, plan_id):
        return self._command("/device/feedingPlan/remove", {
            "deviceSn": serial, "planId": plan_id})

    def _reset(self, path, serial):
        return self._command(path, {
            "deviceSn": serial, "requestId": _request_id(), "timeout": 5000})

    def reset_desiccant(self, serial):
        return self._reset("/device/device/desiccantReset", serial)

    def reset_filter(self, serial):
        return self._reset("/device/device/filterReset", serial)

    def reset_cleaning(self, serial):
        return self._reset("/device/device/machineCleaningReset", serial)

    def set_light(self, serial, on):
        return self._command("/device/setting/updateLightingSetting", {
            "deviceSn": serial,
            "lightSwitch": bool(on),
            "lightAgingType": 1,
            "lightingStartTime": None,
            "lightingEndTime": None,
        })

    def set_low_water(self, serial, ml):
        return self._command("/device/setting/updateLowWaterSetting", {
            "deviceSn": serial, "lowWater": int(ml)})

    def set_radar(self, serial, mode):
        """Sets the sensor range of a fountain: 'sensor_near' or 'sensor_far'.

        "FarTrigger"/"NearTrigger" are what the Petlibro app sends for "far"
        and "near" (captured by the community from the app, PLWF116). A
        setting of the cloud: it answers at once, without waiting for the
        device.
        """
        return self._command("/device/setting/updateRadarSetting", {
            "deviceSn": serial,
            "radarSensingLevel": ("FarTrigger" if mode == "sensor_far"
                                  else "NearTrigger")})

    def set_water_mode(self, serial, mode, interval=None, duration=None,
                       sensing=None, currently_off=False):
        """Sets a fountain's water mode.

        'off' is its own switch (waterStopSwitch); every other mode first
        turns that switch back when the fountain is off. The sensor modes set
        the sensor range first and then the mode. The body carries the same
        fields as the app's request, the sensing duration included.
        """
        if mode == "off":
            return self._command("/device/device/waterModeSetting", {
                "deviceSn": serial, "waterStopSwitch": True})
        kind = {"constant": 0, "intermittent": 1,
                "sensor_near": 2, "sensor_far": 2}.get(mode)
        if kind is None:
            raise ValueError(f"unknown water mode {mode!r}")
        # A 2020 on one step does not stop the others: the sleeping fountain
        # takes them all when it wakes, and a step left out would leave it
        # switched off or in the old mode. The 2020 is raised at the end.
        late = []

        def step(call):
            try:
                call()
            except PetlibroError as e:
                if e.code != CODE_DEVICE_TIMEOUT:
                    raise
                late.append(e)

        if currently_off:
            step(lambda: self._command("/device/device/waterModeSetting", {
                "deviceSn": serial, "waterStopSwitch": False}))
        if kind == 2:
            step(lambda: self.set_radar(serial, mode))
        body = {
            "deviceSn": serial,
            "requestId": _request_id(),
            "useWaterType": kind,
            "useWaterInterval": interval,
            "useWaterDuration": duration,
        }
        if sensing is not None:
            body["sensingWaterDuration"] = sensing
        step(lambda: self._command("/device/device/waterModeSetting", body))
        if late:
            raise late[0]
        return None

    def set_sensing_duration(self, serial, seconds, interval=None,
                             duration=None):
        """How long water flows once the sensor noticed the pet, in seconds.

        Part of the pet-sensing water mode: the app sends it in the same
        request, with useWaterType 2. 45, 60, 75 or 90 seconds in the app;
        firmware 0.3.25 or later (Petlibro support, 2026-10-01). The field
        stood at 45 on the PLWF116 measured on 2026-09-23 - the app's default.
        Not the radar range: that stays as it is and is not sent again (a
        repeated range ran into 2020 once, see set_water_mode).
        """
        return self._command("/device/device/waterModeSetting", {
            "deviceSn": serial,
            "requestId": _request_id(),
            "useWaterType": 2,
            "useWaterInterval": interval,
            "useWaterDuration": duration,
            "sensingWaterDuration": int(seconds),
        })

    def fetch_drink_history(self, serial, day):
        """What a fountain registered on ``day`` ("YYYY-MM-DD"), per hour.

        The answer has 24 values each for the amount (ml), the number of
        drinks and their average duration (seconds), with the hours in
        "xdate". Measured on the PLWF116; single drinks are not offered by
        any endpoint known.
        """
        data = self._post("/data/deviceDrinkWater/history", {
            "deviceSn": serial, "dimension": "day", "dimensionParam": day})
        return data if isinstance(data, dict) else {}
