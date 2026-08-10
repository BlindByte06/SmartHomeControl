# -*- coding: utf-8 -*-
"""
Smart Home Control - Meross device wrappers
Contains MerossChannel, MerossDevice and MerossOfflineDevice.
Extracted from meross_api.py for better modularity.

"""

import logging
import threading
import time
import warnings
from logHandler import log

import addonHandler
try:
    addonHandler.initTranslation()
except Exception as e:
    log.debug(f"initTranslation fehlgeschlagen: {e}")
if "_" not in globals():  # fallback outside of NVDA
    def _(s):
        return s

# Suppress known harmless warnings
warnings.filterwarnings('ignore', message='.*Callback API version.*deprecated.*')
warnings.filterwarnings('ignore', category=DeprecationWarning, module='paho.mqtt.client')

# How long a value received via MQTT push takes precedence over a polled one.
# Only meant to cover the overlap of a push and an in-flight poll - long
# enough for the poll already under way, short enough that a missed push heals
# on the next poll instead of freezing the channel forever.
PUSH_GRACE_SECONDS = 3.0


# ============================================================
# Sensor subdevice models (central so new models are only added HERE)
# ============================================================
# Temperature/humidity: ms100, ms130 - water/leak: ms400, ms405.
TEMPERATURE_SENSOR_TYPES = ('ms100', 'ms130')
WATER_SENSOR_TYPES = ('ms400', 'ms405')
SENSOR_SUBDEVICE_TYPES = TEMPERATURE_SENSOR_TYPES + WATER_SENSOR_TYPES


def is_temperature_sensor_type(device_type):
    """True for temperature/humidity sensors (ms100, ms130)."""
    t = (device_type or '').lower()
    return any(s in t for s in TEMPERATURE_SENSOR_TYPES)


def is_water_sensor_type(device_type):
    """True for water/leak sensors (ms400, ms405)."""
    t = (device_type or '').lower()
    return any(s in t for s in WATER_SENSOR_TYPES)


def is_sensor_type(device_type):
    """True if the device/subdevice type is a known Meross sensor."""
    return is_temperature_sensor_type(device_type) or is_water_sensor_type(device_type)


# ============================================================
# Plug / power-strip models (central so new models are only added HERE).
# Previously these lists were duplicated between the online (MerossDevice)
# and offline (MerossOfflineDevice) path, which is how the MOP320 ended up
# being a plug with a power meter online but neither offline.
# ============================================================
PLUG_TYPES = (
    'mss210', 'mss310', 'mss315',
    'mss425',   # matches mss425, mss425e, mss425f (substring)
    'mss620',
    'mop320',   # outdoor socket, 2 outlets, per-outlet metering
)
POWER_METER_TYPES = ('mss310', 'mss315', 'mop320')

# Fallback outlet counts for multi-outlet models, used ONLY when the cloud
# does not deliver a usable ``channels`` list. The authoritative source is
# always the raw channel data (see _outlets_from_raw_channels) - these numbers
# exist so a device does not silently lose its outlets when the HTTP list is
# incomplete. Longest key wins, so 'mss425e' is matched before 'mss425'.
MULTI_OUTLET_FALLBACK = {
    'mss620': 2,    # 2 sockets
    'mss425e': 4,   # 3 sockets + 1 USB group (EU)
    'mss425f': 5,   # 4 sockets + 1 USB group
    'mss425': 4,    # generic 425 series
    'mop320': 2,    # 2 sockets
}


def _matches_any(device_type, candidates):
    """True if any candidate appears in the (lowercased) device type."""
    t = (device_type or '').lower()
    return any(c in t for c in candidates)


def is_plug_type(device_type):
    """True for known switchable socket models."""
    return _matches_any(device_type, PLUG_TYPES)


def has_power_meter_type(device_type):
    """True for models that report power/voltage/current."""
    return _matches_any(device_type, POWER_METER_TYPES)


def fallback_outlet_count(device_type):
    """Known outlet count for a multi-outlet model, or 0 if not a known one."""
    t = (device_type or '').lower()
    for key in sorted(MULTI_OUTLET_FALLBACK, key=len, reverse=True):
        if key in t:
            return MULTI_OUTLET_FALLBACK[key]
    return 0


def _outlets_from_raw_channels(raw_channels, device_type):
    """Determines the real outlet channel indices of a multi-outlet device.

    ``raw_channels`` is the ``channels`` list from the HTTP device list
    (``HttpDeviceInfo.channels``, a list of dicts) - the SAME source that
    ``meross_iot`` parses into ``device.channels`` for the online path.
    Index 0 is always the master channel (it switches the whole device and
    carries no outlet name); see ``BaseDevice._parse_channels``.

    Returns a list of real channel indices, e.g. [1, 2] for an MSS620 or
    [1, 2, 3, 4, 5] for an MSS425F. Empty list = not a multi-outlet device.

    Deriving the indices instead of hardcoding a count is what keeps the
    offline path aligned with the online path: same indices means same
    channel UUIDs (favorites keep working) and same outlet names.
    """
    count = len(raw_channels) if raw_channels else 0
    if count > 2:
        # master + at least 2 outlets -> trust the cloud data
        return list(range(1, count))

    fallback = fallback_outlet_count(device_type)
    if not fallback:
        return []

    if count == fallback:
        # Genau so viele Einträge, wie das Modell Ausgänge hat: die Cloud hat
        # den Master-Eintrag weggelassen, die Einträge SIND die Ausgänge.
        # Diesen Fall gibt es bei manchen Firmware-Ständen - früher fielen
        # dabei beide Ausgänge aus dem Menü, weil meross_iot Kanal 0 immer
        # als Master markiert und der Filter darauf danach nur einen Kanal
        # übrig ließ.
        log.warning(
            f"Meross {device_type}: Kanalliste ohne Master-Eintrag "
            f"({count} Einträge) - werte sie als {count} Ausgänge")
        return list(range(0, count))

    # The cloud sent an incomplete/missing channel list for a model we
    # know to have several outlets. Assume the usual layout
    # (index 0 = master, 1..n = outlets) so the outlets stay reachable.
    log.warning(
        f"Meross {device_type}: Kanalliste der Cloud unbrauchbar "
        f"({count} Einträge) - nehme {fallback} Ausgänge an")
    return list(range(1, fallback + 1))


def _clean_outlet_name(val):
    """Bereinigt einen Ausgangsnamen aus der Meross-Cloud.

    Der meross_iot-Platzhalter "Main channel"/"master" und leere Werte
    gelten NICHT als echter Name. Rückgabe: bereinigter Name oder None.
    """
    if not val:
        return None
    s = str(val).strip()
    if not s or s.lower() in ("main channel", "master"):
        return None
    return s


