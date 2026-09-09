# Non-DBVM live MCP matrix

This document preserves the initial failure baseline and interface-only mitigation.
The subsequent native fixes and acceptance are indexed in NATIVE_RELIABILITY_FIXES.md;
the FAIL/BLOCKED rows below are historical observations, not the current verdict.

## Scope and evidence

User request (English translation): test everything except DBVM; record potential
adjustments and optimizations item by item, including the assistant's incorrect
tool calls. The original user text is retained in the referenced session transcript.

Evidence: OpenCode session `ses_f7e783cedffepvafQMgSB2ni4H`, actual content-only MCP
tool responses. See CONTENT_ONLY_MIGRATION.md for the preceding generation-15
baseline and controlled Python-backend reconnect. This is a bounded live smoke,
not exhaustive validation of every parameter combination, size or architecture.

- CE PID 2904, backend 0.2.0, OpenCode 1.18.29.
- Original client: KartRider PID 3032, x86, generations 15 and 17, base 0x400000.
- Debug target: owned `ce_mcp.debug_probe_target`, x64 PID 11400, parent 492,
  generation 20, watched u32 0x000002B6D5B48C88. No game debugger was started.
- Target creation: existing module, `--seconds 300`; info file
  `C:/analysis/kartrider-docs/var/sessions/ce-matrix-debug-20260909.txt`.
- No DBVM tool invoked. No target memory write tool, code patch or injection used.
- Expected-error checks are not product failures. Calls with incorrect assumptions
  are listed separately below. A returned success is not automatically acceptance.

## Per-action results

| Tool | Actions exercised and result | Remaining acceptance / adjustment |
|---|---|---|
| ce.status | PASS: full JSON before/after attachment, reconnect and map failure | Native paused truthfulness is a separate FAIL below |
| ce.process | PASS: list/get/attach/detach; NO_TARGET without attach; x86 and x64 identities | Attach returns reconciled:true in these runs; direct completion path not separately distinguished |
| ce.memory_read | PASS: raw hex, typed u16/u32, pointer metadata, complete and stale generation rejection | Other signed/float/string/wstring/base64/followPointers variants not individually verified |
| ce.memory_map | FAIL: standalone moduleFilter=KartRider, limit=5 produces BRIDGE_UNAVAILABLE | Diagnose enumeration vs naming vs transport; do not call a small page bounded work |
| ce.memory_analysis | PASS: checksum 64 bytes and equal self-compare | Different-region mismatch and unreadable-range variants not live checked |
| ce.symbols | PASS: resolve, describe, modules; list returns an empty valid response | Duplicate executable module rows observed; nonempty symbol list and cursor continuation not proven |
| ce.disassembly | PASS response/navigation: instruction/list/previous/next at 0x6912A2; function(summary) returns bounded-incomplete, 100 instructions | FAIL candidate: opcode contains byte text `FF D0 ` rather than `call eax`, while display is correct. Function full and genuine function-boundary accuracy unverified |
| ce.pointer | PASS: resolve and validate header ImageBase chain; 1/1 matched | Miss/unreadable/lifetime cases not covered in live run; finalPointer is an extra read of DOS magic, not finalAddress |
| ce.scan | PASS: AOB and i16 exact start, results, close; numeric unchanged refine; AOB refine rejected | Increased/decreased/changed/exact-refine and unknown/between variants not fully covered |
| ce.operations | PASS: get/list; empty after cleanup; cancel of completed signature is a no-op returning completed | Active cancellation NOT VERIFIED: cancellation request arrived after completion |
| ce.signature | PASS: start/result/close; four-byte bounded-range uniqueness; invalid range rejected | Cancellation while queued/running not proven; full-module pattern reached 13 bytes before no-op cancel |
| ce.artifacts | PASS: memory_dump/list/get_metadata/preview/delete, matching SHA-256 and partial-preview complete:false | Retention/large dump/corruption variants offline-only; DBVM archive excluded |
| ce.structures | PASS: create/update/get/list/read/delete; revision 1 -> 2; stale delete rejected, revision-2 delete succeeds | Larger layouts and every field type not live checked |
| ce.threads | PASS: list returns five rows, nextCursor=5 | Cursor continuation and target exit races untested |
| ce.debug_control | PASS: start/status/detach request lifecycle on disposable target. FAIL: status claims stopped/paused while watched writer runs | pause/continue(run, step_into, step_over) BLOCKED by native-stop validity failure; no stale context used for stepping |
| ce.breakpoints | PASS: write size4 set/list/remove response lifecycle; hits recorded; cleanup list count zero | FAIL sustained-stop assertion. execute/access/other widths BLOCKED pending callback lifecycle isolation |
| ce.debug_events | PASS transport: list returns event IDs/addresses/nextCursor | FAIL/optimization candidate: repeated hits while allegedly paused, rolling-index cursor may lose continuity; stable pagination not proven |
| ce.registers | PASS transport: general registers and 16 XMM fields returned | FAIL acceptance: context freshness/native stop unproven; matching stopGeneration is insufficient |

