# CE MCP Backend

Structured Cheat Engine 7.7 dynamic analysis through MCP for explicitly
authorized Windows processes. The backend excludes arbitrary Lua, shell
commands, injection, and memory writes.

The standalone Windows release requires neither Python nor a Codex plugin or
skill. See [CE_MCP_TOOLS.md](CE_MCP_TOOLS.md) for the tool catalog and
[ARCHITECTURE.md](ARCHITECTURE.md) for trust boundaries.

## Install

Requirements: 64-bit Windows and Cheat Engine 7.7. Extract
`ce-mcp-windows-x64.zip`, close all CE instances, and run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\install.ps1 `
  -CheatEngineDir "C:\tools\Cheat Engine"
```

The installer creates:

```text
Cheat Engine/
|-- autorun/
|   `-- ce_mcp_bridge.lua
`-- mcp/
    |-- server.exe
    |-- ce-mcp-control.exe       optional
    |-- config.json
    |-- http.token
    `-- standalone runtime files
```

The first install creates a random 48-byte token restricted to the installing
Windows user. Upgrades preserve `config.json` and `http.token`; use
`-RotateToken` only when all clients can be updated. Restart CE after install.
CE autorun owns `server.exe` startup and shutdown.

## Connect a client

The default endpoint is `http://127.0.0.1:8001/mcp`. For Codex, expose the
installed token through a user environment variable and register the endpoint:

```powershell
$tokenPath = "C:\tools\Cheat Engine\mcp\http.token"
$token = [IO.File]::ReadAllText($tokenPath).Trim()
[Environment]::SetEnvironmentVariable("CE_MCP_TOKEN", $token, "User")

codex mcp add cheat-engine `
  --url http://127.0.0.1:8001/mcp `
  --bearer-token-env-var CE_MCP_TOKEN
```

Restart Codex after changing its environment. Other Streamable HTTP clients
use the same endpoint and `Authorization: Bearer <token>`.

The server reads authentication from `CE_MCP_TOKEN` when present, otherwise
from `tokenFile` in `config.json`. It refuses HTTP startup without either.

## Configuration and health

Default `mcp\config.json`:

```json
{
  "transport": "streamable-http",
  "host": "127.0.0.1",
  "port": 8001,
  "tokenFile": "http.token",
  "requestDeadlineMs": 5000,
  "maxOutputBytes": 1048576,
  "exitWhenCeExits": true
}
```

Only `127.0.0.1`, `::1`, and `localhost` are accepted. Do not proxy or expose
this plaintext endpoint. `maxOutputBytes` accepts 4096 through 4194304 bytes;
oversized results return `OUTPUT_LIMIT_EXCEEDED` with measured and configured
sizes instead of truncated JSON.

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health/live
$headers = @{ Authorization = "Bearer $env:CE_MCP_TOKEN" }
Invoke-RestMethod http://127.0.0.1:8001/health/ready -Headers $headers
```

Liveness proves only that HTTP is serving. Authenticated readiness also checks
the CE bridge through `ce.status`.

## Use

Begin with `ce.status`, select and attach an explicit PID through `ce.process`,
and preserve returned session, generation, and debugger stop-generation values.
Close owned operations and breakpoints. Never automatically retry an
`OUTCOME_UNKNOWN` mutation.

Tool results contain one complete JSON object in the MCP text `content` block;
the server does not advertise or return `structuredContent`.
`suggestedAction` and `nextActions` are optional backend-authored hints, not CE
or MCP directives, and are never executed by the server.

DBK and DBVM remain disabled and are never initialized by this project. Each
simultaneous CE instance requires a distinct configured HTTP port.

## Optional host controller

Normal manual CE startup and shutdown remains supported. The optional
controller provides terminal lifecycle operations:

```powershell
& "C:\tools\Cheat Engine\mcp\ce-mcp-control.exe" status
& "C:\tools\Cheat Engine\mcp\ce-mcp-control.exe" start
& "C:\tools\Cheat Engine\mcp\ce-mcp-control.exe" stop
& "C:\tools\Cheat Engine\mcp\ce-mcp-control.exe" restart
```

It returns one bounded JSON object. It starts CE, never `server.exe`; normal
stop and restart refuse attached or unobservable state. See the
[host-control contract](docs/contracts/host-control-v1.md) for arguments,
exit codes, and force semantics.

## Development

### Local testing with OpenCode

Requires Python 3.10 or newer and uv. Close CE, then run from the repository
root, replacing the CE path with your installation directory:

```powershell
uv run --locked ce-mcp-install-bridge --ce-dir "C:\tools\Cheat Engine"
```

This installs only `autorun\ce_mcp_bridge.lua`. Add `--replace` to explicitly
overwrite an existing bridge. Start CE afterward and keep it running.

Merge this local MCP entry into your project's `opencode.json` or global
`~/.config/opencode/opencode.json`, replacing the repository path with your
absolute checkout path. If you already have a `ce-mcp` remote entry, replace
that entry with the local configuration below (remove its `url`). Preserve
other entries under `mcp`, such as `idalib`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "ce-mcp": {
      "type": "local",
      "command": [
        "uv",
        "--directory",
        "C:\\path\\to\\CE-mcp-backend",
        "run",
        "--locked",
        "ce-mcp-backend",
        "--transport",
        "stdio"
      ],
      "enabled": true,
      "timeout": 120000
    }
  }
}
```

OpenCode launches the stdio server; do not launch a separate stdio process
manually. This setup needs neither an HTTP token nor `mcp\config.json`.
The startup timeout allows time for uv to prepare the environment on first run.
Restart OpenCode after updating its configuration and check `opencode mcp list`.

For a bridge-only source test, avoid the release's automatic HTTP startup:
if both `mcp\server.exe` and `mcp\config.json` exist in the CE directory, the
Lua bridge launches that server when CE starts. With CE closed, temporarily
rename `mcp\config.json` before testing stdio, then restore it when returning
to the release setup.

### Build and verification

Python 3.10 or newer and uv are required:

```powershell
uv sync --locked --group build
uv run --locked python -m unittest discover -s tests -v
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\build-standalone.ps1
uv run --locked python .\scripts\verify-release.py `
  .\dist\ce-mcp-windows-x64
uv run --locked python .\scripts\verify-compiled.py `
  --server .\dist\ce-mcp-windows-x64\mcp\server.exe `
  --controller .\dist\ce-mcp-windows-x64\mcp\ce-mcp-control.exe
```

Hosted CI runs source and compiled scripted tests without CE. Controlled
real-CE smoke tests are a local release gate. CE-facing changes must follow
[DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md).
