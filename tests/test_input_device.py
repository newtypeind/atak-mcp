# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Input argv builders, the screenshot PNG-banner strip, and log filtering."""

import pytest

from atak_mcp.bridge import (
    AdbError, clear_text, crashes, key, logcat, screenshot, swipe,
    tap_xy, text_input,
)
from atak_mcp.bridge.ui import _PNG_MAGIC


def test_input_argv_builders(fake_run):
    tap_xy(5, 6)
    assert fake_run.last_args == ["shell", "input", "tap", "5", "6"]
    swipe(1, 2, 3, 4, 300)
    assert fake_run.last_args == ["shell", "input", "swipe", "1", "2", "3", "4", "300"]
    text_input("a b")                       # spaces encoded as %s
    assert fake_run.last_args == ["shell", "input", "text", "a%sb"]


def test_key_named_and_numeric(fake_run):
    key("BACK")
    assert fake_run.last_args == ["shell", "input", "keyevent", "KEYCODE_BACK"]
    key("67")
    assert fake_run.last_args == ["shell", "input", "keyevent", "67"]


def test_clear_text_batches_deletes(fake_run):
    clear_text(3)
    # first call moves to end, second sends a batch of DEL (67) keyevents
    assert fake_run.calls[-2][1:] == ["shell", "input", "keyevent", "KEYCODE_MOVE_END"]
    assert fake_run.last_args == ["shell", "input", "keyevent", "67", "67", "67"]


def test_screenshot_strips_foldable_banner(fake_run, tmp_path):
    png = _PNG_MAGIC + b"bodybytes"
    fake_run.stdout = b"[Warning] Multiple displays were found\n" + png
    out = tmp_path / "s.png"
    screenshot(str(out))
    assert out.read_bytes() == png          # banner sliced off at the PNG signature


def test_screenshot_no_png_raises(fake_run, tmp_path):
    fake_run.stdout = b"no image here"
    with pytest.raises(AdbError):
        screenshot(str(tmp_path / "s.png"))


def test_logcat_grep_filters(fake_run):
    fake_run.stdout = b"line ABC\nline xyz\n"
    assert logcat(grep="abc") == "line ABC"


def test_crashes_filters_by_package(fake_run):
    fake_run.stdout = b"com.x boom\nunrelated\ncom.x trace\n"
    assert crashes("com.x") == "com.x boom\ncom.x trace"
