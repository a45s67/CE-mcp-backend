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

    def test_tool_descriptions_preserve_live_failure_caveats(self) -> None:
        # Check safety concepts in published metadata, not exact prose or transport success.
        caveats = {
            "ce.scan": (r"aob.*immutable", r"numeric.*poll.*complet", r"close.*handle"),
            "ce.signature": (
                r"explicit.*range.*contain.*address", r"uniqueness.*only.*range",
                r"poll.*complet.*close",
            ),
            "ce.operations": (r"cancel.*inspect.*state", r"completed.*not cancelled", r"no-op"),
            "ce.pointer": (r"finaladdress.*location", r"finalpointer.*extra.*read", r"validate.*finaladdress"),
            "ce.memory_map": (
                r"pagination.*output only", r"enumeration.*before pag",
                r"bridge_unavailable.*reproduced", r"avoid repeated.*ce.status",
            ),
            "ce.disassembly": (r"heuristic.*not.*exact", r"opcode.*unreliable", r"display.*bytes.*fallback"),
            "ce.debug_control": (
                r"state.*unreliable.*native callback", r"avoid start.*pause.*continue.*pending native",
                r"detach cleanup.*available", r"remove owned breakpoints",
            ),
            "ce.registers": (
                r"cached.*generation", r"freshness.*unverified", r"matching.*does not prove",
                r"do not.*step destinations",
            ),
            "ce.breakpoints": (r"hits.*do not prove.*native stop", r"list/remove cleanup.*available", r"only owned"),
            "ce.debug_events": (r"do not prove.*native stop", r"cursor.*rollover.*unverified", r"events may be missed"),
            "ce.structures": (r"get.*revision.*before update or delete", r"stale.*rejected", r"fetch and review"),
            "ce.artifacts": (r"complete:false.*partial.*not.*failed", r"delete only.*own"),
        }
        tools = {tool.name: tool for tool in build_tool_list(self.service)}
        for name, patterns in caveats.items():
            with self.subTest(tool=name):
                description = (tools[name].description or "").lower()
                for pattern in patterns:
                    self.assertRegex(description, pattern)
        self.assertNotRegex(tools["ce.debug_control"].description.lower(), r"probe[- ]verified")

    def test_input_descriptions_put_caveats_at_decision_points(self) -> None:
        cases = (
            ("ce.scan", "refine", "action", (r"poll.*completed", r"never refine.*aob")),
            ("ce.signature", "start", "address", (r"inside.*rangestart/rangeend", r"only.*range")),
            ("ce.operations", "cancel", "action", (r"inspect.*state", r"completed.*not cancelled")),
            ("ce.pointer", "validate", "target", (r"finaladdress", r"not finalpointer.*extra.*read")),
            ("ce.memory_map", None, "limit", (r"output.*only.*not enumeration", r"bridge_unavailable")),
            ("ce.disassembly", "function", "detail", (r"heuristic.*not exact",)),
            ("ce.debug_control", "continue", "mode", (r"avoid all.*pending native",)),
            ("ce.registers", "read", "expectedStopGeneration", (r"cached", r"does not verify.*freshness")),
            ("ce.debug_events", "list", "cursor", (r"rollover.*unverified", r"miss events")),
            ("ce.structures", "update", "expectedRevision", (r"revision from get", r"stale.*review")),
            ("ce.structures", "delete", "expectedRevision", (r"revision from get", r"stale.*review")),
            ("ce.artifacts", "preview", "action", (r"complete:false.*partial.*not.*failed",)),
            ("ce.artifacts", "delete", "action", (r"only.*own",)),
        )
        tools = {tool.name: tool for tool in build_tool_list(self.service)}
        for name, action, property_name, patterns in cases:
            with self.subTest(tool=name, action=action, property=property_name):
                schema = tools[name].input_schema
                if action is not None:
                    schema = next(
                        branch for branch in schema["oneOf"]
                        if branch["properties"]["action"]["const"] == action
                    )
                description = schema["properties"][property_name].get("description", "").lower()
                for pattern in patterns:
                    self.assertRegex(description, pattern)
