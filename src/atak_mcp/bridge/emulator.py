# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Emulator-specific ATAK troubleshooting: map rendering and mic input.

Two known Android Studio emulator problems with ATAK (from the team's
troubleshooting notes):

  * The map renderer crashes on the emulator's OpenGL. ATAK falls back to a
    software path if an ``opengl.broken`` marker exists in its data dir
    (deptofdefense/AndroidTacticalAssaultKit-CIV issue #48).
  * The host microphone is not passed through to the AVD unless its config
    enables audio input (``hw.audioInput=yes``).
"""

from __future__ import annotations

import glob
import os
from typing import Optional

from ._adb import AdbError, adb

__all__ = ["fix_opengl", "clear_opengl_fix", "fix_audio_input"]

_OPENGL_BROKEN = "/sdcard/atak/opengl.broken"


def fix_opengl(serial: Optional[str] = None) -> str:
    """Disable ATAK's OpenGL map rendering to stop the emulator render crash.

    Creates ``/sdcard/atak/opengl.broken``; ATAK reads it at startup, so restart
    ATAK to apply. Harmless on a real device, but only needed on the emulator.
    """
    adb(["shell", "mkdir", "-p", "/sdcard/atak"], serial, check=False)
    adb(["shell", "touch", _OPENGL_BROKEN], serial)
    return f"created {_OPENGL_BROKEN} (restart ATAK to apply)"


def clear_opengl_fix(serial: Optional[str] = None) -> str:
    """Remove the ``opengl.broken`` marker (re-enable OpenGL rendering)."""
    adb(["shell", "rm", "-f", _OPENGL_BROKEN], serial, check=False)
    return f"removed {_OPENGL_BROKEN}"


def fix_audio_input(avd: Optional[str] = None, avd_home: Optional[str] = None) -> str:
    """Enable host-microphone passthrough for an emulator AVD.

    Adds (or updates) ``hw.audioInput=yes`` in the AVD's ``config.ini``. This is
    a host-side edit under ``~/.android/avd`` -- restart the emulator to apply.
    ``avd`` is the AVD name; if omitted and exactly one AVD exists, it is used.
    """
    home = avd_home or os.path.expanduser("~/.android/avd")
    if avd:
        config = os.path.join(home, f"{avd}.avd", "config.ini")
        configs = [config] if os.path.isfile(config) else []
    else:
        configs = sorted(glob.glob(os.path.join(home, "*.avd", "config.ini")))
    if not configs:
        raise AdbError(f"no AVD config.ini found under {home} (avd={avd!r})")
    if len(configs) > 1 and not avd:
        names = [os.path.basename(os.path.dirname(c))[:-4] for c in configs]
        raise AdbError(f"multiple AVDs {names}; pass avd=<name>")
    config = configs[0]

    with open(config) as fh:
        lines = fh.read().splitlines()
    out, found = [], False
    for ln in lines:
        if ln.split("=", 1)[0].strip() == "hw.audioInput":
            out.append("hw.audioInput=yes")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append("hw.audioInput=yes")
    with open(config, "w") as fh:
        fh.write("\n".join(out) + "\n")
    return f"set hw.audioInput=yes in {config} (restart the emulator to apply)"
