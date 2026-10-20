# -*- coding: utf-8 -*-
"""Shared helper functions for the device dialog and its platform mixins."""

import tones
import wx
from .constants import VESYNC_PURIFIER_LEVEL_LABELS_3


def _beep(beep_const):
    """Plays a (frequency, duration) tuple as audio feedback.

    Always on the wx main thread: the worker threads of the cloud actions
    beep too, and NVDA plays its tones from the main thread - it does not
    say that tones.beep may be called from anywhere else. From another
    thread the tone is handed over with wx.CallAfter.
    """
    frequency, duration = beep_const[0], beep_const[1]
    is_main_thread = getattr(wx, 'IsMainThread', None)
    if is_main_thread is not None and not is_main_thread():
        wx.CallAfter(tones.beep, frequency, duration)
        return
    tones.beep(frequency, duration)


def _vesync_purifier_level_label(level, fan_levels):
    """Returns the display text for a fan level.

    For Core 200S/300S (3 levels) the plain-text label is appended as
    well (e.g. "1 (Low)"). For models with 4 or 5 levels there is no
    unambiguous wording in the Levoit app, so we only show the number.
    """
    if level is None:
        return "?"
    if fan_levels and len(fan_levels) == 3 and level in VESYNC_PURIFIER_LEVEL_LABELS_3:
        return f"{level} ({VESYNC_PURIFIER_LEVEL_LABELS_3[level]})"
    return str(level)
