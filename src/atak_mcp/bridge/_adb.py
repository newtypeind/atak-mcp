# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Low-level adb plumbing and shared constants.

This is the leaf module of the bridge package: everything else builds on
``adb()``. Pure standard library, so it runs anywhere adb runs.
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional

__all__ = [
    "ATAK_CIV_PACKAGE", "ATAK_PACKAGES", "ATAK_TESTED_VERSION",
    "AdbError", "adb", "devices",
]

# ATAK Civilian package id. Override per call where a different flavour is used.
ATAK_CIV_PACKAGE = "com.atakmap.app.civ"

# Known ATAK app flavours (package ids), in detection order.
ATAK_PACKAGES = ("com.atakmap.app.civ", "com.atakmap.app.mil", "com.atakmap.app.gov")

# The ATAK version this build's deep-link grammar / resource ids were verified
# against. `doctor` compares the device's installed version to this and warns on
# a mismatch, so version drift surfaces instead of failing silently.
ATAK_TESTED_VERSION = "5.6"


class AdbError(RuntimeError):
    """Raised when an adb invocation fails or a UI query finds nothing."""


def _base(serial: Optional[str]) -> list[str]:
    cmd = ["adb"]
    serial = serial or os.environ.get("ANDROID_SERIAL")
    if serial:
        cmd += ["-s", serial]
    return cmd


def adb(
    args: list[str],
    serial: Optional[str] = None,
    *,
    binary: bool = False,
    timeout: int = 60,
    check: bool = True,
):
    """Run an adb command and return stdout (str, or bytes when ``binary``)."""
    proc = subprocess.run(
        _base(serial) + args, capture_output=True, timeout=timeout
    )
    if check and proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise AdbError(f"`adb {' '.join(args)}` failed (rc={proc.returncode}): {err}")
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


def devices() -> list[dict]:
    """Return the attached devices as ``[{serial, state, ...}]``."""
    out = adb(["devices", "-l"])
    rows = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        entry = {"serial": parts[0], "state": parts[1] if len(parts) > 1 else "?"}
        for tok in parts[2:]:
            if ":" in tok:
                k, v = tok.split(":", 1)
                entry[k] = v
        rows.append(entry)
    return rows
