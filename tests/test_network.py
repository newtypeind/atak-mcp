# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Emulator detection, adb reverse, and the connect-local-server composition."""

from atak_mcp.bridge import device, servers


def test_is_emulator_false_for_physical(fake_run):
    fake_run.stdout = b""  # getprop returns nothing -> not an emulator
    assert device.is_emulator() is False


def test_is_emulator_true_via_qemu_prop(fake_run):
    def responder(argv):
        return (b"1", 0) if "ro.kernel.qemu" in argv else (b"", 0)
    fake_run.responder = responder
    assert device.is_emulator() is True


def test_reverse_argv(fake_run):
    device.reverse(8080)
    assert fake_run.last_args == ["reverse", "tcp:8080", "tcp:8080"]
    device.reverse(8080, 9090)
    assert fake_run.last_args == ["reverse", "tcp:8080", "tcp:9090"]


def test_host_address(monkeypatch):
    monkeypatch.setattr(device, "is_emulator", lambda serial=None: True)
    assert device.host_address() == "10.0.2.2"
    monkeypatch.setattr(device, "is_emulator", lambda serial=None: False)
    assert device.host_address() == "127.0.0.1"


def test_connect_local_usb_sets_up_reverse(monkeypatch):
    calls = {}
    monkeypatch.setattr(servers, "is_emulator", lambda serial=None: False)
    monkeypatch.setattr(servers, "host_address", lambda serial=None: "127.0.0.1")
    monkeypatch.setattr(servers, "reverse", lambda r, l, s=None: calls.setdefault("reverse", (r, l)))
    monkeypatch.setattr(servers, "add_server",
                        lambda name, host, port, protocol="tcp", serial=None: calls.setdefault(
                            "add", (name, host, port, protocol)) or "ok")

    out = servers.connect_local_server(8080)

    assert calls["reverse"] == (8080, 8080)              # tunnel set up for USB
    assert calls["add"] == ("local", "127.0.0.1", 8080, "tcp")
    assert "usb" in out


def test_connect_local_emulator_skips_reverse(monkeypatch):
    calls = {}
    monkeypatch.setattr(servers, "is_emulator", lambda serial=None: True)
    monkeypatch.setattr(servers, "host_address", lambda serial=None: "10.0.2.2")
    monkeypatch.setattr(servers, "reverse",
                        lambda *a, **k: calls.setdefault("reverse", True))
    monkeypatch.setattr(servers, "add_server",
                        lambda name, host, port, protocol="tcp", serial=None: calls.setdefault(
                            "add", (host, port)) or "ok")

    out = servers.connect_local_server(8080)

    assert "reverse" not in calls                        # emulator reaches host directly
    assert calls["add"] == ("10.0.2.2", 8080)
    assert "emulator" in out
