# Native reliability recovery

## Problem and fix boundaries

Original symptoms and user requirements are retained in NON_DBVM_LIVE_MATRIX.md.
The plan is to independently close disassembly mapping, native debugger stop/context,
memory-map transport, event pagination, and cancellation/typed-read coverage. A
successful fixture does not close an unrelated live acceptance layer. No DBVM tests.

Official public CE source inspected at commit
`ec45d5f47f92a239ba0bf51ec5d04a7509c3fd37` is not labelled CE 7.7. Installed
`C:/Program Files/Cheat Engine/celua.txt` and native CE 7.7 probes take precedence
over assuming exact source/binary equivalence.

## Native evidence

Reproducer: scripts/probe-native.py copies only the CE executable, Lua runtime,
defines/main scripts and optional signature to a NEW directory under var/. Its
sole autorun is the requested probe, or the source bridge for service acceptance.
It starts an owned finite target, retains process handles, enforces external
timeouts, records hashes and exit status, and never modifies the installed CE.
An explicit existing target PID is permitted only for read-only map/bridge checks.
No driver, plugin or other installed autorun script is copied.

Artifacts below are under `C:/analysis/kartrider-docs/var/sessions/`:

| Run | Evidence and result |
|---|---|
| ce-native-disasm-01 | PASS: CE 7.7 split returns extra/opcode/bytes/address, opposite celua.txt order. NOP bytes 90, opcode `nop `, address resolves exactly |
| ce-native-map-01 | PASS: standalone enumeration and all name lookups on owned x64 target |
| ce-native-map-client-01 | PASS on client 3032: 2082 regions, enum 15 ms, measured naming total 109 ms including stage I/O |
| ce-native-bp0-01 | Fixture failure: expected boolean debug_isBroken, actually received function; generic diagnostic encoder then rejected that type; exact processes cleaned |
| ce-native-bp0-02/03 | Control reproduced running counter and repeated hits with callback 0; cleanup strict no-in-flight-hit assertion failed, not a complete lifecycle pass |
| ce-native-bp1-01 | Persistent stop observed; fixture rejected function-valued debug_isBroken and immediate still-listed deletion record; not a product regression |
| ce-native-bp1-02 | PASS: callback 1, first counter latched once, 11 stable samples over a second, literal-true context, one hit, continue/resumed counter, eventual empty native breakpoint list, detach |
| ce-native-bpnone-01 | PASS same lifecycle with no per-breakpoint callback |
| ce-native-stepinto-01 | PASS native co_stepinto: new stopped IP, stable counter, fresh literal-true context, continue and cleanup |
| ce-native-stepover-01 | PASS native co_stepover under the same bounded acceptance |
| ce-bridge-baseline-01 | PASS source service/pipe on isolated CE, module map and corrected NOP/RET function boundary |
| ce-bridge-client-01 | PASS source service/pipe map on client 3032; no ownership claim over the client |

The memory-map failures in the original long-lived CE have NOT been reproduced
by the isolated CE so far. Next discriminating boundary is original-instance
transport/autorun/symbol state, not a speculative enumeration rewrite.

## Debugger interpretation

Public debugeventhandler.pas assigns WaitingToContinue to a per-breakpoint
callback's nonzero result, but negates the global debugger_onBreakpoint result.
Thus the owned per-breakpoint callback must return 1; global default remains 0.
debug_getContext returns true only with an actual waiting thread, otherwise false
without clearing register globals. Non-nil globals therefore do not prove freshness.

The installed debug_isBroken returns a function in these probes and cannot be
used as a boolean guard. Stop acceptance instead requires the independently stable
counter plus literal-true debug_getContext on later timer turns. Production status
must refresh context truth rather than retain a callback-set paused flag.

Native breakpoint removal marks records for deferred deletion; immediate native
list presence is not a retry signal. A single removal request followed by one
explicit continue permits debugger cleanup. Probes require resumed counter,
no additional callback-1 hits, eventual empty list and debugger detach. Function-
valued remove returns are diagnostic only; no return-value truthiness acceptance.

## Fixes and acceptance

| Boundary | Change and verification |
|---|---|
| Disassembly | Corrected Lua return order; opcode is now mnemonic text. Function decoding stops at first ret and list byte budgets drive lazy reads. Lua semantic tests and isolated bridge NOP/RET fixture pass; installed MCP returns `call eax` at 0x6912A2, bytes FFD0, extra empty |
| Native stop/context | Per-breakpoint callback returns 1; global default remains 0. Status/session/guards refresh native waiting truth via literal-true debug_getContext. Registers reject false/nil/nonboolean context and reject process suspension. Native, source service and installed MCP acceptance pass |
| Stepping | Replaced guessed hardware destinations with native co_run/co_stepinto/co_stepover. Native and bridge tests verify new stopped IP and stopGeneration, stale-stop rejection and cleanup; no claim over all instructions/architectures |
| Events | Generation-bound sequence cursors replace ring indices. nextCursor also supports tail polling; droppedEvents reports retention gaps. Deterministic Lua rollover/stale-cursor tests pass; installed MCP pages 1/2 then 3/4 then an empty tail without replay |
| Memory map | Replaced full enumeration with getMemoryRegionInfo over merged matching module ranges (or target space when unfiltered). Stop at page lookahead, name only returned rows, cap inspection at 8192 native queries. Installed MCP now returns consecutive five-row pages under the unchanged deadline |
| Module rows | Deduplicate only exact native metadata duplicates; preserve distinct aliases. Installed KartRider.exe listing now returns one main image |
| Typed strings | Read complete 1/2-byte units strictly inside maxStringBytes and mark complete only after observing a terminator. Avoid native readString off-by-one and odd UTF-16 decoding. Byte-boundary semantic and native fixture tests pass |
| Base64 | Sidecar converts bridge hex when raw base64 was requested. Fixture decode-equivalence and invalid-hex tests pass; installed MZ read returns TVo= |
| Cancellation | Immediate queued signature cancellation, delayed observation after its timer, and close pass in bridge-data-02. Completed cancellation remains a no-op, not an active cancellation pass |

