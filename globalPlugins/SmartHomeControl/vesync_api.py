# -*- coding: utf-8 -*-
"""
VeSync cloud API handler
Communicates with the VeSync cloud API (REST/JSON).

Supported devices:
  - Levoit Core 200S/300S/400S/500S/600S air purifiers (BypassV2 API)
  - Levoit tower fans (LTF-F422S series, BypassV2 API)

The implementation follows pyvesync but is standalone
(uses only requests, no asyncio/orjson/mashumaro dependencies).

"""

import concurrent.futures
import hashlib
import itertools
import platform
import re
import threading
import time
import uuid
from logHandler import log

# requests is bundled in the add-on's lib/ folder
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

from .vesync_devices import (
    VeSyncPurifier, VeSyncTowerFan, VESYNC_PURIFIER_TYPES, VESYNC_FAN_TYPES,
    resolve_device_config,
)
from .errors import CredentialsRejected


# ============================================================
# Constants (compatible with the VeSync app)
# ============================================================
API_BASE_URL_US = "https://smartapi.vesync.com"
API_BASE_URL_EU = "https://smartapi.vesync.eu"
NON_EU_COUNTRY_CODES = ["US", "CA", "MX", "JP"]

APP_VERSION = "5.6.60"
APP_ID = "eldodkfj"
CLIENT_TYPE = "vesyncApp"
PHONE_BRAND = "pyvesync"
PHONE_OS = "Android"
USER_TYPE = "1"
BYPASS_HEADER_UA = "okhttp/3.12.1"
DEFAULT_LANGUAGE = "en"
DEFAULT_TZ = "America/New_York"
API_TIMEOUT = 12
# Reduced timeout for the VeSync fast poll while the dialog is open. With a 4 s
# polling interval and several devices, 12 s per call would block the stop-
# event mechanism and the next tick - so wait only briefly here and do NOT
# retry on error.
API_TIMEOUT_FAST = 5
# TTL of the deviceList cache in seconds. Within this time
# ``_refresh_devicelist_state`` returns the last response without asking the
# cloud again - prevents double calls when the background loop and the fast
# poll trigger within 1-2 s of each other.
DEVICELIST_CACHE_TTL = 2.5
# Max. parallel bypassV2 calls per update_device_status tick.
BYPASS_CONCURRENCY = 4
# After how many consecutive detail failures a device is marked as "stale"
# (``_consecutive_status_failures``).
STALE_THRESHOLD = 3

# Generate TERMINAL_ID once per machine
TERMINAL_ID = "2" + uuid.uuid5(
    uuid.NAMESPACE_DNS, f"{uuid.getnode():x}-{platform.node() or ''}"
).hex


def _hash_password(password):
    """Creates the MD5 hash of the password (like the VeSync app)"""
    return hashlib.md5(password.encode("utf-8")).hexdigest()


# itertools.count is atomic (C implementation) - up to 4 parallel bypassV2
# workers take IDs here; the former mutable default argument with
# `counter[0] += 1` could hand out duplicate traceIds.
_trace_counter = itertools.count(1)


def _new_trace_id():
    """Generates a new trace ID for API calls"""
    return f"APP{TERMINAL_ID[-5:-1]}{int(time.time())}-{next(_trace_counter):05d}"


def _country_code_to_region(country_code):
    """Converts a country code into a region (US or EU)"""
    if (country_code or "US").upper() in NON_EU_COUNTRY_CODES:
        return "US"
    return "EU"


def _region_to_base_url(region):
    """Returns the API base URL for the region"""
    if region == "EU":
        return API_BASE_URL_EU
    return API_BASE_URL_US


