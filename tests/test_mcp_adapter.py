import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

try:
    import mcp.types as types
    from ce_mcp.mcp_adapter import (
        _bounded_result, build_tool_list, create_mcp_server, invoke_tool,
    )
    from ce_mcp.mcp_server import StaticTokenVerifier, create_http_app, load_http_token
    from mcp.server.lowlevel.server import NotificationOptions
except ModuleNotFoundError:
    types = None

from ce_mcp.fake_bridge import FakeBridge
from ce_mcp.models import ContractViolation, ErrorDetail, NextAction
from ce_mcp.service import BackendService, ToolOutcome


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
        self.assertEqual(success.content[0].text, "ce.status completed: bridgeConnected=true.")
        self.assertLess(len(success.content[0].text), 256)
        self.assertTrue(failure.is_error)
        self.assertEqual(failure.structured_content["error"]["code"], "METHOD_NOT_FOUND")

    def test_output_limit_returns_actionable_error_for_safe_paged_read(self) -> None:
        service = BackendService(self.bridge, TOOL_DIR, max_output_bytes=4096)
        result = _bounded_result(
            service,
            "ce.process",
            {"action": "list", "limit": 200, "nameFilter": "sample"},
            ToolOutcome(result={"items": [{"value": "x" * 512}] * 20}),
        )
        self.assertTrue(result.is_error)
        error = result.structured_content["error"]
        self.assertEqual(error["code"], "OUTPUT_LIMIT_EXCEEDED")
        self.assertTrue(error["safeToRetry"])
        self.assertEqual(error["details"]["limitBytes"], 4096)
        self.assertGreater(error["details"]["actualBytes"], 4096)
        self.assertIn("4096-byte limit", error["message"])
        action = error["nextActions"][0]
        self.assertEqual(action["tool"], "ce.process")
        self.assertEqual(action["argumentsPatch"], {"limit": 100})
        self.assertEqual(action["execution"], "suggested")
        self.assertEqual(error["adviceSource"], "ce-mcp-backend")

    def test_output_limit_never_recommends_replaying_a_mutation(self) -> None:
        service = BackendService(self.bridge, TOOL_DIR, max_output_bytes=4096)
        result = _bounded_result(
            service,
            "ce.process",
            {"action": "attach", "pid": 42},
            ToolOutcome(result={"value": "x" * 5000}),
        )
        error = result.structured_content["error"]
        self.assertFalse(error["safeToRetry"])
        self.assertEqual(error["details"]["outcome"], "completed_response_not_returned")
        self.assertEqual(error["nextActions"][0]["tool"], "ce.status")
        self.assertEqual(error["nextActions"][0]["execution"], "required_before_retry")

    def test_next_action_tool_and_arguments_are_validated_against_catalog(self) -> None:
        outcome = ToolOutcome(error=ErrorDetail(
            "TEST_ERROR", "test", True, False,
            next_actions=(NextAction(
                "BAD_RECOVERY", "suggested", "Invalid test recovery.",
                tool="ce.missing", arguments={},
            ),),
        ))
        with self.assertRaisesRegex(ContractViolation, "unknown tool"):
            _bounded_result(self.service, "ce.status", {}, outcome)

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
            self.assertEqual(load_http_token(None), "b" * 32)
            self.assertEqual(load_http_token(token_file), "b" * 32)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "CE_MCP_TOKEN or --token-file is required"):
                load_http_token(None)


if __name__ == "__main__":
    unittest.main()
