# CE bridge lifecycle probes

These scripts are development-only and must not be shipped as MCP handlers.
They isolate undocumented or source-sensitive CE object lifecycles before the
behavior is integrated into `ce_mcp_bridge.lua`.

`memscan_lifecycle_probe.lua` requires an isolated CE process and the
`CE_MCP_MEMSCAN_PROBE_OUTPUT` environment variable. It records its current
stage before every potentially blocking call, allowing an external harness to
time out and identify the exact failing transition. A passing result proves the
native synchronous first/refine/FoundList ordering only; it does not by itself
prove MCP cancellation, reconnect, or session cleanup.

`debug_target.c` is a cooperative native target for architecture-specific
debugger gates. Build it explicitly with Zig for the intended target; generated
executables are test artifacts and are not source deliverables.

`dbvm_direct_probe.lua` directly tests status, physical translation, and one
1-byte/16-entry write watch against `debug_probe_target.py`. It never calls DBK
or DBVM initialization and disables any successfully created watch immediately.

`memscan_cancel_probe.lua` starts an unknown-value scan and only passes if it
observes incomplete progress, destroys the running MemScan, and then observes a
later GUI timer. A scan that completes before cancellation is `inconclusive`.

`memscan_relative_probe.lua` allocates four bytes in a disposable target and
uses controlled writes to prove unchanged, increased, decreased, and changed
refinement without relying on incidental process behavior.

`debugger_lifecycle_probe.lua` uses one cooperative target write loop to prove
Windows-debugger start, a hardware write-breakpoint callback, general and XMM
context access, scripted continue, breakpoint removal, and debugger detach as
one lifecycle.
