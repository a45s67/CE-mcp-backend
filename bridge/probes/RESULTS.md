# Probe results

## 2026-08-31 — Published Codex plugin live matrix on CE 7.7

- Installed `ce-mcp-backend@a45s67` was exercised through its exposed MCP tools
  against disposable x64 Python and Notepad targets; no arbitrary Lua or target
  write was used.
- Verified status/process lifecycle, module and symbol resolution, bounded raw
  and typed memory reads, maps and pagination, compare/checksum, all
  disassembly actions, exact and increased scans, operation listing, pointer
  resolution/validation, signature generation, structure CRUD/read, and
  artifact dump/list/metadata/preview/delete.
- Verified Windows debugger start/status/detach, thread listing, execute/write/
  access hardware breakpoints, the four-slot limit, events, general and XMM
  registers, stale-stop rejection, step event, synchronous pause/resume, and
  breakpoint cleanup.
- Verified stale session rejection, detached `NO_TARGET`, target-exit cleanup,
  invalid read limits, unreadable memory, and normal-profile DBVM rejection.
- Scan and signature cancel requests were issued, but the bounded operations
  completed before cancellation won the race. Deterministic running/queued
  cancellation remains covered by the dedicated lifecycle gates rather than
  being inferred from this timing-dependent run.
- All disposable processes, debugger resources, operations, structures, and
  artifacts created by the matrix were removed.

## CE 7.7 readiness-query runtime probe (2026-08-29)

`C:\tools\Cheat Engine` contains CE `7.7.0.10621`. Its `celua.txt` formally
documents `dbk_initialized()` and `dbvm_initialized()` as non-loading queries.
An isolated CE 7.7 x64 runtime probe confirmed both functions exist, execute
successfully, and return `false` on a clean host. Trace status returned
`(0,0,0)` and physical translation returned nil. No initialization or DBVM
watch/trace resource was created. This validates the production readiness logic
for CE 7.7 and explains why the earlier CE 7.5 build reported `unverified`.

## Direct clean-host DBVM API probe (2026-08-29)

An isolated CE 7.5 x64 process attached to a cooperative Python target without
calling `dbk_initialize` or `dbvm_initialize`. API presence was true for trace
status, physical translation, and watch lifecycle functions, while both
readiness-query functions were absent. Direct `dbvm_traceonbp_getstatus()`
succeeded with `(0, 0, 0)`. `dbk_getPhysicalAddress()` returned normally but
with `nil`, so the bounded probe stopped before creating a watch. This proves
status is safe without DBVM and supports optional readiness queries; it does not
prove a watch can start while DBK/DBVM are disabled.

## CE `createPipe` security descriptor (2026-08-29)

An isolated CE 7.5 x64 process (PID 7428) loaded the production bridge. The
read-only `ce_mcp.pipe_acl_inspect` diagnostic opened only
`CE_MCP_Backend_v1_7428`, verified the server PID, and returned:

`O:<current-user>G:<primary-group>D:(D;;FA;;;NU)(A;;0x12019f;;;WD)(A;;0x12019f;;;CO)`

This explicitly denies Network logons and permits local Everyone plus Creator
Owner pipe access. It is acceptable for the selected single-user local
workstation threat model, but it does not isolate mutually untrusted local
accounts. A separate experiment confirmed that CE's returned server handle
cannot be hardened afterward: `SetKernelObjectSecurity` failed with Win32
`ERROR_ACCESS_DENIED (5)`. No production ownership reversal was retained.

## 2026-08-29 — MemScan first/refine lifecycle

- CE: 7.5 x64 on Windows 11 x64
- Target: Windows PowerShell x64, disposable sleeper process
- Probe: `memscan_lifecycle_probe.lua`
- Range: first module base through base + 4095
- Sequence proved: first scan, wait, first FoundList, exact next scan, wait,
  destroy first FoundList, second FoundList, result validation, cleanup
- Result: `stage=passed`; CE remained responsive

This proves the native synchronous lifecycle only. Bridge operation behavior,
cancellation, disconnect, and generation cleanup require separate gates.

## 2026-08-29 — Exact refine bridge/MCP promotion

- Production bridge only; lifecycle probe autorun removed
- MCP flow: attach, AOB initial/results/close, numeric initial, exact refine,
  refined results, close, logical detach
- Result: exact refine completed; both AOB and refined numeric scans retained one
  result at the module base; CE remained responsive
- Not proved by this run: relative refine modes, cancellation during a running
  scan, disconnect cleanup, x86 target behavior

## 2026-08-29 — Running MemScan destruction lifecycle

- Target: disposable PowerShell process with a 256 MiB allocation
- Probe observed incomplete `getProgress()` values before destruction
- Result: `stage=passed`; `MemScan.destroy()` returned and a later GUI timer ran
- This proves native running-scan destruction; bridge cancellation and
  disconnect cleanup require their own gates.

## 2026-08-29 — Cancellation and disconnect promotion

- Production cancel flow observed a full-range unknown scan in running state,
  cancelled it, closed the handle, then successfully called status,
  disassembly, and detach.
- Disconnect flow closed the first pipe client while a scan was running. A new
  client retained the target session but listed zero operation handles, then
  detached successfully.
