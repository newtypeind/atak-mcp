# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Self-version comparison and the update check (mock the network)."""

import io
import json
import urllib.error

from atak_mcp import update


def test_version_key_ordering():
    assert update._key("v0.1.1") == (0, 1, 1)
    assert update._key("0.2.0") == (0, 2, 0)
    assert update._key("0.2.0") > update._key("v0.1.1")
    assert update._key("1.0") == (1, 0, 0)        # missing parts pad with 0


def test_latest_version_picks_highest_tag(monkeypatch):
    payload = json.dumps([{"name": "v0.1.0"}, {"name": "v0.2.0"}, {"name": "v0.1.1"}]).encode()

    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): self.close()

    monkeypatch.setattr(update.urllib.request, "urlopen", lambda *a, **k: _Resp(payload))
    assert update.latest_version() == "v0.2.0"


def test_check_update_available(monkeypatch):
    monkeypatch.setattr(update, "latest_version", lambda timeout=5.0: "v99.0.0")
    r = update.check_update()
    assert r["update_available"] is True
    assert r["latest"] == "v99.0.0"
    assert r["current"] == update.current_version()


def test_check_update_up_to_date(monkeypatch):
    monkeypatch.setattr(update, "latest_version", lambda timeout=5.0: "v0.0.1")
    r = update.check_update()
    assert r["update_available"] is False
    assert r["hint"] == "up to date"


def test_check_update_offline_degrades(monkeypatch):
    def _boom(timeout=5.0):
        raise urllib.error.URLError("no network")
    monkeypatch.setattr(update, "latest_version", _boom)
    r = update.check_update()
    assert r["update_available"] is None
    assert "error" in r