def _outlet_name_from_raw_channels(raw_channels, index):
    """Liest den Ausgangsnamen (devName) aus den HTTP-Rohkanaldaten.

    ``raw_channels`` ist die ``channels``-Liste aus der HTTP-Geräteliste
    (Liste von dicts). Rückgabe: Name oder None. Funktioniert online wie
    offline, da diese Daten schon bei der Geräteliste geliefert werden.
    """
    try:
        if raw_channels and 0 <= index < len(raw_channels):
            raw = raw_channels[index]
            if isinstance(raw, dict):
                return _clean_outlet_name(raw.get('devName'))
    except Exception as e:
        log.debug(f"Ausgangsname aus Rohdaten fehlgeschlagen: {e}")
    return None


# ============================================================
# Smart error filter for known harmless meross_iot errors
# ============================================================

class MerossErrorFilter(logging.Filter):
    """Filters known harmless meross_iot errors and logs them as DEBUG instead of ERROR"""

    KNOWN_HARMLOSE_PATTERNS = [
        # Unhandled events (harmless - device sends unimplemented data)
        'Appliance.Control.Sensor.LatestX is not currently handled',
        'Unhandled/NotImplemented event handler for Namespace.HUB_SENSOR_ALL',
        'Push notification parsing failed',
        # Unimplemented namespaces
        'is not currently handled/recognized',
        'Appliance.Config.DeviceCfg',
        # MQTT warnings
        'Callback API version 1 is deprecated',
        'that has become online while we were offline',
        # Timeouts
        'Timeout occurred while waiting',
        'TimeoutError',
        'CommandTimeoutError',
        'asyncio.exceptions.CancelledError',
        'Error occurred during subdevice update',
        '_async_send_and_wait_ack',
        # Hub/subdevice errors
        'HubMts100Mixin',
        'subdevice update',
        # Enrollment errors
        'ENROLLEMENT: Failed to enroll device',
        'It must be offline',
        'Device is unreachable',
        # Gerätetyp aus der eingebauten Tabelle erkannt - reine Information.
        'was built statically via known types',
        # ---- Wiederverbindung der MQTT-Sitzung ----
        # Bricht die MQTT-Verbindung ab, setzt meross_iot ALLE Geräte auf
        # OnlineStatus.UNKNOWN (_notify_connection_drop schickt status -1).
        # Kommt die Verbindung zurück, ermittelt die Bibliothek den Status neu
        # und meldet für JEDES Gerät zwei Zeilen: "Updating status for device
        # X" und "X changed its online status ... (was UNKNOWN, now is
        # ONLINE)". Bei zehn Geräten sind das zwanzig Zeilen pro
        # Wiederverbindung - und der MQTT-Keepalive steht auf 30 Sekunden,
        # also reicht ein kurzer WLAN-Wechsel.
        #
        # Das ist keine Störung, sondern die Reparatur: genau dadurch stimmt
        # der Status nach einer Netzlücke wieder. Die erste der beiden Zeilen
        # ist im Bibliothekscode erkennbar eine Debug-Ausgabe, die
        # versehentlich als warning deklariert wurde.
        'Updating status for device',
        'changed its online status while manager was offline',
        # Beim Beenden von NVDA/Abmelden: Antworten treffen ein, nachdem der
        # Event-Loop schon zu ist.
        'as the event loop has been closed already',
        # Nicht implementierte Namespaces - dieselbe Klasse wie die schon
        # gefilterten "is not currently handled"-Meldungen.
        'Uncaught push notification',
        'does not handle messages received on topic',
        # Hub-Meldungen: Sensordaten treffen ein, bevor das Unterzubehör
        # registriert ist. Betrifft vor allem MS100/MS130 am MSH300/MSH450.
        'that has not yet been',
        'which has not been registered with this',
    ]

    # BEWUSST NICHT gefiltert, obwohl sie ebenfalls aus meross_iot kommen -
    # sie können auf ein echtes Problem hinweisen und sollen sichtbar bleiben:
    #   'Failed to subscribe to topics'          - Verbindung wirklich kaputt
    #   'Invalid signature received'             - sicherheitsrelevant
    #   'Unhandled message method'               - Bibliothek bittet um Meldung
    #   'not available in the local registry'    - unsere Geräteliste ist alt
    #   'This future is already done'            - interner Fehler
    #   'Please invoke async_update()'           - Programmierfehler

    def filter(self, record):
        """Filters ERROR messages and downgrades known harmless ones to DEBUG"""
        msg = record.getMessage()
        if record.levelno >= logging.WARNING:
            if any(pattern in msg for pattern in self.KNOWN_HARMLOSE_PATTERNS):
                record.levelno = logging.DEBUG
                record.levelname = 'DEBUG'
        return True


def _install_error_filter():
    """Installs the error filter for meross_iot loggers"""
    filter_instance = MerossErrorFilter()
    logger_names = [
        'meross_iot.model.enums',
        'meross_iot.manager',
        'meross_iot.controller.device',
        'meross_iot.controller.mixins.hub',
        'meross_iot.controller.mixins.runtime',
    ]
    for logger_name in logger_names:
        logger = logging.getLogger(logger_name)
        logger.addFilter(filter_instance)
    log.info("Meross Error-Filter installiert (unterdrückt bekannte harmlose Fehler)")


# ============================================================
# Check whether meross_iot is installed
# ============================================================

try:
    from meross_iot.http_api import MerossHttpClient
    from meross_iot.manager import MerossManager
    from meross_iot.controller.device import GenericSubDevice
    from meross_iot.model.enums import OnlineStatus
    MEROSS_AVAILABLE = True

    # SSL fix for NVDA (Python 3.11/win32 and Python 3.13/win-amd64): wire the
    # certifi CA bundle into aiohttp so Meross certificates are verified even
    # without the system trust store.
    try:
        import ssl
        import certifi
        import aiohttp
        import warnings
        import meross_iot.http_api as _meross_http_api

        _ssl_context = ssl.create_default_context(cafile=certifi.where())

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)

            class _CertifiClientSession(aiohttp.ClientSession):
                """aiohttp.ClientSession with certifi CA bundle for correct SSL verification."""
                def __init__(self, *args, **kwargs):
                    if 'connector' not in kwargs:
                        kwargs['connector'] = aiohttp.TCPConnector(ssl=_ssl_context)
                    super().__init__(*args, **kwargs)

        _meross_http_api.ClientSession = _CertifiClientSession
        log.info(f"SSL-Fix aktiv: meross_iot nutzt certifi CA-Bundle ({certifi.where()})")
    except Exception as _ssl_fix_err:
        log.warning(f"SSL-Fix konnte nicht angewendet werden: {_ssl_fix_err}")

except ImportError:
    MEROSS_AVAILABLE = False
    MerossHttpClient = None  # noqa: N816
    MerossManager = None  # noqa: N816
    GenericSubDevice = None
    OnlineStatus = None
    log.warning("meross_iot nicht installiert!")


# ============================================================
# Thread-safe global databases for MS130 events & light mode
# ============================================================

_sensor_lock = threading.Lock()

