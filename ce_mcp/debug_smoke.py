"""Real-CE MCP vertical slice for debugger stop-generation control."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import monotonic, sleep
from typing import Sequence

from .e2e_smoke import SmokeFailure, _result
from .service import BackendService
from .transport import DEFAULT_PIPE_NAME, WindowsNamedPipeBridgeClient


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ce-mcp-debug-smoke")
    parser.add_argument("--target-pid", type=int, required=True)
    parser.add_argument("--address", required=True)
    parser.add_argument("--pipe", default=DEFAULT_PIPE_NAME)
    parser.add_argument("--deadline-ms", type=int, default=10_000)
    options = parser.parse_args(argv)

    client = WindowsNamedPipeBridgeClient(options.pipe)
    contracts = Path(__file__).resolve().parent / "contracts" / "v1" / "tools"
    service = BackendService(client, contracts, request_deadline_ms=options.deadline_ms)
    report = {"targetPid": options.target_pid, "address": options.address}
    breakpoint_id: str | None = None
    try:
        attached = _result(service, "ce.process", {"action": "attach", "pid": options.target_pid})
        generation = attached["session"]["generation"]
        threads = _result(
            service, "ce.threads",
            {"action": "list", "limit": 200, "expectedGeneration": generation},
        )
        if not threads["items"]:
            raise SmokeFailure("target thread enumeration returned no identifiers")
        started = _result(
            service, "ce.debug_control",
            {"action": "start", "interface": "windows", "expectedGeneration": generation},
        )
        if not started["debugger"]["active"]:
            raise SmokeFailure("Windows debugger did not become active")
        installed = _result(
            service, "ce.breakpoints",
            {
                "action": "set", "address": options.address, "trigger": "write", "size": 4,
                "expectedGeneration": generation,
            },
        )
        breakpoint_id = installed["breakpoint"]["breakpointId"]

        deadline = monotonic() + 15.0
        event = None
        while monotonic() < deadline:
            observed = _result(
                service, "ce.debug_events",
                {"action": "list", "limit": 200, "expectedGeneration": generation},
            )
            if observed["items"]:
                event = observed["items"][-1]
                break
            sleep(0.05)
        if event is None or event["breakpointId"] != breakpoint_id:
            raise SmokeFailure("controlled write breakpoint did not produce its event")
        stop_generation = event["stopGeneration"]

        registers = _result(
            service, "ce.registers",
            {
                "action": "read", "includeVectors": True,
                "expectedGeneration": generation,
                "expectedStopGeneration": stop_generation,
            },
        )
        instruction_register = "rip" if registers["architecture"] == "x86_64" else "eip"
        if instruction_register not in registers["general"]:
            raise SmokeFailure("stopped register context omitted the instruction pointer")
        expected_vectors = 16 if registers["architecture"] == "x86_64" else 8
        if len(registers.get("vectors", {})) != expected_vectors:
            raise SmokeFailure("stopped register context returned an incomplete XMM snapshot")

        _result(
            service, "ce.breakpoints",
            {"action": "remove", "breakpointId": breakpoint_id, "expectedGeneration": generation},
        )
        breakpoint_id = None
        stale = service.call_tool(
            "ce.debug_control",
            {
                "action": "continue", "mode": "run", "expectedGeneration": generation,
                "expectedStopGeneration": stop_generation + 1,
            },
        )
        if stale.error is None or stale.error.code != "STALE_STOP" or stale.error.safe_to_retry:
            raise SmokeFailure("stale stop generation was not rejected safely")
        stepped = _result(
            service, "ce.debug_control",
            {
                "action": "continue", "mode": "step_into", "expectedGeneration": generation,
                "expectedStopGeneration": stop_generation,
            },
        )
        if stepped["debugger"]["stopped"]:
            raise SmokeFailure("debugger did not leave the original stop for step_into")
        step_deadline = monotonic() + 6.0
        step_event = None
        while monotonic() < step_deadline:
            observed = _result(
                service, "ce.debug_events",
                {"action": "list", "limit": 200, "expectedGeneration": generation},
            )
            candidates = [item for item in observed["items"] if item.get("kind") == "step"]
            if candidates:
                step_event = candidates[-1]
                break
            sleep(0.05)
        if step_event is None:
            raise SmokeFailure("step_into did not produce a bounded stop event")
        step_stop_generation = step_event["stopGeneration"]
        _result(
            service, "ce.registers",
            {
                "action": "read", "expectedGeneration": generation,
                "expectedStopGeneration": step_stop_generation,
            },
        )
        continued = _result(
            service, "ce.debug_control",
            {
                "action": "continue", "mode": "run", "expectedGeneration": generation,
                "expectedStopGeneration": step_stop_generation,
            },
        )
        if continued["debugger"]["stopped"]:
            raise SmokeFailure("debugger remained stopped after completing step_into")

        pause_requested = _result(
            service, "ce.debug_control",
            {
                "action": "pause", "expectedGeneration": generation,
            },
        )
        if not pause_requested.get("pauseRequested"):
            raise SmokeFailure("debugger pause request was not accepted")
        pause_deadline = monotonic() + 6.0
        pause_event = None
        while monotonic() < pause_deadline:
            observed = _result(
                service, "ce.debug_events",
                {"action": "list", "limit": 200, "expectedGeneration": generation},
            )
            candidates = [item for item in observed["items"] if item.get("kind") == "pause"]
            if candidates:
                pause_event = candidates[-1]
                break
            sleep(0.05)
        if pause_event is None:
            raise SmokeFailure("debugger pause did not produce a bounded stop event")
        pause_stop_generation = pause_event["stopGeneration"]
        resumed = _result(
            service, "ce.debug_control",
            {
                "action": "continue", "mode": "run", "expectedGeneration": generation,
                "expectedStopGeneration": pause_stop_generation,
            },
        )
        if resumed["debugger"]["stopped"]:
            raise SmokeFailure("debugger remained stopped after resuming a requested pause")
        detached = _result(
            service, "ce.debug_control", {"action": "detach", "expectedGeneration": generation},
        )
        if not detached["detached"]:
            raise SmokeFailure("debugger detach was not confirmed")
        report.update(
            success=True, breakpointId=event["breakpointId"], stopGeneration=stop_generation,
            threadCount=len(threads["items"]), architecture=registers["architecture"],
            vectorCount=len(registers["vectors"]), pauseStopGeneration=pause_stop_generation,
            stepStopGeneration=step_stop_generation,
        )
    except SmokeFailure as exc:
        report.update(success=False, error=str(exc))
    finally:
        if breakpoint_id is not None and service.session is not None:
            service.call_tool(
                "ce.breakpoints",
                {"action": "remove", "breakpointId": breakpoint_id, "expectedGeneration": service.session.generation},
            )
        if service.session is not None:
            service.call_tool(
                "ce.debug_control", {"action": "detach", "expectedGeneration": service.session.generation},
            )
            service.call_tool(
                "ce.process", {"action": "detach", "expectedGeneration": service.session.generation},
            )
        client.close()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("success"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
