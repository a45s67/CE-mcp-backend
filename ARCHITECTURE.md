# Architecture

## Components and trust boundaries

```text
MCP client
    | stdio, or authenticated localhost HTTP
Python sidecar
    | framed JSON over a local PID-specific named pipe
Cheat Engine autorun Lua bridge
    | verified CE Lua APIs
Explicitly attached Windows target
```

The Python sidecar owns MCP schemas, validation, capability policy, session
generation, audit metadata, artifact storage, and transport deadlines. The Lua
bridge owns CE API calls and connection-scoped CE resources. Neither layer
offers a generic command or Lua escape hatch.

## Transport

Stdio is the normal MCP transport. Optional Streamable HTTP is restricted to
localhost and requires a backend-only token. The sidecar accepts only local CE
named pipes, discovers a single CE instance by default, and verifies that the
pipe server PID matches the selected CE process.

Bridge messages are length-prefixed UTF-8 JSON with a bounded frame size,
request correlation, deadlines, and strict schemas. A disconnected connection
causes the bridge to clean its debugger and operation resources.

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
- Audit logs record redacted call metadata, never request arguments, memory
  contents, credentials, or bridge authorization secrets.
- Default audit and artifact locations are backend-controlled under the local
  application-data directory and can be changed only by server configuration.

## Source of truth

- Public API: `ce_mcp/contracts/v1/tools/*.json`
- Sidecar routing and invariants: `ce_mcp/service.py`, `ce_mcp/policy.py`
- CE lifecycle implementation: `bridge/ce_mcp_bridge.lua`
- Runtime evidence: `bridge/probes/RESULTS.md`
- Release behavior: automated tests plus controlled real-CE smoke gates