# Format: {subdevice_id: {temperature: {latest: 1029}, humidity: {latest: 540},
# _hub_uuid: "xxx"}}
_MS130_SENSOR_DATA = {}

# Mapping hub UUID -> list of subdevice IDs
_MS130_HUB_TO_SUBDEVS = {}

# Last logged values per subdevice (deduplication against log spam)
_MS130_LAST_LOGGED = {}

_light_lock = threading.Lock()

# Global cache for light modes (UUID -> 'rgb' or 'white')
_LIGHT_MODE_CACHE = {}

# ------------------------------------------------------------
# Battery cache for hub subdevices (MS100/MS130/valves).
# Battery comes from the HUB_BATTERY namespace, which async_update() does NOT
# fetch, so it is polled separately and cached here. Populated by the API layer
# (background thread), read by the dialog (UI thread) -> needs its own lock.
# ------------------------------------------------------------
_battery_lock = threading.Lock()
# subdevice_id -> {'value': int_percent, 'ts': float}
_SUBDEV_BATTERY = {}
# hub_uuid -> float timestamp of the last SUCCESSFUL battery poll
_HUB_BATTERY_LAST_SUCCESS = {}
# hub_uuid -> float timestamp of the last battery poll ATTEMPT (success or not)
_HUB_BATTERY_LAST_ATTEMPT = {}


def get_subdevice_battery(subdev_id):
    """Thread-safe read of the cached battery percentage (or None)."""
    if not subdev_id:
        return None
    with _battery_lock:
        rec = _SUBDEV_BATTERY.get(subdev_id)
        return rec['value'] if rec else None


def set_subdevice_battery(subdev_id, value):
    """Thread-safe write of a battery percentage for a subdevice."""
    if not subdev_id or value is None:
        return
    with _battery_lock:
        _SUBDEV_BATTERY[subdev_id] = {'value': value, 'ts': time.time()}


def hub_battery_poll_due(hub_uuid, success_interval, retry_interval):
    """True if the hub's subdevice batteries should be (re)polled now.

    After a SUCCESSFUL poll we wait ``success_interval`` (battery changes
    slowly). As long as no success has been recorded yet - or the last success
    is older than the interval - we retry every ``retry_interval`` so a
    transient failure (e.g. a slow/timing-out hub) heals on its own instead of
    being blocked for a full hour.
    """
    if not hub_uuid:
        return False
    with _battery_lock:
        now = time.time()
        last_ok = _HUB_BATTERY_LAST_SUCCESS.get(hub_uuid, 0)
        if (now - last_ok) < success_interval:
            return False
        last_try = _HUB_BATTERY_LAST_ATTEMPT.get(hub_uuid, 0)
        return (now - last_try) >= retry_interval


def mark_hub_battery_attempt(hub_uuid, success):
    """Records a battery poll attempt (and, if successful, its success time)."""
    if not hub_uuid:
        return
    with _battery_lock:
        now = time.time()
        _HUB_BATTERY_LAST_ATTEMPT[hub_uuid] = now
        if success:
            _HUB_BATTERY_LAST_SUCCESS[hub_uuid] = now


def get_sensor_data(subdev_id):
    """Thread-safe access to MS130 sensor data."""
    with _sensor_lock:
        return _MS130_SENSOR_DATA.get(subdev_id)


def set_sensor_data(subdev_id, data):
    """Thread-safe write access to MS130 sensor data."""
    with _sensor_lock:
        _MS130_SENSOR_DATA[subdev_id] = data


def get_sensor_data_keys():
    """Thread-safe copy of the known subdevice IDs."""
    with _sensor_lock:
        return list(_MS130_SENSOR_DATA.keys())


def register_hub_subdevice(hub_uuid, subdev_id):
    """Thread-safe registration of a hub -> subdevice mapping."""
    with _sensor_lock:
        if hub_uuid not in _MS130_HUB_TO_SUBDEVS:
            _MS130_HUB_TO_SUBDEVS[hub_uuid] = []
        if subdev_id not in _MS130_HUB_TO_SUBDEVS[hub_uuid]:
            _MS130_HUB_TO_SUBDEVS[hub_uuid].append(subdev_id)
            log.debug(f"MS130 Mapping: Hub {hub_uuid} → Subdevice {subdev_id}")


def check_and_update_logged(subdev_id, current_key):
    """Thread-safe deduplication logging. Returns True if new."""
    with _sensor_lock:
        last_key = _MS130_LAST_LOGGED.get(subdev_id)
        if current_key != last_key:
            _MS130_LAST_LOGGED[subdev_id] = current_key
            return True
        return False


def get_light_mode(uuid):
    """Thread-safe access to the global light mode cache."""
    with _light_lock:
        return _LIGHT_MODE_CACHE.get(uuid)


def set_light_mode_cache(uuid, mode):
    """Thread-safe write access to the global light mode cache."""
    with _light_lock:
        _LIGHT_MODE_CACHE[uuid] = mode


# ============================================================
# MS130 event handler monkey patch
# ============================================================

def _patch_ms130_event_handler():
    """Patches GenericSubDevice to intercept HUB_SENSOR_ALL events.

    IMPORTANT - behavior on NVDA module reload (NVDA+Ctrl+F3):
    On reload THIS module (``meross_devices``) is imported fresh and gets a
    new, empty ``_MS130_SENSOR_DATA`` dict. ``meross_iot`` however stays in
    ``sys.modules`` - the class ``GenericSubDevice`` (and thus an already
    installed handler) survives the reload.

    If the patch were skipped here with a simple "already patched?" check,
    the OLD handler from the previous load would stay installed. Its closure
    writes incoming MS130 events into the OLD (orphaned) dict while the new
    device wrappers read from the NEW, empty dict -> MS130 sensors show no
    data after the reload (MS100 is not affected because it reads directly
    from the meross_iot object).

    Solution: reinstall the handler on EVERY import so it is bound to the
    current module globals (``set_sensor_data`` etc.). The REAL original
    handler is saved once on the class object and always it (not the most
    recently installed one) is wrapped - so no growing handler chain stacks
    up across multiple reloads.
    """
    if not GenericSubDevice:
        return

    # Save the real meross_iot original handler only ONCE (on the class object,
    # which survives reloads). On further calls reuse the same original instead
    # of wrapping the already installed patch again.
    if hasattr(GenericSubDevice, '_ms130_original_handler'):
        original_handler = GenericSubDevice._ms130_original_handler
        reinstall = True
    else:
        original_handler = GenericSubDevice.async_handle_subdevice_notification
        GenericSubDevice._ms130_original_handler = original_handler
        reinstall = False

    async def patched_handler(self, namespace, data):
        """Intercept HUB_SENSOR_ALL events and store the data"""
        try:
            if 'temperature' in data or 'humidity' in data:
                subdev_id = data.get('id')
                if subdev_id:
                    set_sensor_data(subdev_id, data)

                    if hasattr(self, '_hub') and hasattr(self._hub, 'uuid'):
                        register_hub_subdevice(self._hub.uuid, subdev_id)

                    temp_val = data.get('temperature', {}).get('latest', '?')
                    hum_val = data.get('humidity', {}).get('latest', '?')
                    if check_and_update_logged(subdev_id, (temp_val, hum_val)):
                        log.debug(f"MS130 Event: ID={subdev_id}, Temp={temp_val}, Hum={hum_val}")
        except Exception as e:
            log.debug(f"MS130 Event-Interceptor Fehler: {e}")

        return await original_handler(self, namespace, data)

    GenericSubDevice.async_handle_subdevice_notification = patched_handler
    GenericSubDevice._ms130_handler_patched = True
    if reinstall:
        # Happens after an NVDA module reload: the handler now points to the
        # current module globals again (fresh _MS130_SENSOR_DATA).
        log.info("MS130 Event Handler nach Reload neu installiert!")
    else:
        log.info("MS130 Event Handler gepatcht!")


