# CE MCP Backend Roadmap

## Implementation status (2026-08-30)

All remaining CE-facing work follows the evidence and promotion gates in
[`DEVELOPMENT_WORKFLOW.md`](DEVELOPMENT_WORKFLOW.md). In particular, a contract
or mock test no longer counts as proof of CE runtime support.

- Phase 0 — complete: versioned contracts, canonical models, fake bridge,
  framing, validation, deterministic catalog, and official MCP SDK adapter.
- Phase 1 — implementation and real CE 7.5 x64 vertical slice complete:
  status, process list/attach/get/logical-detach, raw/typed memory read,
  memory map, symbols, and disassembly over a local named pipe.
- Current milestone — Phase 2: bounded scan and long-running operation handles.
- Phase 2 partial — versioned `ce.scan`/`ce.operations` contracts, one
  concurrent scan, eight retained handles, generation binding, progress,
  bounded result pages, cancel/close cleanup, and real CE 7.5 AOB scan are in
  place. Exact refinement passed standalone CE lifecycle and production MCP
  gates. Running-scan cancellation and pipe-disconnect cleanup also passed
  native and production gates. Unchanged/increased/decreased/changed refinement
  passed native and production MCP gates. Comparison refinements
  (between/bigger/smaller) remain pending. Bounded immutable memory-dump
  artifacts passed unit, security, official MCP SDK, and real CE x64 gates. Pointer
  resolve/validate passed a controlled two-level MCP gate; full native pointer
  scan/rescan remains unavailable because CE 7.5 Lua exposes no result-bearing,
  cancellable scanner API.
- Phase 1 hardening: the client verifies the named-pipe server PID
  is the selected/recognized CE process before writing a frame. CE Lua
  `createPipe` exposes no security descriptor parameter. The observed CE pipe
  ACL explicitly denies Network logons and permits local Everyone/Creator Owner;
  the user accepted this for a single-user workstation. Controlled x86,
  same-host latency, and natural target-exit gates passed.
- Phase 3 partial: Windows debugger control, generation-bound hardware
  breakpoints, bounded debug events, authoritative stop generations, stale
  continue rejection, and stopped-client disconnect cleanup passed production
  MCP gates on CE 7.5 x64. Active thread pause now also passed a production
  pause/event/resume gate. Bounded hardware one-shot `step_into` also passed x86
  production after native CE single-step callbacks proved unobservable. Threads,
  registers, software breakpoints, and general trace remain pending extensions.
- Phase 3 threads/registers partial: bounded thread-ID enumeration and
  stopped-only x64 general/XMM snapshots passed the production MCP gate.
  Arbitrary-thread metadata/context, x86 coverage, and register mutation remain
  pending.
- Phase 4 analysis partial: bounded target-to-target memory compare and MD5
  checksum passed a controlled production MCP gate. Bounded, cancellable exact
  signature generation also passed native and production gates. Relocation-aware
  wildcard inference remains pending; the explicit structure workspace is
  tracked as completed below.
- Phase 4 structure workspace: sidecar-owned bounded CRUD, revision guards, and
  generation-bound explicit-layout reads passed a controlled production x64
  gate without mutating CE global structures. Auto-guess, nesting, artifact
  export, and x86 coverage remain pending extensions.
- Phase 5 deferred/experimental: DBVM watch/trace API presence and fail-closed no-load status
  passed a clean-host production gate. Bounded `ce.dbvm_watch` and
  `ce.dbvm_trace` contracts and handlers, opaque generation-owned handles,
  cleanup at every ownership boundary, plus sidecar-profile and independent
  bridge-token authorization are implemented and compile/test cleanly. This CE
  CE 7.5 exposes watch/trace APIs but no readiness query APIs. A direct clean-host
  probe proved trace status returns `(0,0,0)` and physical translation returns
  nil without initialization. Missing readiness queries are therefore treated
  as `unverified`; starts proceed only past successful read-only physical
  translation. Positive lifecycle on an explicitly authorized, already-loaded
  DBVM host is still unproved and has moved to `TODO.md`; it is not a current
  release blocker and must never be satisfied through automatic initialization.
- CE 7.7 compatibility: build `7.7.0.10621` documents and exposes non-loading
  DBK/DBVM readiness queries. A clean-host runtime probe returned `false/false`,
  trace status `(0,0,0)`, and nil physical translation, with no resource created.
