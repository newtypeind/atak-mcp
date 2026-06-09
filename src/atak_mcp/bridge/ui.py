# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Screen capture and the uiautomator UI hierarchy.

The core idea of the whole tool lives here: read the on-screen node tree, find a
node by its text / resource id / content description, and let callers act on the
centre of its bounds instead of guessing pixels.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

from ._adb import AdbError, adb

__all__ = [
    "Node", "screenshot", "ui_xml", "parse_nodes", "dump", "find",
    "wait_for", "exists", "wait_gone",
]

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def screenshot(path: str, serial: Optional[str] = None) -> str:
    """Grab a PNG screenshot to ``path`` and return the path.

    Foldables and multi-display devices (e.g. Galaxy Z Flip/Fold) make
    ``screencap`` print a "[Warning] Multiple displays were found" banner to
    stdout *before* the PNG bytes, which corrupts a naive capture. We locate the
    PNG signature and slice from there, so the warning prefix is harmless.
    """
    data = adb(["exec-out", "screencap", "-p"], serial, binary=True, timeout=30)
    idx = data.find(_PNG_MAGIC)
    if idx < 0:
        head = data[:120].decode("utf-8", "replace")
        raise AdbError(f"screencap returned no PNG signature; head={head!r}")
    with open(path, "wb") as fh:
        fh.write(data[idx:])
    return path


@dataclass
class Node:
    text: str = ""
    resource_id: str = ""
    content_desc: str = ""
    cls: str = ""
    package: str = ""
    clickable: bool = False
    enabled: bool = True
    checked: bool = False
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)

    @property
    def center(self) -> tuple[int, int]:
        l, t, r, b = self.bounds
        return ((l + r) // 2, (t + b) // 2)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["center"] = self.center
        return d

    def label(self) -> str:
        return self.text or self.content_desc or self.resource_id or self.cls


def ui_xml(serial: Optional[str] = None, attempts: int = 3) -> str:
    """Return the raw uiautomator XML dump.

    uiautomator refuses to dump while the screen is animating, so we retry a
    few times. Compose surfaces its semantics tree to accessibility, which is
    what uiautomator reads, so Compose nodes with text/contentDescription show
    up here just like classic View nodes.
    """
    last = ""
    for i in range(attempts):
        out = adb(
            ["shell", "uiautomator", "dump", "/sdcard/atak_mcp_dump.xml"],
            serial,
            timeout=30,
            check=False,
        )
        last = out
        if "dumped to" in out or "UI hierchary" in out:
            return adb(["exec-out", "cat", "/sdcard/atak_mcp_dump.xml"], serial)
        time.sleep(0.6 * (i + 1))
    raise AdbError(f"uiautomator dump failed after {attempts} attempts: {last.strip()}")


def parse_nodes(xml_text: str) -> list[Node]:
    nodes: list[Node] = []
    root = ET.fromstring(xml_text)
    for el in root.iter("node"):
        m = _BOUNDS_RE.match(el.get("bounds", ""))
        bounds = tuple(int(x) for x in m.groups()) if m else (0, 0, 0, 0)
        nodes.append(
            Node(
                text=el.get("text", ""),
                resource_id=el.get("resource-id", ""),
                content_desc=el.get("content-desc", ""),
                cls=el.get("class", ""),
                package=el.get("package", ""),
                clickable=el.get("clickable") == "true",
                enabled=el.get("enabled") == "true",
                checked=el.get("checked") == "true",
                bounds=bounds,
            )
        )
    return nodes


def dump(serial: Optional[str] = None, attempts: int = 3) -> list[Node]:
    return parse_nodes(ui_xml(serial, attempts=attempts))


def find(
    query: str,
    by: str = "any",
    serial: Optional[str] = None,
    nodes: Optional[list[Node]] = None,
    visible_only: bool = True,
    exact: bool = False,
    clickable_only: bool = False,
) -> list[Node]:
    """Find nodes whose text/id/desc matches ``query`` (case-insensitive).

    ``by`` is one of ``any`` | ``text`` | ``id`` | ``desc``. With ``exact`` the
    field must equal the query (after trimming) rather than merely contain it,
    which avoids matching a dialog title like ``"Load Plugins?"`` when you want
    the ``"Load Plugins"`` button.
    """
    if nodes is None:
        nodes = dump(serial)
    q = query.strip().lower()
    fields_for = {
        "text": lambda n: [n.text],
        "id": lambda n: [n.resource_id],
        "desc": lambda n: [n.content_desc],
        "any": lambda n: [n.text, n.resource_id, n.content_desc],
    }[by]
    out = []
    for n in nodes:
        if visible_only and n.bounds[2] - n.bounds[0] <= 0:
            continue
        if clickable_only and not n.clickable:
            continue
        fields = [(f or "").strip().lower() for f in fields_for(n)]
        if exact:
            matched = any(f == q for f in fields)
        else:
            matched = any(q in f for f in fields)
        if matched:
            out.append(n)
    return out


def wait_for(
    query: str,
    by: str = "any",
    timeout: float = 10.0,
    interval: float = 0.7,
    serial: Optional[str] = None,
) -> Node:
    """Poll until a node matching ``query`` appears, or raise on timeout."""
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            # single-attempt dump: fail fast mid-animation and let this loop
            # re-poll, instead of paying ui_xml's escalating internal retries.
            matches = find(query, by=by, nodes=dump(serial, attempts=1))
            if matches:
                return matches[0]
        except AdbError as e:  # transient dump failure
            last_err = e
        time.sleep(interval)
    raise AdbError(f"timed out waiting for {query!r} ({by}); last={last_err}")


def exists(query: str, by: str = "any", serial: Optional[str] = None) -> bool:
    """True if any node currently matches ``query`` (a cheap branch test)."""
    try:
        return bool(find(query, by=by, nodes=dump(serial, attempts=1)))
    except AdbError:
        return False


def wait_gone(
    query: str,
    by: str = "any",
    timeout: float = 10.0,
    interval: float = 0.7,
    serial: Optional[str] = None,
) -> bool:
    """Poll until no node matches ``query`` (dialog closed, spinner gone)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if not find(query, by=by, serial=serial):
                return True
        except AdbError:
            pass  # transient dump failure; treat as not-yet-gone
        time.sleep(interval)
    raise AdbError(f"{query!r} still present after {timeout}s")
