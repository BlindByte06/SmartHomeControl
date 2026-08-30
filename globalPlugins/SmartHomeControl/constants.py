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
    'schedule': _("Schedule"),
    # Translators: Netatmo heating mode (manually set temperature).
    'manual': _("Manual"),
    # Translators: Netatmo heating mode (away, reduced temperature).
    'away': _("Away"),
    # Translators: Netatmo heating mode (frost guard).
    'hg': _("Frost guard"),
    # Translators: Netatmo heating mode (maximum heating power).
    'max': _("Maximum"),
    # Translators: Netatmo heating mode (home).
    'home': _("Home"),
}

# ============================================================
# Diffuser spray mode labels
# Used in: device_dialog.py (_execute_action)
# ============================================================
DIFFUSER_MODE_NAMES = {
    # Translators: Spray mode of a Meross diffuser (light).
    'diffuser_light': _("Light spray"),
    # Translators: Spray mode of a Meross diffuser (strong).
    'diffuser_strong': _("Strong spray"),
    # Translators: Spray mode of a Meross diffuser (off).
    'diffuser_off': _("Off"),
}

# ============================================================
# Meross white presets (keys are the API values of set_light_white)
# Used in: device_dialog.py, history.py
# ============================================================
MEROSS_WHITE_PRESET_NAMES = {
    # Translators: Short names of the light colors (announcement/history).
    'warm': _("Warm white"),
    # Translators: Name of a white light colour of a lamp.
    'daylight': _("Daylight"),
    'cool': _("Cool white"),
}

# The keys above are also written to the history. Versions up to 26.7.3 used
# German ones there - this table keeps those entries readable instead of
# leaving a raw "tageslicht" in the list.
MEROSS_WHITE_PRESET_LEGACY = {
    'tageslicht': 'daylight',
    'kalt': 'cool',
}

# ============================================================
# VeSync mode labels
# Used in: device_dialog.py (actions)
# ============================================================
VESYNC_PURIFIER_MODE_NAMES = {
    # Translators: Operating mode of a VeSync air purifier.
    'auto': _("Auto"),
    # Translators: Operating mode of a VeSync air purifier.
    'manual': _("Manual"),
    # Translators: Operating mode of a VeSync air purifier.
    'sleep': _("Sleep mode"),
    # Translators: Operating mode of a VeSync air purifier.
    'turbo': _("Turbo"),
    # Translators: Operating mode of a VeSync air purifier (for pets).
    'pet': _("Pet mode"),
}

VESYNC_FAN_MODE_NAMES = {
    # Translators: Operating mode of a VeSync fan.
    'normal': _("Normal"),
    # Translators: Operating mode of a VeSync fan.
    'turbo': _("Turbo"),
    # Translators: Operating mode of a VeSync fan.
    'auto': _("Auto"),
    # Translators: Operating mode of a VeSync fan (advanced sleep mode).
    'advancedSleep': _("Sleep mode"),
}

# ============================================================
# Favorites layer: how long the switching press stays valid
# ============================================================
# After a digit has announced a status, the SAME digit switches - but only
# within this window. It exists because the layer used to stay open
# indefinitely: press a digit, get distracted, press it again minutes
# later, and a device switched. On a power strip carrying a computer that
# is not a nuisance but lost work.
#
# The window is measured from the announcement, and the default is five
# seconds rather than the two that feel right for a lamp. A purifier
# announces mode, fan level, air quality and filter life, which takes four
# to five seconds to speak - a two-second window would expire while the
# announcement it is meant to be a reaction to is still running, and that
# is precisely the failure the layer was built to remove.
#
# Letting it expire is never a dead end: the same digit then simply
# announces again and opens a fresh window.
FAV_LAYER_SWITCH_WINDOW_DEFAULT = 5
FAV_LAYER_SWITCH_WINDOW_MIN = 1
FAV_LAYER_SWITCH_WINDOW_MAX = 30

