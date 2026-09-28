# SPDX-FileCopyrightText: 2026 The reqcheck authors
# SPDX-License-Identifier: AGPL-3.0-or-later OR PolyForm-Noncommercial-1.0.0
"""The command line front end. Everything it shows comes from check_listing(),
so an app or a website can show the same result its own way."""

import argparse
import json
import sys
import textwrap

from . import __version__, boards, store
from .listing import check_listing

COMMANDS = ("check", "history", "-h", "--help", "--version")


def say(text, indent="  "):
    for line in textwrap.wrap(str(text), 76):
        print(indent + line)


def _board(ats, token):
    return f'the {boards.NAMES.get(ats, ats)} board "{token}"'


def _posting_line(p):
    board = _board(p["ats"], p["board"])
    if p["live"] is True:
        return f"live on {board}"
    if p["live"] is False:
        return f"not on {board} any more. The board answers, and this posting is not on it."
    return f"could not check {board}: {p['problem']}"


def _print(result):
    err = result.get("error")
    if err:
        if result.get("posting"):
            print(f"\n  posting: {_posting_line(result['posting'])}")
        print()
        say(err["message"])
        if err["code"] == "not_read":
            print('\n    python3 -m reqcheck --domain <employer\'s own website> --title "<exact title>"')
            print("        [--followers <n> --employees <n>]   # optional, from the company page")
        print()
        return

    emp, role, fields, posting = result["employer"], result["role"], result["fields"], result["posting"]
    print(f"\nVerifying: {emp.get('company') or emp['domain']}")
    print(f"  domain : {emp['domain']}  ({emp['source']})")
    if role["title"]:
        print(f"  role   : {role['title']}"
              + ("" if role["source"] == "supplied" else f"  (from {role['source']})"))
    elif result["input"]["url"]:
        print("  role   : not found on the posting. Pass --title to run the careers check.")
    if posting:
        print(f"  posting: {_posting_line(posting)}")
    print("=" * 66)

    established, unchecked, unsupplied = [], [], []
    for name, f in fields.items():
        st = f.get("status")
        if st in ("measured", "absent"):
            established.append((name, f))
        elif st in ("blocked", "unavailable", "not_checked"):
            unchecked.append((name, f))
        else:
            unsupplied.append(name)

    print("\nESTABLISHED")
    if not established:
        print("  nothing: every check was refused or not run")
    for name, f in established:
        print(f"  • {name}: {f.get('value')}")
        for e in f.get("evidence", [])[:2]:
            say(e, "      ")
    if unchecked:
        print("\nCOULD NOT BE CHECKED  (says nothing about the employer)")
        for name, f in unchecked:
            print(f"  • {name}: {f.get('value') if f.get('value') is not None else f.get('status')}")
            say(next(iter(f.get("evidence") or []), "no reason recorded"), "      ")
    if unsupplied:
        print("\nNOT SUPPLIED  (needs a value read by hand)")
        for name in unsupplied:
            print(f"  • {name}")

    print("\n" + "=" * 66)
    if posting and posting.get("live") is False:
        say("This posting is no longer on the board it was posted to. The results above are "
            "about the employer, not the posting.")
        print()
    say(result["summary"])
    if posting:
        board = _board(posting["ats"], posting["board"])
        print()
        if result["board_match"] == "same":
            say(f"The link is on {board}, the board the employer's site links to.")
        elif result["board_match"] == "different":
            others = ", ".join(_board(b["ats"], b["board"]) for b in result["employer_boards"])
            say(f"The link is on {board}. The employer's site links to {others}, not to this board.")
        else:
            say(f"Could not confirm that {emp['domain']} links to {board}.")
    for link in result.get("see_also") or []:
        print()
        say(f"Also worth a look, from {link['source']} ({link['about']}), in your browser:")
        print(f"  {link['url']}")
    print("\n  Only `measured` and `absent` describe the employer. No score is produced.")
    print("=" * 66 + "\n")


def _history(a):
    try:
        where = store.root()
        records = store.history(a.text)
    except store.StoreError as e:
        print(f"  {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(records, indent=2, ensure_ascii=False))
        return 0
    if not records:
        print(f"\n  No saved checks in {where}\n")
        return 0
    print(f"\nSaved checks in {where}\n")
    for rec in records:
        r = rec.get("result") or {}
        domain = (r.get("employer") or {}).get("domain") or "?"
        title = (r.get("role") or {}).get("title") or "(no title)"
        cl = (r.get("fields") or {}).get("careers_listing") or {}
        print(f"  {str(rec.get('saved_at', ''))[:16]}  {boards.clean(domain, 60)}  {boards.clean(title, 70)}")
        print(f"      careers_listing: {cl.get('value')} <{cl.get('status')}>")
        if rec.get("note"):
            say("note: " + boards.clean(rec["note"], 300), "      ")
    print()
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS:
        argv.insert(0, "check")

    ap = argparse.ArgumentParser(
        prog="reqcheck",
        description="Check a job listing against the employer's own careers page, from public sources.")
    ap.add_argument("--version", action="version", version=f"reqcheck {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    c = sub.add_parser("check", help="check a listing (the default command)")
    c.add_argument("url", nargs="?", help="posting URL")
    c.add_argument("--domain", help="employer's own domain, not the job board's")
    c.add_argument("--title", help="exact role title as listed")
    c.add_argument("--company", help="employer name, for display")
    c.add_argument("--followers", type=int, help="LinkedIn follower count, read by hand")
    c.add_argument("--employees", type=int, help="LinkedIn employee count, read by hand")
    c.add_argument("--json", action="store_true", help="print the result as JSON")
    c.add_argument("--save", action="store_true",
                   help="keep a copy in your home folder (~/.reqcheck), and nowhere else")
    c.add_argument("--note", help="your own note, kept with --save in your home folder only")

    h = sub.add_parser("history", help="list your saved checks")
    h.add_argument("text", nargs="?", help="only records containing this text")
    h.add_argument("--json", action="store_true", help="print the records as JSON")

    a = ap.parse_args(argv)
    if a.cmd == "history":
        return _history(a)
    if a.cmd != "check":
        ap.print_help()
        return 2
    if a.note and not a.save:
        c.error("--note is kept only with --save")
    if not a.url and not a.domain:
        c.error("give a posting URL, or --domain")

    result = check_listing(url=a.url, domain=a.domain, title=a.title, company=a.company,
                           followers=a.followers, employees=a.employees)
    if a.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print(result)
    if result.get("error"):
        return 2
    if a.save:
        try:
            where = store.save(result, a.note)
        except store.StoreError as e:
            print(f"  Not saved: {e}", file=sys.stderr)
            return 2
        print(f"  Saved to {where}", file=sys.stderr if a.json else sys.stdout)
    return 0
