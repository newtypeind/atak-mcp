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

from . import __version__, bridge, update

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


@mcp.tool()
def push_file(local: str, remote: str) -> str:
    """Copy a local file onto the device (adb push).

    Needed to configure a TAK server over SSL: stage a client certificate
    (.p12) or an ATAK data package (.zip) on the device, then import it.
    ``remote`` is a device path, e.g. /sdcard/Download/truststore.p12.
    """
    return bridge.push(local, remote)


@mcp.tool()
def broadcast(
    action: str,
    component: str = "",
    string_extras: dict[str, str] | None = None,
    int_extras: dict[str, int] | None = None,
    bool_extras: dict[str, bool] | None = None,
) -> str:
    """Send a broadcast Intent (am broadcast) to configure ATAK without UI taps.

    ATAK and its plugins accept config via broadcast Intents, which is more
    robust than tapping through Settings. Pass extras as JSON objects, e.g.
    string_extras={"filepath": "/sdcard/Download/server.zip"}.
    """
    extras: list[tuple[str, str, str]] = []
    extras += [("s", k, str(v)) for k, v in (string_extras or {}).items()]
    extras += [("i", k, str(v)) for k, v in (int_extras or {}).items()]
    extras += [("z", k, "true" if v else "false") for k, v in (bool_extras or {}).items()]
    return bridge.broadcast(action, component or None, extras)


@mcp.tool()
def pull_file(remote: str, local: str) -> str:
    """Copy a file off the device (adb pull): logs, recordings, exports."""
    return bridge.pull(remote, local)


@mcp.tool()
def long_press(query: str = "", by: str = "any", exact: bool = False,
               ms: int = 600, x: int = -1, y: int = -1) -> str:
    """Long-press a node (by query) or raw x/y. ATAK uses this to drop a marker."""
    if x >= 0 and y >= 0:
        bridge.long_press_xy(x, y, ms)
        return f"long-pressed ({x},{y})"
    node = bridge.long_press(query, by=by, exact=exact, ms=ms)
    return f"long-pressed {node.label()!r} @{node.center}"


@mcp.tool()
def double_tap(query: str = "", by: str = "any", exact: bool = False,
               x: int = -1, y: int = -1) -> str:
    """Double-tap a node (by query) or raw x/y, e.g. to zoom the map in."""
    if x >= 0 and y >= 0:
        bridge.double_tap_xy(x, y)
        return f"double-tapped ({x},{y})"
    node = bridge.double_tap(query, by=by, exact=exact)
    return f"double-tapped {node.label()!r} @{node.center}"


@mcp.tool()
def clear_text(count: int = 120) -> str:
    """Clear the focused text field (jump to end, backspace ``count`` times)."""
    bridge.clear_text(count)
    return "ok"


@mcp.tool()
def exists(query: str, by: str = "any") -> bool:
    """Return whether any node currently matches ``query``."""
    return bridge.exists(query, by=by)


@mcp.tool()
def wait_gone(query: str, by: str = "any", timeout: float = 10.0) -> str:
    """Wait until no node matches ``query`` (dialog closed / spinner gone)."""
    bridge.wait_gone(query, by=by, timeout=timeout)
    return "gone"


@mcp.tool()
def wake_unlock() -> str:
    """Wake the screen and dismiss a non-secure lock screen."""
    bridge.wake_unlock()
    return "ok"


@mcp.tool()
def stay_awake(on: bool = True) -> str:
    """Keep the screen on while charging (handy for unattended runs)."""
    return bridge.stay_awake(on)


@mcp.tool()
def is_running(package: str = bridge.ATAK_CIV_PACKAGE) -> bool:
    """Return whether ``package`` has a live process."""
    return bridge.is_running(package)


@mcp.tool()
def force_stop(package: str = bridge.ATAK_CIV_PACKAGE) -> str:
    """Force-stop a package."""
    return bridge.force_stop(package) or "ok"


