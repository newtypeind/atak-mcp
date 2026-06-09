# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Core adb bridge for driving ATAK (and any Android UI) deterministically.

Everything here is pure standard library so the bridge runs anywhere adb runs.
The design goal is to never tap blindly: callers locate a node by its text,
resource id, or content description (read from the uiautomator hierarchy) and
tap the centre of that node's bounds.

Used by:
  * ``cli.py``    - a command line front end for humans and shell scripts
  * ``server.py`` - an MCP stdio server for AI agents
"""

from __future__ import annotations

import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote, urlparse

# ATAK Civilian package id. Override per call where a different flavour is used.
ATAK_CIV_PACKAGE = "com.atakmap.app.civ"

# Known ATAK app flavours (package ids), in detection order.
ATAK_PACKAGES = ("com.atakmap.app.civ", "com.atakmap.app.mil", "com.atakmap.app.gov")

# The ATAK version this build's deep-link grammar / resource ids were verified
# against. `doctor` compares the device's installed version to this and warns on
# a mismatch, so version drift surfaces instead of failing silently.
ATAK_TESTED_VERSION = "5.6"

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


class AdbError(RuntimeError):
    """Raised when an adb invocation fails or a UI query finds nothing."""


# --------------------------------------------------------------------------- #
# adb plumbing
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# screen capture
# --------------------------------------------------------------------------- #
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def screenshot(path: str, serial: Optional[str] = None) -> str:
    """Grab a PNG screenshot to ``path`` and return the path.

    Foldables and multi-display devices (e.g. Galaxy Z Flip/Fold) make
    ``screencap`` print a "[Warning] Multiple displays were found" banner to
    stdout *before* the PNG bytes, which corrupts a naive capture. We locate the
    PNG signature and slice from there, so the warning prefix is harmless.
    """
    data = adb(["exec-out", "screencap", "-p"], serial, binary=True, timeout=30)
    idx = data.find(_PNG_MAGIC)
    if idx < 0:
        head = data[:120].decode("utf-8", "replace")
        raise AdbError(f"screencap returned no PNG signature; head={head!r}")
    with open(path, "wb") as fh:
        fh.write(data[idx:])
    return path


# --------------------------------------------------------------------------- #
# UI hierarchy
# --------------------------------------------------------------------------- #
@dataclass
class Node:
    text: str = ""
    resource_id: str = ""
    content_desc: str = ""
    cls: str = ""
    package: str = ""
    clickable: bool = False
    enabled: bool = True
    checked: bool = False
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)

    @property
    def center(self) -> tuple[int, int]:
        l, t, r, b = self.bounds
        return ((l + r) // 2, (t + b) // 2)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["center"] = self.center
        return d

    def label(self) -> str:
        return self.text or self.content_desc or self.resource_id or self.cls


def ui_xml(serial: Optional[str] = None, attempts: int = 3) -> str:
    """Return the raw uiautomator XML dump.

    uiautomator refuses to dump while the screen is animating, so we retry a
    few times. Compose surfaces its semantics tree to accessibility, which is
    what uiautomator reads, so Compose nodes with text/contentDescription show
    up here just like classic View nodes.
    """
    last = ""
    for i in range(attempts):
        out = adb(
            ["shell", "uiautomator", "dump", "/sdcard/atak_mcp_dump.xml"],
            serial,
            timeout=30,
            check=False,
        )
        last = out
        if "dumped to" in out or "UI hierchary" in out:
            return adb(["exec-out", "cat", "/sdcard/atak_mcp_dump.xml"], serial)
        time.sleep(0.6 * (i + 1))
    raise AdbError(f"uiautomator dump failed after {attempts} attempts: {last.strip()}")


def parse_nodes(xml_text: str) -> list[Node]:
    nodes: list[Node] = []
    root = ET.fromstring(xml_text)
    for el in root.iter("node"):
        m = _BOUNDS_RE.match(el.get("bounds", ""))
        bounds = tuple(int(x) for x in m.groups()) if m else (0, 0, 0, 0)
        nodes.append(
            Node(
                text=el.get("text", ""),
                resource_id=el.get("resource-id", ""),
                content_desc=el.get("content-desc", ""),
                cls=el.get("class", ""),
                package=el.get("package", ""),
                clickable=el.get("clickable") == "true",
                enabled=el.get("enabled") == "true",
                checked=el.get("checked") == "true",
                bounds=bounds,
            )
        )
    return nodes


def dump(serial: Optional[str] = None, attempts: int = 3) -> list[Node]:
    return parse_nodes(ui_xml(serial, attempts=attempts))


def find(
    query: str,
    by: str = "any",
    serial: Optional[str] = None,
    nodes: Optional[list[Node]] = None,
    visible_only: bool = True,
    exact: bool = False,
    clickable_only: bool = False,
) -> list[Node]:
    """Find nodes whose text/id/desc matches ``query`` (case-insensitive).

    ``by`` is one of ``any`` | ``text`` | ``id`` | ``desc``. With ``exact`` the
    field must equal the query (after trimming) rather than merely contain it,
    which avoids matching a dialog title like ``"Load Plugins?"`` when you want
    the ``"Load Plugins"`` button.
    """
    if nodes is None:
        nodes = dump(serial)
    q = query.strip().lower()
    fields_for = {
        "text": lambda n: [n.text],
        "id": lambda n: [n.resource_id],
        "desc": lambda n: [n.content_desc],
        "any": lambda n: [n.text, n.resource_id, n.content_desc],
    }[by]
    out = []
    for n in nodes:
        if visible_only and n.bounds[2] - n.bounds[0] <= 0:
            continue
        if clickable_only and not n.clickable:
            continue
        fields = [(f or "").strip().lower() for f in fields_for(n)]
        if exact:
            matched = any(f == q for f in fields)
        else:
            matched = any(q in f for f in fields)
        if matched:
            out.append(n)
    return out


def wait_for(
    query: str,
    by: str = "any",
    timeout: float = 10.0,
    interval: float = 0.7,
    serial: Optional[str] = None,
) -> Node:
    """Poll until a node matching ``query`` appears, or raise on timeout."""
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            # single-attempt dump: fail fast mid-animation and let this loop
            # re-poll, instead of paying ui_xml's escalating internal retries.
            matches = find(query, by=by, nodes=dump(serial, attempts=1))
            if matches:
                return matches[0]
        except AdbError as e:  # transient dump failure
            last_err = e
        time.sleep(interval)
    raise AdbError(f"timed out waiting for {query!r} ({by}); last={last_err}")


# --------------------------------------------------------------------------- #
# input
# --------------------------------------------------------------------------- #
def tap_xy(x: int, y: int, serial: Optional[str] = None) -> None:
    adb(["shell", "input", "tap", str(x), str(y)], serial)


def _resolve(
    query: str,
    by: str,
    index: int,
    serial: Optional[str],
    exact: bool,
    prefer_clickable: bool,
) -> Node:
    """Find the single node a gesture should target (shared by tap/long_press)."""
    matches = find(query, by=by, serial=serial, exact=exact)
    if not matches:
        raise AdbError(f"no node matching {query!r} (by={by}, exact={exact})")
    if prefer_clickable and index == 0:
        clickable = [m for m in matches if m.clickable]
        if clickable:
            matches = clickable
    if index >= len(matches):
        raise AdbError(f"index {index} out of range; {len(matches)} match(es)")
    return matches[index]


def tap(
    query: str,
    by: str = "any",
    index: int = 0,
    serial: Optional[str] = None,
    exact: bool = False,
    prefer_clickable: bool = True,
) -> Node:
    """Find a node and tap the centre of its bounds. Returns the tapped node.

    When several nodes match and ``index`` is 0, a clickable match is preferred
    over a non-clickable one (e.g. a Button over a TextView with the same text).
    """
    node = _resolve(query, by, index, serial, exact, prefer_clickable)
    tap_xy(*node.center, serial=serial)
    return node


def long_press_xy(x: int, y: int, ms: int = 600, serial: Optional[str] = None) -> None:
    """Long-press a point by swiping in place. ATAK uses this to drop a marker."""
    swipe(x, y, x, y, ms, serial)


def long_press(
    query: str,
    by: str = "any",
    index: int = 0,
    serial: Optional[str] = None,
    exact: bool = False,
    ms: int = 600,
    prefer_clickable: bool = True,
) -> Node:
    """Find a node and long-press the centre of its bounds. Returns the node."""
    node = _resolve(query, by, index, serial, exact, prefer_clickable)
    long_press_xy(*node.center, ms=ms, serial=serial)
    return node


def double_tap_xy(x: int, y: int, serial: Optional[str] = None) -> None:
    tap_xy(x, y, serial)
    tap_xy(x, y, serial)


def double_tap(
    query: str,
    by: str = "any",
    index: int = 0,
    serial: Optional[str] = None,
    exact: bool = False,
    prefer_clickable: bool = True,
) -> Node:
    """Find a node and double-tap it (e.g. to zoom the ATAK map in)."""
    node = _resolve(query, by, index, serial, exact, prefer_clickable)
    double_tap_xy(*node.center, serial=serial)
    return node


def clear_text(count: int = 120, serial: Optional[str] = None) -> None:
    """Clear the focused text field: jump to the end, then backspace ``count``
    times. ``input`` has no select-all, so this is the robust way to empty a
    field before retyping (e.g. editing an existing TAK server address)."""
    key("MOVE_END", serial)
    adb(["shell", "input", "keyevent", *(["67"] * count)], serial)  # 67 = DEL


def swipe(x1, y1, x2, y2, ms: int = 300, serial: Optional[str] = None) -> None:
    adb(["shell", "input", "swipe", *map(str, (x1, y1, x2, y2, ms))], serial)


def text_input(value: str, serial: Optional[str] = None) -> None:
    # `input text` treats spaces specially; encode them.
    adb(["shell", "input", "text", value.replace(" ", "%s")], serial)


def key(keycode: str, serial: Optional[str] = None) -> None:
    """Send a key event, e.g. ``BACK``, ``HOME``, ``ENTER`` or a numeric code."""
    if keycode.isdigit():
        adb(["shell", "input", "keyevent", keycode], serial)
    else:
        adb(["shell", "input", "keyevent", f"KEYCODE_{keycode.upper()}"], serial)


# --------------------------------------------------------------------------- #
# logs
# --------------------------------------------------------------------------- #
def logcat_clear(serial: Optional[str] = None) -> None:
    adb(["logcat", "-c"], serial, check=False)


def logcat(
    lines: int = 200,
    grep: Optional[str] = None,
    serial: Optional[str] = None,
) -> str:
    """Dump the tail of logcat, optionally filtered by a substring."""
    out = adb(["logcat", "-d", "-v", "time", "-t", str(lines)], serial, timeout=30)
    if grep:
        g = grep.lower()
        out = "\n".join(l for l in out.splitlines() if g in l.lower())
    return out


# --------------------------------------------------------------------------- #
# apps & plugins
# --------------------------------------------------------------------------- #
def list_packages(third_party: bool = True, serial: Optional[str] = None) -> list[str]:
    args = ["shell", "pm", "list", "packages"]
    if third_party:
        args.append("-3")
    out = adb(args, serial)
    return sorted(l.split(":", 1)[1].strip() for l in out.splitlines() if ":" in l)


def list_plugins(serial: Optional[str] = None) -> list[str]:
    """Best-effort list of installed ATAK plugins (by package-name heuristic)."""
    pkgs = list_packages(third_party=True, serial=serial)
    return [p for p in pkgs if "atak" in p.lower() and p != ATAK_CIV_PACKAGE]


def is_installed(package: str, serial: Optional[str] = None) -> bool:
    out = adb(["shell", "pm", "list", "packages", package], serial)
    return any(line.strip() == f"package:{package}" for line in out.splitlines())


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


def launch_atak(package: str = ATAK_CIV_PACKAGE, serial: Optional[str] = None) -> str:
    return adb(
        ["shell", "monkey", "-p", package, "-c",
         "android.intent.category.LAUNCHER", "1"],
        serial,
        check=False,
    )


def foreground_app(serial: Optional[str] = None) -> str:
    """Return the resumed/top activity string (best effort)."""
    out = adb(["shell", "dumpsys", "activity", "activities"], serial, check=False)
    for line in out.splitlines():
        if "mResumedActivity" in line or "topResumedActivity" in line:
            return line.strip()
    return ""


# --------------------------------------------------------------------------- #
# files & intents
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


# ``am``'s typed-extra flags, keyed by a short type code.
_EXTRA_FLAGS = {"s": "--es", "i": "--ei", "z": "--ez", "l": "--el", "f": "--ef"}


def broadcast(
    action: str,
    component: Optional[str] = None,
    extras: Optional[list[tuple[str, str, str]]] = None,
    serial: Optional[str] = None,
) -> str:
    """Send a broadcast Intent via ``adb shell am broadcast``.

    ATAK and many of its plugins accept configuration through broadcast
    Intents, which is far more robust than tapping through Settings (no layout
    to chase, no fields to clear). ``extras`` is a list of ``(type, key,
    value)`` tuples, where ``type`` is one of ``s`` (string), ``i`` (int),
    ``z`` (boolean), ``l`` (long), ``f`` (float).

    Example (import a staged data package)::

        broadcast(
            "com.atakmap.app.IMPORT",
            extras=[("s", "filepath", "/sdcard/Download/server.zip"),
                    ("z", "import", "true")],
        )
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


