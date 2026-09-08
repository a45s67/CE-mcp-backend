import json
from pathlib import Path
import unittest

try:
    from starlette.testclient import TestClient
    from ce_mcp.mcp_server import create_http_app
except ModuleNotFoundError:
    TestClient = None

from ce_mcp.fake_bridge import FakeBridge
from ce_mcp.service import BackendService


ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = ROOT / "ce_mcp" / "contracts" / "v1" / "tools"
FIXTURES = ROOT / "contracts" / "mcp"


@unittest.skipIf(TestClient is None, "HTTP MCP test dependencies are unavailable")
class McpWireContractTests(unittest.TestCase):
    def assert_content_only_payload(self, result: dict) -> dict:
        self.assertNotIn("structuredContent", result)
        self.assertEqual(len(result["content"]), 1)
        block = result["content"][0]
        self.assertEqual(block["type"], "text")
        payload = json.loads(block["text"])
        self.assertIsInstance(payload, dict)
        self.assertEqual(
            block["text"], json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        return payload

    def test_status_call_matches_checked_in_json_rpc_golden(self) -> None:
        bridge = FakeBridge()
        bridge.register("status.get", lambda params: {
            "bridge": {"connected": True, "version": "0.1.0"},
            "capabilities": {
                "available": [], "enabled": [], "disabledReasons": {}, "limits": {},
            },
        })
        service = BackendService(bridge, TOOL_DIR)
        app = create_http_app(service, "127.0.0.1", 8001, "a" * 32)
        headers = {
            "Authorization": "Bearer " + "a" * 32,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        }
        request = json.loads((FIXTURES / "status-success.request.json").read_text())
        expected = json.loads((FIXTURES / "status-success.response.json").read_text())
        with TestClient(app, base_url="http://127.0.0.1:8001") as client:
            response = client.post("/mcp", headers=headers, json=request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        payload = self.assert_content_only_payload(response.json()["result"])
        self.assertIn("backend", payload)
        self.assertNotIn("result", payload)

    def test_tools_list_does_not_advertise_output_schema(self) -> None:
        service = BackendService(FakeBridge(), TOOL_DIR)
        app = create_http_app(service, "127.0.0.1", 8001, "a" * 32)
        headers = {
            "Authorization": "Bearer " + "a" * 32,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        }
        with TestClient(app, base_url="http://127.0.0.1:8001") as client:
            response = client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "id": 11, "method": "tools/list", "params": {},
            })
        self.assertEqual(response.status_code, 200)
        tools = response.json()["result"]["tools"]
        self.assertTrue(tools)
        for tool in tools:
            with self.subTest(tool=tool["name"]):
                self.assertIn("inputSchema", tool)
                self.assertNotIn("outputSchema", tool)

    def test_malformed_json_returns_bounded_protocol_error(self) -> None:
        service = BackendService(FakeBridge(), TOOL_DIR)
        app = create_http_app(service, "127.0.0.1", 8001, "a" * 32)
        headers = {
            "Authorization": "Bearer " + "a" * 32,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        with TestClient(app, base_url="http://127.0.0.1:8001") as client:
            response = client.post("/mcp", headers=headers, content=b"{not-json")
        self.assertLess(len(response.content), 4096)
        self.assertIn(response.status_code, {400, 422})

    def test_http_tool_result_limit_returns_valid_json_rpc_error(self) -> None:
        bridge = FakeBridge()
        bridge.register(
            "process.list",
            lambda params: {
                "items": [{"description": "x" * 512}] * 20,
                "truncated": False,
            },
        )
        service = BackendService(bridge, TOOL_DIR, max_output_bytes=4096)
        app = create_http_app(service, "127.0.0.1", 8001, "a" * 32)
        headers = {
            "Authorization": "Bearer " + "a" * 32,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        }
        request = {
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {
                "name": "ce.process",
                "arguments": {"action": "list", "limit": 200},
            },
        }
        with TestClient(app, base_url="http://127.0.0.1:8001") as client:
            response = client.post("/mcp", headers=headers, json=request)
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertTrue(result["isError"])
        payload = self.assert_content_only_payload(result)
        self.assertEqual(set(payload), {"error"})
        error = payload["error"]
        self.assertEqual(error["code"], "OUTPUT_LIMIT_EXCEEDED")
        self.assertEqual(error["details"]["limitBytes"], 4096)
        self.assertEqual(error["nextActions"][0]["argumentsPatch"], {"limit": 100})
        self.assertLess(len(json.dumps(result).encode("utf-8")), 4096)


if __name__ == "__main__":
    unittest.main()
