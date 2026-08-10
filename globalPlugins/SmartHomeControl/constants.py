# -*- coding: utf-8 -*-
"""
Smart Home Control - Shared constants
Central definition of configuration and recurring mapping tables.

"""

# i18n: the tables below contain display names and are built at module import
# time – therefore THIS module needs its own initTranslation() before the dicts
# are created. The fallback keeps the module importable outside of NVDA (e.g.
# in tests).
import addonHandler
try:
    addonHandler.initTranslation()
except Exception:
    pass
if "_" not in globals():  # fallback outside of NVDA
    def _(s):
        return s

# ============================================================
# Netatmo heating mode labels
# Used in: __init__.py, netatmo_api.py, device_dialog.py
# ============================================================
NETATMO_MODE_NAMES = {
    # Translators: Netatmo heating mode (follows the weekly schedule).
    'schedule': _('Zeitplan'),
    # Translators: Netatmo heating mode (manually set temperature).
    'manual': _('Manuell'),
    # Translators: Netatmo heating mode (away, reduced temperature).
    'away': _('Abwesend'),
    # Translators: Netatmo heating mode (frost guard).
    'hg': _('Frostschutz'),
    # Translators: Netatmo heating mode (maximum heating power).
    'max': _('Maximum'),
    # Translators: Netatmo heating mode (home).
    'home': _('Zuhause'),
}

# ============================================================
# Diffuser spray mode labels
# Used in: device_dialog.py (_execute_action)
# ============================================================
DIFFUSER_MODE_NAMES = {
    # Translators: Spray mode of a Meross diffuser (light).
    'diffuser_light': _('Schwaches Sprühen'),
    # Translators: Spray mode of a Meross diffuser (strong).
    'diffuser_strong': _('Starkes Sprühen'),
    # Translators: Spray mode of a Meross diffuser (off).
    'diffuser_off': _('Aus'),
}

# ============================================================
# VeSync mode labels
# Used in: device_dialog.py (actions)
# ============================================================
VESYNC_PURIFIER_MODE_NAMES = {
    # Translators: Operating mode of a VeSync air purifier.
    'auto': _('Auto'),
    # Translators: Operating mode of a VeSync air purifier.
    'manual': _('Manuell'),
    # Translators: Operating mode of a VeSync air purifier.
    'sleep': _('Schlafmodus'),
    # Translators: Operating mode of a VeSync air purifier.
    'turbo': _('Turbo'),
    # Translators: Operating mode of a VeSync air purifier (for pets).
    'pet': _('Haustier-Modus'),
}

VESYNC_FAN_MODE_NAMES = {
    # Translators: Operating mode of a VeSync fan.
    'normal': _('Normal'),
    # Translators: Operating mode of a VeSync fan.
    'turbo': _('Turbo'),
    # Translators: Operating mode of a VeSync fan.
    'auto': _('Auto'),
    # Translators: Operating mode of a VeSync fan (advanced sleep mode).
    'advancedSleep': _('Schlafmodus'),
}

# Air quality levels (1=excellent ... 4=poor)
VESYNC_AIR_QUALITY_NAMES = {
    # Translators: Air quality level 1 (best) of a VeSync air purifier.
    1: _('ausgezeichnet'),
    # Translators: Air quality level 2 of a VeSync air purifier.
    2: _('gut'),
    # Translators: Air quality level 3 of a VeSync air purifier.
    3: _('mäßig'),
    # Translators: Air quality level 4 (worst) of a VeSync air purifier.
    4: _('schlecht'),
}

# Night light modes (Core 200S)
VESYNC_NIGHTLIGHT_MODE_NAMES = {
    # Translators: Night light state (on).
    'on': _('Ein'),
    # Translators: Night light state (off).
    'off': _('Aus'),
    # Translators: Night light state (dimmed).
    'dim': _('Gedimmt'),
}

# Auto profile (Core 300S/400S/500S/600S)
VESYNC_AUTO_PREFERENCE_NAMES = {
    # Translators: Auto mode profile of a VeSync air purifier.
    'default': _('Standard'),
    # Translators: Auto mode profile of a VeSync air purifier.
    'efficient': _('Effizient'),
    # Translators: Auto mode profile of a VeSync air purifier.
    'quiet': _('Leise'),
}

