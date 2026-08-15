# -*- coding: utf-8 -*-
"""
Smart Home Control - polling scheduler and platform refresh.
Split out of __init__.py; behaviour unchanged.
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
    log.debug(f"initTranslation failed: {e}")
if "_" not in globals():  # fallback if initTranslation() fails
    # Without this fallback `_` stays undefined and the first `_()` call
    # raises a NameError mid-dialog instead of at import time.
    def _(s):
        return s

from .platform_utils import platform_of, PLATFORM_LABELS
from .constants import (
    BACKGROUND_REFRESH_INTERVAL, SCHEDULER_TICK, SCHEDULER_MAX_SLEEP,
    PLATFORM_INTERVALS,
)


def _read_sensor(device, names):
    """First readable value among ``names`` (method or attribute), else None.

    The platforms differ: Meross and Netatmo offer getters, VeSync keeps the
    values as plain attributes. Reading both means a new sensor only needs an
    entry in the table, not a wrapper method.
    """
    for name in names:
        source = getattr(device, name, None)
        if source is None:
            continue
        try:
            value = source() if callable(source) else source
        except Exception:
            continue
        if value is not None:
            return value
    return None


class _SchedulerMixin:
    """Background scheduler: polls all platforms and reports state changes."""

    def request_immediate_poll(self):
        """Requests an immediate poll of all platforms.

        Called when the device dialog opens. Besides the flag it sets
        ``_stop_event`` so the sleeping scheduler wakes up AT ONCE - it
        sleeps until the next due poll instead of ticking every second.
        ``_stop_event`` therefore serves twice: as stop AND as wake signal.
        They are told apart by ``_background_refresh_running``, which
        ``_stop_background_refresh()`` clears BEFORE the ``set()``; a wake-up
        leaves it untouched.
        """
        self._force_poll = True
        self._stop_event.set()

    def _scheduler_wait(self, timeout):
        """Waits ``timeout`` seconds. True = the loop should end.

        Counterpart to ``request_immediate_poll``: if the event is set while
        ``_background_refresh_running`` is still True it was a wake-up, so
        the event is cleared and work continues.
        """
        if not self._stop_event.wait(timeout):
            return False  # regular timeout
        if not self._background_refresh_running:
            return True   # real stop
        # Wake-up: clear the event and carry on. Should a stop arrive
        # between the check and clear(), the while condition catches it
        # because the flag is tested there.
        self._stop_event.clear()
        return False

    @staticmethod
    def _sleep_until_next_due(next_due, active_names):
        """Sleep time until the next due poll (capped)."""
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
            msg = _("{platform} reconnected").format(platform=label)
            log.info(f"Platform status: {label} reconnected")
        else:
            # Translators: Announced when a smart home platform is temporarily
            # unreachable.
            msg = _("{platform} temporarily unreachable").format(platform=label)
            log.warning(f"Platform status: {label} unreachable")
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
                f"Polling scheduler started (tick {SCHEDULER_TICK}s, "
                f"fg/bg intervals per platform from PLATFORM_INTERVALS)"
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

                        base = PLATFORM_INTERVALS[name]['fg' if fg else 'bg']
                        # Another path (refresh_devices() from the dialog) may
                        # have polled this platform in the meantime. Repeating
                        # it would spend the cloud budget twice for the same
                        # data - so only re-schedule. This also covers the
                        # forced poll when the dialog opens.
                        last_any = self._platform_last_refresh.get(name, 0.0)
                        if last_any and (now - last_any) < base:
                            next_due[name] = last_any + base
                            continue

                        # Same lock as refresh_devices(): the dialog must not
                        # send a second round of the same queries while this
                        # poll is running (see _refresh_lock in __init__.py).
                        with self._refresh_lock:
                            result = refresh_fn(fg)

                        # Schedule the next due time. On a network outage the
                        # backoff kicks in: same escalation as before (60s,
                        # 120s, 300s).
                        if self._network_offline:
                            backoff = min(300, BACKGROUND_REFRESH_INTERVAL * (
                                2 ** min(self._consecutive_refresh_failures - 1, 4)))
                            interval = max(base, backoff)
                        else:
                            interval = base
                        # Measure the gap from the END of the call, not from
                        # the start of the loop: refresh_fn() is a network
                        # call and can take 10-30 s. With the old `now` the
                        # next poll fell due that much earlier, so polls
                        # bunched up on exactly the slow connections.
                        # Critical for Meross, which allows 200 messages per
                        # hour and device (see PLATFORM_INTERVALS).
                        next_due[name] = time.time() + interval

                        # result None = not attempted (no devices) -> leave the
                        # status/offline detection untouched, otherwise
                        # evaluate.
                        if result is not None:
                            self._mark_platform_refreshed(name)
                            # Cache timestamp for is_cache_fresh(). Set here for
                            # EVERY platform: previously only _refresh_meross
                            # did it, so with Meross switched off the cache
                            # never counted as fresh and the dialog refreshed
                            # on its own every single time.
                            self._last_refresh_time = time.time()
                            polled_any = True
                            last_ok[name] = result
                            self._announce_platform_state(name, result, True)

                    # Readings go to the history as soon as anything was
                    # polled; the change filter in log_sensor() decides
                    # whether something is really written.
                    if polled_any:
                        self._log_sensor_measurements()
                        # Water sensors report a state, not a value - they are
                        # checked here rather than in the readings.
                        with self._devices_lock:
                            water_devices = list(self.devices)
                        self._detect_water_alarms(water_devices)

                    # LIVE UPDATE: if a poll ran and the dialog is open,
                    # refresh the tree.
                    if polled_any and self._active_dialog and hasattr(
                            self._active_dialog, 'refresh_all_device_data_live'):
                        try:
                            wx.CallAfter(self._active_dialog.refresh_all_device_data_live)
                        except Exception as e:
                            log.debug(f"Dialog live update failed (will be retried): {e}")

                    # ---- Network offline detection with backoff ----
                    # Only evaluate when this tick actually polled; otherwise
                    # the counter would climb at the 5 s rate. The basis is the
                    # last known result per platform: only when ALL active
                    # platforms last failed does the network count as offline.
                    if polled_any:
                        # _fn instead of _ as throwaway name: `_` is the
                        # gettext function in this module and `for ... _ in
                        # ...` would shadow it inside the comprehension.
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
                                    f"Network connection lost (after "
                                    f"{self._consecutive_refresh_failures} failed attempts)")
                        else:
                            if self._network_offline:
                                log.info(
                                    f"Network available again (after "
                                    f"{self._consecutive_refresh_failures} failed attempts)")
                                self._network_offline = False
                            self._consecutive_refresh_failures = 0

                    # Sleep until the next due poll instead of waking up
                    # every second. Interruptible at once: both the stop and
                    # request_immediate_poll() set _stop_event.
                    active_names = [n for n, is_active, _fn in platforms if is_active()]
                    if self._scheduler_wait(
                            self._sleep_until_next_due(next_due, active_names)):
                        break

                except Exception as e:
                    log.debug(f"Polling scheduler error: {e}")
                    if self._scheduler_wait(SCHEDULER_TICK):
                        break

            log.info("Polling scheduler stopped")

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
        """Adds power samples of the metering outlets to the energy log.

        Runs after every Meross poll; the throttling (at least 60 s per
        device) is done by the energy log itself. Channels of multi-outlet
        strips are recorded individually.
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
                    log.debug(f"Energy sample failed for {dev.name}: {e}")
        except Exception as e:
            log.debug(f"Energy log not available: {e}")

    def _log_sensor_measurements(self):
        """Adds readings of all devices to the history.

        Runs after every poll pass. That is the difference to the earlier
        version, which recorded in the dialog's ``_populate_tree``, i.e. only
        when the menu was opened - which made the "history" a log of menu
        openings with arbitrarily large gaps in between.

        Recording every minute is only affordable because ``log_sensor()``
        has a change filter: a value is stored only if it moved by more than
        its threshold or an hour has passed since the last data point.

        All getters used here read cached values from the previous poll
        only - NO extra cloud call happens. Power (watts) is deliberately
        missing: energy.py handles that and also integrates energy amounts
        from it.
        """
        try:
            from .history import get_history
            history = get_history()
        except Exception as e:
            log.debug(f"History not available: {e}")
            return

        with self._devices_lock:
            devices = list(self.devices)

        # Quantity -> possible sources on the device object. Meross and
        # Netatmo expose getters, VeSync plain attributes - both are read, so
        # the air purifiers' particulate values and the tower fans' measured
        # temperature end up in the history as well.
        sources = (
            ('temperature', ('get_temperature', 'temperature')),
            ('humidity', ('get_humidity', 'humidity')),
            ('co2', ('get_co2',)),
            ('pressure', ('get_pressure',)),
            ('noise', ('get_noise',)),
            ('pm25', ('air_quality_value',)),
            ('pm10', ('pm10',)),
        )
        # One-time repair of the entries written under the old key. Needs
        # the device list, which is why it happens here and not on load.
        if not getattr(self, '_history_keys_migrated', False):
            self._history_keys_migrated = True
            try:
                history.migrate_device_keys(devices)
            except Exception as e:
                log.debug(f"History key migration failed: {e}")

        silent = []
        for device in devices:
            try:
                # Gateways/relays have no sensors of their own. They can still
                # carry values taken over from a device in the same room (the
                # Netatmo NAPlug did exactly that) - recording them would
                # produce a second series with identical numbers.
                if getattr(device, 'is_relay', False):
                    continue
                sensor_data = {}
                for quantity, names in sources:
                    value = _read_sensor(device, names)
                    if value is not None:
                        sensor_data[quantity] = value
                if sensor_data:
                    history.log_sensor(device, sensor_data)
                elif (getattr(device, 'is_sensor', False)
                        and not getattr(device, 'is_water_sensor', False)):
                    # A sensor that yields nothing stays invisible in the
                    # history - without this line one could only guess which
                    # device is missing and why. Water sensors are exempt:
                    # they report a state, not a value, and are handled by
                    # _detect_water_alarms.
                    silent.append(getattr(device, 'name', '?'))
            except Exception as e:
                log.debug(
                    f"Recording readings for {getattr(device, 'name', '?')} "
                    f"failed: {e}")
        if silent:
            log.debug(f"Sensors without a reading in this pass: {', '.join(silent)}")

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
            self._log_power_samples(meross_devs)
            log.debug(f"Scheduler Meross: {len(meross_devs)} devices updated")
            return True
        except Exception as e:
            log.debug(f"Scheduler Meross failed: {e}")
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
            log.debug(f"Scheduler Netatmo: {len(fresh_netatmo_devs)} devices updated")
            return True
        except Exception as e:
            log.debug(f"Scheduler Netatmo failed: {e}")
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
            log.debug(f"Scheduler VeSync: {len(vesync_devs)} devices, ok={ok}, fast={fg}")
            return ok
        except Exception as e:
            log.debug(f"Scheduler VeSync failed: {e}")
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
            log.debug(f"Scheduler Cozytouch: {len(cozytouch_devs)} devices, ok={ok}")
            return ok
        except Exception as e:
            log.debug(f"Scheduler Cozytouch failed: {e}")
            return False
    
