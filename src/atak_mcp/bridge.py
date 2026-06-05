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
                scrollable=el.get("scrollable") == "true",
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
def _to_px(x, y, norm: bool, serial: Optional[str] = None) -> tuple[int, int]:
    """Resolve (x, y) to device pixels. With ``norm`` they are fractions in
    [0,1] of the live device resolution; otherwise they pass through as ints.

    Normalized input lets a caller act on a screenshot-relative position safely:
    fractions are scale-invariant, so they survive any client-side downscale of
    the returned image.
    """
    if not norm:
        return (int(x), int(y))
    w, h = device_size(serial)
    if w <= 0 or h <= 0:
        raise AdbError("cannot resolve device resolution for normalized coords")
    px = min(max(int(round(float(x) * w)), 0), w - 1)
    py = min(max(int(round(float(y) * h)), 0), h - 1)
    return (px, py)


def tap_xy(
    x, y, serial: Optional[str] = None, norm: bool = False
) -> tuple[int, int]:
    """Tap (x, y). With ``norm`` the coordinates are [0,1] fractions of the
    screen. Returns the device pixel actually tapped."""
    px, py = _to_px(x, y, norm, serial)
    adb(["shell", "input", "tap", str(px), str(py)], serial)
    return (px, py)


def tap(
    query: str,
    by: str = "any",
    index: int = 0,
    serial: Optional[str] = None,
    exact: bool = False,
    prefer_clickable: bool = True,
    scroll: bool = False,
) -> Node:
    """Find a node and tap the centre of its bounds. Returns the tapped node.

    When several nodes match and ``index`` is 0, a clickable match is preferred
    over a non-clickable one (e.g. a Button over a TextView with the same text).

    With ``scroll`` the node is first brought on-screen via
    :func:`scroll_into_view` (so an off-screen list item can be tapped in one
    call); ``index``/``prefer_clickable`` do not apply in that mode.
    """
    if scroll:
        return scroll_into_view(query, by=by, serial=serial, exact=exact, do_tap=True)
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


def swipe(
    x1, y1, x2, y2, ms: int = 300, serial: Optional[str] = None, norm: bool = False
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Swipe from (x1,y1) to (x2,y2). With ``norm`` all four coordinates are
    [0,1] fractions of the screen. Returns the two device pixel endpoints."""
    p1 = _to_px(x1, y1, norm, serial)
    p2 = _to_px(x2, y2, norm, serial)
    adb(["shell", "input", "swipe", *map(str, (*p1, *p2, ms))], serial)
    return (p1, p2)


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
# scrolling
# --------------------------------------------------------------------------- #
def _largest_scrollable(nodes: list[Node]) -> Optional[Node]:
    """The scrollable node with the largest area, or None if there isn't one."""
    best, best_area = None, 0
    for n in nodes:
        if not n.scrollable:
            continue
        l, t, r, b = n.bounds
        area = (r - l) * (b - t)
        if area > best_area:
            best, best_area = n, area
    return best


def _scroll_signature(nodes: list[Node]) -> tuple:
    """A cheap fingerprint of a dump, to detect when scrolling stops moving."""
    return tuple((n.text, n.resource_id, n.bounds) for n in nodes)


def _scroll_container(bounds: tuple[int, int, int, int], down: bool,
                      serial: Optional[str]) -> None:
    """One vertical swipe inside ``bounds``. ``down`` scrolls content downward
    (revealing items further down the list)."""
    l, t, r, b = bounds
    cx = (l + r) // 2
    span = b - t
    near = t + int(span * 0.75)
    far = t + int(span * 0.25)
    if down:
        swipe(cx, near, cx, far, 300, serial)  # finger up -> content scrolls down
    else:
        swipe(cx, far, cx, near, 300, serial)  # finger down -> content scrolls up


def scroll_into_view(
    query: str,
    by: str = "any",
    serial: Optional[str] = None,
    max_swipes: int = 20,
    exact: bool = False,
    do_tap: bool = False,
) -> Node:
    """Bring an off-screen node into view by scrolling, then return it.

    The bridge reads ``uiautomator dump`` rather than running an on-device
    UiScrollable action, so this is a *bounded auto-scroll*: it swipes inside the
    largest scrollable container (falling back to the whole screen) up to
    ``max_swipes`` times in each direction, re-dumping after each swipe, until a
    node matches ``query`` (``by`` = any|text|id|desc). It stops early in a
    direction once a swipe no longer changes the screen (list end). With
    ``do_tap`` the matched node is tapped. Raises :class:`AdbError` if the node
    never appears.
    """
    nodes = dump(serial)
    matches = find(query, by=by, nodes=nodes, exact=exact)
    if matches:
        node = matches[0]
        if do_tap:
            tap_xy(*node.center, serial)
        return node

    container = _largest_scrollable(nodes)
    if container is not None:
        bounds = container.bounds
    else:
        w, h = device_size(serial)
        bounds = (0, 0, w, h)

    for down in (True, False):
        prev = _scroll_signature(nodes)
        for _ in range(max_swipes):
            _scroll_container(bounds, down, serial)
            time.sleep(0.4)
            nodes = dump(serial)
            matches = find(query, by=by, nodes=nodes, exact=exact)
            if matches:
                node = matches[0]
                if do_tap:
                    tap_xy(*node.center, serial)
                return node
            sig = _scroll_signature(nodes)
            if sig == prev:  # reached the end in this direction
                break
            prev = sig

    raise AdbError(
        f"scroll_into_view: {query!r} not found after scrolling "
        f"(by={by}, max_swipes={max_swipes})"
    )


# --------------------------------------------------------------------------- #
# logs
# --------------------------------------------------------------------------- #
def logcat_clear(serial: Optional[str] = None) -> None:
    adb(["logcat", "-c"], serial, check=False)


def logcat(
    lines: int = 200,
    grep: Optional[str] = None,
    serial: Optional[str] = None,
    since: Optional[str] = None,
) -> str:
    """Dump logcat non-blocking, grepping the FULL buffer and returning matches.

    Always uses ``adb logcat -d`` (dump and exit, never a streaming/follow call
    that would hang). The important behaviour:

    * ``grep`` filters the **whole** ring buffer, not just a trailing window, so
      a marker logged long ago is still found.
    * ``lines`` then caps how many lines are **returned**, applied *after*
      filtering, i.e. "the last N matching lines from the whole buffer". Pass
      ``lines<=0`` (or ``None``) for no cap. With no ``grep``/``since`` this is a
      fast path that asks adb for just the last N lines of the full dump.
    * ``since`` narrows the buffer read to a time window, mapping to
      ``adb logcat -d -t '<since>'``. adb accepts:
        - a line count, e.g. ``"500"``
        - an absolute timestamp, e.g. ``"01-30 14:00:00.000"`` or
          ``"2026-01-30 14:00:00.000"`` (a leading year is allowed)
      ``grep``/``lines`` are still applied within that window.
    """
    args = ["logcat", "-d", "-v", "time"]
    if since:
        args += ["-t", since]
    elif not grep and lines and lines > 0:
        # No filter: let adb return just the last N lines (cheap, avoids
        # shovelling the whole multi-MB buffer over adb just to tail it).
        args += ["-t", str(lines)]
    out = adb(args, serial, timeout=30)
    result = out.splitlines()
    if grep:
        g = grep.lower()
        result = [l for l in result if g in l.lower()]
    if lines and lines > 0:
        result = result[-lines:]
    return "\n".join(result)


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
