# CE lifecycle probes

Development-only scripts for validating CE APIs against disposable targets.
They are not MCP handlers and are not included in release artifacts.

| File | Purpose |
| --- | --- |
| `memscan_lifecycle_probe.lua` | First scan, refine, FoundList, and cleanup ordering |
| `memscan_cancel_probe.lua` | Running-scan cancellation and GUI recovery |
| `memscan_relative_probe.lua` | Controlled relative refinements |
| `signature_lifecycle_probe.lua` | Bounded AOB signature lifecycle |
| `debugger_lifecycle_probe.lua` | Debugger, breakpoint, context, continue, and detach |
| `dbvm_direct_probe.lua` | No-initialization DBVM diagnostics and cleanup |
| `debug_target.c` | Cooperative x86/x64 debugger target; build with Zig |

Use an isolated CE instance, an exact disposable target PID, and an external
timeout. A probe proves only its native CE lifecycle; bridge, disconnect,
session, and MCP behavior require separate tests. Never initialize DBK or DBVM
for a probe.
