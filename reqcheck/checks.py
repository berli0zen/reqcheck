# SPDX-FileCopyrightText: 2026 The reqcheck authors
# SPDX-License-Identifier: AGPL-3.0-or-later OR PolyForm-Noncommercial-1.0.0
"""The checks on an employer, from public sources.

Each check returns a plain dict: signal, value, status and evidence, plus any
working detail (the page read, the routes tried, the boards read). Nothing here
prints, stores or scores anything. See reqcheck.listing for one listing end to
end, and the statuses below for how to read a result.
"""

import re
import subprocess
import urllib.parse
from datetime import date, datetime

from . import boards
from .fetch import HOSTNAME_OK, get, not_read_reason, strip_html

CAREER_PATHS = [
    "/careers", "/careers/", "/jobs", "/jobs/", "/join-us", "/join",
    "/company/careers", "/about/careers", "/work-with-us", "/opportunities",
    "/careers/open-positions", "/about/jobs",
]

# Phrases an employer uses when it is not hiring.
NO_OPENINGS = [
    "no current openings", "no open positions", "no openings at this time",
    "no current opportunities", "we have no current", "not currently hiring",
    "no positions available", "check back",
]

# Status recorded with every value.
#   measured      the check ran and produced a value
#   absent        the check ran and the thing is not there
#   blocked       the check ran and was refused
#   unavailable   the method failed (a list loaded by script, an API that could not be read)
#   not_checked   the check ran, but what it read cannot support a conclusion
#   not_supplied  needs input that was not given
#   not_attempted did not run
# Only `measured` and `absent` describe the employer. The rest describe the check.
STATUSES = ("measured", "absent", "blocked", "unavailable", "not_checked",
            "not_supplied", "not_attempted")

# Requests fetch.get() declines to make, by the status it returns, with the value
# and evidence a check reports when nothing else was read. None of them is the
# site refusing, and none says anything about the employer.
NOT_FETCHED = {
    "refused:private_address": (
        "private_address",
        "The domain resolves to a private or internal address. Those are never fetched."),
    "refused:plain_http": (
        "plain_http_only",
        "The site sends requests to plain HTTP, and pages are only read over HTTPS."),
    "refused:not_read": (
        "not_read",
        "The site is on a platform that is never read automatically."),
}

# Applicant tracking systems that render listings in the browser. When a careers
# page is one of these shells, the role list is not in the HTML fetched, and a
# missing role says nothing about the employer.
ATS_MARKERS = [
    "myworkdayjobs", "workday", "greenhouse.io", "boards.greenhouse",
    "lever.co", "jobs.lever", "icims", "taleo", "successfactors", "smartrecruiters",
    "jobvite", "ashbyhq", "breezy.hr", "recruiting.ultipro", "dayforcehcm",
    "paylocity", "bamboohr", "workable.com", "avature",
]

VIRTUAL_OFFICE = ["regus", "wework", "spaces.com", "servcorp", "opus virtual",
                  "intelligent office", "davinci virtual", "virtual office"]

# Automated candidate-evaluation vendors. A name in the page source is a fact.
AI_HIRING_VENDORS = [
    "hirevue", "paradox.ai", "olivia.paradox", "seekout", "hiredscore",
    "eightfold", "phenompeople", "phenom.com", "textio", "pymetrics",
    "modernhire", "harver", "sapia.ai", "xor.ai", "talkpush", "humanly.io",
    "fetcher.ai", "celential", "moonhub", "covey.io", "sniper.ai",
]
# Self-described automated evaluation. Weaker than a vendor name, since marketing
# copy says "AI-powered" about many things, so these are reported separately.
AI_HIRING_PHRASES = [
    "ai-powered screening", "ai screening", "automated screening",
    "automated assessment", "ai-assisted matching", "algorithmic matching",
    "ai interview", "video interview", "asynchronous interview",
    "automated resume review", "ai-assisted review", "record and take notes",
]


CAREER_LINK = re.compile(
    r"career|job|join|hiring|work-with-us|work-for-us|opportunit|employment|vacanc",
    re.I)

# Signs that a list is only part of a longer one, such as "Showing 60 of 5,624".
# A list read one page deep cannot support absence.
TRUNCATION = [
    re.compile(r"showing\s+[\d,]+\s+(?:of|out of)\s+([\d,]+)", re.I),
    re.compile(r"([\d,]{3,})\s+(?:open\s+)?(?:roles|jobs|positions|openings)", re.I),
    re.compile(r"\b(?:next\s+page|load\s+more|show\s+more|view\s+all\s+jobs)\b", re.I),
    re.compile(r"page\s+1\s+of\s+([\d,]+)", re.I),
]


def detect_truncation(text):
    """A human-readable reason if this page is only part of a list, else None."""
    for pat in TRUNCATION:
        m = pat.search(text)
        if m:
            return m.group(0).strip()
    return None


