from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest

from ce_mcp.cli import run
from ce_mcp.fake_bridge import FakeBridge


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "ce_mcp" / "contracts" / "v1" / "tools"


class CliTests(unittest.TestCase):
    def test_arguments_file_avoids_native_shell_json_quoting(self) -> None:
        import tempfile

        bridge = FakeBridge()
        bridge.register("process.list", lambda params: {"items": [], "truncated": False})
        with tempfile.TemporaryDirectory() as directory:
            arguments = Path(directory) / "arguments.json"
            arguments.write_text('{"action":"list","limit":5}', encoding="utf-8")
            with redirect_stdout(StringIO()):
                code = run(["ce.process", "--arguments-file", str(arguments)], bridge=bridge)
        self.assertEqual(code, 0)
        self.assertEqual(bridge.calls[-1].params["limit"], 5)

    def test_status_smoke_uses_service_and_prints_structured_result(self) -> None:
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
        output = StringIO()
        with redirect_stdout(output):
            code = run(
                ["ce.status", "{}", "--contracts", str(CONTRACTS)], bridge=bridge
            )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())["result"]["bridge"]["connected"])

    def test_invalid_json_and_non_object_arguments_fail_before_bridge(self) -> None:
        bridge = FakeBridge()
        for arguments in ("{", "[]"):
            output = StringIO()
            with redirect_stdout(output):
                code = run(
                    ["ce.status", arguments, "--contracts", str(CONTRACTS)], bridge=bridge
                )
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.getvalue())["error"]["code"], "INVALID_PARAMS")
        self.assertEqual(bridge.calls, [])


if __name__ == "__main__":
    unittest.main()
