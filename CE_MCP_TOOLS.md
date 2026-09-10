# CE MCP tools

The JSON schemas under `ce_mcp/contracts/v1/tools/` are the authoritative
machine-readable contracts. This file is the concise human-readable catalog for
the current release; it does not describe planned APIs.

## Session rules

- Start with `ce.status`, list targets, and attach by explicit PID.
- Preserve `sessionId` and `generation`. Pass `expectedGeneration` to every
  target-bound call.
- Detach, target exit, bridge reconnect, or generation change invalidates all
  target addresses and owned handles.
- Debugger events carry `stopGeneration`; registers and continue/step require
  the current stopped generation.
- Keep reads, scans, pages, signatures, and artifacts bounded. Close operation
  handles and remove breakpoints after use.
- Never retry `OUTCOME_UNKNOWN` mutations automatically.
- Parse the single `content` text block as JSON: a direct success object or
  `{ "error": ... }` with `isError: true`. There is no summary, result wrapper,
  `structuredContent`, or advertised `outputSchema`. Checked-in output schemas
  still validate service payloads internally.
- Backend-authored `suggestedAction` and `nextActions` are optional hints, not
  Cheat Engine or MCP directives; never bypass authorization or generation
  checks when following them.

Addresses are canonical hexadecimal strings. Target pointer width and
architecture come from the attached session.

## Query semantics

- Debugger status/context use the current native waiting context, not cached
  register globals. Process suspension supplies no register context and permits
  only run/unpause. Breakpoint removal releases the logical handle; native list
  deletion may be deferred until after resume. Do not repeat removal for that reason.
- Debugger `interface` reports the native backend (`unknown` if unreadable).
  `start` accepts `windows` or `veh`; omission uses CE's configured default and
  accepts only those two resulting interfaces. A matching active debugger is adopted.
  Disconnect and release remove MCP hooks but never resume or detach an adopted debugger.
  Switching an MCP-owned backend requires breakpoint cleanup and debugger detach.
  `breakpointCount` counts MCP-owned logical handles, not GUI breakpoints. The current
  breakpoint tool exposes hardware debug registers only and removes them by CE's native
  breakpoint ID, never by a potentially shared address.
- Debug events use generation-bound sequence cursors rather than numeric ring
  indices. Discard old cursors when upgrading the bridge. Reuse `nextCursor` even
  on an empty tail, and inspect `droppedEvents` for a retention gap.
- Memory maps use single-region queries over matching module ranges, name only
  returned rows, and stop at page lookahead or an 8192-query budget. A single native
  query may still block. CE's selected query backend controls metadata semantics.
- `maxStringBytes` includes the terminator budget; wide strings read whole 2-byte
  units. `complete` is true only after observing a terminator within that budget.
  Raw `encoding=base64` is converted from bridge hex by the sidecar.
- Function disassembly stops at the first return or its read/count bound; this is
  heuristic recovery, not an exact function/CFG boundary.

## Public tools

| Tool | Actions or purpose |
| --- | --- |
| `ce.status` | Backend, bridge, target, capabilities, limits, and DBVM diagnostic state |
| `ce.process` | `list`, `attach`, `get`, `detach` |
| `ce.memory_read` | Bounded raw or typed reads, strings, and explicit pointer offsets |
| `ce.memory_map` | Bounded regions filtered by module, protection, state, or type |
| `ce.memory_analysis` | `compare`, `checksum` |
| `ce.symbols` | `resolve`, `describe`, `modules`, `list` |
| `ce.disassembly` | `list`, `instruction`, `function`, `previous`, `next` |
| `ce.scan` | `start`, `refine`, `results`, `close` |
| `ce.operations` | `get`, `list`, `cancel` for session-owned long operations |
| `ce.pointer` | `resolve`, `validate` bounded pointer chains |
| `ce.signature` | `start`, `result`, `close` an exact bounded AOB signature job |
| `ce.structures` | `create`, `update`, `list`, `get`, `delete`, `read` sidecar definitions |
| `ce.artifacts` | `memory_dump`, `list`, `get_metadata`, `preview`, `delete` backend-owned artifacts |
| `ce.debug_control` | `status`, `start`, `pause`, `continue`, `detach` |
| `ce.debug_events` | `list` bounded debugger stop events |
| `ce.registers` | `read` stopped-thread registers |
| `ce.breakpoints` | `list`, `set`, `remove` generation-owned hardware breakpoints |
| `ce.threads` | `list` target thread IDs |

Exact required fields, limits, enums, output shapes, and MCP annotations are in
the corresponding JSON schema. Unsupported fields are rejected.

`ce.status.capabilities.limits.maxOutputBytes` reports the active per-tool-result
ceiling. If a result exceeds it, `OUTPUT_LIMIT_EXCEEDED.details` includes
`actualBytes`, `limitBytes`, `tool`, `action`, and outcome. A safe paged read may
include an `argumentsPatch` with a smaller `limit` or `count`; a mutation never
does.

## Capability limits

The current release intentionally has no process launch, memory write,
allocation/protection mutation, assembly, patching, injection, arbitrary Lua,
host shell, or arbitrary host-path tools. Pointer scanning is not exposed;
bounded pointer-chain resolution and validation are supported.

Exact and probe-verified relative scan refinements are enabled. Refinements
whose CE lifecycle is not verified remain capability-disabled.

`ce.dbvm_watch` and `ce.dbvm_trace` contracts are retained for future positive
lifecycle validation but are disabled by the normal profiles. `ce.status`
reports `dbvmReadiness` only as a diagnostic. No tool loads or initializes DBK
or DBVM.