# Hosted applicant tracking systems. A company with no /careers page often has
# its listings on one of these.
ATS_HOSTS = [
    # Hosted boards, keyed on the company slug
    "https://boards.greenhouse.io/{slug}",
    "https://job-boards.greenhouse.io/{slug}",
    "https://jobs.lever.co/{slug}",
    "https://jobs.ashbyhq.com/{slug}",
    "https://{slug}.recruitee.com",
    "https://apply.workable.com/{slug}",
    "https://{slug}.bamboohr.com/careers",
    "https://{slug}.applytojob.com",
    "https://{slug}.breezy.hr",
    "https://{slug}.teamtailor.com",
    "https://jobs.jobvite.com/{slug}",
    "https://careers.smartrecruiters.com/{slug}",
    "https://{slug}.wd1.myworkdayjobs.com",
    "https://{slug}.wd5.myworkdayjobs.com",
    "https://careers-{slug}.icims.com",
    "https://{slug}.applicantpro.com/jobs",
    "https://{slug}.jazz.co",
    "https://{slug}.rippling-ats.com",
    # Subdomains on the company's own domain
    "https://careers.{domain}",
    "https://jobs.{domain}",
    "https://work.{domain}",
    "https://talent.{domain}",
    "https://hiring.{domain}",
    "https://apply.{domain}",
    "https://join.{domain}",
    "https://recruiting.{domain}",
    "https://people.{domain}",
    "https://employment.{domain}",
]

# A page that demands a login or a subscription before it shows listings has
# said nothing about what is behind it.
AUTH_WALL = [
    re.compile(r"\b(sign|log)\s*in\s+to\s+(view|see|continue|access|apply)", re.I),
    re.compile(r"\bcreate\s+(an\s+)?account\s+to\s+(view|see|apply|continue)", re.I),
    re.compile(r"\b(members|subscribers)\s+only\b", re.I),
    re.compile(r"\bsubscribe\s+to\s+(view|see|unlock|continue)", re.I),
    re.compile(r"\bthis\s+content\s+is\s+(locked|restricted|premium)\b", re.I),
    re.compile(r"\bupgrade\s+(your\s+plan\s+)?to\s+(view|see|unlock)", re.I),
    re.compile(r"\byou\s+must\s+be\s+logged\s+in\b", re.I),
    re.compile(r"\bregistration\s+required\b", re.I),
]

LOGIN_URL = re.compile(r"/(login|signin|sign-in|auth|account/login|users/sign_in)\b", re.I)


def detect_auth_wall(text, final_url):
    """A reason string if this page is gated rather than empty, else None."""
    if final_url and LOGIN_URL.search(final_url):
        return f"request was redirected to a login URL ({final_url})"
    for pat in AUTH_WALL:
        m = pat.search(text or "")
        if m:
            return f'page demands authentication: "{m.group(0).strip()}"'
    return None


def _harvest(html, base, pattern=CAREER_LINK):
    """Career-like links on a page, excluding assets and job boards."""
    out = []
    for href in re.findall(r'href="([^"#]+)"', html or ""):
        if not pattern.search(href):
            continue
        if re.search(r"\.(css|js|png|jpe?g|svg|woff2?|ico|xml)$", href, re.I):
            continue
        try:
            url = urllib.parse.urljoin(base + "/", href)
            host = urllib.parse.urlsplit(url).hostname
        except ValueError:
            continue  # a malformed address in the page
        if not_read_reason(host):
            continue
        if url not in out:
            out.append(url)
    return out


def discover_careers_links(base):
    """Try every route to a careers page, and record each one tried.

    Absence of a careers page is only a finding once every route a careers page
    could plausibly live at has been searched. Returns (ordered_candidates,
    ledger); the ledger goes into the evidence so a reader can see what was tried.
    """
    parsed = urllib.parse.urlparse(base)
    domain = parsed.netloc.replace("www.", "")
    slug = domain.split(".")[0]
    found, ledger = [], []

    def add(urls, route, detail):
        new = [u for u in urls if u not in found]
        found.extend(new)
        ledger.append({"route": route, "detail": detail, "new_candidates": len(new)})

    # 1. The site's own homepage links.
    html, final, status = get(base)
    if isinstance(status, int) and status < 400 and html:
        add(_harvest(html, base), "homepage_links",
            f"root returned {status}, {len(html)} bytes")
        # Where the root actually lives. A root that redirects to another domain
        # makes that domain the employer's own site too.
        ledger[-1]["final_url"] = final
    else:
        ledger.append({"route": "homepage_links",
                       "detail": f"root unreadable (status {status})",
                       "new_candidates": 0})

    # 2. robots.txt often names paths, including ones not linked anywhere.
    rhtml, _, rstatus = get(base + "/robots.txt")
    if isinstance(rstatus, int) and rstatus < 400 and rhtml:
        paths = [m for m in re.findall(r"(?:Allow|Disallow|Sitemap):\s*(\S+)", rhtml)
                 if CAREER_LINK.search(m)]
        add([urllib.parse.urljoin(base + "/", p) for p in paths],
            "robots_txt", f"{rstatus}, {len(paths)} career-matching entries")
    else:
        ledger.append({"route": "robots_txt",
                       "detail": f"status {rstatus}", "new_candidates": 0})

    # 3. sitemap.xml, the site's own index of itself.
    sm_urls, sm_detail = [], "not found"
    for sm in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"):
        shtml, _, sstatus = get(base + sm)
        if isinstance(sstatus, int) and sstatus < 400 and shtml:
            locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", shtml)
            # One level of sitemap-index expansion.
            child = [l for l in locs if l.endswith(".xml")][:5]
            for c in child:
                chtml, _, cstatus = get(c)
                if isinstance(cstatus, int) and cstatus < 400 and chtml:
                    locs += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", chtml)
            sm_urls = [l for l in locs if CAREER_LINK.search(l)]
            sm_detail = (f"{sm} {sstatus}, {len(locs)} urls indexed, "
                         f"{len(sm_urls)} career-matching")
            break
    add(sm_urls, "sitemap", sm_detail)

    # 4. Hosted ATS locations.
    ats_hits, ats_rejected = [], 0
    for tpl in ATS_HOSTS:
        url = tpl.format(slug=slug, domain=domain)
        _, final, astatus = get(url)
        if not (isinstance(astatus, int) and astatus < 400):
            continue
        landed = (final or url)
        parts = urllib.parse.urlparse(landed)
        # A 200 is not a hit. Vendors redirect unknown tenants to their own
        # marketing homepage, which answers 200, so the tenant identifier must
        # still be present in the address landed on.
        if slug not in landed.lower() and domain.split(".")[0] not in landed.lower():
            ats_rejected += 1
            continue
        # A bare vendor root with no path is the same failure by another route.
        if parts.path.strip("/") == "" and slug not in parts.netloc.lower():
            ats_rejected += 1
            continue
        ats_hits.append(landed)
    add(ats_hits, "ats_hosts",
        f"{len(ATS_HOSTS)} hosted-ATS locations probed, {len(ats_hits)} belonged to "
        f"this company, {ats_rejected} rejected as vendor redirects")

    # 5. Interior pages often carry a fuller footer than the homepage does.
    for interior in ("/about", "/company", "/contact"):
        ihtml, _, istatus = get(base + interior)
        if isinstance(istatus, int) and istatus < 400 and ihtml:
            add(_harvest(ihtml, base), f"interior{interior}",
                f"{istatus}, {len(ihtml)} bytes")

    return found, ledger


