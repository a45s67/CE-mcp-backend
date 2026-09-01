# Cheat Engine host control CLI v1

`ce-mcp-control.exe` is an optional, out-of-band lifecycle CLI. It controls the
Cheat Engine host; it never starts `mcp\server.exe` directly and is not exposed
as an MCP tool. Cheat Engine's autorun bridge remains the sole sidecar launcher.

## Invocation

```text
ce-mcp-control.exe <status|start|stop|restart>
  [--root <Cheat-Engine-directory>]
  [--executable <filename>]
  [--timeout-ms <1000..60000>]
  [--force]
```

When `--root` is omitted, an installed controller infers it as the parent of
its `mcp` directory. `--executable` is a filename, never a path. It defaults to
`Cheat Engine.exe`; if that launcher is absent, exactly one recognized CE host
executable must exist. Unknown, duplicate, incompatible, oversized, or
control-character arguments are rejected. The default timeout is 20 seconds.

Every invocation writes exactly one bounded UTF-8 JSON object to stdout and
does not write credentials, request bodies, process inventories, or full
configuration contents to either output stream. Success has `status: "ok"`.
Failure has a stable `code`, bounded `message`, `recoverable`, `safeToRetry`,
and `outcome` (`known` or `unknown`).

## Process identity

A CE host is recognized only when its queried executable path is a direct
child of the resolved root and its filename is `Cheat Engine.exe` or begins
with `cheatengine-` and ends in `.exe`, case-insensitively. Zero, one, and
multiple matching processes are distinct states; the controller never chooses
among multiple instances. PID-only identity is insufficient. Stop and force
termination re-query the executable path through the process handle before
acting.

The configured launch executable must be a recognized regular file directly
under the resolved root. The controller creates it without a command shell and
does not create a detached helper.

## MCP observation

The controller reads `<root>\mcp\config.json` using the server's strict field
set and accepts only loopback Streamable HTTP. It obtains authentication from
`CE_MCP_TOKEN` in its own environment when present, otherwise from the resolved
`tokenFile`. Tokens and configuration contents are never returned.

Authenticated readiness must report `status: "ready"` and
`bridge_connected: true`. Safe stop policy additionally calls `ce.status` over
stateless Streamable HTTP. A present `session` means a target is attached and
normal stop/restart is refused. Missing, malformed, unauthenticated, or
unreachable MCP state is unobservable and is also refused unless `--force` was
explicitly supplied.

## Lifecycle semantics

- `status` reports stopped, one running host, or a stable multiple-host error.
  For one host it reports MCP and target observability without changing state.
- `start` is idempotent only when the sole exact-path host reaches authenticated
  readiness. A running but unready host is not duplicated. With no host, it
  starts only the resolved CE executable and waits for one ready host.
- `stop` is idempotent when no host exists. Normally it requires observable MCP
  state with no target session, posts `WM_CLOSE` to windows owned by the exact
  host PID, and waits on the process handle.
- `restart` applies the same stop policy, waits for actual host exit, then uses
  the normal start path. It never relies on a fixed sleep.
- `--force` explicitly permits unobservable or target-attached state. It first
  requests graceful close. If no window accepts `WM_CLOSE`, or graceful close
  exceeds the deadline, it may terminate only the still-matching exact-path
  process handle.
- A timeout or failed termination never claims the final state is known. It
  returns `outcome: "unknown"` and `safeToRetry: false`.

The controller never attaches to a target, never calls debugger mutation tools,
and never initializes DBK or DBVM.

## Exit codes

- `0`: JSON success
- `2`: invalid CLI or configuration
- `3`: incompatible or unsafe current state
- `4`: bounded timeout or unknown lifecycle outcome
- `5`: operating-system, authentication, HTTP, or I/O failure

