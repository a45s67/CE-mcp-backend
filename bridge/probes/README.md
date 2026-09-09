# CE lifecycle probes

Development-only scripts for validating CE APIs against disposable targets.
They are not MCP handlers and are not included in release artifacts.

| File | Purpose |
| --- | --- |
| `memscan_lifecycle_probe.lua` | First scan, refine, FoundList, and cleanup ordering |
| `memscan_cancel_probe.lua` | Running-scan cancellation and GUI recovery |
| `memscan_relative_probe.lua` | Controlled relative refinements |
| `signature_lifecycle_probe.lua` | Bounded AOB signature lifecycle |
| `reliability_probe.lua` | CE 7.7 disassembly, region queries, callback/context/step and deferred-cleanup evidence |
| `dbvm_direct_probe.lua` | No-initialization DBVM diagnostics and cleanup |
| `debug_target.c` | Cooperative x86/x64 debugger target; build with Zig |

Use an isolated CE instance, an exact disposable target PID, and an external
timeout. A probe proves only its native CE lifecycle; bridge, disconnect,
session, and MCP behavior require separate tests. Never initialize DBK or DBVM
for a probe.

Use `scripts/probe-native.py` with a new output directory beneath `var/`. It copies
a minimal runtime and sole autorun, starts a finite owned target, enforces a timeout,
and records process cleanup. Modes `bridge`, `bridge-debug`, and `bridge-data` test
the source bridge/service path; other modes isolate the native APIs first. Use
`--step into` or `--step over` with `--mode breakpoint-1` for native stepping.