def check_careers(site, role):
    """Does the employer's own careers page list this role?"""
    out = {"signal": "careers_listing", "value": None, "evidence": [], "page": None,
           "status": "not_attempted", "method": None,
           "http": {"blocked": 0, "not_found": 0, "ok": 0}}
    base = site if site.startswith("http") else "https://" + site
    base = base.rstrip("/")

    page_text = None
    page_html = ""
    blocked = 0
    seen_404 = 0
    unresolved = 0
    not_fetched = []

    # Discovered links first, fixed paths only as a fallback.
    discovered, ledger = discover_careers_links(base)
    candidates = discovered + [base + p for p in CAREER_PATHS]
    out["discovered_links"] = discovered
    out["route_ledger"] = ledger
    out["method"] = ("discovery+paths" if discovered else "paths_only")

    for url in candidates:
        html, final, status = get(url)
        if isinstance(status, str) and status.startswith("dns"):
            unresolved += 1
            continue
        if status in NOT_FETCHED:
            not_fetched.append(status)
            continue
        if status in (401, 403, 429) or (isinstance(status, str) and status.startswith("error")):
            blocked += 1
            out["http"]["blocked"] += 1
            continue
        if status == 404:
            seen_404 += 1
            out["http"]["not_found"] += 1
            continue
        text = strip_html(html)
        # A careers page mentions hiring somewhere.
        if len(text) > 200 and re.search(r"career|job|opening|position|hiring|join", text, re.I):
            out["page"] = final
            out["http"]["ok"] += 1
            page_text = text
            page_html = html
            break

    # The raw HTML, for the checks that read the same page. A buffer, not evidence.
    out["_html"] = page_html

    if page_text is None:
        if not_fetched and not seen_404 and not blocked:
            out["value"], note = NOT_FETCHED[max(NOT_FETCHED, key=not_fetched.count)]
            out["status"] = "unavailable"
            out["evidence"].append(note)
            return out
        if unresolved and not seen_404 and not blocked:
            out["value"] = "host_unresolved"
            out["status"] = "unavailable"
            out["evidence"].append(
                f"{unresolved} attempts could not resolve the hostname. The domain does not "
                "exist, or the domain given for this company is wrong.")
            out["evidence"].append("Not a refusal and not an absence. Find the real domain first.")
            return out
        if blocked and not seen_404:
            out["value"] = "unreachable"
            out["status"] = "blocked"
            out["evidence"].append(
                f"All {blocked} attempts refused (403/401/429 or connection error), usually "
                "bot protection, not an absent page.")
            out["evidence"].append(
                "Says nothing about the listing either way. Open the careers page in a browser.")
            return out
        # Absence only counts if the search was exhaustive and the site was
        # readable. If the root could not be read, nothing was searched.
        root_ok = any(l["route"] == "homepage_links" and "unreadable" not in l["detail"]
                      for l in ledger)
        ledger_line = "; ".join(
            f"{l['route']}: {l['detail']} (+{l['new_candidates']})" for l in ledger)

        if not root_ok:
            out["value"] = "unreachable"
            out["status"] = "blocked"
            out["evidence"].append(
                "The site root could not be read, so no route to a careers page "
                "could be searched.")
            out["evidence"].append(f"Routes attempted: {ledger_line}")
            return out

        out["value"] = "no_careers_page"
        out["status"] = "absent"
        out["evidence"].append(
            f"No careers page found under {base} after searching {len(ledger)} "
            f"routes and {len(candidates)} candidate URLs "
            f"({seen_404} returned 404"
            + (f", {blocked} refused" if blocked else "") + ").")
        out["evidence"].append(f"Routes attempted: {ledger_line}")
        return out

    # The site root is not a careers page. The keyword test above accepts any
    # page that mentions careers or jobs, which the homepage of a job board or
    # recruiter always does.
    page_path = urllib.parse.urlparse(out["page"] or "").path.strip("/")
    if not page_path:
        out["value"] = "only_root_matched"
        out["status"] = "not_checked"
        out["evidence"].append(
            f'No careers page was found at a distinct path; only the site root '
            f'({out["page"]}) matched the careers keywords.')
        out["evidence"].append(
            "A homepage that mentions jobs is not a job list. Neither presence nor "
            "absence of any role can be concluded.")
        return out

    # Gated before read. Checked first: a login wall can sit in front of a page
    # that would otherwise read as "no openings" or as a missing role.
    wall = detect_auth_wall(page_text, out["page"])
    if wall:
        out["value"] = "auth_walled"
        out["status"] = "blocked"
        out["evidence"].append(f'Careers page at {out["page"]} is gated: {wall}.')
        out["evidence"].append(
            "Nothing behind the gate was read, so neither presence nor absence of any "
            "role can be concluded.")
        return out

    low = page_text.lower()
    hit = next((n for n in NO_OPENINGS if n in low), None)
    if hit:
        out["value"] = "absent"
        out["status"] = "measured"
        out["evidence"].append(f'Careers page states "{hit}": {out["page"]}')
        if "linkedin" in low:
            out["evidence"].append(
                "The page also links to LinkedIn, so listings may be posted there instead.")
        return out

    if role:
        if re.search(re.escape(role), page_text, re.I):
            out["value"] = "present"
            out["status"] = "measured"
            out["listing_source"] = "html"
            out["evidence"].append(f'Role "{role}" found on {out["page"]}')
            return out
        return _look_past_landing(out, base, role, page_text, page_html, ledger)

    out["value"] = "unchecked"
    out["status"] = "not_supplied"
    out["evidence"].append(
        f'Careers page exists at {out["page"]}; pass a role to check for a specific listing.')
    return out