# Run patch and filter at import time
if MEROSS_AVAILABLE:
    _install_error_filter()
    _patch_ms130_event_handler()


def _auto_configure_custom_names(devices):
    """Automatically configures channel names for multi-channel devices (optional)"""


# ============================================================
# MerossChannel
# ============================================================

class MerossChannel:
    """Wrapper for a single channel of a multi-channel device"""

    @staticmethod
    def _resolve_outlet_name(parent_device, channel_info):
        """Ermittelt den in der Meross-App vergebenen Ausgangsnamen.

        Quellen in dieser Reihenfolge:
          1. channel_info.name (aus meross_iot; = devName der Cloud)
          2. Rohdaten der HTTP-Geräteliste (_cached_http_info.channels[i].devName),
             falls meross_iot den Namen nicht durchgereicht hat.

        Der meross_iot-Platzhalter "Main channel" und leere Werte gelten NICHT
        als echter Name. Rückgabe: der Name (str) oder None.

        Hinweis: Nicht jedes Meross-Modell liefert die Ausgangsnamen über die
        Cloud-API - manche speichern sie nur app-lokal. Ist hier None, hat die
        Cloud schlicht keinen Namen geliefert (im NVDA-Log als DEBUG sichtbar).
        """
        # 1. Aus dem geparsten ChannelInfo
        name = _clean_outlet_name(getattr(channel_info, 'name', None))
        if name:
            return name

        # 2. Rohdaten der HTTP-Geräteliste als Fallback
        dev = getattr(parent_device, '_device', None)
        http_info = getattr(dev, '_cached_http_info', None)
        raw_channels = getattr(http_info, 'channels', None)
        name = _outlet_name_from_raw_channels(raw_channels, channel_info.index)
        if name:
            return name

        log.debug(
            f"Kein Ausgangsname von der Meross-Cloud für {parent_device.name} "
            f"Kanal {channel_info.index} - verwende Nummer")
        return None

    def __init__(self, parent_device, channel_info, display_position=None):
        self._parent_device = parent_device
        self._channel_info = channel_info
        self.channel_index = channel_info.index
        self.index = channel_info.index  # alias, mirrors the offline channel
        self.uuid = f"{parent_device.uuid}_ch{channel_info.index}"

        ausgang_nr = display_position if display_position is not None else (channel_info.index + 1)
        # Kurzes Ausgangs-Label: der in der Meross-App vergebene Ausgangsname,
        # falls vorhanden - sonst "Ausgang N". Ein eigener Name ersetzt das
        # generische "Ausgang N" komplett (z.B. "Pumpe" statt "Ausgang 1").
        outlet_name = self._resolve_outlet_name(parent_device, channel_info)
        if outlet_name:
            self.outlet_label = outlet_name
        else:
            # Translators: Compact outlet label with the outlet number.
            self.outlet_label = _("Ausgang {number}").format(number=ausgang_nr)
        # Vollständiger Name inkl. Gerät: "Garten: Pumpe" (benannter Ausgang)
        # bzw. "Garten: Ausgang 1" (ohne Namen) - überall dort verwendet, wo
        # der Kontext (das Elterngerät) nicht ohnehin klar ist (Ansagen,
        # Favoriten, Toggle-Aktion).
        # Translators: Full channel name: device name plus outlet label.
        self.name = _("{device}: {outlet}").format(
            device=parent_device.name, outlet=self.outlet_label)

        self.type = parent_device.type
        self.parent_name = parent_device.name

        self.is_plug = parent_device.is_plug
        self.is_light = parent_device.is_light
        self.is_sensor = False
        self.is_temperature_sensor = False
        self.is_water_sensor = False
        self.has_power_meter = parent_device.has_power_meter
        self.is_hub = False
        self.is_channel = True
        self._is_on = False
        # Timestamp of the last push update (0.0 = none yet). See
        # _update_status() for why this is a timestamp and not a bool.
        self._push_ts = 0.0

        self._update_status()

    @property
    def _device(self):
        """The live meross_iot device object of the parent.

        Deliberately a property and NOT a copy taken in __init__:
        ``MerossAPI.update_device_status()`` replaces ``parent._device`` with a
        freshly looked-up object on every poll (and meross_iot hands out a NEW
        object after an MQTT reconnect / re-enrollment). A copy taken once at
        construction time would keep pointing at the old, orphaned object and
        the channel status would silently freeze.
        """
        return self._parent_device._device

    def mark_push_update(self, state):
        """Applies a state received via MQTT push and opens the grace window.

        Called by the API layer instead of poking the private attributes.
        """
        self._is_on = bool(state)
        self._push_ts = time.time()

    def _update_status(self):
        """Updates the status for this channel.

        A poll that overlaps an incoming push would overwrite the fresher push
        value with the stale polled one, so pushes win for
        PUSH_GRACE_SECONDS. IMPORTANT: this is a short time window, not a
        permanent latch - a previous version used a ``_push_updated`` boolean
        that was never reset anywhere, which turned this method into a
        permanent no-op for the channel. From then on the channel state
        depended solely on MQTT pushes, so a single missed push (reconnect,
        switching via the Meross app or at the device itself) left the channel
        displaying the wrong state for the rest of the NVDA session, with no
        way to recover. The time window heals on its own.
        """
        try:
            if (time.time() - self._push_ts) < PUSH_GRACE_SECONDS:
                return
            device = self._device
            if hasattr(device, 'is_on'):
                self._is_on = device.is_on(channel=self.channel_index)
        except Exception as e:
            log.debug(f"Channel-Status-Update fehlgeschlagen für {self.name}: {e}")

    @property
    def is_on(self):
        return self._is_on

    @property
    def unique_id(self):
        """Unique ID - already unique for channels (parent_uuid_chX)."""
        return self.uuid

    def get_power(self):
        if not self.has_power_meter:
            return None
        try:
            if hasattr(self._device, 'get_power_consumption'):
                metrics = self._device.get_power_consumption(channel=self.channel_index)
                if hasattr(metrics, 'power'):
                    return round(metrics.power, 1)
            return None
        except Exception as e:
            log.debug(f"Konnte Stromverbrauch nicht abrufen für {self.name}: {e}")
            return None


