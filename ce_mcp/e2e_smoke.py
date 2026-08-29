"""Repeatable real-CE read-only vertical-slice smoke test."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Mapping, Sequence

from .service import BackendService
from .transport import DEFAULT_PIPE_NAME, WindowsNamedPipeBridgeClient


class SmokeFailure(RuntimeError):
    pass


def _result(service: BackendService, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    outcome = service.call_tool(tool, arguments)
    if outcome.error is not None:
        raise SmokeFailure(f"{tool}: {outcome.error.code}: {outcome.error.message}")
    assert outcome.result is not None
    return outcome.result


def _wait_operation(
    service: BackendService, operation: Mapping[str, Any], generation: int, timeout: float
) -> Mapping[str, Any]:
    deadline = monotonic() + timeout
    current = operation
    while current["state"] in {"queued", "running"} and monotonic() < deadline:
        sleep(0.05)
        observed = _result(
            service,
            "ce.operations",
            {
                "action": "get",
                "operationId": current["operationId"],
                "expectedGeneration": generation,
            },
        )
        current = observed["operation"]
    if current["state"] != "completed":
        raise SmokeFailure(f"scan did not complete successfully: {current.get('state')!r}")
    return current


def _wait_running_scan(
    service: BackendService, operation: Mapping[str, Any], generation: int, timeout: float
) -> Mapping[str, Any]:
    deadline = monotonic() + timeout
    current = operation
    while monotonic() < deadline:
        progress = current.get("progress", {})
        total = progress.get("total", 0)
        completed = progress.get("completed", 0)
        if current.get("state") == "running" and total > 0 and completed < total:
            return current
        if current.get("state") not in {"queued", "running"}:
            break
        sleep(0.01)
        observed = _result(
            service,
            "ce.operations",
            {
                "action": "get", "operationId": current["operationId"],
                "expectedGeneration": generation,
            },
        )
        current = observed["operation"]
    raise SmokeFailure(f"scan was not observed running before completion: {current!r}")


def run_vertical_slice(
    service: BackendService,
    *,
    target_name: str | None = None,
    target_pid: int | None = None,
    detach: bool = True,
    include_scan: bool = False,
    include_cancel_scan: bool = False,
    scan_timeout_seconds: float = 30.0,
    progress: bool = False,
) -> dict[str, Any]:
    def trace(message: str) -> None:
        if progress:
            print(f"[ce-mcp-e2e] {message}", flush=True)

    report: dict[str, Any] = {"steps": []}
    status = _result(service, "ce.status", {})
    report["steps"].append({"name": "status", "bridge": status["bridge"]})

    if target_pid is None:
        if not target_name:
            raise SmokeFailure("target_name or target_pid is required")
        listed = _result(
            service,
            "ce.process",
            {"action": "list", "nameFilter": target_name, "limit": 200},
        )
        matches = [
            item
            for item in listed.get("items", [])
            if item.get("name", "").casefold() == target_name.casefold()
        ]
        if len(matches) != 1:
            raise SmokeFailure(f"expected one exact target named {target_name!r}, found {len(matches)}")
        target_pid = matches[0]["pid"]

    attached = False
    try:
        attached_result = _result(
            service,
            "ce.process",
            {"action": "attach", "pid": target_pid},
        )
        attached = True
        session = attached_result["session"]
        generation = session["generation"]
        report["session"] = session
        report["steps"].append({"name": "attach", "pid": target_pid})

        modules = _result(
            service,
            "ce.symbols",
            {"action": "modules", "limit": 1, "expectedGeneration": generation},
        )
        if not modules.get("items"):
            raise SmokeFailure("target has no enumerable modules")
        module = modules["items"][0]
        base = module["base"]["address"]
        report["module"] = module
        report["steps"].append({"name": "modules", "base": base})

        memory = _result(
            service,
            "ce.memory_read",
            {
                "mode": "raw",
                "address": base,
                "size": 2,
                "expectedGeneration": generation,
            },
        )
        if memory.get("bytes", "").upper() != "4D5A":
            raise SmokeFailure(f"module base did not contain MZ: {memory.get('bytes')!r}")
        report["steps"].append({"name": "memory_read", "bytes": memory["bytes"]})

        mapped = _result(
            service,
            "ce.memory_map",
            {"moduleFilter": module["name"], "limit": 10, "expectedGeneration": generation},
        )
        if not mapped.get("items"):
            raise SmokeFailure("module-filtered memory map was empty")
        report["steps"].append({"name": "memory_map", "regions": len(mapped["items"])})

        resolved = _result(
            service,
            "ce.symbols",
            {
                "action": "resolve",
                "expression": module["name"],
                "expectedGeneration": generation,
            },
        )
        report["steps"].append(
            {"name": "symbol_resolve", "address": resolved["address"]["address"]}
        )

        if include_cancel_scan:
            trace("starting full-range unknown scan for cancellation")
            cancel_started = _result(
                service,
                "ce.scan",
                {
                    "action": "start", "scanType": "unknown", "valueType": "u8",
                    "rangeStart": "0", "rangeEnd": "7FFFFFFFFFFFFFFF",
                    "protection": "*W*X*C", "expectedGeneration": generation,
                },
            )
            running = _wait_running_scan(
                service, cancel_started["operation"], generation, scan_timeout_seconds
            )
            cancelled = _result(
                service,
                "ce.operations",
                {
                    "action": "cancel", "operationId": running["operationId"],
                    "expectedGeneration": generation,
                },
            )
            if cancelled["operation"]["state"] != "cancelled":
                raise SmokeFailure("running scan did not transition to cancelled")
            _result(service, "ce.status", {})
            _result(
                service,
                "ce.scan",
                {
                    "action": "close", "operationId": running["operationId"],
                    "expectedGeneration": generation,
                },
            )
            trace("running scan cancelled and closed")
            report["steps"].append({"name": "scan_cancel", "operationId": running["operationId"]})

        disassembled = _result(
            service,
            "ce.disassembly",
            {
                "action": "instruction",
                "address": base,
                "expectedGeneration": generation,
            },
        )
        report["steps"].append(
            {"name": "disassembly", "opcode": disassembled["instruction"]["opcode"]}
        )

        if include_scan:
            trace("starting bounded AOB scan")
            base_value = int(base, 16)
            started_scan = _result(
                service,
                "ce.scan",
                {
                    "action": "start",
                    "scanType": "exact",
                    "valueType": "aob",
                    "value": "4D 5A",
                    "rangeStart": base,
                    "rangeEnd": f"0x{base_value + 4095:016X}",
                    "protection": "*W*X*C",
                    "expectedGeneration": generation,
                },
            )
            operation_id = started_scan["operation"]["operationId"]
            _wait_operation(service, started_scan["operation"], generation, scan_timeout_seconds)
            trace("AOB scan completed")
            scan_results = _result(
                service,
                "ce.scan",
                {
                    "action": "results",
                    "operationId": operation_id,
                    "limit": 200,
                    "expectedGeneration": generation,
                },
            )
            addresses = {item["address"]["address"] for item in scan_results["items"]}
            if base.upper() not in {address.upper() for address in addresses}:
                raise SmokeFailure("AOB scan did not return the module base")
            if scan_results["total"] > 256:
                raise SmokeFailure(
                    f"AOB scan degenerated into an unfiltered range: {scan_results['total']} results"
                )
            _result(
                service,
                "ce.scan",
                {
                    "action": "close",
                    "operationId": operation_id,
                    "expectedGeneration": generation,
                },
            )
            trace("AOB scan closed")

            numeric_scan = _result(
                service,
                "ce.scan",
                {
                    "action": "start", "scanType": "exact", "valueType": "u8",
                    "value": "77", "rangeStart": base,
                    "rangeEnd": f"0x{base_value + 4095:016X}",
                    "protection": "*W*X*C", "expectedGeneration": generation,
                },
            )
            numeric_id = numeric_scan["operation"]["operationId"]
            _wait_operation(service, numeric_scan["operation"], generation, scan_timeout_seconds)
            trace("numeric first scan completed; scheduling exact refine")
            refined_scan = _result(
                service,
                "ce.scan",
                {
                    "action": "refine", "operationId": numeric_id,
                    "scanType": "exact", "value": "77", "expectedGeneration": generation,
                },
            )
            _wait_operation(service, refined_scan["operation"], generation, scan_timeout_seconds)
            trace("numeric exact refine completed")
            refined_results = _result(
                service,
                "ce.scan",
                {
                    "action": "results", "operationId": numeric_id,
                    "limit": 200, "expectedGeneration": generation,
                },
            )
            refined_addresses = {
                item["address"]["address"].upper() for item in refined_results["items"]
            }
            if base.upper() not in refined_addresses:
                raise SmokeFailure("numeric refine scan did not retain the module base")
            _result(
                service,
                "ce.scan",
                {"action": "close", "operationId": numeric_id, "expectedGeneration": generation},
            )

            report["steps"].append(
                {
                    "name": "scan", "operationId": operation_id,
                    "results": scan_results["total"],
                    "refinedResults": refined_results["total"],
                }
            )
        report["success"] = True
    finally:
        if detach and attached and service.session is not None:
            generation = service.session.generation
            outcome = service.call_tool(
                "ce.process",
                {"action": "detach", "expectedGeneration": generation},
            )
            report["detach"] = outcome.to_dict()
            if outcome.error is not None:
                report["success"] = False
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ce-mcp-e2e-smoke")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--target-name")
    target.add_argument("--target-pid", type=int)
    parser.add_argument("--pipe", default=DEFAULT_PIPE_NAME)
    parser.add_argument("--deadline-ms", type=int, default=5_000)
    parser.add_argument("--keep-attached", action="store_true")
    parser.add_argument("--scan", action="store_true", help="also exercise asynchronous AOB scan")
    parser.add_argument("--cancel-scan", action="store_true", help="exercise running-scan cancellation")
    parser.add_argument("--progress", action="store_true", help="print live smoke-test milestones")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    options = build_parser().parse_args(argv)
    contracts = Path(__file__).resolve().parent / "contracts" / "v1" / "tools"
    service = BackendService(
        WindowsNamedPipeBridgeClient(options.pipe),
        contracts,
        request_deadline_ms=options.deadline_ms,
    )
    try:
        report = run_vertical_slice(
            service,
            target_name=options.target_name,
            target_pid=options.target_pid,
            detach=not options.keep_attached,
            include_scan=options.scan,
            include_cancel_scan=options.cancel_scan,
            progress=options.progress,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report.get("success"):
            raise SystemExit(2)
    except SmokeFailure as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
