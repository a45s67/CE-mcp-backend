import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

try:
    import mcp.types as types
    from ce_mcp.mcp_adapter import build_tool_list, create_mcp_server, invoke_tool
    from ce_mcp.mcp_server import StaticTokenVerifier, create_http_app, load_http_token
    from mcp.server.lowlevel.server import NotificationOptions
except ModuleNotFoundError:
    types = None

from ce_mcp.fake_bridge import FakeBridge
from ce_mcp.service import BackendService


ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = ROOT / "ce_mcp" / "contracts" / "v1" / "tools"


@unittest.skipIf(types is None, "official MCP SDK is not installed")
class McpAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = FakeBridge()
        self.service = BackendService(self.bridge, TOOL_DIR)

    def test_list_tools_preserves_checked_in_schemas_and_annotations(self) -> None:
        tools = build_tool_list(self.service)
        self.assertEqual([tool.name for tool in tools], sorted(tool.name for tool in tools))
        status = next(tool for tool in tools if tool.name == "ce.status")
        self.assertEqual(status.input_schema["additionalProperties"], False)
        self.assertTrue(status.annotations.read_only_hint)
        self.assertFalse(status.annotations.open_world_hint)
        self.assertIsNotNone(status.output_schema)

    def test_call_tool_returns_structured_content_and_error_flag(self) -> None:
        self.bridge.register(
            "status.get",
            lambda params: {
                "bridge": {"connected": True, "version": "0.1.0"},
                "capabilities": {
                    "available": [],
                    "enabled": [],
                    "disabledReasons": {},
                    "limits": {},
                },
            },
        )
        success = asyncio.run(invoke_tool(self.service, "ce.status", {}))
        failure = asyncio.run(invoke_tool(self.service, "ce.unknown", {}))
        self.assertFalse(success.is_error)
        self.assertIn("backend", success.structured_content)
        self.assertEqual(json.loads(success.content[0].text), success.structured_content)
        self.assertTrue(failure.is_error)
        self.assertEqual(failure.structured_content["error"]["code"], "METHOD_NOT_FOUND")

    def test_server_advertises_tool_capability(self) -> None:
        server = create_mcp_server(self.service)
        capabilities = server.get_capabilities(NotificationOptions(), {})
        self.assertIsNotNone(capabilities.tools)

    def test_static_token_verifier_uses_exact_token(self) -> None:
        token = "a" * 32
        verifier = StaticTokenVerifier(token)
        accepted = asyncio.run(verifier.verify_token(token))
        rejected = asyncio.run(verifier.verify_token("b" * 32))
        self.assertIsNotNone(accepted)
        self.assertIsNone(rejected)
        with self.assertRaises(ValueError):
            StaticTokenVerifier("short")

    def test_http_app_rejects_remote_bind_and_missing_token(self) -> None:
        from starlette.testclient import TestClient

        token = "a" * 32
        with self.assertRaises(ValueError):
            create_http_app(self.service, "0.0.0.0", 8001, token)
        app = create_http_app(self.service, "127.0.0.1", 8001, token)
        with TestClient(app, base_url="http://127.0.0.1:8001") as client:
            missing = client.post("/mcp", json={})
            invalid = client.post(
                "/mcp", json={}, headers={"Authorization": "Bearer " + "b" * 32}
            )
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(invalid.status_code, 401)

    def test_http_health_distinguishes_liveness_auth_and_bridge_readiness(self) -> None:
        from starlette.testclient import TestClient

        token = "a" * 32
        app = create_http_app(self.service, "127.0.0.1", 8001, token)
        with TestClient(app, base_url="http://127.0.0.1:8001") as client:
            self.assertEqual(client.get("/health/live").json(), {"status": "ok"})
            unauthenticated = client.get("/health/ready")
            unavailable = client.get(
                "/health/ready", headers={"Authorization": f"Bearer {token}"}
            )
            self.bridge.register(
                "status.get",
                lambda params: {
                    "bridge": {"connected": True, "version": "0.1.0"},
                    "capabilities": {
                        "available": [],
                        "enabled": [],
                        "disabledReasons": {},
                        "limits": {},
                    },
                },
            )
            ready = client.get(
                "/health/ready", headers={"Authorization": f"Bearer {token}"}
            )
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(unauthenticated.headers["WWW-Authenticate"], "Bearer")
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.json()["diagnostic_code"], "METHOD_NOT_FOUND")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["status"], "ready")

    def test_streamable_http_initializes_and_lists_tools(self) -> None:
        from starlette.testclient import TestClient

        token = "a" * 32
        app = create_http_app(self.service, "127.0.0.1", 8001, token)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        with TestClient(app, base_url="http://127.0.0.1:8001") as client:
            initialized = client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "ce-http-test", "version": "1"},
                    },
                },
            )
            listed = client.post(
                "/mcp",
                headers={**headers, "MCP-Protocol-Version": "2025-06-18"},
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
        self.assertEqual(initialized.status_code, 200)
        self.assertEqual(initialized.json()["result"]["serverInfo"]["name"], "ce-mcp-backend")
        self.assertEqual(listed.status_code, 200)
        names = [tool["name"] for tool in listed.json()["result"]["tools"]]
        self.assertIn("ce.status", names)

    def test_http_token_file_loading(self) -> None:
        token = "a" * 32
        with TemporaryDirectory(dir=ROOT) as directory:
            token_file = Path(directory) / "http.token"
            token_file.write_text(token + "\n", encoding="utf-8")
            self.assertEqual(load_http_token(token_file), token)
        with patch.dict(os.environ, {"CE_MCP_TOKEN": "b" * 32}):
            with self.assertRaisesRegex(ValueError, "--token-file is required"):
                load_http_token(None)


if __name__ == "__main__":
    unittest.main()
