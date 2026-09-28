# Security Policy

## Supported Versions

reqcheck hasn't reached 1.0 and has no release branches. Security fixes go into `main` and the next 0.1.x version, so please check the latest `main` before reporting.

| Version               | Supported          |
| --------------------- | ------------------ |
| 0.1.x (latest `main`) | :white_check_mark: |
| Older commits         | :x:                |

Forks and hosted builds belong to whoever runs them, so report problems in their changes or setup to them. If the flaw is in code that came from this repository, report it here as well.

## Reporting a Vulnerability

Report it privately through GitHub: open this repository's **Security** tab and choose **Report a vulnerability**, or go straight to https://github.com/berli0zen/reqcheck/security/advisories/new. You'll need a free GitHub account. Please don't report a vulnerability in a public issue or pull request.

A useful report includes:

- the commit or version you tested, your operating system and your Python version
- steps to reproduce, ideally as an offline example using made-up pages like the tests in `tests/`
- what an attacker could do with it

### What to expect

- **A first reply** within 7 days.
- **A decision** within 14 days: confirmed or declined, with the reason.
- **Updates** at least every 14 days until the report is closed.

**If it's confirmed,** the fix is prepared privately and you're invited to check it before release. It ships in a new 0.1.x version with a GitHub Security Advisory, plus a CVE ID when the issue qualifies for one. You're credited by your GitHub name unless you'd rather not be.

**If it's declined,** you'll get the reason. If it turns out to be an ordinary bug, you'll be asked to open a public issue for it. Once a report is declined, you're free to discuss it publicly.

Please keep the details private until a fix is released or 90 days have passed since your report, whichever comes first. If a fix needs longer, the maintainers will explain why and agree on a new date with you.

There's no bug bounty. Credit in the advisory is what the project can offer.

### Scope

In scope:

- A way around any protection listed under **Security** in the README.
- Getting reqcheck to fetch from a private, loopback or link-local address (server-side request forgery). This matters most for hosted builds.
- Getting reqcheck to read a site listed under **Not read** in the README.
- Getting text from a page or API to run code, be read by `whois` as an option, or reach the terminal as control or formatting characters.
- Getting reqcheck to save records outside its data folder (`~/.reqcheck`, or the folder `REQCHECK_HOME` names inside your home folder), follow a symlink while saving, or save files other users can read.

Out of scope:

- Wrong or incomplete results, such as a listed job reported as absent or a board it can't read. Those are bugs, so please open a public issue. Pages can also misdescribe themselves, and reqcheck reports what they show.
- Flaws in Python or certifi, or in the sites and services reqcheck reads, such as the Greenhouse, Lever, Ashby and Workable APIs. Please report those to their owners.
- Attacks that need control of your computer or account already, such as editing your files, your `PATH` or your Python environment.
- Slow or large responses that stay inside the timeouts and the 8 MB limit.
- Scanner or linter output without a working example.

### Testing

reqcheck runs on your own computer. There's no server behind it and it collects nothing, so all testing can happen locally. When testing a flaw, use machines and domains you own or control rather than other people's sites, and don't run reqcheck at volume against job boards.

Good-faith research that follows this policy is welcome, and the maintainers won't take legal action over it.
