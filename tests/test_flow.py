# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""End-to-end-ish (still device-free) flow: dump -> find -> tap.

A responder feeds a canned UI tree back through the uiautomator dump/cat path, so
this exercises ui_xml + dump + find + _resolve + tap in one go without a device.
"""

from atak_mcp.bridge import tap, wait_for

_XML = (
    "<?xml version='1.0' encoding='UTF-8'?>"
    "<hierarchy rotation=\"0\">"
    "<node text=\"Record\" resource-id=\"com.x:id/rec\" content-desc=\"\" "
    "class=\"android.widget.Button\" package=\"com.x\" clickable=\"true\" "
    "enabled=\"true\" checked=\"false\" bounds=\"[100,200][300,260]\" />"
    "</hierarchy>"
)


def _ui_responder(argv):
    a = argv[1:]  # strip the leading 'adb'
    if "uiautomator" in a and "dump" in a:
        return b"UI hierchary dumped to: /sdcard/atak_mcp_dump.xml", 0
    if a[:1] == ["exec-out"] and "cat" in a:
        return _XML.encode(), 0
    return b"", 0


def test_tap_resolves_node_via_dump(fake_run):
    fake_run.responder = _ui_responder
    node = tap("Record", by="text")
    assert node.text == "Record"
    # the final shell call taps the centre of the node's bounds: (200, 230)
    assert fake_run.last_args == ["shell", "input", "tap", "200", "230"]


def test_wait_for_returns_on_first_match(fake_run):
    fake_run.responder = _ui_responder
    node = wait_for("Record", by="text", timeout=2.0)
    assert node.resource_id == "com.x:id/rec"
