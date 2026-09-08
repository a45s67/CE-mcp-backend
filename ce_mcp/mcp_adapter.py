"""Official MCP Python SDK 2.x adapter for :mod:`ce_mcp.service`."""

from __future__ import annotations

import json
from typing import Any

import anyio
import mcp.types as types
from mcp.server import Server

from . import __version__
from .models import ErrorDetail, NextAction
from .service import BackendService, ToolOutcome


_SHRINKABLE_ARGUMENTS = {
    ("ce.process", "list"): "limit",
    ("ce.memory_map", None): "limit",
    ("ce.symbols", "modules"): "limit",
    ("ce.symbols", "list"): "limit",
    ("ce.scan", "results"): "limit",
    ("ce.operations", "list"): "limit",
    ("ce.artifacts", "list"): "limit",
    ("ce.debug_events", "list"): "limit",
    ("ce.threads", "list"): "limit",
    ("ce.structures", "list"): "limit",
    ("ce.dbvm_trace", "results"): "limit",
    ("ce.dbvm_watch", "events"): "limit",
    ("ce.disassembly", "list"): "instructionCount",
    ("ce.disassembly", "previous"): "count",
    ("ce.disassembly", "next"): "count",
}


def _shrinkable_argument(name: str, arguments: dict[str, Any]) -> str | None:
    action = arguments.get("action")
    key = action if isinstance(action, str) else None
    field = _SHRINKABLE_ARGUMENTS.get((name, key))
    if field is not None:
        return field
    if name == "ce.memory_read":
        if arguments.get("mode") == "raw":
            return "size"
        if arguments.get("mode") == "typed":
            data_type = arguments.get("dataType")
            if data_type in {"string", "wstring"}:
                return "maxStringBytes"
            return "count"
    return None


def _annotations(value: dict[str, Any]) -> types.ToolAnnotations:
    return types.ToolAnnotations(
        readOnlyHint=value["readOnlyHint"],
        destructiveHint=value["destructiveHint"],
        idempotentHint=value["idempotentHint"],
        openWorldHint=False,
    )


def build_tool_list(service: BackendService) -> list[types.Tool]:
    """Expose input contracts; output schemas remain internal validation only."""

    return [
        types.Tool(
            name=definition["name"],
            description=definition["description"],
            inputSchema=definition["inputSchema"],
            annotations=_annotations(definition["annotations"]),
        )
        for definition in service.catalog
    ]


async def invoke_tool(
    service: BackendService, name: str, arguments: dict[str, Any] | None
) -> types.CallToolResult:
    """Run blocking CE bridge work off the MCP event loop."""

    outcome = await anyio.to_thread.run_sync(
        service.call_tool,
        name,
        arguments or {},
        abandon_on_cancel=True,
    )
    call_arguments = arguments or {}
    return _bounded_result(service, name, call_arguments, outcome)


def _result_dict(payload: dict[str, Any], is_error: bool) -> dict[str, Any]:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"),
        ))],
        isError=is_error,
    ).model_dump(by_alias=True, exclude_none=True)


