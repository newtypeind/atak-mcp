# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""adb runner and device-list parsing (mock the subprocess boundary)."""

import pytest

from atak_mcp.bridge import AdbError, adb, devices

DEVICES_OUT = (
    "List of devices attached\n"
    "R3CRB0C3YPV            device usb:0-1 product:b2qksx model:SM_F711N device:b2q transport_id:2\n"
    "\n"
)


def test_devices_parsing(fake_run):
    fake_run.stdout = DEVICES_OUT.encode()
    rows = devices()
    assert rows == [{
        "serial": "R3CRB0C3YPV", "state": "device", "usb": "0-1",
        "product": "b2qksx", "model": "SM_F711N", "device": "b2q",
        "transport_id": "2",
    }]
    assert fake_run.last_args == ["devices", "-l"]


def test_adb_decodes_str_and_bytes(fake_run):
    fake_run.stdout = b"hello"
    assert adb(["shell", "echo", "hi"]) == "hello"
    assert adb(["exec-out", "screencap"], binary=True) == b"hello"


def test_adb_raises_on_failure(fake_run):
    fake_run.returncode = 1
    with pytest.raises(AdbError):
        adb(["shell", "false"])


def test_adb_check_false_swallows_failure(fake_run):
    fake_run.returncode = 1
    assert adb(["shell", "false"], check=False) == ""


def test_adb_inserts_serial_flag(fake_run):
    # an explicit serial targets one device among several via `adb -s <serial>`
    adb(["shell", "echo", "hi"], "emulator-5554")
    assert fake_run.last == ["adb", "-s", "emulator-5554", "shell", "echo", "hi"]


def test_adb_serial_from_env_when_unset(fake_run, monkeypatch):
    # with no explicit serial, fall back to $ANDROID_SERIAL
    monkeypatch.setenv("ANDROID_SERIAL", "R3CRB0C3YPV")
    adb(["shell", "echo", "hi"])
    assert fake_run.last[:3] == ["adb", "-s", "R3CRB0C3YPV"]


def test_adb_no_serial_flag_by_default(fake_run):
    adb(["shell", "echo", "hi"])
    assert fake_run.last == ["adb", "shell", "echo", "hi"]
