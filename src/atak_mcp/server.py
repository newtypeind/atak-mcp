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
def screenshot() -> Image:
    """Capture the device screen and return it as a PNG image."""
    path = tempfile.mktemp(suffix=".png")
    bridge.screenshot(path)
    return Image(path=path)


@mcp.tool()
def ui_dump(clickable_only: bool = False) -> str:
    """Return the on-screen UI hierarchy as JSON (text, id, desc, bounds, center)."""
    nodes = bridge.dump()
    if clickable_only:
        nodes = [n for n in nodes if n.clickable]
    return json.dumps([n.as_dict() for n in nodes], ensure_ascii=False, indent=2)


@mcp.tool()
def find(query: str, by: str = "any", exact: bool = False) -> str:
    """Find UI nodes matching ``query``. ``by`` = any|text|id|desc; ``exact`` requires equality."""
    nodes = bridge.find(query, by=by, exact=exact)
    return json.dumps([n.as_dict() for n in nodes], ensure_ascii=False, indent=2)


@mcp.tool()
def tap(
    query: str = "", by: str = "any", index: int = 0,
    exact: bool = False, x: int = -1, y: int = -1,
) -> str:
    """Tap a node found by ``query`` (preferred) or raw ``x``/``y`` coordinates.

    Prefers a clickable match over a same-text label; pass ``exact`` to require equality.
    """
    if x >= 0 and y >= 0:
        bridge.tap_xy(x, y)
        return f"tapped ({x},{y})"
    node = bridge.tap(query, by=by, index=index, exact=exact)
    return f"tapped {node.label()!r} @{node.center}"


@mcp.tool()
def wait_for(query: str, by: str = "any", timeout: float = 10.0) -> str:
    """Wait until a node matching ``query`` appears (or time out)."""
    node = bridge.wait_for(query, by=by, timeout=timeout)
    return f"found {node.label()!r} @{node.center}"


@mcp.tool()
def swipe(x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> str:
    """Swipe from (x1,y1) to (x2,y2)."""
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
def logcat(lines: int = 200, grep: str = "") -> str:
    """Dump the tail of logcat, optionally filtered by a substring."""
    return bridge.logcat(lines=lines, grep=grep or None)


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