# Default warning threshold (%) for the filter life of a VeSync air purifier.
# The actual value is configurable per user in the VeSync tab (config
# ``vesyncFilterThreshold``); this constant is only the default/fallback. When
# the remaining life drops to or below the threshold, a warning banner appears
# at the top of the dialog and a one-time warning is announced when crossing
# it.
VESYNC_FILTER_WARN_THRESHOLD = 15

# Fan level labels for Levoit Core 200S/300S (only 3 levels). The labels follow
# the Levoit app: low/medium/high. For models with 4 or 5 levels (Core
# 400S/500S/600S) only the numeric level is shown - see
# vesync_purifier_level_label() in the dialog.
VESYNC_PURIFIER_LEVEL_LABELS_3 = {
    # Translators: Fan level label (lowest level).
    1: _('Niedrig'),
    # Translators: Fan level label (middle level).
    2: _('Mittel'),
    # Translators: Fan level label (highest level).
    3: _('Hoch'),
}

# ============================================================
# NVDA config section (confspec) for this add-on
# Used in: __init__.py
# ============================================================
CONFSPEC = {
    "email": "string(default='')",
    "password": "string(default='')",
    "autoLogin": "boolean(default=true)",
    "announceExternalChanges": "boolean(default=true)",
    # Welcher Tab beim Öffnen des Geräte-Menüs aktiv ist: 'devices' oder
    # 'favorites'.
    "startTab": "string(default='devices')",
    "useMeross": "boolean(default=false)",
    "useNetatmo": "boolean(default=false)",
    "netatmoClientId": "string(default='')",
    "netatmoClientSecret": "string(default='')",
    "netatmoAccessToken": "string(default='')",
    "netatmoRefreshToken": "string(default='')",
    "netatmoTokenExpiry": "float(default=0)",
    # Port of the local OAuth2 callback server. Must match the redirect URI
    # registered at dev.netatmo.com (http://localhost:<port>/callback).
    "netatmoRedirectPort": "integer(default=8474)",
    "useVesync": "boolean(default=false)",
    "vesyncEmail": "string(default='')",
    "vesyncPassword": "string(default='')",
    "vesyncCountryCode": "string(default='DE')",
    "vesyncToken": "string(default='')",
    "vesyncAccountId": "string(default='')",
    "vesyncRegion": "string(default='')",
    "vesyncFilterThreshold": "integer(default=15)",
    # Cozytouch / Atlantic (e.g. Austria Email Revolution Evo 3)
    "useCozytouch": "boolean(default=false)",
    "cozytouchEmail": "string(default='')",
    "cozytouchPassword": "string(default='')",
    "cozytouchToken": "string(default='')",
    "cozytouchCapacityLiters": "integer(default=0)",
    "notifyCozytouchMode": "boolean(default=true)",
    "notifyCozytouchTemp": "boolean(default=true)",
    "notifyCozytouchBoost": "boolean(default=true)",
    "notifyCozytouchPower": "boolean(default=true)",
    "notifyCozytouchAway": "boolean(default=true)",
    # Fine-grained notification settings per platform / event. Maintained by
    # the "Notifications" tab in the settings.
    "notifyMerossToggle": "boolean(default=true)",
    "notifyNetatmoMode": "boolean(default=true)",
    "notifyNetatmoSetpoint": "boolean(default=true)",
    "notifyNetatmoBoiler": "boolean(default=true)",
    "notifyNetatmoOpenWindow": "boolean(default=true)",
    "notifyNetatmoAnticipation": "boolean(default=false)",
    "notifyVesyncToggle": "boolean(default=true)",
    "notifyVesyncMode": "boolean(default=true)",
    "notifyVesyncFanSpeed": "boolean(default=true)",
    "notifyVesyncAirQuality": "boolean(default=true)",
    "notifyVesyncFilter": "boolean(default=true)",
}

