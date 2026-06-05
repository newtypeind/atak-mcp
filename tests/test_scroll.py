# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""scroll_into_view: container selection and the bounded auto-scroll loop."""

import pytest

import atak_mcp.bridge as bridge
from atak_mcp.bridge import Node


def _node(text="", scrollable=False, bounds=(0, 0, 0, 0)):
    return Node(text=text, scrollable=scrollable, bounds=bounds)


def test_parse_nodes_reads_scrollable():
    xml = (
        '<hierarchy><node bounds="[0,0][100,100]" scrollable="true" text="x"/>'
        '<node bounds="[0,0][10,10]" scrollable="false" text="y"/></hierarchy>'
    )
    nodes = bridge.parse_nodes(xml)
    assert nodes[0].scrollable is True
    assert nodes[1].scrollable is False
    assert nodes[0].as_dict()["scrollable"] is True


def test_largest_scrollable_picks_biggest_area():
    nodes = [
        _node(scrollable=True, bounds=(0, 0, 100, 100)),
        _node(scrollable=True, bounds=(0, 0, 500, 500)),
        _node(scrollable=False, bounds=(0, 0, 9999, 9999)),  # big but not scrollable
    ]
    assert bridge._largest_scrollable(nodes).bounds == (0, 0, 500, 500)


def test_largest_scrollable_none_when_absent():
    assert bridge._largest_scrollable([_node(bounds=(0, 0, 10, 10))]) is None


def test_returns_immediately_when_already_visible(monkeypatch):
    target = _node(text="HERE", bounds=(0, 0, 100, 60))
    monkeypatch.setattr(bridge, "dump", lambda serial=None: [target])
    swipes = {"n": 0}
    monkeypatch.setattr(bridge, "swipe", lambda *a, **k: swipes.__setitem__("n", swipes["n"] + 1))
    monkeypatch.setattr(bridge.time, "sleep", lambda *a, **k: None)

    node = bridge.scroll_into_view("HERE")

    assert node.text == "HERE"
    assert swipes["n"] == 0  # no scrolling needed


def test_finds_after_scrolling(monkeypatch):
    container = _node(scrollable=True, bounds=(0, 0, 1000, 1000))
    target = _node(text="TARGET", bounds=(0, 500, 1000, 560))
    screens = [
        [container, _node(text="A", bounds=(0, 0, 100, 60))],  # initial (top)
        [container, _node(text="B", bounds=(0, 0, 100, 60))],  # after swipe 1
        [container, _node(text="C", bounds=(0, 0, 100, 60))],  # after swipe 2
        [container, target],                                   # after swipe 3 -> found
    ]
    state = {"i": 0, "swipes": 0}
    monkeypatch.setattr(bridge, "dump", lambda serial=None: screens[min(state["i"], len(screens) - 1)])

    def fake_swipe(*a, **k):
        state["swipes"] += 1
        state["i"] += 1
    monkeypatch.setattr(bridge, "swipe", fake_swipe)
    monkeypatch.setattr(bridge.time, "sleep", lambda *a, **k: None)

    node = bridge.scroll_into_view("TARGET")

    assert node.text == "TARGET"
    assert state["swipes"] == 3


def test_taps_when_requested(monkeypatch):
    target = _node(text="HERE", bounds=(10, 10, 110, 70))
    monkeypatch.setattr(bridge, "dump", lambda serial=None: [target])
    tapped = {}
    monkeypatch.setattr(bridge, "tap_xy", lambda x, y, serial=None, **k: tapped.update(xy=(x, y)))
    monkeypatch.setattr(bridge.time, "sleep", lambda *a, **k: None)

    bridge.scroll_into_view("HERE", do_tap=True)

    assert tapped["xy"] == (60, 40)  # centre of the bounds


def test_raises_when_never_found(monkeypatch):
    container = _node(scrollable=True, bounds=(0, 0, 1000, 1000))
    static = [container, _node(text="A", bounds=(0, 0, 100, 60))]
    monkeypatch.setattr(bridge, "dump", lambda serial=None: static)  # never changes
    monkeypatch.setattr(bridge, "swipe", lambda *a, **k: None)
    monkeypatch.setattr(bridge.time, "sleep", lambda *a, **k: None)

    with pytest.raises(bridge.AdbError):
        bridge.scroll_into_view("NOPE", max_swipes=5)