def pull(remote: str, local: str, serial: Optional[str] = None) -> str:
    """Copy a file off the device with ``adb pull`` (the counterpart to push).

    Use it to retrieve logs, a screen recording, or a data package that ATAK
    exported, e.g. for CI artifacts.
    """
    return adb(["pull", remote, local], serial, timeout=300)


# --------------------------------------------------------------------------- #
# extra waits
# --------------------------------------------------------------------------- #
def exists(query: str, by: str = "any", serial: Optional[str] = None) -> bool:
    """True if any node currently matches ``query`` (a cheap branch test)."""
    try:
        return bool(find(query, by=by, nodes=dump(serial, attempts=1)))
    except AdbError:
        return False


def wait_gone(
    query: str,
    by: str = "any",
    timeout: float = 10.0,
    interval: float = 0.7,
    serial: Optional[str] = None,
) -> bool:
    """Poll until no node matches ``query`` (dialog closed, spinner gone)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if not find(query, by=by, serial=serial):
                return True
        except AdbError:
            pass  # transient dump failure; treat as not-yet-gone
        time.sleep(interval)
    raise AdbError(f"{query!r} still present after {timeout}s")


# --------------------------------------------------------------------------- #
# device power & app lifecycle
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


# --------------------------------------------------------------------------- #
# high-level composites (Tier B)
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# ATAK deep links (the supported external entry point)
# --------------------------------------------------------------------------- #
# On Android 13+, ATAK's internal broadcast receivers (FOCUS, GO_TO, import,
# etc.) are registered NOT_EXPORTED behind a signature permission, so they
# cannot be reached with `adb shell am broadcast`. The supported way in is the
# exported ``tak:`` VIEW activity, which ATAK-CIV 5.x dispatches on host+path:
#   tak://com.atakmap.app/enroll?host=&username=&token=   (TAK server connection)
#   tak://com.atakmap.app/import?url=<URL>                (import a file/data package)
#   tak://com.atakmap.app/preference?...                  (set preferences)
# These were confirmed by decompiling the installed APK; a plain
# ``com.atakmap.app.IMPORT`` broadcast does nothing.
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
        if since:
            args += ["-t", since]
        else:
            args += ["-t", "400"]
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


# --------------------------------------------------------------------------- #
# version detection & health check (managing ATAK version drift)
# --------------------------------------------------------------------------- #
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
    lasting side effects (the deep-link probe imports a non-resolving URL).
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
    report["checks"]["running"] = "OK: running" if running else "WARN: not running (deep-link probe will fail; `launch` first)"

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


# --------------------------------------------------------------------------- #
# ATAK server connections (Manage Server Connections UI, driven by resource id)
# --------------------------------------------------------------------------- #
# The "Manage Server Connections" list and its add/edit form expose stable
# resource ids, so listing/adding/editing/removing a TAK server connection is
# deterministic UI automation rather than blind tapping. Plain TCP/SSL/QUIC
# streaming connections live here (the `enroll` deep link only covers SSL
# certificate enrollment against a reachable server).
_PROTO_RADIO = {"tcp": "tcp_radio", "ssl": "ssl_radio", "quic": "quic_radio"}


def _ids(nodes: list[Node], suffix: str) -> list[Node]:
    return [n for n in nodes if n.resource_id.endswith("/" + suffix)]


def _parse_connstr(cs: str) -> tuple[str, str, str]:
    """Split a TAK connectString 'host:port:proto' into (host, port, proto)."""
    parts = cs.split(":")
    if len(parts) >= 3:
        return ":".join(parts[:-2]), parts[-2], parts[-1]
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return cs, "", ""


def _server_rows(nodes: list[Node]) -> list[dict]:
    """Pair up the per-row nodes of the server list into structured rows.

    Each row has exactly one description / connection_string / checkbox /
    delete / edit (paired by vertical order) and an optional error string
    (matched to the nearest row)."""
    by_y = lambda ns: sorted(ns, key=lambda n: n.center[1])
    descs = by_y(_ids(nodes, "manage_ports_description"))
    conns = by_y(_ids(nodes, "manage_ports_connection_string"))
    checks = by_y(_ids(nodes, "manage_ports_checkbox"))
    dels = by_y(_ids(nodes, "manage_ports_delete"))
    edits = by_y(_ids(nodes, "manage_ports_edit"))
    errs = _ids(nodes, "manage_ports_error_string")
    rows = []
    for i, conn in enumerate(conns):
        host, port, proto = _parse_connstr(conn.text)
        cy = conn.center[1]
        err = next((e.text for e in errs if abs(e.center[1] - cy) < 140), "")
        enabled = checks[i].checked if i < len(checks) else None
        rows.append({
            "name": descs[i].text if i < len(descs) else "",
            "connect_string": conn.text,
            "host": host, "port": port, "protocol": proto,
            "enabled": enabled,          # the on/off checkbox (reliable)
            "status": err or "",         # ATAK's status/error line; "" when idle/ok
            "_delete_xy": dels[i].center if i < len(dels) else None,
            "_edit_xy": edits[i].center if i < len(edits) else None,
            "_checkbox_xy": checks[i].center if i < len(checks) else None,
        })
    return rows


def _on_server_screen(nodes: list[Node]) -> bool:
    # `netlist` is the server-list ListView; it is the unambiguous marker.
    # ("My Primary IP Address" also shows on the Network Connection Prefs screen
    # one level up, so it cannot be used here.)
    return bool(_ids(nodes, "netlist"))


def _scroll_to_text(query: str, max_swipes: int = 6, serial: Optional[str] = None) -> bool:
    """Scroll the ATAK menu down to ``query``. Blind-swipes the first couple of
    times (the target lives near the bottom) to save uiautomator dumps."""
    if exists(query, by="text", serial=serial):
        return True
    for i in range(max_swipes):
        swipe(2100, 950, 2100, 430, 250, serial)
        time.sleep(0.25)
        if i >= 2 and exists(query, by="text", serial=serial):  # 3 blind swipes first
            return True
    return exists(query, by="text", serial=serial)


def _wait_tap(query: str, by: str = "text", timeout: float = 8.0,
              serial: Optional[str] = None) -> None:
    """Poll until ``query`` appears, then tap it -- using the SAME dump that
    found it (no separate tap dump). Prefers a clickable match. This halves the
    dumps per screen transition, which matters: uiautomator dump is the slow
    step on a real device."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            matches = find(query, by=by, nodes=dump(serial, attempts=1))
            if matches:
                node = next((m for m in matches if m.clickable), matches[0])
                tap_xy(*node.center, serial=serial)
                return
        except AdbError as e:
            last = e
        time.sleep(0.5)
    raise AdbError(f"timed out waiting to tap {query!r}; last={last}")


