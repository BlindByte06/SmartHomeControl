# -*- coding: utf-8 -*-
"""
Meross cloud API handler
Communicates with the Meross cloud API.
The device classes (MerossDevice, MerossOfflineDevice, MerossChannel)
live in meross_devices.py.

"""

import asyncio
import threading
import time
from logHandler import log

from .constants import (
    MEROSS_METRICS_MIN_INTERVAL,
    MEROSS_HOURLY_BUDGET,
    MEROSS_BUDGET_BURST,
    MEROSS_THROTTLE_NOTIFY_COOLDOWN,
    MEROSS_BATTERY_POLL_INTERVAL,
    MEROSS_BATTERY_RETRY_INTERVAL,
)

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

from .meross_devices import (
    MEROSS_AVAILABLE,
    MerossDevice,
    MerossOfflineDevice,
    is_sensor_type,
    _auto_configure_custom_names,
    set_subdevice_battery,
    hub_battery_poll_due,
    mark_hub_battery_attempt,
)

# Conditional imports only if meross_iot is available
if MEROSS_AVAILABLE:
    from .meross_devices import MerossHttpClient, MerossManager, OnlineStatus



class MerossAPI:
    """Meross cloud API handler"""
    
    def __init__(self):
        if not MEROSS_AVAILABLE:
            raise ImportError(
                "meross_iot ist nicht installiert!\n"
                "Bitte installieren Sie es mit: pip install meross-iot"
            )
        
        self.http_client = None
        self.manager = None
        self.loop = None
        self.loop_thread = None
        self._running = False
        
        # Push notification callback for external status changes (Alexa, app,
        # etc.)
        self._on_device_state_changed_callback = None
        self._device_state_cache = {}  # cache for status comparison: {uuid: is_on}
        self._wrapped_devices = []  # reference to MerossDevice wrappers (for channel updates)
        # Schützt _wrapped_devices: gesetzt aus dem Haupt-/Dialog-Thread,
        # gelesen aus dem MQTT-Push-Thread.
        self._wrapped_devices_lock = threading.Lock()

        # ---- Meross cloud rate limit protection (200 messages/h per device)
        # ----
        # Token bucket per device: {uuid: {'tokens': float, 'last': ts}}. Hard-
        # caps ALL cloud queries at MEROSS_HOURLY_BUDGET/hour.
        self._msg_budget = {}
        # Timestamp of the last power metric query per device (decoupled from
        # the status poll).
        self._last_metrics_fetch = {}
        # Cache der consumptionX-Tageswerte: {uuid: (timestamp, data)}
        self._consumption_cache = {}
        # Callback that informs the user when throttling kicks in (set by the
        # plugin).
        self._on_throttle_callback = None
        self._last_throttle_notify = 0.0  # cooldown anchor for the throttle announcement

        # A device that was OFFLINE at login is not enrolled in the manager's
        # registry (discovery only enrolls online devices). When it later comes
        # online it sends a push (e.g. SYSTEM_ONLINE) that meross_iot logs as a
        # WARNING ("device not available in the local registry"). We enroll such
        # devices on demand via a single-device discovery; this dict rate-limits
        # those attempts per UUID.
        self._unknown_device_discovery_ts = {}

    def set_throttle_callback(self, callback):
        """Registers a callback that is called once (with cooldown) when cloud
        queries are throttled because of the Meross hourly limit.

        Args:
            callback: parameterless function; should marshal to the UI
                thread-safely (e.g. via wx.CallAfter).
        """
        self._on_throttle_callback = callback

    def _consume_budget(self, uuid, cost=1):
        """Token bucket per device. Returns True if ``cost`` tokens were
        available (and consumes them), otherwise False (skip the query).

        Runs exclusively on the event loop thread; since there is no
        ``await`` between reading and writing, the access is atomic with
        respect to the device coroutines running in parallel.
        """
        if not uuid:
            return True
        now = time.time()
        rate = MEROSS_HOURLY_BUDGET / 3600.0  # tokens per second
        st = self._msg_budget.get(uuid)
        if st is None:
            st = {'tokens': float(MEROSS_BUDGET_BURST), 'last': now}
            self._msg_budget[uuid] = st
        # Refill the tokens based on the elapsed time (capped at the burst)
        st['tokens'] = min(MEROSS_BUDGET_BURST, st['tokens'] + (now - st['last']) * rate)
        st['last'] = now
        if st['tokens'] >= cost:
            st['tokens'] -= cost
            return True
        return False

    def _notify_throttle(self, device_name=None):
        """Informs the user at most every MEROSS_THROTTLE_NOTIFY_COOLDOWN
        seconds that throttling is active because of the cloud limit.

        The throttling ALWAYS applies only to the single device that used up
        its own hourly budget (the Meross limit is per device). Therefore
        ``device_name`` is passed to the callback so the announcement names
        this specific device instead of sounding global.
        """
        now = time.time()
        if now - self._last_throttle_notify < MEROSS_THROTTLE_NOTIFY_COOLDOWN:
            return
        self._last_throttle_notify = now
        cb = self._on_throttle_callback
        if cb:
            try:
                cb(device_name)
            except Exception as e:
                log.debug(f"Ignorierter Fehler in _notify_throttle: {e}")

    def set_device_state_changed_callback(self, callback):
        """
        Registers a callback for device status changes (push notifications).

        The callback is called when a device is switched on or off externally
        (Alexa, app, etc.).

        Args:
            callback: function with signature callback(device_name, new_state, device_uuid)
                     device_name: name of the device (str)
                     new_state: True = switched on, False = switched off
                     device_uuid: UUID of the device (str)
        """
        self._on_device_state_changed_callback = callback
        log.info("Push-Notification Callback registriert")
    
    def set_wrapped_devices(self, devices):
        """
        Sets the reference to MerossDevice wrappers for channel updates on push notifications.

        Unter demselben Lock wie der Lesezugriff in
        ``_process_toggle_notification``: die Liste wird an mehreren Stellen
        neu gesetzt (Login, Reload, Geräte-Aktualisierung), und ein Push, der
        in dieses Fenster fällt, sah sonst eine leere oder veraltete Liste.

        Args:
            devices: list of MerossDevice wrapper objects
        """
        with self._wrapped_devices_lock:
            self._wrapped_devices = list(devices) if devices else []
    
    def _handle_device_push_notification(self, push_notification, target_devices=None):
        """
        Processes push notifications from Meross devices.

        This method is called when a device changes its status
        (e.g. via Alexa, the Meross app, a physical switch, etc.)

        Args:
            push_notification: the push notification object
            target_devices: list of affected devices (provided by meross_iot)
        """
        try:
            # Extract information from the push notification
            namespace = push_notification.namespace if hasattr(push_notification, 'namespace') else None
            originating_device_uuid = push_notification.originating_device_uuid if hasattr(push_notification, 'originating_device_uuid') else None
            raw_data = push_notification.raw_data if hasattr(push_notification, 'raw_data') else {}
            
            log.debug(f"Push-Notification empfangen: Namespace={namespace}, UUID={originating_device_uuid}, Devices={len(target_devices) if target_devices else 0}")
            
            # Process toggle events (on/off). The namespace can be:
            # Namespace.CONTROL_TOGGLEX, Namespace.CONTROL_TOGGLE, etc.
            namespace_str = str(namespace).upper() if namespace else ""
            
            # Check for toggle namespaces (cover different spellings)
            is_toggle_event = any(toggle_type in namespace_str for toggle_type in [
                'TOGGLEX', 'TOGGLE', 'CONTROL_TOGGLEX', 'CONTROL_TOGGLE'
            ])
            
            if is_toggle_event:
                self._process_toggle_notification(originating_device_uuid, raw_data, target_devices)
            
        except Exception as e:
            log.debug(f"Fehler beim Verarbeiten der Push-Notification: {e}")
    
    def _process_toggle_notification(self, device_uuid, raw_data, target_devices=None):
        """Processes toggle notifications (on/off)"""
        try:
            if not self._on_device_state_changed_callback:
                log.debug("Kein Callback registriert - überspringe")
                return
            
            # Extract the new status and channel info from raw_data. Format:
            # {'togglex': [{'channel': 0, 'onoff': 1, 'lmTime': ...}, ...]} or
            # {'toggle': {'onoff': 1}}
            
            # For multi-channel devices: extract all channel states from the
            # togglex list
            channel_states = {}  # {channel_num: (onoff, lmTime)}
            
            if 'togglex' in raw_data:
                togglex = raw_data['togglex']
                if isinstance(togglex, list):
                    for item in togglex:
                        if isinstance(item, dict):
                            ch_num = item.get('channel')
                            onoff = item.get('onoff', 0) == 1
                            lm_time = item.get('lmTime', 0)
                            if ch_num is not None:
                                channel_states[ch_num] = (onoff, lm_time)
            elif 'toggle' in raw_data:
                toggle = raw_data['toggle']
                if isinstance(toggle, dict):
                    channel_states[0] = (toggle.get('onoff', 0) == 1, toggle.get('lmTime', 0))
            
            if not channel_states:
                return
            
            # Find the device name - use target_devices if available (faster!)
            # Translators: Placeholder when the device name cannot be
            # determined.
            device_name = _("Unbekanntes Gerät")
            actual_uuid = device_uuid
            raw_device_obj = None  # the meross_iot device object
            
            if target_devices and len(target_devices) > 0:
                raw_device_obj = target_devices[0]
                device_name = raw_device_obj.name if hasattr(raw_device_obj, 'name') else _("Unbekanntes Gerät")
                actual_uuid = raw_device_obj.uuid if hasattr(raw_device_obj, 'uuid') else device_uuid
            elif device_uuid and self.manager:
                devices = self.manager.find_devices(device_uuids=[device_uuid])
                if devices:
                    raw_device_obj = devices[0]
                    device_name = raw_device_obj.name
                    actual_uuid = raw_device_obj.uuid
            
            if not actual_uuid:
                log.debug("Keine Geräte-UUID gefunden - überspringe")
                return
            
            # Find the MerossDevice wrapper for multi-channel devices.
            # Unter demselben Lock wie set_wrapped_devices(), damit hier nie
            # eine halb ersetzte Liste gesehen wird (früher konnte ein Push in
            # genau dem Fenster nach Login/Reload durch den Einkanal-Zweig
            # laufen: alle Kanäle teilten sich dann den Cache-Key und die
            # Ansage nutzte den Gerätenamen statt des Ausgangsnamens).
            wrapped_device = None
            with self._wrapped_devices_lock:
                for wd in (self._wrapped_devices or []):
                    if getattr(wd, 'uuid', None) == actual_uuid:
                        wrapped_device = wd
                        break

            is_multi_channel = bool(
                wrapped_device is not None
                and getattr(wrapped_device, 'is_multi_channel', False))
            channels = []
            if is_multi_channel and hasattr(wrapped_device, 'get_channels'):
                channels = wrapped_device.get_channels() or []

            # Zweitquelle: mehr als ein togglex-Eintrag heißt mehrkanalig,
            # auch wenn (noch) kein Wrapper gefunden wurde. Damit stimmt
            # wenigstens der Cache-Key pro Kanal und der zweite Kanal
            # überschreibt nicht den Zustand des ersten.
            if not is_multi_channel and len(channel_states) > 1:
                log.debug(
                    f"Push für {actual_uuid}: {len(channel_states)} Kanäle, "
                    f"aber kein Wrapper - behandle als mehrkanalig")
                is_multi_channel = True

            # Bekannte Kanalindizes des Wrappers. Damit wird nicht mehr auf
            # die NUMMER 0 geprüft, sondern auf Zugehörigkeit: meross_iot
            # markiert Kanal 0 immer als Master, auch wenn er in Wahrheit ein
            # echter Ausgang ist - dessen Änderung wurde dadurch stillschweigend
            # geschluckt. Ist die Menge leer (kein Wrapper), wird nichts
            # gefiltert.
            known_indices = {
                idx for idx in (getattr(ch, 'channel_index', None) for ch in channels)
                if idx is not None
            }

            # Process each channel and find changes. Cache key for multi-
            # channel devices: uuid_channel (e.g. "abc123_1")
            changed_channels = []  # list of (channel_name, new_state, channel_index)

            for ch_num, (ch_state, lm_time) in channel_states.items():
                # Master-Kanal überspringen - erkannt an der Zugehörigkeit zu
                # den Ausgängen des Wrappers, nicht an der Nummer.
                if is_multi_channel and known_indices and ch_num not in known_indices:
                    continue

                # Cache key: UUID + channel for multi-channel, only UUID for
                # single-channel
                cache_key = f"{actual_uuid}_{ch_num}" if is_multi_channel else actual_uuid
                cached_state = self._device_state_cache.get(cache_key)
                
                if cached_state == ch_state:
                    continue
                
                # The status has changed!
                self._device_state_cache[cache_key] = ch_state
                
                # Find the channel name
                ch_name = device_name  # fallback
                if is_multi_channel and channels:
                    for ch in channels:
                        if getattr(ch, 'channel_index', None) == ch_num:
                            ch_name = ch.name
                            # Update the channel status in the wrapper. The
                            # channel opens a SHORT grace window in which a
                            # concurrent poll will not overwrite this fresher
                            # value - see MerossChannel._update_status(). It is
                            # deliberately not a permanent "don't poll me" flag.
                            if hasattr(ch, 'mark_push_update'):
                                ch.mark_push_update(ch_state)
                            break
                
                changed_channels.append((ch_name, ch_state, ch_num))
            
            # Send a callback for each changed channel
            for ch_name, ch_state, ch_num in changed_channels:
                # channel_name_for_callback is the channel name for dialog
                # updates
                channel_name_for_callback = ch_name if is_multi_channel else None
                self._on_device_state_changed_callback(ch_name, ch_state, actual_uuid, channel_name_for_callback)
            
            # If there are no changes for multi-channel, but it is a single-
            # channel device
            if not changed_channels and not is_multi_channel:
                # Fallback for single-channel devices (channel 0 only)
                if 0 in channel_states:
                    new_state = channel_states[0][0]
                    cache_key = actual_uuid
                    cached_state = self._device_state_cache.get(cache_key)
                    if cached_state != new_state:
                        self._device_state_cache[cache_key] = new_state
                        self._on_device_state_changed_callback(device_name, new_state, actual_uuid, None)
            
        except Exception as e:
            # No exc_info: could leak tokens/headers in the stack trace.
            log.error(f"Fehler beim Verarbeiten der Toggle-Notification: {type(e).__name__}: {e}")
    
    def _start_event_loop(self):
        """Starts the event loop in a separate thread"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        # Install a thread excepthook: catches the "Event loop is closed" crash
        # from the paho-mqtt thread that occurs when MQTT messages arrive after
        # the event loop cleanup (known meross_iot issue).
        # ACHTUNG: threading.excepthook ist PROZESSWEIT - der Austausch wirkt
        # auf ganz NVDA und alle Add-ons, solange der Loop läuft. Der Hook
        # filtert deshalb strikt (nur RuntimeError "Event loop is closed" aus
        # paho-mqtt-Threads) und reicht alles andere an den Original-Hook
        # weiter; zurückgetauscht wird nur, wenn unser Hook noch installiert
        # ist (siehe finally unten).
        original_excepthook = getattr(threading, 'excepthook', None)
        
        def _mqtt_safe_excepthook(args):
            # Ignore RuntimeError "Event loop is closed" from paho-mqtt
            if (isinstance(args.exc_value, RuntimeError) and 
                "Event loop is closed" in str(args.exc_value) and
                args.thread and 'paho-mqtt' in (args.thread.name or '')):
                log.debug("MQTT-Thread: Event Loop bereits geschlossen (harmlos, wird ignoriert)")
                return
            # Handle all other exceptions normally
            if original_excepthook:
                original_excepthook(args)
        
        threading.excepthook = _mqtt_safe_excepthook
        
        try:
            # Run the loop blocking until _cleanup() stops it via
            # call_soon_threadsafe(self.loop.stop). Previously a busy poll ran
            # here (run_until_complete(asyncio.sleep(0.1)) in a while loop)
            # that woke up 10x/second and needlessly drew CPU/battery.
            # run_forever() instead sleeps until there is actual work (MQTT
            # callbacks, queued coroutines).
            self.loop.run_forever()
        finally:
            # Cleanup: cancel all pending tasks
            try:
                pending = asyncio.all_tasks(self.loop)
                for task in pending:
                    task.cancel()
                if pending:
                    self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception as e:
                log.debug(f"Event Loop Cleanup Fehler: {e}")
            finally:
                try:
                    self.loop.close()
                    log.info("Event Loop sauber beendet")
                except Exception as e:
                    log.debug(f"Event Loop Close Fehler: {e}")
                # Only restore the excepthook if OURS is still installed -
                # otherwise a hook set by another party in the meantime would
                # be silently overwritten.
                if (original_excepthook
                        and threading.excepthook is _mqtt_safe_excepthook):
                    threading.excepthook = original_excepthook
    
    def _run_async(self, coro, timeout=120):
        """Runs a coroutine on the event loop thread.

        Uses the future from ``run_coroutine_threadsafe`` directly. If the
        timeout expires, the coroutine is CANCELLED in the loop - previously
        it kept running unnoticed after the timeout and held connections/
        resources.
        """
        if not self.loop or self.loop.is_closed():
            raise RuntimeError(_("Event Loop nicht verfügbar"))

        import concurrent.futures
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()  # do not let the coroutine keep running
            raise TimeoutError(
                f"Meross-Operation nach {timeout}s abgebrochen (Timeout)")
    
    def login(self, email, password, api_base_url="https://iotx-eu.meross.com"):
        """
        Logs in to the Meross cloud

        The password is not stored in self; it is passed as an argument to
        the async login coroutine and discarded immediately after
        _run_async() - so no permanent plain text stays in memory.

        Args:
            email: Meross account email
            password: Meross account password
            api_base_url: Meross API base URL (default: EU server)
        """
        if not email or not password:
            # Translators: Validation error when email or password is missing.
            raise ValueError(_("Email und Passwort erforderlich"))

        self.api_base_url = api_base_url
        self.email = email

        log.info(f"Meross API: Starte Login (Server: {api_base_url})...")

        # Set the running flag BEFORE the thread start
        self._running = True

        # Start the event loop thread (threading/time sind Modulimporte;
        # _i statt _, damit gettext-_ nicht verdeckt wird)
        self.loop_thread = threading.Thread(target=self._start_event_loop, daemon=True)
        self.loop_thread.start()

        # Wait until the loop is running
        for _i in range(50):  # 5 seconds max
            if self.loop and self.loop.is_running():
                break
            time.sleep(0.1)
        else:
            self._running = False
            # Translators: Error message when the Meross event loop does not
            # start.
            raise RuntimeError(_("Event Loop konnte nicht gestartet werden"))

        try:
            self._run_async(self._login(password))
        except (ConnectionError, TimeoutError, OSError) as e:
            log.error(f"Login fehlgeschlagen - Netzwerkfehler: {e}")
            self._cleanup()
            raise ConnectionError(f"Verbindungsfehler: {e}")
        except Exception as e:
            log.error(f"Login fehlgeschlagen: {e}")
            self._cleanup()
            raise
        finally:
            password = None
            del password
    
    def _extract_power_from_metrics(self, metrics):
        """Extracts the power consumption from electricity metrics.

        Args:
            metrics: PowerInfo object from async_get_instant_metrics()

        Returns:
            float: power consumption in watts (W), or None on error
        """
        try:
            if metrics is None:
                return None
            
            if hasattr(metrics, 'power') and metrics.power is not None:
                return round(metrics.power, 1)
            
            return None
        except Exception as e:
            log.debug(f"Fehler beim Extrahieren der Power-Daten: {e}")
            return None
        
    async def _login(self, password):
        """Internal async login function. Password as a local argument, not in
        self - so it is collected by the GC after the function ends.
        """
        try:
            self.http_client = await MerossHttpClient.async_from_user_password(
                api_base_url=self.api_base_url,
                email=self.email,
                password=password
            )
        finally:
            password = None
            del password

        self.manager = MerossManager(http_client=self.http_client)
        await self.manager.async_init()

        # Register the push notification handler for external status changes
        self.manager.register_push_notification_handler_coroutine(
            self._async_push_notification_handler
        )
        log.info("Meross API: Login erfolgreich, Push-Notifications aktiviert")
    
    async def _async_push_notification_handler(self, push_notification, target_devices=None, manager=None):
        """
        Async handler for push notifications (called by meross_iot)

        Args:
            push_notification: the push notification object
            target_devices: list of affected devices (from meross_iot)
            manager: the MerossManager (from meross_iot)
        """
        try:
            self._handle_device_push_notification(push_notification, target_devices)
        except Exception as e:
            log.debug(f"Push-Notification Handler Fehler: {e}")

        # If the push is for a device that is not in the registry (typically a
        # device that was offline at login and just came back online), enroll it
        # via a single-device discovery. This makes the device usable again AND
        # stops the recurring meross_iot "device not available in the local
        # registry" warnings for it.
        try:
            if not target_devices:
                uuid = getattr(push_notification, 'originating_device_uuid', None)
                await self._enroll_unknown_device_if_needed(uuid)
        except Exception as e:
            log.debug(f"Ignorierter Fehler beim Nachmelden eines Geräts: {e}")

    async def _enroll_unknown_device_if_needed(self, uuid):
        """Enrolls a device that sent a push but is not in the local registry.

        meross_iot's discovery only enrolls ONLINE devices, so a device that was
        offline at login stays unknown until it is rediscovered. When such a
        device comes online it emits pushes the manager cannot route (and logs a
        WARNING). A single-device discovery re-enrolls it. Rate-limited per UUID
        so an online-flapping device does not trigger repeated cloud calls.
        """
        if not uuid or not self.manager:
            return
        # Already known? Then nothing to do (and no more warnings will occur).
        try:
            if self.manager.find_devices(device_uuids=[uuid]):
                return
        except Exception as e:
            log.debug(f"Ignorierter Fehler in _enroll_unknown_device_if_needed: {e}")

        now = time.time()
        last = self._unknown_device_discovery_ts.get(uuid, 0.0)
        # Cooldown reuses the battery retry interval (5 min) as a sensible floor.
        if (now - last) < MEROSS_BATTERY_RETRY_INTERVAL:
            return
        # Record the attempt BEFORE awaiting so concurrent pushes do not pile up.
        self._unknown_device_discovery_ts[uuid] = now

        try:
            log.info(f"Meross: Gerät {uuid} nicht registriert – starte gezielte Discovery")
            await self.manager.async_device_discovery(meross_device_uuid=uuid)
            log.info(f"Meross: Gerät {uuid} nachträglich registriert")
        except Exception as e:
            log.debug(f"Gezielte Discovery für {uuid} fehlgeschlagen: {type(e).__name__}: {e}")
    
    def get_devices(self):
        """
        Fetches all available devices (incl. offline devices)

        Returns:
            list of MerossDevice and MerossOfflineDevice objects
        """
        if not self._running:
            raise RuntimeError(_("Nicht angemeldet"))
        
        log.debug("Meross API: Rufe Geräte ab...")
        
        async def _get_devices():
            # 1. Fetch ALL devices from the HTTP API (incl. offline)
            all_http_devices = await self.http_client.async_list_devices()
            log.debug(f"HTTP API listet {len(all_http_devices)} Gerät(e) (inkl. offline)")
            
            # 2. Perform the normal discovery (enrolls only online devices)
            await self.manager.async_device_discovery()
            online_devices = self.manager.find_devices()
            log.info(f"Meross API: {len(online_devices)} online Gerät(e) gefunden - starte paralleles Update...")
            
            # 3. Update the status of all ONLINE devices IN PARALLEL (with
            # optimized timeouts). IMPORTANT: hubs (MSH300, MSH450) need LONGER
            # timeouts because they also have to load subdevices (sensors like
            # MS100, MS130)!
            async def update_device(device):
                try:
                    # Check whether it is a hub (has a get_subdevices method or
                    # starts with msh)
                    device_type = device.type.lower() if hasattr(device, 'type') else ''
                    is_hub = hasattr(device, 'get_subdevices') or 'msh' in device_type
                    
                    # Hubs need LONGER timeouts (10s instead of 4s) because
                    # they have to load subdevices. Loading subdevices can be
                    # slow, especially with several sensors
                    timeout = 10.0 if is_hub else 4.0
                    
                    await asyncio.wait_for(device.async_update(), timeout=timeout)
                    
                    # For hubs: explicitly wait for subdevices (important for
                    # sensors!)
                    if is_hub and hasattr(device, 'get_subdevices'):
                        try:
                            # Wait briefly so subdevice events can arrive
                            await asyncio.sleep(0.5)
                            subdevices = list(device.get_subdevices()) if callable(device.get_subdevices) else []
                            if subdevices:
                                log.debug(f"Hub {device.name}: {len(subdevices)} Subdevice(s) gefunden")
                        except Exception as e:
                            log.debug(f"Hub {device.name}: Subdevice-Abfrage fehlgeschlagen: {e}")
                    
                    # For ElectricityMixin devices: also load instant metrics
                    # (power consumption)
                    metrics = None
                    if hasattr(device, 'async_get_instant_metrics'):
                        try:
                            # OPTIMIZED: 2s -> 1.5s for metrics (most devices
                            # answer in <0.5s)
                            metrics = await asyncio.wait_for(
                                device.async_get_instant_metrics(), 
                                timeout=1.5
                            )
                        except asyncio.TimeoutError:
                            log.debug(f"Metrics-Timeout für {device.name} (1.5s) - überspringe")
                        except Exception as e:
                            log.debug(f"Electricity-Daten für {device.name} nicht verfügbar: {e}")
                    
                    return (device, True, metrics)  # device + success status + metrics
                    
                except asyncio.TimeoutError:
                    is_hub = hasattr(device, 'get_subdevices') or 'msh' in (device.type.lower() if hasattr(device, 'type') else '')
                    timeout_used = 10.0 if is_hub else 4.0
                    log.warning(f"Update-Timeout für {device.name} ({timeout_used}s) - Gerät wird trotzdem hinzugefügt")
                    return (device, False, None)  # keep the device, but without complete data
                except Exception as e:
                    log.debug(f"Update fehlgeschlagen für {device.name}: {e}")
                    return (device, False, None)
            
            update_tasks = [update_device(d) for d in online_devices]
            results = await asyncio.gather(*update_tasks, return_exceptions=True)
            
            # Filter only successful updates (but keep all devices!)
            successful = sum(1 for result in results if isinstance(result, tuple) and len(result) >= 2 and result[1])
            log.info(f"Geräte-Update abgeschlossen: {successful}/{len(online_devices)} erfolgreich")
            
            # 4. Create MerossDevice wrappers for ALL ONLINE devices (even with
            # timeout)
            online_device_wrappers = []
            hub_devices = []  # collect hub devices for the later subdevice check
            
            for result in results:
                if isinstance(result, tuple):
                    device, success, metrics = result
                    try:
                        wrapper = MerossDevice(device)
                        # If we have metrics, store the complete PowerInfo
                        # object
                        if metrics is not None:
                            wrapper._cached_metrics = metrics
                        online_device_wrappers.append(wrapper)
                        
                        # Collect hubs for the subdevice check
                        if wrapper.is_hub:
                            hub_devices.append(device)
                        
                        if not success:
                            log.info(f"Gerät {wrapper.name} ohne vollständigen Status hinzugefügt (Timeout)")
                    except Exception as e:
                        log.error(f"Fehler beim Erstellen von MerossDevice-Wrapper für {device.name}: {e}")
            
            # 4b. IMPORTANT: explicitly fetch all subdevices of hubs and add
            # them. This fixes the problem that sensors (MS100, MS130) are
            # sometimes missing when their hub timed out or was slow. DUPLICATE
            # CHECK: unique via wrapper.unique_id. Important: all sensors of
            # ONE hub share the same device.uuid (the hub UUID). Only unique_id
            # (= hub UUID + subdevice ID) makes each sensor unique AND stays
            # stable across all collection passes.
            existing_ids = {d.unique_id for d in online_device_wrappers}
            subdevice_wrappers = []

            for hub_device in hub_devices:
                try:
                    if hasattr(hub_device, 'get_subdevices'):
                        subdevices = list(hub_device.get_subdevices()) if callable(hub_device.get_subdevices) else []
                        for subdev in subdevices:
                            subdev_type = getattr(subdev, 'type', '').lower() if hasattr(subdev, 'type') else ''

                            # Only known sensors (central model list)
                            if not is_sensor_type(subdev_type):
                                continue

                            try:
                                wrapper = MerossDevice(subdev)
                            except Exception as e:
                                log.debug(f"Subdevice-Wrapper-Erstellung fehlgeschlagen: {e}")
                                continue

                            # DUPLICATE CHECK via the stable, unique unique_id
                            if wrapper.unique_id in existing_ids:
                                continue

                            subdevice_wrappers.append(wrapper)
                            existing_ids.add(wrapper.unique_id)
                            log.info(f"Sensor-Subdevice nachträglich hinzugefügt: {wrapper.name} ({wrapper.type})")
                except Exception as e:
                    log.debug(f"Subdevice-Abfrage für Hub {hub_device.name} fehlgeschlagen: {e}")
            
            # Add subdevices found afterwards to the list
            if subdevice_wrappers:
                log.info(f"{len(subdevice_wrappers)} Sensor(en) nachträglich von Hubs hinzugefügt")
                online_device_wrappers.extend(subdevice_wrappers)
            
            online_uuids = {d.uuid for d in online_device_wrappers}
            
            # 5. Find NOT-ENROLLED devices (in the HTTP list but not
            # successfully enrolled). IMPORTANT: distinguish between OFFLINE
            # (online_status != ONLINE) and ENROLLMENT-FAILED (online but
            # timeout)
            not_enrolled_http_devices = []
            truly_offline_devices = []  # only for genuinely offline devices
            enrollment_failed_devices = []  # for online devices with an enrollment timeout
            
            for http_dev in all_http_devices:
                if http_dev.uuid not in online_uuids:
                    # The device is in the HTTP list but not successfully
                    # enrolled
                    not_enrolled_http_devices.append(http_dev)
                    
                    # Check whether the device is REALLY offline or only had an
                    # enrollment timeout
                    if http_dev.online_status == OnlineStatus.ONLINE:
                        # The device is ONLINE according to the HTTP API but
                        # enrollment failed. Typical for devices like the
                        # MOD150 (diffuser) that are slow/problematic during
                        # enrollment
                        enrollment_failed_devices.append(http_dev)
                        log.warning(f"Enrollment-Failed aber ONLINE: {http_dev.dev_name} ({http_dev.device_type}) - UUID: {http_dev.uuid}")
                    else:
                        # The device is really offline
                        truly_offline_devices.append(http_dev)
                        log.debug(f"Offline-Gerät gefunden: {http_dev.dev_name} ({http_dev.device_type})")
            
            # 6. Create MerossOfflineDevice wrappers ONLY for REALLY OFFLINE
            # devices
            offline_device_wrappers = [MerossOfflineDevice(http_dev) for http_dev in truly_offline_devices]
            
            # 7. For enrollment-failed ONLINE devices: second enrollment
            # attempt (single try)
            retry_success_devices = []
            retry_failed_devices = []
            
            if enrollment_failed_devices:
                log.info(f"Versuche Enrollment-Retry für {len(enrollment_failed_devices)} online Gerät(e) mit vorherigem Timeout...")
                
                async def retry_single_device(http_dev):
                    try:
                        # OPTIMIZED: first check whether the device is already
                        # enrolled in the manager. NO repeated
                        # async_device_discovery() - that takes ~25s per call!
                        enrolled_devices = self.manager.find_devices(device_uuids=[http_dev.uuid])
                        if enrolled_devices:
                            device = enrolled_devices[0]
                            try:
                                await asyncio.wait_for(device.async_update(), timeout=6.0)
                                log.info(f"Retry erfolgreich (bereits enrolled): {http_dev.dev_name}")
                                return (MerossDevice(device), True)
                            except (asyncio.TimeoutError, asyncio.CancelledError):
                                log.debug(f"Retry-Update Timeout für {http_dev.dev_name}")
                    except Exception as e:
                        log.debug(f"Retry fehlgeschlagen für {http_dev.dev_name}: {e}")
                    return (MerossOfflineDevice(http_dev), False)
                
                retry_tasks = [retry_single_device(dev) for dev in enrollment_failed_devices]
                retry_results = await asyncio.gather(*retry_tasks, return_exceptions=True)
                
                for result in retry_results:
                    if isinstance(result, tuple):
                        device_wrapper, success = result
                        if success:
                            retry_success_devices.append(device_wrapper)
                        else:
                            retry_failed_devices.append(device_wrapper)
            
            # 8. Combine: enrolled + retry-successful + truly-offline + retry-
            # failed
            all_devices = (online_device_wrappers + 
                          retry_success_devices + 
                          offline_device_wrappers + 
                          retry_failed_devices)
            
            if offline_device_wrappers or retry_success_devices or retry_failed_devices:
                log.info(f"Gesamtanzahl: {len(online_device_wrappers)} enrolled + {len(retry_success_devices)} retry-ok + {len(retry_failed_devices)} retry-fail + {len(offline_device_wrappers)} offline = {len(all_devices)} Gerät(e)")
            
            # 8b. FINAL CHECK: look for missing sensors (MS100, MS130). Hubs
            # may have new subdevices after the update that we missed. Call
            # find_devices() again to make sure all sensors are included.
            # DUPLICATE CHECK: unique and stable via unique_id
            final_device_ids = {d.unique_id for d in all_devices}
            try:
                # Short pause so all hub events can be processed
                await asyncio.sleep(0.3)

                # Fetch all devices from the manager again
                all_current_devices = self.manager.find_devices()

                # Look for sensors that are not in our list yet
                missing_sensors = []

                for device in all_current_devices:
                    device_type = device.type.lower() if hasattr(device, 'type') else ''
                    if not is_sensor_type(device_type):
                        continue

                    try:
                        wrapper = MerossDevice(device)
                    except Exception as e:
                        log.debug(f"Sensor-Wrapper-Erstellung fehlgeschlagen für {device.name}: {e}")
                        continue

                    # DUPLICATE CHECK via the stable, unique unique_id
                    if wrapper.unique_id in final_device_ids:
                        continue

                    missing_sensors.append(wrapper)
                    final_device_ids.add(wrapper.unique_id)
                    log.info(f"Fehlender Sensor nachträglich gefunden: {wrapper.name} ({wrapper.type})")
                
                if missing_sensors:
                    log.info(f"{len(missing_sensors)} fehlende(r) Sensor(en) nachträglich zur Liste hinzugefügt")
                    all_devices.extend(missing_sensors)
            except Exception as e:
                log.debug(f"Finale Sensor-Prüfung fehlgeschlagen: {e}")
            
            # 9. Auto-configuration for known devices with custom names
            _auto_configure_custom_names(all_devices)
            
            return all_devices
        
        try:
            # 120s timeout: hubs with sensors (MS100/MS130) need up to 40s,
            # plus enrollment retries for problematic devices
            return self._run_async(_get_devices(), timeout=120)
        except TimeoutError:
            log.warning("Timeout beim Abrufen der Geräte - versuche Fallback...")
            # Fallback: try to use already enrolled devices from the manager
            # PLUS the HTTP API for missing/offline devices
            try:
                async def _get_devices_fallback():
                    # 1. Fetch already enrolled devices from the manager (these
                    # have MQTT status!)
                    enrolled_devices = []
                    enrolled_uuids = set()
                    try:
                        all_enrolled = self.manager.find_devices()
                        for device in all_enrolled:
                            try:
                                wrapper = MerossDevice(device)
                                enrolled_devices.append(wrapper)
                                enrolled_uuids.add(device.uuid)
                            except Exception as e:
                                log.debug(f"Fallback: Wrapper-Erstellung fehlgeschlagen für {device.name}: {e}")
                        log.info(f"Fallback: {len(enrolled_devices)} bereits enrolled Gerät(e) vom Manager")
                    except Exception as e:
                        log.debug(f"Fallback: Manager find_devices fehlgeschlagen: {e}")
                    
                    # 2. Extract subdevices from hubs (sensors MS100/MS130!)
                    subdev_wrappers = []
                    existing_ids = {d.unique_id for d in enrolled_devices}
                    for wrapper in list(enrolled_devices):
                        if wrapper.is_hub:
                            try:
                                subdevices = wrapper.get_subdevices()
                                for subdev in subdevices:
                                    subdev_type = getattr(subdev, 'type', '').lower()
                                    if not is_sensor_type(subdev_type):
                                        continue
                                    try:
                                        sw = MerossDevice(subdev)
                                    except Exception:
                                        continue
                                    # DUPLICATE CHECK via the stable, unique
                                    # unique_id
                                    if sw.unique_id in existing_ids:
                                        continue
                                    subdev_wrappers.append(sw)
                                    existing_ids.add(sw.unique_id)
                            except Exception as e:
                                log.debug(f"Ignorierter Fehler in _get_devices_fallback: {e}")
                    if subdev_wrappers:
                        log.info(f"Fallback: {len(subdev_wrappers)} Sensor(en) von Hubs hinzugefügt")
                        enrolled_devices.extend(subdev_wrappers)
                    
                    # 3. HTTP API for missing/offline devices
                    all_http_devices = await self.http_client.async_list_devices()
                    log.info(f"Fallback: {len(all_http_devices)} Geräte von HTTP-API")
                    
                    # Only add non-enrolled devices as offline
                    offline_devices = []
                    for http_dev in all_http_devices:
                        if http_dev.uuid not in enrolled_uuids:
                            offline_devices.append(MerossOfflineDevice(http_dev))
                    
                    devices = enrolled_devices + offline_devices
                    log.info(f"Fallback: {len(enrolled_devices)} online + {len(offline_devices)} offline = {len(devices)} Gerät(e)")
                    
                    _auto_configure_custom_names(devices)
                    return devices
                
                devices = self._run_async(_get_devices_fallback(), timeout=30)
                log.info(f"Fallback erfolgreich: {len(devices)} Gerät(e) geladen")
                return devices
            except Exception as fallback_error:
                log.error(f"Auch Fallback fehlgeschlagen: {fallback_error}")
                # Translators: Error message on timeout of the Meross device
                # query.
                raise TimeoutError(_("Geräte-Abfrage dauert zu lange - möglicherweise sind zu viele Geräte offline"))
        except Exception as e:
            log.error(f"Fehler beim Abrufen der Geräte: {e}")
            raise
    
    @staticmethod
    def _extract_battery_value(entry):
        """Reads the battery percentage from one HUB_BATTERY response entry.

        Different hub firmwares use slightly different keys; 'value' is the
        common one, the others are defensive fallbacks. Returns an int 0-100 or
        None.
        """
        if not isinstance(entry, dict):
            return None
        for key in ('value', 'percentage', 'battery', 'remain', 'quantity', 'electricity'):
            v = entry.get(key)
            if isinstance(v, (int, float)) and v >= 0:
                return int(v)
        return None

    async def _poll_hub_batteries(self, hub_device):
        """Polls and caches the battery level of a hub's subdevices.

        Queries the whole hub ONCE via Appliance.Hub.Battery with an EMPTY list
        ({'battery': []}). This is important: the per-subdevice query used by
        meross_iot's async_get_battery_life ({'battery': [{'id': X}]}) returns a
        stub WITHOUT a 'value' on several hub firmwares (that was the 'kein Wert
        in der Antwort' log). The empty-list query returns the real values for
        all subdevices in one call - which is also easier on the hourly budget.

        After the first success per hub we back off to
        MEROSS_BATTERY_POLL_INTERVAL; until then we retry every
        MEROSS_BATTERY_RETRY_INTERVAL. Failures are non-fatal.
        """
        hub_uuid = getattr(hub_device, 'uuid', None)
        try:
            if not hub_battery_poll_due(hub_uuid, MEROSS_BATTERY_POLL_INTERVAL, MEROSS_BATTERY_RETRY_INTERVAL):
                return
            if not hasattr(hub_device, '_execute_command'):
                return
            try:
                subdevices = list(hub_device.get_subdevices()) if hasattr(hub_device, 'get_subdevices') else []
            except Exception as e:
                log.debug(f"Konnte Subdevices für Batterie-Abfrage nicht lesen: {e}")
                subdevices = []
            if not subdevices:
                return

            # One cloud message to the hub -> respect its hourly budget.
            if not self._consume_budget(hub_uuid, 1):
                log.debug(f"Batterie-Abfrage übersprungen (Budget) für Hub {hub_uuid}")
                mark_hub_battery_attempt(hub_uuid, False)
                return

            from meross_iot.model.enums import Namespace
            try:
                resp = await asyncio.wait_for(
                    hub_device._execute_command(
                        method='GET', namespace=Namespace.HUB_BATTERY, payload={'battery': []}),
                    timeout=8.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                log.debug(f"Batterie-Abfrage Timeout für Hub {getattr(hub_device, 'name', '?')}")
                mark_hub_battery_attempt(hub_uuid, False)
                return
            except Exception as e:
                log.debug(f"Batterie-Abfrage fehlgeschlagen für Hub {getattr(hub_device, 'name', '?')}: {type(e).__name__}: {e}")
                mark_hub_battery_attempt(hub_uuid, False)
                return

            # Raw response logged so any unexpected format is visible in the log.
            log.debug(f"HUB_BATTERY Rohantwort für {getattr(hub_device, 'name', '?')}: {resp}")

            entries = resp.get('battery', []) if isinstance(resp, dict) else []
            id_to_value = {}
            for entry in entries:
                sid = entry.get('id') if isinstance(entry, dict) else None
                val = self._extract_battery_value(entry)
                if sid is not None and val is not None:
                    id_to_value[sid] = val

            polled_any = False
            for subdev in subdevices:
                sid = getattr(subdev, 'subdevice_id', None)
                if sid in id_to_value:
                    set_subdevice_battery(sid, id_to_value[sid])
                    polled_any = True
                    log.debug(f"Batterie {getattr(subdev, 'name', '?')}: {id_to_value[sid]}%")
                else:
                    log.debug(f"Batterie {getattr(subdev, 'name', '?')} (id={sid}): kein Wert in der Antwort")

            # Record the attempt; the full-interval back-off only kicks in once
            # at least one battery value was obtained (see hub_battery_poll_due).
            mark_hub_battery_attempt(hub_uuid, polled_any)
        except Exception as e:
            log.debug(f"Ignorierter Fehler in _poll_hub_batteries: {e}")
            mark_hub_battery_attempt(hub_uuid, False)

    def update_device_status(self, devices):
        """
        Updates only the status of existing devices (FASTER than get_devices)

        Args:
            devices: list of MerossDevice objects to update
        """
        if not self._running:
            raise RuntimeError(_("Nicht angemeldet"))
        
        log.debug(f"Meross API: Aktualisiere Status von {len(devices)} Geräten...")
        
        async def _update_status():
            # Import for specific exception types
            try:
                from meross_iot.model.exception import CommandTimeoutError
            except ImportError:
                CommandTimeoutError = Exception  # fallback
            
            # Parallel update function for a single device
            async def update_single_device(meross_device):
                # Skip offline devices
                if hasattr(meross_device, 'is_offline') and meross_device.is_offline:
                    return (False, None)  # do not update offline devices

                # Rate limit protection: status poll (1 cloud message) only
                # when the device's hourly budget allows it. Otherwise skip and
                # inform the user (throttled) - prevents the 24-hour ban.
                if not self._consume_budget(meross_device.uuid, 1):
                    self._notify_throttle(getattr(meross_device, 'name', None))
                    return (False, None)

                try:
                    # Find the original device
                    orig_devices = self.manager.find_devices(device_uuids=[meross_device.uuid])
                    if orig_devices:
                        orig_device = orig_devices[0]

                        # async_update with timeout handling
                        try:
                            await asyncio.wait_for(orig_device.async_update(), timeout=5.0)
                        except asyncio.TimeoutError:
                            # A timeout is normal for slow/offline devices - no
                            # error log
                            return (False, None)
                        except asyncio.CancelledError:
                            # The task was cancelled - normal during cleanup
                            return (False, None)

                        # Load power metrics for electricity devices - but
                        # DECOUPLED from the status poll: at most every
                        # MEROSS_METRICS_MIN_INTERVAL seconds per device and
                        # only when the budget still allows the second cloud
                        # message.
                        if meross_device.has_power_meter and hasattr(orig_device, 'async_get_instant_metrics'):
                            now = time.time()
                            last = self._last_metrics_fetch.get(meross_device.uuid, 0.0)
                            if (now - last) >= MEROSS_METRICS_MIN_INTERVAL and self._consume_budget(meross_device.uuid, 1):
                                try:
                                    metrics = await asyncio.wait_for(
                                        orig_device.async_get_instant_metrics(),
                                        timeout=2.0
                                    )
                                    if metrics is not None:
                                        meross_device._cached_metrics = metrics
                                    # Only record after a successful query so a
                                    # timeout does not block the next attempt.
                                    self._last_metrics_fetch[meross_device.uuid] = now
                                except (asyncio.TimeoutError, asyncio.CancelledError):
                                    pass  # a timeout for metrics is OK
                                except Exception as e:
                                    log.debug(f"Ignorierter Fehler in update_single_device: {e}")

                        # Update the wrapper object
                        meross_device._device = orig_device
                        meross_device._update_status()
                        
                        # Collect hub info
                        hub_device = None
                        if meross_device.is_hub:
                            hub_device = orig_device
                        elif meross_device.type.lower() == 'ms130' and hasattr(orig_device, '_hub'):
                            hub_device = orig_device._hub
                        
                        return (True, hub_device)
                        
                except CommandTimeoutError:
                    # CommandTimeoutError is normal for offline/slow devices
                    return (False, None)
                except asyncio.TimeoutError:
                    # A timeout is normal for slow/offline devices
                    return (False, None)
                except asyncio.CancelledError:
                    # The task was cancelled - normal during cleanup
                    return (False, None)
                except Exception as e:
                    # Only log unknown errors (as DEBUG, not ERROR)
                    error_str = str(e).lower()
                    if 'timeout' not in error_str and 'cancelled' not in error_str:
                        log.debug(f"Status-Update fehlgeschlagen für {meross_device.name}: {type(e).__name__}")
                    return (False, None)
                
                return (False, None)
            
            # Really query each underlying Meross UUID only ONCE.
            #
            # Hub subdevices (water/temperature/humidity sensor on an MSH300
            # hub) all get the SAME UUID from meross_iot - namely the hub's.
            # Triggering a separate async_update() per subdevice would multiply
            # the cloud load on ONE single, per-device rate-limited UUID.
            # Exactly that filled up the shared hub budget and then throttled
            # one of the subdevices seemingly at random (e.g. the rarely used
            # water sensor).
            #
            # Solution: per UUID only ONE representative is really queried (=
            # one cloud call, one budget token); the remaining wrappers of the
            # same UUID are then refreshed from the same, already updated
            # device object - without another cloud call.
            uuid_groups = {}
            for d in devices:
                if getattr(d, 'is_offline', False):
                    continue
                uuid_groups.setdefault(d.uuid, []).append(d)

            update_tasks = [update_single_device(grp[0]) for grp in uuid_groups.values()]
            results = await asyncio.gather(*update_tasks, return_exceptions=True)

            # Refresh sibling wrappers (other subdevices on the same hub) from
            # the representative's device object.
            for grp in uuid_groups.values():
                rep_dev = getattr(grp[0], '_device', None)
                for sibling in grp[1:]:
                    try:
                        if rep_dev is not None:
                            sibling._device = rep_dev
                        sibling._update_status()
                    except Exception as e:
                        log.debug(f"Ignorierter Fehler in _update_status: {e}")
            
            # Count the successes and collect the hubs
            updated = 0
            failed = 0
            hubs = {}
            
            for result in results:
                if isinstance(result, tuple):
                    success, hub_device = result
                    if success:
                        updated += 1
                        if hub_device and hasattr(hub_device, 'uuid'):
                            hubs[hub_device.uuid] = hub_device
                    else:
                        failed += 1
                else:
                    failed += 1
            
            # Update all hubs for fresh subdevice data (important for MS130!)
            if hubs:
                log.debug(f"Aktualisiere {len(hubs)} Hub(s) für Subdevice-Daten...")
                
                async def update_single_hub(hub_device):
                    updated_ok = False
                    try:
                        # Hub update with its own timeout (hubs are often
                        # slower)
                        await asyncio.wait_for(hub_device.async_update(), timeout=8.0)
                        log.debug(f"Hub {hub_device.name} aktualisiert")
                        updated_ok = True
                    except asyncio.TimeoutError:
                        # A timeout is normal for slow hubs - no log
                        pass
                    except asyncio.CancelledError:
                        return False
                    except Exception as e:
                        # Only log unknown errors
                        error_str = str(e).lower()
                        if 'timeout' not in error_str and 'cancelled' not in error_str and 'subdevice' not in error_str:
                            log.debug(f"Hub-Update fehlgeschlagen für {hub_device.name}: {type(e).__name__}")

                    # Poll battery levels INDEPENDENTLY of the (often timing-out)
                    # full hub update: the HUB_BATTERY GET is a small separate
                    # call, so it must not be gated behind async_update success -
                    # otherwise a slow hub would never report a battery value.
                    try:
                        await self._poll_hub_batteries(hub_device)
                    except Exception as e:
                        log.debug(f"Ignorierter Fehler beim Batterie-Poll: {e}")

                    return updated_ok
                
                # Update all hubs IN PARALLEL
                hub_tasks = [update_single_hub(hub_device) for hub_device in hubs.values()]
                await asyncio.gather(*hub_tasks, return_exceptions=True)
            
            log.debug(f"Status-Update abgeschlossen: {updated} erfolgreich, {failed} fehlgeschlagen")
        
        try:
            # PARALLEL updates are MUCH faster - aggressive timeout! Only
            # online devices count for the timeout (offline ones are skipped
            # immediately)
            online_devices = [d for d in devices if not (hasattr(d, 'is_offline') and d.is_offline)]
            # OPTIMIZED: 10s minimum (hubs need up to 8s), plus 0.2s per device
            timeout = max(10, len(online_devices) * 0.2)
            log.debug(f"Paralleles Update ({len(online_devices)} online von {len(devices)}) mit Timeout: {timeout:.1f}s")
            self._run_async(_update_status(), timeout=timeout)
        except TimeoutError:
            log.debug(f"Status-Update Timeout nach {timeout:.0f}s - einige Geräte haben nicht rechtzeitig geantwortet")
            # No raise - partial updates are OK
        except Exception as e:
            log.error(f"Fehler beim Aktualisieren: {type(e).__name__}: {e}")
            raise
    
    @staticmethod
    def summarize_daily_consumption(data):
        """(kwh_heute, kwh_7tage) aus consumptionX-Daten berechnen."""
        import datetime as _dt
        today = _dt.date.today()
        week_start = today - _dt.timedelta(days=6)
        kwh_today = sum(
            e['total_consumption_kwh'] for e in data
            if e['date'].date() == today)
        kwh_week = sum(
            e['total_consumption_kwh'] for e in data
            if week_start <= e['date'].date() <= today)
        return kwh_today, kwh_week

    # TTL des Verbrauchs-Caches: Die Tageswerte ändern sich nur langsam;
    # 15 Minuten halten die Cloud-Last bei maximal 4 Nachrichten pro Stunde
    # und Gerät - unkritisch fürs 200er-Stundenbudget, auch wenn der Dialog
    # oft geöffnet/aktualisiert wird.
    CONSUMPTION_CACHE_TTL = 900.0

    def peek_daily_consumption(self, device_uuid):
        """Liefert die zuletzt abgerufenen Tagesverbräuche aus dem Cache.

        KEIN Netzwerkzugriff - für die Anzeige im Dialog. Liefert auch
        leicht veraltete Daten (besser als nichts); None, wenn noch nie
        abgerufen wurde.
        """
        cached = self._consumption_cache.get(device_uuid)
        return cached[1] if cached else None

    def get_daily_consumption(self, device_uuid):
        """Liest die vom GERÄT selbst gezählten Tagesverbräuche (consumptionX).

        Die Messsteckdosen (MSS310/315, MOP320) zählen ihren Verbrauch
        intern weiter - auch wenn NVDA/das Add-on nicht läuft. Diese Werte
        sind daher vollständiger als die eigenen Leistungs-Stichproben.

        Cloud-schonend: Ergebnisse werden CONSUMPTION_CACHE_TTL Sekunden
        gecacht; innerhalb der TTL kostet der Aufruf KEINE Cloud-Nachricht.
        Zusätzlich greift das normale Pro-Gerät-Budget.

        Returns:
            Liste von {'date': datetime, 'total_consumption_kwh': float}
            oder None, wenn das Gerät die Abfrage nicht unterstützt oder das
            Cloud-Budget sie gerade nicht erlaubt.
        """
        if not self._running:
            return None
        # Frischer Cache -> keine Cloud-Nachricht
        cached = self._consumption_cache.get(device_uuid)
        if cached and (time.time() - cached[0]) < self.CONSUMPTION_CACHE_TTL:
            return cached[1]
        if not self._consume_budget(device_uuid, 1):
            log.debug(f"Verbrauchsabfrage übersprungen (Budget): {device_uuid}")
            return cached[1] if cached else None
        try:
            orig_devices = self.manager.find_devices(device_uuids=[device_uuid])
            if not orig_devices:
                return None
            orig = orig_devices[0]
            if not hasattr(orig, 'async_get_daily_power_consumption'):
                return None

            async def _fetch():
                return await asyncio.wait_for(
                    orig.async_get_daily_power_consumption(), timeout=6.0)

            data = self._run_async(_fetch(), timeout=10)
            if data is not None:
                self._consumption_cache[device_uuid] = (time.time(), data)
            return data
        except Exception as e:
            log.debug(f"Tagesverbrauch nicht abrufbar für {device_uuid}: {type(e).__name__}")
            # Bei Fehlern lieber veraltete Cache-Daten als gar nichts
            return cached[1] if cached else None

    def set_device_state(self, uuid, state, channel=None):
        """
        Switches a device or a channel on or off

        Args:
            uuid: device UUID
            state: True = switch on, False = switch off
            channel: channel index (optional, for multi-channel devices)
        """
        if not self._running:
            raise RuntimeError(_("Nicht angemeldet"))
        
        channel_info = f" Channel {channel}" if channel is not None else ""
        log.info(f"Meross API: Setze Gerät {uuid}{channel_info} auf {state}")
        
        async def _set_state():
            devices = self.manager.find_devices(device_uuids=[uuid])
            
            if not devices:
                # The device was not found - probably offline or no longer
                # connected
                # Translators: Error message: Meross device unreachable.
                raise RuntimeError(_("Gerät nicht erreichbar - möglicherweise offline oder Verbindung unterbrochen"))
            
            device = devices[0]
            
            # SPECIAL: MOD150 diffusers use DiffuserSprayMixin instead of
            # ToggleMixin. Check for diffuser specifics first
            if hasattr(device, 'async_set_spray_mode'):
                # MOD150 diffuser: use spray_mode
                try:
                    if state:
                        # Turn on: set to LIGHT (light mist)
                        from meross_iot.model.enums import DiffuserSprayMode
                        await asyncio.wait_for(
                            device.async_set_spray_mode(mode=DiffuserSprayMode.LIGHT, channel=channel or 0), 
                            timeout=10.0
                        )
                        log.info(f"Diffuser {device.name} eingeschaltet (LIGHT mode)")
                    else:
                        # Turn off: set to OFF
                        from meross_iot.model.enums import DiffuserSprayMode
                        await asyncio.wait_for(
                            device.async_set_spray_mode(mode=DiffuserSprayMode.OFF, channel=channel or 0), 
                            timeout=10.0
                        )
                        log.info(f"Diffuser {device.name} ausgeschaltet")
                    return  # switched successfully
                except asyncio.TimeoutError:
                    # Translators: Error message when a diffuser does not
                    # respond to the on/off command.
                    raise TimeoutError(_("Diffuser nicht erreichbar - antwortet nicht auf Schaltbefehl"))
            
            # Default: ToggleMixin (async_turn_on/async_turn_off)
            if not hasattr(device, 'async_turn_on') or not hasattr(device, 'async_turn_off'):
                # Translators: Placeholder for an unknown device type.
                device_type = device.type if hasattr(device, 'type') else _('Unbekannt')
                # Translators: Error message: device type is not switchable.
                raise ValueError(_("Gerät {name} ({type}) kann nicht geschaltet werden (z.B. Hub oder Sensor)").format(
                    name=device.name, type=device_type))
            
            # Switch (with or without channel) - with timeout handling
            try:
                if state:
                    if channel is not None:
                        await asyncio.wait_for(device.async_turn_on(channel=channel), timeout=10.0)
                        log.debug(f"Gerät {device.name} Channel {channel} eingeschaltet")
                    else:
                        await asyncio.wait_for(device.async_turn_on(), timeout=10.0)
                        log.debug(f"Gerät {device.name} eingeschaltet")
                else:
                    if channel is not None:
                        await asyncio.wait_for(device.async_turn_off(channel=channel), timeout=10.0)
                        log.debug(f"Gerät {device.name} Channel {channel} ausgeschaltet")
                    else:
                        await asyncio.wait_for(device.async_turn_off(), timeout=10.0)
                        log.debug(f"Gerät {device.name} ausgeschaltet")
            except asyncio.TimeoutError:
                # Translators: Error message: device does not respond to the
                # switch command.
                raise TimeoutError(_("Gerät nicht erreichbar - antwortet nicht auf Schaltbefehl"))
        
        try:
            self._run_async(_set_state())
        except Exception as e:
            log.error(f"Fehler beim Schalten: {e}")
            raise
    
    def set_diffuser_spray_mode(self, uuid, spray_mode):
        """
        Sets the spray mode of a diffuser

        Args:
            uuid: device UUID
            spray_mode: DiffuserSprayMode (LIGHT, STRONG, OFF)
        """
        if not self._running:
            raise RuntimeError(_("Nicht angemeldet"))
        
        log.info(f"Meross API: Setze Diffuser {uuid} auf Mode {spray_mode}")
        
        async def _set_spray():
            devices = self.manager.find_devices(device_uuids=[uuid])
            
            if not devices:
                # Translators: Error message: diffuser unreachable.
                raise RuntimeError(_("Diffuser nicht erreichbar - möglicherweise offline"))
            
            device = devices[0]
            
            if not hasattr(device, 'async_set_spray_mode'):
                # Translators: Error message: device does not support diffuser
                # functions.
                raise RuntimeError(_("Gerät {name} ist kein Diffuser").format(name=device.name))
            
            try:
                await asyncio.wait_for(
                    device.async_set_spray_mode(mode=spray_mode, channel=0), 
                    timeout=10.0
                )
                log.info(f"Diffuser {device.name} auf Mode {spray_mode} gesetzt")
            except asyncio.TimeoutError:
                # Translators: Error message when a diffuser does not respond
                # to the mode command.
                raise TimeoutError(_("Diffuser nicht erreichbar - antwortet nicht auf Befehl"))
        
        try:
            self._run_async(_set_spray())
        except Exception as e:
            log.error(f"Fehler beim Setzen des Spray-Modus: {e}")
            raise
    
    # ==================== Lamp functions (MSL450, MSL610, MSL320)
    # ====================
    
    def set_light_color(self, uuid, channel=0, onoff=None, rgb=None, luminance=None, temperature=None):
        """Sets the color, brightness or color temperature of a lamp

        Args:
            uuid: device UUID
            channel: channel (default: 0)
            onoff: optional - True = on, False = off, None = keep the state
            rgb: optional - (red, green, blue) tuple with values 0-255
            luminance: optional - brightness 0-100
            temperature: optional - color temperature 0-100

        Note:
            RGB and color temperature cannot be set at the same time!
        """
        if not self._running:
            raise RuntimeError(_("Nicht angemeldet"))
        
        log.info(f"Meross API: Setze Lichtfarbe für {uuid} - RGB={rgb}, Luminance={luminance}, Temp={temperature}, OnOff={onoff}")
        
        async def _set_light():
            devices = self.manager.find_devices(device_uuids=[uuid])
            
            if not devices:
                # Translators: Error message: lamp unreachable.
                raise RuntimeError(_("Lampe nicht erreichbar - möglicherweise offline"))
            
            device = devices[0]
            
            if not hasattr(device, 'async_set_light_color'):
                # Translators: Error message: device is not a (color-capable)
                # lamp.
                raise RuntimeError(_("Gerät {name} ist keine Lampe oder unterstützt keine Farbsteuerung").format(name=device.name))
            
            try:
                await asyncio.wait_for(
                    device.async_set_light_color(
                        channel=channel,
                        onoff=onoff,
                        rgb=rgb,
                        luminance=luminance,
                        temperature=temperature
                    ),
                    timeout=10.0
                )
                log.info(f"Lampe {device.name} erfolgreich konfiguriert")
            except asyncio.TimeoutError:
                # Translators: Error message when a lamp does not respond to
                # the command.
                raise TimeoutError(_("Lampe nicht erreichbar - antwortet nicht auf Befehl"))
        
        try:
            self._run_async(_set_light())
        except Exception as e:
            log.error(f"Fehler beim Setzen der Lichtfarbe: {e}")
            raise
    
    def set_light_rgb(self, uuid, red, green, blue, channel=0):
        """Sets the RGB color of a lamp (convenience function)

        Args:
            uuid: device UUID
            red: red value 0-255
            green: green value 0-255
            blue: blue value 0-255
            channel: channel (default: 0)
        """
        if not (0 <= red <= 255 and 0 <= green <= 255 and 0 <= blue <= 255):
            # Translators: Error message for invalid RGB values.
            raise ValueError(_("RGB-Werte müssen zwischen 0 und 255 liegen"))
        
        self.set_light_color(uuid=uuid, channel=channel, rgb=(red, green, blue))
    
    def set_light_luminance(self, uuid, luminance, channel=0):
        """Sets the brightness of a lamp (convenience function)

        Args:
            uuid: device UUID
            luminance: brightness 0-100
            channel: channel (default: 0)
        """
        if not (0 <= luminance <= 100):
            # Translators: Error message for an invalid brightness value.
            raise ValueError(_("Helligkeit muss zwischen 0 und 100 liegen"))
        
        self.set_light_color(uuid=uuid, channel=channel, luminance=luminance)
    
    def set_light_temperature(self, uuid, temperature, channel=0):
        """Sets the color temperature of a lamp (convenience function)

        Args:
            uuid: device UUID
            temperature: color temperature 0-100 (0=warm, 100=cool)
            channel: channel (default: 0)
        """
        if not (0 <= temperature <= 100):
            # Translators: Error message for an invalid color temperature
            # value.
            raise ValueError(_("Farbtemperatur muss zwischen 0 und 100 liegen"))
        
        self.set_light_color(uuid=uuid, channel=channel, temperature=temperature)
    
    def set_light_white(self, uuid, white_type="tageslicht", channel=0):
        """Sets a predefined white tone (convenience function)

        Args:
            uuid: device UUID
            white_type: white tone type - "warm" or "warmweiss" (2700K ~ 0)
                                      - "tageslicht" or "neutral" (4000K ~ 50)
                                      - "kalt" or "kaltweiss" (6500K ~ 100)
            channel: channel (default: 0)
        """
        white_type = white_type.lower().strip()
        
        # Mapping of white tone names to temperature values (0-100)
        white_presets = {
            "warm": 0,
            "warmweiss": 0,
            "warmweiß": 0,
            "warmwhite": 0,
            "tageslicht": 50,
            "neutral": 50,
            "neutralweiss": 50,
            "neutralweiß": 50,
            "daylight": 50,
            "kalt": 100,
            "kaltweiss": 100,
            "kaltweiß": 100,
            "coldwhite": 100,
            "cool": 100
        }
        
        if white_type not in white_presets:
            valid_types = ", ".join(sorted(set(white_presets.keys())))
            # Translators: Error message for an invalid white tone name.
            raise ValueError(_("Ungültiger Weißton '{value}'. Gültige Werte: {valid}").format(
                value=white_type, valid=valid_types))
        
        temperature = white_presets[white_type]
        log.info(f"Setze Lampe {uuid} auf Weißton '{white_type}' (Temperatur={temperature})")
        self.set_light_color(uuid=uuid, channel=channel, temperature=temperature)
    
    def _cleanup(self):
        """Cleans up resources"""
        log.debug("Meross API: Starte Cleanup...")
        
        # Close the manager
        if self.manager:
            try:
                async def _close_manager():
                    await self.manager.async_close()
                
                if self.loop and self.loop.is_running():
                    self._run_async(_close_manager(), timeout=5)
            except Exception as e:
                log.debug(f"Manager-Close Fehler: {e}")
        
        # HTTP client logout
        if self.http_client:
            try:
                async def _logout():
                    await self.http_client.async_logout()
                
                if self.loop and self.loop.is_running():
                    self._run_async(_logout(), timeout=5)
            except Exception as e:
                log.debug(f"HTTP-Logout Fehler: {e}")
        
        # Stop the event loop (set the flag FIRST!)
        self._running = False

        if self.loop and self.loop.is_running():
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception as e:
                log.debug(f"Loop-Stop Fehler: {e}")

        # Wait for the thread to end - a bit more generous than 2 s so slow
        # network cleanups (TLS close, MQTT disconnect) are not cut off.
        if self.loop_thread and self.loop_thread.is_alive():
            log.debug("Warte auf Event-Loop-Thread...")
            self.loop_thread.join(timeout=5.0)
            if self.loop_thread.is_alive():
                # Close the loop hard so hanging tasks can be released, and try
                # to join a second time.
                log.warning("Event-Loop-Thread reagiert nicht – versuche Hard-Close")
                try:
                    if self.loop and not self.loop.is_closed():
                        self.loop.call_soon_threadsafe(self.loop.stop)
                except Exception as e:
                    log.debug(f"Ignorierter Fehler in _cleanup: {e}")
                self.loop_thread.join(timeout=2.0)
                if self.loop_thread.is_alive():
                    log.warning("Event-Loop-Thread konnte nicht sauber beendet werden")
            else:
                log.debug("Event-Loop-Thread erfolgreich beendet")

        log.debug("Meross API: Cleanup abgeschlossen")
    
    def logout(self):
        """Logs out and cleans up"""
        log.info("Meross API: Logout...")
        self._cleanup()