All 18 non-DBVM tool families have been invoked across the retained smoke sessions.
This does NOT mean all actions, variants or semantics passed. Blocking failures
prevent safe step/continue acceptance; active cancellation and read/scan variants
remain explicit gaps. Do not promote this table to blanket capability approval.

## Debugger problem record

Original agent-observed symptom: after one hardware write breakpoint, the debugger
reported stopped:true and session.state:paused, yet events continued without any
continue command. Status first showed stopGeneration 175; a later page contained
generations 363 and later. After removing the owned breakpoint, status remained
stopped:true, stopKind:debugger, stopGeneration:1108, breakpointCount:0.

Registers at generation 1108 returned RIP 0x00007FF9DDF649D9 and general/vector
fields. Two subsequent typed reads of the same watched counter returned 4367 and
4611 while both responses still labelled the session paused. This proves the
watched writer executed during that interval; it does not exclude brief native
stops between increments. No resume, pause or step was requested during it.

Competing causes:

1. Per-breakpoint callback return/dispatch does not retain a native stop on this
   CE build. The bridge publishes stopped=true before returning 0.
2. A pre-existing global debugger callback interferes with stop behavior.
3. Cleanup implicitly resumes a target. Less consistent with this sequence's
   active:true and retained breakpoint bookkeeping, but requires instrumentation.
4. Status/register responses are stale even though callback events are real.

Source evidence (bridge/ce_mcp_bridge.lua): per-breakpoint stop bookkeeping at
1297-1313; cached stop guards/status at 1072-1099; session state from the cached flag
at 342-353; removal at 1322-1327; register handler at 1365-1395 accepts a false
debug_getContext return and reads register globals. These findings select an
investigation boundary, NOT a justified callback-return patch.

Next discriminating experiment: isolated native CE probe with no callback,
return-0 callback and return-1 callback in separate runs. Latch the FIRST hit value
once, then independently sample watched counter, hit count and debug_isBroken
over 500-1000 ms without continue. Require stable counter and verified native stop
before one explicit continue. The existing debugger_lifecycle_probe.lua refreshes
valueAtHit on each callback and logs debug_isBroken without requiring it; strengthen
that acceptance before relying on its earlier passing result.

Fix boundary: native callback/stop/context lifecycle, not content serialization.
No production debugger code was changed during this investigation. Step execution
from the observed stale context was deliberately not attempted.

Verification layers: offline suite previously passed; MCP transport passed; native
sustained-stop assertion FAILED; visual UI and game natural behavior not checked.

## Assistant call mistakes and test limitations

| Actual call/decision | Classification | Result and correction |
|---|---|---|
| AOB scan -> refine unchanged, generation 15 | Unsupported operation chosen by assistant | INVALID_PARAMS, `AOB scans are immutable`; closed AOB scan and used i16 for successful unchanged refinement |
| signature.start address 0x6912A2, range 0x400000..0x500000 | Incorrect range from assistant | INVALID_PARAMS, `Address must be inside a valid explicit range`; corrected range to contain address |
| signature.cancel issued after signature completed | Timing/acceptance mistake, not API defect | Returned completed, not cancelled; recorded as no-op behavior, NOT active cancellation pass |
| Earlier parallel map/threads/compare/checksum batch | Poor diagnostic sequencing | Multiple BRIDGE_UNAVAILABLE responses did not identify first failing operation; reran sequentially and isolated map failure |
| Assumed limit=5 meant cheap memory-map work | Incorrect boundedness assumption | Source enumerates/names all regions before pagination; avoid repeating without timing probe |
| Tried separate source MCP while existing OpenCode pipe client remained connected | Ownership/contention risk in test setup | Source status returned BRIDGE_UNAVAILABLE; contention not proved; switched to verified single-instance reconnect |
| Accepted breakpoint/register transport success as potentially paused | Unsafe inference caught by execution witness | Counter advanced despite paused label; removed breakpoint, detached debugger, blocked stepping |

Intentional negative tests, not assistant mistakes: stale memory generation 14 vs
15, stale structure deletion revision 1 vs 2, and no-target process.get. Each was
expected to fail and did. The invalid signature range was NOT intentional.

## Prioritized follow-ups

