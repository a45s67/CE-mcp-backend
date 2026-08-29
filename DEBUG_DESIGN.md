# Debugger lifecycle design

## Verified native lifecycle

CE 7.5 documents `debugProcess`, debugger state queries, breakpoint callbacks,
`debug_getContext(true)`, `debug_getXMMPointer`,
`debug_continueFromBreakpoint`, breakpoint removal, and `detachIfPossible`.
The standalone probe verified the complete Windows-debugger lifecycle against
a cooperative x64 target; details are recorded in
`bridge/probes/RESULTS.md`.

## MCP state model

Debugger state is stricter than the process session generation:

- `generation` changes when the target identity changes;
- `stopGeneration` increments for every observed breakpoint or completed step;
- context/register reads are valid only while stopped;
- register mutation, step, and continue require the current
  `expectedStopGeneration`;
- breakpoint and event handles are generation-bound and removed on debugger
  detach, process detach/replacement, pipe disconnect, and bridge reload;
- callbacks enqueue bounded event snapshots and return promptly; they never
  perform pipe I/O.

`debug_isBroken()` is diagnostic only. In the verified callback-to-timer
transition it can still report false even though context is available and the
watched target value remains unchanged until `debug_continueFromBreakpoint`.
The bridge therefore treats an enqueued breaking event as authoritative stop
state and protects it with `stopGeneration`.

Initial promotion will use the Windows debugger only. VEH, kernel, and DBVM
debugger interfaces remain disabled until separately lifecycle-verified.
The first MCP promotion now includes debugger start/status/continue/detach,
hardware execute/write/access breakpoints, and a bounded event list. General
and XMM register snapshots for the currently stopped thread have now passed an
x64 production gate. Vector and general-register writes remain mutation-gated.

`ce.threads` deliberately exposes only bounded thread identifiers because CE
Lua's `getThreadlist` does not provide reliable state, name, TEB, instruction
pointer, or arbitrary-thread context. Those fields must not be inferred.

The production gate also closes the pipe while the target is stopped. The
bridge removes debugger state and continues/detaches fail-safe. CE may release
its opened-process session as part of debugger detach, so reconnect validation
accepts either the retained session or no session; in the latter case it
reattaches the exact cooperative target before proving the debugger inactive
and breakpoint/event collections empty.

## Test isolation rule

Every real-CE test sets a unique name-only `CE_MCP_PIPE_NAME`. Ordinary use
needs no environment variable because the production pipe contains
`getCheatEngineProcessID()` and the sidecar auto-discovers it. The client must
observe the fresh bridge diagnostic/generation and intended PID. Using the
default pipe while multiple CE processes exist is invalid evidence because the
client may silently connect to a different instance.
