# -*- coding: utf-8 -*-
"""
Smart Home Control - Cozytouch/Atlantic cloud API.

Reverse-engineered protocol (source: gduteil/cozytouch, verified via spike):
  - Login:   POST /users/token  (form, Basic <CLIENT_ID>)            -> access_token
  - Devices: GET  /magellan/cozytouch/setupviewv2                    -> devices[]
  - Status:  GET  /magellan/capabilities/?deviceId=<id>             -> [{capabilityId,value}]
  - Control: POST /magellan/executions/writecapability               -> executionId (201)
             GET  /magellan/executions/<id>  (poll until state==3)

Deliberately follows the same structure as vesync_api.py: synchronous via
``requests``, _post helper with network retry, reauth callback on expired
token, get_credentials/set_credentials and update_device_status with a
report dict.

"""

import threading
import time
from logHandler import log

import requests

import addonHandler
try:
    addonHandler.initTranslation()
except Exception as e:
    log.debug(f"Ignorierter Fehler in <module>: {e}")
if "_" not in globals():  # Fallback, falls initTranslation() scheitert
    # Ohne diesen Fallback bleibt `_` undefiniert und der erste `_()`-Aufruf
    # wirft einen NameError mitten im Dialogaufbau statt beim Import.
    def _(s):
        return s

from .cozytouch_devices import CozytouchWaterHeater

API_BASE = "https://apis.groupe-atlantic.com"
# Public Basic-Auth value hard-coded in the Cozytouch client (base64 of
# client_id:secret) – from gduteil/cozytouch/const.py.
CLIENT_ID = "Q3RfMUpWeVRtSUxYOEllZkE3YVVOQmpGblpVYToyRWNORHpfZHkzNDJVSnFvMlo3cFNKTnZVdjBh"

API_TIMEOUT = 15
EXECUTION_POLL_TIMEOUT = 15  # max. seconds to wait for execution completion


