# -*- coding: utf-8 -*-
"""Smart Home Control - the one error the interface has to tell apart.

Whether a login failed because of the credentials or because of the network
decides what may be offered afterwards: asking for the password again is
right in the first case and wrong in the second - at NVDA start, where the
network is often not up yet, a dialog would pop up unasked.

The message text cannot answer that question: it is translated, so a check
for the word "login" would work in English and fail in German. The API
layers therefore raise THIS type wherever they know that the platform
refused the credentials themselves.

Derived from ValueError, because that is what the wrong-credentials case
used to arrive as - existing `except ValueError` keeps catching it.
"""


class CredentialsRejected(ValueError):
    """The platform refused email/password - a retry will not help."""
