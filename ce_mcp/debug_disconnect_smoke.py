"""Real-CE gate for fail-safe debugger cleanup on client disconnect."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import monotonic, sleep
from typing import Sequence

from .e2e_smoke import SmokeFailure, _result
from .service import BackendService
from .transport import DEFAULT_PIPE_NAME, WindowsNamedPipeBridgeClient


def _service(pipe: str, deadline_ms: int) -> tuple[WindowsNamedPipeBridgeClient, BackendService]:
    client = WindowsNamedPipeBridgeClient(pipe)
    contracts = Path(__file__).resolve().parent / "contracts" / "v1" / "tools"
    return client, BackendService(client, contracts, request_deadline_ms=deadline_ms)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ce-mcp-debug-disconnect-smoke")
    parser.add_argument("--target-pid", type=int, required=True)
    parser.add_argument("--address", required=True)
    parser.add_argument("--pipe", default=DEFAULT_PIPE_NAME)
    parser.add_argument("--deadline-ms", type=int, default=10_000)
    options = parser.parse_args(argv)

    report = {"targetPid": options.target_pid, "address": options.address}
    first, service = _service(options.pipe, options.deadline_ms)
    second: WindowsNamedPipeBridgeClient | None = None
    stage = "attach"
    try:
        attached = _result(service, "ce.process", {"action": "attach", "pid": options.target_pid})
        generation = attached["session"]["generation"]
        stage = "debug-start"
        _result(service, "ce.debug_control", {
            "action": "start", "interface": "windows", "expectedGeneration": generation,
        })
        stage = "breakpoint-set"
        installed = _result(service, "ce.breakpoints", {
            "action": "set", "address": options.address, "trigger": "write", "size": 4,
            "expectedGeneration": generation,
        })
        breakpoint_id = installed["breakpoint"]["breakpointId"]

        stage = "wait-stop"
        deadline = monotonic() + 15.0
        stop_generation = None
        while monotonic() < deadline:
            events = _result(service, "ce.debug_events", {
                "action": "list", "limit": 200, "expectedGeneration": generation,
            })
            if events["items"]:
                stop_generation = events["items"][-1]["stopGeneration"]
                break
            sleep(0.05)
        if stop_generation is None:
            raise SmokeFailure("breakpoint did not stop before disconnect")

        first.close()
        sleep(0.2)
        stage = "reconnect"
        second, recovered = _service(options.pipe, options.deadline_ms)
        status = _result(recovered, "ce.status", {})
        recovered_session = status.get("session")
        if recovered_session is None:
            reattached = _result(recovered, "ce.process", {
                "action": "attach", "pid": options.target_pid,
            })
            generation = reattached["session"]["generation"]
        else:
            generation = recovered_session["generation"]
        debugger = _result(recovered, "ce.debug_control", {
            "action": "status", "expectedGeneration": generation,
        })["debugger"]
        breakpoints = _result(recovered, "ce.breakpoints", {
            "action": "list", "expectedGeneration": generation,
        })
        events = _result(recovered, "ce.debug_events", {
            "action": "list", "limit": 200, "expectedGeneration": generation,
        })
        if debugger["active"] or debugger["stopped"]:
            raise SmokeFailure("debugger remained active or stopped after disconnect")
        if breakpoints["items"] or events["items"]:
            raise SmokeFailure("debugger handles or events survived disconnect")
        _result(recovered, "ce.process", {"action": "detach", "expectedGeneration": generation})
        report.update(success=True, breakpointId=breakpoint_id, stopGeneration=stop_generation)
    except SmokeFailure as exc:
        report.update(success=False, stage=stage, error=str(exc))
    finally:
        first.close()
        if second is not None:
            second.close()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("success"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
