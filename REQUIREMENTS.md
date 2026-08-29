# CE MCP 需求規格

狀態：MVP baseline。`MUST` 是發布門檻，`SHOULD` 可在有書面理由時延後。

## 1. 使用情境

- **UC-01 記憶體探索**：agent attach 程序、讀 memory map、執行 initial/refine scan、檢視結果。
- **UC-02 程式行為定位**：反組譯、設定 breakpoint/watch、讀 event/register、追蹤 references。
- **UC-03 可復原修改**：preview patch、核准後套用、驗證、以 patch ID 還原。
- **UC-04 Pointer 穩定化**：擷取 access、逐層 walk-back、pointer scan/rescan、跨重啟驗證。
- **UC-05 DBVM 觀測**：在 capability 開啟時做 read/write/execute watch 與 bounded trace。

## 2. 功能需求

| ID | 等級 | 需求 | 驗證證據 |
|---|---|---|---|
| CE-F-001 | MUST | backend 提供 status、process attach/detach、session ID 與 generation | contract + integration |
| CE-F-002 | MUST | 支援 raw/typed memory read、canonical address 與 partial-read reporting | x86/x64 integration |
| CE-F-003 | MUST | 支援 memory map 與 cursor pagination | contract |
| CE-F-004 | MUST | 支援 initial/refine scan、results、cancel、cleanup | CE tutorial integration |
| CE-F-005 | MUST | 支援 bounded disassembly 與 symbol/module resolution | integration |
| CE-F-006 | MUST | 支援 pause/continue/step、register read 與 breakpoint event | integration |
| CE-F-007 | MUST | 所有 mutation 驗證 expected generation；stale request 不得執行 | race/robustness test |
| CE-F-008 | MUST | write/allocate/protect/patch 預設不屬於 inspect profile | authorization test |
| CE-F-009 | SHOULD | pointer resolve/scan/rescan/validate 使用 operation 模型 | long-operation test |
| CE-F-010 | SHOULD | DBVM watch 可 start/events/stop，且 disconnect 時 cleanup | DBVM-capable host test |
| CE-F-011 | MUST | 大型 scan/trace/dump 結果轉成 immutable artifact | size-limit test |
| CE-F-012 | MUST | bridge 回報 API/capability，不因 status 自動載入 DBK/DBVM | clean-host test |

## 3. MCP 與資料契約需求

| ID | 等級 | 需求 |
|---|---|---|
| API-001 | MUST | 遠端 Gateway 使用 HTTPS Streamable HTTP；backend 預設只 bind localhost |
| API-002 | MUST | tool 名稱、input/output JSON Schema 與 error code 在同一 major version 內穩定 |
| API-003 | MUST | 所有 64-bit address、handle-like opaque ID 不使用 JSON floating number |
| API-004 | MUST | list 結果 bounded、可分頁並明示 truncation |
| API-005 | MUST | 長工作回 operation ID，可查 status/result；支援時可 cancel |
| API-006 | MUST | mutation transport failure 若結果不明，回 `OUTCOME_UNKNOWN/safeToRetry:false` |
| API-007 | MUST | tools/list deterministic；profile/capability 變更依 MCP 通知更新 catalog |
| API-008 | SHOULD | mutation 支援 idempotency key 與短期 completed-request cache |

## 4. 安全需求

| ID | 等級 | 需求 |
|---|---|---|
| SEC-001 | MUST | Gateway 與 CE backend 使用不同且可撤銷的 credential |
| SEC-002 | MUST | CE named pipe 具 Windows ACL；frame 有大小上限、deadline 與 schema validation |
| SEC-003 | MUST | capability profile 在 backend/bridge 邊界執行，不能只靠 MCP tool 是否顯示 |
| SEC-004 | MUST | memory mutation、injection、remote execution 與 physical memory write 需 scoped approval |
| SEC-005 | MUST | 任意 Lua、host shell、host path 與 DLL path 預設不可用 |
| SEC-006 | MUST | path canonicalization 阻擋 traversal、UNC/device path、ADS、reparse escape |
| SEC-007 | MUST | audit 記錄 mutation，但 redaction token、memory body、sample secrets 與未受限 stdout |
| SEC-008 | MUST | DBVM physical write、MSR/CR write、cloak 與 global TSC 操作不進 MVP |

## 5. 非功能需求

| ID | 等級 | 指標 |
|---|---|---|
| NFR-001 | MUST | 同機 `status` p95 < 200 ms；4 KiB read p95 < 500 ms（不含 cold symbol load） |
| NFR-002 | MUST | inline output 預設上限 256 KiB；read 單次最大 1 MiB；list page 最大 200 |
| NFR-003 | MUST | CE mutation serialized；長 operation 的 concurrency 有明確上限 |
| NFR-004 | MUST | CE/backend/bridge 中斷後可重連；舊 handle 不得復活 |
| NFR-005 | MUST | request、operation、artifact 均有 retention 與 deterministic cleanup |
| NFR-006 | MUST | Windows 11 x64 + CE 7.5 x64 驗證 x64 與 x86 target |
| NFR-007 | SHOULD | backend 服務可獨立升級，不要求更換 CE binary；bridge protocol version negotiated |
| NFR-008 | MUST | log/metrics 有 request correlation、duration、error code，不包含 credential |

## 6. MVP 範圍

### 必須完成

- CE M1 唯讀能力及 M2 的 debug control/breakpoint/register read。
- operation、artifact、session/generation、structured errors。
- inspect/debug profiles、scoped approvals、audit 與所有 MUST security tests。

### 可延後

- pointer scan、structure inference、完整 trace 與 DBVM watch。
- cheat table、auto-assemble apply、DLL/.NET/code injection。
- 多 CE instance、語意統一的跨 debugger tools。

### 不屬於範圍

- 規避 EDR/anti-cheat 或 kernel patch automation。
- 公開 CE backend 到不受信任網路。
- 任意 host command execution 或 GUI/鍵鼠 automation。

## 7. Definition of Done

MVP 只有在以下全部成立時完成：

1. JSON Schema 與 error fixtures 通過 contract suite。
2. CE tutorial x86/x64 完成 attach → scan → breakpoint → event → detach，所有 handle 清除。
3. target restart、target exit、CE crash、pipe disconnect、timeout/cancel、oversize input 均有 deterministic 結果。
4. threat-model 測試證明未授權 mutation、artifact path escape 與 token leakage 被阻擋。
5. 安裝、設定、最小權限、復原與 emergency stop 文件可在全新 Windows 環境重現。
