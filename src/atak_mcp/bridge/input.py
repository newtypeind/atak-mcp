# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Taps, gestures, and text entry.

Every gesture resolves a target node first (via :mod:`ui`) and acts on the
centre of its bounds, except the explicit ``*_xy`` variants that take raw
coordinates.
"""

from __future__ import annotations

import time
from typing import Optional

from ._adb import AdbError, adb
from .ui import Node, device_size, dump, find

__all__ = [
    "tap_xy", "tap", "long_press_xy", "long_press", "double_tap_xy",
    "double_tap", "clear_text", "swipe", "text_input", "key", "scroll_into_view",
]


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


def tap_xy(x, y, serial: Optional[str] = None, norm: bool = False) -> tuple[int, int]:
    """Tap (x, y). With ``norm`` the coordinates are [0,1] fractions of the
    screen. Returns the device pixel actually tapped."""
    px, py = _to_px(x, y, norm, serial)
    adb(["shell", "input", "tap", str(px), str(py)], serial)
    return (px, py)


def _resolve(
    query: str,
    by: str,
    index: int,
    serial: Optional[str],
    exact: bool,
    prefer_clickable: bool,
) -> Node:
    """Find the single node a gesture should target (shared by tap/long_press)."""
    matches = find(query, by=by, serial=serial, exact=exact)
    if not matches:
        raise AdbError(f"no node matching {query!r} (by={by}, exact={exact})")
    if prefer_clickable and index == 0:
        clickable = [m for m in matches if m.clickable]
        if clickable:
            matches = clickable
    if index >= len(matches):
        raise AdbError(f"index {index} out of range; {len(matches)} match(es)")
    return matches[index]


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
    node = _resolve(query, by, index, serial, exact, prefer_clickable)
    tap_xy(*node.center, serial=serial)
    return node


def long_press_xy(x: int, y: int, ms: int = 600, serial: Optional[str] = None) -> None:
    """Long-press a point by swiping in place. ATAK uses this to drop a marker."""
    swipe(x, y, x, y, ms, serial)


def long_press(
    query: str,
    by: str = "any",
    index: int = 0,
    serial: Optional[str] = None,
    exact: bool = False,
    ms: int = 600,
    prefer_clickable: bool = True,
) -> Node:
    """Find a node and long-press the centre of its bounds. Returns the node."""
    node = _resolve(query, by, index, serial, exact, prefer_clickable)
    long_press_xy(*node.center, ms=ms, serial=serial)
    return node


def double_tap_xy(x: int, y: int, serial: Optional[str] = None) -> None:
    tap_xy(x, y, serial)
    tap_xy(x, y, serial)


def double_tap(
    query: str,
    by: str = "any",
    index: int = 0,
    serial: Optional[str] = None,
    exact: bool = False,
    prefer_clickable: bool = True,
) -> Node:
    """Find a node and double-tap it (e.g. to zoom the ATAK map in)."""
    node = _resolve(query, by, index, serial, exact, prefer_clickable)
    double_tap_xy(*node.center, serial=serial)
    return node


def clear_text(count: int = 120, serial: Optional[str] = None) -> None:
    """Clear the focused text field: jump to the end, then backspace ``count``
    times. ``input`` has no select-all, so this is the robust way to empty a
    field before retyping (e.g. editing an existing TAK server address)."""
    key("MOVE_END", serial)
    adb(["shell", "input", "keyevent", *(["67"] * count)], serial)  # 67 = DEL


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