- Result: cancellation and disconnect cleanup bridge/MCP gates passed on CE 7.5
  x64 with a disposable 256 MiB PowerShell target.
- Remaining coverage: target exit during scan, x86 target, forced CE exit, and
  relative refinement modes.

## 2026-08-29 — Relative refinement promotion

- Native probe used one controlled `int32` allocation and proved the complete
  unchanged, increased, decreased, and changed sequence on CE 7.5 x64.
- The first probe attempt used a four-byte address range and returned no
  baseline result. CE scans the containing memory region before candidate
  filtering; scanning the full committed 4096-byte allocation corrected the
  test assumption. This was a probe defect, not evidence against refinement.
- Production MCP vertical slice attached to the smoke process itself, scanned
  a page containing a controlled `ctypes.c_int32`, changed it between requests,
  and retained its exact address through all four refinements.
- Result: all four relative modes are MCP-verified. `between`, `bigger`, and
  `smaller` remain fail-closed pending their own controlled-value gate.

## 2026-08-29 — Debugger lifecycle

- Target: cooperative Python x64 process updating one controlled `uint32` every
  20 ms.
- CE: isolated CE 7.5 x64 using a unique `CE_MCP_PIPE_NAME` and Windows debugger.
- Sequence proved: debugger start, hardware write-breakpoint install, callback
  hit, `debug_getContext(true)`, RIP capture, 16-byte XMM0 local read, scripted
  continue, breakpoint removal, and debugger detach.
- Result: `stage=passed`, with `context=true` and `xmm=16`.
- Follow-up stopped-state gate returned from the breakpoint callback without
  continuing, then inspected context/XMM and continued in a later main-thread
  timer turn. The watched value remained unchanged across those turns
  (`targetStopped=true`) while `debug_isBroken()` still reported false. Event
  arrival, not `debug_isBroken()`, is therefore the authoritative stop signal.
- Two earlier attempts remained at the pre-debug attach trigger because the
  smoke client connected to another CE instance owning the default pipe. No
  debugger API ran in those attempts. A unique pipe made CE identity explicit
  and is now a mandatory real-CE test gate.

## 2026-08-29 — Debugger MCP promotion and disconnect cleanup

- Production MCP passed Windows debugger start, hardware write-breakpoint
  set/hit/remove, bounded event observation, stale `stopGeneration` rejection,
  correct continue, and debugger detach against a cooperative x64 target.
- A separate gate disconnected the only pipe client while stopped. Reconnect
  observed CE had released the opened-process session during fail-safe debugger
  detach; after exact-PID reattach, debugger state was inactive and breakpoint
  and event collections were empty.
- Result: core control/breakpoint/event MCP promotion and stopped-disconnect
  cleanup passed. Thread identity, register tools, stepping, run-until,
  software breakpoints, and trace remain unproved.

## 2026-08-29 — Threads and stopped register snapshots

- `getThreadlist` returned four thread identifiers for the cooperative x64
  target through the bounded production `ce.threads` MCP tool.
- During the controlled hardware write-breakpoint stop, `ce.registers` returned
  general registers including RIP and copied all 16 XMM registers as 16-byte
  hex snapshots before the target was continued.
- CE 7.5 returned boolean false from `debug_getContext(true)` while still
  populating valid register globals. Its documentation defines no boolean
  return semantics, so the bridge treats exceptions or a missing instruction
  pointer as failure and validates actual output instead.
- Arbitrary-thread context, thread state/name/TEB, register writes, and x86
  register coverage remain unproved and are not advertised by these tools.

## 2026-08-29 — Bounded memory compare and checksum

- A controlled x64 target exposed two equal 4096-byte regions and one region
  with exactly one changed byte at offset 2057.
- Production `ce.memory_analysis` reported equality for the identical pair and
  the exact first-difference offset for the changed pair using target-to-target
  `compareMemory` only.
- CE `md5memory` matched an independently calculated Python MD5 digest for the
  same controlled bytes.
- Both actions are read-only, generation-bound, and limited to 1 MiB. CE-local
  comparison modes, host paths, and unbounded ranges are not exposed.

## 2026-08-29 — Bounded signature generation

- Standalone lifecycle probing over a controlled 64 KiB target range observed
  two matches for the first eight bytes and exactly one target match after the
  ninth byte, destroying each intermediate FoundList and MemScan.
- Production `ce.signature` returned the expected nine-byte exact AOB through
  `start`, `ce.operations(get)`, `result`, and `close`.
- A second operation was cancelled in its queued response-flush window; after
  the timer deadline it remained cancelled and closed cleanly.
- Early production attempts exposed two lifecycle defects: starting a fast
  worker before the pipe response flushed, and terminating an already-finished
  signature thread during close. The final design defers work by the same
  proved one-second boundary as process attach and only terminates queued or
  running signature workers.
- Whole-memory `getUniqueAOB` and speculative relocation wildcards remain
  disabled. Signature ranges are explicit and capped at 64 MiB; candidates are
  exact and capped at 64 bytes.

## 2026-08-29 — Sidecar structure workspace

