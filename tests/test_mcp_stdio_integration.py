import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

try:
    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
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
                    called = await session.call_tool("ce.status", {})
                    self.assertTrue(called.is_error)
                    self.assertEqual(
                        called.structured_content["error"]["code"],
                        "BRIDGE_UNAVAILABLE",
                    )
        with TemporaryDirectory(dir=ROOT) as temporary:
            anyio.run(scenario, Path(temporary))


if __name__ == "__main__":
    unittest.main()
