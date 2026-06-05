# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""MCP stdio server exposing the ATAK bridge to AI agents.

Run it directly:

    python -m atak_mcp.server

or register it with an MCP client (e.g. Claude Code / Claude Desktop):

    {
      "mcpServers": {
        "atak": { "command": "python", "args": ["-m", "atak_mcp.server"] }
      }
    }

Requires the ``mcp`` package (``pip install mcp``). The bridge itself
(``bridge.py``) has no third-party dependencies, so the CLI works without it.
"""

from __future__ import annotations

import json
import tempfile

try:
    from mcp.server.fastmcp import FastMCP, Image
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "The 'mcp' package is required to run the server.\n"
        "Install it with:  pip install mcp\n"
        f"(import error: {exc})"
    )

from . import bridge

mcp = FastMCP("atak")


@mcp.tool()
def list_devices() -> str:
    """List attached adb devices."""
    return json.dumps(bridge.devices(), indent=2)


@mcp.tool()
def screenshot() -> list:
    """Capture the device screen as a PNG, plus geometry metadata.

    Returns the image **and** a text line describing the device resolution, the
    returned image's dimensions, and the scale between them, so a caller can map
    screenshot pixels onto ``tap``/``swipe``/``ui_dump`` coordinates regardless
    of any client-side downscale of the displayed image.
    """
    path = tempfile.mktemp(suffix=".png")
    m = bridge.screenshot_meta(path)
    meta = (
        f"device={m['device_width']}x{m['device_height']} "
        f"image={m['image_width']}x{m['image_height']} "
        f"scale={m['scale']} rotation={m['rotation']} "
        f"wm_size={m['wm_width']}x{m['wm_height']}\n"
        "Screenshot pixels map 1:1 to tap/swipe/ui_dump coordinates at device "
        "resolution (tap = screenshot_pixel / scale). If your view of this image "
        "is downscaled, prefer normalized taps: tap(nx=.., ny=..) with values in "
        "[0,1]."
    )
    return [Image(path=path), meta]


@mcp.tool()
def ui_dump(clickable_only: bool = False) -> str:
    """Return the on-screen UI hierarchy as JSON (text, id, desc, bounds, center)."""
    nodes = bridge.dump()
    if clickable_only:
        nodes = [n for n in nodes if n.clickable]
    return json.dumps([n.as_dict() for n in nodes], ensure_ascii=False, indent=2)


@mcp.tool()
def find(query: str, by: str = "any", exact: bool = False, scroll: bool = False) -> str:
    """Find UI nodes matching ``query``. ``by`` = any|text|id|desc; ``exact`` requires equality.

    With ``scroll`` the node is first scrolled into view (bounded auto-scroll),
    so an off-screen list item is located in one call.
    """
    if scroll:
        node = bridge.scroll_into_view(query, by=by, exact=exact)
        return json.dumps([node.as_dict()], ensure_ascii=False, indent=2)
    nodes = bridge.find(query, by=by, exact=exact)
    return json.dumps([n.as_dict() for n in nodes], ensure_ascii=False, indent=2)


@mcp.tool()
def tap(
    query: str = "", by: str = "any", index: int = 0,
    exact: bool = False, x: int = -1, y: int = -1,
    nx: float = -1.0, ny: float = -1.0, scroll: bool = False,
) -> str:
    """Tap a node by ``query``, raw pixel ``x``/``y``, or normalized ``nx``/``ny``.

    Resolution order: ``nx``/``ny`` (normalized [0,1], screenshot-relative) >
    ``x``/``y`` (device pixels) > ``query``. Query taps prefer a clickable match
    over a same-text label; pass ``exact`` to require equality, or ``scroll`` to
    bring an off-screen match into view first.
    """
    if nx >= 0 and ny >= 0:
        px, py = bridge.tap_xy(nx, ny, norm=True)
        return f"tapped ({px},{py}) [norm ({nx},{ny})]"
    if x >= 0 and y >= 0:
        bridge.tap_xy(x, y)
        return f"tapped ({x},{y})"
    node = bridge.tap(query, by=by, index=index, exact=exact, scroll=scroll)
    return f"tapped {node.label()!r} @{node.center}"


@mcp.tool()
def scroll_into_view(
    query: str, by: str = "any", exact: bool = False,
    tap: bool = False, max_swipes: int = 20,
) -> str:
    """Scroll an off-screen node into view (bounded auto-scroll), optionally tapping it.

    Swipes inside the largest scrollable container up to ``max_swipes`` times in
    each direction until ``query`` (``by`` = any|text|id|desc) matches.
    """
    node = bridge.scroll_into_view(
        query, by=by, exact=exact, do_tap=tap, max_swipes=max_swipes
    )
    verb = "tapped" if tap else "found"
    return f"{verb} {node.label()!r} @{node.center}"


@mcp.tool()
def wait_for(query: str, by: str = "any", timeout: float = 10.0) -> str:
    """Wait until a node matching ``query`` appears (or time out)."""
    node = bridge.wait_for(query, by=by, timeout=timeout)
    return f"found {node.label()!r} @{node.center}"


@mcp.tool()
def swipe(
    x1: int = -1, y1: int = -1, x2: int = -1, y2: int = -1, ms: int = 300,
    nx1: float = -1.0, ny1: float = -1.0, nx2: float = -1.0, ny2: float = -1.0,
) -> str:
    """Swipe between two points, given as device pixels (``x1,y1,x2,y2``) or as
    normalized [0,1] fractions (``nx1,ny1,nx2,ny2``)."""
    if min(nx1, ny1, nx2, ny2) >= 0:
        p1, p2 = bridge.swipe(nx1, ny1, nx2, ny2, ms, norm=True)
        return f"swiped {p1}->{p2} [norm]"
    if min(x1, y1, x2, y2) < 0:
        raise ValueError(
            "swipe needs pixel x1,y1,x2,y2 or normalized nx1,ny1,nx2,ny2 in [0,1]"
        )
    bridge.swipe(x1, y1, x2, y2, ms)
    return "ok"


@mcp.tool()
def type_text(value: str) -> str:
    """Type text into the focused input field."""
    bridge.text_input(value)
    return "ok"


@mcp.tool()
def press_key(code: str) -> str:
    """Send a key event such as BACK, HOME, ENTER, or a numeric keycode."""
    bridge.key(code)
    return "ok"


@mcp.tool()
def logcat(lines: int = 200, grep: str = "", since: str = "") -> str:
    """Dump logcat (non-blocking) and return the last ``lines`` matching lines.

    ``grep`` filters the **whole** buffer (not just the tail), and ``lines`` caps
    the result *after* filtering (pass ``lines<=0`` for no cap). ``since`` narrows
    the buffer to a window, mapping to ``adb logcat -d -t '<since>'`` and
    accepting a line count (``"500"``) or a timestamp (``"01-30 14:00:00.000"``
    or ``"2026-01-30 14:00:00.000"``).
    """
    return bridge.logcat(lines=lines, grep=grep or None, since=since or None)


@mcp.tool()
def list_plugins() -> str:
    """List installed ATAK plugins (package-name heuristic)."""
    return "\n".join(bridge.list_plugins()) or "(none)"


@mcp.tool()
def reload_plugin(package: str, apk: str) -> str:
    """Uninstall then reinstall a plugin apk (the reliable ATAK reload path)."""
    return bridge.reload_plugin(package, apk)


@mcp.tool()
def confirm_load(timeout: float = 25.0) -> str:
    """Confirm ATAK's 'load this plugin?' prompt so a reinstalled plugin loads."""
    return bridge.confirm_load(timeout=timeout)


@mcp.tool()
def install_apk(apk: str) -> str:
    """Install an apk with -r -g (reinstall + grant runtime permissions)."""
    return bridge.install(apk)


@mcp.tool()
def launch_atak(package: str = bridge.ATAK_CIV_PACKAGE) -> str:
    """Bring ATAK (or another package) to the foreground."""
    return bridge.launch_atak(package) or "launched"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
