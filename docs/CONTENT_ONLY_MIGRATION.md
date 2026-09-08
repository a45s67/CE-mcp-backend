# Content-only MCP migration

## Problem and boundary

User requirement: "ok, is it easy for you to discard structuredContent and just use
content ?" followed by "ok, plan it and let's go".

Original observed symptoms in OpenCode: `ce.status completed: bridgeConnected=true.`,
`ce.process.attach completed.`, and `ce.memory_read completed.` reached the model,
but process rows, generation, addresses and memory bytes did not. A pre-attach
`ce.process.get` returned `MCP error -32602: Structured content does not match the
tool's output schema: data must have required property 'action', data must NOT
have additional properties`. No screenshot was attached.

Acceptance: the model-visible text must contain the complete bounded result;
no-target get must expose the original error, not a success-schema validation
failure; tools/list must omit outputSchema; no mutation retry or generation guard
may be weakened. Adapter inspection confirmed summary-only text and a structured
error envelope incompatible with the advertised success schema. Missing bridge
data remains a separate possibility to distinguish with a real-CE read.

Observed source mismatch: the HTTP controller and SDK live smoke read
`structuredContent`; the stdio integration test expects it. After the backend
migration these consumers would reject valid results or lose error diagnostics.
This is a source-level finding, not a reproduced live CE failure.

Competing causes are an unmigrated consumer, an unmigrated adapter, or malformed
JSON/content from the endpoint. Decoder tests distinguish malformed payloads;
the official SDK stdio test against an intentionally absent pipe distinguishes
the adapter contract without attaching to CE.

Fix boundary: require exactly one text block containing a JSON object, use the
direct success object or `{ "error": ... }` with `isError`, reject structured
results, and assert that the catalog does not advertise `outputSchema`. Internal
service output schemas and validation remain unchanged. No legacy fallback.

## Verification

- Offline/parser: full locked suite on 2026-09-09: 164 tests, 162 passed and two
  CE Lua compilation checks skipped because their configured runtime was absent.
  Direct success, generation/bytes, error diagnostics, malformed content, legacy
  rejection, exact escaped-byte limits and no-replay mutation advice are covered.
- Protocol: pass, HTTP golden responses and official SDK stdio initialization,
  tools/list and error delivery against an intentionally absent pipe. Internal
  service contract tests remain intact. SDK-added `resultType` is included in
  the serialized result size limit.
- Real CE internal state: blocked before attachment. A fresh source MCP smoke
  targeting KartRider PID 3032 and CE PID 2904 initialized and listed tools, then
  received content-only `BRIDGE_UNAVAILABLE` at status. The existing OpenCode MCP
  still returned old summary text. Connection ownership/contention is a hypothesis,
  not proven; reconnect OpenCode to the updated source before retrying. No memory
  read, debugger action or target attachment occurred in that smoke attempt.
- Visual checks: not applicable to this transport-only change.
- Natural installed controller/live smoke behavior: not checked; live verification pending.

Commands (TEMP/TMP set to `C:\analysis\kartrider-docs\var`):

```powershell
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m ce_mcp.mcp_live_smoke --target-pid 3032 --ce-pid 2904 --deadline-ms 5000
```

The failed live smoke surfaced its diagnostic through nested SDK exception groups;
it did not produce a successful live report. No compiled release was rebuilt or
deployed. README.md has pre-existing unrelated edits and was deliberately left
untouched; its old transport description needs an owner-coordinated update.

## OpenCode live follow-up

Content-only model-visible acceptance passed after restarting the OpenCode-owned
stdio backend. `ce.status` returned full JSON, no-target `ce.process.get` returned
NO_TARGET, and attach/get/read/detach succeeded on KartRider PID 3032, generation 9.
The raw read at 0x00400000 returned `4D5A`, `complete:true`, with the session and
resolved address visible to the model. This supersedes the initial blocked
OpenCode acceptance above; the separate compiled-controller path remains untested.

Single-server reconnect was then reproduced on OpenCode 1.18.29, PID 8404, listening
on 127.0.0.1:4096. After guarded detach, the following calls returned true:

```text
POST /mcp/cheat-engine/disconnect?directory=C%3A%5Canalysis%5Ckartrider-docs
POST /mcp/cheat-engine/connect?directory=C%3A%5Canalysis%5Ckartrider-docs
```

