# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Version detection and the ``doctor`` health check.

Only ATAK-internal bits (deep-link grammar, resource ids, broadcast behaviour)
drift between ATAK versions; the Android-level tools do not. ``doctor`` measures
those drift-prone bits on a connected device so a future build's compatibility
is a fact, not a guess.
"""

from __future__ import annotations

import re
import time
from typing import Optional

from ._adb import (
    ATAK_CIV_PACKAGE, ATAK_PACKAGES, ATAK_TESTED_VERSION, AdbError, adb, devices,
)
from .device import is_installed, is_running
from .intents import _device_logtime, deep_link
from .ui import dump

__all__ = ["atak_version", "installed_atak", "doctor"]

_VERSION_RE = re.compile(r"versionName=(\S+)")


def atak_version(package: str = ATAK_CIV_PACKAGE, serial: Optional[str] = None) -> str:
    """Installed versionName of ``package`` (e.g. ``5.6.0.11``), or '' if absent."""
    out = adb(["shell", "dumpsys", "package", package], serial, check=False, timeout=30)
    m = _VERSION_RE.search(out)
    return m.group(1) if m else ""


def installed_atak(serial: Optional[str] = None) -> list[tuple[str, str]]:
    """Detect installed ATAK flavours as ``[(package, versionName), ...]``."""
    found = []
    for pkg in ATAK_PACKAGES:
        if is_installed(pkg, serial):
            found.append((pkg, atak_version(pkg, serial)))
    return found


def doctor(package: str = ATAK_CIV_PACKAGE, serial: Optional[str] = None) -> dict:
    """Probe the connected device for everything version-sensitive, so a future
    ATAK build's (in)compatibility is a measured fact, not a guess.

    Reports the device, the installed ATAK flavour/version vs the tested
    version, whether the deep-link entry point still works, whether key resource
    ids are still present, and whether internal broadcasts are reachable. Has no
    lasting side effects (the deep-link probe hits a no-op path).
    """
    report: dict = {"tested_version": ATAK_TESTED_VERSION, "checks": {}}

    devs = devices()
    report["device"] = devs[0] if devs else None
    if not devs:
        report["checks"]["device"] = "FAIL: no adb device"
        return report

    flavours = installed_atak(serial)
    report["atak_installed"] = [{"package": p, "version": v} for p, v in flavours]
    ver = atak_version(package, serial)
    report["atak_version"] = ver
    if not ver:
        report["checks"]["atak_present"] = f"FAIL: {package} not installed"
        return report
    major_minor = ".".join(ver.split(".")[:2])
    report["checks"]["version_match"] = (
        f"OK: {ver}" if major_minor == ATAK_TESTED_VERSION
        else f"WARN: device {ver} != tested {ATAK_TESTED_VERSION}; re-verify deep links / ids"
    )

    running = is_running(package, serial)
    report["checks"]["running"] = (
        "OK: running" if running
        else "WARN: not running (deep-link probe will fail; `launch` first)"
    )

    # resource-id sanity FIRST, while the map is still in front (the deep-link
    # probe below can pop a dialog that hides the nav bar).
    try:
        nodes = dump(serial)
        ids = {n.resource_id.split("/")[-1] for n in nodes if n.resource_id}
        expected = {"tak_nav_menu_button", "tak_nav_zoom"}
        present = sorted(expected & ids)
        report["checks"]["resource_ids"] = (
            f"OK: {present}" if present else
            f"WARN: none of {sorted(expected)} found on this screen (open the map first)"
        )
    except AdbError as e:
        report["checks"]["resource_ids"] = f"WARN: {e}"

    # deep-link entry point: a no-op path that ATAK logs ("uri processing:") but
    # matches to no handler, so routing is confirmed with zero side effects (no
    # download, no dialog, no config change).
    try:
        deep_link("tak://com.atakmap.app/atak_mcp_doctor_probe",
                  serial, verify=True, timeout=6.0)
        report["checks"]["deep_link"] = "OK: tak: deep-link entry point is live (routing confirmed)"
    except AdbError as e:
        report["checks"]["deep_link"] = f"FAIL: {e}"

    # is the internal broadcast bus reachable from adb? (informational; on
    # Android 13+ this is blocked, which is why we use deep links.)
    since = _device_logtime(serial)
    adb(["shell", "am", "broadcast", "-a", "com.atakmap.android.maps.FOCUS",
         "--es", "point", "0.0,0.0"], serial, check=False)
    time.sleep(1.5)
    log = adb(["logcat", "-d", "-v", "time", "-t", since or "200"], serial, check=False, timeout=30)
    reached = "FocusBroadcastReceiver" in log or "Unable to focus" in log
    report["checks"]["internal_broadcast"] = (
        "reachable: this build allows am broadcast to ATAK internals (map FOCUS works)"
        if reached else
        "blocked: ATAK internals are NOT_EXPORTED (expected on Android 13+); use deep links"
    )
    return report
