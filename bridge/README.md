# CE Lua bridge

`ce_mcp_bridge.lua` is the in-process Cheat Engine 7.7 adapter. It has no MCP
listener, public network endpoint, host shell, GUI automation, injection, or
arbitrary Lua evaluation.

## Installation and lifecycle

Use `ce-mcp-install-bridge --ce-dir <directory>` to install the bridge as
`autorun\ce_mcp_bridge.lua`, then restart Cheat Engine. For development it can
also be loaded from the Lua Engine with `dofile`.

The bridge creates `\\.\pipe\CE_MCP_Backend_v1_<CE_PID>`. Normal users set no
pipe environment variable. The sidecar discovers one CE instance or requires
an explicit `--ce-pid` when several are running, then verifies the pipe server
PID. `CE_MCP_PIPE_NAME` exists only as an integration-test override.

Reloading stops the previous bridge. Disconnect, detach, target replacement,
and CE shutdown clean generation-owned scans, signatures, breakpoints, and
debugger state.

## Implemented method groups

- status and process lifecycle;
- bounded memory reads, maps, comparison, and checksum;
- module/symbol lookup and disassembly;
- asynchronous scans and operation lifecycle;
- bounded pointer-chain resolution and validation;
- signature generation;
- debugger control, events, threads, registers, and hardware breakpoints;
- explicit reads using sidecar-owned structure definitions;
- bounded memory chunks used by the sidecar artifact store.

Exact handler names and schemas are derived from
`ce_mcp/contracts/v1/tools/`; [CE_MCP_TOOLS.md](../CE_MCP_TOOLS.md) is the
human-readable catalog.

Every CE API call is marshalled through `thread.synchronize`; the worker thread
performs only blocking framed pipe I/O. Frames are `uint32-le length + UTF-8
JSON` and are limited to 8 MiB.

Logical detach invalidates the MCP generation. CE may retain its native process
handle, which is reported as `ceHandleRetained: true`; no target operation is
accepted until another explicit attach.

## Deliberate limits

The bridge does not expose writes, allocation, protection changes, assembly,
patches, injection, or generic Lua. Pointer scanning and unverified comparison
refinements remain unavailable. DBVM contracts are disabled by normal policy
and the bridge never initializes DBK or DBVM.

CE Lua `createPipe` does not accept an explicit security descriptor. The bridge
therefore relies on CE's process token/default DACL, while the sidecar accepts
only local pipe names and verifies the server PID.
