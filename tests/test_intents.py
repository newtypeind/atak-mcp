# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Deep-link URI construction and broadcast argv (mock the subprocess boundary)."""

import pytest

from atak_mcp.bridge import AdbError, broadcast, deep_link, enroll, import_url


def _shell_cmd(fake_run):
    # deep links go out as a single device-shell string: ["shell", "am start ..."]
    args = fake_run.last_args
    assert args[0] == "shell"
    return args[1]


def test_deep_link_single_quotes_uri_for_device_shell(fake_run):
    deep_link("tak://x/y?a=1&b=2", verify=False)
    cmd = _shell_cmd(fake_run)
    # the & must survive inside single quotes so the device shell keeps the query
    assert cmd == "am start -a android.intent.action.VIEW -d 'tak://x/y?a=1&b=2'"


def test_enroll_encodes_values_keeps_param_separators(fake_run):
    enroll("1.2.3.4:8087:tcp", "alice", "s&t", verify=False)
    cmd = _shell_cmd(fake_run)
    assert "tak://com.atakmap.app/enroll?" in cmd
    assert "host=1.2.3.4%3A8087%3Atcp" in cmd   # value encoded
    assert "&username=alice" in cmd             # separator kept
    assert "&token=s%26t" in cmd                # & inside value encoded


def test_import_url_encodes_url(fake_run):
    import_url("https://h/x.zip?a=1&b=2", verify=False)
    cmd = _shell_cmd(fake_run)
    assert cmd.startswith("am start -a android.intent.action.VIEW -d 'tak://com.atakmap.app/import?url=")
    assert "https%3A%2F%2Fh%2Fx.zip%3Fa%3D1%26b%3D2" in cmd


def test_broadcast_maps_typed_extras(fake_run):
    broadcast("com.x.ACT", None, [("s", "k", "v"), ("z", "b", "true"), ("i", "n", "3")])
    assert fake_run.last_args == [
        "shell", "am", "broadcast", "-a", "com.x.ACT",
        "--es", "k", "v", "--ez", "b", "true", "--ei", "n", "3",
    ]


def test_broadcast_component_and_bad_extra(fake_run):
    broadcast("ACT", "pkg/.Recv")
    assert fake_run.last_args[:6] == ["shell", "am", "broadcast", "-a", "ACT", "-n"]
    with pytest.raises(AdbError):
        broadcast("ACT", extras=[("q", "k", "v")])  # unknown extra type
