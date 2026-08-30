# CE MCP Backend

本倉庫定義 Cheat Engine MCP backend 的可實作規格。

- [ARCHITECTURE.md](ARCHITECTURE.md)：元件邊界、部署、安全模型與里程碑
- [REQUIREMENTS.md](REQUIREMENTS.md)：可追蹤的功能／非功能需求與 MVP 完成定義
- [CE_MCP_TOOLS.md](CE_MCP_TOOLS.md)：CE MCP 的核心工具、資料契約與狀態模型
- [ROADMAP.md](ROADMAP.md)：實作階段、交付物、驗收 gate 與完成順序
- [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md)：CE API 的 evidence-first 實作與 promotion gate

Repeatable real-CE scan gates include `ce-mcp-e2e-smoke`,
`ce-mcp-disconnect-smoke`, and `ce-mcp-relative-scan-smoke`. They are explicit
integration commands and are not run as part of the ordinary unit-test suite.
`ce-mcp-pointer-smoke` similarly verifies controlled two-level chain resolution
and batch validation against real CE.
`ce-mcp-artifact-smoke` verifies a multi-chunk memory dump, content hash,
cross-chunk preview, metadata, listing, and deletion against real CE.

Create/synchronize the locked project environment, then run the ordinary suite
without allowing an implicit lockfile update:

```powershell
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
```

Normal usage defaults to the `debug` capability profile and needs no policy
file or environment variable. An optional read-only sidecar can use a local
JSON file containing `{"profile":"inspect"}` with
`--policy-config <path>`. DBVM tools require a separately configured
`hypervisor` policy on both sidecar and CE bridge; see `DBVM_DESIGN.md`. Merely
selecting that profile never loads DBK or DBVM.

Positive DBK/DBVM watch/trace validation is currently deferred and tracked in
`TODO.md`. The experimental tools remain disabled by default; ordinary CE MCP
usage does not require DBK or DBVM.

The production server writes redacted, rotating JSONL audit metadata under
`%LOCALAPPDATA%\CE-MCP\audit` by default. Override the directory with
`--audit-root`; request arguments, memory content, HTTP credentials, and bridge
authorization tokens are never audit fields.

`ce-mcp-performance-smoke --target-pid <pid> --address <4KiB-readable-address>`
runs the repeatable status/read p95 release gate. `bridge/probes/debug_target.c`
can be cross-compiled as a cooperative x86 or x64 target for architecture and
latency gates.

Built distributions contain the production Lua bridge. After installing the
wheel, run `ce-mcp-install-bridge --ce-dir C:\path\to\Cheat Engine`; it writes
only `autorun\ce_mcp_bridge.lua` and refuses to replace an existing file unless
`--replace` is explicitly supplied.

## Quick start (Cheat Engine 7.7)

```powershell
uv sync --locked
uv run --locked ce-mcp-install-bridge --ce-dir "C:\tools\Cheat Engine"
```

Restart Cheat Engine once after installing or replacing the autorun bridge.
Normal stdio usage requires no environment variables, pipe name, policy file,
DBK, or DBVM. Configure an MCP client with:

```json
{
  "command": "C:\\path\\to\\CE-mcp-backend\\.venv\\Scripts\\ce-mcp-backend.exe",
  "args": ["--transport", "stdio"]
}
```

Use the absolute path produced by your own project environment; MCP clients do
not necessarily inherit an interactive shell's `PATH`.

### Codex plugin

This repository is also a validated Codex plugin. Its root [`.mcp.json`](.mcp.json)
uses the same self-contained pattern as IDA Pro MCP:

```text
uv run --locked ce-mcp-backend --transport stdio
```

Codex runs that command with the installed plugin root as `cwd`, so uv reads
the bundled `pyproject.toml` and `uv.lock`. A marketplace installation therefore
does not require a machine-specific `.venv` path or manual MCP JSON. `uv` must
be available on `PATH`; the first MCP startup may take longer while uv creates
the environment.

The plugin also ships the `cheat-engine-debugging` skill. Installing the Codex
plugin does not silently modify Cheat Engine: install the Lua bridge once with
`ce-mcp-install-bridge`, then restart CE. Install or refresh the published
plugin with:

```powershell
codex plugin marketplace add a45s67/codex-marketplace
codex plugin remove ce-mcp-backend@a45s67
codex plugin add ce-mcp-backend@a45s67
```

