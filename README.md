# atak-mcp

Drive **ATAK** (Android Team Awareness Kit) and its plugins over `adb` without
guessing pixel coordinates. `atak-mcp` reads the on-screen UI tree from
`uiautomator`, finds elements by text / resource id / content description, and
taps the centre of their bounds. It ships as a plain command-line tool **and** as
a [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server, so both
humans and AI agents can build, install, open, and exercise ATAK plugins
hands-free.

It exists because the usual ATAK plugin dev loop is "build, install, then poke at
the screen by hand". That does not work for CI or for AI agents, and blind
`adb shell input tap x y` breaks the moment the layout shifts. `atak-mcp` makes
the loop scriptable and deterministic.

> Works with Jetpack Compose UIs too: Compose publishes its semantics tree to
> Android accessibility, which is exactly what `uiautomator` reads, so a
> `Button { Text("Record") }` is findable by the text "Record".

## Requirements

- [`adb`](https://developer.android.com/tools/adb) on your `PATH`, with a device
  or emulator authorized for debugging
- [`uv`](https://docs.astral.sh/uv/) (recommended) — installs and runs the tool
  in an isolated environment, no manual `pip`/venv needed
- Python 3.10+ (uv can provide this for you)

Install `uv` if you do not have it:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# or Homebrew
brew install uv
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## Quick start (no clone needed)

`uvx` (an alias for `uv tool run`) runs the tool straight from this repository in
a throwaway environment:

```bash
# Pinned to a release (recommended for reproducibility)
uvx --from git+https://github.com/newtypeind/atak-mcp@v0.2.0 atak-mcp devices

# Or always track the latest main
uvx --from git+https://github.com/newtypeind/atak-mcp atak-mcp devices
```

Two console commands are provided:

- `atak-mcp` — the CLI (for humans and shell scripts)
- `atak-mcp-server` — the MCP stdio server (for AI agents)

## Use as an MCP server

### Claude Code

Add it to a project with the CLI:

```bash
claude mcp add atak -- uvx --from git+https://github.com/newtypeind/atak-mcp@v0.2.0 atak-mcp-server
```

or commit a project-scoped `.mcp.json` so your whole team (and any agent) gets it:

```json
{
  "mcpServers": {
    "atak": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/newtypeind/atak-mcp@v0.2.0",
        "atak-mcp-server"
      ]
    }
  }
}
```

### Claude Desktop / other MCP clients

Add the same block to the client's MCP config (for Claude Desktop that is
`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "atak": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/newtypeind/atak-mcp@v0.2.0", "atak-mcp-server"]
    }
  }
}
```

Select a device with the `ANDROID_SERIAL` environment variable when more than one
is attached (add an `"env": { "ANDROID_SERIAL": "..." }` key to the server entry).

### Updating atak-mcp

Your MCP client launches the server fresh each session with `uvx`, so "updating"
is really about which version `uvx` resolves. Pick one of:

1. **Pin a release tag (recommended).** Keep `@v0.2.0` in the config. `uvx`
   caches and reuses it, so the version is reproducible. To update, bump the tag
   (e.g. `@v0.3.0`) and restart the client — you decide when.

2. **Track `main`, auto-refresh.** Drop the tag and add `--refresh`, so every
   launch re-resolves the branch and rebuilds if it moved:

   ```json
   "args": ["--refresh", "--from", "git+https://github.com/newtypeind/atak-mcp", "atak-mcp-server"]
   ```

   Always latest, at the cost of a few seconds' startup and no reproducibility.

3. **One-off refresh.** Force a re-pull without editing the config:
   `uvx --refresh --from git+https://github.com/newtypeind/atak-mcp@main atak-mcp-server`,
   or clear the cache with `uv cache clean`.

To know *when* an update is worth pulling, ask the server: the `check_update`
tool (or `atak-mcp update-check`) compares the running version to the latest
GitHub release tag and reports `update_available` plus a one-line hint;
`mcp_version` / `atak-mcp --version` print the installed version. An agent can
call `check_update` at the start of a session and tell you if you're behind.

### Tools exposed

Screen & input: `screenshot` (returns the image plus geometry: device/image
size, scale, rotation), `ui_dump`, `find` (with `scroll`), `exists`, `tap`
(by query, raw `x`/`y`, or normalized `nx`/`ny` in [0,1], and a `scroll` option),
`scroll_into_view`, `long_press`, `double_tap`, `swipe` (pixels or normalized),
`type_text`, `clear_text`, `press_key`, `wait_for`, `wait_gone`.

Normalized coordinates and the screenshot geometry let a caller act on a
screenshot-relative position even when the displayed image is downscaled
client-side (`tap = screenshot_pixel / scale`, or just pass `nx`/`ny`). `find
--scroll` / `scroll_into_view` bring an off-screen list item into view first.

Device & lifecycle: `list_devices`, `wake_unlock`, `stay_awake`, `is_running`,
`launch_atak`, `restart_atak`, `force_stop`, `clear_app_data`, `grant_permission`,
`push_file`, `pull_file`, `broadcast`.

Plugins & diagnostics: `list_plugins`, `install_apk`, `reload_plugin`,
`confirm_load`, `deploy_plugin`, `wait_atak_ready`, `open_tool`, `logcat`,
`crashes`, `record_start`, `record_stop`.

ATAK deep links: `enroll` (configure a TAK server), `import_url` (import from a
URL), `deep_link` (any raw `tak:` URI). These are the supported way to drive
ATAK from outside the app; see *Configure a TAK server* for the Android 13+
limits that make them necessary.

Server connections: `list_servers` (name, host, port, protocol, enabled,
status), `add_server`, `edit_server`, `remove_server`, `set_server_enabled`.

Version / health: `atak_version` (the installed ATAK version), `doctor` (probe
the device for version drift and capabilities), `mcp_version` (this tool's own
version), `check_update` (is a newer atak-mcp release out — see *Updating
atak-mcp*).

## Use as a CLI

```bash
A="uvx --from git+https://github.com/newtypeind/atak-mcp@v0.2.0 atak-mcp"

$A devices                       # list attached devices
$A screenshot -o /tmp/atak.png   # capture screen (handles the foldable warning)
$A dump --clickable              # list clickable nodes with centres
$A find "Record" --by desc       # locate a node by content description
$A tap "Barbara Babel" --by text # tap a node by its label (prefers clickable)
$A tap --xy 540 1200             # tap raw coordinates
$A wait "My Location" --timeout 15
$A logcat --grep BarbaraBabel -n 300
$A list-plugins                  # installed ATAK plugins
$A reload com.example.plugin path/to/app.apk
$A confirm-load                  # confirm ATAK's "load this plugin?" dialog
$A launch                        # foreground ATAK civ

# Whole plugin loop in one call, then check it did not crash
$A deploy com.example.plugin path/to/app.apk   # reload + launch + confirm + wait ready
$A open-tool "Your Plugin Name"                # Tools menu -> your plugin
$A crashes com.example.plugin                  # empty == no crash

# Configure ATAK via its deep links (the supported external entry point)
$A enroll tak.example.com:8089:ssl --username alice --token s3cr3t  # add a TAK server
$A import-url https://files.example.com/overlay.zip                 # import from a URL
$A deeplink "tak://com.atakmap.app/import?url=https://host/x.kml"   # any tak: link

# CI hygiene
$A clear-data && $A launch && $A ready          # fresh state, then wait until up
$A grant com.atakmap.app.civ android.permission.ACCESS_FINE_LOCATION
$A record-start ; sleep 10 ; $A record-stop -o run.mp4
```

### Open a plugin without hunting for it

ATAK plugins register a toolbar item that appears, with its label, in the Tools
menu. So opening one is just:

```bash
$A tap "Tools"
$A tap "Your Plugin Name"
```

### Configure a TAK server, import data

ATAK exposes a `tak:` deep-link activity, and that is the supported way to drive
it from outside the app. `enroll` adds/configures a server connection and
`import-url` pulls in a file or data package:

```bash
$A enroll tak.example.com:8089:ssl --username alice --token s3cr3t
$A import-url https://files.example.com/server.zip
```

These were confirmed by decompiling ATAK-CIV 5.x: the `enroll` link is handled
with `host`/`username`/`token` parameters, and `import` with a `url` parameter.

`import_url` pops a "Import &lt;url&gt;? Yes/No" confirmation in ATAK; tap it
through with `tap "Yes"` (or `tap "No"` to cancel).

### Manage server connections

`enroll` is specifically the SSL certificate-enrollment flow and only adds a
connection once it reaches a real TAK server. To create/inspect/change plain
TCP/SSL/QUIC streaming connections, use the server-connection commands, which
drive ATAK's "Manage Server Connections" screen by resource id:

```bash
$A servers                                      # list connections (JSON)
$A add-server hq 1.2.3.4 8087 --proto tcp       # add a TCP connection
$A edit-server hq --port 8088                   # change a field
$A set-server hq --off                          # disable (or --on)
$A rm-server hq                                  # delete
```

`servers` reports, per connection: `name`, `host`, `port`, `protocol`,
`enabled` (the on/off checkbox) and `status` (ATAK's status/error line, empty
when idle). Speed note: these walk five screens deep into Settings, and a
single uiautomator dump is the slow step (~2s on the test foldable), so the
first call is ~20s on that device; once on the list, follow-up calls (another
`servers`, a `set-server`) are ~4-5s. A faster device is proportionally
quicker.

A note on limits. On Android 13+, ATAK registers its internal broadcast
receivers as `NOT_EXPORTED` behind a signature permission, so `adb shell am
broadcast` cannot reach them. That rules out driving map actions like
panning/zooming (`com.atakmap.android.maps.FOCUS`) or marker creation straight
from adb: those need to run inside ATAK's process (a plugin) or arrive over a
TAK server. The deep links above and the UI automation (`tap`/`type`/`find`)
remain the dependable external surface.

### Managing ATAK version drift

The bridge is verified against the ATAK version in `bridge.ATAK_TESTED_VERSION`
(currently `5.6`). The Android-level tools (`tap`/`find`/`pm`/`am`, screenshots,
lifecycle) do not depend on the ATAK version; only the ATAK-internal bits do:
the `tak:` deep-link grammar, resource ids, and the not-exported broadcast
behaviour. Two things keep that manageable:

- Deep-link calls are verified after the fact. `enroll` / `import_url` /
  `deep_link` watch logcat to confirm ATAK actually processed the URI and raise
  if it did not, so a grammar change on a future build surfaces loudly instead
  of failing silently. Pass `--no-verify` to skip.
- `doctor` probes a connected device and reports the installed flavour/version
  against the tested version, whether the deep-link entry point still routes
  (a side-effect-free no-op probe), whether the resource ids the UI helpers use
  are present, and whether internal broadcasts are reachable. Run it against a
  new ATAK release to see what, if anything, changed:

  ```bash
  $A version          # -> 5.6.0.x
  $A doctor           # JSON health report
  ```

When a new ATAK release moves something, `doctor` flags it; re-derive the
deep-link grammar from the installed APK (decompile + a `logcat` check) and
bump `ATAK_TESTED_VERSION`.

## Development

```bash
git clone https://github.com/newtypeind/atak-mcp
cd atak-mcp
uv run atak-mcp devices             # run the CLI from source
uv run atak-mcp-server              # run the MCP server from source
uv build                            # build wheel + sdist into dist/
uv run --extra test pytest          # device-free unit suite
uv run --extra test pytest -m device  # opt-in: needs a connected device
```

Tests live in `tests/` and run without a device: the suite mocks the single
`adb` boundary (see `tests/conftest.py`). CI runs them on every PR (the hard
gate) and reports patch coverage of changed lines as a review guide. New bridge
functions, MCP tools, or CLI commands should ship with a test — see
[CLAUDE.md](CLAUDE.md).

## How it works

```
src/atak_mcp/
  bridge/        # the core, split by concern (standard library only)
    _adb.py      #   low-level adb runner, devices, constants, AdbError
    ui.py        #   screenshot + uiautomator tree (Node, dump, find, wait_for)
    input.py     #   taps, gestures, text entry
    device.py    #   power/lifecycle, packages, permissions, files, logs, diag
    intents.py   #   broadcasts and tak: deep links (enroll, import_url, ...)
    plugins.py   #   install/reload lifecycle and dev-loop composites
    health.py    #   version detection and the doctor check
    servers.py   #   TAK server connection CRUD
  cli.py         # argparse front end
  server.py      # FastMCP stdio server
```

`bridge` shells out to `adb`. Screenshots are captured with
`adb exec-out screencap -p`; on foldables/multi-display devices that command
prepends a warning banner before the PNG bytes, which the bridge strips. The UI
tree comes from `uiautomator dump` (retried, since it refuses to dump mid-
animation). `find`/`tap` match against each node's text, resource id, and content
description, and `tap` prefers a clickable match over a same-text label.

`bridge/__init__.py` re-exports every submodule's public API, so callers use a
flat `bridge.<name>` and don't need to know which file a function lives in.

For configuration that the UI cannot reach, `push` wraps `adb push` (to stage a
certificate or data package on the device) and the `tak:` deep links
(`enroll`/`import_url`/`deep_link`) drive ATAK's supported external entry point,
so an agent is not limited to what is tappable on screen.

## License

Apache-2.0. See [LICENSE](LICENSE).