- Production MCP created, listed, revision-updated, read, and deleted a
  sidecar-owned definition without adding anything to CE's global structure
  list.
- A packed x64 target layout verified six copied field types: raw bytes, u32,
  u64, f32, bounded string, and pointer. The read was target-generation-bound
  and the bridge independently enforced field count, offset, type, and size
  limits.
- Result: deterministic structure workspace CRUD and explicit-layout target
  reads passed. CE heuristic `Structure.autoGuess`, nested child structures,
  artifact export, x86 pointer width, and global GUI integration remain separate
  unproved capabilities.

## 2026-08-29 — DBVM clean-host capability detection

- A fresh isolated CE 7.5 x64 instance was queried twice through production
  `ce.status` without attaching a target.
- The build exposed watch/trace functions but did not expose
  `dbk_initialized` or `dbvm_initialized`. Both status calls therefore kept
  `dbvm.watch` and `dbvm.trace` out of enabled capabilities and returned the
  same no-query-API disabled reason.
- Static bridge tests prove the status handler references only the non-mutating
  state-query names and contains no `dbk_initialize` or `dbvm_initialize` call.
- Result: clean-host no-load behavior passed. Positive DBVM watch/trace lifecycle
  remains pending a DBVM-capable host plus bridge/profile authorization.

## 2026-08-29 — Controlled DBVM contracts and dual authorization

- Added bounded generation-owned watch (`status/start/events/stop`) and the
  single-resource trace (`status/start/results/stop/remove`) contracts and Lua
  handlers. All handles are cleaned on target replacement, logical detach,
  client disconnect, and bridge stop/reload.
- The sidecar defaults to `debug`; `hypervisor` requires a local configuration
  token which is injected only after public schema validation. The bridge
  independently snapshots its own policy and compares that token before DBVM
  handler lookup. Neither boundary initializes DBK or DBVM.
- CE's local documentation does not define a return value for
  `dbvm_traceonbp`, so start is confirmed with `dbvm_traceonbp_getstatus` rather
  than treating nil as failure.
- The full Python suite passes 105 tests, including compilation with CE's Lua
  5.3 runtime. An attempted isolated positive/fail-closed host gate was denied
  because installing a hypervisor-enabled autorun policy was not authorized;
  the denied operation was atomic and created no autorun files or processes.
  Positive lifecycle remains explicitly unproved.

## 2026-08-29 — Active debugger pause and resume

- The reference bridge exposes `debug_breakThread` as a fire-and-forget call.
  The production bridge adds the missing lifecycle: a temporary
  `debugger_onBreakpoint`, five-second timeout, restoration of any previous
  callback, a bounded `pause` event, and a new authoritative stop generation.
- An isolated CE 7.5 x64 gate attached to a cooperative target, observed the
  existing hardware write breakpoint at stop generation 2, continued, then
  actively paused one of four enumerated threads at stop generation 3. General
  context was readable, resume and debugger detach succeeded, and 16 XMM
  snapshots remained available in the breakpoint portion of the gate.
- Exact test PIDs and the temporary autorun bridge were removed after path and
  start-time verification. No test CE or target process remained.

## 2026-08-29 — x86 debug, hardware stepping, pipe identity, and latency

- A Zig-built cooperative x86 target passed process attach, hardware write
  breakpoint, stopped x86 general context, all eight x86 XMM snapshots,
  generation-bound `step_into`, synchronous process pause/resume, and detach.
  Stop generations advanced deterministically from breakpoint 2, step 3, to
  pause 4.
- CE 7.5 did not deliver `debug_breakThread` or native single-step completion
  through the expected Lua callback. Those failed gates drove two corrections:
  process-wide pause now uses documented `pause`/`unpause`, while stepping uses
  bounded one-shot hardware execute breakpoints derived from CE disassembler
  metadata. Temporary step slots are removed on hit and every cleanup boundary.
- The Windows client used `GetNamedPipeServerProcessId` before sending any frame
  and proved the real server PID matched the selected CE instance. This closes
  same-name pipe impersonation, but CE Lua `createPipe` still offers no security
  descriptor parameter; explicit native ACL remains open.
- A separate x64 4 KiB target passed 30-sample warm same-host performance gates:
  status p95 0.792 ms and raw 4 KiB read p95 2.203 ms, versus required 200 ms and
  500 ms thresholds. The repeatable gate is `ce-mcp-performance-smoke`.

## 2026-08-29 — Natural target exit and packaged delivery

- CE 7.5 retains the old value from `getOpenedProcessID()` after a process
  exits. The bridge now cross-checks a successful read-only `getProcesslist()`;
  a missing PID advances generation and invokes all debugger, operation, and
  hypervisor cleanup. If enumeration itself fails, it does not guess.
- A cooperative target exited naturally after attach. Production `ce.status`
  then omitted the bridge session, the sidecar cleared its cached session, and
  a call carrying old generation 4 was rejected with `NO_TARGET`.
- Wheel and sdist builds succeeded after 114 tests. A disposable no-deps wheel
  install exposed 20 contracts and the bridge installer; the installed Lua
  bridge hash exactly matched source. Runtime dependencies remain locked and
  tested in the project uv environment.
