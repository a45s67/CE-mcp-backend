import json
from pathlib import Path
import tempfile
import unittest

from ce_mcp.audit import JsonlAuditLog
from ce_mcp.fake_bridge import FakeBridge
from ce_mcp.service import BackendService


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "ce_mcp" / "contracts" / "v1" / "tools"


class AuditTests(unittest.TestCase):
    def test_metadata_is_correlated_and_request_arguments_are_never_logged(self) -> None:
        bridge = FakeBridge()
        bridge.register("process.list", lambda _: {"items": [], "truncated": False})
        with tempfile.TemporaryDirectory() as directory:
            audit = JsonlAuditLog(Path(directory), max_bytes=4096, retained_files=2)
            service = BackendService(bridge, CONTRACTS, audit_sink=audit)
            outcome = service.call_tool("ce.process", {
                "action": "list", "nameFilter": "SECRET-memory-sample", "limit": 5,
            })
            self.assertIsNone(outcome.error)
            lines = (Path(directory) / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        events = [json.loads(line) for line in lines]
        self.assertEqual([event["phase"] for event in events], ["accepted", "completed"])
        self.assertEqual(events[0]["requestId"], events[1]["requestId"])
        self.assertEqual(events[0]["requestId"], bridge.calls[0].request_id)
        self.assertNotIn("SECRET-memory-sample", "\n".join(lines))
        self.assertIn("durationMs", events[1])

    def test_mutation_is_marked_before_bridge_call(self) -> None:
        class ObservingBridge(FakeBridge):
            def __init__(self, log_path: Path) -> None:
                super().__init__()
                self.log_path = log_path

            def call(self, request):
                accepted = json.loads(self.log_path.read_text(encoding="utf-8").splitlines()[-1])
                if accepted["phase"] != "accepted" or not accepted["mutation"]:
                    raise AssertionError("mutation was not audited before bridge execution")
                return super().call(request)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge = ObservingBridge(root / "audit.jsonl")
            bridge.register("process.attach", lambda _: {"session": {
                "sessionId": "ce-01jabcdef", "generation": 1, "state": "running", "pid": 99,
                "architecture": "x86_64", "pointerWidth": 64,
            }})
            service = BackendService(bridge, CONTRACTS, audit_sink=JsonlAuditLog(root, max_bytes=4096))
            outcome = service.call_tool("ce.process", {"action": "attach", "pid": 99})
            self.assertIsNone(outcome.error)


if __name__ == "__main__":
    unittest.main()
