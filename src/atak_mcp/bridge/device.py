# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Device and app system operations: power/lifecycle, packages, permissions,
file transfer, logs, and diagnostics (crashes, screen recording).

These are Android-level (``pm`` / ``am`` / ``svc`` / ``logcat`` / push-pull) and
do not depend on the ATAK version, so they are the most durable layer.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Optional

from ._adb import ATAK_CIV_PACKAGE, AdbError, _base, adb
from .input import key

__all__ = [
    "logcat_clear", "logcat", "list_packages", "is_installed", "foreground_app",
    "push", "pull", "wake_unlock", "stay_awake", "is_running", "force_stop",
    "clear_app_data", "launch_atak", "restart_atak", "grant_permission",
    "revoke_permission", "crashes", "record_start", "record_stop",
    "is_emulator", "reverse", "reverse_list", "reverse_remove", "host_address",
]


# --------------------------------------------------------------------------- #
# logs
# --------------------------------------------------------------------------- #
def logcat_clear(serial: Optional[str] = None) -> None:
    adb(["logcat", "-c"], serial, check=False)


def logcat(
    lines: int = 200,
    grep: Optional[str] = None,
    serial: Optional[str] = None,
    since: Optional[str] = None,
) -> str:
    """Dump logcat non-blocking, grepping the FULL buffer and returning matches.

    Always uses ``adb logcat -d`` (dump and exit, never a streaming/follow call
    that would hang). The important behaviour:

    * ``grep`` filters the **whole** ring buffer, not just a trailing window, so
      a marker logged long ago is still found.
    * ``lines`` then caps how many lines are **returned**, applied *after*
      filtering, i.e. "the last N matching lines from the whole buffer". Pass
      ``lines<=0`` (or ``None``) for no cap. With no ``grep``/``since`` this is a
      fast path that asks adb for just the last N lines of the full dump.
    * ``since`` narrows the buffer read to a time window, mapping to
      ``adb logcat -d -t '<since>'``. adb accepts a line count (e.g. ``"500"``)
      or an absolute timestamp (e.g. ``"01-30 14:00:00.000"`` or
      ``"2026-01-30 14:00:00.000"``). ``grep``/``lines`` still apply within it.
    """
    args = ["logcat", "-d", "-v", "time"]
    if since:
        args += ["-t", since]
    elif not grep and lines and lines > 0:
        # No filter: let adb return just the last N lines (cheap, avoids
        # shovelling the whole multi-MB buffer over adb just to tail it).
        args += ["-t", str(lines)]
    out = adb(args, serial, timeout=30)
    result = out.splitlines()
    if grep:
        g = grep.lower()
        result = [l for l in result if g in l.lower()]
    if lines and lines > 0:
        result = result[-lines:]
    return "\n".join(result)


# --------------------------------------------------------------------------- #
# packages
# --------------------------------------------------------------------------- #
def list_packages(third_party: bool = True, serial: Optional[str] = None) -> list[str]:
    args = ["shell", "pm", "list", "packages"]
    if third_party:
        args.append("-3")
    out = adb(args, serial)
    return sorted(l.split(":", 1)[1].strip() for l in out.splitlines() if ":" in l)


def is_installed(package: str, serial: Optional[str] = None) -> bool:
    out = adb(["shell", "pm", "list", "packages", package], serial)
    return any(line.strip() == f"package:{package}" for line in out.splitlines())


def foreground_app(serial: Optional[str] = None) -> str:
    """Return the resumed/top activity string (best effort)."""
    out = adb(["shell", "dumpsys", "activity", "activities"], serial, check=False)
    for line in out.splitlines():
        if "mResumedActivity" in line or "topResumedActivity" in line:
            return line.strip()
    return ""


# --------------------------------------------------------------------------- #
# file transfer
# --------------------------------------------------------------------------- #
def push(local: str, remote: str, serial: Optional[str] = None) -> str:
    """Copy a local file onto the device with ``adb push``.

    This is the missing piece for configuring a TAK server over SSL: the pure
    tap-through-Settings path cannot get a client certificate (``.p12``) or an
    ATAK data package (``.zip``) onto the device. Stage the file here first,
    then import it (via the file browser, or a ``broadcast`` intent).

    ``remote`` is a device path, e.g. ``/sdcard/Download/truststore.p12``.
    """
    if not os.path.isfile(local):
        raise AdbError(f"local file not found: {local}")
    return adb(["push", local, remote], serial, timeout=300)


def pull(remote: str, local: str, serial: Optional[str] = None) -> str:
    """Copy a file off the device with ``adb pull`` (the counterpart to push).

    Use it to retrieve logs, a screen recording, or a data package that ATAK
    exported, e.g. for CI artifacts.
    """
    return adb(["pull", remote, local], serial, timeout=300)


# --------------------------------------------------------------------------- #
# network: reaching a host-local server from the device
# --------------------------------------------------------------------------- #
def is_emulator(serial: Optional[str] = None) -> bool:
    """True if the target is an Android emulator (rather than a USB device).

    This decides how the device reaches a server on the host's loopback: an
    emulator uses the ``10.0.2.2`` host alias directly, while a USB device needs
    an ``adb reverse`` tunnel and then talks to its own ``127.0.0.1``.
    """
    s = serial or os.environ.get("ANDROID_SERIAL", "") or ""
    if s.startswith("emulator-"):
        return True
    for prop in ("ro.kernel.qemu", "ro.boot.qemu"):
        if adb(["shell", "getprop", prop], serial, check=False).strip() == "1":
            return True
    chars = adb(["shell", "getprop", "ro.build.characteristics"], serial, check=False)
    return "emulator" in chars