### Memory-map investigation outcome

`ce-original-map-transport-01` reproduced a five-second transport failure in the
original long-lived CE, while isolated Windows-query instances passed quickly.
`ce-original-map-long-01` used a diagnostic-only 30-second deadline and returned
success in 11594 ms. Its raw regions were private/free with different allocation
boundaries, rather than the MEM_IMAGE regions of the isolated run. Those fields
are compatible with CE's alternative query backends; they do NOT prove CE queried
itself. No API pointer, kernel/DMA setting or driver initialization was changed.

Public NewKernelHandler/dbk32 sources show query backends can synthesize private
regions and scan page tables. Installed custom autorun files also exist; file
presence alone is not evidence that a particular initializer executed. We did not
disable unrelated autorun scripts or force Windows metadata.

Installed celua.txt documents getMemoryRegionInfo(address). Native
`ce-native-single-region-01` checked it against enum output on the same addresses.
This permits the actual fix: query only needed regions, rather than wait for the
whole address-space enumeration. The production deadline was NOT increased.
`ce-bridge-paged-map-01` verifies the new source against the client in isolation;
the installed OpenCode path then passed page one and cursor="5" page two.

The map now retains bounded phase/write-count diagnostics in ce.status. Native
backend metadata still differs by CE configuration. An individual native query
can still block, and live mappings can move between offset-based pages. The
8192-query guard fails closed rather than claiming a complete filtered result.

### Additional data findings

`ce-bridge-data-01` exposed six failing cases: narrow string byte limits 1/5/6,
wide limits 1/5 (including garbage after an odd read), and raw base64 ignored.
`ce-bridge-data-02` passes all scalar/count-two integer, float and pointer cases,
string bounds and normal strings, raw encoding equivalence, pointer following,
unequal compare, thread continuation, exact scan/refine, unknown/unchanged scans,
queued cancellation and cleanup. These are source service/CE checks, not blanket
coverage of every string encoding, instruction, architecture or scan comparison.

### Installed acceptance and ownership

The user explicitly authorized normal close/restart of the original CE and bridge
installation, preserving the game and IDA. The bridge installer copied only the
source Lua bridge; no client bytes were patched. Original CE PID 2904 and later
diagnostic replacements were closed normally, never force-terminated. Final CE PID
13148 during acceptance, then 13484 after deploying the final review fixes; game
PID 3032 survived throughout. OpenCode disconnected/reconnected only cheat-engine.

Installed game checks at generation 3: memory-map pages 0/5, disassembly mnemonic,
one deduplicated main module, base64 MZ read and logical detach all pass. The exact
tool responses remain in session `ses_f7e783cedffepvafQMgSB2ni4H`.

Installed debugger check used owned x64 target 6096 (wrapper 13600), generation 6:
pause reported suspend and rejected registers; run resumed; one write breakpoint
stopped at generation 2 with counter 2576 unchanged across separate calls. General
and all 16 XMM registers were returned. Removed breakpoint, native step-into reached
generation 3/RIP 0x7FF9DDF649E0; old stop 2 was rejected. Step-over reached generation
4/RIP 0x7FF9DDF649E3. Event pages/tail matched these stops. The final run resumed the
target; its 300-second lifetime had elapsed, so the following read correctly failed
STALE_SESSION and status showed no target. Both owned PIDs exited. The earlier
`ce-bridge-debug-01` separately verifies resumed counter and explicit debugger/
process detach before target expiry. No debugger was attached to the game.

### Probe mistakes preserved

- Initial probe incorrectly required debug_isBroken to be boolean and immediate
  native list deletion; revised only after raw evidence/public binding inspection.
- A phase-diagnostic insertion initially landed in the event handler rather than
  map completion. It was corrected before installed event acceptance; early
  naming-stage markers alone were therefore insufficient to select a root cause.
- One Task invocation omitted required subagent_type and was rejected before work.
- One metadata patch used an incorrect maxLength context and applied no changes;
  the current file was reread before applying the correct patch.
- The final manual debugger target expired on resume; that is recorded as a
  lifecycle/timing limit, not fabricated successful post-resume counter evidence.

DBVM remains excluded. ABI/call-variant breadth, real event-ring overflow under
many native threads and adversarial mapping changes are residual coverage limits,
not evidence that the repaired bounded workflows failed.

Final review also reproduced and fixed two boundary cases: preexisting global
callbacks no longer bypass stop-generation/event bookkeeping, while their return
semantics are preserved; event counters reset on target-generation changes but
not debugger-only cleanup. Five new Lua semantic regressions cover these cases
and exact module deduplication. Final locked suite: 214 tests passed, zero skips;
installed Lua DLL discovery now includes the actual Program Files installation.
`ce-bridge-debug-final` and `ce-bridge-data-final` passed with both owned processes
exited and no cleanup errors. Early external-target probe reports incorrectly used
targetExited:true for a target they did not own; the runner now reports null there,
and the still-running client PID independently establishes survival.
The superseded debugger_lifecycle_probe.lua was removed rather than retaining its
known false-pass logic as an executable reference; the new isolated runner replaces it.
