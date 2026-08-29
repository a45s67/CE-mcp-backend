# CE Lua Bridge

`ce_mcp_bridge.lua` is the in-process Cheat Engine adapter. It intentionally
contains no MCP server, public network listener, authentication, workflow, host
shell, GUI automation, injection, or arbitrary Lua evaluation.

## Load

In Cheat Engine 7.5, open the Lua Engine and execute:

```lua
dofile([[C:\path\to\CE-mcp-backend\bridge\ce_mcp_bridge.lua]])
```

The script creates `\\.\pipe\CE_MCP_Backend_v1_<CE_PID>` using
`getCheatEngineProcessID()`. The sidecar discovers the only running CE instance
automatically, so ordinary users set no environment variable. If several CE
instances are running, select one with sidecar `--ce-pid <pid>`; ambiguity is
never resolved silently. `CE_MCP_PIPE_NAME` is a name-only integration-test
override. Reloading the script first destroys the previous worker and pipe.

## Current bridge methods

- `status.get`
- `process.list`
- `process.attach`
- `process.get`
- `process.detach`
- `memory.read` in raw mode
- asynchronous `scan.start|refine|results|close`
- `operations.get|list|cancel`
- bounded `pointer.resolve|validate`

Every CE API call executes through `thread.synchronize`; the worker thread only
performs blocking framed I/O. Frames use `uint32-le length + UTF-8 JSON` and are
limited to 8 MiB.

`process.detach` ends the MCP target session and invalidates its generation.
Cheat Engine 7.5 has no reliable Lua API for closing the process handle itself,
so the result explicitly returns `ceHandleRetained: true`. A later explicit
attach starts a new MCP session; no memory/debug operation is accepted while
logically detached.

Scan handles are bound to the target generation. At most one scan may execute
at once and at most eight handles may remain open. Results are returned in pages
of at most 200 entries. Cancel, close, detach, or target replacement destroys
the underlying CE `MemScan`/`FoundList` objects.

Initial scans, refinement, polling, paged results, cancel, and close are
operation-backed. The scan owner thread preserves the attached FoundList until
CE has completely finished `nextScan`, then atomically replaces it.

Exact plus unchanged/increased/decreased/changed refinement are enabled and
MCP-verified on CE 7.5 x64. Between/bigger/smaller refinement remain
fail-closed. Running-scan cancellation uses the probe-verified MemScan
destruction path.

The CE Lua `createPipe` API does not expose an explicit security descriptor. The
sidecar accepts only a local `\\.\pipe\...` name and the deployment account must
give Cheat Engine a restrictive Windows token/default DACL. Explicit pipe ACLs
remain a release gate for a native bridge variant or a verified CE API extension.
