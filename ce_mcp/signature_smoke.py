"""Real-CE vertical gate for bounded cancellable signature generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import monotonic, sleep
from typing import Sequence

from .analysis_smoke import _read_info
from .e2e_smoke import SmokeFailure, _result
from .service import BackendService
from .transport import DEFAULT_PIPE_NAME, WindowsNamedPipeBridgeClient


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ce-mcp-signature-smoke")
    parser.add_argument("--info", type=Path, required=True)
    parser.add_argument("--pipe", default=DEFAULT_PIPE_NAME)
    parser.add_argument("--deadline-ms", type=int, default=10_000)
    options = parser.parse_args(argv)
    info = _read_info(options.info)
    target_pid = int(info["pid"])
    client = WindowsNamedPipeBridgeClient(options.pipe)
    contracts = Path(__file__).resolve().parent / "contracts" / "v1" / "tools"
    service = BackendService(client, contracts, request_deadline_ms=options.deadline_ms)
    report: dict[str, object] = {"targetPid": target_pid}
    operation_id: str | None = None
    stage = "attach"
    try:
        attached = _result(service, "ce.process", {"action": "attach", "pid": target_pid})
        generation = attached["session"]["generation"]
        stage = "signature-start"
        started = _result(service, "ce.signature", {
            "action": "start", "address": f"0x{info['target']}",
            "rangeStart": f"0x{info['base']}", "rangeEnd": f"0x{info['end']}",
            "minBytes": 8, "maxBytes": 16, "expectedGeneration": generation,
        })
        operation_id = started["operation"]["operationId"]
        stage = "operation-poll"
        deadline = monotonic() + 20.0
        operation = started["operation"]
        while operation["state"] in {"queued", "running"} and monotonic() < deadline:
            sleep(0.05)
            operation = _result(service, "ce.operations", {
                "action": "get", "operationId": operation_id,
                "expectedGeneration": generation,
            })["operation"]
        if operation["state"] != "completed":
            raise SmokeFailure(f"signature operation ended as {operation['state']!r}")
        stage = "signature-result"
        result = _result(service, "ce.signature", {
            "action": "result", "operationId": operation_id,
            "expectedGeneration": generation,
        })
        expected = int(info["expected_min"])
        if not result.get("unique") or result.get("byteCount") != expected:
            raise SmokeFailure("signature did not become unique at the controlled byte length")
        if len(result["pattern"].split()) != expected or result.get("offset") != 0:
            raise SmokeFailure("signature snapshot shape or target offset is incorrect")
        stage = "signature-close"
        _result(service, "ce.signature", {
            "action": "close", "operationId": operation_id,
            "expectedGeneration": generation,
        })
        operation_id = None
        stage = "queued-cancel-start"
        queued = _result(service, "ce.signature", {
            "action": "start", "address": f"0x{info['target']}",
            "rangeStart": f"0x{info['base']}", "rangeEnd": f"0x{info['end']}",
            "minBytes": 8, "maxBytes": 16, "expectedGeneration": generation,
        })
        operation_id = queued["operation"]["operationId"]
        stage = "queued-cancel"
        cancelled = _result(service, "ce.operations", {
            "action": "cancel", "operationId": operation_id,
            "expectedGeneration": generation,
        })["operation"]
        if cancelled["state"] != "cancelled":
            raise SmokeFailure("queued signature operation was not cancelled")
        sleep(1.2)
        observed_cancel = _result(service, "ce.operations", {
            "action": "get", "operationId": operation_id,
            "expectedGeneration": generation,
        })["operation"]
        if observed_cancel["state"] != "cancelled":
            raise SmokeFailure("cancelled signature timer later started a worker")
        _result(service, "ce.signature", {
            "action": "close", "operationId": operation_id,
            "expectedGeneration": generation,
        })
        operation_id = None
        _result(service, "ce.process", {"action": "detach", "expectedGeneration": generation})
        report.update(success=True, byteCount=expected, pattern=result["pattern"], queuedCancel=True)
    except SmokeFailure as exc:
        report.update(success=False, stage=stage, error=str(exc))
    finally:
        if operation_id is not None and service.session is not None:
            service.call_tool("ce.signature", {
                "action": "close", "operationId": operation_id,
                "expectedGeneration": service.session.generation,
            })
        if service.session is not None:
            service.call_tool("ce.process", {
                "action": "detach", "expectedGeneration": service.session.generation,
            })
        client.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("success"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
