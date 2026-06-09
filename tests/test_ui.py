# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Pure-logic tests for the uiautomator tree: parse_nodes, Node, find."""

from atak_mcp.bridge.ui import Node, find, parse_nodes

SAMPLE_XML = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node text="Record" resource-id="com.x:id/rec" content-desc="" class="android.widget.Button" package="com.x" clickable="true" enabled="true" checked="false" bounds="[100,200][300,260]" />
  <node text="" resource-id="com.x:id/box" content-desc="toggle" class="android.widget.CheckBox" package="com.x" clickable="true" enabled="true" checked="true" bounds="[10,10][40,40]" />
  <node text="Hidden" resource-id="" content-desc="" class="android.widget.TextView" package="com.x" clickable="false" enabled="true" checked="false" bounds="[0,0][0,0]" />
</hierarchy>"""


def test_parse_nodes_basic():
    nodes = parse_nodes(SAMPLE_XML)
    assert len(nodes) == 3
    rec = nodes[0]
    assert rec.text == "Record"
    assert rec.resource_id == "com.x:id/rec"
    assert rec.clickable is True
    assert rec.checked is False
    assert rec.bounds == (100, 200, 300, 260)
    assert nodes[1].checked is True            # the checkbox
    assert nodes[1].content_desc == "toggle"


def test_node_center_and_label():
    n = parse_nodes(SAMPLE_XML)[0]
    assert n.center == (200, 230)              # midpoint of the bounds
    assert n.label() == "Record"               # text wins
    # falls back to content_desc when there is no text
    box = parse_nodes(SAMPLE_XML)[1]
    assert box.label() == "toggle"


def test_node_as_dict_includes_center():
    d = parse_nodes(SAMPLE_XML)[0].as_dict()
    assert d["center"] == (200, 230)
    assert d["text"] == "Record"


def test_find_by_each_field():
    nodes = parse_nodes(SAMPLE_XML)
    assert [n.text for n in find("record", by="text", nodes=nodes)] == ["Record"]
    assert [n.resource_id for n in find("box", by="id", nodes=nodes)] == ["com.x:id/box"]
    assert find("toggle", by="desc", nodes=nodes)[0].content_desc == "toggle"
    # 'any' searches text, id, and desc
    assert len(find("rec", by="any", nodes=nodes)) == 1


def test_find_exact_vs_contains():
    nodes = parse_nodes(SAMPLE_XML)
    assert find("Rec", by="text", exact=True, nodes=nodes) == []
    assert len(find("Rec", by="text", exact=False, nodes=nodes)) == 1
    assert len(find("Record", by="text", exact=True, nodes=nodes)) == 1


def test_find_visible_only_and_clickable_only():
    nodes = parse_nodes(SAMPLE_XML)
    # the "Hidden" node has zero width, so visible_only (default) drops it
    assert find("Hidden", nodes=nodes) == []
    assert len(find("Hidden", visible_only=False, nodes=nodes)) == 1
    # clickable_only filters the non-clickable Hidden node out
    assert find("Hidden", visible_only=False, clickable_only=True, nodes=nodes) == []


def test_node_defaults():
    n = Node()
    assert n.center == (0, 0)
    assert n.clickable is False and n.checked is False