def _open_server_connections(serial: Optional[str] = None) -> None:
    """Navigate to ATAK's Manage Server Connections screen. Idempotent: returns
    at once if already there, so a list call followed by edit/remove does not
    re-walk Settings. Fast path covers the two common start states (the map and
    the list); a slower cascade handles being mid-Settings."""
    nodes = dump(serial)
    if _on_server_screen(nodes):
        return
    menu = next((n for n in nodes
                 if n.resource_id.endswith("/tak_nav_menu_button")), None)
    if menu is not None:
        tap_xy(*menu.center, serial=serial)        # reuse the dump we just took
        time.sleep(0.8)
        if not _scroll_to_text("Settings", serial=serial):
            raise AdbError("could not find 'Settings' in the ATAK menu")
        _wait_tap("Settings", serial=serial)
        _wait_tap("Network Preferences", serial=serial)
        _wait_tap("Network Connection Preferences", serial=serial)
        _wait_tap("Manage Server Connections", serial=serial)
        wait_for("netlist", by="id", timeout=8, serial=serial)
        return
    # mid-Settings fallback: step toward the list one screen at a time.
    for _ in range(6):
        nodes = dump(serial)
        if _on_server_screen(nodes):
            return
        has = lambda t: any(t in (n.text or "") for n in nodes)
        if has("Manage Server Connections"):
            _wait_tap("Manage Server Connections", serial=serial)
            wait_for("netlist", by="id", timeout=8, serial=serial)
            return
        if has("Network Connection Preferences"):
            _wait_tap("Network Connection Preferences", serial=serial)
            wait_for("Manage Server Connections", timeout=8, serial=serial)
        elif has("Network Preferences"):
            _wait_tap("Network Preferences", serial=serial)
            wait_for("Network Connection Preferences", timeout=8, serial=serial)
        else:
            raise AdbError("cannot reach server connections from the current screen")
    raise AdbError("failed to reach Manage Server Connections")