class CozytouchAPI:
    """Atlantic Cozytouch cloud API handler."""

    def __init__(self):
        self._session = requests.Session()
        self._token = ""
        self._setup_id = None
        # Setup fields needed for the away-mode PUT (see set_away_mode).
        # Filled from the setupviewv2 response in get_devices().
        self._setup = {}
        self._reauth_callback = None
        self._reauth_lock = threading.Lock()
        # Some accounts/gateways reject writecapability on the away
        # capabilities (152/222) with HTTP 403 - the cloud maintains them
        # itself based on the setup-level absence object. Remember that so
        # we do not retrigger a pointless reauth+retry on every toggle.
        self._away_cap_write_forbidden = False
        # Network error state (for throttled logs, as with VeSync)
        self._network_error_logged = False

    # ---------------- Credentials / Reauth ----------------
    def is_authenticated(self):
        return bool(self._token)

    def set_credentials(self, token):
        self._token = token or ""

    def get_credentials(self):
        return {"token": self._token}

    def set_reauth_callback(self, callback):
        """The callback receives the API instance, should return True on success
        and update self._token internally (e.g. via calling login() again).
        """
        self._reauth_callback = callback

    # ---------------- HTTP helpers ----------------
    def _is_network_error(self, exc):
        return isinstance(exc, (requests.ConnectionError, requests.Timeout))

    def _headers(self, json_body=True):
        h = {"Authorization": f"Bearer {self._token}"}
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    def _request(self, method, path, headers=None, json_body=None, data=None,
                 timeout=API_TIMEOUT, _is_retry=False):
        """Authenticated request with one network retry and token reauth.

        Returns (status_code, parsed_or_text). 401/403 triggers the reauth
        callback – once – and then retries with a fresh token.
        """
        url = API_BASE + path
        if headers is None:
            headers = self._headers()
        try:
            resp = self._session.request(
                method, url, headers=headers, json=json_body, data=data,
                timeout=timeout,
            )
        except (requests.ConnectionError, requests.Timeout) as e:
            if _is_retry:
                raise
            log.debug(f"Cozytouch: Netzwerkfehler ({type(e).__name__}), 1x Retry in 1s...")
            time.sleep(1)
            resp = self._session.request(
                method, url, headers=headers, json=json_body, data=data,
                timeout=timeout,
            )

        # Token expired -> reauth once, then retry
        if resp.status_code in (401, 403) and not _is_retry and self._reauth_callback:
            if self._reauth_lock.acquire(blocking=False):
                try:
                    log.info(f"Cozytouch: Token abgelaufen (HTTP {resp.status_code}), Re-Auth...")
                    try:
                        ok = bool(self._reauth_callback(self))
                    except Exception as e:
                        log.warning(f"Cozytouch Re-Auth fehlgeschlagen: {e}")
                        ok = False
                finally:
                    self._reauth_lock.release()
                if not ok:
                    return resp.status_code, None
            else:
                # Another thread is currently doing a reauth - wait for it
                with self._reauth_lock:
                    pass
            # Retry with a fresh token (rebuild headers)
            return self._request(method, path, headers=self._headers(json_body=(json_body is not None)),
                                 json_body=json_body, data=data, timeout=timeout, _is_retry=True)

        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, resp.text

    # ---------------- Login ----------------
    def login(self, email, password):
        """Logs in and stores the bearer token.

        The password is not stored in self – it is only used for this
        request (the caller keeps it encrypted, see the plugin).
        """
        if not email or not password:
            # Translators: Validation error when email or password is missing.
            raise ValueError(_("E-Mail und Passwort erforderlich"))
        url = API_BASE + "/users/token"
        try:
            resp = self._session.post(
                url,
                data={
                    "grant_type": "password",
                    "scope": "openid",
                    "username": "GA-PRIVATEPERSON/" + email,
                    "password": password,
                },
                headers={
                    "Authorization": "Basic " + CLIENT_ID,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=API_TIMEOUT,
            )
        except (requests.ConnectionError, requests.Timeout) as e:
            # Translators: Error message on a Cozytouch connection problem.
            # {error} = technical error type.
            raise ConnectionError(_("Cozytouch Verbindungsfehler: {error}").format(
                error=type(e).__name__))

        try:
            token = resp.json()
        except ValueError:
            raise RuntimeError(_("Cozytouch: Ungültige Antwort beim Login"))

        if not isinstance(token, dict) or "access_token" not in token:
            err = token.get("error") if isinstance(token, dict) else None
            if err == "invalid_grant":
                raise ValueError(_("Cozytouch: E-Mail oder Passwort falsch"))
            raise RuntimeError(_("Cozytouch: Login fehlgeschlagen (HTTP {code})").format(
                code=resp.status_code))

        self._token = token["access_token"]
        log.info("Cozytouch: Login erfolgreich")
        return True

    # ---------------- Devices ----------------
    def get_devices(self):
        """Reads the device list (setupviewv2) and returns wrapper objects."""
        status, data = self._request("GET", "/magellan/cozytouch/setupviewv2")
        if not isinstance(data, list) or not data:
            log.warning(f"Cozytouch: Geräteliste unerwartet (HTTP {status})")
            return []
        setup = data[0]
        self._setup_id = setup.get("id")
        # Keep the setup fields required by the away-mode PUT
        # (/magellan/v2/setups/<id>). Mirrors gduteil/cozytouch hub.py.
        self._setup = {
            key: setup[key]
            for key in (
                "address", "area", "currency", "mainDHWEnergy",
                "mainHeatingEnergy", "name", "numberOfPersons",
                "numberOfRooms", "setupBuildingDate", "type",
            )
            if key in setup
        }
        devices = []
        for raw in setup.get("devices", []):
            wrapper = self._wrap_device(raw)
            if wrapper is not None:
                devices.append(wrapper)
        log.info(f"Cozytouch: {len(devices)} Gerät(e) gefunden")
        return devices

    def _wrap_device(self, raw):
        """Creates the matching device wrapper.

        Currently every device is treated as a hot water heat pump (the only
        known hardware: Austria Email Revolution Evo 3). When extending later, branch on
        modelId/productId here.
        """
        try:
            return CozytouchWaterHeater(raw, self)
        except Exception as e:
            log.warning(f"Cozytouch: Gerät konnte nicht eingelesen werden: {e}")
            return None

    def _fetch_capabilities(self, device_id):
        status, data = self._request(
            "GET", "/magellan/capabilities/?deviceId=" + str(device_id)
        )
        if isinstance(data, list):
            return data
        return None

    def update_device_status(self, devices):
        """Updates the capabilities of all given Cozytouch devices.

        Returns a report dict {devices_ok, devices_total} (like VeSync) which
        the background refresh evaluates for the platform status announcement.
        """
        ok_count = 0
        total = 0
        for dev in devices:
            if not getattr(dev, "is_cozytouch", False):
                continue
            total += 1
            try:
                caps = self._fetch_capabilities(dev.device_id)
                if caps is not None:
                    dev.apply_capabilities(caps)
                    dev.is_offline = False
                    ok_count += 1
                else:
                    dev.is_offline = True
            except Exception as e:
                log.debug(f"Cozytouch: Status-Update für {dev.name} fehlgeschlagen: {e}")
        return {"devices_ok": ok_count, "devices_total": total}

    # ---------------- Control ----------------
    @staticmethod
    def _extract_execution_id(resp):
        """Reads the executionId from the writecapability response.

        Depending on endpoint/firmware, the API returns the ID either as a
        bare value (int/str) or embedded in an object ({"execId": ...} or
        similar). Both forms are supported so the subsequent status polling
        builds the correct URL and valid executions are not incorrectly
        counted as failures.
        """
        if isinstance(resp, bool):
            return None
        if isinstance(resp, (int, str)):
            return resp
        if isinstance(resp, dict):
            for key in ("execId", "executionId", "id"):
                val = resp.get(key)
                if val is not None:
                    return val
        return None

    def set_capability(self, device_id, capability_id, value):
        """Writes a capability and waits for the execution to complete.

        Returns True if the execution was confirmed as completed (state==3).
        """
        ok, _status = self._set_capability_ex(device_id, capability_id, value)
        return ok

    def _set_capability_ex(self, device_id, capability_id, value, quiet=False):
        """Like set_capability, but returns (ok, http_status).

        ``quiet`` downgrades the failure log to DEBUG - used for the
        best-effort away-mode capability writes, where an HTTP 403 is
        expected on some accounts and must not alarm the user.
        """
        status, exec_resp = self._request(
            "POST", "/magellan/executions/writecapability",
            json_body={
                "capabilityId": int(capability_id),
                "deviceId": int(device_id),
                "value": str(value),
            },
        )
        if status != 201:
            _log = log.debug if quiet else log.warning
            _log(f"Cozytouch: writecapability fehlgeschlagen (HTTP {status})")
            return False, status
        exec_id = self._extract_execution_id(exec_resp)
        if exec_id is None:
            log.warning(
                f"Cozytouch: writecapability ohne verwertbare executionId "
                f"(HTTP {status}, Antworttyp {type(exec_resp).__name__})")
            return False, status

        # Poll for completion (state: 1=waiting, 2=running, 3=done).
        #
        # WICHTIG: Die Ausführungs-Meldung der Atlantic-Cloud ist NICHT
        # verlässlich. Beobachtet am realen Gerät: Beim Umstellen auf den
        # Zeitprogramm-Modus meldet die Cloud Status 4 ("fehlgeschlagen"),
        # obwohl das Gerät den Befehl übernimmt. Auch die Referenz-
        # Implementierung (gduteil/cozytouch) behandelt alles außer 3 als
        # Fehler - loggt ihn aber nur still, statt ihn dem Nutzer zu zeigen.
        # Ein falsches False hätte hier doppelte Folgen: Fehlerton trotz
        # erfolgreicher Umstellung UND die eigene Änderung würde nicht als
        # lokale Aktion registriert - der nächste Poll meldet sie dann
        # fälschlich als externe Änderung.
        # Daher gilt: Bei jedem unklaren Ergebnis (Status 4, unbekannte
        # Status-Werte, Timeout) wird der TATSÄCHLICHE Capability-Wert am
        # Gerät nachgeprüft - der ist die einzige verlässliche Wahrheit.
        deadline = time.time() + EXECUTION_POLL_TIMEOUT
        timed_out = True
        state = None
        while time.time() < deadline:
            st, exec_data = self._request(
                "GET", "/magellan/executions/" + str(exec_id)
            )
            state = exec_data.get("state") if isinstance(exec_data, dict) else None
            if state == 3:
                return True, status
            if state is not None and state not in (1, 2):
                # Cloud meldet Fehlschlag (z.B. Status 4) - nachprüfen.
                timed_out = False
                break
            time.sleep(1)

        # Kurz warten, bis der neue Wert in der Cloud sichtbar ist, dann den
        # echten Geräte-Zustand prüfen.
        time.sleep(1.5)
        verified = self._verify_capability_value(device_id, capability_id, value)
        if verified:
            log.debug(
                f"Cozytouch: Ausführung {exec_id} (Status {state}) - Wert wurde "
                f"trotzdem übernommen, gilt als Erfolg")
            return True, status
        if timed_out and verified is None:
            # Timeout UND Prüfung nicht möglich: Write wurde mit HTTP 201
            # angenommen - im Zweifel als Erfolg werten (Gerät ist ggf. nur
            # langsam), statt fälschlich einen Fehler zu melden.
            log.debug(
                f"Cozytouch: Ausführung {exec_id} unbestätigt, Prüfung nicht "
                f"möglich - Write angenommen (HTTP 201), gilt als Erfolg")
            return True, status
        log.warning(
            f"Cozytouch: Ausführung {exec_id} fehlgeschlagen "
            f"(Status {state}, Wert nicht übernommen)")
        return False, status

    def _verify_capability_value(self, device_id, capability_id, expected_value):
        """Prüft, ob eine Capability tatsächlich den erwarteten Wert trägt.

        Returns:
            True  - Wert wurde übernommen
            False - Wert weicht ab (Befehl wirklich nicht übernommen)
            None  - Prüfung nicht möglich (Capabilities nicht lesbar)
        """
        try:
            caps = self._fetch_capabilities(device_id)
        except Exception as e:
            log.debug(f"Cozytouch: Verifikation nicht möglich: {e}")
            return None
        if not caps:
            return None
        for cap in caps:
            if cap.get("capabilityId") == int(capability_id):
                actual = str(cap.get("value", "")).strip()
                expected = str(expected_value).strip()
                if actual == expected:
                    return True
                # Numerischer Vergleich als Fallback ("4" vs. "4.0000...")
                try:
                    return abs(float(actual) - float(expected)) < 0.01
                except (TypeError, ValueError):
                    return False
        return None

    def set_away_mode(self, device_id, on, start_ts=None, end_ts=None,
                      ts_capability_id=None, mode_capability_id=None):
        """Enables/disables away mode the way the Cozytouch backend expects it.

        A bare write of the away switch capability (152) is NOT enough - the
        backend ties away mode to the setup-level absence date range. The
        reference implementation (gduteil/cozytouch hub.set_away_mode_timestamps)
        performs three coordinated steps, replicated here:

          1. PUT /magellan/v2/setups/<setupId> with the stored setup fields and
             ``absence`` = {startDate, endDate} (enable) or {} (disable).
          2. writecapability <ts_capability_id> (152 -> 222) = "[start,end]"
             or "[0,0]".
          3. writecapability <mode_capability_id> (152) = "1" or "0".

        After enabling, the device may report the switch as "2" (= pending
        until startDate is reached); callers must treat any value != "0" as on.

        The setup PUT is the authoritative action: real-device testing showed
        that steps 2 and 3 can be rejected with HTTP 403 (the cloud maintains
        those capabilities itself), while the away mode still activates
        correctly through the PUT alone. The reference implementation never
        checks its capability writes, which hides the same rejection there.
        The capability writes are therefore best effort; after a 403 they are
        skipped on subsequent calls.

        Returns True when the setup PUT succeeded.
        """
        from .cozytouch_devices import CAP_AWAY, CAP_AWAY_TS
        if ts_capability_id is None:
            ts_capability_id = CAP_AWAY_TS
        if mode_capability_id is None:
            mode_capability_id = CAP_AWAY

        if self._setup_id is None:
            log.warning("Cozytouch: set_away_mode ohne Setup-ID (get_devices nie gelaufen?)")
            return False

        # 1. Setup-level absence range
        json_data = dict(self._setup)
        json_data["absence"] = (
            {"startDate": int(start_ts), "endDate": int(end_ts)}
            if on and start_ts and end_ts else {}
        )
        status, _resp = self._request(
            "PUT", "/magellan/v2/setups/" + str(self._setup_id),
            json_body=json_data,
        )
        if status not in (200, 204):
            log.warning(f"Cozytouch: Absence-PUT fehlgeschlagen (HTTP {status})")
            return False

        # 2. + 3. Capability writes - best effort only (see docstring).
        if not self._away_cap_write_forbidden:
            if on and start_ts and end_ts:
                ts_value = "[" + str(int(start_ts)) + "," + str(int(end_ts)) + "]"
            else:
                ts_value = "[0,0]"
            ok_ts, st_ts = self._set_capability_ex(
                device_id, ts_capability_id, ts_value, quiet=True)
            ok_mode, st_mode = self._set_capability_ex(
                device_id, mode_capability_id, "1" if on else "0", quiet=True)
            if 403 in (st_ts, st_mode):
                log.info(
                    "Cozytouch: writecapability für Abwesenheit nicht erlaubt "
                    "(HTTP 403) – Cloud pflegt die Werte selbst; künftige "
                    "Schreibversuche werden übersprungen.")
                self._away_cap_write_forbidden = True
            elif not (ok_ts and ok_mode):
                log.debug("Cozytouch: Abwesenheits-Capability-Write unbestätigt (unkritisch)")

        return True

    def logout(self):
        self._token = ""
        try:
            self._session.close()
        except Exception as e:
            log.debug(f"Ignorierter Fehler in logout: {e}")
