"""Repeatable same-host latency gate for status and 4 KiB target reads."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Sequence

from .e2e_smoke import SmokeFailure, _result
from .service import BackendService
from .transport import DEFAULT_PIPE_NAME, WindowsNamedPipeBridgeClient


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999999) - 1))
    return ordered[index]


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ce-mcp-performance-smoke")
    parser.add_argument("--target-pid", type=int, required=True)
    parser.add_argument("--address", required=True)
    parser.add_argument("--pipe", default=DEFAULT_PIPE_NAME)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--deadline-ms", type=int, default=5_000)
    options = parser.parse_args(argv)
    if not 20 <= options.samples <= 500:
        raise SystemExit("--samples must be between 20 and 500")

    client = WindowsNamedPipeBridgeClient(options.pipe)
    contracts = Path(__file__).resolve().parent / "contracts" / "v1" / "tools"
    service = BackendService(client, contracts, request_deadline_ms=options.deadline_ms)
    report: dict[str, object] = {"samples": options.samples, "targetPid": options.target_pid}
    try:
        attached = _result(service, "ce.process", {"action": "attach", "pid": options.target_pid})
        generation = attached["session"]["generation"]
        status_args: dict[str, object] = {}
        read_args = {
            "mode": "raw", "address": options.address, "size": 4096,
            "expectedGeneration": generation,
        }
        for _ in range(3):
            _result(service, "ce.status", status_args)
            _result(service, "ce.memory_read", read_args)
        measurements: dict[str, list[float]] = {"status": [], "read4KiB": []}
        for _ in range(options.samples):
            for label, tool, arguments in (
                ("status", "ce.status", status_args),
                ("read4KiB", "ce.memory_read", read_args),
            ):
                started = perf_counter()
                _result(service, tool, arguments)
                measurements[label].append((perf_counter() - started) * 1000)
        metrics = {
            label: {
                "p50Ms": round(median(values), 3),
                "p95Ms": round(percentile(values, 0.95), 3),
                "maxMs": round(max(values), 3),
            }
            for label, values in measurements.items()
        }
        passed = metrics["status"]["p95Ms"] < 200 and metrics["read4KiB"]["p95Ms"] < 500
        report.update(success=passed, metrics=metrics, thresholds={"statusP95Ms": 200, "read4KiBP95Ms": 500})
        if not passed:
            raise SmokeFailure("same-host latency threshold was exceeded")
    except SmokeFailure as exc:
        report.update(success=False, error=str(exc))
    finally:
        if service.session is not None:
            service.call_tool("ce.process", {"action": "detach", "expectedGeneration": service.session.generation})
        client.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("success"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
