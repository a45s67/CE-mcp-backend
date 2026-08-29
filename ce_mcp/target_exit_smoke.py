"""Real-CE gate proving target exit invalidates bridge and sidecar sessions."""

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
    parser = argparse.ArgumentParser(prog="ce-mcp-target-exit-smoke")
    parser.add_argument("--target-pid", type=int, required=True)
    parser.add_argument("--pipe", default=DEFAULT_PIPE_NAME)
    parser.add_argument("--timeout", type=float, default=15.0)
    options = parser.parse_args(argv)
    client = WindowsNamedPipeBridgeClient(options.pipe)
    service = BackendService(
        client, Path(__file__).resolve().parent / "contracts" / "v1" / "tools",
        request_deadline_ms=5_000,
    )
    report: dict[str, object] = {"targetPid": options.target_pid}
    try:
        attached = _result(service, "ce.process", {"action": "attach", "pid": options.target_pid})
        old_generation = attached["session"]["generation"]
        deadline = monotonic() + options.timeout
        observed = None
        while monotonic() < deadline:
            observed = _result(service, "ce.status", {})
            if "session" not in observed:
                break
            sleep(0.05)
        if observed is None or "session" in observed:
            raise SmokeFailure("target exit was not observed before timeout")
        stale = service.call_tool(
            "ce.memory_read",
            {"mode": "raw", "address": "0x1", "size": 1, "expectedGeneration": old_generation},
        )
        if stale.error is None or stale.error.code != "NO_TARGET":
            raise SmokeFailure("sidecar retained a usable session after target exit")
        report.update(success=True, oldGeneration=old_generation, bridgeSessionAbsent=True,
                      sidecarSessionAbsent=service.session is None)
    except SmokeFailure as exc:
        report.update(success=False, error=str(exc))
    finally:
        client.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("success"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
