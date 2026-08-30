---
name: cheat-engine-debugging
description: Analyze and debug explicitly authorized Windows processes through the structured Cheat Engine MCP tools. Use for process attach, memory inspection, scans, symbols, disassembly, breakpoints, stepping, registers, signatures, comparisons, and explicit structure reads; do not use for arbitrary Lua, host commands, injection, or DBVM initialization.
---

# Cheat Engine debugging

Use only the `ce.*` MCP tools exposed by this plugin. Never substitute arbitrary
Lua, shell execution, injection, or unstructured CE commands.

Start with `ce.status`. If the bridge is unavailable, tell the user to start or
restart Cheat Engine after installing the bridge. A single running CE instance
is discovered automatically; multiple instances require an explicitly selected
CE PID in the plugin MCP configuration.

List processes and attach by explicit PID. Preserve the returned `sessionId`
and `generation`; pass `expectedGeneration` to every generation-bound call.
After detach, target exit, reconnect, or a reported generation change, discard
all prior handles, addresses that depend on target layout, operation IDs,
breakpoint IDs, and stop generations.

For debugger work, treat each stop as a separate state. Read events first and
use that event's `stopGeneration` for registers or continue/step. Never retry an
`OUTCOME_UNKNOWN` mutation. Reconcile it with `ce.status` or the relevant
read-only status/list call.

Keep reads, scans, result pages, disassembly, and signatures bounded. Close
scan/signature operations and remove breakpoints when no longer needed. Detach
when the requested workflow is complete unless the user explicitly wants the
session retained.

`dbvmReadiness` is diagnostic only. DBK/DBVM positive lifecycle is deferred in
this release, the tools are disabled by default, and this plugin must never
initialize either subsystem.