def _encoded_size(value: dict[str, Any]) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _output_limit_error(
    service: BackendService,
    name: str,
    arguments: dict[str, Any],
    actual_bytes: int,
    original: ToolOutcome,
) -> ToolOutcome:
    action_value = arguments.get("action")
    action = action_value if isinstance(action_value, str) else None
    mutation = service.is_mutation(name, arguments)
    shrink_field = _shrinkable_argument(name, arguments)
    next_actions: tuple[NextAction, ...] = ()
    suggested_action: str | None = None
    safe_to_retry = False
    if original.result is not None and not mutation and shrink_field is not None:
        current = arguments.get(shrink_field)
        # An omitted count can mean one value; never increase work or prescribe
        # the same irreducible request as a size-limit recovery.
        implicit_one = (
            name == "ce.memory_read" and shrink_field == "count"
        )
        if current == 1 or (current is None and implicit_one):
            suggested_action = (
                f"This read already requests the minimum {shrink_field}; do not repeat it "
                "unchanged. Use a narrower representation or review the output limit."
            )
        else:
            recommended = max(1, current // 2) if isinstance(current, int) else 1
            preserved = tuple(
                sorted(key for key in arguments if key != shrink_field)[:16]
            )
            suggested_action = f"Retry this read with a smaller {shrink_field} value."
            next_actions = (
                NextAction(
                    code="RETRY_WITH_SMALLER_RESULT",
                    execution="suggested",
                    reason=f"Reduce {shrink_field} so the response fits the configured limit.",
                    tool=name,
                    arguments_patch={shrink_field: recommended},
                    preserve_arguments=preserved,
                ),
            )
            safe_to_retry = True
    elif mutation:
        suggested_action = "Reconcile current state before deciding whether to issue another mutation."
        next_actions = (
            NextAction(
                code="REFRESH_STATUS",
                execution="required_before_retry",
                reason="The mutation response could not be delivered; reconcile its outcome.",
                tool="ce.status",
                arguments={},
            ),
        )
    outcome_label = (
        "completed_response_not_returned"
        if original.result is not None
        else "error_response_not_returned"
    )
    message = (
        f"Tool response was {actual_bytes} bytes, exceeding the configured "
        f"{service.max_output_bytes}-byte limit."
    )
    details = {
        "actualBytes": actual_bytes,
        "limitBytes": service.max_output_bytes,
        "tool": name,
        "action": action,
        "outcome": outcome_label,
    }
    if original.error is not None:
        details["originalErrorCode"] = original.error.code
    return ToolOutcome(
        error=ErrorDetail(
            code="OUTPUT_LIMIT_EXCEEDED",
            message=message,
            recoverable=True,
            safe_to_retry=safe_to_retry,
            suggested_action=suggested_action,
            advice_source="ce-mcp-backend" if suggested_action is not None else None,
            next_actions=next_actions,
            details=details,
        )
    )


def _bounded_result(
    service: BackendService,
    name: str,
    arguments: dict[str, Any],
    outcome: ToolOutcome,
) -> types.CallToolResult:
    if outcome.error is not None:
        for action in outcome.error.next_actions:
            service.validate_next_action(
                action, current_tool=name, current_arguments=arguments
            )
    value = outcome.to_dict()
    payload = dict(outcome.result) if outcome.result is not None else value
    candidate = _result_dict(payload, outcome.error is not None)
    actual_bytes = _encoded_size(candidate)
    if actual_bytes > service.max_output_bytes:
        outcome = _output_limit_error(service, name, arguments, actual_bytes, outcome)
        assert outcome.error is not None
        for action in outcome.error.next_actions:
            service.validate_next_action(
                action, current_tool=name, current_arguments=arguments
            )
        payload = outcome.to_dict()
        candidate = _result_dict(payload, True)
        if _encoded_size(candidate) > service.max_output_bytes:
            raise RuntimeError("configured MCP output limit cannot contain its bounded error")
    return types.CallToolResult(**candidate)


def create_mcp_server(service: BackendService) -> Server:
    async def on_list_tools(context, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=build_tool_list(service))

    async def on_call_tool(context, params: types.CallToolRequestParams):
        return await invoke_tool(service, params.name, params.arguments)

    return Server(
        "ce-mcp-backend",
        version=__version__,
        title="Cheat Engine MCP Backend",
        description="Safe, structured Cheat Engine dynamic-analysis tools",
        instructions=(
            "Begin with ce.status, then use ce.process to list and explicitly attach to an "
            "authorized target. Preserve the returned session generation for target-bound "
            "calls and the latest debugger stop generation for register, resume, and step "
            "calls. Treat both generations as stale after detach, target exit, or bridge "
            "reconnect. Close scan/signature operations, remove owned breakpoints, and detach "
            "during cleanup. Never retry an OUTCOME_UNKNOWN mutation; reconcile with ce.status "
            "or the relevant read-only status/list action. DBK and DBVM are never initialized "
            "by this server and their tools remain unavailable unless the user configured and "
            "enabled the separate hypervisor policy explicitly. "
            "Tool results are complete bounded JSON objects in one text block; no structuredContent "
            "is required. A successful call is not proof of complete data or completed cancellation. "
            "Current native debugger stop/context claims failed live verification: do not use "
            "start, pause, continue or register snapshots for debugging until reverified. "
            "Keep status and owned-resource cleanup available. If a read loses the bridge, stop "
            "the sequence and reconcile status rather than issuing a parallel retry batch."
        ),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