def list_servers(serial: Optional[str] = None) -> list[dict]:
    """List ATAK's configured server connections with per-connection data:
    name, host, port, protocol, enabled, connected, status. Returns rows that
    are on screen (scroll for very long lists is not yet handled)."""
    _open_server_connections(serial)
    time.sleep(0.4)
    return [
        {k: v for k, v in r.items() if not k.startswith("_")}
        for r in _server_rows(dump(serial))
    ]


def _find_row(name: str, serial: Optional[str]) -> dict:
    row = next((r for r in _server_rows(dump(serial)) if r["name"] == name), None)
    if not row:
        raise AdbError(f"no server connection named {name!r}")
    return row


def add_server(
    name: str,
    host: str,
    port,
    protocol: str = "tcp",
    serial: Optional[str] = None,
) -> str:
    """Add a streaming CoT (TAK server) connection via the Add form."""
    radio = _PROTO_RADIO.get(protocol.lower())
    if not radio:
        raise AdbError(f"protocol must be tcp|ssl|quic, got {protocol!r}")
    _open_server_connections(serial)
    tap("More options", by="desc", serial=serial)
    wait_for("Add", by="text", timeout=6, serial=serial)
    tap("Add", by="text", exact=True, serial=serial)
    wait_for("add_host", by="id", timeout=6, serial=serial)
    tap("add_description", by="id", serial=serial); time.sleep(0.4)
    text_input(name, serial)
    tap("add_host", by="id", serial=serial); time.sleep(0.4)
    text_input(host, serial)
    key("BACK", serial); time.sleep(0.6)            # hide keyboard
    tap("advanced_options_cb", by="id", serial=serial); time.sleep(0.6)
    tap(radio, by="id", serial=serial); time.sleep(0.4)   # sets default port
    tap("add_port", by="id", serial=serial); time.sleep(0.3)
    clear_text(8, serial); time.sleep(0.3)
    text_input(str(port), serial); time.sleep(0.3)
    key("BACK", serial); time.sleep(0.6)            # hide keyboard
    tap("add_net_button", by="id", serial=serial)   # OK
    time.sleep(1.0)
    return f"added {name!r} -> {host}:{port}:{protocol.lower()}"


