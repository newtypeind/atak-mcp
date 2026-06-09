# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""TAK server connection CRUD, driven through the Manage Server Connections UI.

The list and its add/edit form expose stable resource ids, so listing / adding /
editing / removing a TAK server connection is deterministic UI automation rather
than blind tapping. Plain TCP/SSL/QUIC streaming connections live here (the
``enroll`` deep link only covers SSL certificate enrollment against a reachable
server).

The slow step on a real device is the uiautomator dump, so the navigation
helpers below take care to minimise dumps: ``_open_server_connections`` returns
immediately if the list is already up, and ``_wait_tap`` taps the node from the
same dump that found it.
"""

from __future__ import annotations

import time
from typing import Optional

from ._adb import AdbError
from .input import clear_text, key, swipe, tap, tap_xy, text_input
from .ui import Node, dump, exists, find, wait_for

__all__ = [
    "list_servers", "add_server", "remove_server", "set_server_enabled",
    "edit_server",
]

_PROTO_RADIO = {"tcp": "tcp_radio", "ssl": "ssl_radio", "quic": "quic_radio"}


# --------------------------------------------------------------------------- #
# parsing the list rows
# --------------------------------------------------------------------------- #
def _ids(nodes: list[Node], suffix: str) -> list[Node]:
    return [n for n in nodes if n.resource_id.endswith("/" + suffix)]


def _parse_connstr(cs: str) -> tuple[str, str, str]:
    """Split a TAK connectString 'host:port:proto' into (host, port, proto)."""
    parts = cs.split(":")
    if len(parts) >= 3:
        return ":".join(parts[:-2]), parts[-2], parts[-1]
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return cs, "", ""


def _server_rows(nodes: list[Node]) -> list[dict]:
    """Pair up the per-row nodes of the server list into structured rows.

    Each row has exactly one description / connection_string / checkbox /
    delete / edit (paired by vertical order) and an optional error string
    (matched to the nearest row)."""
    by_y = lambda ns: sorted(ns, key=lambda n: n.center[1])
    descs = by_y(_ids(nodes, "manage_ports_description"))
    conns = by_y(_ids(nodes, "manage_ports_connection_string"))
    checks = by_y(_ids(nodes, "manage_ports_checkbox"))
    dels = by_y(_ids(nodes, "manage_ports_delete"))
    edits = by_y(_ids(nodes, "manage_ports_edit"))
    errs = _ids(nodes, "manage_ports_error_string")
    rows = []
    for i, conn in enumerate(conns):
        host, port, proto = _parse_connstr(conn.text)
        cy = conn.center[1]
        err = next((e.text for e in errs if abs(e.center[1] - cy) < 140), "")
        enabled = checks[i].checked if i < len(checks) else None
        rows.append({
            "name": descs[i].text if i < len(descs) else "",
            "connect_string": conn.text,
            "host": host, "port": port, "protocol": proto,
            "enabled": enabled,          # the on/off checkbox (reliable)
            "status": err or "",         # ATAK's status/error line; "" when idle/ok
            "_delete_xy": dels[i].center if i < len(dels) else None,
            "_edit_xy": edits[i].center if i < len(edits) else None,
            "_checkbox_xy": checks[i].center if i < len(checks) else None,
        })
    return rows


def _on_server_screen(nodes: list[Node]) -> bool:
    # `netlist` is the server-list ListView; it is the unambiguous marker.
    # ("My Primary IP Address" also shows on the Network Connection Prefs screen
    # one level up, so it cannot be used here.)
    return bool(_ids(nodes, "netlist"))


# --------------------------------------------------------------------------- #
# navigation (dump-frugal)
# --------------------------------------------------------------------------- #
def _scroll_to_text(query: str, max_swipes: int = 6, serial: Optional[str] = None) -> bool:
    """Scroll the ATAK menu down to ``query``. Blind-swipes the first couple of
    times (the target lives near the bottom) to save uiautomator dumps."""
    if exists(query, by="text", serial=serial):
        return True
    for i in range(max_swipes):
        swipe(2100, 950, 2100, 430, 250, serial)
        time.sleep(0.25)
        if i >= 2 and exists(query, by="text", serial=serial):  # 3 blind swipes first
            return True
    return exists(query, by="text", serial=serial)


def _wait_tap(query: str, by: str = "text", timeout: float = 8.0,
              serial: Optional[str] = None) -> None:
    """Poll until ``query`` appears, then tap it -- using the SAME dump that
    found it (no separate tap dump). Prefers a clickable match. This halves the
    dumps per screen transition, which matters: uiautomator dump is the slow
    step on a real device."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            matches = find(query, by=by, nodes=dump(serial, attempts=1))
            if matches:
                node = next((m for m in matches if m.clickable), matches[0])
                tap_xy(*node.center, serial=serial)
                return
        except AdbError as e:
            last = e
        time.sleep(0.5)
    raise AdbError(f"timed out waiting to tap {query!r}; last={last}")