# ---------------------------------------------------------------------------
# Past the landing page.
#
# A careers landing page is often not the job list. The list can be one level
# down, or loaded from a hosted board the page links to. `absent` comes only
# from a list read in full: a board read completely and linked from the
# employer's own site, or a page that visibly lists jobs.
# ---------------------------------------------------------------------------

# Links from a landing page toward the page that lists jobs. Word-bounded, so a
# word that merely contains "job" does not count.
LISTING_LINK = re.compile(
    r"(?<![a-z])(?:jobs?|openings?|open-roles?|roles|positions?|vacanc(?:y|ies)|"
    r"opportunit(?:y|ies)|search|careers?)(?![a-z])", re.I)
LISTING_STRONG = re.compile(
    r"(?<![a-z])(?:jobs?|openings?|open-roles?|roles|positions?|vacanc(?:y|ies)|search)(?![a-z])",
    re.I)
NOT_LISTING = re.compile(
    r"/(?:blog|news|press|resources|events?|webinars?|podcasts?|team|people|leadership|"
    r"stor(?:y|ies)|customers?|case-stud[\w-]*|legal|privacy|terms|cookies?|benefits|culture|"
    r"life-at[\w-]*|candidate[\w-]*|faqs?)(?:/|$)", re.I)
LOCALE = re.compile(r"^/[a-z]{2}(?:-[a-z]{2,4})?/", re.I)
MAX_FOLLOW = 4

# Links to individual postings. A page carrying several is showing a job list;
# a page carrying none is not, whatever its URL says.
POSTING_HREF = re.compile(
    r"(?:/jobs?/[^/?#\"'\s]*\d"
    r"|[?&]gh_jid=\d+"
    r"|greenhouse\.io/[^/\"'\s]+/jobs/\d+"
    r"|lever\.co/[^/\"'\s]+/[0-9a-f]{8}-[0-9a-f]{4}"
    r"|ashbyhq\.com/[^/\"'\s]+/[0-9a-f]{8}-[0-9a-f]{4}"
    r"|workable\.com/[^/\"'\s]+/j/[0-9a-z]{6,}"
    r"|myworkdayjobs\.com/[^\"'\s]*/job/"
    r"|/(?:careers?|jobs?|positions?|openings?|roles?)/[^?#\"'\s]*[0-9a-f]{8}-[0-9a-f]{4})",
    re.I)
MIN_JOB_LINKS = 3

# Job boards with no API read here. Next to a board that was read, a link to one
# of these means some of the employer's jobs may be listed elsewhere. Matched as
# board addresses, not bare names: a product name in page text is not a board.
UNREAD_ATS = re.compile(
    r"[a-z0-9-]+\.(?:wd\d+\.)?myworkdayjobs\.com|[a-z0-9-]+\.icims\.com|taleo\.net"
    r"|successfactors\.(?:com|eu)|(?:careers|jobs)\.smartrecruiters\.com/\w"
    r"|jobs\.jobvite\.com/\w|[a-z0-9-]+\.breezy\.hr|recruiting\d*\.ultipro\.com"
    r"|dayforcehcm\.com/candidateportal|recruiting\.paylocity\.com"
    r"|[a-z0-9-]+\.bamboohr\.com/(?:careers|jobs)|avature\.net|[a-z0-9-]+\.applytojob\.com"
    r"|[a-z0-9-]+\.recruitee\.com|[a-z0-9-]+\.teamtailor\.com|[a-z0-9-]+\.rippling-ats\.com",
    re.I)


