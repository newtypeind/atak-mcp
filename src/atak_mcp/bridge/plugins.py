# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""ATAK plugin install/reload lifecycle and the high-level dev-loop composites."""

from __future__ import annotations

import time
from typing import Optional

from ._adb import ATAK_CIV_PACKAGE, AdbError, adb
from .device import foreground_app, is_running, launch_atak, list_packages
from .input import tap, tap_xy
from .ui import Node, dump, wait_for

__all__ = [
    "list_plugins", "install", "uninstall", "reload_plugin", "confirm_load",
    "open_tool", "wait_atak_ready", "deploy_plugin",
]


def list_plugins(serial: Optional[str] = None) -> list[str]:
    """Best-effort list of installed ATAK plugins (by package-name heuristic)."""
    pkgs = list_packages(third_party=True, serial=serial)
    return [p for p in pkgs if "atak" in p.lower() and p != ATAK_CIV_PACKAGE]


def install(apk: str, serial: Optional[str] = None) -> str:
    # -r reinstall, -g grant all runtime permissions (so RECORD_AUDIO is live).
    return adb(["install", "-r", "-g", apk], serial, timeout=300)


def uninstall(package: str, serial: Optional[str] = None) -> str:
    return adb(["uninstall", package], serial, timeout=120, check=False)


def reload_plugin(package: str, apk: str, serial: Optional[str] = None) -> str:
    """Package-based reload: uninstall then install.

    Empirically ATAK only picks up plugin code changes reliably after a full
    uninstall/reinstall, so this is the canonical reload path.
    """
    rm = uninstall(package, serial)
    add = install(apk, serial)
    return f"uninstall: {rm.strip()}\ninstall: {add.strip()}"


def confirm_load(timeout: float = 25.0, serial: Optional[str] = None) -> str:
    """Confirm ATAK's "load this plugin?" prompt so a freshly installed plugin
    actually loads.

    ATAK does not reliably auto-load a plugin after an uninstall/reinstall; it
    instead pops a dialog (either the per-plugin "Would you like to load this
    installed plugin?" with an OK button, or the startup "Load Plugins?" with a
    "Load Plugins" button). This polls for either and taps the confirm button.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            nodes = dump(serial)
        except AdbError:
            time.sleep(1.0)
            continue
        haystack = " ".join((n.text or "").lower() for n in nodes)
        is_prompt = (
            "load this installed plugin" in haystack
            or "load plugins" in haystack
            or "load plugin" in haystack
        )
        if is_prompt:
            for label in ("OK", "Load Plugins", "Load", "Yes"):
                btn = next(
                    (n for n in nodes
                     if n.clickable and (n.text or "").strip().lower() == label.lower()),
                    None,
                )
                if btn:
                    x, y = btn.center
                    tap_xy(x, y, serial)
                    return f"confirmed plugin load via {label!r}"
        time.sleep(1.5)
    return "no load prompt appeared (plugin may already be loaded)"


def wait_atak_ready(
    package: str = ATAK_CIV_PACKAGE,
    timeout: float = 60.0,
    serial: Optional[str] = None,
) -> str:
    """Block until ATAK is running, foregrounded, and its UI is dumpable (past
    the splash). Returns the foreground activity string."""
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            if is_running(package, serial):
                fg = foreground_app(serial)
                if package in fg and dump(serial):
                    return fg
        except AdbError as e:
            last = str(e)
        time.sleep(1.5)
    raise AdbError(f"ATAK not ready after {timeout}s; last={last}")


def open_tool(name: str, timeout: float = 10.0, serial: Optional[str] = None) -> Node:
    """Open an ATAK tool/plugin: tap the Tools menu, wait for the item, tap it.

    Plugins register a toolbar item that shows up by label in the Tools menu, so
    this is the generic 'open my plugin' helper.
    """
    tap("Tools", serial=serial)
    wait_for(name, timeout=timeout, serial=serial)
    return tap(name, serial=serial)


def deploy_plugin(
    package: str,
    apk: str,
    serial: Optional[str] = None,
    ready_timeout: float = 60.0,
) -> dict:
    """Full plugin dev loop in one call: reinstall, launch, confirm the load
    prompt, wait until ATAK is ready. Returns a step-by-step report."""
    out: dict = {}
    out["reload"] = reload_plugin(package, apk, serial)
    out["launch"] = launch_atak(serial=serial)
    out["confirm_load"] = confirm_load(serial=serial)
    out["ready"] = wait_atak_ready(serial=serial, timeout=ready_timeout)
    return out