- Release hardening partial: the production MCP server now writes bounded,
  rotating JSONL audit records with a request ID shared with bridge calls,
  duration, action, mutation flag, generation, and error code. It never accepts
  request arguments or credentials as log fields, and durably records mutation
  acceptance before bridge execution. Same-host 30-sample
  status/read performance passed at 0.792 ms / 2.203 ms p95.
- Packaging gate: 119 tests passed before the 2026-08-30 wheel/sdist build. A clean no-deps
  wheel install exposed 20 contracts, a working `ce-mcp-install-bridge` entry
  point, and a packaged Lua bridge whose SHA-256 exactly matched source. Locked
  dependency runtime remains covered by the project venv suite.
- Natural target-exit gate passed after correcting CE 7.5's stale
  `getOpenedProcessID()` behavior with a successful-process-list liveness
  cross-check. Bridge and sidecar sessions both invalidated the old generation.

Cheat Engine 7.5 does not expose a reliable Lua operation for closing its
current process handle. `ce.process(action="detach")` therefore invalidates the
MCP session and generation and reports `ceHandleRetained: true`; it does not
claim that CE released the OS handle.

狀態：active，2026-08-29

## Goal

完成可用、可測試且安全的 Cheat Engine MCP backend：採用外部 MCP sidecar 與輕量 CE bridge，優先交付核心 process、memory、scan、disassembly 與 debug workflow，再補齊 threads、signature、vector registers、memory compare/checksum、structure workspace 與受控 DBVM watch/trace。

不包含分析 VM 的 power、snapshot、guest agent 或檔案投遞工具。

## Release strategy

- 每個 phase 都必須留下可執行測試與版本化 contract，不以「程式已寫完」作為完成標準。
- 先交付 `inspect`，再開放 `debug`、`modify` 與 `hypervisor` profiles。
- public MCP 使用少量 domain tools；bridge method 可以細粒度，但不得直接變成 175 個 public tools。
- 任一 phase 未通過 exit gate，不開始依賴它的 mutation 或 DBVM 能力。

## Phase 0 — Contract foundation

目標：在接觸真實 CE API 前凍結可測試邊界。

交付物：

- 專案骨架與 build/test commands。
- MCP tool input/output JSON Schemas。
- bridge frame、request/result/error envelope。
- canonical address、session/generation、cursor、operation、artifact models。
- capability profiles 與 scoped approval contract。
- fake CE bridge，可重播 success/error/state-change fixtures。
- contract tests 與 schema compatibility check。

Exit gate：

- 核心 tools 的成功與主要錯誤 fixture 全數通過。
- 64-bit address 不經 JSON floating number。
- stale generation、timeout、oversize frame、unknown mutation outcome 有 deterministic result。
- tool catalog deterministic，schema lint 無錯。

## Phase 1 — Read-only vertical slice

目標：讓 agent 能安全連接 CE、attach target 並完成基本程式探索。

交付物：

- Windows named-pipe bridge、ACL、main-thread dispatcher 與 reconnect。
- Streamable HTTP MCP sidecar；stdio 作本機開發模式。
- `ce.status`。
- `ce.process`: `list|attach|detach|get`。
- `ce.memory_read`、`ce.memory_map`。
- `ce.disassembly`: `list|instruction|function|previous|next`。
- `ce.symbols`: `resolve|describe|modules|list`。
- structured logging、request correlation、limits 與 redaction。

Exit gate：

- CE Tutorial x86/x64 均可 attach、resolve module、read typed/raw memory、disassemble、detach。
- CE 關閉、target exit、pipe disconnect 後不殘留有效 session/handle。
- `status` p95 < 200 ms，4 KiB read p95 < 500 ms（同機、不含 cold symbol load）。

Release：`v0.1 inspect-preview`。

## Phase 2 — Search and long operations

目標：完成實際尋址工作流，且不讓大型結果卡住 MCP request 或 CE UI。

交付物：

- `ce.scan`: initial/refine/AOB/module/results/cancel/close。
- `ce.pointer`: resolve/scan/rescan/results/validate。
- `ce.operations`: get/list/cancel/expiration/cleanup。
- `ce.artifacts`: memory dump、metadata、bounded preview。
- pagination、result limits、progress 與 cancellation。
- scan/watch/operation zombie cleanup。

Exit gate：

- CE Tutorial 可完成 unknown/exact/refine 與 AOB scan。
- pointer chain 可解析並在 target restart 後正確失效。
- cancel、timeout、disconnect、oversize result 均能 cleanup。
- 長 scan 不會長時間阻塞 CE GUI main thread。

