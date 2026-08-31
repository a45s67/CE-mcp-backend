"""Framework-neutral MCP tool service facade.

The facade is the policy and contract boundary.  A future MCP SDK adapter only
needs to expose ``catalog`` and forward calls to ``call_tool``; CE-specific work
stays behind the bridge interface.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from threading import Lock
from time import monotonic, sleep
from typing import Any, Mapping, Protocol
from uuid import uuid4

from . import __version__
from .artifacts import ArtifactStore, ArtifactStoreError
from .audit import AuditLogError
from .catalog import load_catalog
from .models import ContractViolation, ErrorDetail, NextAction, Session
from .protocol import BridgeRequest, BridgeResponse, BridgeTransportError
from .policy import Policy
from .schema import validate
from .structures import StructureWorkspace, StructureWorkspaceError
from .server_config import (
    DEFAULT_MAX_OUTPUT_BYTES,
    HARD_MAX_OUTPUT_BYTES,
    MIN_MAX_OUTPUT_BYTES,
)


class BridgeClient(Protocol):
    def call(self, request: BridgeRequest) -> BridgeResponse: ...


class AuditSink(Protocol):
    def record(self, event: Mapping[str, Any]) -> None: ...


@dataclass(frozen=True)
class ToolOutcome:
    result: Mapping[str, Any] | None = None
    error: ErrorDetail | None = None

    def __post_init__(self) -> None:
        if (self.result is None) == (self.error is None):
            raise ContractViolation("tool outcome requires exactly one result or error")

    def to_dict(self) -> dict[str, Any]:
        if self.result is not None:
            return {"result": dict(self.result)}
        assert self.error is not None
        return {"error": self.error.to_dict()}


class BackendService:
    """Validate, authorize, route, and normalize public CE tool calls."""

    _METHODS = {
        "ce.status": {"status": "status.get"},
        "ce.process": {
            "list": "process.list",
            "attach": "process.attach",
            "detach": "process.detach",
            "get": "process.get",
        },
        "ce.memory_read": {"read": "memory.read"},
        "ce.memory_map": {"map": "memory.map"},
        "ce.memory_analysis": {"compare": "memory.compare", "checksum": "memory.checksum"},
        "ce.disassembly": {
            "list": "disassembly.list",
            "instruction": "disassembly.instruction",
            "function": "disassembly.function",
            "previous": "disassembly.previous",
            "next": "disassembly.next",
        },
        "ce.symbols": {
            "resolve": "symbols.resolve",
            "describe": "symbols.describe",
            "modules": "symbols.modules",
            "list": "symbols.list",
        },
        "ce.scan": {
            "start": "scan.start",
            "refine": "scan.refine",
            "results": "scan.results",
            "close": "scan.close",
        },
        "ce.operations": {
            "get": "operations.get",
            "list": "operations.list",
            "cancel": "operations.cancel",
        },
        "ce.artifacts": {
            "memory_dump": "sidecar.artifacts.memory_dump",
            "list": "sidecar.artifacts.list",
            "get_metadata": "sidecar.artifacts.get_metadata",
            "preview": "sidecar.artifacts.preview",
            "delete": "sidecar.artifacts.delete",
        },
        "ce.pointer": {
            "resolve": "pointer.resolve",
            "validate": "pointer.validate",
        },
        "ce.debug_control": {
            "status": "debug.control.status", "start": "debug.control.start",
            "pause": "debug.control.pause",
            "continue": "debug.control.continue", "detach": "debug.control.detach",
        },
        "ce.breakpoints": {
            "list": "debug.breakpoints.list", "set": "debug.breakpoints.set",
            "remove": "debug.breakpoints.remove",
        },
        "ce.debug_events": {"list": "debug.events.list"},
        "ce.threads": {"list": "threads.list"},
        "ce.registers": {"read": "debug.registers.read"},
        "ce.signature": {"start": "signature.start", "result": "signature.result", "close": "signature.close"},
        "ce.structures": {"read": "structures.read"},
        "ce.dbvm_watch": {
            "status": "dbvm.watch.status", "start": "dbvm.watch.start",
            "events": "dbvm.watch.events", "stop": "dbvm.watch.stop",
        },
        "ce.dbvm_trace": {
            "status": "dbvm.trace.status", "start": "dbvm.trace.start",
            "results": "dbvm.trace.results", "stop": "dbvm.trace.stop",
            "remove": "dbvm.trace.remove", "archive_results": "dbvm.trace.results",
        },
    }
    _PROCESS_MUTATIONS = {"attach", "detach"}
    _MUTATING_ACTIONS = {
        "ce.process": {"attach", "detach"},
        "ce.scan": {"start", "refine", "close"},
        "ce.operations": {"cancel"},
        "ce.debug_control": {"start", "pause", "continue", "detach"},
        "ce.breakpoints": {"set", "remove"},
        "ce.signature": {"start", "close"},
        "ce.dbvm_watch": {"start", "stop"},
        "ce.dbvm_trace": {"start", "stop", "remove", "archive_results"},
    }

    def __init__(
        self,
        bridge: BridgeClient,
        contract_dir: Path,
        *,
        backend_version: str = __version__,
        request_deadline_ms: int = 5_000,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        artifact_store: ArtifactStore | None = None,
        structure_workspace: StructureWorkspace | None = None,
        policy: Policy | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._bridge = bridge
        self._catalog = load_catalog(contract_dir)
        self._tools = {tool["name"]: tool for tool in self._catalog}
        self._backend_version = backend_version
        if not 1 <= request_deadline_ms <= 300_000:
            raise ValueError("request_deadline_ms must be between 1 and 300000")
        self._request_deadline_ms = request_deadline_ms
        if not MIN_MAX_OUTPUT_BYTES <= max_output_bytes <= HARD_MAX_OUTPUT_BYTES:
            raise ValueError(
                f"max_output_bytes must be between {MIN_MAX_OUTPUT_BYTES} and "
                f"{HARD_MAX_OUTPUT_BYTES}"
            )
        self._max_output_bytes = max_output_bytes
        self._artifact_store = artifact_store
        self._structure_workspace = structure_workspace or StructureWorkspace()
        self._policy = policy or Policy()
        self._audit_sink = audit_sink
        self._session: Session | None = None
        self._call_lock = Lock()

    @property
    def catalog(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._catalog)

    @property
    def session(self) -> Session | None:
        return self._session

    @property
    def max_output_bytes(self) -> int:
        return self._max_output_bytes

    def is_mutation(self, name: str, arguments: Mapping[str, Any]) -> bool:
        action = arguments.get("action")
        return isinstance(action, str) and action in self._MUTATING_ACTIONS.get(name, set())

    def validate_next_action(
        self,
        action: NextAction,
        *,
        current_tool: str,
        current_arguments: Mapping[str, Any],
    ) -> None:
        if action.tool is None:
            return
        definition = self._tools.get(action.tool)
        if definition is None:
            raise ContractViolation("next action references an unknown tool")
        if action.arguments is not None:
            candidate = dict(action.arguments)
        elif action.arguments_patch is not None:
            if action.tool != current_tool:
                raise ContractViolation("argument patches may only target the current tool")
            candidate = dict(current_arguments)
            candidate.update(action.arguments_patch)
        else:
            candidate = {}
        validate(definition["inputSchema"], candidate)

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> ToolOutcome:
        # State preflight, bridge execution, output validation, and session update
        # form one transaction. The bridge's own I/O lock alone would still allow
        # two callers to preflight against the same stale generation.
        request_id = f"req-{uuid4().hex}"
        action_value = arguments.get("action") if isinstance(arguments, Mapping) else None
        action = action_value[:64] if isinstance(action_value, str) else None
        mutation = action in self._MUTATING_ACTIONS.get(name, set())
        started = monotonic()
        if self._audit_sink is not None:
            try:
                self._audit_sink.record({
                    "phase": "accepted", "requestId": request_id,
                    "tool": name[:128], "action": action, "mutation": mutation,
                })
            except (AuditLogError, OSError):
                return self._error(
                    "AUDIT_UNAVAILABLE", "Security audit log is unavailable",
                    recoverable=True, safe_to_retry=True,
                )
        with self._call_lock:
            outcome = self._call_tool_locked(name, arguments, request_id)
        if self._audit_sink is not None:
            event: dict[str, Any] = {
                "phase": "completed", "requestId": request_id, "tool": name[:128],
                "action": action, "mutation": mutation,
                "durationMs": round((monotonic() - started) * 1000, 3),
                "outcome": "success" if outcome.error is None else "error",
            }
            if outcome.error is not None:
                event["errorCode"] = outcome.error.code
            if self._session is not None:
                event["sessionId"] = self._session.session_id
                event["generation"] = self._session.generation
            try:
                self._audit_sink.record(event)
            except (AuditLogError, OSError):
                # The accepted record was durably flushed before any mutation;
                # never replace an already-observed mutation result here.
                pass
        return outcome

    def _call_tool_locked(self, name: str, arguments: Mapping[str, Any], request_id: str) -> ToolOutcome:
        tool = self._tools.get(name)
        if tool is None:
            return self._error(
                "METHOD_NOT_FOUND",
                f"MCP tool is not registered: {name}",
                recoverable=False,
                safe_to_retry=True,
            )
        try:
            validate(tool["inputSchema"], arguments)
        except ContractViolation as exc:
            return self._error(
                "INVALID_PARAMS", str(exc), recoverable=True, safe_to_retry=True
            )

        preflight = self._preflight(name, arguments)
        if preflight is not None:
            return ToolOutcome(error=preflight)

        if name == "ce.artifacts":
            return self._call_artifact(tool, arguments)
        if name == "ce.structures":
            return self._call_structure(tool, arguments)
        if name == "ce.dbvm_trace" and arguments["action"] == "archive_results":
            return self._call_dbvm_trace_archive(tool, arguments)

        method = self._bridge_method(name, arguments)
        bridge_params = dict(arguments)
        if name.startswith("ce.dbvm_"):
            bridge_params.update(self._policy.private_bridge_params())
        request = BridgeRequest(
            request_id=request_id,
            session_id=self._session.session_id if self._session else None,
            method=method,
            params=bridge_params,
            deadline_ms=self._request_deadline_ms,
        )
        try:
            response = self._bridge.call(request)
        except ContractViolation as exc:
            return self._error(
                "BACKEND_CONTRACT_VIOLATION",
                str(exc),
                recoverable=False,
                safe_to_retry=False,
            )
        except (BridgeTransportError, ConnectionError, EOFError, OSError):
            reconciled = self._reconcile_process_mutation(name, arguments)
            if reconciled is not None:
                return reconciled
            return self._transport_failure(name, arguments)

        if response.error is not None:
            return ToolOutcome(error=response.error)
        assert response.result is not None
        if (
            name == "ce.process"
            and arguments.get("action") in {"attach", "detach"}
            and response.result.get("pending") is True
        ):
            reconciled = self._reconcile_process_mutation(name, arguments)
            if reconciled is not None:
                return reconciled
            return self._error(
                "OUTCOME_UNKNOWN",
                "CE accepted the process mutation but its final state was not observed",
                recoverable=True,
                safe_to_retry=False,
                suggested_action="Call ce.status and reconcile target state before another mutation.",
                next_actions=(NextAction(
                    "REFRESH_STATUS", "required_before_retry",
                    "Observe current target state without repeating the mutation.",
                    tool="ce.status", arguments={},
                ),),
            )
        try:
            result = self._normalize(name, arguments, response.result)
            validate(tool["outputSchema"], result)
            self._update_session(name, arguments, result)
        except ContractViolation as exc:
            return self._error(
                "BACKEND_CONTRACT_VIOLATION",
                str(exc),
                recoverable=False,
                safe_to_retry=False,
            )
        return ToolOutcome(result=result)

    def _call_dbvm_trace_archive(
        self, tool: Mapping[str, Any], arguments: Mapping[str, Any]
    ) -> ToolOutcome:
        store = self._artifact_store
        if store is None:
            return self._error(
                "CAPABILITY_UNAVAILABLE", "Artifact store is not configured",
                recoverable=True, safe_to_retry=True,
            )
        assert self._session is not None
        trace_id = str(arguments["traceId"])
        cursor: str | None = None
        items: list[Mapping[str, Any]] = []
        trace: Mapping[str, Any] | None = None
        deadline = monotonic() + self._request_deadline_ms / 1000.0
        try:
            while True:
                if monotonic() >= deadline:
                    return self._error(
                        "TIMEOUT", "DBVM trace archival exceeded the request deadline",
                        recoverable=True, safe_to_retry=True,
                    )
                params: dict[str, Any] = {
                    "action": "results", "traceId": trace_id, "limit": 200,
                    "expectedGeneration": self._session.generation,
                    **self._policy.private_bridge_params(),
                }
                if cursor is not None:
                    params["cursor"] = cursor
                response = self._bridge.call(BridgeRequest(
                    request_id=f"req-{uuid4().hex}", session_id=self._session.session_id,
                    method="dbvm.trace.results", params=params,
                    deadline_ms=max(1, int((deadline - monotonic()) * 1000)),
                ))
                if response.error is not None:
                    return ToolOutcome(error=response.error)
                assert response.result is not None
                page = response.result.get("items")
                observed_trace = response.result.get("trace")
                if not isinstance(page, list) or any(not isinstance(item, Mapping) for item in page):
                    raise ArtifactStoreError("bridge returned malformed DBVM trace items")
                if not isinstance(observed_trace, Mapping):
                    raise ArtifactStoreError("bridge returned malformed DBVM trace metadata")
                if trace is None:
                    trace = dict(observed_trace)
                items.extend(dict(item) for item in page)
                if len(items) > 1024:
                    raise ArtifactStoreError("bridge exceeded the bounded DBVM trace size")
                if response.result.get("truncated") is not True:
                    break
                next_cursor = response.result.get("nextCursor")
                if not isinstance(next_cursor, str) or next_cursor == cursor:
                    raise ArtifactStoreError("bridge returned an invalid DBVM trace cursor")
                cursor = next_cursor

            document = {
                "format": "ce-mcp-dbvm-trace-v1", "trace": trace,
                "itemCount": len(items), "items": items,
            }
            encoded = json.dumps(
                document, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
            ).encode("utf-8")
            artifact = store.create(
                [encoded], kind="dbvm-trace", media_type="application/json",
                session_id=self._session.session_id, generation=self._session.generation,
                source={"traceId": trace_id, "itemCount": len(items)},
            )
            result = self._normalize("ce.dbvm_trace", arguments, {
                "session": self._session.to_dict(), "trace": trace,
                "artifact": artifact, "itemCount": len(items),
            })
            validate(tool["outputSchema"], result)
            return ToolOutcome(result=result)
        except (ArtifactStoreError, ContractViolation) as exc:
            return self._error(
                "ARTIFACT_ERROR", str(exc), recoverable=True, safe_to_retry=True,
            )
        except (BridgeTransportError, ConnectionError, EOFError, OSError):
            return self._error(
                "BRIDGE_UNAVAILABLE", "DBVM trace archival was interrupted",
                recoverable=True, safe_to_retry=True,
            )

    def _bridge_method(self, name: str, arguments: Mapping[str, Any]) -> str:
        if name == "ce.status":
            return self._METHODS[name]["status"]
        if name in {"ce.memory_read", "ce.memory_map"}:
            key = "read" if name == "ce.memory_read" else "map"
            return self._METHODS[name][key]
        return self._METHODS[name][arguments["action"]]

    def _preflight(
        self, name: str, arguments: Mapping[str, Any]
    ) -> ErrorDetail | None:
        action = arguments.get("action")
        if self._policy.profile == "inspect" and action in self._MUTATING_ACTIONS.get(name, set()):
            return ErrorDetail(
                code="PROFILE_DISABLED",
                message="The inspect profile does not permit state-changing tools",
                recoverable=True,
                safe_to_retry=True,
                suggested_action="Select the debug profile in the local sidecar policy config",
            )
        if name.startswith("ce.dbvm_") and self._policy.profile != "hypervisor":
            return ErrorDetail(
                code="PROFILE_DISABLED",
                message="DBVM tools require the hypervisor profile",
                recoverable=True,
                safe_to_retry=True,
                suggested_action="Configure matching sidecar and CE bridge hypervisor policies",
            )
        needs_target = name in {
            "ce.memory_read",
            "ce.memory_map",
            "ce.memory_analysis",
            "ce.disassembly",
            "ce.symbols",
            "ce.scan",
            "ce.operations",
            "ce.pointer",
            "ce.debug_control",
            "ce.breakpoints",
            "ce.debug_events",
            "ce.threads",
            "ce.registers",
            "ce.signature",
            "ce.dbvm_watch",
            "ce.dbvm_trace",
        } or (name == "ce.structures" and arguments.get("action") == "read") or (name == "ce.artifacts" and arguments.get("action") == "memory_dump") or (
            name == "ce.process" and arguments.get("action") in {"detach", "get"}
        )
        if needs_target and self._session is None:
            return ErrorDetail(
                code="NO_TARGET",
                message="No target process is attached",
                recoverable=True,
                safe_to_retry=True,
                suggested_action="ce.process(action='list') then ce.process(action='attach')",
            )
        expected = arguments.get("expectedGeneration")
        if expected is not None and self._session is not None:
            if expected != self._session.generation:
                return ErrorDetail(
                    code="STALE_SESSION",
                    message="Target session generation does not match the observed generation",
                    recoverable=True,
                    safe_to_retry=name != "ce.process",
                    current_state=self._session.state.value,
                    suggested_action="Call ce.status and re-resolve target addresses",
                    next_actions=(NextAction(
                        "REFRESH_STATUS", "required_before_retry",
                        "Obtain the current session generation before continuing.",
                        tool="ce.status", arguments={},
                    ),),
                    details={
                        "expectedGeneration": expected,
                        "actualGeneration": self._session.generation,
                    },
                )
        return None

    def _normalize(
        self,
        name: str,
        arguments: Mapping[str, Any],
        bridge_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = dict(bridge_result)
        if name == "ce.status":
            result = {
                "backend": {
                    "version": self._backend_version,
                    "protocolVersion": 1,
                },
                **result,
            }
            capabilities = result.get("capabilities")
            if isinstance(capabilities, Mapping):
                normalized_capabilities = dict(capabilities)
                available = list(normalized_capabilities.get("available", []))
                enabled = list(normalized_capabilities.get("enabled", []))
                disabled = dict(normalized_capabilities.get("disabledReasons", {}))
                limits = dict(normalized_capabilities.get("limits", {}))
                limits["maxOutputBytes"] = self._max_output_bytes
                for capability in ("artifacts.memory_dump", "artifacts.metadata", "artifacts.preview"):
                    if capability not in available:
                        available.append(capability)
                    if self._artifact_store is not None:
                        if capability not in enabled:
                            enabled.append(capability)
                    else:
                        disabled[capability] = "artifact store is not configured"
                if self._artifact_store is not None:
                    limits["maxArtifactBytes"] = self._artifact_store.max_artifact_bytes
                    limits["maxArtifacts"] = self._artifact_store.max_artifacts
                    limits["artifactRetentionSeconds"] = self._artifact_store.retention_seconds
                for capability in tuple(available):
                    if str(capability).startswith("dbvm.") and self._policy.profile != "hypervisor":
                        if capability in enabled:
                            enabled.remove(capability)
                        disabled[capability] = "sidecar hypervisor profile is disabled"
                normalized_capabilities.update(
                    available=available, enabled=enabled,
                    disabledReasons=disabled, limits=limits,
                )
                result["capabilities"] = normalized_capabilities
        elif name in {
            "ce.process", "ce.disassembly", "ce.symbols", "ce.scan", "ce.operations",
            "ce.pointer", "ce.artifacts", "ce.debug_control", "ce.breakpoints",
            "ce.debug_events", "ce.threads", "ce.registers", "ce.memory_analysis", "ce.signature",
            "ce.structures",
            "ce.dbvm_watch", "ce.dbvm_trace",
        }:
            result = {"action": arguments["action"], **result}
        return result

    def _call_artifact(
        self, tool: Mapping[str, Any], arguments: Mapping[str, Any]
    ) -> ToolOutcome:
        store = self._artifact_store
        if store is None:
            return self._error(
                "CAPABILITY_UNAVAILABLE", "Artifact store is not configured",
                recoverable=True, safe_to_retry=True,
            )
        action = arguments["action"]
        try:
            if action == "memory_dump":
                assert self._session is not None
                requested = int(arguments["size"])
                remaining = requested
                chunks: list[bytes] = []
                resolved_base: int | None = None
                deadline = monotonic() + self._request_deadline_ms / 1000.0
                while remaining:
                    if monotonic() >= deadline:
                        return self._error(
                            "TIMEOUT", "Memory dump exceeded the request deadline",
                            recoverable=True, safe_to_retry=True,
                        )
                    offset = requested - remaining
                    address = arguments["address"] if resolved_base is None else f"0x{resolved_base + offset:X}"
                    chunk_size = min(remaining, 256 * 1024)
                    request = BridgeRequest(
                        request_id=f"req-{uuid4().hex}", session_id=self._session.session_id,
                        method="memory.read",
                        params={
                            "mode": "raw", "address": address, "size": chunk_size,
                            "expectedGeneration": self._session.generation,
                        },
                        deadline_ms=max(1, int((deadline - monotonic()) * 1000)),
                    )
                    try:
                        response = self._bridge.call(request)
                    except (ContractViolation, BridgeTransportError, ConnectionError, EOFError, OSError):
                        return self._error(
                            "BRIDGE_UNAVAILABLE", "Memory dump read was interrupted",
                            recoverable=True, safe_to_retry=True,
                        )
                    if response.error is not None:
                        return ToolOutcome(error=response.error)
                    assert response.result is not None
                    raw = response.result.get("bytes")
                    resolved = response.result.get("resolvedAddress")
                    if not isinstance(raw, str) or not isinstance(resolved, Mapping):
                        raise ArtifactStoreError("bridge returned an invalid memory-read result")
                    try:
                        chunk = bytes.fromhex(raw)
                        observed_address = int(str(resolved["address"]), 16)
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ArtifactStoreError("bridge returned malformed memory bytes") from exc
                    if len(chunk) != chunk_size:
                        raise ArtifactStoreError("bridge returned a partial memory-read chunk")
                    if resolved_base is None:
                        resolved_base = observed_address
                    elif observed_address != resolved_base + offset:
                        raise ArtifactStoreError("bridge resolved a non-contiguous dump address")
                    chunks.append(chunk)
                    remaining -= chunk_size
                assert resolved_base is not None
                artifact = store.create(
                    chunks, kind="memory-dump", media_type="application/octet-stream",
                    session_id=self._session.session_id, generation=self._session.generation,
                    source={"address": f"0x{resolved_base:016X}", "size": requested},
                )
                raw_result: Mapping[str, Any] = {"artifact": artifact}
            elif action == "list":
                offset = int(arguments.get("cursor", "0"))
                limit = int(arguments.get("limit", 100))
                items, total = store.list(offset=offset, limit=limit)
                last = offset + len(items)
                raw_result = {"items": items, "truncated": last < total}
                if last < total:
                    raw_result = {**raw_result, "nextCursor": str(last)}
            elif action == "get_metadata":
                raw_result = {"artifact": store.metadata(str(arguments["artifactId"]))}
            elif action == "preview":
                offset = int(arguments.get("offset", 0))
                artifact, preview = store.preview(
                    str(arguments["artifactId"]), offset=offset, size=int(arguments.get("size", 256))
                )
                raw_result = {
                    "artifact": artifact, "offset": offset, "bytes": preview.hex().upper(),
                    "encoding": "hex", "complete": offset + len(preview) >= artifact["size"],
                }
            else:
                store.delete(str(arguments["artifactId"]))
                raw_result = {"deleted": True}
            result = self._normalize("ce.artifacts", arguments, raw_result)
            validate(tool["outputSchema"], result)
            return ToolOutcome(result=result)
        except ArtifactStoreError as exc:
            return self._error(
                "ARTIFACT_ERROR", str(exc), recoverable=True, safe_to_retry=True
            )

    def _call_structure(
        self, tool: Mapping[str, Any], arguments: Mapping[str, Any]
    ) -> ToolOutcome:
        action = str(arguments["action"])
        workspace = self._structure_workspace
        try:
            if action == "create":
                raw = {"structure": workspace.create(str(arguments["name"]), int(arguments["size"]), arguments["fields"])}
            elif action == "update":
                raw = {"structure": workspace.update(
                    str(arguments["structureId"]), int(arguments["expectedRevision"]),
                    str(arguments["name"]), int(arguments["size"]), arguments["fields"],
                )}
            elif action == "get":
                raw = {"structure": workspace.get(str(arguments["structureId"]))}
            elif action == "list":
                items = workspace.list()
                offset = int(arguments.get("cursor", "0"))
                limit = int(arguments.get("limit", 100))
                page = items[offset:offset + limit]
                raw = {"items": page, "truncated": offset + len(page) < len(items)}
                if raw["truncated"]:
                    raw["nextCursor"] = str(offset + len(page))
            elif action == "delete":
                workspace.delete(str(arguments["structureId"]), int(arguments["expectedRevision"]))
                raw = {"deleted": True}
            else:
                definition = workspace.get(str(arguments["structureId"]))
                assert self._session is not None
                request = BridgeRequest(
                    request_id=f"req-{uuid4().hex}", session_id=self._session.session_id,
                    method="structures.read",
                    params={
                        "base": arguments["base"], "fields": definition["fields"],
                        "expectedGeneration": arguments["expectedGeneration"],
                    },
                    deadline_ms=self._request_deadline_ms,
                )
                response = self._bridge.call(request)
                if response.error is not None:
                    return ToolOutcome(error=response.error)
                assert response.result is not None
                raw = {**response.result, "structure": definition}
            result = {"action": action, **raw}
            validate(tool["outputSchema"], result)
            self._update_session("ce.structures", arguments, result)
            return ToolOutcome(result=result)
        except StructureWorkspaceError as exc:
            return self._error("STRUCTURE_ERROR", str(exc), recoverable=True, safe_to_retry=True)
        except (BridgeTransportError, ConnectionError, EOFError, OSError):
            return self._transport_failure("ce.structures", arguments)
        except ContractViolation as exc:
            return self._error("BACKEND_CONTRACT_VIOLATION", str(exc), recoverable=False, safe_to_retry=False)

    def _update_session(
        self,
        name: str,
        arguments: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        if name == "ce.process" and arguments.get("action") == "detach":
            self._session = None
            return
        if name == "ce.status" and "session" not in result:
            self._session = None
            return
        session_value = result.get("session")
        if session_value is not None:
            if not isinstance(session_value, Mapping):
                raise ContractViolation("session result must be an object")
            self._session = Session.from_dict(session_value)

    def _transport_failure(
        self, name: str, arguments: Mapping[str, Any]
    ) -> ToolOutcome:
        mutation = arguments.get("action") in self._MUTATING_ACTIONS.get(name, set())
        if mutation:
            return self._error(
                "OUTCOME_UNKNOWN",
                "Bridge disconnected before the mutation outcome was observed",
                recoverable=True,
                safe_to_retry=False,
                suggested_action="Call ce.status and reconcile state before another mutation.",
                next_actions=(NextAction(
                    "REFRESH_STATUS", "required_before_retry",
                    "Observe current state without repeating the mutation.",
                    tool="ce.status", arguments={},
                ),),
            )
        return self._error(
            "BRIDGE_UNAVAILABLE",
            "Cheat Engine bridge is unavailable",
            recoverable=True,
            safe_to_retry=True,
            suggested_action="Confirm Cheat Engine is running and its autorun bridge loaded.",
            next_actions=(NextAction(
                "CHECK_CE_BRIDGE", "manual",
                "Confirm Cheat Engine and the local autorun bridge are available.",
            ),),
        )

    def _reconcile_process_mutation(
        self, name: str, arguments: Mapping[str, Any]
    ) -> ToolOutcome | None:
        """Observe an attach/detach result after CE rebuilt its autorun Lua state.

        This never repeats the mutation. It only reconnects and calls the
        read-only ``status.get`` bridge method until the original request deadline.
        """

        if name != "ce.process" or arguments.get("action") not in {"attach", "detach"}:
            return None
        action = arguments["action"]
        deadline = monotonic() + self._request_deadline_ms / 1000.0
        while monotonic() < deadline:
            remaining_ms = max(1, int((deadline - monotonic()) * 1000))
            request = BridgeRequest(
                request_id=f"req-{uuid4().hex}",
                method="status.get",
                params={},
                deadline_ms=min(remaining_ms, 250),
            )
            try:
                response = self._bridge.call(request)
            except (BridgeTransportError, ConnectionError, EOFError, OSError, ContractViolation):
                sleep(0.05)
                continue
            if response.error is not None or response.result is None:
                sleep(0.05)
                continue
            session_value = response.result.get("session")
            if action == "detach" and session_value is None:
                result = {"action": "detach", "detached": True, "reconciled": True}
                self._session = None
                return ToolOutcome(result=result)
            if action == "attach" and isinstance(session_value, Mapping):
                try:
                    observed = Session.from_dict(session_value)
                except ContractViolation:
                    return None
                if observed.pid != arguments["pid"]:
                    sleep(0.05)
                    continue
                result = {
                    "action": "attach",
                    "session": observed.to_dict(),
                    "reconciled": True,
                }
                self._session = observed
                return ToolOutcome(result=result)
            sleep(0.05)
        return None

    @staticmethod
    def _error(
        code: str,
        message: str,
        *,
        recoverable: bool,
        safe_to_retry: bool,
        suggested_action: str | None = None,
        next_actions: tuple[NextAction, ...] = (),
    ) -> ToolOutcome:
        return ToolOutcome(
            error=ErrorDetail(
                code=code,
                message=message,
                recoverable=recoverable,
                safe_to_retry=safe_to_retry,
                suggested_action=suggested_action,
                next_actions=next_actions,
            )
        )
