"""Real-CE vertical gate for sidecar structure workspace and target reads."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .analysis_smoke import _read_info
from .e2e_smoke import SmokeFailure, _result
from .service import BackendService
from .transport import DEFAULT_PIPE_NAME, WindowsNamedPipeBridgeClient


FIELDS = [
    {"name": "tag", "offset": 0, "type": "bytes", "size": 4},
    {"name": "flags", "offset": 4, "type": "u32"},
    {"name": "counter", "offset": 8, "type": "u64"},
    {"name": "ratio", "offset": 16, "type": "f32"},
    {"name": "name", "offset": 20, "type": "string", "size": 12},
    {"name": "pointer", "offset": 32, "type": "pointer"},
]


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ce-mcp-structure-smoke")
    parser.add_argument("--info", type=Path, required=True)
    parser.add_argument("--pipe", default=DEFAULT_PIPE_NAME)
    options = parser.parse_args(argv)
    info = _read_info(options.info)
    target_pid = int(info["pid"])
    client = WindowsNamedPipeBridgeClient(options.pipe)
    contracts = Path(__file__).resolve().parent / "contracts" / "v1" / "tools"
    service = BackendService(client, contracts)
    report: dict[str, object] = {"targetPid": target_pid}
    structure_id: str | None = None
    revision: int | None = None
    try:
        created = _result(service, "ce.structures", {
            "action": "create", "name": "ControlledLayout",
            "size": int(info["size"]), "fields": FIELDS,
        })["structure"]
        structure_id, revision = created["structureId"], created["revision"]
        listed = _result(service, "ce.structures", {"action": "list", "limit": 100})
        if [item["structureId"] for item in listed["items"]] != [structure_id]:
            raise SmokeFailure("workspace list did not retain the created definition")
        updated = _result(service, "ce.structures", {
            "action": "update", "structureId": structure_id,
            "expectedRevision": revision, "name": "ControlledLayoutV2",
            "size": int(info["size"]), "fields": FIELDS,
        })["structure"]
        revision = updated["revision"]
        attached = _result(service, "ce.process", {"action": "attach", "pid": target_pid})
        generation = attached["session"]["generation"]
        read = _result(service, "ce.structures", {
            "action": "read", "structureId": structure_id,
            "base": f"0x{info['base']}", "expectedGeneration": generation,
        })
        values = {item["name"]: item["value"] for item in read["values"]}
        expected_pointer = f"0x{int(info['pointer'], 16):016X}"
        if values["tag"] != "43454D43" or values["flags"] != 0xA1B2C3D4:
            raise SmokeFailure("byte or u32 fields did not match the controlled layout")
        if values["counter"] != "0x1122334455667788" or abs(values["ratio"] - 3.25) > 0.0001:
            raise SmokeFailure("u64 or float fields did not match the controlled layout")
        if values["name"] != "CE-MCP" or values["pointer"] != expected_pointer:
            raise SmokeFailure("string or pointer fields did not match the controlled layout")
        _result(service, "ce.process", {"action": "detach", "expectedGeneration": generation})
        _result(service, "ce.structures", {
            "action": "delete", "structureId": structure_id, "expectedRevision": revision,
        })
        structure_id = None
        report.update(success=True, revision=revision, fieldCount=len(values))
    except SmokeFailure as exc:
        report.update(success=False, error=str(exc))
    finally:
        if service.session is not None:
            service.call_tool("ce.process", {
                "action": "detach", "expectedGeneration": service.session.generation,
            })
        if structure_id is not None and revision is not None:
            service.call_tool("ce.structures", {
                "action": "delete", "structureId": structure_id, "expectedRevision": revision,
            })
        client.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("success"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