def _site_of(host):
    host = (host or "").lower().split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    return ".".join(host.split(".")[-2:])


def _on_site(url, sites):
    host = urllib.parse.urlparse(url or "").netloc.lower().split(":")[0]
    return any(host == s or host.endswith("." + s) for s in sites)


def _employer_sites(base, ledger):
    """The employer's own site: the domain given, plus wherever its root redirects."""
    sites = {_site_of(urllib.parse.urlparse(base).netloc)}
    for entry in ledger:
        if entry.get("route") == "homepage_links" and entry.get("final_url"):
            sites.add(_site_of(urllib.parse.urlparse(entry["final_url"]).netloc))
    return {s for s in sites if s}


def _listing_links(html, page_url, sites):
    """Up to MAX_FOLLOW links from a landing page that could lead to its job list."""
    here = urllib.parse.urlparse(page_url)
    here_path = here.path.rstrip("/")
    found = []
    for href in re.findall(r'href="([^"#]+)"', boards._unescape(html)):
        # SI-10: an href is page content, and can hold a script template rather
        # than an address. Anything that is not a plain URL is skipped.
        if re.search(r"[\s'\"<>{}|\\^`+]", href):
            continue
        u = urllib.parse.urlparse(urllib.parse.urljoin(page_url, href))
        path = u.path.rstrip("/")
        if u.scheme not in ("http", "https") or not _on_site(u.geturl(), sites):
            continue
        if path == here_path or not LISTING_LINK.search(path):
            continue
        if re.search(r"\.(css|js|png|jpe?g|svg|gif|webp|woff2?|ico|xml|pdf|md|txt|json|rss|atom)$",
                     path, re.I):
            continue
        if NOT_LISTING.search(path) or (LOCALE.match(path + "/") and not LOCALE.match(here.path)):
            continue
        url = urllib.parse.urlunparse((u.scheme, u.netloc, u.path, "", "", ""))
        if url not in found:
            found.append(url)
    found.sort(key=lambda x: (not LISTING_STRONG.search(urllib.parse.urlparse(x).path),
                              not urllib.parse.urlparse(x).path.startswith(here_path + "/"),
                              len(x)))
    return found[:MAX_FOLLOW]


def _job_links(html):
    hrefs = re.findall(r'href="([^"]+)"', boards._unescape(html))
    return {h.split("#")[0] for h in hrefs if POSTING_HREF.search(h)}


def _partial_words(role, text):
    words = re.findall(r"[A-Za-z]{4,}", role)
    return [w for w in words if re.search(rf"\b{re.escape(w)}\b", text, re.I)]


def board_linked_from_site(board, base, sites, discovered):
    """The employer page that links to this hosted board, or None.

    Checked: the homepage, then up to two of the employer's own career pages found
    during discovery. A board reached only by probing ATS hosts for the company's
    name is not shown to be the employer's until one of these links to it.
    """
    mine = (board["ats"], board["token"].lower())
    for url in [base] + [u for u in discovered or [] if _on_site(u, sites)][:2]:
        html, _, status = get(url)
        if isinstance(status, int) and status < 400 and any(
                (r["ats"], r["token"].lower()) == mine for r in boards.find_refs(html, url)):
            return url
    return None


def _look_past_landing(out, base, role, page_text, page_html, ledger):
    """The role is not on the careers landing page. Find the list before concluding."""
    sites = _employer_sites(base, ledger)
    page = out["page"]
    surfaces = [(page, page_html, page_text)]   # every page read that could hold the list
    followed = []

    if _on_site(page, sites):
        for url in _listing_links(page_html, page, sites):
            html, final, status = get(url)
            followed.append(f"{url} ({status})")
            if status in (401, 403, 429) or (isinstance(status, str) and status.startswith("error")):
                out["http"]["blocked"] += 1
                continue
            if status == 404:
                out["http"]["not_found"] += 1
                continue
            if not (isinstance(status, int) and status < 400 and html):
                continue
            out["http"]["ok"] += 1
            text = strip_html(html)
            if re.search(re.escape(role), text, re.I):
                out.update(value="present", status="measured", listing_source="html",
                           listing_page=final, followed=followed, _html=html)
                out["evidence"].append(
                    f'Role "{role}" found on {final}, linked from the careers page {page}')
                return out
            surfaces.append((final, html, text))
    out["followed"] = followed

    # Boards the pages link to. `linked` means a page on the employer's own site
    # links there. A board seen only on the board itself is merely name-matched.
    refs = []

    def add(ref, linked):
        for r in refs:
            if (r["ats"], r["token"].lower()) == (ref["ats"], ref["token"].lower()):
                if linked and not r["linked"]:
                    r.update(linked=True, source=ref["source"])
                return
        refs.append({"ats": ref["ats"], "token": ref["token"], "source": ref["source"],
                     "linked": linked})

    for url, html, _ in surfaces:
        for ref in boards.find_refs(html, url):
            add(ref, _on_site(url, sites))

    # The landing page can itself be a hosted board, reached by a homepage link or
    # by probing ATS hosts for the company's name. Only a link from the employer's
    # own site makes it the employer's.
    own = boards.board_from_url(page)
    if own and not _on_site(page, sites):
        source = board_linked_from_site(own, base, sites, out.get("discovered_links"))
        add({"ats": own["ats"], "token": own["token"], "source": source or page}, bool(source))

    use = [r for r in refs if r["linked"]] or refs
    if len(use) > boards.MAX_BOARDS:
        names = ", ".join(f'{boards.NAMES[r["ats"]]} "{r["token"]}"' for r in use[:6])
        out["value"] = "board_inconclusive"
        out["status"] = "not_checked"
        out["board"] = {"refs": use[:10]}
        out["evidence"].append(
            f"The pages read link to {len(use)} different job boards ({names}). That reads as "
            "a directory of several companies' jobs, not one employer's list, so none was "
            "treated as the employer's.")
        out["evidence"].append("Role absence cannot be concluded.")
        return out
    if use:
        return _decide_from_boards(out, role, use, surfaces)
    return _decide_from_pages(out, role, surfaces, followed)


