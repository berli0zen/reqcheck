# SPDX-FileCopyrightText: 2026 The reqcheck authors
# SPDX-License-Identifier: AGPL-3.0-or-later OR PolyForm-Noncommercial-1.0.0
"""Network access for every check.

HTTPS certificates are verified, every read is bounded, and nothing is fetched
from a private, loopback, link-local or otherwise non-public address, including
through a redirect. A website built on this code fetches whatever domain a
visitor types, and without that block it could be used to reach the network it
runs on (server-side request forgery).
"""

import http.client
import ipaddress
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; reqcheck)"

# SI-10 (NIST SP 800-53r5), Information Input Validation. Remote bodies are
# untrusted input, so reads are bounded. 8 MB is far above any real careers page.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

# AC-6, Least Privilege, applied to what reaches an external binary. `whois`
# receives a hostname, and a value beginning with "-" would be read as a flag.
HOSTNAME_OK = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$")


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


CTX = _ctx()


def is_public(host):
    """True if every address the host resolves to is publicly routable, False if
    any is not, None if it does not resolve."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, ValueError):
        return None
    if not infos:
        return None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            return False
    return True


class _NonPublic(Exception):
    pass


class _PublicRedirectsOnly(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to a public http(s) address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlparse(newurl)
        if target.scheme not in ("http", "https") or is_public(target.hostname or "") is not True:
            raise _NonPublic(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_PublicRedirectsOnly,
                                      urllib.request.HTTPSHandler(context=CTX))


def get(url, timeout=15):
    """Returns (text, final_url, status). status is an int, or a string reason.

    A refusal (403) is not an absent page, and callers keep the two apart. The
    string reasons: "dns:unresolved", "invalid:url", "refused:private_address",
    and "error:<name>" for a failed connection or an over-size body.
    """
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "", url, "invalid:url"
    public = is_public(parts.hostname)
    if public is None:
        return "", url, "dns:unresolved"
    if public is False:
        return "", url, "refused:private_address"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            # Read one byte past the cap so truncation is detectable.
            body = r.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                # A truncated body cannot support absence: what is being looked
                # for may be in the part that was not read.
                return "", r.geturl(), "error:ResponseTooLarge"
            return body.decode("utf-8", "replace"), r.geturl(), r.status
    except _NonPublic:
        return "", url, "refused:private_address"
    except urllib.error.HTTPError as e:
        return "", url, e.code
    except urllib.error.URLError as e:
        # A DNS failure means the host does not exist, which is not a server
        # refusing the request.
        reason = getattr(e, "reason", None)
        if isinstance(reason, OSError) and getattr(reason, "errno", None) in (8, -2, -3, 11001):
            return "", url, "dns:unresolved"
        if "getaddrinfo" in str(reason) or "Name or service not known" in str(reason):
            return "", url, "dns:unresolved"
        return "", url, f"error:{e.__class__.__name__}"
    except (ssl.SSLError, OSError) as e:
        return "", url, f"error:{e.__class__.__name__}"
    except (http.client.InvalidURL, ValueError):
        # A malformed address built from page content. Nothing was sent, so this
        # is not a refusal and is not counted as one.
        return "", url, "invalid:url"
    except http.client.HTTPException as e:
        return "", url, f"error:{e.__class__.__name__}"


def strip_html(h):
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h or "", flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))
