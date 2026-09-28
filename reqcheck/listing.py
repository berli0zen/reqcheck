# SPDX-FileCopyrightText: 2026 The reqcheck authors
# SPDX-License-Identifier: AGPL-3.0-or-later OR PolyForm-Noncommercial-1.0.0
"""One job listing, end to end.

    from reqcheck import check_listing
    result = check_listing(url="https://job-boards.greenhouse.io/example/jobs/123")

The result is plain JSON-serialisable data for any front end: the command line,
a desktop app or a website. Nothing is printed or stored here.
"""

import json
import re
import urllib.parse
from datetime import datetime

from . import __version__, boards, checks
from .fetch import HOSTNAME_OK, get, not_read_reason

# Hosts that belong to a job board or ATS vendor, never to the employer. A
# posting page's only outside link can be the vendor's own "powered by" link.
BOARD_HOSTS = re.compile(
    r"greenhouse\.io|greenhouse\.com|grnh\.se|lever\.co|ashbyhq|workable|recruitee|bamboohr|jobvite|"
    r"smartrecruiters|myworkdayjobs|icims|breezy\.hr|teamtailor|jazz\.co|"
    r"linkedin\.com|indeed\.com|glassdoor|ziprecruiter|remoteok|weworkremotely",
    re.I)


def host_of(url):
    """An address's hostname, lowercased and without a leading "www.", or ""."""
    try:
        host = urllib.parse.urlsplit(url or "").hostname or ""
    except ValueError:
        return ""
    return host.removeprefix("www.")


def clean_domain(value):
    """A bare hostname from what a user typed, or None if it is not one."""
    d = (value or "").strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0].split(":")[0].rstrip(".")
    return d if HOSTNAME_OK.match(d) else None


class _LinkNotRead(Exception):
    """The posting link leads to a platform that is never read. The argument is
    the host it leads to."""


def employer_domain_from_page(url):
    """The employer's domain from a readable posting page: the most frequent
    outside host it links to, excluding job boards and the page's own site.
    Only a valid hostname counts. The page's author writes its links, and a
    link's host can carry control or format characters.

    Returns (domain, how, html) or (None, reason, html). An unknown domain
    returns None. A wrong one would produce results about a company that was
    never examined. Raises _LinkNotRead if the link leads to a platform that is
    never read.
    """
    html, final, status = get(url)
    landed = host_of(final)
    if status == "refused:not_read" or not_read_reason(landed):
        raise _LinkNotRead(landed)
    if not isinstance(status, int) or status >= 400 or not html:
        return None, f"posting page returned {status}", None

    # A link can redirect, so the page's own site is where it landed as well as
    # the host the link named.
    own = {host_of(url), landed}
    candidates = []
    for href in re.findall(r'href="(https?://[^"]+)"', html):
        h = host_of(href)
        if not HOSTNAME_OK.match(h) or h in own or BOARD_HOSTS.search(h) or not_read_reason(h):
            continue
        if re.search(r"(google|facebook|twitter|x\.com|youtube|instagram|"
                     r"cloudflare|gstatic|jquery|cdn)", h, re.I):
            continue
        candidates.append(h)
    if candidates:
        best = max(set(candidates), key=candidates.count)
        return best, f"most frequent outside host on the posting page ({candidates.count(best)}x)", html
    return None, "no outside host other than job boards found on the page", html


def title_from_page(html):
    """The posting's title from the page's own JSON-LD JobPosting block, or None.

    The page's <title> tag is not used: it wraps the role in board and company
    names, and trimming them would be guessing.
    """
    for block in re.findall(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>",
                            html or "", re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        items = data if isinstance(data, list) else [data]
        for d in list(items):
            if isinstance(d, dict) and isinstance(d.get("@graph"), list):
                items += d["@graph"]
        for d in items:
            if not isinstance(d, dict):
                continue
            kind = d.get("@type")
            if (kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind)) \
                    and isinstance(d.get("title"), str):
                return boards.clean(d["title"]) or None
    return None


def employer_boards(careers, domain):
    """The hosted boards the employer's own pages link to, as (ats, token) pairs."""
    refs = (careers.get("board") or {}).get("refs")
    if refs is not None:
        return {(r["ats"], r["token"].lower()) for r in refs if r.get("linked")}
    # The role was found in page HTML, so no board was read. The page can still
    # show which board it links to, or be a hosted board the employer links to.
    page = careers.get("listing_page") or careers.get("page")
    if not page:
        return set()
    base = "https://" + domain
    sites = checks._employer_sites(base, careers.get("route_ledger") or [])
    if checks._on_site(page, sites):
        return {(r["ats"], r["token"].lower()) for r in boards.find_refs(careers.get("_html"), page)}
    own = boards.board_from_url(page)
    if own and checks.board_linked_from_site(own, base, sites, careers.get("discovered_links")):
        return {(own["ats"], own["token"].lower())}
    return set()


def summary(careers):
    """One neutral sentence on the careers listing, the field the others support."""
    v, s = careers.get("value"), careers.get("status")
    if v == "no_careers_page" and s == "absent":
        return "No careers page was found on the employer's site after every route was searched."
    if v == "absent" and s in ("absent", "measured"):
        return ("Not found: the role is not on the employer's own careers page, and the list "
                "read was complete.")
    if v == "present" and s == "measured":
        if careers.get("listing_source") == "board_api":
            return "Found: the role is on the employer's own job board, which their site links to."
        return "Found: the role is on the employer's own careers page."
    return (f"Not checked: careers_listing came back {v} <{s}>. Look for the role on the "
            "employer's careers page yourself.")