def _decide_from_boards(out, role, refs, surfaces):
    reads = [boards.read_board(r["ats"], r["token"], get) for r in refs]
    out["board"] = {
        "refs": refs,
        "read": [{"ats": b["ats"], "token": b["token"], "api": b["api"],
                  "jobs_listed": len(b["jobs"]), "complete": b["complete"],
                  "problem": b["problem"]} for b in reads],
    }
    out["listing_source"] = "board_api"

    def label(r):
        return f'{boards.NAMES[r["ats"]]} board "{r["token"]}"'

    def whose(r):
        return (f"linked from {r['source']}, so it is the employer's own" if r["linked"] else
                f"found at {r['source']}, and no page read on the employer's own site links to it")

    hits, near = [], []
    for r, b in zip(refs, reads):
        m, n = boards.match_role(role, b["jobs"])
        hits += [(r, b, j) for j in m]
        near += [j["title"] for j in n]
    hits.sort(key=lambda h: not h[0]["linked"])

    if hits:
        r, b, j = hits[0]
        out["board"]["matched"] = [{"title": jj["title"], "url": jj["url"],
                                    "board": f'{rr["ats"]}:{rr["token"]}'} for rr, _, jj in hits[:5]]
        out["evidence"].append(
            f'Role "{role}" is listed on the {label(r)} as "{j["title"]}" '
            f'({len(b["jobs"])} jobs read through {b["api"]}).')
        out["evidence"].append(f"That board is {whose(r)}.")
        if r["linked"]:
            out["value"] = "present"
            out["status"] = "measured"
        else:
            out["value"] = "board_inconclusive"
            out["status"] = "not_checked"
            out["evidence"].append(
                "A board that shares the company's name is not shown to be the employer's.")
        return out

    lines = [f'{label(r)}: {len(b["jobs"])} jobs, '
             + ("read in full" if b["complete"] else f'not read in full ({b["problem"]})')
             for r, b in zip(refs, reads)]
    near_line = ("Closest listed titles: " + "; ".join(f'"{t}"' for t in near[:5])
                 + ". One may be this role under another name.") if near else None

    if not any(b["jobs"] or b["complete"] for b in reads):
        out["value"] = "board_unreadable"
        out["status"] = "unavailable"
        out["evidence"].append(
            f"The careers page links to {', '.join(label(r) for r in refs)}, and the board's API "
            f"could not be read: {'; '.join(b['problem'] or 'no reason given' for b in reads)}.")
        out["evidence"].append("Role absence cannot be concluded.")
        return out

    # Absence needs every board read in full, each linked from the employer's own
    # pages, and no link on those pages to a board this cannot read.
    other = sorted({m.group(0).lower() for _, html, _ in surfaces
                    for m in UNREAD_ATS.finditer(boards._unescape(html))})
    reasons = []
    if not all(b["complete"] for b in reads):
        reasons.append("not every board was read in full")
    if not all(r["linked"] for r in refs):
        reasons.append("a board was found by the company's name, not through the employer's own site")
    if other:
        reasons.append(f"the pages also link to {', '.join(other[:3])}, which this cannot read")
    if reasons:
        out["value"] = "board_inconclusive"
        out["status"] = "not_checked"
        out["evidence"].append(
            f'Role "{role}" is not on the board(s) read, and that cannot count as absence: '
            + "; ".join(reasons) + ".")
        out["evidence"] += lines
        if near_line:
            out["evidence"].append(near_line)
        return out

    out["value"] = "absent"
    out["status"] = "measured"
    out["evidence"].append(
        f'Role "{role}" is not on the employer\'s own board: searched every listed title on '
        + ", ".join(f'the {label(r)} ({len(b["jobs"])} jobs, read in full through {b["api"]})'
                    for r, b in zip(refs, reads)) + ".")
    out["evidence"].append(
        "Linked from " + ", ".join(sorted({r["source"] for r in refs}))
        + ", so the list read is the employer's own.")
    if near_line:
        out["evidence"].append(near_line)
    return out


