# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Install custom map sources so a fresh ATAK has a usable basemap.

ATAK ships no usable basemap outside its US bundle, so a clean install (or an
emulator) can show a blank map. ``init_maps`` downloads the open-source
ATAK-Maps release (custom map-source XMLs: Bing / Google / ESRI / ...) and
pushes them into ATAK's mobile map-sources directory, where they show up in the
map-source picker. Reopen the picker (or restart ATAK) and choose one, e.g.
``Bing_Satellite``.
"""

from __future__ import annotations

import io
import os
import tempfile
import urllib.request
import zipfile
from typing import Optional

from ._adb import adb
from .device import push

__all__ = ["init_maps", "list_map_sources", "ATAK_MAPSOURCE_DIR"]

# Where ATAK keeps imported "mobile" (tile/WMS) map-source definitions.
ATAK_MAPSOURCE_DIR = "/sdcard/atak/imagery/mobile/mapsources"

_ATAK_MAPS_URL = (
    "https://github.com/joshuafuller/ATAK-Maps/releases/download/{tag}/atak-maps-{ver}.zip"
)


def _default_url(tag: str) -> str:
    return _ATAK_MAPS_URL.format(tag=tag, ver=tag.lstrip("v"))


def init_maps(
    serial: Optional[str] = None,
    tag: str = "v1.5.0",
    url: Optional[str] = None,
    timeout: float = 60.0,
) -> dict:
    """Download the ATAK-Maps custom map sources and install them on the device.

    Pushes each ``<customMapSource>`` XML into :data:`ATAK_MAPSOURCE_DIR`.
    Restart ATAK (or reopen the map-source picker) and choose a source. Returns
    ``{installed, dir, sources}``. ``url`` overrides the default release asset
    derived from ``tag``.
    """
    url = url or _default_url(tag)
    req = urllib.request.Request(url, headers={"User-Agent": "atak-mcp"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()

    adb(["shell", "mkdir", "-p", ATAK_MAPSOURCE_DIR], serial, check=False)
    installed: list[str] = []
    with tempfile.TemporaryDirectory() as td, zipfile.ZipFile(io.BytesIO(data)) as z:
        for entry in z.namelist():
            if not entry.lower().endswith(".xml"):
                continue
            base = os.path.basename(entry)
            local = os.path.join(td, base)
            with open(local, "wb") as fh:
                fh.write(z.read(entry))
            push(local, f"{ATAK_MAPSOURCE_DIR}/{base}", serial)
            installed.append(base)

    return {
        "installed": len(installed),
        "dir": ATAK_MAPSOURCE_DIR,
        "sources": sorted(installed),
    }


def list_map_sources(serial: Optional[str] = None) -> list[str]:
    """List the map-source XMLs currently in ATAK's mobile map-sources dir."""
    out = adb(["shell", "ls", "-1", ATAK_MAPSOURCE_DIR], serial, check=False)
    return [l.strip() for l in out.splitlines() if l.strip().lower().endswith(".xml")]