# ============================================================
# Background refresh configuration
# ============================================================
BACKGROUND_REFRESH_INTERVAL = 30   # seconds between automatic updates (backoff base)
CACHE_VALID_DURATION = 45          # seconds for which the cache counts as "fresh"
BOILER_COOLDOWN = 300              # 5 minutes cooldown for pure boiler changes

# ============================================================
# Unified polling scheduler
# ============================================================
# A single background thread polls all platforms based on a per-platform
# "next_due" time. This makes all platforms behave predictably the same -
# instead of the previous scattered counters and an extra VeSync-only thread.
#
# Two profiles:
# - 'fg' (foreground): device dialog is open -> responsive, short intervals.
# - 'bg' (background): dialog is closed -> gentle, longer intervals.
#
# The VeSync cloud has a daily limit (3200 + 1500*device count requests) and
# has no push mechanism. That is why VeSync has a higher minimum spacing in
# the background (60 s instead of 30 s). With the dialog open the user is
# present; 15 s is uncritical there.
# 1 s base tick: with larger values (e.g. 5 s) the synchronous HTTP handling
# of one tick (many Meross devices etc.) shifts the following ticks off the
# target grid, turning 15 s into effectively 18-20 s. With 1 s the next due
# poll hits the interval within <=1 s. The loop load is minimal because
# non-due ticks only check four next_due values.
#
# Der Scheduler schläft inzwischen bis zur naechsten faelligen Abfrage statt
# in festen Takten aufzuwachen (siehe _scheduler_body). SCHEDULER_TICK ist
# damit nur noch die Untergrenze der Aufloesung - die Genauigkeit haengt
# nicht mehr daran, und der Thread wacht statt 86400-mal am Tag nur noch so
# oft auf, wie tatsaechlich gepollt wird. SCHEDULER_MAX_SLEEP deckelt die
# Schlafdauer als Sicherheitsnetz, falls next_due einmal weit in der Zukunft
# liegt.
SCHEDULER_TICK = 1  # base tick of the scheduler in seconds (resolution of the next_due check)
SCHEDULER_MAX_SLEEP = 15  # Obergrenze der Schlafdauer (s), reines Sicherheitsnetz
PLATFORM_INTERVALS = {
    # Meross: deliberately gentler than the others because the Meross cloud
    # caps messages at 200/hour PER DEVICE. Confirmed by Meross support in
    # writing: the cap is a safety measure against server flooding; persistent
    # excess first triggers a "cloud termination notice", and only if the same
    # device still exceeds it three days later is THAT DEVICE blocked for 24
    # hours (other devices and the account are unaffected). The figure is not
    # published anywhere by Meross, so treat it as a support statement rather
    # than documented API behaviour. Meross delivers on/off changes in real time
    # via MQTT push anyway, so the periodic poll is only a safety
    # resynchronization and may be slow. See also MEROSS_* below and the
    # throttle in meross_api.py.
    'meross':    {'fg': 30, 'bg': 45},
    'netatmo':   {'fg': 15, 'bg': 30},
    'vesync':    {'fg': 15, 'bg': 60},
    'cozytouch': {'fg': 15, 'bg': 30},
}

# ============================================================
# Meross cloud rate limit protection
# ============================================================
# The Meross cloud allows max. 200 messages/hour per device. Each status
# poll (async_update) is 1 message; power metering plugs need a second one
# for the power values (async_get_instant_metrics). Without protection,
# continuous operation (especially with metering plugs or a long-open
# dialog) can break the limit and trigger a 24-hour ban.
#
# Two mechanisms work together:
# 1. Power metrics are decoupled from the status poll and queried at most
#    every MEROSS_METRICS_MIN_INTERVAL seconds per device.
# 2. A token bucket per device hard-caps ALL cloud queries at
#    MEROSS_HOURLY_BUDGET/hour - regardless of the configured intervals.
MEROSS_METRICS_MIN_INTERVAL = 120   # minimum spacing (s) between power metric queries per device
MEROSS_HOURLY_BUDGET = 150          # hard ceiling: cloud queries/hour per device (< 200 with reserve)
MEROSS_BUDGET_BURST = 15            # token bucket capacity (allowed short burst per device)
MEROSS_THROTTLE_NOTIFY_COOLDOWN = 600  # minimum spacing (s) between throttle announcements to the user
# Battery level of hub subdevices (MS100/MS130/valves) changes very
# slowly, so it is polled at most once per this interval per hub (one extra
# HUB_BATTERY cloud call per subdevice at that cadence - negligible against the
# hourly budget above).
MEROSS_BATTERY_POLL_INTERVAL = 3600  # spacing (s) between battery polls per hub AFTER a success
MEROSS_BATTERY_RETRY_INTERVAL = 300  # retry spacing (s) while no battery value has been obtained yet