def _decide_from_pages(out, role, surfaces, followed):
    """No board to read. Conclude only from a page that visibly lists jobs."""
    landing_url, _, landing_text = surfaces[0]
    all_html = " ".join((h or "") for _, h, _ in surfaces).lower()
    ats = sorted({m for m in ATS_MARKERS if m in all_html})
    # A server-rendered listings page is substantial. A shell is not.
    thin = len(landing_text) < 1500
    where = landing_url + (f" and {len(surfaces) - 1} listing page(s) it links to"
                           if len(surfaces) > 1 else "")

    if ats or thin:
        out["value"] = "js_rendered"
        out["status"] = "unavailable"
        why = f"listings load in the browser via {', '.join(ats[:3])}" if ats \
              else f"page body is only {len(landing_text)} chars, consistent with a shell"
        out["evidence"].append(f"Careers page at {where} could not be read: {why}.")
        out["evidence"].append(
            "Role absence cannot be concluded; the listings were not in the HTML. "
            "Search the careers page in a browser.")
        return out

    for url, _, text in surfaces:
        trunc = detect_truncation(text)
        if trunc:
            out["value"] = "list_truncated"
            out["status"] = "not_checked"
            out["evidence"].append(f'Careers page at {url} shows only part of its list: "{trunc}".')
            out["evidence"].append(
                f'Role "{role}" was not on the part fetched, which says nothing about '
                "whether it is in the full list.")
            return out

    shown = [(u, n) for u, n in ((u, len(_job_links(h))) for u, h, _ in surfaces)
             if n >= MIN_JOB_LINKS]
    partial = _partial_words(role, " ".join(t for _, _, t in surfaces))
    if not shown:
        out["value"] = "no_job_list"
        out["status"] = "not_checked"
        out["evidence"].append(f'Role "{role}" not found on {where}.')
        out["evidence"].append(
            f"None of those pages shows a job list (fewer than {MIN_JOB_LINKS} links to individual "
            "postings), so the list is somewhere this did not reach, or loads by script. "
            "Role absence cannot be concluded.")
        if followed:
            out["evidence"].append("Listing pages followed: " + ", ".join(followed))
        return out

    out["value"] = "absent"
    out["status"] = "measured"
    out["listing_source"] = "html"
    out["evidence"].append(
        f'Role "{role}" not found on {", ".join(u for u, _ in shown)} '
        f"({sum(n for _, n in shown)} links to individual postings read).")
    out["evidence"].append(
        "Those pages list jobs and show no sign of pagination, so the list read is treated "
        "as complete.")
    if partial:
        out["evidence"].append(
            f"Partial word matches present ({', '.join(partial)}); check the page by hand.")
    return out


def check_automated_evaluation(careers_result):
    """Named automated-evaluation vendors, or self-described automated screening,
    in the careers page source.

    "None found" needs a page that is the hiring surface. A page that loads its
    listings from elsewhere is a shell, and markers would sit in the pages that
    load them, which were not read.
    """
    out = {"signal": "automated_evaluation", "value": None, "evidence": [],
           "status": "not_attempted"}
    page_html = careers_result.get("_html") or ""

    if careers_result.get("status") == "blocked":
        out["status"] = "blocked"
        out["evidence"].append(
            "The careers page refused automated requests, so its source could not be searched.")
        return out
    if not page_html:
        out["status"] = "not_checked"
        out["evidence"].append("No careers page was read, so there was nothing to search.")
        return out

    low = page_html.lower()
    vendors = sorted({v for v in AI_HIRING_VENDORS if v in low})
    phrases = sorted({p for p in AI_HIRING_PHRASES if p in low})
    if vendors or phrases:
        out["status"] = "measured"
        out["value"] = "vendor_named" if vendors else "self_described"
        if vendors:
            out["evidence"].append(
                f"Automated-evaluation vendor(s) named in the page source: {', '.join(vendors)}.")
        if phrases:
            out["evidence"].append(
                f"Self-described automated evaluation: {', '.join(phrases[:4])}. Weaker than a "
                "vendor name.")
        return out

    shell = (careers_result.get("status") in ("unavailable", "not_checked")
             or careers_result.get("listing_source") == "board_api"
             or (careers_result.get("value") == "unchecked"
                 and (any(m in low for m in ATS_MARKERS) or boards.find_refs(page_html, ""))))
    if shell:
        out["status"] = "not_checked"
        out["value"] = "page_is_a_shell"
        out["evidence"].append(
            "The careers page loads its listings from elsewhere, so the HTML read is not the "
            "hiring surface. Markers would sit in the pages that were not read.")
        return out

    out["status"] = "absent"
    out["value"] = "none_found"
    out["evidence"].append(
        "No named vendor and no self-described automated screening in the careers page "
        "source. Scope is the page read; the application flow was not exercised.")
    return out


def check_bot_protection(careers_result):
    """How many requests to the employer's site were refused."""
    out = {"signal": "bot_protection", "value": None, "status": "not_attempted", "evidence": []}
    h = careers_result.get("http", {})
    b, nf, ok = h.get("blocked", 0), h.get("not_found", 0), h.get("ok", 0)
    total = b + nf + ok
    if not total:
        out["evidence"].append("no requests made")
        return out
    out["value"] = "blocks_automated_access" if b and not ok else ("permits" if ok or nf else "unknown")
    out["status"] = "measured"
    out["evidence"].append(f"of {total} requests: {b} refused, {nf} returned 404, {ok} served")
    return out


