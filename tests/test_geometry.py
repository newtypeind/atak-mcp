# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Display geometry + normalized-coordinate conversion.

The tricky real-world case (Galaxy Z Flip3, app forced to landscape): ``wm
size`` reports the natural portrait 1080x2640 and the system rotation reads 0,
while screencap / uiautomator / ``input tap`` all live in 2640x1080. The
geometry resolver must prefer ``dumpsys display`` so it does not transpose.
"""

import struct

import atak_mcp.bridge as bridge

# Trimmed but faithful `dumpsys display` for the Z Flip3 in app-forced landscape.
DUMPSYS_LANDSCAPE = """
  mViewports=[DisplayViewport{type=INTERNAL, valid=true, isActive=true, displayId=0, uniqueId='local:1', physicalPort=130, orientation=1, logicalFrame=Rect(0, 0 - 2640, 1080), physicalFrame=Rect(0, 0 - 2640, 1080), deviceWidth=2640, deviceHeight=1080}, DisplayViewport{type=INTERNAL, valid=true, isActive=false, displayId=1, orientation=0, logicalFrame=Rect(0, 0 - 512, 260), deviceWidth=512, deviceHeight=260}]
    mBaseDisplayInfo=DisplayInfo{"Built-in Screen", real 1080 x 2640, rotation 0, density 480}
    mOverrideDisplayInfo=DisplayInfo{"Built-in Screen", real 2640 x 1080, rotation 1, density 480}
"""

DUMPSYS_PORTRAIT = """
  mViewports=[DisplayViewport{type=INTERNAL, valid=true, isActive=true, displayId=0, orientation=0, logicalFrame=Rect(0, 0 - 1080, 2340), deviceWidth=1080, deviceHeight=2340}]
"""

DUMPSYS_OVERRIDE_ONLY = (
    'mOverrideDisplayInfo=DisplayInfo{"Built-in Screen", real 2640 x 1080, rotation 1}'
)


def _png_header(w, h):
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = b"IHDR" + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00"
    return sig + struct.pack(">I", 13) + ihdr


# --------------------------------------------------------------------------- #
# parsers
# --------------------------------------------------------------------------- #
def test_parse_display_size_prefers_viewport():
    assert bridge._parse_display_size(DUMPSYS_LANDSCAPE) == (2640, 1080, "viewport")
    assert bridge._parse_display_size(DUMPSYS_PORTRAIT) == (1080, 2340, "viewport")


def test_parse_display_size_override_fallback():
    assert bridge._parse_display_size(DUMPSYS_OVERRIDE_ONLY) == (2640, 1080, "override")


def test_parse_display_size_unparseable():
    assert bridge._parse_display_size("no geometry here") == (0, 0, "")


def test_parse_rotation():
    assert bridge._parse_rotation(DUMPSYS_LANDSCAPE) == 1
    assert bridge._parse_rotation(DUMPSYS_PORTRAIT) == 0
    assert bridge._parse_rotation("nothing") is None


def test_parse_wm_size():
    assert bridge._parse_wm_size("Physical size: 1080x2640") == (1080, 2640)
    # Override beats Physical.
    assert bridge._parse_wm_size(
        "Physical size: 1080x2640\nOverride size: 1440x3200"
    ) == (1440, 3200)


def test_png_size():
    assert bridge._png_size(_png_header(2640, 1080)) == (2640, 1080)
    assert bridge._png_size(b"not a png at all") == (0, 0)


# --------------------------------------------------------------------------- #
# display_geometry (adb stubbed)
# --------------------------------------------------------------------------- #
def _stub_adb(display="", wm="", window="", user_rotation=""):
    def fake(args, *a, **k):
        joined = " ".join(args)
        if "dumpsys display" in joined:
            return display
        if "wm size" in joined:
            return wm
        if "dumpsys window" in joined:
            return window
        if "user_rotation" in joined:
            return user_rotation
        return ""
    return fake


def test_display_geometry_app_forced_landscape(monkeypatch):
    monkeypatch.setattr(
        bridge, "adb",
        _stub_adb(display=DUMPSYS_LANDSCAPE, wm="Physical size: 1080x2640"),
    )
    g = bridge.display_geometry()
    # Current-rotation pixel space matches screencap/tap, NOT the transposed wm size.
    assert (g["width"], g["height"]) == (2640, 1080)
    assert g["rotation"] == 1
    assert (g["wm_width"], g["wm_height"]) == (1080, 2640)
    assert g["source"] == "viewport"


def test_display_geometry_falls_back_to_wm_size(monkeypatch):
    monkeypatch.setattr(
        bridge, "adb",
        _stub_adb(display="garbage", wm="Physical size: 1080x2340", window="mRotation=0"),
    )
    g = bridge.display_geometry()
    assert (g["width"], g["height"]) == (1080, 2340)  # portrait, rotation 0
    assert g["source"] == "wm_size"


def test_display_geometry_wm_fallback_landscape(monkeypatch):
    monkeypatch.setattr(
        bridge, "adb",
        _stub_adb(display="garbage", wm="Physical size: 1080x2340", window="mRotation=1"),
    )
    g = bridge.display_geometry()
    assert (g["width"], g["height"]) == (2340, 1080)  # rotation 1 -> landscape


# --------------------------------------------------------------------------- #
# normalized -> pixel conversion
# --------------------------------------------------------------------------- #
def test_to_px_passthrough():
    assert bridge._to_px(100, 200, norm=False) == (100, 200)


def test_to_px_normalized(monkeypatch):
    monkeypatch.setattr(bridge, "device_size", lambda serial=None: (2640, 1080))
    assert bridge._to_px(0.5, 0.5, norm=True) == (1320, 540)
    assert bridge._to_px(0.0, 0.0, norm=True) == (0, 0)
    # 1.0 clamps to the last addressable pixel.
    assert bridge._to_px(1.0, 1.0, norm=True) == (2639, 1079)


def test_tap_xy_normalized(monkeypatch):
    monkeypatch.setattr(bridge, "device_size", lambda serial=None: (2640, 1080))
    calls = []
    monkeypatch.setattr(bridge, "adb", lambda args, *a, **k: calls.append(list(args)))
    assert bridge.tap_xy(0.5, 0.5, norm=True) == (1320, 540)
    assert calls[0] == ["shell", "input", "tap", "1320", "540"]


def test_swipe_normalized(monkeypatch):
    monkeypatch.setattr(bridge, "device_size", lambda serial=None: (2640, 1080))
    calls = []
    monkeypatch.setattr(bridge, "adb", lambda args, *a, **k: calls.append(list(args)))
    p1, p2 = bridge.swipe(0.1, 0.2, 0.8, 0.9, 300, norm=True)
    assert p1 == (264, 216) and p2 == (2112, 972)
    assert calls[0] == ["shell", "input", "swipe", "264", "216", "2112", "972", "300"]
