# CE MCP Backend

Standalone, structured Cheat Engine 7.7 dynamic analysis through MCP. The
backend exposes bounded tools for explicitly authorized Windows processes; it
does not expose arbitrary Lua, shell commands, injection, or memory writes.

The Windows release does not require Python, uv, a Codex plugin, or a skill.
Tool behavior is documented by MCP itself and in
[CE_MCP_TOOLS.md](CE_MCP_TOOLS.md). Trust boundaries are described in
[ARCHITECTURE.md](ARCHITECTURE.md).

## Install the Windows release

Prerequisites: 64-bit Windows and Cheat Engine 7.7. Extract
`ce-mcp-windows-x64.zip`, close every Cheat Engine instance, and run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\install.ps1 `
  -CheatEngineDir "C:\tools\Cheat Engine"
```

The resulting installation is:

```text
Cheat Engine/
|-- autorun/
|   `-- ce_mcp_bridge.lua
`-- mcp/
    |-- server.exe
    |-- ce-mcp-control.exe       (optional host controller)
    |-- config.json
    |-- http.token
    `-- standalone runtime files
```

The first install generates a random 48-byte HTTP token and restricts its ACL
to the installing Windows user. Normal upgrades preserve `config.json` and
`http.token`. Use `-RotateToken` only when every registered client will also be
updated.

Restart Cheat Engine after installation. Its autorun bridge creates a
PID-specific local named pipe and starts `mcp\server.exe` once with the exact CE
PID and `mcp\config.json`. Closing CE causes its owned HTTP server to exit.

## Optional host controller

Ordinary users can continue opening and closing Cheat Engine normally. The
optional `mcp\ce-mcp-control.exe` exists for a lifecycle-aware gateway or a
terminal workflow:

```powershell
& "C:\tools\Cheat Engine\mcp\ce-mcp-control.exe" status
& "C:\tools\Cheat Engine\mcp\ce-mcp-control.exe" start
& "C:\tools\Cheat Engine\mcp\ce-mcp-control.exe" stop
& "C:\tools\Cheat Engine\mcp\ce-mcp-control.exe" restart
```

It emits exactly one bounded JSON object. `start` launches Cheat Engine, never
`server.exe`; the autorun bridge remains the only sidecar launcher. Normal
`stop` and `restart` require authenticated MCP state and refuse to close CE
while a target session is attached. `--force` is an explicit last resort,
limited to the exact CE process under the configured installation root.

The default launcher is `<CE>\Cheat Engine.exe`. Use
`--executable <filename>` only to select another recognized executable directly
inside the same CE root. `--timeout-ms` accepts 1000 through 60000. See the
[host-control contract](docs/contracts/host-control-v1.md) for exact semantics.

## Connect an MCP client

The default endpoint is `http://127.0.0.1:8001/mcp`. Every MCP request requires
the Bearer token. For Codex, copy the installed token into the user environment
and register the plain MCP endpoint:

```powershell
$tokenPath = "C:\tools\Cheat Engine\mcp\http.token"
$token = [IO.File]::ReadAllText($tokenPath).Trim()
[Environment]::SetEnvironmentVariable("CE_MCP_TOKEN", $token, "User")

codex mcp add cheat-engine `
  --url http://127.0.0.1:8001/mcp `
  --bearer-token-env-var CE_MCP_TOKEN
```

Restart Codex after changing its user environment. The command stores the
environment-variable name, not the secret value. Other Streamable HTTP MCP
clients should use the same URL and `Authorization: Bearer <token>` header.

The server itself resolves authentication in this order:

1. `CE_MCP_TOKEN`, when inherited by the server process.
2. `tokenFile` from `config.json`, resolved relative to that config file.
3. Refuse HTTP startup when neither source is available.

This lets CE's automatic launch use `http.token`, while controlled deployments
can override it through the environment without putting a secret in process
arguments.

## Configuration and health

Installed `mcp\config.json` defaults to:

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

Only `127.0.0.1`, `::1`, and `localhost` are accepted. Do not publish this
plaintext endpoint to a LAN or the internet.

`maxOutputBytes` defaults to 1 MiB, accepts 4096 through 4194304 bytes, and
limits each MCP tool result on both stdio and HTTP. Oversized results are never
returned as truncated JSON. The server instead returns
`OUTPUT_LIMIT_EXCEEDED` with measured and configured byte counts. Read-only
paged calls may receive a smaller `limit` or `count` suggestion; completed
mutations are never replayed and require state reconciliation.

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health/live
$headers = @{ Authorization = "Bearer $env:CE_MCP_TOKEN" }
Invoke-RestMethod http://127.0.0.1:8001/health/ready -Headers $headers
```

Liveness only proves that HTTP is serving. Authenticated readiness returns 200
when the CE bridge answers `ce.status`, or 503 with a bounded diagnostic code.

## Use

The MCP server instructions tell clients to begin with `ce.status`, explicitly
attach using `ce.process`, preserve session and debugger stop generations, and
clean up owned operations and breakpoints. Never retry an `OUTCOME_UNKNOWN`
mutation. DBK and DBVM are deferred, disabled by default, and never initialized
by this project; see [TODO.md](TODO.md).

Tool `content` is a short human-readable summary. Authoritative data remains in
`structuredContent`. Error `suggestedAction` and `nextActions` values are
optional recovery hints authored by this backend—not Cheat Engine or MCP—and
are never executed by the server. Clients may ignore them. `execution` is one
of `suggested`, `required_before_retry`, or `manual`; any mutation still
requires the normal authorization and generation checks.

If multiple CE instances are needed, each requires a distinct HTTP port. The
first release deliberately uses one configured endpoint and fails on a port
collision instead of silently selecting another port.

## Source development

Python 3.10 or newer and uv are required only for development:

```powershell
uv sync --locked --group build
uv run --locked python -m unittest discover -s tests -v
```

Build and verify the same Windows artifact used by CI:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\build-standalone.ps1

uv run --locked python .\scripts\verify-release.py `
  .\dist\ce-mcp-windows-x64

uv run --locked python .\scripts\verify-compiled.py `
  --server .\dist\ce-mcp-windows-x64\mcp\server.exe `
  --controller .\dist\ce-mcp-windows-x64\mcp\ce-mcp-control.exe
```

The compiled verifier exercises `--help`, stdio MCP, Streamable HTTP, both
authentication paths, health, initialization, tool listing, missing-bridge
errors, and bounded shutdown without launching Cheat Engine. Controlled real-CE
smoke commands remain local release evidence and are not run on hosted CI.

Repository layout:

- `bridge/`: CE autorun Lua bridge and real-CE probes.
- `ce_mcp/`: server, policy, transport, artifacts, and schemas.
- `ce_controller/`: optional dependency-free host lifecycle controller.
- `ce_mcp/contracts/v1/tools/`: authoritative public MCP tool contracts.
- `scripts/`: standalone build, installer, and offline verification gates.
- `tests/`: unit, contract, and scripted integration tests.