def _open_server_connections(serial: Optional[str] = None) -> None:
    """Navigate to ATAK's Manage Server Connections screen. Idempotent: returns
    at once if already there, so a list call followed by edit/remove does not
    re-walk Settings. Fast path covers the two common start states (the map and
    the list); a slower cascade handles being mid-Settings."""
    nodes = dump(serial)
    if _on_server_screen(nodes):
        return
    menu = next((n for n in nodes
                 if n.resource_id.endswith("/tak_nav_menu_button")), None)
    if menu is not None:
        tap_xy(*menu.center, serial=serial)        # reuse the dump we just took
        time.sleep(0.8)
        if not _scroll_to_text("Settings", serial=serial):
            raise AdbError("could not find 'Settings' in the ATAK menu")
        _wait_tap("Settings", serial=serial)
        _wait_tap("Network Preferences", serial=serial)
        _wait_tap("Network Connection Preferences", serial=serial)
        _wait_tap("Manage Server Connections", serial=serial)
        wait_for("netlist", by="id", timeout=8, serial=serial)
        return
    # mid-Settings fallback: step toward the list one screen at a time.
    for _ in range(6):
        nodes = dump(serial)
        if _on_server_screen(nodes):
            return
        has = lambda t: any(t in (n.text or "") for n in nodes)
        if has("Manage Server Connections"):
            _wait_tap("Manage Server Connections", serial=serial)
            wait_for("netlist", by="id", timeout=8, serial=serial)
            return
        if has("Network Connection Preferences"):
            _wait_tap("Network Connection Preferences", serial=serial)
            wait_for("Manage Server Connections", timeout=8, serial=serial)
        elif has("Network Preferences"):
            _wait_tap("Network Preferences", serial=serial)
            wait_for("Network Connection Preferences", timeout=8, serial=serial)
        else:
            raise AdbError("cannot reach server connections from the current screen")
    raise AdbError("failed to reach Manage Server Connections")


def _find_row(name: str, serial: Optional[str]) -> dict:
    row = next((r for r in _server_rows(dump(serial)) if r["name"] == name), None)
    if not row:
        raise AdbError(f"no server connection named {name!r}")
    return row


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
def list_servers(serial: Optional[str] = None) -> list[dict]:
    """List ATAK's configured server connections with per-connection data:
    name, host, port, protocol, enabled, status. Returns rows that are on
    screen (scroll for very long lists is not yet handled)."""
    _open_server_connections(serial)
    time.sleep(0.4)
    return [
        {k: v for k, v in r.items() if not k.startswith("_")}
        for r in _server_rows(dump(serial))
    ]


