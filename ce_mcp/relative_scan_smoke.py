"""Real-CE MCP vertical slice for relative scan refinement."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from .e2e_smoke import SmokeFailure, _result, _wait_operation
from .service import BackendService
from .transport import DEFAULT_PIPE_NAME, WindowsNamedPipeBridgeClient


def _contains_address(result: Mapping[str, Any], address: int) -> bool:
    expected = f"0x{address:016X}".upper()
    return any(item["address"]["address"].upper() == expected for item in result["items"])


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ce-mcp-relative-scan-smoke")
    parser.add_argument("--pipe", default=DEFAULT_PIPE_NAME)
    parser.add_argument("--deadline-ms", type=int, default=10_000)
    parser.add_argument("--scan-timeout", type=float, default=30.0)
    options = parser.parse_args(argv)

    value = ctypes.c_int32(100)
    address = ctypes.addressof(value)
    page_start = address & ~0xFFF
    client = WindowsNamedPipeBridgeClient(options.pipe)
    contracts = Path(__file__).resolve().parent / "contracts" / "v1" / "tools"
    service = BackendService(client, contracts, request_deadline_ms=options.deadline_ms)
    operation_id: str | None = None
    generation: int | None = None
    report: dict[str, Any] = {"pid": os.getpid(), "address": f"0x{address:016X}", "steps": []}
    try:
        attached = _result(service, "ce.process", {"action": "attach", "pid": os.getpid()})
        generation = attached["session"]["generation"]
        started = _result(
            service,
            "ce.scan",
            {
                "action": "start", "scanType": "exact", "valueType": "i32", "value": "100",
                "rangeStart": f"0x{page_start:016X}",
                "rangeEnd": f"0x{page_start + 4095:016X}",
                "protection": "*W*X*C", "expectedGeneration": generation,
            },
        )
        operation_id = started["operation"]["operationId"]
        _wait_operation(service, started["operation"], generation, options.scan_timeout)

        for mode, next_value in (
            ("unchanged", 100), ("increased", 101),
            ("decreased", 99), ("changed", 123),
        ):
            value.value = next_value
            refined = _result(
                service,
                "ce.scan",
                {
                    "action": "refine", "operationId": operation_id,
                    "scanType": mode, "expectedGeneration": generation,
                },
            )
            _wait_operation(service, refined["operation"], generation, options.scan_timeout)
            results = _result(
                service,
                "ce.scan",
                {
                    "action": "results", "operationId": operation_id,
                    "limit": 200, "expectedGeneration": generation,
                },
            )
            if not _contains_address(results, address):
                raise SmokeFailure(f"{mode} refinement dropped the controlled address")
            report["steps"].append({"mode": mode, "value": next_value, "total": results["total"]})
        report["success"] = True
    except SmokeFailure as exc:
        report.update(success=False, error=str(exc))
    finally:
        if operation_id is not None and generation is not None:
            service.call_tool(
                "ce.scan",
                {"action": "close", "operationId": operation_id, "expectedGeneration": generation},
            )
        if service.session is not None:
            service.call_tool(
                "ce.process", {"action": "detach", "expectedGeneration": service.session.generation}
            )
        client.close()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("success"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
