# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""logcat: full-buffer grep, post-filter line cap, and the ``since`` window.

The bug these guard against: the old implementation passed ``-t <lines>`` to
adb *before* grepping, so a marker older than the tail window was invisible.
"""

import atak_mcp.bridge as bridge


def _fake_adb(buffer_text, recorder):
    def fake(args, *a, **k):
        recorder.append(list(args))
        return buffer_text
    return fake


def test_grep_searches_full_buffer(monkeypatch):
    # Marker is on the FIRST line, followed by 400 filler lines.
    buf = "\n".join(
        ["01-01 00:00:00.000 I/OLD( 1): MARKER old event"]
        + [f"01-01 00:00:00.000 I/FLOOD( 1): filler {i}" for i in range(400)]
    )
    calls = []
    monkeypatch.setattr(bridge, "adb", _fake_adb(buf, calls))

    out = bridge.logcat(lines=200, grep="MARKER")

    # No -t: the whole ring buffer is dumped, then filtered.
    assert calls[0] == ["logcat", "-d", "-v", "time"]
    assert "MARKER old event" in out
    assert out.splitlines() == ["01-01 00:00:00.000 I/OLD( 1): MARKER old event"]


def test_lines_caps_after_filtering(monkeypatch):
    buf = "\n".join(f"line MARKER {i}" for i in range(10))
    monkeypatch.setattr(bridge, "adb", _fake_adb(buf, []))

    out = bridge.logcat(lines=3, grep="MARKER")

    # The last 3 *matches*, not a grep within the last 3 lines.
    assert out.splitlines() == ["line MARKER 7", "line MARKER 8", "line MARKER 9"]


def test_no_filter_uses_fast_tail_path(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge, "adb", _fake_adb("a\nb\nc\nd\ne", calls))

    out = bridge.logcat(lines=2)

    # With no grep/since, ask adb for just the last N lines.
    assert calls[0] == ["logcat", "-d", "-v", "time", "-t", "2"]
    assert out.splitlines() == ["d", "e"]


def test_since_count_maps_to_dash_t(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge, "adb", _fake_adb("x\ny\nz", calls))

    bridge.logcat(since="500")

    assert calls[0] == ["logcat", "-d", "-v", "time", "-t", "500"]


def test_since_timestamp_is_a_single_arg(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge, "adb", _fake_adb("hit MARKER", calls))

    bridge.logcat(since="06-05 09:00:00.000", grep="MARKER")

    # The space-bearing timestamp must reach adb as one argv token.
    assert calls[0] == ["logcat", "-d", "-v", "time", "-t", "06-05 09:00:00.000"]


def test_lines_zero_means_no_cap(monkeypatch):
    buf = "\n".join(f"MARKER {i}" for i in range(50))
    monkeypatch.setattr(bridge, "adb", _fake_adb(buf, []))

    out = bridge.logcat(lines=0, grep="MARKER")

    assert len(out.splitlines()) == 50


def test_backward_compatible_grep_and_lines(monkeypatch):
    # Existing callers pass only grep/lines; behaviour stays sane.
    monkeypatch.setattr(bridge, "adb", _fake_adb("foo\nMARKER hit\nbar", []))

    assert bridge.logcat(lines=200, grep="MARKER") == "MARKER hit"
