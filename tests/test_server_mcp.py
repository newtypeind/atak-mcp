# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""MCP layer: tool registration and a couple of call_tool round-trips.

Runs the real FastMCP tool dispatch in-process (no subprocess); bridge calls are
monkeypatched so nothing touches a device.
"""

from atak_mcp import __version__, server

EXPECTED_TOOLS = {
    "list_devices", "screenshot", "ui_dump", "find", "tap", "long_press",
    "enroll", "import_url", "deep_link", "list_servers", "add_server",
    "remove_server", "edit_server", "set_server_enabled", "doctor",
    "atak_version", "mcp_version", "check_update",
}


async def test_all_tools_registered():
    names = {t.name for t in await server.mcp.list_tools()}
    assert EXPECTED_TOOLS <= names, f"missing: {EXPECTED_TOOLS - names}"
    assert len(names) >= 48


async def test_call_tool_list_servers(monkeypatch):
    monkeypatch.setattr(server.bridge, "list_servers", lambda serial=None: [{"name": "hq"}])
    result = await server.mcp.call_tool("list_servers", {})
    assert "hq" in str(result)


async def test_call_tool_mcp_version():
    result = await server.mcp.call_tool("mcp_version", {})
    assert __version__ in str(result)


async def test_serial_passes_through_to_bridge(monkeypatch):
    # an explicit serial reaches the bridge so a tool targets a chosen device
    captured = {}

    def fake_tap(query, by="any", index=0, exact=False, scroll=False, serial=None):
        captured["serial"] = serial
        return server.bridge.Node(text="X", bounds=(0, 0, 10, 10))

    monkeypatch.setattr(server.bridge, "tap", fake_tap)
    await server.mcp.call_tool("tap", {"query": "X", "serial": "emulator-5554"})
    assert captured["serial"] == "emulator-5554"


async def test_empty_serial_becomes_none(monkeypatch):
    # the default empty-string serial is normalized to None (use default device)
    captured = {}

    def fake_list_servers(serial=None):
        captured["serial"] = serial
        return [{"name": "hq"}]

    monkeypatch.setattr(server.bridge, "list_servers", fake_list_servers)
    await server.mcp.call_tool("list_servers", {})
    assert captured["serial"] is None
