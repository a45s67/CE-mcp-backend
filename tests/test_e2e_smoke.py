from pathlib import Path
import unittest

from ce_mcp.e2e_smoke import SmokeFailure, run_vertical_slice
from ce_mcp.fake_bridge import FakeBridge
from ce_mcp.service import BackendService


ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = ROOT / "ce_mcp" / "contracts" / "v1" / "tools"
SESSION = {
    "sessionId": "ce-01jabcdef",
    "generation": 7,
    "state": "running",
    "pid": 4242,
    "architecture": "x86_64",
    "pointerWidth": 64,
}


class E2ESmokeTests(unittest.TestCase):
    def make_service(self, mz="4D5A", detach_handler=None):
        bridge = FakeBridge()
        bridge.register(
            "status.get",
            lambda params: {
                "bridge": {"connected": True, "version": "0.1.0"},
                "capabilities": {
                    "available": ["memory.read"],
                    "enabled": ["memory.read"],
                    "disabledReasons": {},
                    "limits": {"maxReadBytes": 1048576},
                },
            },
        )
        bridge.register(
            "process.list", lambda params: {"items": [{"pid": 4242, "name": "sample.exe"}], "truncated": False}
        )
        bridge.register("process.attach", lambda params: {"session": SESSION})
        bridge.register(
            "process.detach",
            detach_handler or (lambda params: {"detached": True}),
        )
        bridge.register(
            "symbols.modules",
            lambda params: {
                "session": SESSION,
                "items": [
                    {
                        "name": "sample.exe",
                        "base": {"address": "0x0000000140000000"},
                        "size": 4096,
                    }
                ],
                "truncated": False,
            },
        )
        bridge.register(
            "memory.read",
            lambda params: {
                "session": SESSION,
                "resolvedAddress": {"address": "0x0000000140000000"},
                "bytes": mz,
                "encoding": "hex",
                "complete": True,
            },
        )
        bridge.register(
            "memory.map",
            lambda params: {"session": SESSION, "items": [{"name": "sample.exe"}], "truncated": False},
        )
        bridge.register(
            "symbols.resolve",
            lambda params: {"session": SESSION, "address": {"address": "0x0000000140000000"}},
        )
        bridge.register(
            "disassembly.instruction",
            lambda params: {"session": SESSION, "instruction": {"opcode": "dec ebp"}},
        )
        operation = {
            "operationId": "scan-00000007-00000001",
            "kind": "scan", "state": "completed", "generation": 7,
            "cancellable": False, "resultCount": 1,
        }
        bridge.register("scan.start", lambda params: {"session": SESSION, "operation": operation})
        bridge.register("scan.refine", lambda params: {"session": SESSION, "operation": operation})
        bridge.register(
            "scan.results",
            lambda params: {
                "session": SESSION, "operation": operation,
                "items": [{"address": {"address": "0x0000000140000000"}, "value": "4D 5A"}],
                "total": 1, "truncated": False,
            },
        )
        bridge.register("scan.close", lambda params: {"session": SESSION, "closed": True})
        return BackendService(bridge, TOOL_DIR)

    def test_vertical_slice_checks_every_phase1_read_path_and_detaches(self) -> None:
        service = self.make_service()
        report = run_vertical_slice(service, target_name="sample.exe")
        self.assertTrue(report["success"])
        self.assertIsNone(service.session)
        self.assertIn("result", report["detach"])
        self.assertEqual(
            [step["name"] for step in report["steps"]],
            ["status", "attach", "modules", "memory_read", "memory_map", "symbol_resolve", "disassembly"],
        )

    def test_rejects_wrong_module_magic_but_still_detaches(self) -> None:
        service = self.make_service(mz="0000")
        with self.assertRaises(SmokeFailure):
            run_vertical_slice(service, target_name="sample.exe")
        self.assertIsNone(service.session)

    def test_detach_failure_marks_report_unsuccessful(self) -> None:
        service = self.make_service(
            detach_handler=lambda params: {"unexpected": True}
        )
        report = run_vertical_slice(service, target_name="sample.exe")
        self.assertFalse(report["success"])
        self.assertIn("error", report["detach"])

    def test_optional_scan_slice_verifies_and_closes_operation(self) -> None:
        service = self.make_service()
        report = run_vertical_slice(service, target_name="sample.exe", include_scan=True)
        self.assertTrue(report["success"])
        self.assertEqual(report["steps"][-1]["name"], "scan")


if __name__ == "__main__":
    unittest.main()