def see_also(name):
    """Other public tools that hold what reqcheck cannot measure, as links for the
    user to open. reqcheck sends them nothing.

    do-not-ghost-me (donotghostme.com, AGPL-3.0) collects anonymous reports from
    applicants who were ghosted. Its public API refuses automated clients, so the
    link goes to its company search, which opens in the user's own browser.
    """
    return [{
        "source": "do-not-ghost-me",
        "about": "anonymous reports from applicants who were ghosted",
        "url": "https://www.donotghostme.com/companies?" + urllib.parse.urlencode({"search": name}),
    }]


def _not_read(subject, reason):
    """The error for a platform that is never read."""
    parts = [f"{subject} not read automatically.", f"{reason}." if reason else "",
             "Pass the employer's domain and the title by hand."]
    return {"code": "not_read", "message": " ".join(p for p in parts if p)}


def check_listing(url=None, domain=None, title=None, company=None, followers=None, employees=None):
    """Check one listing. Give a posting URL, or the employer's domain and a title.

    Returns a dict. On a problem with the input, `error` holds {code, message}
    and no checks were run: "not_read" (a platform that is never read, named by
    the link or reached through it), "no_domain" (the employer's domain could
    not be identified), "invalid_domain" or "no_input".
    """
    result = {
        "reqcheck_version": __version__,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "input": {"url": url, "domain": domain, "title": title, "company": company,
                  "followers": followers, "employees": employees},
        "employer": None, "role": None, "posting": None, "fields": {},
        "board_match": None, "employer_boards": None, "summary": None, "see_also": [],
        "error": None,
    }
    if domain is not None:
        domain = clean_domain(domain)
        if not domain:
            result["error"] = {"code": "invalid_domain",
                               "message": f"{result['input']['domain']!r} is not a domain name."}
            return result
    title = boards.clean(title, 200) or None if title else None
    followers = followers if isinstance(followers, int) and followers >= 0 else None
    employees = employees if isinstance(employees, int) and employees >= 0 else None
    how, title_from = "supplied", "supplied"
    posted_on = posting = None

    if url:
        h = host_of(url)
        blocked_reason = not_read_reason(h)
        if blocked_reason and not domain:
            result["error"] = _not_read(f"{boards.clean(h, 253)} is", blocked_reason)
            return result
        if not blocked_reason:
            # A posting on a hosted board: the board's own API gives its title and state.
            posted_on = boards.board_from_url(url)
            if posted_on and posted_on.get("job_id"):
                posting = boards.read_posting(posted_on["ats"], posted_on["token"],
                                              posted_on["job_id"], get)
                if not title and posting.get("title"):
                    title = posting["title"]
                    title_from = f"the {boards.NAMES[posted_on['ats']]} board's API"
            if not domain and posted_on and posted_on["ats"] == "workable":
                site = boards.account_site("workable", posted_on["token"], get)
                if site:
                    domain, how = site, "the website given on the employer's Workable account"
            if not domain or not title:
                try:
                    found, found_how, page_html = employer_domain_from_page(url)
                except _LinkNotRead as e:
                    landed = e.args[0]
                    if not domain:
                        result["error"] = _not_read(
                            f"The posting link leads to {boards.clean(landed, 253)}, which is",
                            not_read_reason(landed))
                        return result
                    found = found_how = page_html = None
                if not domain:
                    domain, how = found, found_how
                if not title:
                    t = title_from_page(page_html)
                    if t:
                        title, title_from = t, "the posting page's JSON-LD data"

    if posting:
        result["posting"] = {"ats": posted_on["ats"], "board": posted_on["token"],
                             "job_id": posted_on["job_id"], "live": posting["live"],
                             "api": posting["api"], "problem": posting["problem"]}
    if not domain:
        result["error"] = (
            {"code": "no_domain",
             "message": f"Could not identify the employer's own domain: {how}. Pass it with --domain."}
            if url else {"code": "no_input", "message": "Give a posting URL, or the employer's domain."})
        return result

    result["employer"] = {"domain": domain, "source": how, "company": company}
    result["see_also"] = see_also(company or domain.split(".")[0])
    result["role"] = {"title": title, "source": title_from if title else None}

    careers = checks.check_careers(domain, title)
    fields = [
        careers,
        checks.check_bot_protection(careers),
        checks.check_automated_evaluation(careers),
        checks.check_domain_age(domain),
        checks.check_contact(domain),
        checks.check_ratio(followers, employees),
    ]
    if posted_on:
        theirs = employer_boards(careers, domain)
        mine = (posted_on["ats"], posted_on["token"].lower())
        result["board_match"] = "same" if mine in theirs else ("different" if theirs else "unconfirmed")
        result["employer_boards"] = [{"ats": a, "board": t} for a, t in sorted(theirs)]
    result["fields"] = {f["signal"]: {k: v for k, v in f.items() if k not in ("signal", "_html")}
                        for f in fields}
    result["summary"] = summary(careers)
    return result