The old uv/entrypoint/Python process tree exited (8072/3216/12660/3764), replaced by
10912/10368/4688/7616. CE PID 2904 and KartRider PID 3032 survived unchanged; the
idalib subtree was not restarted. Status, fresh attach at generation 13, MZ read
and guarded detach all passed in the same conversation. This is Python-process
restart, not Lua hot reload. Verify the listening PID belongs to the current
OpenCode instance and use its actual port/name/directory; these recorded IDs are
not reusable configuration.

## Bounded client smoke

Sequential MCP calls against the same client used generation 15. No target memory
writes, debugger start/pause, hardware breakpoints, DBVM, or UI input were used.
Process survival is not visual/gameplay acceptance. The retained OpenCode tool
transcript in session `ses_f7e783cedffepvafQMgSB2ni4H` contains the actual responses.

| Check | Observed result |
|---|---|
| Typed read at 0x40003C, u32 | 384 (PE offset 0x180) |
| Raw read at 0x400180, 64 bytes | Starts `504500004C010600`, complete |
| Checksum at 0x400000, 64 bytes | MD5 `70638c845eb5e57c7443562619f7ab40` |
| Compare same 64-byte region | equal:true |
| Threads limit 5 | Five IDs, truncated:true, nextCursor:"5" |
| Resolve KartRider.exe+180 | 0x00400180 |
| Disassembly 0x6912A2, five instructions | Displays `call eax`, `lea ecx,[ebp-04]`, `push ecx`, `mov edx,[ebp+08]`, `mov ecx,[edx]`; bytes and addresses visible |
| Pointer base 0x4001B4, offsets [0] | finalAddress 0x00400000; validation matched 1/1 |
| AOB scan 0x400000..0x400FFF, `4D 5A 90 00` | One result at 0x00400000; operation status/results/close pass |
| AOB refine unchanged | Explicit INVALID_PARAMS: AOB scans are immutable |
| i16 scan same range, 23117, alignment 2 | One result; unchanged refine/results/close pass |
| Signature 0x6912A2 in 0x691000..0x691FFF | `FF D0 8D 4D`, unique:true in that range, four bytes; close pass |
| Artifact dump/preview/delete | 64-byte dump; preview `4D5A90000300000004000000FFFF0000`; partial preview correctly complete:false; delete pass |
| Structure create/read/delete | DOS-header magic 23117 and PE offset 384; revision-guarded delete pass |
| Stale generation 14 read | STALE_SESSION, actualGeneration:15; no read performed |
| Operation cleanup | Empty operation list after both scans and signature closed |
| Isolated memory map, moduleFilter KartRider, limit 5 | BRIDGE_UNAVAILABLE; failure reproduced |
| Recovery and final cleanup | One status call succeeded with generation 15; guarded detach succeeded |
| Host/target survival | Get-Process reported CE 2904 and client 3032 Responding:true; client title ` Client` |

No cancellation-while-running, cursor-page continuation, all numeric/string read
types, register/stepping, DBVM or arbitrary object layouts were accepted by this
smoke. Earlier module listing returned duplicate KartRider.exe entries at the same
base; the Lua handler forwards enumModules entries without deduplication. That is
an observation, not a diagnosed enumeration defect.

### Memory-map problem record

Original agent-observed symptom: isolated
`ce.memory_map(moduleFilter="KartRider",limit=5,expectedGeneration=15)` returned
`BRIDGE_UNAVAILABLE` after the sequential successful calls above. An earlier
concurrent batch also failed, but does not establish execution order or cause.

Competing causes: full enumMemoryRegions cost, per-region getNameFromAddress cost,
or pipe response/connection failure. Source inspection at bridge handler lines
807-844 shows complete enumeration and per-region naming before filtering and
pagination. Thus limit=5 bounds returned rows, not work. The handler runs through
thread.synchronize; caller pipe timeout does not cancel the CE handler. These are
source facts, not proof that a particular API timed out. Status recovery and client
survival reject a persistent CE/client exit as the explanation for this run.

Next discriminating experiment: inspect duration/transport diagnostics, then an
observation-only disposable-target probe that times enumMemoryRegions separately
from per-region naming and response serialization. Do not increase timeouts or
rewrite the Lua handler speculatively. Fix boundary remains unselected. Offline
tests pass but this memory-map natural request assertion fails; visual/gameplay
checks are not applicable to its transport assertion and were not performed.

Verdict: content-only migration and same-session backend reconnect live verified;
bounded read/resource smoke largely passes, memory-map reliability remains open.

Expanded non-DBVM coverage, assistant call mistakes, and the subsequently reproduced
debugger paused-state/counter mismatch are recorded in NON_DBVM_LIVE_MATRIX.md.
Debugger transport success must not be interpreted as native stop acceptance.
