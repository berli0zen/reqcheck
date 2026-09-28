# SPDX-FileCopyrightText: 2026 The reqcheck authors
# SPDX-License-Identifier: AGPL-3.0-or-later OR PolyForm-Noncommercial-1.0.0
"""Offline tests. No network: every employer here is a fake one on acme.example.

    python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reqcheck import boards, checks, fetch, listing, store  # noqa: E402
from reqcheck.cli import main as cli_main  # noqa: E402

UUID = "61eaa54c-e1b7-4064-afad-f7df3d48d652"


def fake_web(pages):
    """A get() that serves `pages` (url -> (status, body)) and 404 for anything else."""
    def get(url, timeout=15):
        status, body = pages.get(url, (404, ""))
        return (body if status == 200 else ""), url, status
    return get


def lands_on(final, status=200, body=""):
    """A get() whose every request ends at `final`, as if a redirect led there."""
    def get(url, timeout=15):
        return body, final, status
    return get


class BoardUrls(unittest.TestCase):
    def test_postings(self):
        f = boards.board_from_url
        self.assertEqual(f("https://job-boards.greenhouse.io/acme/jobs/123"),
                         {"ats": "greenhouse", "token": "acme", "job_id": "123"})
        self.assertEqual(f("https://boards.greenhouse.io/embed/job_app?for=acme&token=123"),
                         {"ats": "greenhouse", "token": "acme", "job_id": "123"})
        self.assertEqual(f(f"https://jobs.lever.co/acme/{UUID}/apply"),
                         {"ats": "lever", "token": "acme", "job_id": UUID})
        self.assertEqual(f(f"https://jobs.ashbyhq.com/Acme/{UUID}"),
                         {"ats": "ashby", "token": "Acme", "job_id": UUID})
        self.assertEqual(f("https://apply.workable.com/acme/j/14A5EE35C3/"),
                         {"ats": "workable", "token": "acme", "job_id": "14A5EE35C3"})
        self.assertEqual(f("https://apply.workable.com/acme/"),
                         {"ats": "workable", "token": "acme", "job_id": None})

    def test_rejects(self):
        for url in ("https://boards.greenhouse.io/../../etc", "https://jobs.lever.co/acme/not-a-uuid",
                    "https://www.linkedin.com/jobs/view/1", "javascript:alert(1)", ""):
            self.assertIsNone(boards.board_from_url(url), url)

    def test_refs_in_a_page(self):
        html = r'''<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>
            <a href="https:\/\/job-boards.greenhouse.io\/beta\/jobs\/5">escaped in page data</a>
            <a href="https://my.greenhouse.io/users/sign_in?job_board=gamma">job alerts</a>
            <a href="https://jobs.lever.co/delta">lever</a> <a href="https://apply.workable.com/epsilon/">workable</a>
            <a href="https://job-boards.eu.greenhouse.io/eucorp">no public API</a>
            <a href="https://www.workable.com/">vendor site</a> <a href="https://jobs.workable.com/search">index</a>'''
        found = sorted((r["ats"], r["token"]) for r in boards.find_refs(html, "page"))
        self.assertEqual(found, [("greenhouse", "acme"), ("greenhouse", "beta"), ("greenhouse", "gamma"),
                                 ("lever", "delta"), ("workable", "epsilon")])

    def test_tokens(self):
        self.assertEqual(boards._token("acme"), "acme")
        for bad in ("embed", "a/../b", "x" * 150, ""):
            self.assertIsNone(boards._token(bad), bad)

    def test_match(self):
        jobs = [{"title": "Associate SOC Analyst (Remote)", "listed": True},
                {"title": "SOC Analyst II", "listed": True},
                {"title": "Chef", "listed": True},
                {"title": "Associate SOC Analyst", "listed": False}]
        matches, near = boards.match_role("associate soc analyst", jobs)
        self.assertEqual([j["title"] for j in matches], ["Associate SOC Analyst (Remote)"])
        self.assertEqual([j["title"] for j in near], ["SOC Analyst II"])

    def test_clean_strips_control_characters(self):
        self.assertEqual(boards.clean("Bad\x1b[31mTitle\u202e\tx"), "Bad[31mTitle x")


class BoardApis(unittest.TestCase):
    def test_greenhouse_complete(self):
        get = fake_web({"https://boards-api.greenhouse.io/v1/boards/acme/jobs": (200, json.dumps(
            {"jobs": [{"title": "A", "id": 1, "absolute_url": "u"}], "meta": {"total": 1}}))})
        board = boards.read_board("greenhouse", "acme", get)
        self.assertTrue(board["complete"])
        self.assertEqual(len(board["jobs"]), 1)

    def test_greenhouse_total_mismatch_is_incomplete(self):
        get = fake_web({"https://boards-api.greenhouse.io/v1/boards/acme/jobs": (200, json.dumps(
            {"jobs": [], "meta": {"total": 3}}))})
        self.assertFalse(boards.read_board("greenhouse", "acme", get)["complete"])

    def test_unknown_board(self):
        board = boards.read_board("lever", "nobody", fake_web({}))
        self.assertFalse(board["complete"])
        self.assertTrue(board["problem"].startswith("404"))

    def test_redirect_off_the_api_host_is_refused(self):
        board = boards.read_board("ashby", "acme", lambda url, timeout=15: ("{}", "https://elsewhere.example/", 200))
        self.assertIn("redirected", board["problem"])

    def test_workable_counts_each_job_once(self):
        body = json.dumps({"jobs": [{"shortcode": "AAAAAA", "title": "T"}, {"shortcode": "AAAAAA", "title": "T"}]})
        board = boards.read_board("workable", "acme", fake_web(
            {"https://apply.workable.com/api/v1/widget/accounts/acme": (200, body)}))
        self.assertEqual(len(board["jobs"]), 1)

    def test_closed_posting_on_a_live_board(self):
        get = fake_web({"https://boards-api.greenhouse.io/v1/boards/acme": (200, json.dumps({"name": "Acme"}))})
        self.assertIs(boards.read_posting("greenhouse", "acme", "9", get)["live"], False)

    def test_missing_board_is_not_a_closed_posting(self):
        posting = boards.read_posting("greenhouse", "nobody", "9", fake_web({}))
        self.assertIsNone(posting["live"])


class Network(unittest.TestCase):
    def test_private_addresses_are_never_fetched(self):
        for url in ("http://127.0.0.1/", "http://localhost/", "http://10.0.0.1/",
                    "http://169.254.169.254/latest/meta-data/", "http://[::1]/"):
            self.assertEqual(fetch.get(url)[2], "refused:private_address", url)

    def test_only_web_addresses(self):
        self.assertEqual(fetch.get("file:///etc/passwd")[2], "invalid:url")
        self.assertEqual(fetch.get("ftp://acme.example/")[2], "invalid:url")

    def test_address_is_checked_on_the_connection_itself(self):
        # A DNS answer that is public for the first lookup and loopback for the
        # next (DNS rebinding) must never reach the loopback address.
        answers = []

        def rebinding(host, port, *args, **kwargs):
            answers.append(host)
            ip = "93.184.215.14" if len(answers) == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]

        with mock.patch("socket.getaddrinfo", rebinding):
            self.assertEqual(fetch.get("https://rebind.example/")[2], "refused:private_address")
        self.assertEqual(len(answers), 2)

    def test_plain_http_is_read_over_https(self):
        sent = []

        class Recorder:
            def open(self, req, timeout):
                sent.append(req.full_url)
                raise urllib.error.URLError(OSError("offline"))

        with mock.patch.object(fetch, "is_public", return_value=True), \
             mock.patch.object(fetch, "_OPENER", Recorder()):
            fetch.get("http://www.acme.example/careers")
            fetch.get("http://www.acme.example:80/jobs?page=2")
        self.assertEqual(sent, ["https://www.acme.example/careers", "https://www.acme.example/jobs?page=2"])

    def test_redirects(self):
        handler = fetch._CheckedRedirects()
        page = urllib.request.Request("https://www.acme.example/jobs")
        onward = handler.redirect_request(page, None, 301, "Moved", {}, "http://jobs.acme.example/")
        self.assertEqual(onward.full_url, "https://jobs.acme.example/")
        for target, refusal in (("http://www.acme.example/jobs", fetch._PlainHTTP),
                                ("https://lnkd.in/abc", fetch._NotRead),
                                ("https://uk.linkedin.com/jobs/view/1", fetch._NotRead),
                                ("ftp://acme.example/", fetch._NotWeb)):
            with self.assertRaises(refusal, msg=target):
                handler.redirect_request(page, None, 302, "Found", {}, target)

    def test_platforms_never_read_under_any_address(self):
        for host in ("www.linkedin.com", "uk.linkedin.com", "lnkd.in", "www.glassdoor.co.uk",
                     "uk.indeed.com", "www.ziprecruiter.co.uk", "builtin.com", "www.dice.com"):
            self.assertTrue(fetch.not_read_reason(host), host)
        for host in ("acme.example", "notlinkedin.example", "linkedin-jobs.example", "dice.example"):
            self.assertIsNone(fetch.not_read_reason(host), host)
        self.assertEqual(fetch.get("https://lnkd.in/abc")[2], "refused:not_read")


class Pages(unittest.TestCase):
    def test_listing_links(self):
        page = ('<a href="/careers/jobs/">j</a><a href="/careers/candidate-info/">c</a>'
                '<a href="/de/careers/">de</a><a href="/jobsite-safety">word</a><a href="/blog/jobs-guide">b</a>'
                '<a href="/careers/open-roles?x=1">r</a><a href="https://other.example/jobs">o</a>'
                '<a href="/research">r</a><a href="/job/\' + x + \'">template</a>')
        found = checks._listing_links(page, "https://www.acme.example/careers/", {"acme.example"})
        self.assertEqual(found, ["https://www.acme.example/careers/jobs/",
                                 "https://www.acme.example/careers/open-roles"])

    def test_job_list_detection(self):
        html = ('<a href="/jobs/123">a</a><a href="/careers/?gh_jid=55">b</a>'
                '<a href="/jobs/?page=2">c</a><a href="/jobs/soc-analyst">d</a>')
        self.assertEqual(len(checks._job_links(html)), 2)

    def test_truncation(self):
        self.assertTrue(checks.detect_truncation("Showing 60 of 5,624 jobs"))
        self.assertIsNone(checks.detect_truncation("Three open roles"))

    def test_ratio(self):
        self.assertEqual(checks.check_ratio(1200, 40)["value"], 30)
        self.assertEqual(checks.check_ratio(None, 40)["status"], "not_supplied")

    def test_title_from_json_ld(self):
        html = ('<script type="application/ld+json">{"@context":"https://schema.org",'
                '"@graph":[{"@type":"JobPosting","title":"Security Analyst"}]}</script>')
        self.assertEqual(listing.title_from_page(html), "Security Analyst")
        self.assertIsNone(listing.title_from_page("<title>Job Application for X at Y</title>"))

    def test_domains(self):
        self.assertEqual(listing.clean_domain("https://www.Acme.example/careers"), "www.acme.example")
        self.assertIsNone(listing.clean_domain("-bad"))
        self.assertIsNone(listing.clean_domain("not a domain"))

    def test_hosts(self):
        self.assertEqual(listing.host_of("https://WWW.Acme.Example:8443/x"), "acme.example")
        self.assertEqual(listing.host_of("https://awww.example/"), "awww.example")
        self.assertEqual(listing.host_of("https://[::1"), "")

    def test_malformed_links_are_skipped(self):
        page = '<a href="http://[careers">bad</a><a href="/careers">good</a>'
        self.assertEqual(checks._harvest(page, "https://acme.example"), ["https://acme.example/careers"])

    def test_a_site_that_only_serves_plain_http(self):
        with mock.patch.object(checks, "get", lambda url, timeout=15: ("", url, "refused:plain_http")):
            careers = checks.check_careers("acme.example", "Security Analyst")
        self.assertEqual((careers["value"], careers["status"]), ("plain_http_only", "unavailable"))


class LinksThatRedirect(unittest.TestCase):
    def test_a_link_that_leads_to_a_platform_never_read(self):
        # Refused by get(), or followed by some other get() that did not refuse it.
        for get in (lands_on("https://www.linkedin.com/jobs/view/1", "refused:not_read"),
                    lands_on("https://www.linkedin.com/jobs/view/1", 200,
                             '<a href="https://www.acme.example/">Acme</a>')):
            with mock.patch.object(listing, "get", get):
                result = listing.check_listing(url="https://short.example/abc")
            self.assertEqual(result["error"]["code"], "not_read")
            self.assertIn("leads to linkedin.com", result["error"]["message"])
            self.assertEqual(result["fields"], {})

    def test_the_site_a_link_lands_on_is_not_the_employer(self):
        page = ('<a href="https://jobs.aggregator.example/">Jobs</a>' * 4
                + '<a href="https://www.acme.example/">Acme</a>')
        with mock.patch.object(listing, "get", lands_on("https://jobs.aggregator.example/p/1", 200, page)):
            found = listing.employer_domain_from_page("https://go.redirect.example/j/1")
        self.assertEqual(found[0], "acme.example")


ACME = {
    "https://acme.example": (200, '<html><body><a href="/careers">Careers</a> Acme makes things.</body></html>'),
    "https://acme.example/careers": (200, '<html><body><h1>Careers at Acme</h1><p>'
                                     + "We hire engineers, analysts and support staff. " * 8
                                     + '</p><script src="https://boards.greenhouse.io/embed/job_board/js?for=acme">'
                                       '</script></body></html>'),
    "https://boards-api.greenhouse.io/v1/boards/acme/jobs": (200, json.dumps(
        {"jobs": [{"title": "Security Analyst", "id": 7, "absolute_url": "https://acme.example/careers?gh_jid=7"}],
         "meta": {"total": 1}})),
}
WHOIS = SimpleNamespace(stdout="Creation Date: 2001-02-03T00:00:00Z\nRegistrar: Example Registrar\n")


class EndToEnd(unittest.TestCase):
    def run_check(self, title):
        with mock.patch.object(checks, "get", fake_web(ACME)), \
             mock.patch.object(listing, "get", fake_web(ACME)), \
             mock.patch.object(checks.subprocess, "run", return_value=WHOIS):
            return listing.check_listing(domain="acme.example", title=title)

    def test_role_on_the_employers_board(self):
        result = self.run_check("Security Analyst")
        careers = result["fields"]["careers_listing"]
        self.assertEqual((careers["value"], careers["status"]), ("present", "measured"))
        self.assertEqual(careers["listing_source"], "board_api")
        self.assertTrue(result["summary"].startswith("Found"))
        self.assertEqual(result["fields"]["domain_age"]["status"], "measured")
        self.assertNotIn("_html", careers)
        json.dumps(result)

    def test_role_not_on_the_employers_board(self):
        careers = self.run_check("Chief Llama Wrangler")["fields"]["careers_listing"]
        self.assertEqual((careers["value"], careers["status"]), ("absent", "measured"))

    def test_platforms_that_are_never_read(self):
        result = listing.check_listing(url="https://www.linkedin.com/jobs/view/1")
        self.assertEqual(result["error"]["code"], "not_read")
        self.assertEqual(result["fields"], {})

    def test_input_errors(self):
        self.assertEqual(listing.check_listing()["error"]["code"], "no_input")
        self.assertEqual(listing.check_listing(domain="-x")["error"]["code"], "invalid_domain")

    def test_see_also_is_only_a_link(self):
        link = listing.see_also("Acme & Co")[0]
        self.assertEqual(link["url"], "https://www.donotghostme.com/companies?search=Acme+%26+Co")


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix=".reqcheck-test-", dir=Path.home()))
        self.env = mock.patch.dict(os.environ, {"REQCHECK_HOME": str(self.tmp / "store")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp)

    def test_saved_privately_and_read_back(self):
        path = store.save({"employer": {"domain": "acme.example"}}, note="my own note")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(store.history("acme")[0]["note"], "my own note")
        self.assertEqual(store.history("nothing like this"), [])

    def test_never_outside_the_home_folder(self):
        with mock.patch.dict(os.environ, {"REQCHECK_HOME": tempfile.gettempdir()}):
            with self.assertRaises(store.StoreError):
                store.root()

    def test_never_inside_the_project(self):
        project = Path(store.__file__).resolve().parent.parent
        with mock.patch.dict(os.environ, {"REQCHECK_HOME": str(project / "data")}):
            with self.assertRaises(store.StoreError):
                store.root()

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "needs O_NOFOLLOW")
    def test_refuses_to_write_through_a_symlink(self):
        folder = store.root()
        folder.mkdir(parents=True)
        elsewhere = self.tmp / "elsewhere.txt"
        elsewhere.write_text("")
        (folder / store.CHECKS_FILE).symlink_to(elsewhere)
        with self.assertRaises(OSError):
            store.save({})
        self.assertEqual(elsewhere.read_text(), "")

    def test_cli_history(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli_main(["history", "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue()), [])

    def test_cli_note_needs_save(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli_main(["--domain", "acme.example", "--note", "x"])


if __name__ == "__main__":
    unittest.main()
