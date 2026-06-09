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
    "Node", "screenshot", "screenshot_meta", "display_geometry", "device_size",
    "ui_xml", "parse_nodes", "dump", "find", "wait_for", "exists", "wait_gone",
]

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def screenshot(path: str, serial: Optional[str] = None) -> str:
    """Grab a PNG screenshot to ``path`` and return the path.

    The PNG is written at the device's native resolution; the bridge never
    resizes it (see :func:`screenshot_meta` for the geometry a caller needs to
    map screenshot pixels onto tap coordinates).

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


def _png_size(data: bytes) -> tuple[int, int]:
    """Width/height from a PNG's IHDR chunk (``data`` starting at the signature).

    Avoids a Pillow dependency so the bridge stays standard-library only.
    """
    if len(data) < 24 or data[:8] != _PNG_MAGIC:
        return (0, 0)
    return (
        int.from_bytes(data[16:20], "big"),
        int.from_bytes(data[20:24], "big"),
    )


def screenshot_meta(path: str, serial: Optional[str] = None) -> dict:
    """Capture a screenshot and report the geometry needed to map its pixels.

    Returns ``{path, image_width, image_height, device_width, device_height,
    wm_width, wm_height, rotation, scale, source}``. ``image_*`` are the PNG's
    real dimensions; ``device_*`` is the current-rotation pixel space that
    ``input tap`` / ``ui_dump`` use (see :func:`display_geometry`); ``scale`` is
    ``image_width / device_width`` (1.0 because the bridge does not resize, but
    reported so a caller can detect any future downscale). Screenshot pixels map
    onto tap coordinates by ``tap = screenshot_pixel / scale``.
    """
    screenshot(path, serial)
    with open(path, "rb") as fh:
        head = fh.read(33)  # signature (8) + IHDR up to height (offset 24)
    iw, ih = _png_size(head)
    geo = display_geometry(serial)
    dw, dh = geo["width"], geo["height"]
    scale = round(iw / dw, 4) if dw else 1.0
    return {
        "path": path,
        "image_width": iw,
        "image_height": ih,
        "device_width": dw,
        "device_height": dh,
        "wm_width": geo["wm_width"],
        "wm_height": geo["wm_height"],
        "rotation": geo["rotation"],
        "scale": scale,
        "source": geo["source"],
    }


# --------------------------------------------------------------------------- #
# display geometry
# --------------------------------------------------------------------------- #
# Order matters: the *current-rotation* pixel space (what screencap, uiautomator
# bounds and `input tap` all share) is read from `dumpsys display` first, because
# on devices that run an app in forced landscape the system rotation (`wm size`
# + user_rotation) still reads portrait and would transpose the dimensions. This
# is exactly the Galaxy Z Flip3 case: `wm size` = 1080x2640 / rotation 0, while
# the live framebuffer (and tap space) is 2640x1080.
_DISPLAY_SIZE_PATTERNS = (
    ("viewport", re.compile(r"isActive=true[^}]*?deviceWidth=(\d+),\s*deviceHeight=(\d+)")),
    ("override", re.compile(r"mOverrideDisplayInfo=DisplayInfo\{.*?\breal (\d+) x (\d+)")),
    ("logicalFrame", re.compile(r"logicalFrame=Rect\(0,\s*0\s*-\s*(\d+),\s*(\d+)\)")),
)
_VIEWPORT_ROT_RE = re.compile(r"isActive=true[^}]*?orientation=(\d)")
_OVERRIDE_ROT_RE = re.compile(r"mOverrideDisplayInfo=DisplayInfo\{.*?\brotation (\d)")
_WINDOW_ROT_RE = re.compile(r"\bmRotation=(\d)")
_WM_SIZE_RE = re.compile(r"(\d+)\s*x\s*(\d+)")


def _parse_display_size(dumpsys_display: str) -> tuple[int, int, str]:
    """(width, height, source) of the active display from ``dumpsys display``."""
    for source, pat in _DISPLAY_SIZE_PATTERNS:
        m = pat.search(dumpsys_display)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            if w > 0 and h > 0:
                return (w, h, source)
    return (0, 0, "")


def _parse_rotation(dumpsys_display: str) -> Optional[int]:
    """Effective display rotation (0|1|2|3) from ``dumpsys display`` text."""
    for pat in (_VIEWPORT_ROT_RE, _OVERRIDE_ROT_RE):
        m = pat.search(dumpsys_display)
        if m:
            return int(m.group(1))
    return None


def _parse_wm_size(wm_size: str) -> tuple[int, int]:
    """Natural-orientation size from ``wm size`` (Override beats Physical)."""
    physical = override = None
    for line in wm_size.splitlines():
        m = _WM_SIZE_RE.search(line)
        if not m:
            continue
        wh = (int(m.group(1)), int(m.group(2)))
        if "Override" in line:
            override = wh
        elif "Physical" in line:
            physical = wh
    return override or physical or (0, 0)


def display_geometry(serial: Optional[str] = None) -> dict:
    """Resolve the device's pixel geometry in one place.

    Returns ``{width, height, rotation, wm_width, wm_height, source}`` where
    ``width``/``height`` are the current-rotation pixel dimensions shared by
    screencap, uiautomator bounds and ``input tap``. Prefers ``dumpsys display``
    (correct under app-forced landscape) and falls back to ``wm size`` +
    rotation when that cannot be parsed.
    """
    display = adb(["shell", "dumpsys", "display"], serial, check=False)
    width, height, source = _parse_display_size(display)
    rotation = _parse_rotation(display)
    pw, ph = _parse_wm_size(adb(["shell", "wm", "size"], serial, check=False))

    if rotation is None:
        win = adb(["shell", "dumpsys", "window"], serial, check=False)
        m = _WINDOW_ROT_RE.search(win)
        if m:
            rotation = int(m.group(1))
        else:
            val = adb(
                ["shell", "settings", "get", "system", "user_rotation"],
                serial,
                check=False,
            ).strip()
            rotation = int(val) if val.isdigit() else 0

    if not (width and height) and pw and ph:
        # Fall back to wm size, oriented by rotation (landscape = wider).
        if rotation in (1, 3):
            width, height = max(pw, ph), min(pw, ph)
        else:
            width, height = min(pw, ph), max(pw, ph)
        source = "wm_size"

    return {
        "width": width,
        "height": height,
        "rotation": rotation,
        "wm_width": pw,
        "wm_height": ph,
        "source": source,
    }


def device_size(serial: Optional[str] = None) -> tuple[int, int]:
    """Current-rotation (width, height) the same way ``input tap`` sees them."""
    geo = display_geometry(serial)
    return (geo["width"], geo["height"])


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
    scrollable: bool = False
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
                scrollable=el.get("scrollable") == "true",
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