# ============================================================
# MerossDevice
# ============================================================

class MerossDevice:
    """Wrapper for a Meross device with a simple interface"""

    # Class-level defaults. MerossOfflineDevice deliberately does NOT call
    # super().__init__() (it wraps an HttpDeviceInfo, not a live device), but it
    # DOES inherit methods that read these attributes - e.g. is_in_rgb_mode()
    # reads self._light_mode outside of any try block, which used to raise
    # AttributeError for every offline MSL lamp. Defining them here means every
    # subclass has them, whatever its __init__ does.
    _light_mode = None
    _cached_rgb = None
    _cached_temperature = None
    _cached_metrics = None
    _subdevice_id = None
    is_channel = False       # counterpart to MerossChannel.is_channel = True
    is_multi_channel = False
    is_offline = False

    def __init__(self, device):
        self._device = device
        self.uuid = device.uuid
        self.name = device.name
        self.type = device.type
        self._is_on = False
        self._last_event_data = {}
        self._channels = []
        self._cached_metrics = None
        self._light_mode = None
        self._cached_rgb = None
        self._cached_temperature = None

        # Subdevice ID for hub-based devices (MS130, MS100)
        self._subdevice_id = None
        if hasattr(device, 'subdevice_id') and device.subdevice_id:
            self._subdevice_id = device.subdevice_id
        elif hasattr(device, '_subdevice_id') and device._subdevice_id:
            self._subdevice_id = device._subdevice_id

        # Detect the device type
        type_lower = self.type.lower()

        self.is_plug = (
            is_plug_type(type_lower)
            or ("mss" in type_lower and "plug" in self.name.lower())
        )
        self.is_light = "msl" in type_lower or "light" in type_lower or "lamp" in type_lower
        self.is_diffuser = "mod150" in type_lower or hasattr(device, 'async_set_spray_mode')
        self.is_temperature_sensor = is_temperature_sensor_type(type_lower)
        self.is_water_sensor = is_water_sensor_type(type_lower)
        self.is_sensor = self.is_temperature_sensor or self.is_water_sensor
        # MOP320 meters per outlet; the actual read is guarded by
        # hasattr(get_power_consumption) in case the cloud does not report the
        # capability for a particular variant.
        self.has_power_meter = has_power_meter_type(type_lower)
        self.is_hub = "msh" in type_lower or hasattr(device, 'get_subdevices')

        # Multi-channel devices
        self.is_multi_channel = False
        self._channels = []

        self._setup_channels(device)

        if "ms130" in type_lower:
            self._setup_ms130_handler()

        try:
            self._update_status()
        except Exception as e:
            log.debug(f"Ignorierter Fehler in __init__: {e}")

    def _setup_channels(self, device):
        """Ermittelt die Ausgänge eines Mehrfachgeräts - wie im Offline-Pfad.

        Früher stand hier::

            non_master = [ch for ch in device.channels
                          if not ch.is_master_channel]
            if len(non_master) > 1: ...

        ``meross_iot`` setzt ``is_master_channel = (index == 0)`` IMMER
        (``lib/meross_iot/controller/device.py``), unabhängig davon, ob Kanal 0
        wirklich ein Master ist. Meldet die Cloud für ein MSS620 nur
        ``[Pumpe, Licht]`` statt ``[Master, Pumpe, Licht]``, bleibt nach dem
        Filter ein Kanal übrig, ``is_multi_channel`` wird False - und BEIDE
        Ausgänge verschwinden aus dem Menü.

        Jetzt läuft die Ermittlung über dieselbe Hilfsfunktion wie offline
        (``_outlets_from_raw_channels``), die bei unbrauchbarer Kanalliste auf
        ``MULTI_OUTLET_FALLBACK`` zurückfällt. Der eigentliche Gewinn ist
        nicht der Einzelfall, sondern dass Online- und Offline-Pfad
        per Konstruktion dieselben Kanalindizes liefern: gleiche Indizes =
        gleiche Kanal-UUIDs = Favoriten überleben einen Offline-Zeitraum.
        """
        channels = getattr(device, 'channels', None) or []
        raw_channels = getattr(
            getattr(device, '_cached_http_info', None), 'channels', None)
        # Rohdaten bevorzugen; fehlen sie, die geparste Liste als Ersatz -
        # sie stammt aus derselben Quelle und hat dieselbe Länge.
        indices = _outlets_from_raw_channels(raw_channels or channels, self.type)
        if not indices:
            return

        by_index = {}
        for ch_info in channels:
            try:
                by_index[ch_info.index] = ch_info
            except AttributeError:
                continue

        for position, index in enumerate(indices, start=1):
            ch_info = by_index.get(index)
            if ch_info is None:
                # Die Cloud kennt den Index nicht (Fallback-Fall). Ohne
                # ChannelInfo lässt sich kein MerossChannel bauen - der
                # Offline-Pfad übernimmt diesen Fall.
                log.debug(
                    f"Meross {self.name}: Kanal {index} nicht in der "
                    f"Kanalliste - übersprungen")
                continue
            self._channels.append(MerossChannel(self, ch_info, position))

        if self._channels:
            self.is_multi_channel = True
            log.info(
                f"Multi-Channel Gerät erkannt: {self.name} mit "
                f"{len(self._channels)} Kanälen "
                f"(Indizes {[c.channel_index for c in self._channels]})")

    def _setup_ms130_handler(self):
        """Registers the event handler for MS130 HUB_SENSOR_ALL events"""
        try:
            if hasattr(self._device, '_event_handlers'):
                original_handler = getattr(self._device, 'async_handle_subdevice_notification', None)

                async def custom_handler(namespace, payload):
                    if 'temperature' in payload or 'humidity' in payload:
                        self._last_event_data = payload
                        log.debug(
                            f"MS130 Event empfangen für {self.name}: "
                            f"Temp={payload.get('temperature', {}).get('latest', '?') / 100.0 if 'temperature' in payload else '?'}°C"
                        )
                    if original_handler:
                        await original_handler(namespace, payload)

                self._device.async_handle_subdevice_notification = custom_handler
        except Exception as e:
            log.debug(f"MS130 Handler-Setup fehlgeschlagen: {e}")

    def update_from_hub_event(self, event_data):
        """Manual update method for hub events (fallback)"""
        if 'temperature' in event_data or 'humidity' in event_data:
            self._last_event_data = event_data
            log.debug(f"MS130 manuelle Event-Update für {self.name}")

    def get_subdevices(self):
        """Returns the subdevices (hubs only)"""
        if not self.is_hub:
            return []
        try:
            if hasattr(self._device, 'get_subdevices'):
                if callable(self._device.get_subdevices):
                    return self._device.get_subdevices()
                return self._device.get_subdevices
            return []
        except Exception as e:
            log.debug(f"Konnte Subdevices nicht abrufen: {e}")
            return []

    def get_channels(self):
        return self._channels if self.is_multi_channel else []

    def _update_channels_status(self):
        if not self.is_multi_channel or not self._channels:
            return
        try:
            for ch in self._channels:
                ch._update_status()
        except Exception as e:
            log.debug(f"Channel-Status-Update fehlgeschlagen für {self.name}: {e}")

    def _update_status(self):
        """Updates the status (without power - that is loaded asynchronously)"""
        try:
            if self.is_light:
                self._light_mode = None
                self._cached_rgb = None
                self._cached_temperature = None

            if hasattr(self._device, 'get_current_spray_mode'):
                from meross_iot.model.enums import DiffuserSprayMode
                current_mode = self._device.get_current_spray_mode()
                self._is_on = (current_mode != DiffuserSprayMode.OFF)
                log.debug(f"Diffuser {self.name} Status: Mode={current_mode}, is_on={self._is_on}")
            elif hasattr(self._device, 'is_on'):
                if callable(self._device.is_on):
                    self._is_on = self._device.is_on()
                else:
                    self._is_on = self._device.is_on
            elif hasattr(self._device, 'get_status'):
                self._is_on = self._device.get_status()

            self._update_channels_status()

        except Exception as e:
            log.debug(f"Status-Update fehlgeschlagen für {self.name}: {e}")

    @property
    def is_on(self):
        return self._is_on

    @property
    def unique_id(self):
        """Unique ID for this device (important for favorites management).

        For hub subdevices (MS100/MS130 sensors) the subdevice ID is
        appended to the hub UUID, since all sensors of one hub would
        otherwise share the same UUID. For normal devices identical to uuid.
        """
        subdev_id = getattr(self, '_subdevice_id', None)
        if subdev_id:
            return f"{self.uuid}_{subdev_id}"
        return self.uuid

    def get_diffuser_spray_mode(self):
        """Returns the current spray mode as a string (diffusers only)"""
        if not self.is_diffuser:
            return None
        try:
            if hasattr(self._device, 'get_current_spray_mode'):
                from meross_iot.model.enums import DiffuserSprayMode
                mode = self._device.get_current_spray_mode()
                if mode == DiffuserSprayMode.OFF:
                    # Translators: Diffuser spray mode (off).
                    return _("Aus")
                elif mode == DiffuserSprayMode.LIGHT:
                    # Translators: Diffuser spray mode (light).
                    return _("Schwaches Sprühen")
                elif mode == DiffuserSprayMode.STRONG:
                    # Translators: Diffuser spray mode (strong).
                    return _("Starkes Sprühen")
                else:
                    # Translators: Unknown diffuser spray mode.
                    return _("Unbekannt ({mode})").format(mode=mode)
        except Exception as e:
            log.debug(f"Fehler beim Auslesen des Spray-Mode: {e}")
        return "Unbekannt"

    # ---- Power metering ----

    def get_power(self):
        """Power in watts (MSS310/MSS315 only)"""
        if not self.has_power_meter:
            return None
        if self._cached_metrics is not None and hasattr(self._cached_metrics, 'power'):
            return round(self._cached_metrics.power, 1)
        return None

    def get_voltage(self):
        """Voltage in volts (MSS310/MSS315 only)"""
        if not self.has_power_meter:
            return None
        if self._cached_metrics is not None and hasattr(self._cached_metrics, 'voltage'):
            return round(self._cached_metrics.voltage, 1)
        return None

    def get_current(self):
        """Amperage in amps (MSS310/MSS315 only)"""
        if not self.has_power_meter:
            return None
        if self._cached_metrics is not None and hasattr(self._cached_metrics, 'current'):
            return round(self._cached_metrics.current, 3)
        return None

    # ---- Temperature sensors ----

    def get_temperature(self):
        """Returns the temperature (temperature sensors only)"""
        if not self.is_temperature_sensor:
            return None
        try:
            if self.type.lower() == 'ms130':
                return self._get_ms130_value('temperature', scale=100.0)

            # MS100: default method
            if hasattr(self._device, 'get_subdevices'):
                try:
                    subdevices = list(self._device.get_subdevices())
                    if subdevices:
                        subdev = subdevices[0]
                        if hasattr(subdev, 'last_sampled_temperature'):
                            temp = subdev.last_sampled_temperature
                            if temp is not None:
                                return round(temp, 1)
                        elif hasattr(subdev, 'lastData') and isinstance(subdev.lastData, dict):
                            if 'temperature' in subdev.lastData:
                                temp = subdev.lastData['temperature']
                                if temp is not None:
                                    return round(temp, 1)
                except Exception as e:
                    log.error(f"MS100 {self.name}: Fehler beim Lesen der Subdevices: {e}")

            if hasattr(self._device, 'last_sampled_temperature'):
                temp = self._device.last_sampled_temperature
                if temp is not None:
                    return round(temp, 1)

            if hasattr(self._device, 'last_sample'):
                sample = self._device.last_sample
                if isinstance(sample, dict) and 'temperature' in sample:
                    temp = sample['temperature']
                    if temp is not None:
                        return round(temp, 1)

            return None
        except Exception as e:
            log.error(f"Fehler beim Abrufen der Temperatur für {self.name}: {e}", exc_info=True)
            return None

    def get_humidity(self):
        """Returns the humidity (temperature sensors only)"""
        if not self.is_temperature_sensor:
            return None
        try:
            if self.type.lower() == 'ms130':
                return self._get_ms130_value('humidity', scale=10.0)

            if hasattr(self._device, 'get_subdevices'):
                try:
                    subdevices = list(self._device.get_subdevices())
                    if subdevices:
                        subdev = subdevices[0]
                        if hasattr(subdev, 'last_sampled_humidity'):
                            humidity = subdev.last_sampled_humidity
                            if humidity is not None:
                                return round(humidity, 1)
                        elif hasattr(subdev, 'lastData') and isinstance(subdev.lastData, dict):
                            if 'humidity' in subdev.lastData:
                                humidity = subdev.lastData['humidity']
                                if humidity is not None:
                                    return round(humidity, 1)
                except Exception as e:
                    log.error(f"MS100 {self.name}: Fehler beim Lesen der Subdevice-Feuchtigkeit: {e}")

            if hasattr(self._device, 'last_sampled_humidity'):
                humidity = self._device.last_sampled_humidity
                if humidity is not None:
                    return round(humidity, 1)

            if hasattr(self._device, 'last_sample'):
                sample = self._device.last_sample
                if isinstance(sample, dict) and 'humidity' in sample:
                    humidity = sample['humidity']
                    if humidity is not None:
                        return round(humidity, 1)

            return None
        except Exception as e:
            log.error(f"Fehler beim Abrufen der Luftfeuchtigkeit für {self.name}: {e}", exc_info=True)
            return None

    def _get_ms130_value(self, field, scale):
        """Helper: reads an MS130 sensor value from the thread-safe database.

        Args:
            field: 'temperature' or 'humidity'
            scale: division factor (100.0 for temperature, 10.0 for humidity)
        """
        subdev_id = self._resolve_subdevice_id()
        if subdev_id:
            event_data = get_sensor_data(subdev_id)
            if event_data and field in event_data:
                raw = event_data[field]
                if isinstance(raw, dict) and 'latest' in raw:
                    return round(raw['latest'] / scale, 1)
        else:
            log.debug(
                f"MS130 {self.name}: Subdevice-ID '{subdev_id}' nicht in Event-Daten. "
                f"Verfügbar: {get_sensor_data_keys()}"
            )
        return None

    def _resolve_subdevice_id(self):
        """Determines the subdevice ID for hub-based devices."""
        if self._subdevice_id:
            return self._subdevice_id
        if hasattr(self._device, 'subdevice_id') and self._device.subdevice_id:
            self._subdevice_id = self._device.subdevice_id
            return self._subdevice_id
        if hasattr(self._device, '_subdevice_id') and self._device._subdevice_id:
            self._subdevice_id = self._device._subdevice_id
            return self._subdevice_id
        return None

    def is_water_detected(self):
        """Checks whether water was detected (water sensors only)"""
        if not self.is_water_sensor:
            return None
        try:
            if hasattr(self._device, 'is_triggered'):
                if callable(self._device.is_triggered):
                    return self._device.is_triggered()
                return self._device.is_triggered
            return None
        except Exception as e:
            log.debug(f"Konnte Wasserstatus nicht abrufen: {e}")
            return None

    # ==================== Lamp functions ====================

    def supports_rgb(self, channel=0):
        if not self.is_light:
            return False
        try:
            type_lower = self.type.lower()
            if 'msl610' in type_lower:
                return False
            if hasattr(self._device, 'get_supports_rgb'):
                result = self._device.get_supports_rgb(channel=channel)
                if result:
                    return True
            if 'msl450' in type_lower or 'msl320' in type_lower:
                return True
            return False
        except Exception as e:
            log.debug(f"Fehler beim Prüfen der RGB-Unterstützung für {self.name}: {e}")
            return False

    def supports_luminance(self, channel=0):
        if not self.is_light:
            return False
        try:
            if hasattr(self._device, 'get_supports_luminance'):
                result = self._device.get_supports_luminance(channel=channel)
                if result:
                    return True
            type_lower = self.type.lower()
            if 'msl' in type_lower:
                return True
            return False
        except Exception as e:
            log.debug(f"Fehler beim Prüfen der Helligkeits-Unterstützung für {self.name}: {e}")
            return False

    def supports_temperature(self, channel=0):
        if not self.is_light:
            return False
        try:
            if hasattr(self._device, 'get_supports_temperature'):
                result = self._device.get_supports_temperature(channel=channel)
                if result:
                    return True
            type_lower = self.type.lower()
            if 'msl450' in type_lower or 'msl610' in type_lower:
                return True
            if 'msl320' in type_lower:
                return False
            return False
        except Exception as e:
            log.debug(f"Fehler beim Prüfen der Farbtemperatur-Unterstützung für {self.name}: {e}")
            return False

    def get_rgb_color(self, channel=0):
        if not self.is_light:
            return None
        try:
            if self._light_mode == 'rgb' and self._cached_rgb is not None:
                return self._cached_rgb
            if hasattr(self._device, 'get_rgb_color'):
                rgb = self._device.get_rgb_color(channel=channel)
                if rgb:
                    return rgb
            return None
        except Exception as e:
            log.debug(f"Fehler beim Abrufen der RGB-Farbe für {self.name}: {e}")
            return None

    def get_luminance(self, channel=0):
        if not self.is_light:
            return None
        try:
            if hasattr(self._device, 'get_luminance'):
                luminance = self._device.get_luminance(channel=channel)
                if luminance is not None:
                    return luminance
            return None
        except Exception as e:
            log.debug(f"Fehler beim Abrufen der Helligkeit für {self.name}: {e}")
            return None

    def get_color_temperature(self, channel=0):
        if not self.is_light:
            return None
        try:
            if hasattr(self._device, 'get_color_temperature'):
                temp = self._device.get_color_temperature(channel=channel)
                if temp is not None:
                    return temp
            return None
        except Exception as e:
            log.debug(f"Fehler beim Abrufen der Farbtemperatur für {self.name}: {e}")
            return None

    def is_in_rgb_mode(self, channel=0):
        """Checks whether the lamp is currently in RGB color mode.

        Check order: device type -> global cache -> local cache ->
        capacity value -> fallback heuristic.
        """
        if not self.is_light:
            return None

        type_lower = self.type.lower()

        if 'msl610' in type_lower:
            return False

        # Global cache (thread-safe)
        global_mode = get_light_mode(self.uuid)
        if global_mode == 'rgb':
            return True
        elif global_mode == 'white':
            return False

        # Local cache
        if self._light_mode == 'rgb':
            return True
        elif self._light_mode == 'white':
            return False

        # capacity from the library
        try:
            if hasattr(self._device, '_channel_light_status'):
                channel_info = self._device._channel_light_status.get(channel)
                if channel_info is not None and hasattr(channel_info, '_capacity'):
                    capacity = channel_info._capacity
                    if capacity is not None:
                        is_rgb = (capacity & 1) == 1
                        is_temp = (capacity & 2) == 2
                        if is_temp and not is_rgb:
                            return False
                        if is_rgb:
                            return True
        except Exception as e:
            log.debug(f"Fehler beim Lesen des capacity für {self.name}: {e}")

        # Heuristic
        try:
            temperature = None
            try:
                if hasattr(self._device, '_channel_light_status'):
                    ci = self._device._channel_light_status.get(channel)
                    if ci is not None:
                        temperature = ci._temperature
            except Exception as e:
                log.debug(f"Ignorierter Fehler in is_in_rgb_mode: {e}")

            if temperature is None:
                temperature = self.get_color_temperature(channel)

            rgb = self.get_rgb_color(channel)

            if temperature is not None and temperature > 0:
                return False

            if rgb is None or (rgb[0] == 0 and rgb[1] == 0 and rgb[2] == 0):
                return False

            r, g, b = rgb
            color_difference = max(r, g, b) - min(r, g, b)
            return color_difference > 50

        except Exception as e:
            log.debug(f"Fehler beim Prüfen des Lichtmodus für {self.name}: {e}")
            return None

    def set_light_mode(self, mode, rgb=None, temperature=None):
        """Sets the internal light mode and caches the values."""
        if mode in ('rgb', 'white'):
            self._light_mode = mode
            set_light_mode_cache(self.uuid, mode)
            log.debug(f"{self.name}: Lichtmodus auf '{mode}' gesetzt (lokal + global)")

            if mode == 'rgb' and rgb is not None:
                self._cached_rgb = rgb
                self._cached_temperature = None
            elif mode == 'white' and temperature is not None:
                self._cached_temperature = temperature
                self._cached_rgb = None


