"""Real-CE MCP vertical slice for pointer-chain resolve and validation."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
from typing import Sequence

from .e2e_smoke import SmokeFailure, _result
from .service import BackendService
from .transport import DEFAULT_PIPE_NAME, WindowsNamedPipeBridgeClient


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ce-mcp-pointer-smoke")
    parser.add_argument("--pipe", default=DEFAULT_PIPE_NAME)
    parser.add_argument("--deadline-ms", type=int, default=10_000)
    options = parser.parse_args(argv)

    leaf = ctypes.c_int32(0x12345678)
    first_offset, second_offset = 0x20, 0x14
    intermediate = ctypes.c_void_p(ctypes.addressof(leaf) - second_offset)
    base = ctypes.c_void_p(ctypes.addressof(intermediate) - first_offset)
    base_address = ctypes.addressof(base)
    target_address = ctypes.addressof(leaf)

    client = WindowsNamedPipeBridgeClient(options.pipe)
    contracts = Path(__file__).resolve().parent / "contracts" / "v1" / "tools"
    service = BackendService(client, contracts, request_deadline_ms=options.deadline_ms)
    report = {
        "pid": os.getpid(), "base": f"0x{base_address:016X}",
        "target": f"0x{target_address:016X}", "offsets": [first_offset, second_offset],
    }
    try:
        attached = _result(service, "ce.process", {"action": "attach", "pid": os.getpid()})
        generation = attached["session"]["generation"]
        resolved = _result(
            service,
            "ce.pointer",
            {
                "action": "resolve", "base": f"0x{base_address:X}",
                "offsets": [first_offset, second_offset], "expectedGeneration": generation,
            },
        )
        if int(resolved["finalAddress"]["address"], 16) != target_address:
            raise SmokeFailure("resolved chain did not land on the controlled target")

        validated = _result(
            service,
            "ce.pointer",
            {
                "action": "validate", "target": f"0x{target_address:X}",
                "chains": [
                    {"base": f"0x{base_address:X}", "offsets": [first_offset, second_offset]},
                    {"base": f"0x{base_address:X}", "offsets": [first_offset, second_offset + 4]},
                    {"base": "0x1", "offsets": [0]},
                ],
                "includeMisses": True, "expectedGeneration": generation,
            },
        )
        if validated["matched"] != 1 or validated["unreadable"] != 1:
            raise SmokeFailure(f"unexpected validation classification: {validated!r}")
        report.update(
            success=True, resolvedSteps=len(resolved["chain"]),
            matched=validated["matched"], unreadable=validated["unreadable"],
            misses=len(validated["misses"]),
        )
    except SmokeFailure as exc:
        report.update(success=False, error=str(exc))
    finally:
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