# ============================================================
# VeSync air fryer (Cosori)
# ============================================================
# Cooking states as the appliance reports them. Established from the log of
# a complete programme on a CAF-P583S: standby -> ready (programme set,
# countdown not yet running) -> cooking -> cookEnd -> standby.
#
# Stored language-neutrally and translated only when displayed, so a value
# that reaches the history or the favourites does not stay in the language
# the interface happened to have.
#
# 'cookStop' joined them from a log, and its meaning came from the tester
# standing at the appliance: it is PAUSE, not stop. The name reads like an
# ending and is not one - the programme is still loaded, its remaining time
# frozen, waiting to go on. Guessing from the name alone would have had the
# add-on offer to start something new while a meal sat half-cooked.
#
# The older single-element Cosori line (CS137/CS158) speaks a different
# protocol and additionally documents 'heating', 'preheatStop', 'preheatEnd'
# and 'pullOut'. None of those has been seen on a Dual Blaze and what they
# mean there would be a guess, so they are deliberately absent: an unknown
# value falls through as raw text and the log then names the one to add -
# which is exactly how 'cookStop' arrived.
VESYNC_FRYER_COOK_STATES = {
    # Translators: An air fryer that is not cooking.
    'standby': _("standby"),
    # Translators: An air fryer with a programme selected that has not
    # started running yet.
    'ready': _("ready to start"),
    # Translators: An air fryer that is cooking.
    'cooking': _("cooking"),
    # Translators: An air fryer that has finished its programme.
    'cookend': _("finished"),
    # Translators: An air fryer whose programme is paused and can go on.
    'cookstop': _("paused"),
}

# What is worth saying out loud when the cooking state changes - as
# opposed to what the tree shows, which is every state.
#
# The end is the reason this exists. A cook who cannot look into the
# basket has nothing else to go by: the tree speaks only the line under
# the focus, and in a tester's log a whole programme ran to its end with
# the screen reader saying "Temperature: 196 °C" three times and nothing
# about the programme being over.
#
# Three of the five states are in here. 'standby' is the appliance
# falling back to nothing afterwards and is no news, and 'ready' only
# comes about with somebody standing at the appliance turning its dial,
# who does not need to be told what they just did.
#
# One short phrase each, not the programme name as well: the announcement
# already carries the device name in front of it, and "Sigh fry: Roast:
# finished" is three things where one will do.
VESYNC_FRYER_COOK_ANNOUNCEMENTS = {
    # Translators: Announcement when an air fryer has started cooking.
    'cooking': _("Programme running"),
    # Translators: Announcement when a cooking programme was paused at the
    # appliance.
    'cookstop': _("Programme paused"),
    # Translators: Announcement when a cooking programme has finished.
    'cookend': _("Programme finished"),
}

# Cooking programmes. The appliance reports the programme twice: as `mode`,
# which stays English, and as `recipeName`, which arrives in the language of
# the VeSync app (an ioBroker capture shows mode "Chicken" next to
# recipeName "Huhn"). Only `mode` is usable as a key, and only it is stored.
#
# Eleven of the twelve are confirmed against a CAF-P583S: the appliance was
# asked to load each programme in turn and the replies were read off. The
# spellings are not what one would guess - 'AirFry' has no space, 'Veggies'
# is not 'Vegetables', and fries arrive as 'French fries', which was the one
# guess that missed and showed up as raw English in the interface. Keys are
# normalised for case and spaces on lookup, and 'fries' is kept alongside in
# case a regional variant shortens it.
#
# 'Keep warm' is documented by Cosori for this model - 80 degrees for 30
# minutes, the twelfth of the twelve functions - and simply has not been
# loaded on the test appliance yet, which is why no reply has ever carried
# it. What the wire calls it is therefore still unconfirmed: the key here
# is the manual's wording with the spaces stripped, and if the appliance
# spells it differently it will be displayed as it comes and the log will
# say what to add.
VESYNC_FRYER_PROGRAMME_NAMES = {
    # Translators: Cooking programme of an air fryer.
    'steak': _("Steak"),
    # Translators: Cooking programme of an air fryer.
    'chicken': _("Chicken"),
    # Translators: Cooking programme of an air fryer.
    'seafood': _("Seafood"),
    # Translators: Cooking programme of an air fryer.
    'veggies': _("Vegetables"),
    # Translators: Cooking programme of an air fryer.
    'frenchfries': _("Fries"),
    # Translators: Cooking programme of an air fryer.
    'fries': _("Fries"),
    # Translators: Cooking programme of an air fryer (food from frozen).
    'frozen': _("Frozen food"),
    # Translators: Cooking programme of an air fryer.
    'airfry': _("Air fry"),
    # Translators: Cooking programme of an air fryer.
    'reheat': _("Reheat"),
    # Translators: Cooking programme of an air fryer.
    'broil': _("Broil"),
    # Translators: Cooking programme of an air fryer.
    'roast': _("Roast"),
    # Translators: Cooking programme of an air fryer.
    'bake': _("Bake"),
    # Translators: Cooking programme of an air fryer.
    'keepwarm': _("Keep warm"),
    # Translators: Cooking programme of an air fryer with temperature and
    # time set by hand rather than by a preset. Deliberately not just
    # "Manual" - that msgid already belongs to the purifier operating mode.
    'custom': _("Manual programme"),
}

