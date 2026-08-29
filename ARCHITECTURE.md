# CE MCP 架構設計

狀態：初版設計，2026-08-28

## 1. 目標

提供 AI agent 一個穩定、可稽核且可復原的 Cheat Engine 動態分析介面，涵蓋：

1. 透過 Cheat Engine 做程序、記憶體、掃描、反組譯、除錯、pointer 與 DBVM 分析。
2. 未來由 Dynamic Analysis Gateway 將 CE、x64dbg 與 WinDbg backend 聚合為單一遠端 MCP endpoint。

不以「暴露所有 CE Lua API」為目標。MVP 應提供少量、結構化且可組合的工具；罕用或極高風險能力經 capability profile 才啟用。

## 2. 參考實作結論

| 來源 | 可保留做法 | 不採用做法 |
|---|---|---|
| `CE-MCP-Plugin` | CE plugin SDK 的直接宿主能力 | `COMMAND:parameters`、硬編碼 TCP、單一巨大 dispatcher |
| `cheatengine-mcp-bridge` | worker IPC、CE main-thread marshaling、length-prefixed JSON、scan/watch cleanup | 約 175 個原子工具、Python/Lua 重複 schema、長工作卡住 CE UI |
| survey gateway | localhost backend、固定 gateway、dotted namespace、動態 catalog | MVP 內做 semantic auto-routing 或公開所有 offline tools |
| `celua.txt` | 正式 CE Lua/DBVM API 為 bridge 能力基準 | 直接把任意 Lua execution 預設開放給遠端 client |

## 3. 元件與信任邊界

```text
Codex / Claude
      |
      | HTTPS Streamable HTTP + bearer token
      v
Dynamic Analysis Gateway              management network
      |
      +-- ce.* --> CE Backend :8001 -- local framed IPC --> CE Bridge
                                                  --> Cheat Engine
                                                  --> target process / DBVM
```

### CE Bridge

CE process 內只保留：

- CE Lua/plugin API adaptation；
- main-thread 安全派送；
- event capture；
- scan、breakpoint、watch 等 handle 的生命週期；
- bounded local IPC、health、版本與 capability 回報。

不得在 CE process 內實作公開 HTTP、認證、大型資料分析或 workflow orchestration。

### CE Backend

獨立 sidecar 負責：

- MCP Streamable HTTP（另提供 stdio 開發模式）；
- schema、驗證、權限與審計；
- 將 domain tool 展開成 bridge calls；
- pagination、artifact、operation 與 cancellation；
- 每一 CE instance 一條 serialized mutation queue。

## 4. Transport

### 公開端

- Gateway：HTTPS Streamable HTTP，固定 `/mcp`。
- Backend：`127.0.0.1` 上的 Streamable HTTP，固定 `/mcp`，各自 bearer token。
- CE local bridge：每個 CE instance 使用含 CE PID 的 Windows named pipe；
  sidecar 單一 instance 自動發現、多 instance fail-closed 並要求 `--ce-pid`；
  frame 為 `uint32-le length + UTF-8 JSON`。
- named pipe ACL 僅允許執行 backend 與 CE 的 Windows identity。

TCP relay 僅作明確啟用的相容模式，必須 mTLS 或 tunnel；不能像現有原型一樣把無認證控制 port 綁在可路由網卡。

### Bridge envelope

```json
{
  "protocolVersion": 1,
  "requestId": "01J...",
  "sessionId": "ce-01J...",
  "method": "memory.read",
  "params": {"address": "sample.exe+0x1234", "size": 64},
  "deadlineMs": 5000
}
```

回應必須為互斥的 `result` 或 `error`。每個 frame 上限 8 MiB；大資料改寫 artifact。

## 5. 狀態與識別

CE session 狀態：

```text
offline -> online -> attached/running <-> attached/paused -> exited
```

每次 CE attach/detach 或 target restart 都產生新的 `generation`。所有 state-sensitive mutation 可帶 `expectedGeneration`；不符時回 `STALE_SESSION`，防止 agent 用舊地址寫入新程序。

核心識別：

- `sessionId`：一次 CE attach 的 ID。
- `generation`：單調遞增的 target state 世代。
- `operationId`：掃描、dump、restore 等長工作。
- `artifactId`：受控 artifact store 中的不可變物件。

## 6. 統一資料規則

- 64-bit address 一律用 canonical hex string，不可用 JSON number。
- address input 可為 `module+rva`、symbol 或 absolute address；output 同時提供 resolved address、module、RVA、pointer width。
- bytes 用 bounded hex/base64；超過門檻回 artifact。
- list 一律有 `items`、`nextCursor`、`truncated`。
- 時間用 UTC RFC 3339；duration 用整數毫秒。
- mutation 結果回傳新的 state/generation，以及實際改動範圍。

