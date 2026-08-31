"""Official MCP Python SDK 2.x adapter for :mod:`ce_mcp.service`."""

from __future__ import annotations

import json
from typing import Any

import anyio
import mcp.types as types
from mcp.server import Server

from .service import BackendService


def _annotations(value: dict[str, Any]) -> types.ToolAnnotations:
    return types.ToolAnnotations(
        readOnlyHint=value["readOnlyHint"],
        destructiveHint=value["destructiveHint"],
        idempotentHint=value["idempotentHint"],
        openWorldHint=False,
    )


def build_tool_list(service: BackendService) -> list[types.Tool]:
    """Translate the deterministic checked-in catalog without schema inference."""

    return [
        types.Tool(
            name=definition["name"],
            description=definition["description"],
            inputSchema=definition["inputSchema"],
            outputSchema=definition["outputSchema"],
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
    value = outcome.to_dict()
    payload = outcome.result if outcome.result is not None else value
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )
        ],
        structuredContent=payload,
        isError=outcome.error is not None,
    )


def create_mcp_server(service: BackendService) -> Server:
    async def on_list_tools(context, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=build_tool_list(service))

    async def on_call_tool(context, params: types.CallToolRequestParams):
        return await invoke_tool(service, params.name, params.arguments)

    return Server(
        "ce-mcp-backend",
        version="0.0.1",
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
            "enabled the separate hypervisor policy explicitly."
        ),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
