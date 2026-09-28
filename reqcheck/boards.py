# SPDX-FileCopyrightText: 2026 The reqcheck authors
# SPDX-License-Identifier: AGPL-3.0-or-later OR PolyForm-Noncommercial-1.0.0
"""Read an employer's hosted job board through the board's public API.

Greenhouse, Lever, Ashby and Workable each publish a JSON API that renders an
employer's careers page. When a careers page loads its listings from one of
them, the listings are not in the page HTML. Reading the API the page reads is
reading the careers page.

How the four APIs behave, which the rules below depend on:
  - An unknown board answers 404. A known board answers 200 with its published
    list, so an empty or non-matching list is a measurement of that board.
  - Greenhouse states its own total (meta.total). Lever pages with limit/skip.
    Workable's widget lists a job once per location and is complete once
    de-duplicated by shortcode. Ashby returns the whole board in one response,
    which can exceed the 8 MB read cap on very large boards.
  - Greenhouse EU boards (job-boards.eu.greenhouse.io) have no public API host,
    so they are not read.

Two rules:
  - A board counts as the employer's only when a page on the employer's own site
    links to it. A board that merely shares the company's name is never used to
    conclude absence.
  - Absence needs the whole list. A board read in part supports "present" and
    never "absent".

Fetching is passed in as `get`, so this module makes no network decisions of its
own and inherits the caller's TLS verification and read cap.
"""

import json
import re
import unicodedata
import urllib.parse

SUPPORTED = ("greenhouse", "lever", "ashby", "workable")
NAMES = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "workable": "Workable"}

# SI-10: identifiers taken from a remote page are validated before they go into a
# URL, and escaped when they do. The API hosts below are fixed, so nothing read
# from a page can choose where a request is sent.
TOKEN_OK = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?$")
RESERVED = {
    "embed", "api", "v0", "v1", "v2", "v3", "jobs", "job", "j", "static", "assets",
    "careers", "widget", "accounts", "favicon", "robots", "sitemap", "images", "img",
    "css", "js", "fonts", "www", "apply", "help", "resources", "blog", "about",
    "login", "signup", "cdn", "posting-api", "static-assets", "privacy", "terms",
}
UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
JOB_ID_OK = {
    "greenhouse": re.compile(r"^\d{1,20}$"),
    "lever": re.compile(rf"^{UUID}$"),
    "ashby": re.compile(rf"^{UUID}$"),
    "workable": re.compile(r"^[A-Za-z0-9]{6,16}$"),
}
HOSTNAME_OK = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")
API_HOST = {
    "greenhouse": "boards-api.greenhouse.io",
    "lever": "api.lever.co",
    "ashby": "api.ashbyhq.com",
    "workable": "apply.workable.com",
}

# More distinct boards than this on one site reads as a directory of other
# companies' jobs (an investor portfolio, a job board), not one employer's list.
MAX_BOARDS = 3
LEVER_PAGE = 100
LEVER_MAX_PAGES = 20

# Where a page points at a board. Run over HTML with JSON escapes undone.
_REFS = [
    ("greenhouse", re.compile(
        r"(?:boards|job-boards)\.greenhouse\.io/embed/job_(?:board|app)(?:/js)?\?"
        r"[^\"'<>\s]*?\bfor=([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(
        r"(?<![\w.-])(?:boards-api|api)\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(
        r"(?<![\w.-])(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9_-]+)", re.I)),
    # Greenhouse's job-alert sign-up names the board: my.greenhouse.io/...?job_board=acme
    ("greenhouse", re.compile(
        r"(?<![\w.-])my\.greenhouse\.io/[^\"'<>\s]*?[?&]job_board=([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(
        r"(?<![\w.-])(?:jobs\.lever\.co|api\.lever\.co/v0/postings)/([A-Za-z0-9._-]+)", re.I)),
    ("ashby", re.compile(
        r"(?<![\w.-])(?:jobs\.ashbyhq\.com|api\.ashbyhq\.com/posting-api/job-board)/"
        r"([A-Za-z0-9._-]+)", re.I)),
    ("workable", re.compile(
        r"(?<![\w.-])apply\.workable\.com/(?:api/v\d/(?:widget/)?accounts/)?([A-Za-z0-9_-]+)", re.I)),
    # Older Workable boards live on a subdomain: acme.workable.com
    ("workable", re.compile(
        r"(?<![\w.-])([A-Za-z0-9][A-Za-z0-9-]{0,62})\.workable\.com(?![\w.-])", re.I)),
]