## 7. 長工作與 artifact

掃描、pointer scan、trace、memory dump 與 symbol load 採 operation 模型：

1. start tool 回 `operationId`。
2. `operations.get` 回狀態、進度與時間。
3. `operations.cancel` 請求取消。
4. 完成後 inline 回小結果，大結果回 `artifactId`。

operation 狀態為 `queued|running|succeeded|failed|cancelled|expired`。結果至少保留 30 分鐘；cleanup 必須停止 CE scan/watch handle 並刪除受控暫存檔。

## 8. 安全模型

### Capability profiles

| Profile | 允許能力 |
|---|---|
| `inspect`（預設） | 狀態、process list、read、map、scan、disassembly、symbols |
| `debug` | inspect + pause/continue、register、breakpoint、trace |
| `modify` | debug + write、protection、allocate、patch/auto-assemble |
| `inject` | modify + DLL/code injection、remote execution |
| `hypervisor` | 明確列出的 DBVM read/watch/trace；physical write 仍另需核准 |

任意 Lua、任意 host shell、任意 host filesystem、DBVM key/MSR/control-register write 不進 MVP。所有 artifact path 經 allowlist root 解析與 canonicalization。

### 高風險操作

下列工具需要 `approvalToken` 或部署層的 interactive approval：memory write、patch、injection、remote code execution、physical memory write。token 綁定 principal、tool、精確參數摘要、session/generation，且短期有效、單次使用。

### 稽核

每次 mutation 記錄 principal、request ID、tool、target、參數摘要、前後 generation、結果與 duration；不得記錄 bearer token、完整 memory bytes、sample secrets 或 remote execution output 全文。

## 9. 錯誤契約

```json
{
  "code": "TARGET_NOT_PAUSED",
  "message": "Register update requires a paused target",
  "currentState": "running",
  "recoverable": true,
  "safeToRetry": true,
  "suggestedAction": "ce.debug_control(action='pause')"
}
```

穩定 error code 至少包含：`BACKEND_OFFLINE`、`BRIDGE_UNAVAILABLE`、`NO_TARGET`、`TARGET_RUNNING`、`TARGET_NOT_PAUSED`、`STALE_SESSION`、`ADDRESS_UNRESOLVED`、`ACCESS_DENIED`、`CAPABILITY_DISABLED`、`LIMIT_EXCEEDED`、`OPERATION_BUSY`、`TIMEOUT`、`CANCELLED`、`OUTCOME_UNKNOWN`、`DBK_NOT_LOADED`、`DBVM_NOT_LOADED`。

transport 中斷後不得自動重試 mutation；若可能已執行，回 `OUTCOME_UNKNOWN` 與 `safeToRetry:false`。

## 10. 非功能需求

- `status` p95 < 200 ms；4 KiB memory read p95 < 500 ms（同機、不含符號載入）。
- CE mutation 嚴格序列化；read 只有在 CE API 確認 thread-safe 時才可有限並行。
- 每 client 最大 4 個 request、每 backend 最大 2 個長 operation；均可設定。
- 預設 inline output 256 KiB、最大 memory read 1 MiB、單頁 list 200 items。
- backend/bridge crash 不得造成 CE/target crash；重連後舊 handle 全部失效。
- config 與 tool schema 版本化；新增 optional field 保持 backward compatible。
- Windows x64 為 MVP；CE 7.5 x64 為最低驗證版本，x86 target 為必要測項。

## 11. MVP 里程碑

### M0：contract 與 simulator

- 固定 JSON Schema、錯誤、state/generation、operation contract。
- fake CE bridge 可重播測試 fixture。

### M1：唯讀 CE

- status/process/attach、memory read/map、scan、disassembly、symbols。
- named pipe ACL、timeouts、pagination、artifact。

### M2：除錯與受控修改

- breakpoint/events/register/debug control。
- write/allocate/protection/auto-assemble 置於 `modify` profile 並要求 approval。

### M3：進階分析

- pointer scan、structure、trace、DBVM watch。
- DBVM physical write、cloak、MSR 等能力延後並預設永久關閉。

## 12. 驗收基線

- unit：schema、address parser、ACL/path、capability、cursor、error mapping。
- contract：所有 tools/list schema 與成功/失敗 fixture。
- integration：CE tutorial x86/x64 的 attach/read/scan/breakpoint/cleanup。
- robustness：CE 關閉、target exit、pipe 斷線、timeout、取消、重複 request、restore 中斷。
- security：非 localhost backend、未授權 profile、artifact path traversal、oversize frame、token redaction。