The server discovers the single running CE instance and verifies that the
named-pipe server PID is that CE process. If several CE instances are open, add
`"--ce-pid", "<pid>"` to `args`; discovery intentionally fails instead of
guessing. A normal agent workflow is `ce.status`, `ce.process list`, explicit
`ce.process attach`, generation-bound target tools, then `ce.process detach`.
Never retry an `OUTCOME_UNKNOWN` mutation automatically.

To verify the actual official-SDK stdio path against a disposable target:

```powershell
uv run --locked ce-mcp-live-smoke --target-pid <controlled-pid>
```

Use `--ce-pid <pid>` only when more than one CE instance is running. This smoke
performs MCP initialize/list-tools/status, process discovery, attach, module
memory read, disassembly, and detach in one client session.

### Stop, recovery, and uninstall

- Stop the MCP sidecar by closing the MCP client/session or terminating only
  its `ce-mcp-backend` child process. Pipe disconnect cleanup removes debugger
  and operation resources owned by that connection.
- If an attach mutation returns `OUTCOME_UNKNOWN`, do not retry it. Call
  `ce.status` to reconcile the observed CE target first.
- If the bridge is unavailable, confirm CE was restarted after installation and
  that exactly one CE instance is running, or configure `--ce-pid`.
- Emergency stop inside CE is `StopCEMCPBridge()` in the Lua Engine. Restarting
  CE loads the installed autorun bridge again.
- To uninstall, close the MCP client and remove only
  `<Cheat Engine>\autorun\ce_mcp_bridge.lua`, then restart CE. The installer
  never modifies the CE executable or other autorun files.

For localhost HTTP behind a remote Gateway, `CE_MCP_TOKEN` is a backend-only
secret of at least 32 characters. It must be different from the credential that
remote users present to the Gateway; never expose the backend port beyond
localhost.

設計依據：

- `C:\tools\CE` 的 Cheat Engine 7.5 安裝內容與 `celua.txt`
- `dynamic-analysis-mcp-survey/CE-MCP-Plugin`
- `dynamic-analysis-mcp-survey/cheatengine-mcp-bridge`
- survey 內的 Dynamic Analysis Gateway、開發指南與 repository review

DBVM experimental contracts are retained but disabled by default and tracked
in `TODO.md`; they are not required for this non-DBVM release.

## Development

Phase 0 contract tests 不需要外部套件：

```powershell
py -3 -m unittest discover -s tests -v
```

載入 [bridge/ce_mcp_bridge.lua](bridge/ce_mcp_bridge.lua) 後，可先用不經 MCP SDK 的本機 smoke CLI 驗證真實 pipe：

一般使用不需設定 pipe 或環境變數。Bridge pipe 自動包含 CE PID；只有同時
執行多個 CE 時使用 `--ce-pid <pid>` 明確選擇。`--pipe` 是測試／特殊部署
override。

```powershell
py -3 -m ce_mcp ce.status '{}' --deadline-ms 5000
py -3 -m ce_mcp ce.process '{"action":"list","limit":20}'
```

正式 MCP stdio server：

```powershell
.\.venv\Scripts\python.exe -m ce_mcp.mcp_server --transport stdio
```

localhost Streamable HTTP 必須提供至少 32 字元的 backend token：

```powershell
$env:CE_MCP_TOKEN = '<random backend-only token>'
.\.venv\Scripts\python.exe -m ce_mcp.mcp_server --transport streamable-http --host 127.0.0.1 --port 8001
```

真實 CE vertical-slice smoke（會 attach、驗證 module `MZ`、map、symbol、disassembly，最後 detach）：

```powershell
.\.venv\Scripts\python.exe -m ce_mcp.e2e_smoke --target-name Tutorial-x86_64.exe
```

加上 `--scan` 會額外執行非同步、bounded 的 AOB scan，輪詢 operation、驗證結果並 close handle。

`--cancel-scan` 驗證 running scan cancellation；`python -m
ce_mcp.disconnect_smoke --target-pid <pid>` 驗證 pipe disconnect cleanup。

目前程式結構：

- `ce_mcp/`：共用 protocol models、service facade、framing、transport 與 fake bridge。
- `ce_mcp/contracts/v1/`：隨 wheel 安裝的版本化 bridge 與 MCP tool JSON Schemas。
- `tests/`：離線 contract、framing、state invariant tests。