@mcp.tool()
def clear_app_data(package: str = bridge.ATAK_CIV_PACKAGE) -> str:
    """Wipe an app's data (pm clear) for a from-scratch state."""
    return bridge.clear_app_data(package)


@mcp.tool()
def restart_atak(package: str = bridge.ATAK_CIV_PACKAGE) -> str:
    """Force-stop then relaunch ATAK (or another package)."""
    return bridge.restart_atak(package) or "restarted"


@mcp.tool()
def grant_permission(package: str, permission: str) -> str:
    """Grant a runtime permission (pm grant) to skip the in-app dialog."""
    return bridge.grant_permission(package, permission) or "ok"


@mcp.tool()
def crashes(package: str = "", lines: int = 500) -> str:
    """Return the crash log buffer (empty means no crashes). Pass-fail check."""
    return bridge.crashes(package or None, lines=lines) or "(no crashes)"


@mcp.tool()
def record_start(remote: str = "/sdcard/atak_mcp_record.mp4", time_limit: int = 180) -> str:
    """Start screenrecord detached on the device; stop with record_stop."""
    return bridge.record_start(remote, time_limit)


@mcp.tool()
def record_stop(remote: str = "/sdcard/atak_mcp_record.mp4", local: str = "") -> str:
    """Stop the recording and, if ``local`` is set, pull the mp4 off the device."""
    return bridge.record_stop(remote, local or None)


@mcp.tool()
def wait_atak_ready(package: str = bridge.ATAK_CIV_PACKAGE, timeout: float = 60.0) -> str:
    """Block until ATAK is running, foregrounded, and its UI is dumpable."""
    return bridge.wait_atak_ready(package, timeout)


@mcp.tool()
def open_tool(name: str, timeout: float = 10.0) -> str:
    """Open an ATAK tool/plugin by tapping Tools then its labelled item."""
    node = bridge.open_tool(name, timeout=timeout)
    return f"opened {node.label()!r} @{node.center}"


@mcp.tool()
def deploy_plugin(package: str, apk: str, ready_timeout: float = 60.0) -> str:
    """Full plugin dev loop: reinstall, launch, confirm load, wait ready."""
    return json.dumps(bridge.deploy_plugin(package, apk, ready_timeout=ready_timeout), indent=2)


@mcp.tool()
def enroll(host: str, username: str = "", token: str = "", verify: bool = True) -> str:
    """Configure/enroll a TAK server connection via ATAK's exported enroll deep link.

    ``host`` like "tak.example.com:8089:ssl"; ``token`` is the password or
    enrollment token. This is the supported external path on Android 13+, where
    ATAK's internal broadcast receivers are not reachable from adb. With
    ``verify``, confirm via logcat that ATAK processed the link.
    """
    return bridge.enroll(host, username or None, token or None, verify=verify)


@mcp.tool()
def import_url(url: str, verify: bool = True) -> str:
    """Import a file or data package from a URL via ATAK's import deep link.

    For a local file, host it and pass the URL (ATAK needs a content:// URI that
    adb cannot easily mint for an arbitrary local path).
    """
    return bridge.import_url(url, verify=verify)


@mcp.tool()
def deep_link(uri: str, verify: bool = True) -> str:
    """Open a raw ``tak:`` deep link through ATAK's exported VIEW activity.

    With ``verify``, confirm via logcat that ATAK processed it (raises if not).
    """
    return bridge.deep_link(uri, verify=verify)


@mcp.tool()
def atak_version(package: str = bridge.ATAK_CIV_PACKAGE) -> str:
    """Installed ATAK versionName (e.g. 5.6.0.11), or empty if not installed."""
    return bridge.atak_version(package) or "(not installed)"


@mcp.tool()
def doctor(package: str = bridge.ATAK_CIV_PACKAGE) -> str:
    """Probe the device for ATAK version drift and capabilities.

    Reports installed flavour/version vs the tested version, whether the deep-link
    entry point still works, resource-id presence, and whether internal broadcasts
    are reachable. Use this against a new ATAK release to see what (if anything)
    broke. No lasting side effects.
    """
    return json.dumps(bridge.doctor(package), indent=2, ensure_ascii=False)


