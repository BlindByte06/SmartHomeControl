# -*- coding: utf-8 -*-
"""Smart Home Control - the errors the interface has to tell apart.

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

The second question is how loud a failure goes into the log. See
``log_failure``.
"""

import re


class CredentialsRejected(ValueError):
    """The platform refused email/password - a retry will not help."""


# Words of failures that come from the network or a cloud, in the messages of
# requests, aiohttp, Windows and the add-on's own API layers (some of which
# were written in German once).
_TRANSIENT_WORDS = (
    'timeout', 'timed out', 'time out', 'connection', 'network',
    'unreachable', 'not reachable', 'not responding', 'does not respond',
    'failed to resolve', 'getaddrinfo', 'name resolution', 'max retries',
    'temporarily', 'service unavailable', 'bad gateway', 'nicht erreichbar',
    'zeitüberschreitung', 'winerror 10051', 'winerror 10053',
    'winerror 10054', 'winerror 10060', 'winerror 10061', 'winerror 10065',
)


def is_transient(error):
    """True for a failure that is no fault of the add-on.

    The network, a timeout, a server answering 5xx, a cloud refusing a
    request with a code of its own. Retrying later is the remedy for all of
    them, and none of them is something to look for in the add-on's code.
    """
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    try:
        import requests
        if isinstance(error, (requests.ConnectionError, requests.Timeout)):
            return True
    except Exception:  # requests is missing outside NVDA, or a stand-in
        pass
    # A cloud that answered with an error code of its own (Petlibro and
    # VeSync keep it on the exception).
    if getattr(error, 'code', None) is not None:
        return True
    text = str(error).lower()
    if re.search(r'(http|api)[^0-9]{0,12}5\d\d\b', text):
        return True
    return any(word in text for word in _TRANSIENT_WORDS)


def log_failure(context, error):
    """Logs a failed operation at the level it deserves.

    WARNING for what the network, a cloud or the credentials did, ERROR for
    everything else. An ERROR makes NVDA play its error sound for whoever
    has "Play a sound for logged errors" switched on: every login of a
    laptop that starts before its Wi-Fi did that once per platform, and a
    plug that did not answer a switch command once more.

    Never with a stack trace: it can carry request data - tokens, headers.
    """
    try:
        from logHandler import log
    except ImportError:  # outside of NVDA (checks)
        import logging
        log = logging.getLogger(__name__)
    message = f"{context}: {type(error).__name__}: {error}"
    if isinstance(error, CredentialsRejected) or is_transient(error):
        log.warning(message)
    else:
        log.error(message)
