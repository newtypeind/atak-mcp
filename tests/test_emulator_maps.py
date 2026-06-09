# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Emulator render/mic fixes and the map-source installer."""

import io
import zipfile

import pytest

from atak_mcp.bridge import emulator, maps


# --------------------------------------------------------------------------- #
# emulator fixes
# --------------------------------------------------------------------------- #
def test_fix_opengl_touches_marker(fake_run):
    emulator.fix_opengl()
    # last call creates the marker ATAK reads to disable GL
    assert fake_run.last_args == ["shell", "touch", "/sdcard/atak/opengl.broken"]


def test_fix_audio_input_adds_line(tmp_path):
    avd_home = tmp_path / "avd"
    cfg = avd_home / "Pixel.avd"
    cfg.mkdir(parents=True)
    (cfg / "config.ini").write_text("hw.gpu.enabled=yes\nhw.ramSize=2048\n")

    msg = emulator.fix_audio_input(avd="Pixel", avd_home=str(avd_home))

    body = (cfg / "config.ini").read_text()
    assert "hw.audioInput=yes" in body
    assert "Pixel" in msg


def test_fix_audio_input_replaces_existing(tmp_path):
    avd_home = tmp_path / "avd"
    cfg = avd_home / "Pixel.avd"
    cfg.mkdir(parents=True)
    (cfg / "config.ini").write_text("hw.audioInput=no\n")

    emulator.fix_audio_input(avd="Pixel", avd_home=str(avd_home))

    body = (cfg / "config.ini").read_text().splitlines()
    assert body.count("hw.audioInput=yes") == 1
    assert "hw.audioInput=no" not in body


def test_fix_audio_input_missing_avd(tmp_path):
    with pytest.raises(Exception):
        emulator.fix_audio_input(avd="Nope", avd_home=str(tmp_path))


# --------------------------------------------------------------------------- #
# map installer
# --------------------------------------------------------------------------- #
def _zip_with(*names) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, b"<customMapSource/>")
    return buf.getvalue()


def test_init_maps_pushes_only_xmls(monkeypatch):
    data = _zip_with("Bing/Bing_Satellite.xml", "Google/google_hybrid.xml", "README.txt")

    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): self.close()

    monkeypatch.setattr(maps.urllib.request, "urlopen", lambda *a, **k: _Resp(data))
    monkeypatch.setattr(maps, "adb", lambda *a, **k: "")  # mkdir
    pushed = []
    monkeypatch.setattr(maps, "push", lambda local, remote, serial=None: pushed.append(remote))

    result = maps.init_maps()

    assert result["installed"] == 2
    assert sorted(r.split("/")[-1] for r in pushed) == ["Bing_Satellite.xml", "google_hybrid.xml"]
    assert all(p.startswith(maps.ATAK_MAPSOURCE_DIR) for p in pushed)


def test_list_map_sources_filters_xml(monkeypatch):
    monkeypatch.setattr(maps, "adb", lambda *a, **k: "Bing_Satellite.xml\nnotes.txt\nesri.xml\n")
    assert maps.list_map_sources() == ["Bing_Satellite.xml", "esri.xml"]


def test_default_url_derives_from_tag():
    assert maps._default_url("v1.5.0").endswith("/v1.5.0/atak-maps-1.5.0.zip")
