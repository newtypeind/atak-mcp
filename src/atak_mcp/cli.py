# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Newtype Industries
"""Command line front end for the ATAK bridge.

Examples
--------
    python -m atak_mcp.cli devices
    python -m atak_mcp.cli screenshot -o /tmp/atak.png
    python -m atak_mcp.cli find "Record" --json
    python -m atak_mcp.cli tap "Record"
    python -m atak_mcp.cli tap --xy 540 1200
    python -m atak_mcp.cli logcat --grep BarbaraBabel -n 300
    python -m atak_mcp.cli reload com.atakmap.android.barbarababel app.apk
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__, bridge, update
from .bridge import AdbError


def _print_nodes(nodes, as_json: bool):
    if as_json:
        print(json.dumps([n.as_dict() for n in nodes], ensure_ascii=False, indent=2))
        return
    if not nodes:
        print("(no matches)")
        return
    for n in nodes:
        cx, cy = n.center
        flags = "".join(
            [("C" if n.clickable else "-"), ("E" if n.enabled else "-")]
        )
        print(f"[{flags}] @({cx},{cy}) {n.bounds}  {n.label()!r}  <{n.cls}>")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="atak-mcp", description="Drive ATAK over adb.")
    p.add_argument("--serial", help="adb device serial (default: $ANDROID_SERIAL)")
    p.add_argument("--version", action="version", version=f"atak-mcp {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("devices", help="list attached devices")
    sub.add_parser("update-check", help="check GitHub for a newer atak-mcp release")

    sp = sub.add_parser("screenshot", help="capture a PNG screenshot")
    sp.add_argument("-o", "--out", default="/tmp/atak_mcp_screen.png")

    sp = sub.add_parser("dump", help="dump the UI hierarchy")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--clickable", action="store_true", help="only clickable nodes")

    sp = sub.add_parser("find", help="find nodes by text/id/desc")
    sp.add_argument("query")
    sp.add_argument("--by", choices=["any", "text", "id", "desc"], default="any")
    sp.add_argument("--exact", action="store_true", help="require exact match")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("tap", help="tap a node (by query) or raw coordinates")
    sp.add_argument("query", nargs="?")
    sp.add_argument("--by", choices=["any", "text", "id", "desc"], default="any")
    sp.add_argument("--index", type=int, default=0)
    sp.add_argument("--exact", action="store_true", help="require exact match")
    sp.add_argument("--xy", nargs=2, type=int, metavar=("X", "Y"))

    sp = sub.add_parser("wait", help="wait for a node to appear")
    sp.add_argument("query")
    sp.add_argument("--by", choices=["any", "text", "id", "desc"], default="any")
    sp.add_argument("--timeout", type=float, default=10.0)

    sp = sub.add_parser("swipe", help="swipe between two points")
    for a in ("x1", "y1", "x2", "y2"):
        sp.add_argument(a, type=int)
    sp.add_argument("--ms", type=int, default=300)

    sp = sub.add_parser("text", help="type text into the focused field")
    sp.add_argument("value")

    sp = sub.add_parser("key", help="send a key event (BACK, HOME, ENTER, ...)")
    sp.add_argument("code")

    sp = sub.add_parser("logcat", help="dump tail of logcat")
    sp.add_argument("-n", "--lines", type=int, default=200)
    sp.add_argument("--grep")
    sp.add_argument("--clear", action="store_true")

    sub.add_parser("list-plugins", help="list installed ATAK plugins")
    sp = sub.add_parser("list-packages", help="list packages")
    sp.add_argument("--all", action="store_true", help="include system packages")

    sp = sub.add_parser("install", help="install an apk (-r -g)")
    sp.add_argument("apk")

    sp = sub.add_parser("uninstall", help="uninstall a package")
    sp.add_argument("package")

    sp = sub.add_parser("reload", help="uninstall+install a plugin package")
    sp.add_argument("package")
    sp.add_argument("apk")

    sp = sub.add_parser("confirm-load", help="confirm ATAK's 'load plugin?' prompt")
    sp.add_argument("--timeout", type=float, default=25.0)

    sp = sub.add_parser("launch", help="launch ATAK (or another package)")
    sp.add_argument("package", nargs="?", default=bridge.ATAK_CIV_PACKAGE)

    sub.add_parser("foreground", help="show the resumed top activity")

    sp = sub.add_parser("push", help="copy a local file to the device")
    sp.add_argument("local")
    sp.add_argument("remote", help="device path, e.g. /sdcard/Download/cert.p12")

    sp = sub.add_parser("broadcast", help="send a broadcast Intent (am broadcast)")
    sp.add_argument("action", help="intent action, e.g. com.atakmap.app.IMPORT")
    sp.add_argument("-n", "--component", help="explicit target component")
    sp.add_argument("--es", nargs=2, action="append", default=[],
                    metavar=("KEY", "VAL"), help="string extra (repeatable)")
    sp.add_argument("--ei", nargs=2, action="append", default=[],
                    metavar=("KEY", "VAL"), help="int extra (repeatable)")
    sp.add_argument("--ez", nargs=2, action="append", default=[],
                    metavar=("KEY", "VAL"), help="boolean extra (repeatable)")

    sp = sub.add_parser("pull", help="copy a file off the device")
    sp.add_argument("remote")
    sp.add_argument("local")

    sp = sub.add_parser("long-press", help="long-press a node or coordinates")
    sp.add_argument("query", nargs="?")
    sp.add_argument("--by", choices=["any", "text", "id", "desc"], default="any")
    sp.add_argument("--exact", action="store_true")
    sp.add_argument("--ms", type=int, default=600)
    sp.add_argument("--xy", nargs=2, type=int, metavar=("X", "Y"))

    sp = sub.add_parser("double-tap", help="double-tap a node or coordinates")
    sp.add_argument("query", nargs="?")
    sp.add_argument("--by", choices=["any", "text", "id", "desc"], default="any")
    sp.add_argument("--exact", action="store_true")
    sp.add_argument("--xy", nargs=2, type=int, metavar=("X", "Y"))

    sp = sub.add_parser("clear-text", help="clear the focused text field")
    sp.add_argument("-n", "--count", type=int, default=120)

    sp = sub.add_parser("exists", help="exit 0 if a node matches, else 1")
    sp.add_argument("query")
    sp.add_argument("--by", choices=["any", "text", "id", "desc"], default="any")

    sp = sub.add_parser("wait-gone", help="wait until a node disappears")
    sp.add_argument("query")
    sp.add_argument("--by", choices=["any", "text", "id", "desc"], default="any")
    sp.add_argument("--timeout", type=float, default=10.0)

    sub.add_parser("wake", help="wake screen + dismiss non-secure lockscreen")
    sp = sub.add_parser("stay-awake", help="keep screen on while charging")
    sp.add_argument("--off", action="store_true")

    sp = sub.add_parser("is-running", help="exit 0 if the package is running")
    sp.add_argument("package", nargs="?", default=bridge.ATAK_CIV_PACKAGE)

    sp = sub.add_parser("force-stop", help="force-stop a package")
    sp.add_argument("package", nargs="?", default=bridge.ATAK_CIV_PACKAGE)

    sp = sub.add_parser("clear-data", help="wipe app data (pm clear)")
    sp.add_argument("package", nargs="?", default=bridge.ATAK_CIV_PACKAGE)

    sp = sub.add_parser("restart", help="force-stop then launch")
    sp.add_argument("package", nargs="?", default=bridge.ATAK_CIV_PACKAGE)

    sp = sub.add_parser("grant", help="grant a runtime permission")
    sp.add_argument("package")
    sp.add_argument("permission")

    sp = sub.add_parser("revoke", help="revoke a runtime permission")
    sp.add_argument("package")
    sp.add_argument("permission")

    sp = sub.add_parser("crashes", help="show the crash log buffer")
    sp.add_argument("package", nargs="?")
    sp.add_argument("-n", "--lines", type=int, default=500)

    sp = sub.add_parser("record-start", help="start screenrecord (detached)")
    sp.add_argument("--remote", default="/sdcard/atak_mcp_record.mp4")
    sp.add_argument("--time-limit", type=int, default=180)

    sp = sub.add_parser("record-stop", help="stop screenrecord, optionally pull it")
    sp.add_argument("--remote", default="/sdcard/atak_mcp_record.mp4")
    sp.add_argument("-o", "--out", help="pull the mp4 to this local path")

    sp = sub.add_parser("ready", help="wait until ATAK is up and dumpable")
    sp.add_argument("package", nargs="?", default=bridge.ATAK_CIV_PACKAGE)
    sp.add_argument("--timeout", type=float, default=60.0)

    sp = sub.add_parser("open-tool", help="open an ATAK tool/plugin via the Tools menu")
    sp.add_argument("name")
    sp.add_argument("--timeout", type=float, default=10.0)

    sp = sub.add_parser("deploy", help="reload+launch+confirm+wait (full plugin loop)")
    sp.add_argument("package")
    sp.add_argument("apk")

    sp = sub.add_parser("enroll", help="configure a TAK server via the enroll deep link")
    sp.add_argument("host", help="server host, e.g. tak.example.com:8089:ssl")
    sp.add_argument("--username")
    sp.add_argument("--token", help="password or enrollment token")
    sp.add_argument("--no-verify", action="store_true", help="don't confirm via logcat")

    sp = sub.add_parser("import-url", help="import a file/data package from a URL")
    sp.add_argument("url")
    sp.add_argument("--no-verify", action="store_true")

    sp = sub.add_parser("deeplink", help="open a raw tak: deep link")
    sp.add_argument("uri")
    sp.add_argument("--no-verify", action="store_true")

    sp = sub.add_parser("version", help="installed ATAK version(s)")
    sp.add_argument("package", nargs="?", default=bridge.ATAK_CIV_PACKAGE)

    sp = sub.add_parser("doctor", help="probe device for ATAK version-drift / capabilities")
    sp.add_argument("package", nargs="?", default=bridge.ATAK_CIV_PACKAGE)

    sub.add_parser("servers", help="list ATAK server connections (JSON)")

    sp = sub.add_parser("add-server", help="add a TAK server connection")
    sp.add_argument("name")
    sp.add_argument("host")
    sp.add_argument("port")
    sp.add_argument("--proto", choices=["tcp", "ssl", "quic"], default="tcp")

    sp = sub.add_parser("rm-server", help="remove a server connection by name")
    sp.add_argument("name")

    sp = sub.add_parser("edit-server", help="edit a server connection")
    sp.add_argument("name")
    sp.add_argument("--name", dest="new_name")
    sp.add_argument("--host", dest="new_host")
    sp.add_argument("--port", dest="new_port")
    sp.add_argument("--proto", dest="new_proto", choices=["tcp", "ssl", "quic"])

    sp = sub.add_parser("set-server", help="enable/disable a server connection")
    sp.add_argument("name")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--on", action="store_true")
    g.add_argument("--off", action="store_true")

    args = p.parse_args(argv)
    s = args.serial

    try:
        if args.cmd == "devices":
            print(json.dumps(bridge.devices(), indent=2))
        elif args.cmd == "update-check":
            print(json.dumps(update.check_update(), indent=2, ensure_ascii=False))
        elif args.cmd == "screenshot":
            print(bridge.screenshot(args.out, s))
        elif args.cmd == "dump":
            nodes = bridge.dump(s)
            if args.clickable:
                nodes = [n for n in nodes if n.clickable]
            _print_nodes(nodes, args.json)
        elif args.cmd == "find":
            _print_nodes(
                bridge.find(args.query, by=args.by, exact=args.exact, serial=s),
                args.json,
            )
        elif args.cmd == "tap":
            if args.xy:
                bridge.tap_xy(args.xy[0], args.xy[1], s)
                print(f"tapped ({args.xy[0]},{args.xy[1]})")
            elif args.query:
                n = bridge.tap(
                    args.query, by=args.by, index=args.index, exact=args.exact, serial=s,
                )
                print(f"tapped {n.label()!r} @{n.center}")
            else:
                print("tap: provide a query or --xy X Y", file=sys.stderr)
                return 2
        elif args.cmd == "wait":
            n = bridge.wait_for(args.query, by=args.by, timeout=args.timeout, serial=s)
            print(f"found {n.label()!r} @{n.center}")
        elif args.cmd == "swipe":
            bridge.swipe(args.x1, args.y1, args.x2, args.y2, args.ms, s)
            print("ok")
        elif args.cmd == "text":
            bridge.text_input(args.value, s)
            print("ok")
        elif args.cmd == "key":
            bridge.key(args.code, s)
            print("ok")
        elif args.cmd == "logcat":
            if args.clear:
                bridge.logcat_clear(s)
            print(bridge.logcat(lines=args.lines, grep=args.grep, serial=s))
        elif args.cmd == "list-plugins":
            print("\n".join(bridge.list_plugins(s)) or "(none)")
        elif args.cmd == "list-packages":
            print("\n".join(bridge.list_packages(not args.all, s)))
        elif args.cmd == "install":
            print(bridge.install(args.apk, s))
        elif args.cmd == "uninstall":
            print(bridge.uninstall(args.package, s))
        elif args.cmd == "reload":
            print(bridge.reload_plugin(args.package, args.apk, s))
        elif args.cmd == "confirm-load":
            print(bridge.confirm_load(timeout=args.timeout, serial=s))
        elif args.cmd == "launch":
            print(bridge.launch_atak(args.package, s) or "launched")
        elif args.cmd == "foreground":
            print(bridge.foreground_app(s) or "(unknown)")
        elif args.cmd == "push":
            print(bridge.push(args.local, args.remote, s))
        elif args.cmd == "broadcast":
            extras = (
                [("s", k, v) for k, v in args.es]
                + [("i", k, v) for k, v in args.ei]
                + [("z", k, v) for k, v in args.ez]
            )
            print(bridge.broadcast(args.action, args.component, extras, s))
        elif args.cmd == "pull":
            print(bridge.pull(args.remote, args.local, s))
        elif args.cmd == "long-press":
            if args.xy:
                bridge.long_press_xy(args.xy[0], args.xy[1], args.ms, s)
                print(f"long-pressed ({args.xy[0]},{args.xy[1]})")
            elif args.query:
                n = bridge.long_press(args.query, by=args.by, exact=args.exact,
                                      ms=args.ms, serial=s)
                print(f"long-pressed {n.label()!r} @{n.center}")
            else:
                print("long-press: provide a query or --xy X Y", file=sys.stderr)
                return 2
        elif args.cmd == "double-tap":
            if args.xy:
                bridge.double_tap_xy(args.xy[0], args.xy[1], s)
                print(f"double-tapped ({args.xy[0]},{args.xy[1]})")
            elif args.query:
                n = bridge.double_tap(args.query, by=args.by, exact=args.exact, serial=s)
                print(f"double-tapped {n.label()!r} @{n.center}")
            else:
                print("double-tap: provide a query or --xy X Y", file=sys.stderr)
                return 2
        elif args.cmd == "clear-text":
            bridge.clear_text(args.count, s)
            print("ok")
        elif args.cmd == "exists":
            ok = bridge.exists(args.query, by=args.by, serial=s)
            print("yes" if ok else "no")
            return 0 if ok else 1
        elif args.cmd == "wait-gone":
            bridge.wait_gone(args.query, by=args.by, timeout=args.timeout, serial=s)
            print("gone")
        elif args.cmd == "wake":
            bridge.wake_unlock(s)
            print("ok")
        elif args.cmd == "stay-awake":
            print(bridge.stay_awake(not args.off, s))
        elif args.cmd == "is-running":
            run = bridge.is_running(args.package, s)
            print("running" if run else "stopped")
            return 0 if run else 1
        elif args.cmd == "force-stop":
            print(bridge.force_stop(args.package, s) or "ok")
        elif args.cmd == "clear-data":
            print(bridge.clear_app_data(args.package, s))
        elif args.cmd == "restart":
            print(bridge.restart_atak(args.package, s) or "restarted")
        elif args.cmd == "grant":
            print(bridge.grant_permission(args.package, args.permission, s) or "ok")
        elif args.cmd == "revoke":
            print(bridge.revoke_permission(args.package, args.permission, s) or "ok")
        elif args.cmd == "crashes":
            out = bridge.crashes(args.package, lines=args.lines, serial=s)
            print(out or "(no crashes)")
        elif args.cmd == "record-start":
            print(bridge.record_start(args.remote, args.time_limit, s))
        elif args.cmd == "record-stop":
            print(bridge.record_stop(args.remote, args.out, s))
        elif args.cmd == "ready":
            print(bridge.wait_atak_ready(args.package, args.timeout, s))
        elif args.cmd == "open-tool":
            n = bridge.open_tool(args.name, timeout=args.timeout, serial=s)
            print(f"opened {n.label()!r} @{n.center}")
        elif args.cmd == "deploy":
            print(json.dumps(bridge.deploy_plugin(args.package, args.apk, s), indent=2))
        elif args.cmd == "enroll":
            print(bridge.enroll(args.host, args.username, args.token, s,
                                verify=not args.no_verify))
        elif args.cmd == "import-url":
            print(bridge.import_url(args.url, s, verify=not args.no_verify))
        elif args.cmd == "deeplink":
            print(bridge.deep_link(args.uri, s, verify=not args.no_verify))
        elif args.cmd == "version":
            print(bridge.atak_version(args.package, s) or "(not installed)")
        elif args.cmd == "doctor":
            report = bridge.doctor(args.package, s)
            print(json.dumps(report, indent=2, ensure_ascii=False))
        elif args.cmd == "servers":
            print(json.dumps(bridge.list_servers(s), indent=2, ensure_ascii=False))
        elif args.cmd == "add-server":
            print(bridge.add_server(args.name, args.host, args.port, args.proto, s))
        elif args.cmd == "rm-server":
            print(bridge.remove_server(args.name, s))
        elif args.cmd == "edit-server":
            print(bridge.edit_server(args.name, args.new_name, args.new_host,
                                     args.new_port, args.new_proto, s))
        elif args.cmd == "set-server":
            print(bridge.set_server_enabled(args.name, args.on, s))
    except AdbError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