# ============================================================
# MerossOfflineChannel
# ============================================================

class MerossOfflineChannel:
    """One outlet of an offline multi-outlet device.

    A real class rather than a ``type('OfflineChannel', (), {...})()`` throwaway,
    because that construct turned ``'get_power': lambda: None`` into an unbound
    method: ``ch.get_power()`` passed ``self`` into a zero-argument lambda and
    raised TypeError. It also only exposed ``.index`` while the online channel
    exposes ``.channel_index``, so callers looking up a channel by
    ``channel_index`` (``__init__.py``, ``meross_api.py``) hit an
    AttributeError. This class mirrors the MerossChannel interface exactly.
    """

    is_on = False
    is_offline = True
    is_channel = True
    is_sensor = False
    is_temperature_sensor = False
    is_water_sensor = False
    is_hub = False

    def __init__(self, parent_device, channel_index, display_position, outlet_label):
        self._parent_device = parent_device
        # Both spellings: online channels expose channel_index, and several
        # call sites look devices up by it.
        self.channel_index = channel_index
        self.index = channel_index
        self.display_position = display_position
        self.outlet_label = outlet_label
        # Same naming scheme as online: "Garten: Pumpe" / "Garten: Ausgang 1".
        # Translators: Full channel name: device name plus outlet label.
        self.name = _("{device}: {outlet}").format(
            device=parent_device.name, outlet=outlet_label)
        # UUID uses the REAL channel index - identical to the online path
        # (f"{uuid}_ch{channel_info.index}") so favorites created while the
        # device was online still match once it goes offline.
        self.uuid = f"{parent_device.uuid}_ch{channel_index}"
        self.type = parent_device.type
        self.parent_name = parent_device.name
        self.is_plug = parent_device.is_plug
        self.is_light = parent_device.is_light
        self.has_power_meter = parent_device.has_power_meter

    @property
    def unique_id(self):
        return self.uuid

    def get_power(self):
        return None

    def get_voltage(self):
        return None

    def get_current(self):
        return None


