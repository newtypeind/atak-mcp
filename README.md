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
uvx --from git+https://github.com/newtypeind/atak-mcp@v0.1.0 atak-mcp devices

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
claude mcp add atak -- uvx --from git+https://github.com/newtypeind/atak-mcp@v0.1.0 atak-mcp-server
```

or commit a project-scoped `.mcp.json` so your whole team (and any agent) gets it:

```json
{
  "mcpServers": {
    "atak": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/newtypeind/atak-mcp@v0.1.0",
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
      "args": ["--from", "git+https://github.com/newtypeind/atak-mcp@v0.1.0", "atak-mcp-server"]
    }
  }
}
```

Select a device with the `ANDROID_SERIAL` environment variable when more than one
is attached (add an `"env": { "ANDROID_SERIAL": "..." }` key to the server entry).

### Tools exposed

`list_devices`, `screenshot` (returns the image **plus** geometry metadata),
`ui_dump`, `find`, `tap`, `scroll_into_view`, `wait_for`, `swipe`, `type_text`,
`press_key`, `logcat`, `list_plugins`, `reload_plugin`, `install_apk`,
`confirm_load`, `launch_atak`.

### Screenshots and tap coordinates

`tap`, `swipe`, and the `ui_dump` bounds all use the device's **native pixel**
coordinate space. The screenshot is returned at that same native resolution (the
bridge never resizes it), but an MCP client often downscales the image it shows
you, so a pixel you read off the displayed image is not the pixel you should tap.

To make the mapping unambiguous, `screenshot` returns a metadata line alongside
the image, for example:

```
device=2640x1080 image=2640x1080 scale=1.0 rotation=1 wm_size=1080x2640
```

- `device` is the current-rotation pixel space that `tap`/`swipe`/`ui_dump` use.
- `image` is the returned PNG's real dimensions; `scale = image / device`.
- `wm_size` is the natural-orientation size from `adb shell wm size`.

`device` is resolved from `adb shell dumpsys display` (falling back to `wm size`
+ rotation). That matters on devices that run an app in forced landscape, e.g. a
Galaxy Z Flip3 where `wm size` reports `1080x2640` and the system rotation reads
`0`, while the live framebuffer and tap space are actually `2640x1080`.

The robust way to act on a screenshot-relative position is **normalized
coordinates** in `[0,1]`, which survive any client-side downscale:

- `tap(nx=0.5, ny=0.5)` taps the screen centre (MCP) / `tap --nxy 0.5 0.5` (CLI).
- `swipe(nx1=.., ny1=.., nx2=.., ny2=..)` (MCP) / `swipe ... --norm` (CLI).

### Reaching off-screen nodes

`find`/`tap` only see what is currently on screen. `scroll_into_view` brings an
off-screen item into view first (a bounded auto-scroll inside the largest
scrollable container, both directions, stopping at the list end), then returns
its node and can tap it. `tap`/`find` also accept a `scroll=true` shortcut.

### logcat

`logcat` greps the **whole** buffered log (`adb logcat -d`), not just a trailing
window, then returns the last `lines` matches (the cap is applied *after*
filtering; pass `lines<=0` for no cap). Add `since` to bound the read to a window
(`adb -t`): a line count (`"500"`) or a timestamp (`"01-30 14:00:00.000"` or
`"2026-01-30 14:00:00.000"`). It always uses `-d` (dump and exit), so it never
hangs.

## Use as a CLI

```bash
A="uvx --from git+https://github.com/newtypeind/atak-mcp@v0.1.0 atak-mcp"

$A devices                       # list attached devices
$A screenshot -o /tmp/atak.png   # capture screen; geometry printed to stderr
$A dump --clickable              # list clickable nodes with centres
$A find "Record" --by desc       # locate a node by content description
$A find "Far Setting" --scroll   # scroll an off-screen match into view, then list it
$A tap "Barbara Babel" --by text # tap a node by its label (prefers clickable)
$A tap --xy 540 1200             # tap raw device pixels
$A tap --nxy 0.5 0.5             # tap by normalized [0,1] coordinates
$A tap "Far Item" --scroll       # scroll into view, then tap
$A scroll-into-view "Far Item" --tap
$A swipe 0.5 0.8 0.5 0.2 --norm  # normalized swipe (scroll up)
$A wait "My Location" --timeout 15
$A logcat --grep BarbaraBabel -n 300                 # greps the whole buffer
$A logcat --grep BarbaraBabel --since "06-05 09:00:00.000"
$A list-plugins                  # installed ATAK plugins
$A reload com.example.plugin path/to/app.apk
$A confirm-load                  # confirm ATAK's "load this plugin?" dialog
$A launch                        # foreground ATAK civ
```

### Open a plugin without hunting for it

ATAK plugins register a toolbar item that appears, with its label, in the Tools
menu. So opening one is just:

```bash
$A tap "Tools"
$A tap "Your Plugin Name"
```

## Development

```bash
git clone https://github.com/newtypeind/atak-mcp
cd atak-mcp
uv run atak-mcp devices          # run the CLI from source
uv run atak-mcp-server           # run the MCP server from source
uv run pytest                    # run the unit tests (no device required)
uv build                         # build wheel + sdist into dist/
```

The unit tests cover the device-independent logic (the logcat command/filter,
normalized-to-pixel conversion, the `dumpsys`/`wm size`/PNG parsers, and the
scroll loop) by stubbing `adb`, so they run without a connected device.

## How it works

```
src/atak_mcp/
  bridge.py   # adb + uiautomator core (standard library only)
  cli.py      # argparse front end
  server.py   # FastMCP stdio server
```

`bridge.py` shells out to `adb`. Screenshots are captured with
`adb exec-out screencap -p` at native resolution (the bridge never resizes
them); on foldables/multi-display devices that command prepends a warning banner
before the PNG bytes, which the bridge strips. The UI tree comes from
`uiautomator dump` (retried, since it refuses to dump mid-animation). `find`/`tap`
match against each node's text, resource id, and content description, and `tap`
prefers a clickable match over a same-text label. Display geometry (for the
screenshot metadata and normalized coordinates) is read from `dumpsys display`
with a `wm size` + rotation fallback.

## License

Apache-2.0. See [LICENSE](LICENSE).
