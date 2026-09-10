import json
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

    def test_tool_descriptions_preserve_source_guards_and_remaining_caveats(self) -> None:
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
                r"native single-region queries", r"module filters restrict query ranges",
                r"names.*only.*returned rows", r"8192-query budget", r"cursor.*mappings change",
                r"native call can still block", r"bridge loss.*stop.*ce.status.*retrying",
            ),
            "ce.disassembly": (
                r"corrected lua.*mapping.*opcode reliable.*current code",
                r"heuristic.*first ret.*bound.*not.*exact function boundary",
                r"bytelimit.*lazy.*reads", r"next instruction.*read beyond.*budget",
            ),
            "ce.debug_control": (
                r"windows or veh debugger", r"omitting interface.*configured default",
                r"adopts.*matching active", r"never resumes or detaches.*adopted",
                r"native stopped truth.*refreshed.*each status/session.*guard",
                r"stopgeneration.*current native waiting context.*not.*old snapshot.*lifetime",
                r"pause.*process suspension.*only run.*no register context",
                r"not proof.*all architectures.*call variants",
                r"cleanup.*available", r"remove owned breakpoints",
            ),
            "ce.registers": (
                r"windows debugger.*guards", r"native stopped truth.*refreshed",
                r"stopgeneration.*match.*current native waiting context",
                r"reject processsuspended.*debug_getcontext.*other than literal true",
                r"old.*snapshots.*do not.*lifetime.*recheck",
                r"do not imply every architecture.*call variant.*proven", r"status.*detach cleanup.*available",
            ),
            "ce.breakpoints": (
                r"windows or veh.*breakpoints", r"per-breakpoint callback.*1.*global callback.*0.*valid",
                r"hits.*history.*not current stop context.*fresh status.*stopgeneration",
                r"remove.*logical.*handle.*native disable.*ce breakpoint id", r"never.*shared address",
                r"native list deletion.*deferred",
                r"do not repeat removal.*native list presence", r"only owned.*release",
            ),
            "ce.memory_read": (
                r"raw encoding.*hex.*base64.*conversion.*sidecar",
                r"maxstringbytes.*strict byte budget.*terminator", r"wide reads.*whole 2-byte units",
                r"string complete.*true only.*terminator.*observed.*budget",
                r"limit without one.*incomplete",
            ),
            "ce.debug_events": (
                r"generation-bound.*sequence cursors", r"reuse nextcursor.*truncated is false.*poll",
                r"droppedevents.*retention gap", r"not a complete execution history",
                r"events.*do not prove.*still stopped",
            ),
            "ce.structures": (r"get.*revision.*before update or delete", r"stale.*rejected", r"fetch and review"),
            "ce.artifacts": (r"complete:false.*partial.*not.*failed", r"different target sessions", r"confirm.*artifactid.*sessionid"),
        }
        tools = {tool.name: tool for tool in build_tool_list(self.service)}
        for name, patterns in caveats.items():
            with self.subTest(tool=name):
                description = (tools[name].description or "").lower()
                for pattern in patterns:
                    self.assertRegex(description, pattern)

    def test_fixed_metadata_does_not_reintroduce_obsolete_warnings(self) -> None:
        obsolete = {
            "ce.debug_control": (r"avoid.*(?:start|pause|continue|resume|step)", r"cached", r"pending native"),
            "ce.breakpoints": (r"avoid new breakpoint", r"pending native callback verification"),
            "ce.registers": (r"cached", r"freshness.*unverified", r"do not.*step destinations"),
            "ce.disassembly": (r"opcode.*unreliable", r"fallback evidence"),
            "ce.memory_map": (r"naming of all regions.*before pag", r"reproduced.*bridge_unavailable"),
            "ce.memory_read": (r"context", r"stopgeneration", r"debugger"),
        }
        tools = {tool.name: tool for tool in build_tool_list(self.service)}
        for name, patterns in obsolete.items():
            tool = tools[name]
            metadata = json.dumps([tool.description, tool.input_schema]).lower()
            for pattern in patterns:
                with self.subTest(tool=name, obsolete=pattern):
                    self.assertNotRegex(metadata, pattern)

    def test_input_descriptions_put_caveats_at_decision_points(self) -> None:
        cases = (
            ("ce.scan", "refine", "action", (r"poll.*completed", r"never refine.*aob")),
            ("ce.signature", "start", "address", (r"inside.*rangestart/rangeend", r"only.*range")),
            ("ce.operations", "cancel", "action", (r"inspect.*state", r"completed.*not cancelled")),
            ("ce.pointer", "validate", "target", (r"finaladdress", r"not finalpointer.*extra.*read")),
            ("ce.memory_map", None, "limit", (
                r"returned rows.*not inspected regions", r"capped at 8192",
                r"native calls.*no latency guarantee",
            )),
            ("ce.memory_map", None, "moduleFilter", (r"query only merged matching module ranges", r"rather than enumerate.*full")),
            ("ce.disassembly", "function", "detail", (r"heuristic.*first ret.*bound.*not exact", r"completeness does not prove.*all control-flow")),
            ("ce.disassembly", "list", "byteLimit", (r"returned instruction bytes.*lazy reads", r"next instruction.*read.*exceeds.*budget.*omitted")),
            ("ce.debug_control", "pause", "action", (r"suspend.*not.*debugger thread stop", r"no register context.*only.*mode=run")),
            ("ce.debug_control", "continue", "mode", (
                r"co_run.*co_stepinto.*co_stepover.*fresh native stop.*generation checks",
                r"process suspension.*only run.*not stepping or register context",
            )),
            ("ce.debug_control", "continue", "expectedStopGeneration", (
                r"match.*current stopgeneration.*native waiting context rechecked",
                r"old snapshot does not.*lifetime", r"suspension.*only run/unpause.*not context or stepping",
            )),
            ("ce.registers", "read", "expectedStopGeneration", (
                r"match.*current stopgeneration.*native waiting context",
                r"reject processsuspended.*debug_getcontext.*other than literal true",
                r"old snapshot does not.*lifetime",
            )),
            ("ce.breakpoints", "remove", "action", (r"only.*owned.*logical.*request native disable", r"deferred.*do not repeat removal.*native list")),
            ("ce.memory_read", "raw", "encoding", (r"hex.*default.*base64.*sidecar converts.*base64 when requested",)),
            ("ce.memory_read", "typed", "maxStringBytes", (
                r"strict byte budget.*terminator.*not a character count",
                r"whole 2-byte units.*odd final byte unread", r"complete.*true only.*terminator.*observed.*budget",
            )),
            ("ce.debug_events", "list", "cursor", (
                r"nextcursor unchanged.*not a numeric ring index",
                r"last delivered event.*generation", r"droppedevents.*rollover gap",
            )),
            ("ce.structures", "update", "expectedRevision", (r"revision from get", r"stale.*review")),
            ("ce.structures", "delete", "expectedRevision", (r"revision from get", r"stale.*review")),
            ("ce.artifacts", "preview", "action", (r"complete:false.*partial.*not.*failed",)),
            ("ce.artifacts", "delete", "action", (r"irreversibly", r"confirm.*artifactid.*sessionid")),
        )
        tools = {tool.name: tool for tool in build_tool_list(self.service)}
        for name, action, property_name, patterns in cases:
            with self.subTest(tool=name, action=action, property=property_name):
                schema = tools[name].input_schema
                if action is not None:
                    schema = next(
                        branch for branch in schema["oneOf"]
                        if branch["properties"].get("action", branch["properties"].get("mode", {})).get("const") == action
                    )
                description = schema["properties"][property_name].get("description", "").lower()
                for pattern in patterns:
                    self.assertRegex(description, pattern)