def remove_server(name: str, serial: Optional[str] = None) -> str:
    """Remove the server connection named ``name`` (taps its delete + confirm)."""
    _open_server_connections(serial)
    row = _find_row(name, serial)
    tap_xy(*row["_delete_xy"], serial=serial)
    time.sleep(1.0)
    nodes = dump(serial)                       # one dump for the confirm dialog
    for label in ("Delete", "Yes", "OK", "Confirm"):
        m = [n for n in find(label, by="text", nodes=nodes, exact=True) if n.clickable]
        if m:
            tap_xy(*m[0].center, serial=serial)
            return f"removed {name!r}"
    return f"tapped delete for {name!r} (no confirm dialog appeared)"


def set_server_enabled(name: str, enabled: bool, serial: Optional[str] = None) -> str:
    """Enable/disable a server connection by toggling its checkbox."""
    _open_server_connections(serial)
    row = _find_row(name, serial)
    if row["enabled"] == enabled:
        return f"{name!r} already {'enabled' if enabled else 'disabled'}"
    tap_xy(*row["_checkbox_xy"], serial=serial)
    return f"{'enabled' if enabled else 'disabled'} {name!r}"


def edit_server(
    name: str,
    new_name: Optional[str] = None,
    new_host: Optional[str] = None,
    new_port=None,
    new_protocol: Optional[str] = None,
    serial: Optional[str] = None,
) -> str:
    """Edit a server connection: change any of name/host/port/protocol."""
    _open_server_connections(serial)
    row = _find_row(name, serial)
    tap_xy(*row["_edit_xy"], serial=serial)
    wait_for("add_host", by="id", timeout=6, serial=serial)
    typed = False
    if new_name is not None:
        tap("add_description", by="id", serial=serial); time.sleep(0.3)
        clear_text(40, serial); text_input(new_name, serial); typed = True
    if new_host is not None:
        tap("add_host", by="id", serial=serial); time.sleep(0.3)
        clear_text(60, serial); text_input(new_host, serial); typed = True
    if typed:                       # only dismiss the keyboard if we opened it;
        key("BACK", serial); time.sleep(0.5)   # a stray BACK closes the dialog
    if new_protocol is not None or new_port is not None:
        if not exists("add_port", by="id", serial=serial):
            tap("advanced_options_cb", by="id", serial=serial); time.sleep(0.5)
        if new_protocol is not None:
            radio = _PROTO_RADIO.get(new_protocol.lower())
            if not radio:
                raise AdbError(f"protocol must be tcp|ssl|quic, got {new_protocol!r}")
            tap(radio, by="id", serial=serial); time.sleep(0.3)
        if new_port is not None:
            tap("add_port", by="id", serial=serial); time.sleep(0.3)
            clear_text(8, serial); text_input(str(new_port), serial)
        key("BACK", serial); time.sleep(0.5)
    tap("add_net_button", by="id", serial=serial)
    time.sleep(1.0)
    return f"edited {name!r}"