Release：`v0.2 analysis-preview`。

## Phase 3 — Debug workflow

目標：支援 agent 以明確 stop generation 做可控的動態追蹤。

交付物：

- `ce.debug_control`: start/pause/continue/step/run-until/stop/detach。
- `ce.threads`: list/get；受控 break/suspend/resume 延後到 profile 核准。
- `ce.registers`: general、flags、segments、XMM/vector read；set 置於 mutation policy。
- `ce.breakpoints`: software/hardware/data、thread filter、hits/event cursor。
- `ce.trace`: bounded start/status/cancel/results。
- debugger event queue、stop reason 與 stop generation。

Exit gate：

- breakpoint hit 回傳 thread、IP、register context 與 stop reason。
- stale `expectedStopGeneration` 無法 set register 或 continue。
- hardware slot exhaustion、target running/paused state errors 可預測。
- detach/reload 清除 DR slots、breakpoints 與 event subscriptions。

Release：`v0.3 debug-preview`。

## Phase 4 — Controlled mutation and analysis parity

目標：補齊參考 bridge 中真正有分析價值的差距，同時維持可復原修改。

交付物：

- `ce.memory_write` 與 verify/before-after hash。
- `ce.memory_manage`: allocate/free/protect/query。
- `ce.assembly`: assemble、AA preview/apply、patch ID、disable/rollback。
- unique AOB signature generation and validation。
- memory compare/checksum/string search。
- `ce.structures`: dissect/list/get/create/update/delete/export。
- `ce.analysis`: references、call references、RTTI、pointer access。
- `ce.cheat_table` 作選配 profile。

Exit gate：

- 所有 mutation 都驗證 session generation 並需要正確 profile/approval。
- patch preview 列出預期 allocation/write ranges；rollback 驗證原始 bytes。
- signature 在指定 module/range 驗證 uniqueness。
- 未核准的 write、Lua、host path、shell、injection 均被拒絕並留下 audit event。

Release：`v0.4 modify-preview`。

## Phase 5 — DBVM and release hardening

目標：以選配、可清理、預設關閉的方式加入 CE Ring -1 觀測。

交付物：

- `ce.dbvm_watch`: start/status/events/stop。
- `ce.dbvm_trace`: start/status/results/archive_results/stop/remove。
- DBK/DBVM capability detection，不因 status 自動載入。
- physical/virtual address metadata、bounded event buffer、stack capture opt-in。
- integration、robustness、security、performance 與 packaging suites。
- 安裝、升級、復原、emergency stop 與最小權限文件。

不納入：physical memory write、MSR/CR write、cloak、change-register-on-breakpoint、global TSC speedhack、CPUID logging。

Exit gate：

- DBVM 未載入時 graceful capability/error，不影響非 DBVM tools。
- disconnect、detach、bridge reload 後 watch/trace 全數 cleanup。
- Windows 11 x64、CE 7.5 x64、x86/x64 target 驗收通過。
- [REQUIREMENTS.md](REQUIREMENTS.md) 的所有 MUST 項目均有測試或文件證據。

Release：`v1.0`。

## Priorities

| Priority | Scope |
|---|---|
| P0 | Contract、bridge safety、session/generation、read-only vertical slice |
| P1 | Scan、operations、debug、threads、breakpoints |
| P2 | Controlled write/patch、signature、memory analysis、structures |
| P3 | Cheat table、injection、DBVM watch/trace |
| Excluded | VM management、host/guest shell、GUI/input automation、unsafe DBVM/kernel mutation |

## Current milestone

**Phase 0 — Contract foundation** is active.

已完成第一個 contract slice：Python 3.10 專案 layout、fake bridge、framing、共同 models、service facade，以及 `ce.status`/`ce.process`/`ce.memory_read` 的第一版 schema 與 contract tests。Phase 1 唯讀 catalog 已增加 `ce.memory_map`、`ce.disassembly`、`ce.symbols`，並完成 bridge method routing 與輸出驗證。

Windows named-pipe connector、最小 CE Lua bridge、官方 MCP SDK 2.x stdio/Streamable HTTP adapter 與可安裝 wheel 已完成。MCP stdio 的 initialize、tools/list、tools/call 已通過真實 client/server integration；HTTP 限制 localhost 並要求 bearer token。

下一個具體交付是讓 `ce.status`、process list/attach 與 raw memory read 在 Cheat Engine 7.5 上完成真實 end-to-end smoke test，接著實作 Lua bridge 的 memory map、disassembly 與 symbols handlers。
