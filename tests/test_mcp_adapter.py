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

    def test_list_tools_preserves_inputs_and_annotations_without_output_schema(self) -> None:
        tools = build_tool_list(self.service)
        self.assertEqual([tool.name for tool in tools], sorted(tool.name for tool in tools))
        status = next(tool for tool in tools if tool.name == "ce.status")
        self.assertEqual(status.input_schema["additionalProperties"], False)
        self.assertTrue(status.annotations.read_only_hint)
        self.assertFalse(status.annotations.open_world_hint)
        for tool, definition in zip(tools, self.service.catalog):
            self.assertEqual(tool.input_schema, definition["inputSchema"])
            self.assertIn("outputSchema", definition)
            self.assertNotIn("outputSchema", tool.model_dump(by_alias=True, exclude_none=True))

    def test_call_tool_returns_json_content_and_error_flag(self) -> None:
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
        self.assertIn("backend", json.loads(success.content[0].text))
        for result in (success, failure):
            self.assertEqual(len(result.content), 1)
            self.assertEqual(result.content[0].type, "text")
            self.assertNotIn("structuredContent", result.model_dump(by_alias=True, exclude_none=True))
        self.assertTrue(failure.is_error)
        self.assertEqual(json.loads(failure.content[0].text)["error"]["code"], "METHOD_NOT_FOUND")

    def test_output_limit_returns_actionable_error_for_safe_paged_read(self) -> None:
        service = BackendService(self.bridge, TOOL_DIR, max_output_bytes=4096)
        result = _bounded_result(
            service,
            "ce.process",
            {"action": "list", "limit": 200, "nameFilter": "sample"},
            ToolOutcome(result={"items": [{"value": "x" * 512}] * 20}),
        )
        self.assertTrue(result.is_error)
        error = json.loads(result.content[0].text)["error"]
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

    def test_target_generation_and_memory_bytes_are_visible_in_text(self) -> None:
        session = {
            "sessionId": "ce-01jabcdef", "generation": 7, "state": "running",
            "pid": 4242, "architecture": "x86_64", "pointerWidth": 64,
        }
        self.bridge.register("process.attach", lambda params: {"session": session})
        self.bridge.register("process.get", lambda params: {"session": session})
        memory = {
            "session": session, "resolvedAddress": {"address": "0x0000000000001234"},
            "bytes": "01020304", "encoding": "hex", "complete": True,
        }
        self.bridge.register("memory.read", lambda params: memory)
        for arguments in ({"action": "attach", "pid": 4242}, {"action": "get"}):
            result = asyncio.run(invoke_tool(self.service, "ce.process", arguments))
            self.assertFalse(result.is_error, result.content)
            payload = json.loads(result.content[0].text)
            self.assertEqual(payload["session"], session)
            self.assertEqual(payload["action"], arguments["action"])
        result = asyncio.run(invoke_tool(self.service, "ce.memory_read", {
            "mode": "raw", "address": "0x1234", "size": 4, "expectedGeneration": 7,
        }))
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(json.loads(result.content[0].text), memory)

    def test_process_get_without_target_preserves_original_error(self) -> None:
        result = asyncio.run(invoke_tool(self.service, "ce.process", {"action": "get"}))
        self.assertTrue(result.is_error)
        self.assertEqual(json.loads(result.content[0].text)["error"]["code"], "NO_TARGET")
        self.assertEqual(self.bridge.calls, [])

    def test_output_limit_counts_json_text_escaping_and_preserves_exact_boundary(self) -> None:
        outcome = ToolOutcome(result={"value": '\\"\n' * 3000})
        result = _bounded_result(self.service, "ce.status", {}, outcome)
        wire = result.model_dump(by_alias=True, exclude_none=True)
        size = len(json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        self.assertGreater(size, len(result.content[0].text.encode("utf-8")))
        for limit, expected_error in ((size, False), (size - 1, True)):
            service = BackendService(self.bridge, TOOL_DIR, max_output_bytes=limit)
            bounded = _bounded_result(service, "ce.status", {}, outcome)
            self.assertEqual(bounded.is_error, expected_error)
            payload = json.loads(bounded.content[0].text)
            if expected_error:
                self.assertEqual(payload["error"]["details"]["actualBytes"], size)
            else:
                self.assertEqual(payload, outcome.result)

    def test_output_limit_never_recommends_replaying_a_mutation(self) -> None:
        service = BackendService(self.bridge, TOOL_DIR, max_output_bytes=4096)
        result = _bounded_result(
            service,
            "ce.process",
            {"action": "attach", "pid": 42},
            ToolOutcome(result={"value": "x" * 5000}),
        )
        error = json.loads(result.content[0].text)["error"]
        self.assertFalse(error["safeToRetry"])
        self.assertEqual(error["details"]["outcome"], "completed_response_not_returned")
        self.assertEqual(error["nextActions"][0]["tool"], "ce.status")
        self.assertEqual(error["nextActions"][0]["execution"], "required_before_retry")

    def test_output_limit_does_not_increase_default_work_or_repeat_minimum(self) -> None:
        service = BackendService(self.bridge, TOOL_DIR, max_output_bytes=4096)
        outcome = ToolOutcome(result={"value": "x" * 5000})
        cases = [
            ("ce.memory_read", {"mode": "typed", "dataType": "u32", "address": "0x400000"}),
            ("ce.memory_read", {"mode": "raw", "address": "0x400000", "size": 1}),
            ("ce.process", {"action": "list", "limit": 1}),
        ]
        for name, arguments in cases:
            with self.subTest(name=name, arguments=arguments):
                result = _bounded_result(service, name, arguments, outcome)
                error = json.loads(result.content[0].text)["error"]
                self.assertFalse(error["safeToRetry"])
                self.assertFalse(error.get("nextActions"))
                self.assertIn("minimum", error["suggestedAction"])
        for name, arguments, field in (
            ("ce.process", {"action": "list"}, "limit"),
            ("ce.disassembly", {"action": "list", "address": "0x400000"}, "instructionCount"),
            ("ce.memory_read", {"mode": "typed", "address": "0x400000", "dataType": "string"}, "maxStringBytes"),
        ):
            with self.subTest(name=name, field=field):
                result = _bounded_result(service, name, arguments, outcome)
                error = json.loads(result.content[0].text)["error"]
                self.assertTrue(error["safeToRetry"])
                self.assertEqual(error["nextActions"][0]["argumentsPatch"], {field: 1})

    def test_oversized_mutation_error_does_not_claim_success(self) -> None:
        service = BackendService(self.bridge, TOOL_DIR, max_output_bytes=4096)
        result = _bounded_result(service, "ce.process", {"action": "attach", "pid": 42},
            ToolOutcome(error=ErrorDetail("OUTCOME_UNKNOWN", "Unobserved outcome", True, False,
                details={"diagnostic": "x" * 5000})))
        error = json.loads(result.content[0].text)["error"]
        self.assertFalse(error["safeToRetry"])
        self.assertEqual(error["details"]["outcome"], "error_response_not_returned")
        self.assertEqual(error["details"]["originalErrorCode"], "OUTCOME_UNKNOWN")
        self.assertNotIn("mutation completed", error["nextActions"][0]["reason"])

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
