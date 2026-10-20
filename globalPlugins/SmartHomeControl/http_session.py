# -*- coding: utf-8 -*-
"""Smart Home Control - an HTTP session that keeps credentials on their host.

requests follows redirects by itself. On a redirect to another host it drops
the Authorization header, but nothing else: Petlibro sends its token in a
header of its own ("token"), and VeSync, Cozytouch and Netatmo send tokens,
passwords and client secrets in the request body - which a 307 or 308
redirect carries along unchanged. A redirect from one of these clouds to
another host would therefore have handed them to that host.

The clouds are reached over TLS on fixed host names, so this needs a
compromised or misconfigured server to matter at all. It is cheap to rule
out all the same: a redirect to another host drops the token header, and one
that would carry a body there is refused.
"""

import requests

# Stand-ins keep the module importable where requests is stubbed out (the
# checks under tools/checks replace it with a bare module).
_SessionBase = getattr(requests, "Session", object)
_RequestException = getattr(requests, "RequestException", Exception)


class CrossHostRedirect(_RequestException):
    """A redirect to another host that would have carried credentials."""


class SafeSession(_SessionBase):
    """requests.Session that does not carry credentials to another host."""

    # Headers that hold a credential besides Authorization, which requests
    # removes by itself.
    SENSITIVE_HEADERS = ("token",)

    def rebuild_auth(self, prepared_request, response):
        if self.should_strip_auth(response.request.url, prepared_request.url):
            if prepared_request.body:
                # Only a 307 or 308 keeps the body - every other redirect has
                # turned the request into a GET without one by now.
                raise CrossHostRedirect(
                    "refused a redirect to another host that would have "
                    "carried the request body along")
            for name in self.SENSITIVE_HEADERS:
                prepared_request.headers.pop(name, None)
        super().rebuild_auth(prepared_request, response)
