# SPDX-FileCopyrightText: 2026 The reqcheck authors
# SPDX-License-Identifier: AGPL-3.0-or-later OR PolyForm-Noncommercial-1.0.0
"""Network access for every check.

Every request goes over HTTPS with certificates verified, every read is bounded,
and nothing is fetched from a private, loopback, link-local or otherwise
non-public address. The address check runs on each connection, against the one
DNS lookup that connection uses, so it holds through redirects and through a DNS
answer that changes between two lookups (DNS rebinding). A website built on this
code fetches whatever domain a visitor types, and without that block it could be
used to reach the network it runs on (server-side request forgery).

The platforms in NOT_READ are never requested, whether an address names them
directly or a redirect leads there.
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

# Platforms whose terms prohibit automated access, or which block it. Never read,
# whether an address names them or a redirect leads there. Matched on the
# hostname, so subdomains, country sites and LinkedIn's lnkd.in links are covered.
NOT_READ = (
    (re.compile(r"(?:^|\.)(?:linkedin\.[a-z.]+|lnkd\.in)$"),
     "LinkedIn's User Agreement prohibits automated access"),
    (re.compile(r"(?:^|\.)indeed\.[a-z.]+$"), "Indeed blocks automated clients"),
    (re.compile(r"(?:^|\.)glassdoor\.[a-z.]+$"), "Glassdoor blocks automated clients"),
    (re.compile(r"(?:^|\.)ziprecruiter\.[a-z.]+$"), "ZipRecruiter blocks automated clients"),
    (re.compile(r"(?:^|\.)builtin\.com$"), "Built In serves a CAPTCHA to automated clients"),
    (re.compile(r"(?:^|\.)dice\.com$"), "Dice renders listings in the browser"),
)


def not_read_reason(host):
    """Why a host is never read automatically, or None if it can be read."""
    host = (host or "").lower().rstrip(".")
    return next((why for pattern, why in NOT_READ if pattern.search(host)), None)


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


CTX = _ctx()


def _is_global(address):
    ip = ipaddress.ip_address(address.split("%")[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def is_public(host):
    """True if every address the host resolves to is publicly routable, False if
    any is not, None if it does not resolve."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, ValueError):
        return None
    if not infos:
        return None
    return all(_is_global(info[4][0]) for info in infos)


class _Refused(Exception):
    """A request reqcheck will not make. The argument is the address refused."""
    status = "refused"


class _NonPublic(_Refused):
    status = "refused:private_address"


class _NotRead(_Refused):
    status = "refused:not_read"


class _PlainHTTP(_Refused):
    status = "refused:plain_http"


class _NotWeb(_Refused):
    status = "invalid:url"


def _https(url):
    """The same address over HTTPS, or None if it is not a web address.

    Plain HTTP is never used: a page read unencrypted can be altered on the way,
    and the checks would report what the alteration says. Raises ValueError on
    a malformed address.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    netloc = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    if parts.port and not (parts.scheme == "http" and parts.port == 80):
        netloc += f":{parts.port}"
    return urllib.parse.urlunsplit(("https", netloc, parts.path, parts.query, parts.fragment))


def _connect_public(address, timeout=None, source_address=None):
    """socket.create_connection, but only to publicly routable addresses, all
    taken from the same lookup the connection then uses."""
    host, port = address
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    if not all(_is_global(info[4][0]) for info in infos):
        raise _NonPublic(host)
    error = None
    for family, kind, proto, _, sockaddr in infos:
        sock = None
        try:
            sock = socket.socket(family, kind, proto)
            if isinstance(timeout, (int, float)):
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as e:
            error = e
            if sock is not None:
                sock.close()
    raise error or OSError(f"no address for {host}")


class _CheckedHTTPSConnection(http.client.HTTPSConnection):
    """An HTTPS connection made only to an address _connect_public has checked."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect_public


class _CheckedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_CheckedHTTPSConnection, req, context=self._context)


class _NoPlainHTTP(urllib.request.HTTPHandler):
    """Refuses plain HTTP, should an address reach the opener without _https()."""

    def http_open(self, req):
        raise _PlainHTTP(req.full_url)


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect over HTTPS, and never to a platform in NOT_READ. The
    connection then checks the new address like any other."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            target = _https(newurl)
        except ValueError:
            target = None
        refusal = None
        if not target:
            refusal = _NotWeb(newurl)
        elif not_read_reason(urllib.parse.urlsplit(target).hostname):
            refusal = _NotRead(target)
        elif urllib.parse.urlsplit(newurl).scheme == "http" and target == req.full_url:
            # The site sends its own HTTPS address back to plain HTTP.
            refusal = _PlainHTTP(newurl)
        if refusal:
            if fp is not None:
                fp.close()
            raise refusal
        return super().redirect_request(req, fp, code, msg, headers, target)


def _opener():
    """Only the handlers reqcheck needs. No proxy, since a proxy makes its own DNS
    lookup where the address check cannot see it. No file:, ftp: or data:
    addresses, and no plain HTTP."""
    opener = urllib.request.OpenerDirector()
    for handler in (_NoPlainHTTP(), _CheckedHTTPSHandler(context=CTX), _CheckedRedirects(),
                    urllib.request.HTTPDefaultErrorHandler(), urllib.request.HTTPErrorProcessor(),
                    urllib.request.UnknownHandler()):
        opener.add_handler(handler)
    return opener


_OPENER = _opener()


def get(url, timeout=15):
    """Returns (text, final_url, status). status is an int, or a string reason.

    A plain http:// address is read over HTTPS instead. A refusal (403) is not
    an absent page, and callers keep the two apart. The string reasons:
    "dns:unresolved", "invalid:url", "refused:private_address",
    "refused:not_read" (a platform in NOT_READ; when a redirect led there,
    final_url is where it led), "refused:plain_http" (a site that sends HTTPS
    requests back to plain HTTP), and "error:<name>" for a failed connection or
    an over-size body.
    """
    try:
        target = _https(url)
    except ValueError:
        target = None
    if not target:
        return "", url, "invalid:url"
    host = urllib.parse.urlsplit(target).hostname
    if not_read_reason(host):
        return "", url, "refused:not_read"
    public = is_public(host)
    if public is None:
        return "", url, "dns:unresolved"
    if public is False:
        return "", url, "refused:private_address"
    req = urllib.request.Request(target, headers={
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
    except _Refused as e:
        return "", e.args[0], e.status
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
