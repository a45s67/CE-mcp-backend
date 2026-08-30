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

Addresses are canonical hexadecimal strings. Target pointer width and
architecture come from the attached session.

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
