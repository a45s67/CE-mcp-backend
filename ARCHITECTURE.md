# Architecture

## Components and trust boundaries

```text
MCP client -- authenticated localhost Streamable HTTP --> compiled sidecar
                                                               |
Cheat Engine -- owns sidecar lifetime --> PID-specific named pipe
      |                                                        |
      +---------------- autorun Lua bridge <--------------------+
                               |
                    explicitly attached Windows target

Optional gateway/terminal --> host controller --> Cheat Engine process only
```

The Python sidecar owns MCP schemas, validation, capability policy, session
generation, audit metadata, artifact storage, and transport deadlines. The Lua
bridge owns CE API calls and connection-scoped CE resources. Neither layer
offers a generic command or Lua escape hatch.

The optional controller is a separate host-lifecycle trust boundary. It is not
an MCP tool and cannot attach to a target. It starts only a recognized CE
executable under the resolved installation root; CE autorun still exclusively
starts the sidecar. Status and safe shutdown use authenticated MCP observation,
exact executable-path process identity, bounded deadlines, and graceful
`WM_CLOSE`. Normal stop/restart fail closed for attached or unobservable state.
Explicit force mode is limited to the same verified process handle and never
changes DBK or DBVM state.

## Transport

The installed runtime uses stateless Streamable HTTP, returns JSON responses,
is restricted to localhost, and requires a backend-only Bearer token on every
MCP request. Stdio remains available for development and protocol diagnostics.
An unauthenticated liveness endpoint reports
only that the event loop is serving; authenticated readiness calls the same
`ce.status` service boundary and reports a bounded diagnostic without target or
credential data. The sidecar accepts only local CE named pipes, discovers a
single CE instance by default, and verifies that the pipe server PID matches the
selected CE process.

The autorun bridge launches only `<CE>\mcp\server.exe`, supplies a strict local
config path and the current CE PID, and never puts the HTTP token in process
arguments. The sidecar monitors that explicit CE identity and exits when its CE
owner terminates. Configuration is strict JSON; unknown fields fail startup.

Bridge messages are length-prefixed UTF-8 JSON with a bounded frame size,
request correlation, deadlines, and strict schemas. A disconnected connection
causes the bridge to clean its debugger and operation resources.

Every MCP tool result is measured as compact UTF-8 JSON at the shared adapter
used by stdio and HTTP. The configured output ceiling defaults to 1 MiB and has
a 4 MiB hard maximum. `structuredContent` is authoritative; `content` is only a
bounded summary. An oversized completed mutation is not repeated: the client
receives an `OUTPUT_LIMIT_EXCEEDED` reconciliation error with
`safeToRetry=false`.

## State

An attach produces a session identity and monotonically changing generation.
Target-bound requests use optimistic generation checks. Detach, target death,
or reconnect invalidates prior target state and owned resources.

Debugger state adds a stop generation. A stop event establishes the only valid
generation for register reads and resume/step commands. This prevents actions
based on stale debugger observations.

Long-running scans and signatures return generation-owned operation IDs.
Artifacts and structure definitions are sidecar-owned and never accept an
arbitrary host output path.

## Capability policy

`inspect` is read-only. The default `debug` profile adds the verified debugger
and controlled analysis workflows. Experimental DBVM contracts require
separate sidecar and bridge authorization, remain disabled in normal use, and
never initialize DBK or DBVM.

The public surface deliberately excludes writes, allocation/protection changes,
assembly, patches, injection, arbitrary Lua, shell commands, GUI automation,
and unrestricted filesystem access.

## Safety and observability

- Every input and output is validated against a checked-in versioned schema.
- Reads, result pages, operations, structures, and artifacts have hard limits.
- Transport failures distinguish safe retry from `OUTCOME_UNKNOWN` mutation
  outcomes.
- Optional `suggestedAction` and `nextActions` recovery hints are explicitly
  attributed to `ce-mcp-backend`, bounded and schema-validated, and never
  executed automatically.
- Audit logs record redacted call metadata, never request arguments, memory
  contents, credentials, or bridge authorization secrets.
- Default audit and artifact locations are backend-controlled under the local
  application-data directory and can be changed only by server configuration.

## Source of truth

- Public API: `ce_mcp/contracts/v1/tools/*.json`
- Sidecar routing and invariants: `ce_mcp/service.py`, `ce_mcp/policy.py`
- CE lifecycle implementation: `bridge/ce_mcp_bridge.lua`
- Optional host lifecycle: `ce_controller/` and
  `docs/contracts/host-control-v1.md`
- CE lifecycle probes: `bridge/probes/`
- Release behavior: automated tests plus controlled real-CE smoke gates