@mcp.tool()
def list_servers() -> str:
    """List ATAK server connections with per-connection data.

    Returns JSON: name, connect_string, host, port, protocol, enabled (the
    on/off checkbox), and status (ATAK's status/error line, empty when idle/ok).
    """
    return json.dumps(bridge.list_servers(), indent=2, ensure_ascii=False)


@mcp.tool()
def add_server(name: str, host: str, port: str, protocol: str = "tcp") -> str:
    """Add a streaming CoT (TAK server) connection. protocol = tcp|ssl|quic."""
    return bridge.add_server(name, host, port, protocol)


@mcp.tool()
def remove_server(name: str) -> str:
    """Remove the server connection with the given name."""
    return bridge.remove_server(name)


@mcp.tool()
def edit_server(name: str, new_name: str = "", new_host: str = "",
                new_port: str = "", new_protocol: str = "") -> str:
    """Edit a server connection. Pass only the fields to change (protocol = tcp|ssl|quic)."""
    return bridge.edit_server(
        name,
        new_name or None, new_host or None,
        new_port or None, new_protocol or None,
    )


@mcp.tool()
def set_server_enabled(name: str, enabled: bool) -> str:
    """Enable or disable a server connection (toggles its checkbox)."""
    return bridge.set_server_enabled(name, enabled)


@mcp.tool()
def mcp_version() -> str:
    """The installed atak-mcp version."""
    return __version__


@mcp.tool()
def check_update() -> str:
    """Check GitHub for a newer atak-mcp release.

    Returns JSON {current, latest, update_available, hint}. Use it to tell the
    user when to refresh; applying the update is a uvx concern (re-pin the tag,
    or `uvx --refresh` if tracking main).
    """
    return json.dumps(update.check_update(), indent=2, ensure_ascii=False)


@mcp.tool()
def is_emulator() -> bool:
    """Return whether the target is an Android emulator (vs a USB device)."""
    return bridge.is_emulator()


@mcp.tool()
def reverse(remote_port: int, local_port: int = 0) -> str:
    """Set up `adb reverse tcp:<remote> tcp:<local>` (local defaults to remote).

    The device's localhost:<remote_port> then tunnels to the host's
    localhost:<local_port>, so an app on a USB device can reach a host server.
    """
    return bridge.reverse(remote_port, local_port or None)


@mcp.tool()
def connect_local_server(port: int, name: str = "local", protocol: str = "tcp") -> str:
    """Connect ATAK to a TAK server running on the host machine.

    USB device: sets up an adb reverse tunnel and connects to 127.0.0.1:<port>.
    Emulator: connects to 10.0.2.2:<port> (the host alias). protocol = tcp|ssl|quic.
    """
    return bridge.connect_local_server(port, name, protocol)


@mcp.tool()
def fix_opengl() -> str:
    """Disable ATAK's OpenGL map rendering (emulator render-crash fix). Restart ATAK to apply."""
    return bridge.fix_opengl()


@mcp.tool()
def fix_audio_input(avd: str = "") -> str:
    """Enable host-mic passthrough in an emulator AVD (hw.audioInput=yes). Restart the emulator."""
    return bridge.fix_audio_input(avd or None)


@mcp.tool()
def init_maps(tag: str = "v1.5.0") -> str:
    """Install ATAK-Maps custom map sources so a blank-map ATAK has a basemap.

    Downloads the ATAK-Maps release and pushes its map-source XMLs into ATAK.
    Reopen the map-source picker (or restart ATAK) and choose one, e.g. Bing_Satellite.
    """
    return json.dumps(bridge.init_maps(tag=tag), indent=2, ensure_ascii=False)


@mcp.tool()
def list_map_sources() -> str:
    """List the map-source XMLs installed in ATAK's mobile map-sources dir."""
    return "\n".join(bridge.list_map_sources()) or "(none)"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
