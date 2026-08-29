"""Real-CE MCP vertical slice for controlled memory-dump artifacts."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Sequence

from .artifacts import ArtifactStore
from .e2e_smoke import SmokeFailure, _result
from .service import BackendService
from .transport import DEFAULT_PIPE_NAME, WindowsNamedPipeBridgeClient


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ce-mcp-artifact-smoke")
    parser.add_argument("--pipe", default=DEFAULT_PIPE_NAME)
    parser.add_argument("--deadline-ms", type=int, default=15_000)
    options = parser.parse_args(argv)

    size = 256 * 1024 + 4096
    expected = bytes((index * 31 + 7) & 0xFF for index in range(size))
    buffer = (ctypes.c_ubyte * size).from_buffer_copy(expected)
    address = ctypes.addressof(buffer)
    client = WindowsNamedPipeBridgeClient(options.pipe)
    report = {"pid": os.getpid(), "address": f"0x{address:016X}", "size": size}

    with tempfile.TemporaryDirectory(prefix="ce-mcp-artifact-smoke-") as directory:
        contracts = Path(__file__).resolve().parent / "contracts" / "v1" / "tools"
        service = BackendService(
            client, contracts, request_deadline_ms=options.deadline_ms,
            artifact_store=ArtifactStore(Path(directory)),
        )
        artifact_id: str | None = None
        try:
            attached = _result(service, "ce.process", {"action": "attach", "pid": os.getpid()})
            generation = attached["session"]["generation"]
            dumped = _result(
                service,
                "ce.artifacts",
                {
                    "action": "memory_dump", "address": f"0x{address:X}", "size": size,
                    "expectedGeneration": generation,
                },
            )
            artifact = dumped["artifact"]
            artifact_id = artifact["artifactId"]
            if artifact["sha256"] != hashlib.sha256(expected).hexdigest():
                raise SmokeFailure("artifact hash differs from the controlled buffer")
            metadata = _result(
                service, "ce.artifacts", {"action": "get_metadata", "artifactId": artifact_id}
            )["artifact"]
            if metadata != artifact:
                raise SmokeFailure("stored metadata differs from creation result")
            preview = _result(
                service,
                "ce.artifacts",
                {"action": "preview", "artifactId": artifact_id, "offset": 256 * 1024 - 8, "size": 32},
            )
            if bytes.fromhex(preview["bytes"]) != expected[256 * 1024 - 8 : 256 * 1024 + 24]:
                raise SmokeFailure("preview across the read-chunk boundary differs")
            listed = _result(service, "ce.artifacts", {"action": "list", "limit": 10})
            if [item["artifactId"] for item in listed["items"]] != [artifact_id]:
                raise SmokeFailure("artifact list did not return the created artifact")
            _result(service, "ce.artifacts", {"action": "delete", "artifactId": artifact_id})
            artifact_id = None
            report.update(success=True, sha256=artifact["sha256"], previewBytes=32)
        except SmokeFailure as exc:
            report.update(success=False, error=str(exc))
        finally:
            if artifact_id is not None:
                service.call_tool("ce.artifacts", {"action": "delete", "artifactId": artifact_id})
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
