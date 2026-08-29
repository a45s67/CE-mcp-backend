# Controlled DBVM watch and trace design

DBVM is an optional hypervisor capability, not a prerequisite for ordinary CE
MCP tools. Status may inspect already-exposed state queries but must never call
`dbk_initialize`, `dbvm_initialize`, change keys, offload the OS, or load a
driver.

The bridge distinguishes API presence from runtime readiness. When a CE build
exposes `dbk_initialized` / `dbvm_initialized`, a false result blocks start.
Some CE 7.5 builds omit both queries; there readiness is reported as
`unverified`, and start proceeds only through a read-only physical-address
translation gate. A nil/failed translation stops before watch/trace creation and
explains that DBK/DBVM may be disabled or the page unavailable. The bridge never
probes readiness by creating a watch and never initializes DBK or DBVM.

CE 7.7 build `7.7.0.10621` documents both queries and a clean-host runtime probe
confirmed that each exists and returns `false` without loading anything. Thus
CE 7.7 reports `not-ready` until the user enables DBK/DBVM, while older builds
without those APIs retain the guarded `unverified` compatibility path.

Watch/trace start requires all of:

- hypervisor profile selected in both sidecar policy and bridge authorization;
- DBK and DBVM not reported unloaded when state queries exist;
- current target generation and a resolvable virtual address;
- bridge-owned virtual-to-physical resolution;
- bounded byte range, internal event capacity, returned page size, and optional
  stack capture;
- registered cleanup before the start response is accepted.

The checked-in `ce.dbvm_watch` contract supports `status|start|events|stop`.
Watches are limited to eight concurrent handles, 1–8 bytes within one physical
page, and 1–1024 native log entries; result pages are capped at 200. Modes are
`write`, `access` (CE's read-and-write watch), and `execute`.

The checked-in `ce.dbvm_trace` contract supports
`status|start|results|archive_results|stop|remove`. CE exposes one global trace-on-breakpoint
resource, so the bridge permits one trace with at most 1024 steps. Start success
is confirmed through `dbvm_traceonbp_getstatus`, because CE's local Lua
documentation does not define a return value for `dbvm_traceonbp`.

`archive_results` is sidecar-owned: it reads every bounded result page, rejects
over-limit or non-progressing responses, and stores a deterministic JSON
snapshot in the private artifact store. The CE bridge never receives a host
path. Artifacts default to a seven-day/128-item retention policy, configurable
with `--artifact-retention-seconds` and `--max-artifacts`.

Watch and trace IDs remain opaque and generation-bound. Disconnect, target
replacement, logical detach, bridge reload, explicit stop, and cancellation must
disable/remove them. Raw physical addresses may be returned as metadata but are
never accepted for writes. Physical memory write, MSR/CR write, cloak,
change-register-on-breakpoint, global TSC changes, key changes, and implicit
initialization are outside the project scope.

## Dual local authorization

The sidecar defaults to the `debug` profile. `inspect` additionally rejects all
state-changing public tools. `hypervisor` must be selected with
`--policy-config`; its `bridgeAuthorizationToken` is loaded locally and never
appears in an MCP tool schema or tool arguments.

The CE bridge independently snapshots `_G.CE_MCP_POLICY` at startup. Every
`dbvm.*` bridge request is rejected before handler lookup unless that policy
explicitly enables hypervisor access and the sidecar's private profile/token
fields match. A policy change requires a bridge reload. Neither policy boundary
loads DBK or DBVM. Missing readiness functions are handled by the non-mutating
physical-translation gate; explicit false readiness still blocks start.

Use `policy.example.json` as the sidecar template. The separate
`bridge/00_ce_mcp_policy.example.lua` documents the CE-side setting. Ordinary
debug users need neither file.

Static and contract gates cover authorization, bounds, Lua 5.3 compilation,
generation ownership, and cleanup placement. A clean CE 7.5 host directly
returned trace status `(0,0,0)` without initialization and returned nil from
physical translation, stopping before watch creation. Positive watch/trace
runtime behavior therefore remains unproved. A later gate must use an explicitly
authorized, already-loaded DBVM host; selecting either policy is never
sufficient to load it.

## Agent-facing failure guidance

The MCP descriptions and structured DBVM errors explicitly tell the caller that
`CAPABILITY_UNAVAILABLE` or `CE_API_ERROR` may mean the user has not enabled or
loaded DBK/DBVM, or that DBVM became unavailable after a readiness check. The
caller should ask the user to confirm DBK/DBVM inside Cheat Engine and then call
`ce.status` before retrying. This is diagnostic guidance, not permission for the
backend to initialize either component.
