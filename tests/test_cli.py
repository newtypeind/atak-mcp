# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""CLI argument parsing and dispatch (bridge/update calls monkeypatched)."""

import pytest

from atak_mcp import cli
from atak_mcp.bridge import AdbError


def test_update_check_dispatch(monkeypatch, capsys):
    monkeypatch.setattr("atak_mcp.cli.update.check_update",
                        lambda: {"current": "0.2.0", "latest": "v0.2.0"})
    assert cli.main(["update-check"]) == 0
    assert "0.2.0" in capsys.readouterr().out


def test_servers_dispatch(monkeypatch, capsys):
    monkeypatch.setattr("atak_mcp.cli.bridge.list_servers", lambda s: [{"name": "hq"}])
    assert cli.main(["servers"]) == 0
    assert "hq" in capsys.readouterr().out


def test_tap_xy_dispatch(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("atak_mcp.cli.bridge.tap_xy", lambda x, y, s: calls.append((x, y)))
    assert cli.main(["tap", "--xy", "5", "6"]) == 0
    assert calls == [(5, 6)]
    assert "tapped (5,6)" in capsys.readouterr().out


def test_version_flag_exits():
    with pytest.raises(SystemExit):
        cli.main(["--version"])


def test_adb_error_returns_exit_1(monkeypatch):
    def boom(s):
        raise AdbError("no device")
    monkeypatch.setattr("atak_mcp.cli.bridge.list_servers", boom)
    assert cli.main(["servers"]) == 1


def test_serial_flag_reaches_bridge(monkeypatch):
    captured = {}

    def fake_list_servers(s):
        captured["s"] = s
        return [{"name": "hq"}]

    monkeypatch.setattr("atak_mcp.cli.bridge.list_servers", fake_list_servers)
    assert cli.main(["--serial", "emulator-5554", "servers"]) == 0
    assert captured["s"] == "emulator-5554"
