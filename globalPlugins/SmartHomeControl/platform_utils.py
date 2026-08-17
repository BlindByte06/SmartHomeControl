# -*- coding: utf-8 -*-
"""Smart Home Control - Central platform mapping for device wrappers.

Single source of truth for the question "which platform does this
device belong to?". Previously the pattern "Meross = everything that is
not Netatmo/VeSync" was scattered across the code in several variants –
with the risk that new platforms (most recently Cozytouch) get forgotten
in individual places. New platforms only need to be added HERE and in
PLATFORM_LABELS; all filter/count/group call sites use these helpers.

"""

# Order = display/count order (Meross first, historically).
PLATFORMS = ('meross', 'netatmo', 'vesync', 'cozytouch')

# Brand names (untranslated) for announcements and tree labels.
PLATFORM_LABELS = {
    'meross': 'Meross',
    'netatmo': 'Netatmo',
    'vesync': 'VeSync',
    'cozytouch': 'Cozytouch',
}

# Platforms that sign in with email and password. Netatmo is missing on
# purpose: its authorisation is granted in the browser (OAuth2), so a
# refused login is not answered by typing a password again but by the
# "Connect to Netatmo" button.
PASSWORD_PLATFORMS = ('meross', 'vesync', 'cozytouch')


def platform_of(device):
    """Returns the platform key ('meross'/'netatmo'/'vesync'/'cozytouch')
    of a device wrapper.

    Meross is the fallback because Meross wrappers are the only ones
    that do not have to carry explicit is_* flags of the other platforms.
    """
    if getattr(device, 'is_netatmo', False):
        return 'netatmo'
    if getattr(device, 'is_vesync', False):
        return 'vesync'
    if getattr(device, 'is_cozytouch', False):
        return 'cozytouch'
    return 'meross'


def split_by_platform(devices):
    """Splits a device list into a dict {platform: [devices]}.

    ALWAYS returns all four keys (possibly with an empty list) so
    callers do not have to check defensively.
    """
    result = {name: [] for name in PLATFORMS}
    for d in devices or []:
        result[platform_of(d)].append(d)
    return result