# Air quality levels (1=excellent ... 4=poor)
VESYNC_AIR_QUALITY_NAMES = {
    # Translators: Air quality level 1 (best) of a VeSync air purifier.
    1: _("excellent"),
    # Translators: Air quality level 2 of a VeSync air purifier.
    2: _("good"),
    # Translators: Air quality level 3 of a VeSync air purifier.
    3: _("moderate"),
    # Translators: Air quality level 4 (worst) of a VeSync air purifier.
    4: _("poor"),
}

# Night light modes (Core 200S)
VESYNC_NIGHTLIGHT_MODE_NAMES = {
    # Translators: Night light state (on).
    'on': _("On"),
    # Translators: Night light state (off).
    'off': _("Off"),
    # Translators: Night light state (dimmed).
    'dim': _("Dimmed"),
}

# Auto profile (Core 300S/400S/500S/600S)
VESYNC_AUTO_PREFERENCE_NAMES = {
    # Translators: Auto mode profile of a VeSync air purifier.
    'default': _("Default"),
    # Translators: Auto mode profile of a VeSync air purifier.
    'efficient': _("Efficient"),
    # Translators: Auto mode profile of a VeSync air purifier.
    'quiet': _("Quiet"),
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
    1: _("Low"),
    # Translators: Fan level label (middle level).
    2: _("Medium"),
    # Translators: Fan level label (highest level).
    3: _("High"),
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
    # Which tab is active when the device menu opens: 'devices' or
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
    "notifyMerossWater": "boolean(default=true)",
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
    "notifyVesyncCook": "boolean(default=true)",

    # Not a notification: the favorites layer's switching window, in
    # seconds. It belongs here for the same reason as everything above -
    # a key without an entry in this table is handed back as the TEXT
    # that stands in nvda.ini, because there is no declaration to
    # validate it against. The default mirrors
    # FAV_LAYER_SWITCH_WINDOW_DEFAULT; the range is not declared because
    # _fav_layer_switch_window clamps it anyway and a value outside the
    # range would then fail validation instead of simply being pulled
    # into it.
    "favLayerSwitchWindow": "integer(default=5)",
}

# ============================================================
# Background refresh configuration
# ============================================================
BACKGROUND_REFRESH_INTERVAL = 30   # seconds between automatic updates (backoff base)
# How long the device cache counts as "fresh" (dialog and status announcement
# then use it directly instead of polling themselves). This MUST stay above the
# largest background poll interval plus the duration of that poll - otherwise
# the cache is stale again just before every poll and the dialog runs a full
# refresh of its own on top of the scheduler's. With bg=60 s (VeSync) and a
# poll taking up to ~10 s, 90 s leaves the needed headroom.
CACHE_VALID_DURATION = 90          # seconds for which the cache counts as "fresh"
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
# The scheduler now sleeps until the next poll is due instead of waking on a
# fixed tick (see _scheduler_body). SCHEDULER_TICK is therefore only the
# lower bound of the resolution, and the thread wakes as often as it polls
# rather than 86400 times a day. SCHEDULER_MAX_SLEEP caps the sleep as a
# safety net should next_due ever sit far in the future.
SCHEDULER_TICK = 1  # base tick of the scheduler in seconds (resolution of the next_due check)
SCHEDULER_MAX_SLEEP = 15  # upper bound of the sleep (s), pure safety net
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
# Tokens that only user-initiated queries may use. With the dialog open, the
# routine poll of a metering plug (status every 30 s plus power metrics every
# 120 s) reaches exactly MEROSS_HOURLY_BUDGET, so it emptied the bucket and the
# consumption query - one message per device every 15 minutes - kept being
# skipped ("Consumption query skipped (budget)"). The background poll now stops
# at this reserve, so what the user explicitly asks for always gets through.
MEROSS_BUDGET_RESERVE = 5
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
# Maximum age of the /homesdata cache (topology + heating schedules).
# This data only changes when the user rearranges something in the Netatmo
# app, so five minutes is plenty fresh and saves one request per thermostat
# expansion in the device menu. After the add-on changes a schedule the
# cache is dropped immediately (invalidate_homesdata_cache).
HOMESDATA_CACHE_SECONDS = 300
# At most this often update_device_status does the FULL status run
# (getstationsdata + homesdata + schedule resolution, >=3 calls); in between
# it polls only /homestatus (1 call per home). Keeps the foreground load
# (15 s interval) safely under Netatmo's ~500 calls/hour user limit:
# ~240 homestatus/h plus 12 full runs/h.
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
