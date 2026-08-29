"""Real-CE proof that pipe disconnect cleans session-bound operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import sleep
from typing import Sequence

from .e2e_smoke import SmokeFailure, _result, _wait_running_scan
from .service import BackendService
from .transport import DEFAULT_PIPE_NAME, WindowsNamedPipeBridgeClient


def _service(pipe: str, deadline_ms: int) -> tuple[BackendService, WindowsNamedPipeBridgeClient]:
    client = WindowsNamedPipeBridgeClient(pipe)
    contracts = Path(__file__).resolve().parent / "contracts" / "v1" / "tools"
    return BackendService(client, contracts, request_deadline_ms=deadline_ms), client


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ce-mcp-disconnect-smoke")
    parser.add_argument("--target-pid", type=int, required=True)
    parser.add_argument("--pipe", default=DEFAULT_PIPE_NAME)
    parser.add_argument("--deadline-ms", type=int, default=10_000)
    options = parser.parse_args(argv)

    first, first_client = _service(options.pipe, options.deadline_ms)
    second_client: WindowsNamedPipeBridgeClient | None = None
    try:
        _result(first, "ce.status", {})
        attached = _result(first, "ce.process", {"action": "attach", "pid": options.target_pid})
        generation = attached["session"]["generation"]
        started = _result(
            first,
            "ce.scan",
            {
                "action": "start", "scanType": "unknown", "valueType": "u8",
                "rangeStart": "0", "rangeEnd": "7FFFFFFFFFFFFFFF",
                "protection": "*W*X*C", "expectedGeneration": generation,
            },
        )
        operation = _wait_running_scan(first, started["operation"], generation, 30.0)
        first_client.close()

        sleep(0.25)
        second, second_client = _service(options.pipe, options.deadline_ms)
        status = _result(second, "ce.status", {})
        session = status.get("session")
        if not session:
            raise SmokeFailure("target session disappeared after sidecar disconnect")
        listed = _result(
            second,
            "ce.operations",
            {"action": "list", "limit": 200, "expectedGeneration": session["generation"]},
        )
        if listed["items"]:
            raise SmokeFailure("operation handles survived sidecar disconnect")
        detached = _result(
            second,
            "ce.process",
            {"action": "detach", "expectedGeneration": session["generation"]},
        )
        print(
            json.dumps(
                {
                    "success": True,
                    "disconnectedOperationId": operation["operationId"],
                    "remainingOperations": 0,
                    "detach": detached,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except SmokeFailure as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(2)
    finally:
        first_client.close()
        if second_client is not None:
            second_client.close()


if __name__ == "__main__":
    main()
