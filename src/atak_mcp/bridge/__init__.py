# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""adb bridge for driving ATAK (and any Android UI) deterministically.

Pure standard library, so the bridge runs anywhere adb runs. The design goal is
to never tap blindly: callers locate a node by its text, resource id, or content
description (read from the uiautomator hierarchy) and act on the centre of that
node's bounds.

The package is split by concern; this module re-exports the public API so
callers (``cli.py``, ``server.py``) keep using a flat ``bridge.<name>``:

  * ``_adb``    - low-level adb runner, devices, shared constants, AdbError
  * ``ui``      - screenshot + uiautomator tree (Node, dump, find, wait_for, ...)
  * ``input``   - taps, gestures, text entry
  * ``device``  - power/lifecycle, packages, permissions, files, logs, diagnostics
  * ``intents`` - broadcasts and ``tak:`` deep links (enroll, import_url, ...)
  * ``plugins``  - install/reload lifecycle and dev-loop composites
  * ``health``   - version detection and the ``doctor`` check
  * ``servers``  - TAK server connection CRUD (Manage Server Connections UI)
  * ``emulator`` - emulator render/mic troubleshooting
  * ``maps``     - install custom map sources for a usable basemap
"""

from __future__ import annotations

from ._adb import *  # noqa: F401,F403
from .ui import *  # noqa: F401,F403
from .input import *  # noqa: F401,F403
from .device import *  # noqa: F401,F403
from .intents import *  # noqa: F401,F403
from .plugins import *  # noqa: F401,F403
from .health import *  # noqa: F401,F403
from .servers import *  # noqa: F401,F403
from .emulator import *  # noqa: F401,F403
from .maps import *  # noqa: F401,F403

from . import (
    _adb, device, emulator, health, input, intents, maps, plugins, servers, ui,
)

__all__ = [
    *_adb.__all__,
    *ui.__all__,
    *input.__all__,
    *device.__all__,
    *intents.__all__,
    *plugins.__all__,
    *health.__all__,
    *servers.__all__,
    *emulator.__all__,
    *maps.__all__,
]
