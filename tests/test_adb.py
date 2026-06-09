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