# ============================================================
# VeSync API class
# ============================================================
class VeSyncAPI:
    """VeSync cloud API handler (synchronous, REST-based)"""

    def __init__(self, country_code="DE"):
        self.country_code = (country_code or "DE").upper()
        self.region = _country_code_to_region(self.country_code)
        self.base_url = _region_to_base_url(self.region)
        self.token = None
        self.account_id = None
        self.time_zone = DEFAULT_TZ
        self._devices = []

        # Requests session for connection reuse: without one every call
        # opens a new TLS connection - needless latency and handshake load
        # during the fast poll (up to 4 parallel bypassV2 calls every 4 s).
        # requests.Session is safe in practice for parallel .post() calls
        # from several threads (the urllib3 pool below is thread-safe); the
        # pool is sized to the parallelism of the update tick.
        self._session = requests.Session()
        _adapter = requests.adapters.HTTPAdapter(
            pool_connections=2, pool_maxsize=max(4, BYPASS_CONCURRENCY))
        self._session.mount("https://", _adapter)

        # Reauth callback: VeSync has no refresh token endpoint. When the API
        # responds with code=-11201022 / "token expired", _post calls this
        # callback, which typically triggers a password login. Setter:
        # ``set_reauth_callback``. The call is best effort - if it fails, _post
        # passes on the original error.
        self._reauth_callback = None
        # Lock instead of a boolean: several threads can arrive with an expired
        # token in parallel (fast poll + background loop + dialog action). Only
        # the first one triggers the reauth, the others wait on the lock and
        # retry afterwards with the fresh token.
        self._reauth_lock = threading.Lock()
        # In-memory cache of the device list response (for
        # ``_refresh_devicelist_state``). Prevents double calls when the
        # background loop and the fast poll trigger one after the other within
        # the TTL.
        self._devicelist_cache = None
        self._devicelist_cache_ts = 0.0
        self._devicelist_cache_lock = threading.Lock()

        # Network error deduplication (analogous to NetatmoAPI)
        self._last_network_error_time = 0
        self._network_error_count = 0
        self._NETWORK_ERROR_LOG_INTERVAL = 300

    # ----------------------------------------------------------
    # Helpers: headers and body
    # ----------------------------------------------------------
    @staticmethod
    def _bypass_headers():
        """Default headers for bypass V2 endpoints"""
        return {
            "Content-Type": "application/json; charset=UTF-8",
            "User-Agent": BYPASS_HEADER_UA,
        }

    def _common_body(self):
        """Common fields for all authenticated requests"""
        return {
            "acceptLanguage": DEFAULT_LANGUAGE,
            "appVersion": APP_VERSION,
            "phoneBrand": PHONE_BRAND,
            "phoneOS": PHONE_OS,
            "accountID": self.account_id or "",
            "token": self.token or "",
            "timeZone": self.time_zone,
            "userCountryCode": self.country_code,
            "debugMode": False,
            "traceId": _new_trace_id(),
            "terminalId": TERMINAL_ID,
        }

    # ----------------------------------------------------------
    # Network error logging (analogous to NetatmoAPI)
    # ----------------------------------------------------------
    def _is_network_error(self, exception):
        """Checks whether an error is transient (DNS, connection, timeout, HTTP 5xx)"""
        error_msg = str(exception).lower()
        network_indicators = [
            "failed to resolve", "getaddrinfo failed", "name resolution",
            "connection refused", "connection reset", "connection aborted",
            "no route to host", "network is unreachable", "nicht erreichbar",
            "max retries exceeded", "connectionerror", "timeout",
            "winerror 10065", "winerror 10060", "winerror 10061",
        ]
        if any(indicator in error_msg for indicator in network_indicators):
            return True
        return bool(re.search(r"\((?:http\s)?5\d{2}\)", error_msg))

    def _log_network_error(self, context, exception):
        """Logs network errors with deduplication"""
        now = time.time()
        if self._is_network_error(exception):
            self._network_error_count += 1
            if (now - self._last_network_error_time) > self._NETWORK_ERROR_LOG_INTERVAL:
                log.error(f"{context}: {exception}")
                self._last_network_error_time = now
            else:
                log.debug(f"{context} (retry #{self._network_error_count}): {exception}")
        else:
            log.error(f"{context}: {exception}")

    def _reset_network_error_state(self):
        """Resets the network error counters"""
        if self._network_error_count > 0:
            log.info(f"VeSync: network available again (after {self._network_error_count} failed attempts)")
        self._network_error_count = 0

    # ----------------------------------------------------------
    # HTTP helper methods
    # ----------------------------------------------------------
    # VeSync response codes that indicate an expired/invalid token. Source:
    # pyvesync (see vesync_api.py comment above). Used in _post for the
    # automatic re-login.
    _AUTH_EXPIRED_CODES = {-11201022, -11201021, -11201001, -11000086}

    def _post(self, endpoint, body, headers=None, timeout=API_TIMEOUT,
              _is_retry=False, fast=False):
        """Authenticated POST request against the VeSync API.

        Args:
            fast: True for calls from the VeSync fast poll. No network retry
                (so the stop event stays responsive) and the timeout default
                is ``API_TIMEOUT_FAST`` (5 s).

        Retry strategy:
          - In normal mode: 1 retry on ConnectionError/timeout (1 s pause).
            No retry in fast mode.
          - On an expired token (``_AUTH_EXPIRED_CODES``) the caller waits on
            the ``_reauth_lock`` and the first thread triggers the reauth;
            all threads then retry with the fresh token.
        """
        if fast and timeout == API_TIMEOUT:
            timeout = API_TIMEOUT_FAST
        url = self.base_url + endpoint
        if headers is None:
            headers = self._bypass_headers()

        try:
            resp = self._session.post(url, headers=headers, json=body, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as e:
            if _is_retry or fast:
                raise
            log.debug(f"VeSync: network error ({type(e).__name__}), one retry in 1 s...")
            time.sleep(1)
            resp = self._session.post(url, headers=headers, json=body, timeout=timeout)

        if resp.status_code != 200:
            # The status code is enough - `resp.text` may contain sensitive
            # fields and is not mirrored into the exception message.
            raise RuntimeError(_(
                "VeSync API HTTP {code}"
            ).format(code=resp.status_code))

        try:
            data = resp.json()
        except ValueError as e:
            raise RuntimeError(_("VeSync API: invalid JSON response: {error}").format(error=e))

        # Detect token expiry -> attempt reauth (thread-safe)
        code = data.get("code") if isinstance(data, dict) else None
        if (code in self._AUTH_EXPIRED_CODES
                and not _is_retry
                and self._reauth_callback):
            # acquire (blocking=False) reveals whether another thread is
            # already doing the reauth. If so, we wait on the lock and retry
            # afterwards with the token that is fresh by then.
            already_reauthing = not self._reauth_lock.acquire(blocking=False)
            if already_reauthing:
                # Wait for the running reauth (lock acquire blocks), then
                # release the lock immediately and retry with the fresh token.
                with self._reauth_lock:
                    pass
                log.debug("VeSync: reauth happened in parallel - retrying with the new token")
            else:
                try:
                    log.info(f"VeSync: token expired (code={code}), trying re-auth...")
                    try:
                        ok = bool(self._reauth_callback(self))
                    except Exception as e:
                        log.warning(f"VeSync re-auth failed: {e}")
                        ok = False
                    if not ok:
                        return data
                finally:
                    self._reauth_lock.release()
            # Replace the token fields in the body with fresh ones (best
            # effort). IMPORTANT: work on a copy - the caller may hold a
            # reference to the same dict (e.g. a reused request body) and must
            # not see tokens changed by our retry.
            retry_body = body
            if isinstance(body, dict):
                retry_body = dict(body)
                if "token" in retry_body and self.token:
                    retry_body["token"] = self.token
                if "accountID" in retry_body and self.account_id:
                    retry_body["accountID"] = self.account_id
            return self._post(endpoint, retry_body, headers=headers, timeout=timeout,
                              _is_retry=True, fast=fast)

        return data

    def set_reauth_callback(self, callback):
        """Registers a callback for re-authentication on token expiry.

        The callback receives the ``VeSyncAPI`` instance and should return
        ``True`` on success and update ``self.token`` / ``self.account_id``
        (e.g. by calling ``login(email, password)`` again).
        """
        self._reauth_callback = callback

    # ----------------------------------------------------------
    # Authentication
    # ----------------------------------------------------------
    def is_authenticated(self):
        """Checks whether a valid token is present"""
        return bool(self.token and self.account_id)

    def set_credentials(self, token, account_id, country_code=None, region=None):
        """Sets existing credentials (e.g. from the saved config)"""
        self.token = token
        self.account_id = account_id
        if country_code:
            self.country_code = country_code.upper()
        if region:
            self.region = region
        else:
            self.region = _country_code_to_region(self.country_code)
        self.base_url = _region_to_base_url(self.region)

    def get_credentials(self):
        """Returns the current credentials (for saving)"""
        return {
            "token": self.token or "",
            "account_id": self.account_id or "",
            "country_code": self.country_code,
            "region": self.region,
        }

    def login(self, email, password):
        """
        Logs in to the VeSync cloud (two-step login).

        Step 1: authByPWDOrOTM -> returns authorizeCode
        Step 2: loginByAuthorizeCode4Vesync -> returns token + accountID

        Args:
            email: VeSync account email
            password: account password (sent only as an MD5 hash)
        """
        if not email or not password:
            # Translators: Validation error during VeSync login.
            raise ValueError(_("Email and password required"))

        log.info(f"VeSync API: starting login (region: {self.region})...")

        # ---- Step 1: authByPWDOrOTM ----
        auth_body = {
            "acceptLanguage": DEFAULT_LANGUAGE,
            "accountID": "",
            "appID": APP_ID,
            "sourceAppID": APP_ID,
            "authProtocolType": "generic",
            "clientInfo": PHONE_BRAND,
            "clientType": CLIENT_TYPE,
            "clientVersion": f"VeSync {APP_VERSION}",
            "debugMode": False,
            "email": email,
            "method": "authByPWDOrOTM",
            "osInfo": PHONE_OS,
            "password": _hash_password(password),
            "terminalId": TERMINAL_ID,
            "timeZone": self.time_zone,
            "token": "",
            "traceId": _new_trace_id(),
            "userCountryCode": self.country_code,
        }

        try:
            auth_resp = self._post(
                "/globalPlatform/api/accountAuth/v1/authByPWDOrOTM",
                auth_body,
            )
        finally:
            password = None
            del password

        if auth_resp.get("code") != 0:
            # Translators: Placeholder when VeSync does not provide an error
            # text.
            msg = auth_resp.get("msg") or _("Unknown error")
            # This is the step that checks the password, so the interface may
            # offer to enter it again - see errors.CredentialsRejected.
            # Translators: VeSync login error, with the server message as
            # {msg}.
            raise CredentialsRejected(
                _("VeSync login failed: {msg}").format(msg=msg))

        result = auth_resp.get("result") or {}
        authorize_code = result.get("authorizeCode")
        if not authorize_code:
            # Translators: Login error when VeSync does not return an
            # authorization code.
            raise RuntimeError(_(
                "VeSync login failed: no authorizeCode received"))
        self.account_id = result.get("accountID", "")

        # ---- Step 2: loginByAuthorizeCode4Vesync ----
        self._exchange_authorize_code(authorize_code)
        log.info("VeSync API: login successful")
        return True

    def _exchange_authorize_code(self, authorize_code, biz_token=None):
        """Exchanges the authorizeCode for an access token"""
        login_body = {
            "acceptLanguage": DEFAULT_LANGUAGE,
            "accountID": "",
            "authorizeCode": authorize_code,
            "clientInfo": PHONE_BRAND,
            "clientType": CLIENT_TYPE,
            "clientVersion": f"VeSync {APP_VERSION}",
            "debugMode": False,
            "emailSubscriptions": False,
            "method": "loginByAuthorizeCode4Vesync",
            "osInfo": PHONE_OS,
            "terminalId": TERMINAL_ID,
            "timeZone": self.time_zone,
            "token": "",
            "userCountryCode": self.country_code,
            "traceId": _new_trace_id(),
        }
        if biz_token:
            login_body["bizToken"] = biz_token
            login_body["regionChange"] = "lastRegion"

        login_resp = self._post(
            "/user/api/accountManage/v1/loginByAuthorizeCode4Vesync",
            login_body,
        )

        code = login_resp.get("code")
        if code != 0:
            # Cross-region error? -> retry with a different region
            result = login_resp.get("result") or {}
            cross_region_token = result.get("bizToken")
            new_country = result.get("countryCode")
            new_region = result.get("currentRegion")
            if cross_region_token and new_region:
                log.info(f"VeSync: cross-region login - switching to {new_region}")
                self.country_code = (new_country or self.country_code).upper()
                self.region = new_region.upper()
                self.base_url = _region_to_base_url(self.region)
                return self._exchange_authorize_code(authorize_code, cross_region_token)
            msg = login_resp.get("msg") or _("Unknown error")
            # Translators: Error in the second step of the VeSync login.
            raise RuntimeError(_("VeSync login failed: {msg}").format(msg=msg))

        result = login_resp.get("result") or {}
        self.token = result.get("token")
        self.account_id = result.get("accountID", self.account_id)
        country_code = result.get("countryCode")
        if country_code:
            self.country_code = country_code.upper()
        current_region = result.get("currentRegion")
        if current_region:
            self.region = current_region.upper()
            self.base_url = _region_to_base_url(self.region)

    # ----------------------------------------------------------
    # Device list
    # ----------------------------------------------------------
    def get_devices(self):
        """Fetches all supported VeSync devices and returns wrapper objects"""
        if not self.is_authenticated():
            # Translators: Error message when a VeSync API call happens before
            # the user has logged in.
            raise RuntimeError(_("Not logged in"))

        log.debug("VeSync API: fetching the device list...")

        list_body = {
            "acceptLanguage": DEFAULT_LANGUAGE,
            "accountID": self.account_id,
            "appVersion": APP_VERSION,
            "method": "devices",
            "pageNo": 1,
            "pageSize": 100,
            "phoneBrand": PHONE_BRAND,
            "phoneOS": PHONE_OS,
            "timeZone": self.time_zone,
            "token": self.token,
            "traceId": _new_trace_id(),
        }

        try:
            resp = self._post(
                "/cloud/v1/deviceManaged/devices",
                list_body,
            )
        except Exception as e:
            self._log_network_error("VeSync device list error", e)
            raise

        if resp.get("code") != 0:
            msg = resp.get("msg") or _("Unknown error")
            # Translators: Error while fetching the VeSync device list.
            raise RuntimeError(_("VeSync device list failed: {msg}").format(msg=msg))

        result = resp.get("result") or {}
        raw_devices = result.get("list", []) or []

        devices = []
        for raw in raw_devices:
            wrapper = self._wrap_device(raw)
            if wrapper is not None:
                devices.append(wrapper)

        # Fetch the status for all devices (parallel-ish, sequential)
        for dev in devices:
            try:
                self._update_device_details(dev)
            except Exception as e:
                log.debug(f"VeSync: status update for {dev.name} failed: {e}")

        self._reset_network_error_state()
        self._devices = devices
        log.info(f"VeSync API: {len(devices)} device(s) found")
        return devices

    def _wrap_device(self, raw):
        """Creates a matching wrapper object for a raw device"""
        device_type = (raw.get("deviceType") or "").strip()
        if not device_type:
            return None

        # Lookup tolerates regional variants: LAP-C201S-WEU falls back to the
        # profile of another LAP-C201S-* entry (see resolve_device_config).
        config, _matched = resolve_device_config(device_type, VESYNC_PURIFIER_TYPES)
        if config is not None:
            return VeSyncPurifier(raw, self, config)

        config, _matched = resolve_device_config(device_type, VESYNC_FAN_TYPES)
        if config is not None:
            return VeSyncTowerFan(raw, self, config)

        log.debug(f"VeSync: device type {device_type} is not supported")
        return None

    # ----------------------------------------------------------
    # BypassV2 calls
    # ----------------------------------------------------------
    def call_bypass_v2(self, device, payload_method, data=None, fast=False):
        """
        Sends a BypassV2 call to a device.

        Args:
            device: VeSync wrapper (must have cid, configModule, deviceRegion)
            payload_method: e.g. 'getPurifierStatus', 'setSwitch', ...
            data: inner data (dict or None)
            fast: True for calls from the fast poll (no retry, short timeout).

        Returns:
            dict: API response
        """
        if not self.is_authenticated():
            # Translators: Error message when a VeSync API call happens before
            # the user has logged in.
            raise RuntimeError(_("Not logged in"))

        body = self._common_body()
        body.update({
            "method": "bypassV2",
            "cid": device.cid,
            "configModule": device.config_module or "",
            "configModel": device.config_module or "",
            "deviceId": device.cid,
            "payload": {
                "method": payload_method,
                "source": "APP",
                "data": data or {},
            },
        })

        try:
            return self._post(
                "/cloud/v2/deviceManaged/bypassV2",
                body,
                fast=fast,
            )
        except Exception as e:
            self._log_network_error(f"VeSync bypassV2 ({payload_method}) error", e)
            raise

    def _update_device_details(self, device, fast=False):
        """Fetches fresh status data for a single device.

        Updates ``device._consecutive_status_failures``: reset the counter on
        success, increment on failure. The caller (``update_device_status``)
        decides what happens on persistent failure (mark the device stale).
        """
        try:
            payload_method = device.get_status_method()
            resp = self.call_bypass_v2(device, payload_method, fast=fast)
            device.apply_status_response(resp)
            device._consecutive_status_failures = 0
            return True
        except Exception as e:
            log.debug(f"VeSync: details for {device.name} not retrievable: {e}")
            device._consecutive_status_failures = (
                getattr(device, '_consecutive_status_failures', 0) + 1
            )
            return False

    # ----------------------------------------------------------
    # Status update for all devices (for the background refresh)
    # ----------------------------------------------------------
    def update_device_status(self, devices, fast=False):
        """Updates the status of the given VeSync devices.

        Before the expensive ``bypassV2`` detail calls run, the VeSync device
        list (``/cloud/v1/deviceManaged/devices``) is queried first and each
        wrapper's ``deviceStatus`` (on/off) and ``connectionStatus`` (online/
        offline) are updated from this fresher source. The cloud caches
        ``bypassV2`` responses so aggressively that external toggles from the
        Levoit app do not show through even after minutes - the device list,
        however, is serialized from the current cloud state on every call. If
        the user switches in the app, we see it here ~1 second later instead
        of only after several minutes.

        Args:
            fast: True for calls from the fast poll - enables short timeouts,
                the deviceList TTL cache and no network retry.

        Returns:
            dict with health report: ``{'devicelist_ok': bool, 'devices_ok': int,
            'devices_failed': int}``. The caller uses this for the platform
            status detection (``_announce_platform_state``) and for the stale
            marking.
        """
        if not self.is_authenticated():
            # Translators: Error message when a VeSync API call happens before
            # the user has logged in.
            raise RuntimeError(_("Not logged in"))

        devicelist_ok = True
        # 1) Query the device list (fresh cloud snapshot, with TTL cache)
        try:
            self._refresh_devicelist_state(devices, fast=fast)
        except Exception as e:
            devicelist_ok = False
            log.debug(f"VeSync: could not refresh the device list: {e}")

        # 2) Detail status in parallel via bypassV2 (mode/level/sensors)
        vesync_devs = [d for d in devices if getattr(d, "is_vesync", False)]
        ok_count = 0
        fail_count = 0
        if vesync_devs:
            # ThreadPoolExecutor per tick: low overhead cost, clean cleanup via
            # context manager, no persistent worker thread.
            workers = min(BYPASS_CONCURRENCY, len(vesync_devs))
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="VeSyncBypassV2",
            ) as ex:
                futures = {
                    ex.submit(self._update_device_details, dev, fast): dev
                    for dev in vesync_devs
                }
                for fut in concurrent.futures.as_completed(futures):
                    try:
                        success = fut.result()
                    except Exception:
                        success = False
                    if success:
                        ok_count += 1
                    else:
                        fail_count += 1

            # Stale marking: devices that have not delivered fresh data for
            # ``STALE_THRESHOLD`` polls in a row get a ``_status_stale=True``
            # flag. The dialog can display that.
            for dev in vesync_devs:
                fails = getattr(dev, '_consecutive_status_failures', 0)
                dev._status_stale = (fails >= STALE_THRESHOLD)

        return {
            'devicelist_ok': devicelist_ok,
            'devices_ok': ok_count,
            'devices_failed': fail_count,
        }

    def _fetch_devicelist_raw(self, fast=False):
        """Returns the raw device list entries (with a short-TTL cache).

        For several calls within ``DEVICELIST_CACHE_TTL`` seconds the cached
        response is returned - this prevents double calls when the background
        loop (30 s) and the fast poll (4 s) overlap in time, or when the
        initial refresh runs shortly after a regular tick.
        """
        with self._devicelist_cache_lock:
            now = time.time()
            if (self._devicelist_cache is not None
                    and (now - self._devicelist_cache_ts) < DEVICELIST_CACHE_TTL):
                return self._devicelist_cache
        list_body = {
            "acceptLanguage": DEFAULT_LANGUAGE,
            "accountID": self.account_id,
            "appVersion": APP_VERSION,
            "method": "devices",
            "pageNo": 1,
            "pageSize": 100,
            "phoneBrand": PHONE_BRAND,
            "phoneOS": PHONE_OS,
            "timeZone": self.time_zone,
            "token": self.token,
            "traceId": _new_trace_id(),
        }
        resp = self._post("/cloud/v1/deviceManaged/devices", list_body, fast=fast)
        if resp.get("code") != 0:
            log.debug(f"VeSync device list error: {resp.get('msg')}")
            return []
        raw = (resp.get("result") or {}).get("list") or []
        with self._devicelist_cache_lock:
            self._devicelist_cache = raw
            self._devicelist_cache_ts = time.time()
        return raw

    def _refresh_devicelist_state(self, devices, fast=False):
        """Updates is_on/is_offline of the wrappers from the device list.

        The device list provides ``deviceStatus`` ('on'/'off') and
        ``connectionStatus`` ('online'/'offline') per device. Unlike the
        per-device ``bypassV2`` calls, this endpoint does not appear to be
        cached server-side - it is therefore our primary source for detecting
        external switching (Levoit app, physical controls) promptly.
        """
        raw_devices = self._fetch_devicelist_raw(fast=fast)
        if not raw_devices:
            return
        # Map cid -> raw entry for fast lookups
        by_cid = {}
        for raw in raw_devices:
            cid = raw.get("cid")
            if cid:
                by_cid[cid] = raw
        import time as _time
        # Protection window: if the user just switched the device themselves,
        # the cloud device list may still return the old status. In that case
        # we respect the optimistic local ``_is_on`` display for a few seconds
        # so the user does not see the old value jump back right after their
        # action.
        local_toggle_window = 8.0
        now_ts = _time.time()
        for dev in devices:
            if not getattr(dev, "is_vesync", False):
                continue
            raw = by_cid.get(dev.cid)
            if not raw:
                continue
            # Take over fresh mode/level/air-quality values from the extension
            # field of the device list (for Core purifiers). Unlike the
            # bypassV2 detail response, the device list is not cached server-
            # side, so external changes show up here within ~1 s. For devices
            # without extension (e.g. tower fan) this is a no-op.
            try:
                dev.apply_devicelist_extension(raw)
            except Exception as e:
                log.debug(f"VeSync: parsing the extension for {dev.name} failed: {e}")
            # IMPORTANT: some devices (e.g. Levoit tower fans) provide NO
            # deviceStatus/connectionStatus in the device list - the fields are
            # simply ``None`` there. An empty string would not equal ``"on"``,
            # so the device would incorrectly be interpreted as "off".
            # Therefore: only take over the fields when they are actually
            # populated. Otherwise leave ``_is_on``/``is_offline`` untouched -
            # the state then comes from the bypassV2 heuristic (e.g.
            # screenState evaluation of the tower fan).
            raw_device_status = raw.get("deviceStatus")
            raw_connection_status = raw.get("connectionStatus")
            # Some devices (e.g. Levoit tower fans) provide NO
            # deviceStatus/connectionStatus in the device list. Only take over
            # the fields when they are actually populated. Otherwise
            # ``_is_on``/``is_offline`` stays from the bypassV2
            # response/heuristic.
            if not raw_device_status or not raw_connection_status:
                continue
            old_is_on = getattr(dev, '_is_on', None)
            old_is_offline = getattr(dev, 'is_offline', None)
            connection_status = raw_connection_status.lower()
            device_status = raw_device_status.lower()
            new_is_offline = (connection_status == "offline")
            new_is_on = (device_status == "on")
            last_toggle = getattr(dev, '_last_local_toggle_ts', 0.0)
            in_local_window = (now_ts - last_toggle) < local_toggle_window
            if in_local_window and old_is_on != new_is_on:
                log.debug(
                    f"VeSync device list: {dev.name} is_on={new_is_on} "
                    f"(still in the guard window after a local toggle, ignored)"
                )
                # Still update is_offline (connection loss is independent of
                # the toggle action).
                dev.is_offline = new_is_offline
                continue
            dev.is_offline = new_is_offline
            dev._is_on = new_is_on
            if old_is_on != new_is_on or old_is_offline != new_is_offline:
                log.info(
                    f"VeSync device list: {dev.name} "
                    f"is_on {old_is_on}→{new_is_on}, "
                    f"is_offline {old_is_offline}→{new_is_offline} "
                    f"(deviceStatus={device_status!r}, connectionStatus={connection_status!r})"
                )

    # ----------------------------------------------------------
    # Convenience methods for the plugin
    # ----------------------------------------------------------
    def _find(self, uuid_str, expected_type=None):
        """Finds a cached device by its UUID.

        Args:
            uuid_str: UUID of the device (format 'vesync_<cid>')
            expected_type: optional - class tuple
        """
        device = next((d for d in self._devices if d.uuid == uuid_str), None)
        if device is None:
            # Translators: Error when a VeSync device can no longer be found in
            # the API cache via its UUID.
            raise ValueError(_("VeSync device not found"))
        if expected_type is not None and not isinstance(device, expected_type):
            # Translators: Error when a VeSync action is to be executed on a
            # device of the wrong type (e.g. a purifier action on a tower fan).
            raise ValueError(_("VeSync device has the wrong type"))
        return device

    def set_device_state(self, uuid_str, state):
        """Switches a VeSync device on or off."""
        return self._find(uuid_str).toggle_switch(state)

    # ---- Air purifiers ----
    def set_purifier_mode(self, uuid_str, mode):
        """Sets the mode of an air purifier (auto/manual/sleep)."""
        return self._find(uuid_str, (VeSyncPurifier,)).set_mode(mode)

    def set_purifier_fan_speed(self, uuid_str, speed):
        """Sets the fan level of an air purifier (manual mode)."""
        return self._find(uuid_str, (VeSyncPurifier,)).set_fan_speed(speed)

    def set_purifier_display(self, uuid_str, on):
        """Toggles the display of an air purifier."""
        return self._find(uuid_str, (VeSyncPurifier,)).toggle_display(on)

    def set_purifier_child_lock(self, uuid_str, on):
        """Toggles the child lock of an air purifier."""
        return self._find(uuid_str, (VeSyncPurifier,)).toggle_child_lock(on)

    def set_purifier_nightlight(self, uuid_str, mode):
        """Sets the night light mode (on/off/dim)."""
        return self._find(uuid_str, (VeSyncPurifier,)).set_nightlight_mode(mode)

    def set_purifier_auto_preference(self, uuid_str, preference, room_size=None):
        """Sets the auto profile (default/efficient/quiet)."""
        return self._find(uuid_str, (VeSyncPurifier,)).set_auto_preference(preference, room_size)

    def reset_purifier_filter(self, uuid_str):
        """Resets the filter status to 100%."""
        return self._find(uuid_str, (VeSyncPurifier,)).reset_filter()

    # ---- Tower fans ----
    def set_fan_mode(self, uuid_str, mode):
        """Sets the mode of a tower fan."""
        return self._find(uuid_str, (VeSyncTowerFan,)).set_mode(mode)

    def set_fan_speed(self, uuid_str, speed):
        """Sets the fan level of a tower fan."""
        return self._find(uuid_str, (VeSyncTowerFan,)).set_fan_speed(speed)

    def set_fan_oscillation(self, uuid_str, on):
        """Toggles the oscillation of a tower fan."""
        return self._find(uuid_str, (VeSyncTowerFan,)).toggle_oscillation(on)

    def set_fan_mute(self, uuid_str, on):
        """Toggles the mute of a tower fan."""
        return self._find(uuid_str, (VeSyncTowerFan,)).toggle_mute(on)

    def set_fan_display(self, uuid_str, on):
        """Toggles the display of a tower fan."""
        return self._find(uuid_str, (VeSyncTowerFan,)).toggle_display(on)

    def logout(self):
        """Discards the token and ends the session"""
        log.info("VeSync API: Logout")
        self.token = None
        self.account_id = None
        try:
            self._session.close()
        except Exception as e:
            log.debug(f"Ignored error while closing the session: {e}")
