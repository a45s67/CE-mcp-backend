import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

try:
    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.types import CallToolResult, TextContent
    from ce_mcp.mcp_live_smoke import McpLiveSmokeFailure, _value
except ModuleNotFoundError:
    anyio = None


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(anyio is None, "official MCP SDK is not installed")
class McpStdioIntegrationTests(unittest.TestCase):
    def test_initialize_list_and_offline_status_call(self) -> None:
        async def scenario(audit_root: Path) -> None:
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m",
                    "ce_mcp.mcp_server",
                    "--transport",
                    "stdio",
                    "--deadline-ms",
                    "50",
                    "--audit-root",
                    str(audit_root),
                    "--artifact-root",
                    str(audit_root / "artifacts"),
                    "--pipe",
                    r"\\.\pipe\CE_MCP_Backend_test_intentionally_absent",
                ],
                cwd=ROOT,
                env=dict(os.environ),
            )
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    initialized = await session.initialize()
                    self.assertEqual(initialized.server_info.name, "ce-mcp-backend")
                    listed = await session.list_tools()
                    names = [tool.name for tool in listed.tools]
                    self.assertEqual(names, sorted(names))
                    self.assertIn("ce.status", names)
                    for tool in listed.tools:
                        self.assertIsNone(tool.output_schema)
                        self.assertNotIn("outputSchema", tool.model_dump(by_alias=True, exclude_none=True))
                    called = await session.call_tool("ce.status", {})
                    self.assertTrue(called.is_error)
                    self.assertIsNone(called.structured_content)
                    self.assertNotIn("structuredContent", called.model_dump(by_alias=True, exclude_none=True))
                    self.assertEqual(len(called.content), 1)
                    self.assertEqual(called.content[0].type, "text")
                    payload = json.loads(called.content[0].text)
                    self.assertEqual(set(payload), {"error"})
                    self.assertEqual(
                        payload["error"]["code"],
                        "BRIDGE_UNAVAILABLE",
                    )
                    no_target = await session.call_tool("ce.process", {"action": "get"})
                    self.assertTrue(no_target.is_error)
                    self.assertIsNone(no_target.structured_content)
                    self.assertEqual(
                        json.loads(no_target.content[0].text)["error"]["code"], "NO_TARGET",
                    )
        with TemporaryDirectory() as temporary:
            anyio.run(scenario, Path(temporary))


@unittest.skipIf(anyio is None, "official MCP SDK is not installed")
class McpSmokeDecoderTests(unittest.TestCase):
    def test_direct_success_and_error(self) -> None:
        result = CallToolResult(content=[TextContent(type="text", text='{"items":[]}')])
        self.assertEqual(_value(result, "ce.process"), {"items": []})
        result = CallToolResult(isError=True, content=[TextContent(
            type="text", text='{"error":{"code":"BRIDGE_UNAVAILABLE","message":"offline"}}',
        )])
        with self.assertRaisesRegex(McpLiveSmokeFailure, "BRIDGE_UNAVAILABLE: offline"):
            _value(result, "ce.status")

    def test_rejects_old_or_malformed_results(self) -> None:
        text = TextContent(type="text", text='{}')
        cases = [
            CallToolResult(content=[]),
            CallToolResult(content=[text, text]),
            CallToolResult(content=[text], structuredContent={}),
            CallToolResult(content=[{"type": "image", "data": "", "mimeType": "image/png"}]),
            CallToolResult(content=[text], isError=True),
        ]
        cases.extend(CallToolResult(content=[TextContent(type="text", text=value)])
                     for value in ('summary', '[]', 'null', '1', '{"error":{}}'))
        for result in cases:
            with self.subTest(result=result), self.assertRaises(McpLiveSmokeFailure):
                _value(result, "ce.status")


if __name__ == "__main__":
    unittest.main()
