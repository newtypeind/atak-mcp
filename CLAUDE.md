# atak-mcp — working notes for Claude Code

Drive ATAK over `adb`/`uiautomator`: a dependency-free bridge, a CLI, and an MCP
server. The core is `src/atak_mcp/bridge/`, split by concern (`_adb`, `ui`,
`input`, `device`, `intents`, `plugins`, `health`, `servers`); `bridge/__init__.py`
re-exports everything so callers use a flat `bridge.<name>`. `cli.py` and
`server.py` are thin front ends over the bridge.

## Testing is required for new work

When you add or change a bridge function, an MCP tool, or a CLI command, add or
update a test, and make the suite green before you commit:

```bash
uv run --extra test pytest          # device-free unit suite (the default)
uv run --extra test pytest --cov    # with coverage
uv run --extra test pytest -m device  # opt-in: needs a connected adb device
```

CI runs the same suite on every push/PR and gates on it; PRs also gate on **patch
coverage** (changed lines must be ~90% covered), so untested new code is blocked
regardless of whether a session remembers this note.

### How tests work here

Everything funnels through `bridge._adb.adb()`, which is the only place that
shells out. The `fake_run` fixture (in `tests/conftest.py`) patches that one
`subprocess.run`, so tests run without a device: they assert the argv that *would*
have been sent and feed back canned stdout. Tests mirror the modules
(`tests/test_<module>.py`).

What to test for each kind of change:
- **Parsers / pure logic** (`ui.parse_nodes`/`find`, `servers._parse_connstr`/
  `_server_rows`, `update._key`, `_adb.devices`): direct input/output tests, no
  mock needed. Aim high (~90%+) — these are the refactor-fragile parts.
- **adb wrappers** (`input.*`, `intents`, `device` wrappers, `broadcast`,
  `screenshot`): use `fake_run`; assert the argv and the parsing of canned
  output, plus error cases. See `tests/test_intents.py`, `tests/test_input_device.py`.
- **MCP tools** (`server.py`): add the tool to the registration test and, if it
  has real logic, a `call_tool` round-trip with the bridge call monkeypatched.
  See `tests/test_server_mcp.py`.
- **CLI commands**: dispatch test via `cli.main([...])` with the bridge call
  monkeypatched. See `tests/test_cli.py`.
- **Device-timing orchestration** (navigation cascades, polling loops, `doctor`,
  the server-CRUD tap sequences): do NOT chase line coverage with brittle mocked
  sleeps. Cover the pure sub-pieces, and leave the orchestration to opt-in
  `@pytest.mark.device` integration tests.

## Adding an MCP tool / CLI command (the pattern)

1. Implement the function in the right `bridge/` submodule; add it to that
   module's `__all__`.
2. Expose it: a `@mcp.tool()` in `server.py` and/or a subparser + dispatch arm
   in `cli.py`.
3. Add a test (argv/parse for adb wrappers; logic test for parsers; registration
   + `call_tool` for MCP tools).
4. `uv run --extra test pytest` green.

## ATAK version specifics

ATAK-internal behaviour (the `tak:` deep-link grammar, resource ids, the
not-exported broadcast rule) is verified against `bridge.ATAK_TESTED_VERSION`.
When targeting a new ATAK build, run `atak-mcp doctor` against a device to see
what drifted, re-derive from the installed APK if needed, and bump that constant.
Do not guess ATAK Intent/URI strings — ground them in decompiled evidence or an
on-device `logcat` check.
