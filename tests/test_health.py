# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""doctor's device-selection helper (pure logic; the rest of doctor is device-only)."""

from atak_mcp.bridge.health import _select_device

DEVS = [
    {"serial": "A", "state": "device"},
    {"serial": "B", "state": "device"},
]


def test_select_device_by_serial():
    assert _select_device(DEVS, "B") == {"serial": "B", "state": "device"}


def test_select_device_defaults_to_first_when_no_target():
    assert _select_device(DEVS, None)["serial"] == "A"


def test_select_device_missing_target_is_none():
    # a serial that is not attached resolves to None, so doctor reports the miss
    assert _select_device(DEVS, "ZZZ") is None


def test_select_device_empty_list_is_none():
    assert _select_device([], None) is None
    assert _select_device([], "A") is None
