from pathlib import Path
import unittest

from ce_mcp.fake_bridge import FakeBridge
from ce_mcp.mcp_adapter import build_tool_list, create_mcp_server
from ce_mcp.service import BackendService


ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = ROOT / "ce_mcp" / "contracts" / "v1" / "tools"


class McpMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = BackendService(FakeBridge(), TOOL_DIR)

    def test_server_instructions_preserve_cross_tool_safety_workflow(self) -> None:
        instructions = create_mcp_server(self.service).instructions
        self.assertIsNotNone(instructions)
        assert instructions is not None
        for required in (
            "ce.status", "attach", "session generation", "stop generation",
            "OUTCOME_UNKNOWN", "cleanup", "DBK", "DBVM",
        ):
            self.assertIn(required, instructions)

    def test_every_tool_has_actionable_bounded_description(self) -> None:
        for tool in build_tool_list(self.service):
            self.assertIsNotNone(tool.description, tool.name)
            assert tool.description is not None
            self.assertGreaterEqual(len(tool.description), 60, tool.name)
            self.assertLessEqual(len(tool.description), 512, tool.name)