# A single posting, or a board's front page, given as a URL.
_POSTING = [
    ("greenhouse", re.compile(
        r"^https?://(?:boards|job-boards)\.greenhouse\.io/(?!embed/)([A-Za-z0-9_-]+)"
        r"(?:/jobs/(\d+))?/?(?:[?#].*)?$", re.I)),
    ("lever", re.compile(
        rf"^https?://jobs\.lever\.co/([A-Za-z0-9._-]+)(?:/({UUID}))?(?:/apply)?/?(?:[?#].*)?$", re.I)),
    ("ashby", re.compile(
        rf"^https?://jobs\.ashbyhq\.com/([A-Za-z0-9._-]+)(?:/({UUID}))?(?:/application)?/?(?:[?#].*)?$",
        re.I)),
    ("workable", re.compile(
        r"^https?://apply\.workable\.com/([A-Za-z0-9_-]+)(?:/j/([A-Za-z0-9]{6,16}))?(?:/apply)?/?"
        r"(?:[?#].*)?$", re.I)),
]
_GH_EMBED_APP = re.compile(r"^https?://(?:boards|job-boards)\.greenhouse\.io/embed/job_app\?", re.I)

STOP = {"a", "an", "and", "the", "of", "for", "to", "in", "on", "at", "with", "or", "-", "&"}


def clean(s, n=160):
    """Printable text from a remote value: control and format characters removed.

    A job title comes from whoever runs the board. Stripping Unicode categories
    C* removes terminal escape sequences and bidirectional overrides, so a title
    cannot rewrite the report it is printed in.
    """
    s = re.sub(r"\s+", " ", str(s or ""))
    s = "".join(ch for ch in s if not unicodedata.category(ch).startswith("C"))
    return re.sub(r" +", " ", s).strip()[:n]


def _unescape(html):
    """Undo the JSON and HTML escaping that hides board URLs inside page data."""
    h = html or ""
    for a, b in (("\\/", "/"), ("\\u002F", "/"), ("\\u002f", "/"), ("&amp;", "&"), ("&#x2F;", "/")):
        h = h.replace(a, b)
    return h


def _token(raw):
    tok = (raw or "").strip("._-")
    if not TOKEN_OK.match(tok) or tok.lower() in RESERVED:
        return None
    return tok


