# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Server-list parsing logic (no device): connectString + row pairing."""

from atak_mcp.bridge import servers
from atak_mcp.bridge.ui import Node


def _node(rid: str, text: str = "", checked: bool = False, cy: int = 0) -> Node:
    """A node whose resource id ends with ``rid`` and whose centre y is ``cy``."""
    return Node(resource_id=f"com.atakmap.app.civ:id/{rid}", text=text,
                checked=checked, bounds=(0, 0, 0, 2 * cy))


def test_parse_connstr():
    assert servers._parse_connstr("1.2.3.4:8087:tcp") == ("1.2.3.4", "8087", "tcp")
    assert servers._parse_connstr("host:8089:ssl") == ("host", "8089", "ssl")
    assert servers._parse_connstr("host:8089") == ("host", "8089", "")
    assert servers._parse_connstr("weird") == ("weird", "", "")


def test_on_server_screen_marker():
    assert servers._on_server_screen([_node("netlist")]) is True
    assert servers._on_server_screen([_node("manage_ports_description", "hq")]) is False
    assert servers._on_server_screen([]) is False


def test_server_rows_pairs_two_rows():
    nodes = [
        # row A (top): an enabled tcp connection, no error line
        _node("manage_ports_description", "hq", cy=300),
        _node("manage_ports_connection_string", "1.2.3.4:8087:tcp", cy=380),
        _node("manage_ports_checkbox", checked=True, cy=310),
        _node("manage_ports_delete", cy=510),
        _node("manage_ports_edit", cy=510),
        # row B (bottom): a disabled ssl connection with an error line
        _node("manage_ports_description", "alt", cy=700),
        _node("manage_ports_connection_string", "5.6.7.8:8089:ssl", cy=780),
        _node("manage_ports_checkbox", checked=False, cy=710),
        _node("manage_ports_delete", cy=910),
        _node("manage_ports_edit", cy=910),
        _node("manage_ports_error_string", "connection timed out", cy=820),
    ]
    rows = servers._server_rows(nodes)
    assert len(rows) == 2

    a, b = rows
    assert (a["name"], a["host"], a["port"], a["protocol"]) == ("hq", "1.2.3.4", "8087", "tcp")
    assert a["enabled"] is True
    assert a["status"] == ""                       # no error near row A

    assert (b["name"], b["protocol"], b["enabled"]) == ("alt", "ssl", False)
    assert b["status"] == "connection timed out"   # error matched to the nearer row


def test_server_rows_empty():
    assert servers._server_rows([_node("netlist")]) == []