def reverse(remote_port: int, local_port: Optional[int] = None,
            serial: Optional[str] = None) -> str:
    """Set up ``adb reverse tcp:<remote> tcp:<local>``.

    The device's ``localhost:<remote_port>`` is then tunnelled to the host's
    ``localhost:<local_port>`` (defaults to the same port), so an app on a USB
    device can reach a server running on the host machine.
    """
    local_port = remote_port if local_port is None else local_port
    return adb(["reverse", f"tcp:{remote_port}", f"tcp:{local_port}"], serial)


def reverse_list(serial: Optional[str] = None) -> str:
    """List active ``adb reverse`` tunnels."""
    return adb(["reverse", "--list"], serial, check=False)


def reverse_remove(remote_port: int, serial: Optional[str] = None) -> str:
    """Remove the ``adb reverse`` tunnel for ``tcp:<remote_port>``."""
    return adb(["reverse", "--remove", f"tcp:{remote_port}"], serial, check=False)


def host_address(serial: Optional[str] = None) -> str:
    """The device-side address that reaches the host's loopback.

    ``10.0.2.2`` on an emulator (the standard host alias), ``127.0.0.1`` on a
    USB device (valid only once an ``adb reverse`` tunnel for the port exists).
    """
    return "10.0.2.2" if is_emulator(serial) else "127.0.0.1"


# --------------------------------------------------------------------------- #
# power & app lifecycle
# --------------------------------------------------------------------------- #
def wake_unlock(serial: Optional[str] = None) -> None:
    """Wake the screen and dismiss a non-secure lock screen (WAKEUP + MENU).

    Does not defeat a PIN/pattern lock; it just gets past the swipe-to-unlock
    keyguard that interrupts unattended runs.
    """
    key("WAKEUP", serial)
    key("MENU", serial)


def stay_awake(on: bool = True, serial: Optional[str] = None) -> str:
    """Keep the screen on while charging (``svc power stayon``) for CI runs."""
    return adb(["shell", "svc", "power", "stayon", "true" if on else "false"], serial)


def is_running(package: str = ATAK_CIV_PACKAGE, serial: Optional[str] = None) -> bool:
    """True if ``package`` has a live process."""
    out = adb(["shell", "pidof", package], serial, check=False)
    return bool(out.strip())


def force_stop(package: str = ATAK_CIV_PACKAGE, serial: Optional[str] = None) -> str:
    return adb(["shell", "am", "force-stop", package], serial)


def clear_app_data(package: str = ATAK_CIV_PACKAGE, serial: Optional[str] = None) -> str:
    """Wipe an app's data (``pm clear``) for a reproducible from-scratch state."""
    return adb(["shell", "pm", "clear", package], serial)


def launch_atak(package: str = ATAK_CIV_PACKAGE, serial: Optional[str] = None) -> str:
    return adb(
        ["shell", "monkey", "-p", package, "-c",
         "android.intent.category.LAUNCHER", "1"],
        serial,
        check=False,
    )


def restart_atak(package: str = ATAK_CIV_PACKAGE, serial: Optional[str] = None) -> str:
    force_stop(package, serial)
    return launch_atak(package, serial)


# --------------------------------------------------------------------------- #
# permissions
# --------------------------------------------------------------------------- #
def grant_permission(package: str, permission: str, serial: Optional[str] = None) -> str:
    """Grant a runtime permission (``pm grant``) to skip the in-app dialog,
    e.g. ``android.permission.ACCESS_FINE_LOCATION``."""
    return adb(["shell", "pm", "grant", package, permission], serial)


def revoke_permission(package: str, permission: str, serial: Optional[str] = None) -> str:
    return adb(["shell", "pm", "revoke", package, permission], serial)


# --------------------------------------------------------------------------- #
# diagnostics: crashes & screen recording
# --------------------------------------------------------------------------- #
def crashes(
    package: Optional[str] = None,
    lines: int = 500,
    serial: Optional[str] = None,
) -> str:
    """Return the crash log buffer (FATAL EXCEPTION / native crashes), optionally
    filtered to lines mentioning ``package``. Empty string means no crashes,
    which makes this a one-line pass/fail check after exercising a plugin."""
    out = adb(
        ["logcat", "-d", "-b", "crash", "-v", "time", "-t", str(lines)],
        serial, timeout=30, check=False,
    )
    if package:
        out = "\n".join(l for l in out.splitlines() if package in l)
    return out.strip()


def record_start(
    remote: str = "/sdcard/atak_mcp_record.mp4",
    time_limit: int = 180,
    serial: Optional[str] = None,
) -> str:
    """Start ``screenrecord`` detached on the device. Stop it with ``record_stop``.

    ``time_limit`` (max 180s on most builds) is a safety cap if stop is missed.
    """
    subprocess.Popen(
        _base(serial) + ["shell", "screenrecord", "--time-limit", str(time_limit), remote],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return remote


def record_stop(
    remote: str = "/sdcard/atak_mcp_record.mp4",
    local: Optional[str] = None,
    serial: Optional[str] = None,
) -> str:
    """Stop the recording (SIGINT lets screenrecord finalise the mp4) and, if
    ``local`` is given, pull the file off the device."""
    adb(["shell", "pkill", "-INT", "screenrecord"], serial, check=False)
    time.sleep(2.0)  # let screenrecord flush and close the container
    if local:
        pull(remote, local, serial)
        return local
    return remote
