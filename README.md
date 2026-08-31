# CE MCP Backend

Structured Cheat Engine 7.7 dynamic analysis through MCP. The backend exposes
bounded tools for explicitly authorized Windows processes; it does not expose
arbitrary Lua, shell commands, injection, or memory writes.

Current tool behavior is documented in [CE_MCP_TOOLS.md](CE_MCP_TOOLS.md).
Implementation boundaries are in [ARCHITECTURE.md](ARCHITECTURE.md), and the
evidence-first maintenance rules are in
[DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md).

## Install

Prerequisites: Windows, Cheat Engine 7.7, Python 3.10 or newer, and `uv` on
`PATH`.

Install the Lua bridge once, then restart Cheat Engine:

```powershell
uv sync --locked
uv run --locked ce-mcp-install-bridge --ce-dir "C:\tools\Cheat Engine"
```

The installer writes only `autorun\ce_mcp_bridge.lua` and refuses to replace
an existing file unless `--replace` is supplied. Normal stdio use needs no pipe
environment variable, policy file, DBK, or DBVM.

### Codex plugin

```powershell
codex plugin marketplace add a45s67/codex-marketplace
codex plugin add ce-mcp-backend@a45s67
```

To refresh an installed development release:

```powershell
codex plugin marketplace upgrade
codex plugin remove ce-mcp-backend@a45s67
codex plugin add ce-mcp-backend@a45s67
```

Codex runs `uv run --locked ce-mcp-backend --transport stdio` from the plugin
root, so users do not configure a machine-specific virtual-environment path or
MCP JSON. The first startup creates the plugin environment and can take longer.

Installing the plugin does not install or start the CE Lua bridge. Cheat Engine
must be running after the bridge installation.

### Other MCP clients

Use the executable from the project environment:

```json
{
  "command": "C:\\path\\to\\CE-mcp-backend\\.venv\\Scripts\\ce-mcp-backend.exe",
  "args": ["--transport", "stdio"]
}
```

## Use

The normal sequence is:

1. Call `ce.status`.
2. List processes and attach by explicit PID with `ce.process`.
3. Preserve the returned session generation for target-bound calls.
4. Close operation handles and remove breakpoints when finished.
5. Detach with `ce.process`.

If multiple Cheat Engine instances are running, configure `--ce-pid <pid>`;
automatic discovery intentionally refuses to guess. Never retry an
`OUTCOME_UNKNOWN` mutation. Reconcile state with `ce.status` or the relevant
read-only list/status call.

The default `debug` profile enables the supported non-DBVM tools. An optional
read-only deployment can pass a local JSON file containing
`{"profile":"inspect"}` through `--policy-config`. DBK/DBVM support is deferred,
disabled by default, and never initialized by this project; see [TODO.md](TODO.md).

### Streamable HTTP

Stdio remains the Codex plugin default. For a direct MCP client or local gateway,
start the same backend as an authenticated, stateless Streamable HTTP server:

```powershell
$tokenPath = Join-Path $env:LOCALAPPDATA "CE-MCP\http.token"
New-Item -ItemType Directory -Force (Split-Path $tokenPath) | Out-Null
$token = [Convert]::ToBase64String(
  [Security.Cryptography.RandomNumberGenerator]::GetBytes(48)
)
[IO.File]::WriteAllText($tokenPath, $token)

uv run --locked ce-mcp-backend `
  --transport streamable-http `
  --host 127.0.0.1 `
  --port 8001 `
  --token-file $tokenPath
```

Configure the client with endpoint `http://127.0.0.1:8001/mcp` and header
`Authorization: Bearer <the token>`. The backend implements stateless
Streamable HTTP with JSON responses. Every MCP request requires the token.

Health checks are separate from MCP:

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health/live
$headers = @{ Authorization = "Bearer $token" }
Invoke-RestMethod http://127.0.0.1:8001/health/ready -Headers $headers
```

`/health/live` only proves that the HTTP server is running. Authenticated
`/health/ready` returns HTTP 200 when the CE bridge answers `ce.status`, or HTTP
503 with a bounded `diagnostic_code` when it cannot. The token must contain at
least 32 characters. Only its file path is accepted as server configuration;
the secret is not read from an environment variable or accepted on the command
line.

Only loopback hosts (`127.0.0.1`, `::1`, or `localhost`) are accepted. Do not
publish this plaintext HTTP endpoint to a LAN or the internet; use a trusted
local gateway if remote access is required.

## Recovery and uninstall

- If the bridge is unavailable, restart CE and confirm exactly one CE instance
  is running, or select it with `--ce-pid`.
- Closing the MCP session cleans debugger and operation resources owned by that
  connection.
- Emergency bridge stop inside CE is `StopCEMCPBridge()` in the Lua Engine.
- To uninstall, close the MCP client, remove only
  `<Cheat Engine>\autorun\ce_mcp_bridge.lua`, and restart CE.

## Development

Create the locked environment and run the ordinary suite:

```powershell
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
```

Validate the official MCP stdio path against a controlled target:

```powershell
uv run --locked ce-mcp-live-smoke --target-pid <controlled-pid>
```

Use `--ce-pid <pid>` only when multiple CE instances are open. Additional live
gates are exposed as `ce-mcp-*-smoke` commands in `pyproject.toml`. Probe source
and retained real-CE evidence live under [bridge/probes](bridge/probes/README.md).

Repository layout:

- `bridge/`: the autorun Lua bridge and standalone lifecycle probes.
- `ce_mcp/`: MCP server, policy, state, transport, artifacts, and schemas.
- `ce_mcp/contracts/v1/tools/`: authoritative public tool contracts.
- `skills/`: Codex guidance for safe tool use.
- `tests/`: offline contracts, invariants, integration tests, and live fixtures.
