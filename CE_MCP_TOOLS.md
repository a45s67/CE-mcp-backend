# CE MCP Tool Contract

狀態：MVP public surface 提案

## 1. 設計原則

現有 Lua/Python bridge 的約 175 個 atomic tools 證明 CE 覆蓋率可達成，但會增加模型選擇成本、schema 重複與維護負擔。本設計把 public surface 壓到 domain tools；backend 內部仍可拆成細粒度 bridge method。

Gateway 公開名稱為 `ce.<name>`；CE backend 獨立使用時 downstream name 為 `<name>`。

## 2. Common response

每個結果包含：

```json
{
  "session": {
    "sessionId": "ce-01J...",
    "generation": 7,
    "state": "paused",
    "pid": 4242,
    "architecture": "x86_64",
    "pointerWidth": 64
  },
  "data": {}
}
```

沒有 attach 時 `session` 可為 `null`。address object：

```json
{
  "expression": "sample.exe+0x1234",
  "address": "0x00007FF612341234",
  "module": "sample.exe",
  "rva": "0x1234"
}
```

## 3. MVP tools

### `ce.status`

回報 backend、bridge、CE、target、DBK/DBVM 與 capability profile；不得觸發 DBVM 載入。

Input：`{}`。

### `ce.process`

`action`: `list|attach|detach|launch|get`

主要參數：`pid`、`name`、`path`、`args[]`、`breakOnEntry`、`expectedGeneration`、pagination/filter。`name` 多重符合時不可猜測，回 candidates。

風險：list/get 唯讀；attach/launch 為 execution control。

### `ce.memory_read`

支援兩種模式：

- raw：`address`、`size`、`encoding=hex|base64`；
- typed：`address`、`dataType=u8|i8|u16|i16|u32|i32|u64|i64|f32|f64|pointer|string|wstring`、`count`。

可選 `followPointers` offsets，但最大 32 層。回每一層 resolved address；partial read 明確列出 unreadable ranges。

### `ce.memory_write`

輸入 raw bytes 或 typed value，必須帶 `expectedGeneration` 與 approval。支援 `verify:true`；回 before/after hash 及 written ranges。單次最大 64 KiB。

### `ce.memory_map`

分頁列出 region，支援 module/protection/state/type filter。回 base、size、protection、module mapping。

### `ce.memory_manage`

`action`: `allocate|free|protect|query_protection`

free/protect 必須限制在本 session 建立或明確核准的範圍；回 backend-owned allocation handle，cleanup policy 為 `on_detach|manual`。

### `ce.scan`

`action`: `start|refine|results|cancel|close`

支援 exact/unknown/increased/decreased/changed/unchanged/between、typed value 與 AOB。start/refine 回 operation/scan ID；results 分頁。scan 綁定 session generation，target restart 後失效。

### `ce.pointer`

`action`: `resolve|validate`

`resolve` 與 `validate` 使用 CE target-width pointer read。單層 pointer holder
搜尋組合既有 bounded exact scan。CE 7.5 Lua 沒有可讀取結果且可取消的
pointer-scanner API，因此 native `scan_start|rescan|results` 不得用 GUI side
effect 假裝成功。validate 可批次檢查多條 chain，但不得跨 generation 宣稱
穩定；跨重啟驗證由 agent/workflow 明確執行。

### `ce.disassembly`

`action`: `list|instruction|function|previous|next`

輸入 address 與 bounded count/byte range；輸出 address、bytes、mnemonic、operands、branch target、symbol。function analysis 可回 artifact。

### `ce.assembly`

`action`: `assemble|auto_assemble_preview|auto_assemble_apply|disable_patch`

preview 必須先解析 allocation、symbol、write ranges 與可逆性；apply 需要 approval 並回 `patchId`。disable 只能操作 backend 所建立且仍符合 generation 的 patch。

### `ce.symbols`

`action`: `resolve|describe|modules|list|register|unregister|reload`

symbols/modules/list 分頁；Windows symbol download 是長 operation，server 設定 symbol cache allowlist。

### `ce.debug_control`

`action`: `start|pause|continue|step_into|step_over|run_until|stop|detach`

回 stop reason、thread、IP 與新的 generation。所有 action 走 serialized executor。continue 類 action 可帶 `expectedStopGeneration`。

### `ce.registers`

`action`: `get|set`

get 支援 general/flags/segment/vector detail level；set 只允許 paused state，需 approval 與 `expectedStopGeneration`。

### `ce.breakpoints`

`action`: `list|set|remove|enable|disable|hits`

類型為 software/hardware execute/read/write/access。回 backend breakpoint ID；hardware slot exhaustion 回結構化錯誤。hits 使用 cursor 與 stop sequence，不能只回自由文字。

### `ce.trace`

`action`: `start|status|cancel|results`

設定 start/stop condition、maxSteps、timeout、register/memory capture policy。預設不擷取 stack bytes；大結果為 artifact。

### `ce.analysis`

`action`: `address_info|references|call_references|structure|rtti|pointer_access`

純分析盡量在 sidecar 執行，避免佔用 CE main thread。references/structure 結果分頁；pointer_access 接受 hit 的 instruction/register snapshot，產生 pointer walk-back facts。

### `ce.artifacts`

`action`: `memory_dump|list|get_metadata|delete`

memory dump 僅能讀 target memory 到 backend artifact store，不接受任意 host filename。download 由 MCP resource 或 gateway artifact endpoint 完成。

### `ce.operations`

`action`: `get|list|cancel`

統一管理 scan、pointer scan、trace、dump 與 symbol load。

## 4. 選配 tools

### `ce.cheat_table`

`action`: `load|save|entries|create|update|delete|freeze|unfreeze`

只允許受控 artifact/path；script entry 的啟用等同 code injection，依 `modify`/`inject` policy 審核。

### `ce.inject`

`action`: `dll|dotnet|code|method`

預設關閉。檔案必須來自已掃描且 hash 固定的 artifact；禁止 client 傳任意 host path。

### `ce.dbvm_watch`

`action`: `start|status|events|stop`

輸入 virtual address，由 bridge 解析 physical address；mode 為 read/write/execute，含 bounded buffer、event limit、stack capture opt-in。回 watch ID，disconnect/detach 時 cleanup。

### `ce.dbvm_trace`

`action`: `start|status|results|stop|remove`

包裝 `dbvm_traceonbp_*`，綁定 physical/virtual address 與 generation。結果必須標註 register context 的 architecture。

## 5. 明確不進 MVP

- 任意 Lua execution、host shell、CE GUI automation、鍵鼠輸入。
- DBVM key 設定、MSR/CR write、global TSC speedhack、CPUID logging。
- DBVM cloak/change-register-on-breakpoint、physical memory write。
- 任意 DLL/path、任意網路請求或不受限檔案讀寫。

這些能力未來若加入，應各自是明確、預設關閉的 tool，而不是藏在 `execute` 或 `lua_eval` escape hatch。

## 6. Capability discovery

`ce.status` 回傳機器可用與 policy 允許的交集：

```json
{
  "available": ["memory.read", "debug.hardware_breakpoint", "dbvm.watch"],
  "enabled": ["memory.read", "debug.hardware_breakpoint"],
  "disabledReasons": {"dbvm.watch": "profile hypervisor is disabled"},
  "limits": {"maxReadBytes": 1048576, "maxScanResultsPage": 200}
}
```

tool catalog 可依 profile 隱藏整個選配 tool，但同一 profile 內名稱與 schema 必須穩定。
