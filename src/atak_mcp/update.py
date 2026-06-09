# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Self-version reporting and update checks for atak-mcp itself.

The MCP server / CLI is launched fresh from git by ``uvx`` each session, so it
cannot replace its own running code. What it *can* do is tell the caller whether
a newer release exists, so an agent or a human knows when to refresh. Applying
the update is an ``uvx`` concern (re-pin the tag, or ``--refresh`` a branch);
see the README "Updating atak-mcp" section.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from . import __version__

__all__ = ["current_version", "latest_version", "check_update", "REPO"]

# GitHub repo to check for newer release tags.
REPO = "newtypeind/atak-mcp"
_TAGS_URL = f"https://api.github.com/repos/{REPO}/tags"


def current_version() -> str:
    """The installed atak-mcp version."""
    return __version__


def _key(version: str) -> tuple[int, int, int]:
    """A sortable (major, minor, patch) from 'v0.2.0' / '0.2.0' (extra parts ignored)."""
    nums = [int(n) for n in re.findall(r"\d+", version)][:3]
    nums += [0] * (3 - len(nums))
    return tuple(nums)  # type: ignore[return-value]


def latest_version(timeout: float = 5.0) -> str:
    """Highest release tag on the GitHub repo (e.g. ``v0.3.0``). Network call."""
    req = urllib.request.Request(
        _TAGS_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "atak-mcp"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        tags = json.load(resp)
    versions = [t["name"] for t in tags if re.search(r"\d", t.get("name", ""))]
    return max(versions, key=_key) if versions else ""


def check_update(timeout: float = 5.0) -> dict:
    """Compare the installed version to the latest GitHub tag.

    Returns ``{current, latest, update_available, hint}``. ``update_available``
    is ``None`` (with an ``error``) when the check could not reach GitHub, so an
    offline run degrades gracefully instead of raising.
    """
    cur = current_version()
    try:
        latest = latest_version(timeout)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        return {"current": cur, "latest": None, "update_available": None,
                "error": f"could not reach GitHub: {e}"}
    if not latest:
        return {"current": cur, "latest": None, "update_available": None,
                "error": "no release tags found"}
    available = _key(latest) > _key(cur)
    hint = (
        f"update available: pin @{latest} in your MCP config and restart, "
        f"or use `uvx --refresh` if you track main"
        if available else "up to date"
    )
    return {"current": cur, "latest": latest, "update_available": available, "hint": hint}
