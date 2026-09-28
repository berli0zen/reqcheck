# SPDX-FileCopyrightText: 2026 The reqcheck authors
# SPDX-License-Identifier: AGPL-3.0-or-later OR PolyForm-Noncommercial-1.0.0
"""reqcheck: check a job listing against the employer's own careers page, from
public sources.

    from reqcheck import check_listing
    result = check_listing(url="https://job-boards.greenhouse.io/example/jobs/123")

The result is plain JSON-serialisable data, so any front end can use it: the
command line in reqcheck.cli, a desktop app or a website. Saved checks live only
in the user's own home folder (reqcheck.store).
"""

__version__ = "0.1.0"

from .checks import STATUSES  # noqa: E402
from .listing import check_listing  # noqa: E402

__all__ = ["check_listing", "STATUSES", "__version__"]