# ============================================================
# MerossOfflineDevice
# ============================================================

class MerossOfflineDevice(MerossDevice):
    """Wrapper for offline Meross devices (from HttpDeviceInfo)"""

    def __init__(self, http_device_info):
        class PseudoDevice:
            def __init__(self, info):
                self.uuid = info.uuid
                self.name = info.dev_name
                self.type = info.device_type
                self.online_status = info.online_status

        self._http_info = http_device_info
        self._device = PseudoDevice(http_device_info)
        self.uuid = http_device_info.uuid
        self.name = http_device_info.dev_name
        self.type = http_device_info.device_type
        self._is_on = False
        self._last_event_data = {}
        self.is_offline = True

        type_lower = self.type.lower()
        # Same central model tables as the online path - previously these lists
        # were duplicated here and had drifted apart (the MOP320 was a metering
        # plug online but neither a plug nor a meter offline).
        self.is_plug = (
            is_plug_type(type_lower)
            or ("mss" in type_lower and "plug" in self.name.lower())
        )
        self.is_light = "msl" in type_lower or "light" in type_lower or "lamp" in type_lower
        self.is_diffuser = "mod150" in type_lower
        self.is_temperature_sensor = is_temperature_sensor_type(type_lower)
        self.is_water_sensor = is_water_sensor_type(type_lower)
        self.is_sensor = self.is_temperature_sensor or self.is_water_sensor
        self.has_power_meter = has_power_meter_type(type_lower)
        self.is_hub = "msh" in type_lower

        self.is_multi_channel = False
        self._channels = []
        self._build_offline_channels(http_device_info)

    def _build_offline_channels(self, http_device_info):
        """Builds the outlet list of an offline multi-outlet device.

        The channel indices come from the raw channel data of the HTTP device
        list - the SAME source the online path uses - instead of a hardcoded
        outlet count. The previous version assumed ``num_channels = 2`` for
        every mss620/mss425 and derived the offset as
        ``len(raw_channels) - num_channels``. That is correct for the MSS620
        (2 outlets) but wrong for the whole MSS425 series: an MSS425F has
        4 sockets + 1 USB group, so with 6 raw channels the offset became 4 and
        the device showed 2 outlets with the indices 4 and 5, labelled
        "Ausgang 1" and "Ausgang 2". Outlets disappeared, the remaining ones
        were mislabelled (what was announced as outlet 1 was physically
        outlet 4), and favorites broke because the channel UUIDs no longer
        matched the online ones.
        """
        # Raw channel data of the HTTP device list. Contains devName per outlet
        # EVEN WHILE THE DEVICE IS OFFLINE, which is what lets the outlet names
        # from the Meross app show up offline as well.
        raw_channels = getattr(http_device_info, 'channels', None) or []
        try:
            dump = [(i, c.get('devName') if isinstance(c, dict) else None)
                    for i, c in enumerate(raw_channels)]
            log.debug(f"Meross {self.name}: Rohkanäle (Index, devName) = {dump}")
        except Exception as e:
            log.debug(f"Rohkanal-Dump fehlgeschlagen: {e}")

        outlet_indices = _outlets_from_raw_channels(raw_channels, self.type)
        if not outlet_indices:
            return

        self.is_multi_channel = True
        named = 0
        for position, raw_idx in enumerate(outlet_indices, start=1):
            # Outlet name exactly as online: the name given in the Meross app
            # if the cloud provides one, otherwise "Ausgang N".
            outlet_name = _outlet_name_from_raw_channels(raw_channels, raw_idx)
            if outlet_name:
                outlet_label = outlet_name
                named += 1
            else:
                # Translators: Compact outlet label with the outlet number.
                outlet_label = _("Ausgang {number}").format(number=position)
            self._channels.append(
                MerossOfflineChannel(self, raw_idx, position, outlet_label))

        log.info(
            f"Offline Multi-Channel Gerät: {self.name} ({self.type}) mit "
            f"{len(self._channels)} Ausgängen, {named} davon mit eigenem Namen")

    def _raise_offline_error(self):
        # Translators: Error message: action on an offline device.
        raise RuntimeError(_("Gerät '{name}' ist offline oder nicht erreichbar").format(name=self.name))

    @property
    def is_on(self):
        return False

    def get_power(self):
        return None

    def get_diffuser_spray_mode(self):
        if not self.is_diffuser:
            return None
        # Translators: Spray mode display when the diffuser is offline.
        return _("Offline")

    def get_temperature(self):
        return None

    def get_humidity(self):
        return None

    def is_water_detected(self):
        return None

    def is_in_rgb_mode(self, channel=0):
        """Unknown while offline - consistent with the other getters here.

        The inherited implementation would fall through to its heuristic and
        confidently answer "False" for a lamp it cannot even reach.
        """
        return None

    def get_subdevices(self):
        return []

    def get_channels(self):
        return self._channels
