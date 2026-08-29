"""Real-CE vertical gate for bounded memory compare and checksum."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping, Sequence

from .e2e_smoke import SmokeFailure, _result
from .service import BackendService
from .transport import DEFAULT_PIPE_NAME, WindowsNamedPipeBridgeClient


def _read_info(path: Path) -> Mapping[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="ascii").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ce-mcp-analysis-smoke")
    parser.add_argument("--info", type=Path, required=True)
    parser.add_argument("--pipe", default=DEFAULT_PIPE_NAME)
    parser.add_argument("--deadline-ms", type=int, default=10_000)
    options = parser.parse_args(argv)

    info = _read_info(options.info)
    target_pid = int(info["pid"])
    size = int(info["size"])
    addresses = {name: f"0x{info[name]}" for name in ("left", "equal", "differing")}
    client = WindowsNamedPipeBridgeClient(options.pipe)
    contracts = Path(__file__).resolve().parent / "contracts" / "v1" / "tools"
    service = BackendService(client, contracts, request_deadline_ms=options.deadline_ms)
    report: dict[str, object] = {"targetPid": target_pid, "size": size}
    try:
        attached = _result(service, "ce.process", {"action": "attach", "pid": target_pid})
        generation = attached["session"]["generation"]
        same = _result(service, "ce.memory_analysis", {
            "action": "compare", "leftAddress": addresses["left"],
            "rightAddress": addresses["equal"], "size": size,
            "expectedGeneration": generation,
        })
        different = _result(service, "ce.memory_analysis", {
            "action": "compare", "leftAddress": addresses["left"],
            "rightAddress": addresses["differing"], "size": size,
            "expectedGeneration": generation,
        })
        checksum = _result(service, "ce.memory_analysis", {
            "action": "checksum", "address": addresses["left"], "size": size,
            "algorithm": "md5", "expectedGeneration": generation,
        })
        if not same["equal"]:
            raise SmokeFailure("equal controlled regions compared unequal")
        expected_difference = int(info["difference"])
        if different["equal"] or different.get("firstDifference") != expected_difference:
            raise SmokeFailure("first differing offset did not match the controlled fixture")
        if checksum["digest"].lower() != info["md5"].lower():
            raise SmokeFailure("CE checksum did not match the independently computed digest")
        _result(service, "ce.process", {"action": "detach", "expectedGeneration": generation})
        report.update(success=True, firstDifference=expected_difference, md5=checksum["digest"])
    except SmokeFailure as exc:
        report.update(success=False, error=str(exc))
    finally:
        if service.session is not None:
            service.call_tool(
                "ce.process", {"action": "detach", "expectedGeneration": service.session.generation},
            )
        client.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("success"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
