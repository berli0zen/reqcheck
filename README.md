# reqcheck

Checks whether a job listing appears on the employer's own careers page, using public sources.

It's a command-line tool and a library. Every result is plain data, so the same checks can sit
behind a desktop app, a website or anything else.

## Requirements

Python 3.9 or later, standard library only. `certifi` is used when installed. The domain-age check
calls `whois`.

## Command line

```bash
# a posting on Greenhouse, Lever, Ashby or Workable
python3 -m reqcheck https://job-boards.greenhouse.io/example/jobs/123

# any other posting: give the employer's domain and the exact title
python3 -m reqcheck --domain example.com --title "Security Analyst"

# add counts read by hand from the company's LinkedIn page
python3 -m reqcheck --domain example.com --title "Security Analyst" --followers 1200 --employees 40

# the full result as JSON
python3 -m reqcheck https://job-boards.greenhouse.io/example/jobs/123 --json

# keep a copy, with your own note, in your home folder
python3 -m reqcheck https://job-boards.greenhouse.io/example/jobs/123 --save --note "applied"
python3 -m reqcheck history
```

`pip install .` adds a `reqcheck` command that does the same.

## Fields

| field | what it reports |
|---|---|
| `posting` | For Greenhouse, Lever, Ashby and Workable links: whether the posting is still on its board and its title, from the board's public API, and whether that board is the one the employer's site links to |
| `careers_listing` | Whether the title is on the employer's own careers page. Follows the landing page one level to its job list, and reads any Greenhouse, Lever, Ashby or Workable board the site links to |
| `bot_protection` | How many requests to the employer's site were refused |
| `automated_evaluation` | Automated-evaluation vendors named in the careers page source, or self-described automated screening |
| `domain_age` | Registration date, from `whois` |
| `contact_details` | Phone area codes, email addresses and virtual-office provider names published on the site |
| `follower_employee_ratio` | Computed from the counts you supply |

## Statuses

| status | meaning |
|---|---|
| `measured` | the check ran and produced a value |
| `absent` | the check ran and the thing is not there |
| `blocked` | the site refused the request |
| `unavailable` | the method failed, such as a list that loads by script or an API that could not be read |
| `not_checked` | the check ran, but what it read cannot support a conclusion |
| `not_supplied` | needs a value that was not given |

Only `measured` and `absent` describe the employer. The others describe what the tool could not see.

A role is reported absent only when the list read was complete: a hosted board read in full and
linked from the employer's own site, or a page that shows a job list with no sign of pagination.
No score is produced.

## What you save stays in your home folder

Nothing is kept unless you pass `--save`. Saved checks, and any note you add, go to
`~/.reqcheck/checks.jsonl`, readable only by you. `REQCHECK_HOME` can move that folder, but only to
somewhere inside your home folder. reqcheck never writes inside the project and never sends your
checks anywhere.

## Building on it

```python
from reqcheck import check_listing

result = check_listing(url="https://job-boards.greenhouse.io/example/jobs/123")
print(result["summary"])
print(result["fields"]["careers_listing"]["status"])
```

`check_listing()` returns the same data `--json` prints. Nothing in the core prints, stores or
scores anything, so it can go behind any front end, split into services, or be ported to another
language. Whatever form a build takes, keep these:

- **Users' records stay on their own devices.** An app writes to the user's home folder. A website
  keeps history in the visitor's browser. A server keeps nothing about its users or what they
  checked.
- **Keep the private-address block.** It stops a hosted build from being used to reach the network
  it runs on. A hosted build should also rate-limit and block internal address ranges at the
  network level, as a second layer.
- **Keep `measured` and `absent` apart from everything else,** and add no score.
- **Don't automate the platforms under Not read.**

The tests run offline against a made-up employer:

```bash
python3 -m unittest discover -s tests
```

## Not read

LinkedIn, Indeed, Glassdoor, ZipRecruiter, Built In and Dice, including their country sites,
LinkedIn's lnkd.in links, and any link that redirects to them. Automated access breaches their
terms or is blocked. For listings there, pass the domain and title by hand.

## Security

- HTTPS only, with certificate verification. A plain `http://` address is read over HTTPS instead,
  and a site that only serves plain HTTP is reported as unavailable.
- Nothing is fetched from a private, loopback or link-local address. The check runs on each
  connection, against the DNS lookup that connection uses, so redirects and DNS answers that change
  between lookups can't get around it. Proxy settings are ignored for the same reason.
- Responses are read up to 8 MB. Anything larger is reported as unreadable.
- Board names and posting IDs taken from pages are validated and escaped before use, and the API
  hosts are fixed.
- Text from remote sources has control and format characters removed before it is printed.
- `whois` receives a validated hostname and is never run through a shell.
- Saved checks are owner-only files inside the home folder, never written through a symlink.

## Credits

- [do-not-ghost-me](https://github.com/necdetsanli/do-not-ghost-me) (AGPL-3.0) collects anonymous
  reports from applicants who were ghosted, at donotghostme.com. Every reqcheck result links to its
  company search, which you open in your own browser. Its public API refuses automated clients, so
  reqcheck doesn't call it.
- [didtheyghostme](https://github.com/didtheyghostme/didtheyghostme) (MIT) tracks what happened
  after people applied, the side of the question reqcheck doesn't cover.

No code from either project is included.

## License

reqcheck is available under either of two licenses. Pick the one that fits what you're building:

- **[AGPL-3.0-or-later](LICENSE-AGPL)** for any use, commercial included. If you share a build, or
  let others use a modified build over a network, you must offer its full source under the same
  license.
- **[PolyForm Noncommercial 1.0.0](LICENSE-POLYFORM-NC)** for noncommercial use. Your build can stay
  private, but it can't be used to make money.

Together they mean no build of reqcheck goes private for profit.

Required Notice: Copyright (c) 2026 The reqcheck authors
