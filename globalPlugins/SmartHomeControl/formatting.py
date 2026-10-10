# -*- coding: utf-8 -*-
"""Smart Home Control - numbers the way the interface language writes them.

Sixteen places wrote their decimals with a comma of their own making
(``f"{x:.2f}".replace(".", ",")``), and the rest with Python's point. So an
English interface heard "0,52 kilowatt hours" while a German one heard
"21.5 degrees" - each language got half of its numbers wrong.

The separator is therefore a text of the catalogue like any other: the
translator of a language decides how it writes a number, and every place
that shows one goes through here.

Not for the CSV export: a spreadsheet reads the separator of the Windows
region, not the one of the NVDA language (see history.export_csv).
"""

try:
    import addonHandler
    addonHandler.initTranslation()
except Exception:  # outside of NVDA (checks, build)
    pass
if "_" not in globals():
    def _(s):
        return s


def _localise(text):
    """Puts the language's separator into ``text`` written with a point."""
    integer, point, fraction = text.partition(".")
    if not point:
        return text
    # Translators: How a number with decimals is written wherever the add-on
    # shows or says one (device tree, announcements, history). {integer} is
    # the part before the decimal separator, {fraction} the part after it.
    # English writes a point; German translates this as "{integer},{fraction}".
    return _("{integer}.{fraction}").format(integer=integer, fraction=fraction)


def fmt_decimal(value, digits=1):
    """``value`` with exactly ``digits`` decimals, e.g. "21,5" in German.

    Anything that is not a number comes back as text, unchanged - a reading
    that arrives as something unexpected is still worth showing.
    """
    if value is None:
        return ""
    try:
        text = f"{float(value):.{int(digits)}f}"
    except (TypeError, ValueError):
        return str(value)
    return _localise(text)


def fmt_number(value):
    """``value`` with as many decimals as it has: 230 stays 230, 12.5 -> 12,5."""
    if value is None:
        return ""
    try:
        text = f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)
    return _localise(text)
