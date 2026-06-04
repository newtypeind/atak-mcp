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

from . import bridge
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
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("devices", help="list attached devices")

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

    args = p.parse_args(argv)
    s = args.serial

    try:
        if args.cmd == "devices":
            print(json.dumps(bridge.devices(), indent=2))
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
    except AdbError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
