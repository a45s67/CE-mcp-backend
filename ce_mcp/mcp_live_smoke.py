"""Official-MCP-SDK live stdio workflow against a controlled target."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any, Mapping, Sequence

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class McpLiveSmokeFailure(RuntimeError):
    pass


def _value(result: Any, tool: str) -> Mapping[str, Any]:
    value = result.structured_content
    if result.is_error:
        error = value.get("error", {}) if isinstance(value, Mapping) else {}
        raise McpLiveSmokeFailure(
            f"{tool}: {error.get('code', 'UNKNOWN')}: {error.get('message', 'tool failed')}"
        )
    if not isinstance(value, Mapping):
        raise McpLiveSmokeFailure(f"{tool}: structuredContent was not an object")
    return value


async def run_workflow(target_pid: int, ce_pid: int | None, deadline_ms: int) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix="ce-mcp-live-") as temporary:
        state_root = Path(temporary)
        args = [
            "-m", "ce_mcp.mcp_server", "--transport", "stdio",
            "--deadline-ms", str(deadline_ms),
            "--audit-root", str(state_root / "audit"),
            "--artifact-root", str(state_root / "artifacts"),
        ]
        if ce_pid is not None:
            args.extend(("--ce-pid", str(ce_pid)))
        parameters = StdioServerParameters(
            command=sys.executable, args=args, cwd=root, env=dict(os.environ),
        )
        report: dict[str, Any] = {"targetPid": target_pid, "steps": []}
        generation: int | None = None
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                if initialized.server_info.name != "ce-mcp-backend":
                    raise McpLiveSmokeFailure("unexpected MCP server identity")
                report["steps"].append("initialize")

                listed = await session.list_tools()
                names = [tool.name for tool in listed.tools]
                required = {"ce.status", "ce.process", "ce.memory_read", "ce.disassembly", "ce.symbols"}
                if not required.issubset(names):
                    raise McpLiveSmokeFailure("MCP tool catalog omitted live-workflow tools")
                report["toolCount"] = len(names)
                report["steps"].append("list_tools")

                status = _value(await session.call_tool("ce.status", {}), "ce.status")
                if not status.get("bridge", {}).get("connected"):
                    raise McpLiveSmokeFailure("CE bridge did not report connected")
                report["steps"].append("status")

                processes = _value(await session.call_tool(
                    "ce.process", {"action": "list", "limit": 200}
                ), "ce.process")
                if target_pid not in {item.get("pid") for item in processes.get("items", [])}:
                    raise McpLiveSmokeFailure("controlled target was absent from process list")
                report["steps"].append("process_list")

                try:
                    attached = _value(await session.call_tool(
                        "ce.process", {"action": "attach", "pid": target_pid}
                    ), "ce.process")
                    generation = int(attached["session"]["generation"])
                    report["steps"].append("attach")

                    modules = _value(await session.call_tool("ce.symbols", {
                        "action": "modules", "limit": 1, "expectedGeneration": generation,
                    }), "ce.symbols")
                    module = modules.get("items", [None])[0]
                    if not isinstance(module, Mapping):
                        raise McpLiveSmokeFailure("attached target had no module")
                    base = module["base"]["address"]
                    memory = _value(await session.call_tool("ce.memory_read", {
                        "mode": "raw", "address": base, "size": 2,
                        "expectedGeneration": generation,
                    }), "ce.memory_read")
                    if memory.get("bytes", "").upper() != "4D5A":
                        raise McpLiveSmokeFailure("target module did not begin with MZ")
                    report["steps"].append("memory_read")

                    instruction = _value(await session.call_tool("ce.disassembly", {
                        "action": "instruction", "address": base,
                        "expectedGeneration": generation,
                    }), "ce.disassembly")
                    report["module"] = module["name"]
                    report["opcode"] = instruction["instruction"]["opcode"]
                    report["steps"].append("disassembly")
                finally:
                    if generation is not None:
                        detached = await session.call_tool("ce.process", {
                            "action": "detach", "expectedGeneration": generation,
                        })
                        _value(detached, "ce.process")
                        report["steps"].append("detach")
        report["success"] = True
        return report


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ce-mcp-live-smoke")
    parser.add_argument("--target-pid", type=int, required=True)
    parser.add_argument("--ce-pid", type=int)
    parser.add_argument("--deadline-ms", type=int, default=15_000)
    options = parser.parse_args(argv)
    try:
        report = anyio.run(run_workflow, options.target_pid, options.ce_pid, options.deadline_ms)
    except McpLiveSmokeFailure as exc:
        report = {"success": False, "error": str(exc)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("success"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