def add_server(
    name: str,
    host: str,
    port,
    protocol: str = "tcp",
    serial: Optional[str] = None,
) -> str:
    """Add a streaming CoT (TAK server) connection via the Add form."""
    radio = _PROTO_RADIO.get(protocol.lower())
    if not radio:
        raise AdbError(f"protocol must be tcp|ssl|quic, got {protocol!r}")
    _open_server_connections(serial)
    tap("More options", by="desc", serial=serial)
    wait_for("Add", by="text", timeout=6, serial=serial)
    tap("Add", by="text", exact=True, serial=serial)
    wait_for("add_host", by="id", timeout=6, serial=serial)
    tap("add_description", by="id", serial=serial); time.sleep(0.4)
    text_input(name, serial)
    tap("add_host", by="id", serial=serial); time.sleep(0.4)
    text_input(host, serial)
    key("BACK", serial); time.sleep(0.6)            # hide keyboard
    tap("advanced_options_cb", by="id", serial=serial); time.sleep(0.6)
    tap(radio, by="id", serial=serial); time.sleep(0.4)   # sets default port
    tap("add_port", by="id", serial=serial); time.sleep(0.3)
    clear_text(8, serial); time.sleep(0.3)
    text_input(str(port), serial); time.sleep(0.3)
    key("BACK", serial); time.sleep(0.6)            # hide keyboard
    tap("add_net_button", by="id", serial=serial)   # OK
    time.sleep(1.0)
    return f"added {name!r} -> {host}:{port}:{protocol.lower()}"


def remove_server(name: str, serial: Optional[str] = None) -> str:
    """Remove the server connection named ``name`` (taps its delete + confirm)."""
    _open_server_connections(serial)
    row = _find_row(name, serial)
    tap_xy(*row["_delete_xy"], serial=serial)
    time.sleep(1.0)
    nodes = dump(serial)                       # one dump for the confirm dialog
    for label in ("Delete", "Yes", "OK", "Confirm"):
        m = [n for n in find(label, by="text", nodes=nodes, exact=True) if n.clickable]
        if m:
            tap_xy(*m[0].center, serial=serial)
            return f"removed {name!r}"
    return f"tapped delete for {name!r} (no confirm dialog appeared)"


def set_server_enabled(name: str, enabled: bool, serial: Optional[str] = None) -> str:
    """Enable/disable a server connection by toggling its checkbox."""
    _open_server_connections(serial)
    row = _find_row(name, serial)
    if row["enabled"] == enabled:
        return f"{name!r} already {'enabled' if enabled else 'disabled'}"
    tap_xy(*row["_checkbox_xy"], serial=serial)
    return f"{'enabled' if enabled else 'disabled'} {name!r}"


def edit_server(
    name: str,
    new_name: Optional[str] = None,
    new_host: Optional[str] = None,
    new_port=None,
    new_protocol: Optional[str] = None,
    serial: Optional[str] = None,
) -> str:
    """Edit a server connection: change any of name/host/port/protocol."""
    _open_server_connections(serial)
    row = _find_row(name, serial)
    tap_xy(*row["_edit_xy"], serial=serial)
    wait_for("add_host", by="id", timeout=6, serial=serial)
    typed = False
    if new_name is not None:
        tap("add_description", by="id", serial=serial); time.sleep(0.3)
        clear_text(40, serial); text_input(new_name, serial); typed = True
    if new_host is not None:
        tap("add_host", by="id", serial=serial); time.sleep(0.3)
        clear_text(60, serial); text_input(new_host, serial); typed = True
    if typed:                       # only dismiss the keyboard if we opened it;
        key("BACK", serial); time.sleep(0.5)   # a stray BACK closes the dialog
    if new_protocol is not None or new_port is not None:
        if not exists("add_port", by="id", serial=serial):
            tap("advanced_options_cb", by="id", serial=serial); time.sleep(0.5)
        if new_protocol is not None:
            radio = _PROTO_RADIO.get(new_protocol.lower())
            if not radio:
                raise AdbError(f"protocol must be tcp|ssl|quic, got {new_protocol!r}")
            tap(radio, by="id", serial=serial); time.sleep(0.3)
        if new_port is not None:
            tap("add_port", by="id", serial=serial); time.sleep(0.3)
            clear_text(8, serial); text_input(str(new_port), serial)
        key("BACK", serial); time.sleep(0.5)
    tap("add_net_button", by="id", serial=serial)
    time.sleep(1.0)
    return f"edited {name!r}"
