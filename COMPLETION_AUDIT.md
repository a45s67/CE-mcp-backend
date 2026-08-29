# Completion audit

Audit date: 2026-08-30. This file records evidence, not intent. `Proved` means
the current implementation has a matching contract/test and, where CE behavior
is involved, a real CE gate. `Partial` and `open` items prevent a v1.0 claim.

Final local packaging gate (2026-08-30): `uv run --locked python -m unittest
discover -s tests -v` passed 119/119 after CE 7.7 readiness compatibility and
DBK/DBVM deferral.

CE 7.7 production dogfood (2026-08-30) passed automatic PID-specific pipe
discovery, status, process attach/detach, memory/map, bounded scan/refine,
symbols/disassembly, Windows debugger lifecycle, threads, 16 XMM registers,
memory compare/checksum, signature generation/cancel, and structure reads.
The official MCP SDK live stdio gate then passed one continuous
`initialize/list_tools/status/process_list/attach/memory_read/disassembly/detach`
session and advertised 20 tools. The gate is repeatable as `ce-mcp-live-smoke`.
The rebuilt wheel installed into a disposable Python 3.12 venv, exposed 20
contracts including DBVM trace `archive_results`, packaged the ACL inspector,
and its installer entry point ran. Packaged/source bridge Lua SHA-256 both equal
`94827C46D0031D1293BC345231CD09A2982DBABC3B990E1C54A0DDAB114C2E94`.
The post-onboarding rebuild installed successfully with all runtime dependencies
in a disposable Python 3.12 environment, exposed the new live-smoke entry point
and 20 contracts, and resolved the packaged bridge through the product
installer. Wheel SHA-256 is
`ABE5DA0900E99ED6E1B6FEAF488A0A8373E436347D513C380D745453E19FEA38`;
sdist SHA-256 is
`3512472D5F73785C9E83C94AC89A04701E99D8DE877CAA52E78700FFC74135CD`.

## User objective

| Requirement | Status | Authoritative evidence |
|---|---|---|
| External MCP sidecar and lightweight CE bridge | Proved | `ce_mcp/mcp_server.py`, official SDK live CE 7.7 stdio workflow, framed local bridge, wheel gate |
| Process/session/generation | Proved | contracts, x64/x86 attach gates, reconciliation, natural target-exit gate |
| Raw/typed memory and map | Proved | x64 vertical slice, x86 debugger target reads, bounded schemas |
| Scan/refine/results/cancel/cleanup | Proved for exposed modes | exact and relative native/production gates; comparison modes remain explicitly disabled |
| Bounded disassembly and symbols/modules | Proved | production vertical slice and contracts |
| Debug start, breakpoint/events, continue, step, pause, detach | Proved | x64 breakpoint gate; x86 breakpoint, hardware one-shot step, synchronous pause/resume gate |
| Threads and vector/general registers | Proved | x64 16-XMM and x86 8-XMM stopped-context gates |
| Signature generation | Proved | native lifecycle and production operation gate |
| Memory compare/checksum | Proved | controlled difference/MD5 production gate |
| Structure workspace | Proved for explicit layouts | sidecar CRUD/revision and packed x64 read gate; heuristic CE global structures are intentionally absent |
| Controlled DBVM watch/trace | Deferred/experimental | contracts, bridge lifecycle, dual authorization, bounds, cleanup, Lua compilation, CE 7.5/7.7 no-load behavior, and readiness queries pass; positive already-loaded lifecycle moved to `TODO.md` by user and is not a current release blocker |
| No analysis VM management tools | Proved | no VM power/snapshot/guest/shell surface exists |

## MUST requirement audit

| IDs | Status | Notes |
|---|---|---|
| CE-F-001..007, CE-F-012 | Proved for the exposed scope | Contracts, stale-generation tests, x86/x64 and clean-host gates are recorded in `bridge/probes/RESULTS.md`. |
| CE-F-008 | Proved | `inspect` rejects every registered mutation before bridge execution; write/allocate/protect/patch are not exposed. |
| CE-F-011 | Proved for exposed large outputs | Memory dumps are immutable artifacts. DBVM trace `archive_results` aggregates only bounded pages (maximum 1024 entries) into a deterministic immutable JSON artifact; ordinary scan/debug/DBVM pages remain bounded inline. |
| API-001..006 | Proved | Localhost HTTP enforcement/token tests, deterministic schemas, canonical addresses/IDs, pagination, operation handles, and unknown-mutation outcomes pass. |
| API-007 | Proved under restart-only policy | Catalog is stable/deterministic. Profiles are startup configuration and cannot change during an MCP session, so no runtime catalog-change notification is required. |
| SEC-001 | Proved at the backend boundary; Gateway-side enforcement is an integration constraint | Stdio is local process transport. HTTP requires a dedicated backend-only token and rejects missing/short/incorrect tokens; deployment documentation requires the remote Gateway credential to remain distinct. |
| SEC-002 | Accepted for the local-workstation threat model | Frame bounds, deadlines, schema validation, and server-PID identity pass. A real CE 7.5 `createPipe` descriptor was inspected as `D:(D;;FA;;;NU)(A;;0x12019f;;;WD)(A;;0x12019f;;;CO)`: network logons are explicitly denied, while local Everyone and Creator Owner can connect. The user accepted this host-default local ACL; it is not suitable as a same-machine multi-user isolation boundary. |
| SEC-003 | Proved | Sidecar profile and independent bridge policy/token guard execute before DBVM handler lookup. |
| SEC-004, SEC-005, SEC-008 | Proved by absence/fail-closed policy | No arbitrary Lua, shell, injection, host/DLL path, physical write, MSR/CR write, cloak, or TSC handlers exist. |
| SEC-006 | Proved for current path surfaces | Artifact IDs/root containment and installer CE/autorun containment are tested; public tools accept no host paths. |
| SEC-007, NFR-008 | Proved | Production rotating JSONL audit shares bridge request ID and records duration/error/mutation without arguments, memory, or tokens. |
| NFR-001 | Proved on current host | 30-sample warm p95: status 0.792 ms; 4 KiB read 2.203 ms. |
| NFR-002..004, NFR-006 | Proved for exposed surface | Bounds/concurrency, reconnect and stale-handle cleanup tests; controlled CE 7.5 x64 host with x64 and x86 targets. |
| NFR-005 | Proved | Operation/resource cleanup and bounded handle counts pass. The artifact store prunes valid owned pairs after seven days or above 128 items by default, preserves unrelated/malformed files, and exposes configurable limits. |

## Current release blockers

No known blocker remains for the default non-DBVM local-workstation release.
Previously recorded real CE x64/x86, target-exit, performance, stdio, HTTP,
artifact, and debugger gates remain authoritative; the final changes affect
DBVM readiness/guidance and documentation only, and the production Lua bridge
was recompiled with CE 7.7's runtime.

The positive DBK/DBVM lifecycle is tracked separately in `TODO.md`; it is not a
release blocker while the feature remains experimental and disabled by default.