def check_domain_age(site):
    out = {"signal": "domain_age", "value": None, "status": "not_attempted", "evidence": []}
    host = re.sub(r"^https?://", "", site).split("/")[0].split(":")[0]
    host = ".".join(host.split(".")[-2:])
    if not HOSTNAME_OK.match(host):
        out["status"] = "not_checked"
        out["evidence"].append(
            f"{host!r} is not a valid hostname, so it was not passed to an external command.")
        return out
    try:
        raw = subprocess.run(["whois", host], capture_output=True, text=True, timeout=25).stdout
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        out["status"] = "unavailable"
        out["evidence"].append("whois binary unavailable")
        return out
    m = re.search(r"Creation Date:\s*(\d{4}-\d{2}-\d{2})", raw)
    if not m:
        out["status"] = "unavailable"
        out["evidence"].append("whois responded without a creation date; the registry does not publish it")
        return out
    created = m.group(1)
    age = (date.today() - datetime.strptime(created, "%Y-%m-%d").date()).days
    out["value"] = age
    out["status"] = "measured"
    out["evidence"].append(f"registered {created} ({age} days old)")
    reg = re.search(r"Registrar:\s*(.+)", raw)
    if reg:
        out["evidence"].append(f"registrar: {reg.group(1).strip()}")
    return out


def check_contact(site):
    """Contact details published on the site: phone area codes, emails, and
    virtual-office provider names."""
    out = {"signal": "contact_details", "value": None, "status": "not_attempted", "evidence": []}
    base = site if site.startswith("http") else "https://" + site
    text = ""
    raw = ""
    blocked = False
    not_fetched = []
    searched = []
    # Links found on the homepage first, then fixed paths. Contact details often
    # sit one level deeper than any fixed path, such as /legal/privacy.
    CONTACTISH = re.compile(r"contact|legal|privacy|terms|imprint|about|support|help", re.I)
    discovered = []
    root_html, _, root_status = get(base.rstrip("/"))
    if isinstance(root_status, int) and root_status < 400 and root_html:
        discovered = _harvest(root_html, base.rstrip("/"), CONTACTISH)[:12]

    fixed = [base.rstrip("/") + p for p in
             ["/contact", "/contact-us", "/about", "/company", "/imprint", "/legal", ""]]
    for url in discovered + fixed:
        html, _, status = get(url)
        if isinstance(status, int) and status == 200:
            text += " " + strip_html(html)
            raw += " " + (html or "")
            searched.append(f"{url.replace(base.rstrip(chr(47)), '') or '/'} ({status})")
        elif status in (401, 403, 429):
            blocked = True
            searched.append(f"{url.replace(base.rstrip(chr(47)), '') or '/'} (refused {status})")
        elif status in NOT_FETCHED:
            not_fetched.append(status)
            searched.append(f"{url.replace(base.rstrip(chr(47)), '') or '/'} "
                            f"(not fetched: {NOT_FETCHED[status][0].replace('_', ' ')})")
        else:
            searched.append(f"{url.replace(base.rstrip(chr(47)), '') or '/'} ({status})")
    if not text.strip() and not_fetched and not blocked:
        out["value"], note = NOT_FETCHED[max(NOT_FETCHED, key=not_fetched.count)]
        out["status"] = "unavailable"
        out["evidence"].append(note)
        return out
    if not text.strip():
        out["value"] = "unreachable"
        out["status"] = "blocked" if blocked else "unavailable"
        out["evidence"].append("site refused automated requests (403/401); check by hand" if blocked
                               else "could not reach the site")
        return out
    phones = sorted(set(re.findall(r"\b(?:\+?1[-. ]?)?\(?(\d{3})\)?[-. ]?\d{3}[-. ]?\d{4}\b", text)))[:5]
    if phones:
        out["status"] = "measured"
        out["evidence"].append(
            f"phone area codes found: {', '.join(phones)}; compare against the stated city")
    vo = [v for v in VIRTUAL_OFFICE if v in text.lower()]
    if vo:
        out["value"] = "virtual_office_terms"
        out["status"] = "measured"
        out["evidence"].append(f"virtual-office provider names on the site: {', '.join(vo)}")
    host = re.sub(r"^https?://(www\.)?", "", base).rstrip("/").split("/")[0]
    emails = sorted(set(re.findall(r"[\w.+-]+@[\w.-]+\.\w{2,}", raw)))
    own_domain = [e for e in emails if host.split(":")[0] in e.lower()]
    if emails:
        out["status"] = "measured"
        shown = own_domain or emails
        out["evidence"].append(
            f"contact email(s) published: {', '.join(shown[:4])}"
            + ("" if own_domain else "; none on the company's own domain"))

    out["evidence"].append("Paths searched: " + ", ".join(searched))

    if not phones and not vo and not emails:
        # Named after what was tested: no phone and no email on the paths listed,
        # not that the company publishes no contact details anywhere.
        out["value"] = "no_phone_or_email_found"
        out["status"] = "absent"
        out["evidence"].append(
            "Read successfully; no phone number and no email address on any path searched. "
            "Scope is those paths only.")
    elif not phones:
        out["value"] = "email_only_no_phone"
        out["status"] = "measured"
    return out


def check_ratio(followers, employees):
    out = {"signal": "follower_employee_ratio", "value": None, "status": "not_attempted", "evidence": []}
    if followers is None or employees is None:
        out["status"] = "not_supplied"
        out["evidence"].append("pass --followers and --employees, read from the company's LinkedIn page")
        return out
    if employees <= 0:
        return out
    r = followers / employees
    out["value"] = round(r)
    out["status"] = "measured"
    out["evidence"].append(f"{followers:,} followers / {employees:,} employees = {r:,.0f}:1")
    return out