# ============================================================
# Netatmo API endpoints
# ============================================================
NETATMO_AUTH_URL = "https://api.netatmo.com/oauth2/authorize"
NETATMO_TOKEN_URL = "https://api.netatmo.com/oauth2/token"
NETATMO_API_BASE = "https://api.netatmo.com/api"
# Höchstalter des /homesdata-Zwischenspeichers (Topologie + Heizprogramme).
# Diese Daten ändern sich nur, wenn der Nutzer in der Netatmo-App etwas
# umbaut - fünf Minuten sind reichlich frisch und sparen im Geräte-Menü eine
# Anfrage pro Aufklappen eines Thermostats. Nach einem Programmwechsel durch
# die Erweiterung wird der Speicher ohnehin sofort verworfen
# (invalidate_homesdata_cache).
HOMESDATA_CACHE_SECONDS = 300
# Höchstens alle so viele Sekunden macht update_device_status den VOLLEN
# Statuslauf (getstationsdata + homesdata + Zeitplan-Auflösung, >=3 Aufrufe);
# dazwischen wird nur /homestatus gepollt (1 Aufruf pro Haus). Hält die
# fg-Polling-Last (15-s-Intervall) sicher unter Netatmos Nutzerlimit von
# ~500 Aufrufen/Stunde: ~240 homestatus/h + 12 volle Läufe/h.
NETATMO_FULL_REFRESH_SECONDS = 300
# Note: this redirect URI MUST match the URI registered at dev.netatmo.com
# exactly (scheme, host, port and path - even localhost vs. 127.0.0.1 matters).
# The port is configurable per user in the settings (config key
# ``netatmoRedirectPort``); this constant is only the default. 8474 is
# deliberately an innocuous, rarely used port (the previous default 1521 is the
# Oracle DB listener port and collided).
NETATMO_REDIRECT_HOST = "localhost"
NETATMO_REDIRECT_PORT = 8474
NETATMO_REDIRECT_PATH = "/callback"


def netatmo_redirect_uri(port=None, host=NETATMO_REDIRECT_HOST):
    """Builds the Netatmo redirect URI for the given port.

    Single source of truth so the local callback server, the authorization
    request and the token exchange are guaranteed to use the same URI -
    any deviation otherwise leads to "redirect_uri mismatch".
    """
    return f"http://{host}:{port or NETATMO_REDIRECT_PORT}{NETATMO_REDIRECT_PATH}"


# Default URI (backward compatibility for imports without an explicit port).
NETATMO_REDIRECT_URI = netatmo_redirect_uri()
NETATMO_DEFAULT_SCOPES = "read_station read_thermostat write_thermostat read_homecoach"

# ============================================================
# Audio feedback (central beep constants)
# Each beep is a tuple (frequency_hz, duration_ms).
# Used in __init__.py, device_dialog.py and settings_panel.py so that
# all platforms sound consistent.
# ============================================================
BEEP_ON = (800, 50)           # device switched on
BEEP_OFF = (600, 50)          # device switched off
BEEP_ACTION = (700, 60)       # generic action performed
BEEP_ERROR = (200, 200)       # an error occurred
BEEP_SUCCESS = (880, 80)      # success confirmation (e.g. status query finished)
BEEP_LOADING = (440, 80)      # loading/wait indicator (periodic)
BEEP_EXTERNAL_CHANGE = (700, 80)  # external change detected (Alexa / app / physical)
