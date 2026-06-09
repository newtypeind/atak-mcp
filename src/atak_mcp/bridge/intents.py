# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Intents and ATAK deep links.

On Android 13+, ATAK's internal broadcast receivers (FOCUS, GO_TO, import, etc.)
are registered NOT_EXPORTED behind a signature permission, so they cannot be
reached with ``adb shell am broadcast``. The supported way in is the exported
``tak:`` VIEW activity, which ATAK-CIV 5.x dispatches on host+path::

    tak://com.atakmap.app/enroll?host=&username=&token=   (TAK server connection)
    tak://com.atakmap.app/import?url=<URL>                (import a file/data package)
    tak://com.atakmap.app/preference?...                  (set preferences)

These were confirmed by decompiling the installed APK; a plain
``com.atakmap.app.IMPORT`` broadcast does nothing.
"""

from __future__ import annotations

import time
from typing import Optional
from urllib.parse import quote, urlparse

from ._adb import ATAK_TESTED_VERSION, AdbError, adb

__all__ = ["broadcast", "deep_link", "enroll", "import_url"]

# ``am``'s typed-extra flags, keyed by a short type code.
_EXTRA_FLAGS = {"s": "--es", "i": "--ei", "z": "--ez", "l": "--el", "f": "--ef"}


def broadcast(
    action: str,
    component: Optional[str] = None,
    extras: Optional[list[tuple[str, str, str]]] = None,
    serial: Optional[str] = None,
) -> str:
    """Send a broadcast Intent via ``adb shell am broadcast``.

    ``extras`` is a list of ``(type, key, value)`` tuples, where ``type`` is one
    of ``s`` (string), ``i`` (int), ``z`` (boolean), ``l`` (long), ``f`` (float).
    Note that ATAK's own receivers are not reachable this way on Android 13+ (see
    the module docstring); this remains useful for other apps and plugins.
    """
    args = ["shell", "am", "broadcast", "-a", action]
    if component:
        args += ["-n", component]
    for t, k, v in extras or []:
        flag = _EXTRA_FLAGS.get(t)
        if not flag:
            raise AdbError(f"unknown extra type {t!r}; use one of {''.join(_EXTRA_FLAGS)}")
        args += [flag, k, v]
    return adb(args, serial, timeout=60)


def _device_logtime(serial: Optional[str] = None) -> str:
    """Device-local timestamp in logcat's ``-v time`` format, for a since-filter."""
    return adb(["shell", "date", "+%m-%d %H:%M:%S.000"], serial, check=False).strip()


def _deeplink_processed(uri: str, since: str, timeout: float, serial: Optional[str]) -> bool:
    """True if ATAK logged ``uri processing: ...`` for ``uri`` after ``since``.

    Matches on ``host + path`` (e.g. ``com.atakmap.app/enroll``), which appears
    verbatim and un-encoded in ATAK's log, so query values don't affect it.
    """
    p = urlparse(uri)
    needle = f"{p.netloc}{p.path}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        args = ["logcat", "-d", "-v", "time"]
        args += ["-t", since] if since else ["-t", "400"]
        log = adb(args, serial, check=False, timeout=30)
        for line in log.splitlines():
            if "uri processing" in line and needle in line:
                return True
        time.sleep(0.6)
    return False


def deep_link(
    uri: str,
    serial: Optional[str] = None,
    verify: bool = True,
    timeout: float = 6.0,
) -> str:
    """Hand ATAK a ``tak:`` deep-link URI through its exported VIEW activity.

    The URI is single-quoted for the device shell so ``&`` query separators
    survive. With ``verify`` (default), confirm ATAK actually processed the URI
    by watching logcat; if it does not, raise rather than fail silently -- the
    common signal that this ATAK build no longer matches our deep-link grammar
    (or that ATAK is not running). Pass ``verify=False`` to fire-and-forget.
    """
    since = _device_logtime(serial) if verify else ""
    out = adb(["shell", f"am start -a android.intent.action.VIEW -d '{uri}'"], serial)
    if not verify:
        return out
    if _deeplink_processed(uri, since, timeout, serial):
        return out + "\n(verified: ATAK processed the deep link)"
    raise AdbError(
        f"ATAK did not process deep link {uri!r}. This entry point is verified on "
        f"ATAK {ATAK_TESTED_VERSION}; this build may differ, or ATAK is not "
        f"running (try `launch`/`ready` first, or run `doctor`)."
    )


def enroll(
    host: str,
    username: Optional[str] = None,
    token: Optional[str] = None,
    serial: Optional[str] = None,
    verify: bool = True,
) -> str:
    """Configure/enroll a TAK server connection via the ``enroll`` deep link.

    ``token`` is the password or enrollment token. Verified against ATAK-CIV
    5.x, where ``CotMapComponent`` handles ``tak://com.atakmap.app/enroll`` and
    reads the ``host``, ``username`` and ``token`` query parameters.
    """
    q = "host=" + quote(host, safe="")
    if username is not None:
        q += "&username=" + quote(username, safe="")
    if token is not None:
        q += "&token=" + quote(token, safe="")
    return deep_link(f"tak://com.atakmap.app/enroll?{q}", serial, verify=verify)


def import_url(url: str, serial: Optional[str] = None, verify: bool = True) -> str:
    """Import a file or data package from a URL via the ``import`` deep link.

    Verified against ATAK-CIV 5.x (``ImportExportMapComponent`` reads the
    ``url`` parameter of ``tak://com.atakmap.app/import``). For a *local* file
    ATAK needs a ``content://`` URI, which adb cannot easily mint; host the file
    and pass its URL here instead.
    """
    return deep_link(
        "tak://com.atakmap.app/import?url=" + quote(url, safe=""), serial, verify=verify
    )