def find_refs(html, source):
    """Every supported board a page points at, in order of first appearance."""
    h = _unescape(html)
    seen, out = set(), []
    for ats, pat in _REFS:
        for m in pat.finditer(h):
            tok = _token(m.group(1))
            if not tok:
                continue
            key = (ats, tok.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append({"ats": ats, "token": tok, "source": source})
    return out


def board_from_url(url):
    """The board, and the posting if there is one, that a URL points at. Else None."""
    url = (url or "").strip()
    if _GH_EMBED_APP.match(url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        tok, jid = _token((q.get("for") or [""])[0]), (q.get("token") or [""])[0]
        if tok and JOB_ID_OK["greenhouse"].match(jid):
            return {"ats": "greenhouse", "token": tok, "job_id": jid}
        return None
    for ats, pat in _POSTING:
        m = pat.match(url)
        if not m:
            continue
        tok = _token(m.group(1))
        if not tok:
            return None
        jid = m.group(2)
        if jid and not JOB_ID_OK[ats].match(jid):
            jid = None
        return {"ats": ats, "token": tok, "job_id": jid}
    return None


def _fetch_json(get, url, host):
    """(data, problem). A problem is a plain reason; data is None whenever there is one."""
    text, final, status = get(url)
    if status == 404:
        return None, "404: no board or posting at this address"
    if status == "error:ResponseTooLarge":
        return None, "the response is over the 8 MB read cap, so it was not read"
    if not isinstance(status, int) or status >= 400 or not text:
        return None, f"the API answered {status}"
    # A redirect off the API host would mean reading something other than the board.
    if urllib.parse.urlparse(final or url).netloc.lower() != host:
        return None, f"the API redirected away from {host}"
    try:
        return json.loads(text), None
    except ValueError:
        return None, "the API answered with something other than JSON"


def read_board(ats, token, get):
    """Read one board's full published list.

    Returns jobs as {title, id, url, listed}, and whether the list is complete.
    `complete` is only ever True when the API itself gives us grounds for it.
    """
    out = {"ats": ats, "token": token, "api": None, "jobs": [], "complete": False, "problem": None}
    tok = _token(token)
    if ats not in SUPPORTED or not tok:
        out["problem"] = "not a board this can read"
        return out
    t = urllib.parse.quote(tok, safe="")
    host = API_HOST[ats]

    if ats == "greenhouse":
        out["api"] = f"https://{host}/v1/boards/{t}/jobs"
        data, problem = _fetch_json(get, out["api"], host)
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if problem or not isinstance(jobs, list):
            out["problem"] = problem or "unexpected response shape"
            return out
        out["jobs"] = [{"title": clean(j.get("title")), "id": str(j.get("id")),
                        "url": clean(j.get("absolute_url"), 300), "listed": True}
                       for j in jobs if isinstance(j, dict)]
        total = (data.get("meta") or {}).get("total")
        if isinstance(total, int) and total == len(out["jobs"]):
            out["complete"] = True
        else:
            out["problem"] = f"the board states {total} jobs and returned {len(out['jobs'])}"
        return out

    if ats == "lever":
        out["api"] = f"https://{host}/v0/postings/{t}?mode=json"
        for page in range(LEVER_MAX_PAGES):
            url = f"{out['api']}&limit={LEVER_PAGE}&skip={page * LEVER_PAGE}"
            data, problem = _fetch_json(get, url, host)
            if problem or not isinstance(data, list):
                out["problem"] = problem or "unexpected response shape"
                return out
            out["jobs"] += [{"title": clean(j.get("text")), "id": str(j.get("id")),
                             "url": clean(j.get("hostedUrl"), 300), "listed": True}
                            for j in data if isinstance(j, dict)]
            if len(data) < LEVER_PAGE:
                out["complete"] = True
                return out
        out["problem"] = f"stopped after {LEVER_MAX_PAGES * LEVER_PAGE} postings; the list may be longer"
        return out

    if ats == "ashby":
        out["api"] = f"https://{host}/posting-api/job-board/{t}"
        data, problem = _fetch_json(get, out["api"], host)
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if problem or not isinstance(jobs, list):
            out["problem"] = problem or "unexpected response shape"
            return out
        out["jobs"] = [{"title": clean(j.get("title")), "id": str(j.get("id")),
                        "url": clean(j.get("jobUrl"), 300), "listed": j.get("isListed") is not False}
                       for j in jobs if isinstance(j, dict)]
        out["complete"] = True
        return out

    # workable
    out["api"] = f"https://{host}/api/v1/widget/accounts/{t}"
    data, problem = _fetch_json(get, out["api"], host)
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if problem or not isinstance(jobs, list):
        out["problem"] = problem or "unexpected response shape"
        return out
    seen = set()
    for j in jobs:
        code = j.get("shortcode") if isinstance(j, dict) else None
        if not code or code in seen:
            continue
        seen.add(code)
        out["jobs"].append({"title": clean(j.get("title")), "id": str(code),
                            "url": clean(j.get("url") or j.get("application_url"), 300), "listed": True})
    out["complete"] = True
    return out


def _norm(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s)   # hyphen, en and em dashes, minus
    return re.sub(r"\s+", " ", s).strip()


def _words(s):
    return {w for w in re.findall(r"[a-z0-9]+", _norm(s)) if w not in STOP}


def match_role(role, jobs):
    """(matches, near_misses) for a title against a board's listed jobs.

    A match is the same test the screener applies to page text: the role, case-
    and space-insensitive, appears inside a listed title. Near misses share most
    of the role's words and are shown for a human to judge. They are never
    counted as matches.
    """
    r = _norm(role)
    listed = [j for j in jobs if j.get("listed")]
    exact = [j for j in listed if _norm(j["title"]) == r]
    within = [j for j in listed if r and r in _norm(j["title"]) and j not in exact]
    want = _words(role)
    near = []
    if want:
        scored = []
        for j in listed:
            if j in exact or j in within:
                continue
            shared = len(want & _words(j["title"]))
            if shared >= min(2, len(want)) and shared / len(want) >= 0.5:
                scored.append((shared / len(want), j))
        near = [j for _, j in sorted(scored, key=lambda x: -x[0])[:5]]
    return exact + within, near


def read_posting(ats, token, job_id, get):
    """Whether one posting is live on its board, and its title as the board states it.

    live is True, False (the board answers and this posting is not on it), or None
    (we could not tell). False is only returned once the board itself has answered,
    so a mistyped board is never reported as a closed job.
    """
    out = {"ats": ats, "token": token, "job_id": job_id, "title": None, "live": None,
           "api": None, "problem": None}
    tok = _token(token)
    if ats not in SUPPORTED or not tok or not job_id or not JOB_ID_OK[ats].match(job_id):
        out["problem"] = "not a posting this can read"
        return out
    t = urllib.parse.quote(tok, safe="")
    j = urllib.parse.quote(job_id, safe="")
    host = API_HOST[ats]

    if ats == "ashby":
        board = read_board(ats, tok, get)
        out["api"] = board["api"]
        if board["problem"] and not board["jobs"]:
            out["problem"] = board["problem"]
            return out
        hit = next((x for x in board["jobs"] if x["id"].lower() == job_id.lower()), None)
        if hit:
            out["title"], out["live"] = hit["title"], True
        elif board["complete"]:
            out["live"] = False
        return out

    single = {
        "greenhouse": f"https://{host}/v1/boards/{t}/jobs/{j}",
        "lever": f"https://{host}/v0/postings/{t}/{j}",
        "workable": f"https://{host}/api/v2/accounts/{t}/jobs/{j}",
    }[ats]
    out["api"] = single
    data, problem = _fetch_json(get, single, host)
    if isinstance(data, dict):
        out["title"] = clean(data.get("title") or data.get("text")) or None
        state = data.get("state")
        out["live"] = (state == "published") if (ats == "workable" and state) else True
        return out
    if problem and problem.startswith("404"):
        # The posting is missing. Say it is off the board only if the board answers.
        exists = _board_exists(ats, t, get)
        if exists:
            out["live"] = False
        else:
            out["problem"] = ("neither the posting nor the board was found" if exists is False
                              else "the posting was not found and the board could not be checked")
        return out
    out["problem"] = problem or "unexpected response shape"
    return out


def _board_exists(ats, quoted_token, get):
    """True, False (404), or None. A small request, not a full board read."""
    host = API_HOST[ats]
    url = {
        "greenhouse": f"https://{host}/v1/boards/{quoted_token}",
        "lever": f"https://{host}/v0/postings/{quoted_token}?mode=json&limit=1",
        "workable": f"https://{host}/api/v1/widget/accounts/{quoted_token}",
    }.get(ats)
    if not url:
        return None
    data, problem = _fetch_json(get, url, host)
    if data is not None:
        return True
    return False if (problem or "").startswith("404") else None


def account_site(ats, token, get):
    """The website an employer gives on its Workable account, as a bare hostname.

    Workable is self-serve, so this is what the account holder typed, not a
    verified fact. It is used only to decide which site to check, and the screen
    then tests whether that site actually points back at this board.
    """
    tok = _token(token)
    if ats != "workable" or not tok:
        return None
    host = API_HOST[ats]
    data, problem = _fetch_json(get, f"https://{host}/api/v1/accounts/{urllib.parse.quote(tok, safe='')}", host)
    url = data.get("url") if isinstance(data, dict) else None
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    site = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    site = site[4:] if site.startswith("www.") else site
    return site if HOSTNAME_OK.match(site) else None
