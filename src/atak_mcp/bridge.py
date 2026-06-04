# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Core adb bridge for driving ATAK (and any Android UI) deterministically.

Everything here is pure standard library so the bridge runs anywhere adb runs.
The design goal is to never tap blindly: callers locate a node by its text,
resource id, or content description (read from the uiautomator hierarchy) and
tap the centre of that node's bounds.

Used by:
  * ``cli.py``    - a command line front end for humans and shell scripts
  * ``server.py`` - an MCP stdio server for AI agents
"""

from __future__ import annotations

import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

# ATAK Civilian package id. Override per call where a different flavour is used.
ATAK_CIV_PACKAGE = "com.atakmap.app.civ"

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


class AdbError(RuntimeError):
    """Raised when an adb invocation fails or a UI query finds nothing."""


# --------------------------------------------------------------------------- #
# adb plumbing
# --------------------------------------------------------------------------- #
def _base(serial: Optional[str]) -> list[str]:
    cmd = ["adb"]
    serial = serial or os.environ.get("ANDROID_SERIAL")
    if serial:
        cmd += ["-s", serial]
    return cmd


def adb(
    args: list[str],
    serial: Optional[str] = None,
    *,
    binary: bool = False,
    timeout: int = 60,
    check: bool = True,
):
    """Run an adb command and return stdout (str, or bytes when ``binary``)."""
    proc = subprocess.run(
        _base(serial) + args, capture_output=True, timeout=timeout
    )
    if check and proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise AdbError(f"`adb {' '.join(args)}` failed (rc={proc.returncode}): {err}")
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


def devices() -> list[dict]:
    """Return the attached devices as ``[{serial, state, ...}]``."""
    out = adb(["devices", "-l"])
    rows = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        entry = {"serial": parts[0], "state": parts[1] if len(parts) > 1 else "?"}
        for tok in parts[2:]:
            if ":" in tok:
                k, v = tok.split(":", 1)
                entry[k] = v
        rows.append(entry)
    return rows


# --------------------------------------------------------------------------- #
# screen capture
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# UI hierarchy
# --------------------------------------------------------------------------- #
@dataclass
class Node:
    text: str = ""
    resource_id: str = ""
    content_desc: str = ""
    cls: str = ""
    package: str = ""
    clickable: bool = False
    enabled: bool = True
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
                bounds=bounds,
            )
        )
    return nodes


def dump(serial: Optional[str] = None) -> list[Node]:
    return parse_nodes(ui_xml(serial))


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
            matches = find(query, by=by, serial=serial)
            if matches:
                return matches[0]
        except AdbError as e:  # transient dump failure
            last_err = e
        time.sleep(interval)
    raise AdbError(f"timed out waiting for {query!r} ({by}); last={last_err}")


# --------------------------------------------------------------------------- #
# input
# --------------------------------------------------------------------------- #
def tap_xy(x: int, y: int, serial: Optional[str] = None) -> None:
    adb(["shell", "input", "tap", str(x), str(y)], serial)


def tap(
    query: str,
    by: str = "any",
    index: int = 0,
    serial: Optional[str] = None,
    exact: bool = False,
    prefer_clickable: bool = True,
) -> Node:
    """Find a node and tap the centre of its bounds. Returns the tapped node.

    When several nodes match and ``index`` is 0, a clickable match is preferred
    over a non-clickable one (e.g. a Button over a TextView with the same text).
    """
    matches = find(query, by=by, serial=serial, exact=exact)
    if not matches:
        raise AdbError(f"no node matching {query!r} (by={by}, exact={exact})")
    if prefer_clickable and index == 0:
        clickable = [m for m in matches if m.clickable]
        if clickable:
            matches = clickable
    if index >= len(matches):
        raise AdbError(f"index {index} out of range; {len(matches)} match(es)")
    node = matches[index]
    x, y = node.center
    tap_xy(x, y, serial)
    return node


def swipe(x1, y1, x2, y2, ms: int = 300, serial: Optional[str] = None) -> None:
    adb(["shell", "input", "swipe", *map(str, (x1, y1, x2, y2, ms))], serial)


def text_input(value: str, serial: Optional[str] = None) -> None:
    # `input text` treats spaces specially; encode them.
    adb(["shell", "input", "text", value.replace(" ", "%s")], serial)


def key(keycode: str, serial: Optional[str] = None) -> None:
    """Send a key event, e.g. ``BACK``, ``HOME``, ``ENTER`` or a numeric code."""
    if keycode.isdigit():
        adb(["shell", "input", "keyevent", keycode], serial)
    else:
        adb(["shell", "input", "keyevent", f"KEYCODE_{keycode.upper()}"], serial)


# --------------------------------------------------------------------------- #
# logs
# --------------------------------------------------------------------------- #
def logcat_clear(serial: Optional[str] = None) -> None:
    adb(["logcat", "-c"], serial, check=False)


def logcat(
    lines: int = 200,
    grep: Optional[str] = None,
    serial: Optional[str] = None,
) -> str:
    """Dump the tail of logcat, optionally filtered by a substring."""
    out = adb(["logcat", "-d", "-v", "time", "-t", str(lines)], serial, timeout=30)
    if grep:
        g = grep.lower()
        out = "\n".join(l for l in out.splitlines() if g in l.lower())
    return out


# --------------------------------------------------------------------------- #
# apps & plugins
# --------------------------------------------------------------------------- #
def list_packages(third_party: bool = True, serial: Optional[str] = None) -> list[str]:
    args = ["shell", "pm", "list", "packages"]
    if third_party:
        args.append("-3")
    out = adb(args, serial)
    return sorted(l.split(":", 1)[1].strip() for l in out.splitlines() if ":" in l)


def list_plugins(serial: Optional[str] = None) -> list[str]:
    """Best-effort list of installed ATAK plugins (by package-name heuristic)."""
    pkgs = list_packages(third_party=True, serial=serial)
    return [p for p in pkgs if "atak" in p.lower() and p != ATAK_CIV_PACKAGE]


def is_installed(package: str, serial: Optional[str] = None) -> bool:
    out = adb(["shell", "pm", "list", "packages", package], serial)
    return any(line.strip() == f"package:{package}" for line in out.splitlines())


def install(apk: str, serial: Optional[str] = None) -> str:
    # -r reinstall, -g grant all runtime permissions (so RECORD_AUDIO is live).
    return adb(["install", "-r", "-g", apk], serial, timeout=300)


def uninstall(package: str, serial: Optional[str] = None) -> str:
    return adb(["uninstall", package], serial, timeout=120, check=False)


def reload_plugin(package: str, apk: str, serial: Optional[str] = None) -> str:
    """Package-based reload: uninstall then install.

    Empirically ATAK only picks up plugin code changes reliably after a full
    uninstall/reinstall, so this is the canonical reload path.
    """
    rm = uninstall(package, serial)
    add = install(apk, serial)
    return f"uninstall: {rm.strip()}\ninstall: {add.strip()}"


def confirm_load(timeout: float = 25.0, serial: Optional[str] = None) -> str:
    """Confirm ATAK's "load this plugin?" prompt so a freshly installed plugin
    actually loads.

    ATAK does not reliably auto-load a plugin after an uninstall/reinstall; it
    instead pops a dialog (either the per-plugin "Would you like to load this
    installed plugin?" with an OK button, or the startup "Load Plugins?" with a
    "Load Plugins" button). This polls for either and taps the confirm button.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            nodes = dump(serial)
        except AdbError:
            time.sleep(1.0)
            continue
        haystack = " ".join((n.text or "").lower() for n in nodes)
        is_prompt = (
            "load this installed plugin" in haystack
            or "load plugins" in haystack
            or "load plugin" in haystack
        )
        if is_prompt:
            for label in ("OK", "Load Plugins", "Load", "Yes"):
                btn = next(
                    (n for n in nodes
                     if n.clickable and (n.text or "").strip().lower() == label.lower()),
                    None,
                )
                if btn:
                    x, y = btn.center
                    tap_xy(x, y, serial)
                    return f"confirmed plugin load via {label!r}"
        time.sleep(1.5)
    return "no load prompt appeared (plugin may already be loaded)"


def launch_atak(package: str = ATAK_CIV_PACKAGE, serial: Optional[str] = None) -> str:
    return adb(
        ["shell", "monkey", "-p", package, "-c",
         "android.intent.category.LAUNCHER", "1"],
        serial,
        check=False,
    )


def foreground_app(serial: Optional[str] = None) -> str:
    """Return the resumed/top activity string (best effort)."""
    out = adb(["shell", "dumpsys", "activity", "activities"], serial, check=False)
    for line in out.splitlines():
        if "mResumedActivity" in line or "topResumedActivity" in line:
            return line.strip()
    return ""
