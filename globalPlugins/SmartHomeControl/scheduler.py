# -*- coding: utf-8 -*-
"""
Smart Home Control - Polling-Scheduler und Plattform-Refresh
Ausgelagert aus __init__.py (Modul-Aufteilung, Verhalten unverändert).
"""

import threading
import time

import wx
import ui
from logHandler import log

import addonHandler
try:
    addonHandler.initTranslation()
except Exception as e:
    log.debug(f"initTranslation fehlgeschlagen: {e}")
if "_" not in globals():  # Fallback, falls initTranslation() scheitert
    # Ohne diesen Fallback bleibt `_` undefiniert und der erste `_()`-Aufruf
    # wirft einen NameError mitten im Dialogaufbau statt beim Import.
    def _(s):
        return s

from .platform_utils import platform_of, PLATFORM_LABELS
from .constants import (
    BACKGROUND_REFRESH_INTERVAL, SCHEDULER_TICK, SCHEDULER_MAX_SLEEP,
    PLATFORM_INTERVALS,
)


class _SchedulerMixin:
    """Hintergrund-Scheduler: pollt alle Plattformen und meldet Statuswechsel."""

    def request_immediate_poll(self):
        """Fordert eine sofortige Abfrage aller Plattformen an.

        Wird beim Öffnen des Geräte-Dialogs aufgerufen. Neben dem Flag wird
        ``_stop_event`` gesetzt, damit der schlafende Scheduler SOFORT
        aufwacht - er schläft nämlich bis zur nächsten fälligen Abfrage und
        nicht mehr im Sekundentakt. ``_stop_event`` dient dabei doppelt: als
        Stopp- UND als Wecksignal. Unterschieden wird am Flag
        ``_background_refresh_running``, das ``_stop_background_refresh()``
        VOR dem ``set()`` auf False setzt - ein Weckruf lässt es unangetastet.
        """
        self._force_poll = True
        self._stop_event.set()

    def _scheduler_wait(self, timeout):
        """Wartet ``timeout`` Sekunden. True = die Schleife soll enden.

        Gegenstück zu ``request_immediate_poll``: wird das Event gesetzt,
        während ``_background_refresh_running`` noch True ist, war es ein
        Weckruf - das Event wird zurückgesetzt und weitergearbeitet.
        """
        if not self._stop_event.wait(timeout):
            return False  # regulärer Zeitablauf
        if not self._background_refresh_running:
            return True   # echter Stopp
        # Weckruf: Event zurücksetzen und weiterlaufen. Kommt zwischen
        # Prüfung und clear() doch ein Stopp, greift die while-Bedingung am
        # Schleifenkopf, weil dort das Flag geprüft wird.
        self._stop_event.clear()
        return False

    @staticmethod
    def _sleep_until_next_due(next_due, active_names):
        """Schlafdauer bis zur nächsten fälligen Abfrage (gedeckelt)."""
        due = [next_due[n] for n in active_names if n in next_due]
        if not due:
            return SCHEDULER_MAX_SLEEP
        remaining = min(due) - time.time()
        return max(SCHEDULER_TICK, min(remaining, SCHEDULER_MAX_SLEEP))

    def _announce_platform_state(self, platform_name, ok, attempted):
        """Announces platform status changes once (connected <-> disconnected).

        Called by the background loop after every refresh attempt.
        Speaks ONLY on the transition, not on repeated failures/successes.

        Args:
            platform_name: 'meross', 'netatmo', 'vesync' or 'cozytouch'
            ok: True if the current refresh succeeded
            attempted: True if the refresh was attempted at all
        """
        if not attempted:
            return  # do not change the status when not checked (e.g. skipped)

        attr = f"_{platform_name}_connected"
        prev = getattr(self, attr)

        if prev is None:
            # First initialization after login: set the status without an
            # announcement
            setattr(self, attr, ok)
            return

        if prev == ok:
            return  # no change

        setattr(self, attr, ok)
        # Platform names are brand names -> do not translate.
        label = PLATFORM_LABELS.get(platform_name, platform_name)
        if ok:
            # Translators: Announced when a smart home platform is reachable
            # again after a connection loss. {platform} is
            # Meross/Netatmo/VeSync.
            msg = _("{platform} wieder verbunden").format(platform=label)
            log.info(f"Plattform-Status: {label} wieder verbunden")
        else:
            # Translators: Announced when a smart home platform is temporarily
            # unreachable.
            msg = _("{platform} vorübergehend nicht erreichbar").format(platform=label)
            log.warning(f"Plattform-Status: {label} nicht erreichbar")
        wx.CallAfter(ui.message, msg)

    def _start_background_refresh(self):
        """Starts the unified polling scheduler for automatic updates.

        A single thread polls ALL platforms based on a per-platform
        ``next_due`` time. Intervals come from ``PLATFORM_INTERVALS`` and
        depend on whether the device dialog is open (foreground = short
        intervals) or not (background = gentle intervals). This makes all
        platforms behave predictably the same; the former VeSync-only
        thread is gone.
        """
        t = self._background_refresh_thread
        if self._background_refresh_running and t is not None and t.is_alive():
            return  # already active

        self._background_refresh_running = True
        self._stop_event.clear()  # reset the event

        def scheduler_loop():
            try:
                _scheduler_body()
            finally:
                # IMPORTANT: always reset the flag. If the loop ends by itself
                # (e.g. because is_logged_in became False after a failed re-
                # login), a stuck True would block every later start in
                # _start_background_refresh.
                self._background_refresh_running = False

        def _scheduler_body():
            log.info(
                f"Polling-Scheduler gestartet (Takt {SCHEDULER_TICK}s, "
                f"Intervalle fg/bg pro Plattform aus PLATFORM_INTERVALS)"
            )

            # IMPORTANT: first poll after a short delay (5 seconds) so the
            # cache becomes fresh quickly and the dialog opens without waiting.
            if self._scheduler_wait(5):
                return

            # next_due[platform] = earliest time the platform may be polled
            # again. 0.0 = due immediately on the first pass.
            next_due = {name: 0.0 for name in PLATFORM_INTERVALS}
            # Last known result per platform (for the offline detection).
            last_ok = {}

            # Handler per platform: (is-active flag function, refresh
            # function). The refresh functions return True (success), False
            # (error) or None (nothing to do - e.g. no devices).
            platforms = (
                ('meross', lambda: self.use_meross, lambda fg: self._refresh_meross()),
                ('netatmo', lambda: self.use_netatmo, lambda fg: self._refresh_netatmo()),
                ('vesync', lambda: self.use_vesync, lambda fg: self._refresh_vesync(fg)),
                ('cozytouch', lambda: self.use_cozytouch, lambda fg: self._refresh_cozytouch()),
            )

            while self._background_refresh_running and self.is_logged_in:
                try:
                    if not self._background_refresh_running or not self.is_logged_in:
                        break

                    # Opening the dialog forces an immediate poll of all
                    # platforms at the foreground rate (replaces the former
                    # VeSync fast poll).
                    if self._force_poll:
                        self._force_poll = False
                        for name in next_due:
                            next_due[name] = 0.0

                    now = time.time()
                    fg = self._active_dialog is not None
                    polled_any = False

                    for name, is_active, refresh_fn in platforms:
                        if not is_active():
                            continue
                        if now < next_due[name]:
                            continue

                        result = refresh_fn(fg)

                        # Schedule the next due time. On a network outage the
                        # backoff kicks in: same escalation as before (60s,
                        # 120s, 300s).
                        base = PLATFORM_INTERVALS[name]['fg' if fg else 'bg']
                        if self._network_offline:
                            backoff = min(300, BACKGROUND_REFRESH_INTERVAL * (
                                2 ** min(self._consecutive_refresh_failures - 1, 4)))
                            interval = max(base, backoff)
                        else:
                            interval = base
                        # Abstand ab dem ENDE des Aufrufs rechnen, nicht ab
                        # dem Schleifenbeginn: refresh_fn() ist ein Netzaufruf
                        # und kann 10-30 s dauern. Mit dem alten `now` wurde
                        # die naechste Abfrage entsprechend frueher faellig -
                        # die Polls ruecken also ausgerechnet bei langsamer
                        # Verbindung zusammen. Fuer Meross ist das kritisch:
                        # dort gilt ein Limit von 200 Nachrichten pro Stunde
                        # und Geraet (siehe PLATFORM_INTERVALS).
                        next_due[name] = time.time() + interval

                        # result None = not attempted (no devices) -> leave the
                        # status/offline detection untouched, otherwise
                        # evaluate.
                        if result is not None:
                            polled_any = True
                            last_ok[name] = result
                            self._announce_platform_state(name, result, True)

                    # Messwerte in den Verlauf, sobald überhaupt gepollt
                    # wurde. Der Änderungsfilter in log_sensor() entscheidet,
                    # ob tatsächlich etwas geschrieben wird.
                    if polled_any:
                        self._log_sensor_measurements()

                    # LIVE UPDATE: if a poll ran and the dialog is open,
                    # refresh the tree.
                    if polled_any and self._active_dialog and hasattr(
                            self._active_dialog, 'refresh_all_device_data_live'):
                        try:
                            wx.CallAfter(self._active_dialog.refresh_all_device_data_live)
                        except Exception as e:
                            log.debug(f"Dialog Live-Update fehlgeschlagen (wird wiederholt): {e}")

                    # ---- Network offline detection with backoff ----
                    # Only evaluate when this tick actually polled; otherwise
                    # the counter would climb at the 5 s rate. The basis is the
                    # last known result per platform: only when ALL active
                    # platforms last failed does the network count as offline.
                    if polled_any:
                        # _fn statt _ als Wegwerfname: `_` ist in diesem Modul
                        # die gettext-Funktion, und ein `for ... _ in ...`
                        # verdeckt sie innerhalb der Comprehension.
                        active_results = [
                            last_ok[name] for name, is_active, _fn in platforms
                            if is_active() and name in last_ok
                        ]
                        all_failed = bool(active_results) and not any(active_results)
                        if all_failed:
                            self._consecutive_refresh_failures += 1
                            if (not self._network_offline
                                    and self._consecutive_refresh_failures >= 2):
                                self._network_offline = True
                                log.warning(
                                    f"Netzwerk-Verbindung verloren (nach "
                                    f"{self._consecutive_refresh_failures} fehlgeschlagenen Versuchen)")
                        else:
                            if self._network_offline:
                                log.info(
                                    f"Netzwerk wieder verfügbar (nach "
                                    f"{self._consecutive_refresh_failures} fehlgeschlagenen Versuchen)")
                                self._network_offline = False
                            self._consecutive_refresh_failures = 0

                    # Bis zur naechsten faelligen Abfrage schlafen statt im
                    # Sekundentakt aufzuwachen. Sofort unterbrechbar: sowohl
                    # der Stopp als auch request_immediate_poll() setzen
                    # _stop_event.
                    active_names = [n for n, is_active, _fn in platforms if is_active()]
                    if self._scheduler_wait(
                            self._sleep_until_next_due(next_due, active_names)):
                        break

                except Exception as e:
                    log.debug(f"Polling-Scheduler Fehler: {e}")
                    if self._scheduler_wait(SCHEDULER_TICK):
                        break

            log.info("Polling-Scheduler beendet")

        self._background_refresh_thread = threading.Thread(target=scheduler_loop, daemon=True)
        self._background_refresh_thread.start()

    def _stop_background_refresh(self):
        """Stops the scheduler thread (immediately interruptible) and resets the state."""
        self._background_refresh_running = False
        self._force_poll = False
        self._stop_event.set()  # wake the sleeping thread immediately
        # Reset the platform status so no stale status changes are announced
        # after a renewed login
        self._meross_connected = None
        self._netatmo_connected = None
        self._vesync_connected = None
        self._cozytouch_connected = None
        # Discard ALL state snapshots. This way, after a renewed login no old
        # comparison data can fake an external change that is in truth only the
        # initial value.
        self._previous_vesync_states = {}
        self._recent_vesync_actions = {}
        self._previous_netatmo_therm_states = {}
        self._last_boiler_announce_time = {}
        self._previous_cozytouch_states = {}
        self._recent_cozytouch_actions = {}
        self._recent_local_toggles = {}
        self._last_announced_change = None
        self._last_announced_time = 0

    # ----------------------------------------------------------
    # Platform refresh helpers (called by the scheduler)
    # ----------------------------------------------------------
    # Each helper encapsulates the status update of ONE platform and returns:
    # True  = poll successful
    # False = poll failed
    # None  = nothing to do (platform inactive or no devices)
    # This keeps the scheduler loop lean and each platform is
    # testable/maintainable in isolation.
    def _log_power_samples(self, meross_devs):
        """Nimmt Leistungs-Stichproben der Messsteckdosen ins Energie-Log auf.

        Läuft nach jedem Meross-Poll; die Drosselung (min. 60 s Abstand pro
        Gerät) übernimmt das Energie-Log selbst. Kanäle von Mehrfach-/
        Doppelsteckdosen werden einzeln erfasst.
        """
        try:
            from .energy import get_energy_log
            elog = get_energy_log()
            for dev in meross_devs:
                if not getattr(dev, 'has_power_meter', False):
                    continue
                try:
                    watts = dev.get_power()
                    if watts is not None:
                        elog.add_sample(dev.unique_id, dev.name, watts)
                    for ch in (dev.get_channels() or []):
                        ch_watts = ch.get_power()
                        if ch_watts is not None:
                            elog.add_sample(ch.unique_id, ch.name, ch_watts)
                except Exception as e:
                    log.debug(f"Energie-Stichprobe fehlgeschlagen für {dev.name}: {e}")
        except Exception as e:
            log.debug(f"Energie-Log nicht verfügbar: {e}")

    def _log_sensor_measurements(self):
        """Nimmt Messwerte aller Geräte in den Verlauf auf.

        Läuft nach jedem Poll-Durchlauf. Das ist der Unterschied zur früheren
        Fassung: erfasst wurde damals in ``_populate_tree`` des Dialogs, also
        nur beim Öffnen des Menüs - der "Verlauf" war dadurch ein Protokoll
        der Menüöffnungen mit beliebig großen Lücken dazwischen.

        Bezahlbar ist das minütliche Erfassen nur, weil ``log_sensor()`` einen
        Änderungsfilter hat: gespeichert wird ein Wert nur, wenn er sich um
        mehr als die jeweilige Schwelle geändert hat oder seit dem letzten
        Stützpunkt eine Stunde vergangen ist.

        Alle hier benutzten Getter lesen ausschließlich zwischengespeicherte
        Werte aus dem vorangegangenen Poll - es entsteht KEIN zusätzlicher
        Cloud-Aufruf. Leistung (Watt) fehlt bewusst: dafür ist energy.py
        zuständig, das daraus auch Energiemengen integriert.
        """
        try:
            from .history import get_history
            history = get_history()
        except Exception as e:
            log.debug(f"Verlauf nicht verfügbar: {e}")
            return

        with self._devices_lock:
            devices = list(self.devices)

        getters = (
            ('temperature', 'get_temperature'),
            ('humidity', 'get_humidity'),
            ('co2', 'get_co2'),
            ('pressure', 'get_pressure'),
        )
        for device in devices:
            try:
                sensor_data = {}
                for quantity, getter_name in getters:
                    getter = getattr(device, getter_name, None)
                    if getter is None:
                        continue
                    value = getter()
                    if value is not None:
                        sensor_data[quantity] = value
                if sensor_data:
                    history.log_sensor(device, sensor_data)
            except Exception as e:
                log.debug(
                    f"Messwert-Erfassung für {getattr(device, 'name', '?')} "
                    f"fehlgeschlagen: {e}")

    def _refresh_meross(self):
        if not (self.api and self.use_meross):
            return None
        with self._devices_lock:
            # Only real Meross devices - other platforms have no Meross
            # interface and would otherwise be passed to the Meross API.
            meross_devs = [d for d in self.devices if platform_of(d) == 'meross']
        if not meross_devs:
            return None
        try:
            self.api.update_device_status(meross_devs)
            self._last_refresh_time = time.time()
            self._log_power_samples(meross_devs)
            log.debug(f"Scheduler Meross: {len(meross_devs)} Geräte aktualisiert")
            return True
        except Exception as e:
            log.debug(f"Scheduler Meross fehlgeschlagen: {e}")
            return False

    def _refresh_netatmo(self):
        if not (self.netatmo_api and self.use_netatmo):
            return None
        try:
            # Fetch fresh data, detect external changes BEFORE the in-place
            # update.
            fresh_netatmo_devs = self.netatmo_api.get_devices()
            self._detect_netatmo_changes(fresh_netatmo_devs)
            # Update in place so tree references stay valid.
            with self._devices_lock:
                netatmo_devs = [d for d in self.devices if getattr(d, 'is_netatmo', False)]
                fresh_map = {d.uuid: d for d in fresh_netatmo_devs}
                for dev in netatmo_devs:
                    fresh = fresh_map.get(dev.uuid)
                    if fresh:
                        dev._dashboard_data = fresh._dashboard_data
                        dev._therm_measured = fresh._therm_measured
                        dev._therm_setpoint = fresh._therm_setpoint
                        dev._therm_setpoint_mode = fresh._therm_setpoint_mode
                        dev._therm_setpoint_end_time = fresh._therm_setpoint_end_time
                        dev._boiler_status = fresh._boiler_status
                        dev._schedule_zone_name = fresh._schedule_zone_name
                        dev._therm_setpoint_default_duration = fresh._therm_setpoint_default_duration
                        dev._anticipating = fresh._anticipating
                        dev._open_window = fresh._open_window
                        dev._next_schedule_change = fresh._next_schedule_change
                        dev._active_schedule_name = fresh._active_schedule_name
                        dev.is_offline = fresh.is_offline
                        dev.raw_data = fresh.raw_data
                # Add new devices (if newly discovered)
                existing_uuids = {d.uuid for d in netatmo_devs}
                for fresh_dev in fresh_netatmo_devs:
                    if fresh_dev.uuid not in existing_uuids:
                        self.devices.append(fresh_dev)
            log.debug(f"Scheduler Netatmo: {len(fresh_netatmo_devs)} Geräte aktualisiert")
            return True
        except Exception as e:
            log.debug(f"Scheduler Netatmo fehlgeschlagen: {e}")
            return False

    def _refresh_vesync(self, fg):
        """Polls VeSync. ``fg`` (dialog open) uses the API's fast path.

        The VeSync cloud has a daily limit (3200 + 1500*devices) and no
        push mechanism. The minimum spacing in the background (60s, see
        PLATFORM_INTERVALS) keeps the load safely below it.
        """
        if not (self.vesync_api and self.use_vesync):
            return None
        with self._devices_lock:
            vesync_devs = [d for d in self.devices if getattr(d, 'is_vesync', False)]
        if not vesync_devs:
            return None
        try:
            report = self.vesync_api.update_device_status(vesync_devs, fast=fg) or {}
            # Successful if the deviceList came through OR at least one detail
            # call succeeded.
            ok = bool(report.get('devicelist_ok')) or bool(report.get('devices_ok'))
            self._detect_vesync_changes(vesync_devs)
            log.debug(f"Scheduler VeSync: {len(vesync_devs)} Geräte, ok={ok}, fast={fg}")
            return ok
        except Exception as e:
            log.debug(f"Scheduler VeSync fehlgeschlagen: {e}")
            return False

    def _refresh_cozytouch(self):
        if not (self.cozytouch_api and self.use_cozytouch):
            return None
        with self._devices_lock:
            cozytouch_devs = [d for d in self.devices if getattr(d, 'is_cozytouch', False)]
        if not cozytouch_devs:
            return None
        try:
            report = self.cozytouch_api.update_device_status(cozytouch_devs) or {}
            ok = bool(report.get('devices_ok'))
            self._detect_cozytouch_changes(cozytouch_devs)
            log.debug(f"Scheduler Cozytouch: {len(cozytouch_devs)} Geräte, ok={ok}")
            return ok
        except Exception as e:
            log.debug(f"Scheduler Cozytouch fehlgeschlagen: {e}")
            return False
    