1. HIGH: make debugger stopped/context claims evidence-backed; until then block
   stepping acceptance and do not use cached registers to choose step destinations.
2. HIGH: isolate memory-map failure with native phase timing and transport diagnostics.
   BRIDGE_UNAVAILABLE currently hides timeout vs pipe loss from the caller.
3. MEDIUM: verify/fix disassembly opcode/extra field mapping against installed CE's
   splitDisassembledString contract; display text alone should not hide a bad field.
4. MEDIUM: improve active-cancel test to atomically start/cancel a queued operation
   in a bounded scripted probe; do not start huge scans merely to outrun tool latency.
5. MEDIUM: audit event cursors under ring-buffer rollover and repeated hit storms.
6. LOW: decide module deduplication only after checking CE enumModules provenance.
7. LOW: add specific error hints for immutable AOB scans, finalAddress vs finalPointer,
   stale structure revision, and completed-operation cancellation.
8. LOW: finish nonempty symbol-list, page continuation, typed-read and scan variant
   fixtures; distinguish valid empty responses from positive capability evidence.

## Cleanup

- Game-side scans/signatures closed, artifacts deleted, structures deleted, and
  generation-17 process detached before debugger testing.
- Disposable-target breakpoint removed; debugger.detach returned active:false,
  stopped:false, breakpointCount:0; generation-20 process detach succeeded.
- The owned target was then stopped by exact PID after checking parent 492 and
  its expected module/info-path command line. Both 11400 and wrapper 492 exited.
- CE 2904 and KartRider 3032 remained running. No other Python, CE or IDA process
  was stopped. No DBVM resource was created.

## Interface improvements after review

User request: "think and enhance the mcp based on the experience".
This bounded update stays at the content-only adapter and published tool-contract
boundary. It does not patch CE callback behavior or claim to fix memory-map timing.

- Twelve tool descriptions now expose the measured caveats, rather than keeping
  them only in this document. Input-field descriptions explain immutable AOB scans,
  explicit signature range containment, completed-vs-cancelled state, pointer
  finalAddress vs finalPointer, revision refresh and partial artifact previews.
- Removed the debugger's unconditional "probe-verified" description. Debugger,
  register and breakpoint guidance explicitly warns about unverified native stops
  and directs callers to preserve cleanup and avoid further debugging until the
  native lifecycle is reverified. These are warnings, NOT a mechanical capability
  gate; the bridge still advertises these capabilities. Do not treat metadata as a
  substitute for the native fix or a security enforcement boundary.
- Server instructions now explain content-only JSON, completion/cancellation
  interpretation, and stopping a failed query sequence instead of parallel retries.

### Output-limit recovery problem record

Observed code defect: default-size overflow recovery proposed a value of 50 even
when an omitted typed-read count means one; explicit count/size/limit=1 was also
advised to retry unchanged. These are source observations reproduced by adapter
fixtures, not live target failures. An oversized error from a mutation also carried
recovery text claiming the mutation "completed" despite an unknown/error outcome.

Competing causes: incorrect implicit default, arithmetic clamping at one, and
success-only wording reused for error outcomes. Discriminating tests use direct
bounded-result fixtures, inspect argumentsPatch and safeToRetry, and independently
check completed-response vs error-response labels. No target API is needed.

Fix boundary: adapter recovery guidance only. Omitted page/string bounds now shrink
to one, never increase to 50. A default typed count of one or explicit bound of one
does not receive a same-size retry action. Oversized mutation errors request
reconciliation without asserting success, and preserve originalErrorCode (including
OUTCOME_UNKNOWN) in bounded details. Internal service validation and CE execution
remain unchanged.

Acceptance: offline recovery fixtures and published metadata contracts must pass;
the HTTP wire/official SDK suite must retain valid content-only responses. Live
memory-map/native-debugger failures remain open and are not reclassified by this
interface change. No visual or natural-gameplay change is claimed.

Validation completed: `uv run --locked python -m unittest discover -s tests -q`
ran 168 tests, 166 passed and the same two CE Lua-runtime checks were skipped.
`git diff --check` passed. A fixture initially used an invalid 5000-character
ErrorDetail.message and failed its 512-character model guard; it was corrected to
use a valid message and oversized details. This was a test-authoring mistake,
not a product regression.

After verifying loopback-listener ownership, the current OpenCode instance
disconnected/reconnected only cheat-engine successfully. The refreshed MCP returned
complete status JSON and NO_TARGET for get; client PID 3032 attached at generation
24, returned `4D5A` from 0x400000 with complete:true, and detached successfully.
No unsafe debugger or known-failing memory-map call was repeated. Output-limit
edge cases were verified with fixtures, not by forcing huge live target responses.
