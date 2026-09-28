# SPDX-FileCopyrightText: 2026 The reqcheck authors
# SPDX-License-Identifier: AGPL-3.0-or-later OR PolyForm-Noncommercial-1.0.0
"""Saved checks, kept only in the user's own home folder.

Anything a person keeps from this tool, including their own notes about an
employer, is theirs alone. It is written under the home folder of whoever runs
it (~/.reqcheck by default), readable only by them, and never inside the
project and never to a server. A website built on this code keeps its users'
history on their own devices for the same reason.

REQCHECK_HOME can move the folder, but only to somewhere inside the home folder.
"""

import json
import os
from datetime import datetime
from pathlib import Path

DEFAULT_DIR = ".reqcheck"
CHECKS_FILE = "checks.jsonl"
SCHEMA = 1


class StoreError(Exception):
    pass


def root():
    """The folder saved checks live in. Raises StoreError if it would sit outside
    the home folder or inside the project."""
    home = Path.home().resolve()
    raw = os.environ.get("REQCHECK_HOME")
    path = Path(raw).expanduser().resolve() if raw else home / DEFAULT_DIR
    if path == home or home not in path.parents:
        raise StoreError(f"{path} is not a folder inside your home folder ({home}); "
                         "saved checks are only kept there.")
    project = Path(__file__).resolve().parent.parent
    if path == project or project in path.parents:
        raise StoreError(f"{path} is inside the reqcheck project; saved checks never go there.")
    return path


def _open_private(path):
    """Open for append, owner-only, refusing to follow a symlink."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    if hasattr(os, "fchmod"):
        os.fchmod(fd, 0o600)
    return fd


def save(result, note=None):
    """Append one check, and the user's note if any, to their own history.
    Returns the file written."""
    folder = root()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if hasattr(os, "chmod"):
        os.chmod(folder, 0o700)
    record = {
        "schema": SCHEMA,
        "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "note": note,
        "result": result,
    }
    target = folder / CHECKS_FILE
    fd = _open_private(target)
    try:
        os.write(fd, (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return target


def history(text=None):
    """Saved checks, oldest first. With `text`, only records that contain it."""
    target = root() / CHECKS_FILE
    if not target.exists():
        return []
    out = []
    with open(target, encoding="utf-8") as f:
        for line in f:
            if text and text.lower() not in line.lower():
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out
