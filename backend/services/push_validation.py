# -*- coding: utf-8 -*-
"""Validation of Web Push subscriptions (SSRF / abuse protection).

The server later makes an HTTPS request TO the endpoint a browser hands us, so an attacker who could
register `https://169.254.169.254/...` or `https://intranet/...` could make the server call arbitrary
hosts. We therefore accept only https endpoints on the hosts of the real push services."""
import base64
import ipaddress
import re
from urllib.parse import urlsplit

MAX_ENDPOINT = 700
MAX_KEY = 200
# Browser push services (Chrome/Edge/Android/Samsung = FCM, Firefox = Mozilla, Safari = Apple, legacy Edge = WNS)
ALLOWED_HOST_SUFFIXES = (
    "fcm.googleapis.com", "android.googleapis.com", "push.services.mozilla.com",
    "push.apple.com", "notify.windows.com",
)
_B64 = re.compile(r"^[A-Za-z0-9_\-+/=]+$")


def validate_endpoint(endpoint):
    """-> (ok, error). Pure string checks - no network access."""
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > MAX_ENDPOINT:
        return False, "Invalid endpoint length."
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in endpoint):
        return False, "Invalid characters in endpoint."
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return False, "Malformed endpoint."
    if parts.scheme != "https":
        return False, "Endpoint must use https."
    if parts.username or parts.password or parts.fragment:
        return False, "Endpoint must not contain credentials or fragments."
    if port not in (None, 443):
        return False, "Endpoint port not allowed."
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return False, "Endpoint has no host."
    try:
        ipaddress.ip_address(host)
        return False, "IP-address endpoints are not allowed."
    except ValueError:
        pass
    if not any(host == s or host.endswith("." + s) for s in ALLOWED_HOST_SUFFIXES):
        return False, "Endpoint host is not a known push service."
    return True, ""


def validate_keys(p256dh, auth):
    for v in (p256dh, auth):
        if not isinstance(v, str) or not v or len(v) > MAX_KEY or not _B64.match(v):
            return False, "Invalid subscription keys."
    try:
        raw = base64.urlsafe_b64decode(p256dh + "=" * (-len(p256dh) % 4))
        if len(raw) != 65:
            return False, "Invalid p256dh key."
    except Exception:
        return False, "Invalid p256dh key."
    return True, ""


def endpoint_host(endpoint):
    return (urlsplit(endpoint).hostname or "").lower()
