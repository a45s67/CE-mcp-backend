import json
from pathlib import Path
import unittest

from ce_mcp.catalog import load_catalog
from ce_mcp.models import ContractViolation
from ce_mcp.schema import validate
from ce_mcp.service import BackendService


ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = ROOT / "ce_mcp" / "contracts" / "v1" / "tools"


def load_tool(name: str) -> dict:
    with (TOOL_DIR / f"{name}.json").open(encoding="utf-8") as stream:
        return json.load(stream)


class CatalogTests(unittest.TestCase):
    def test_catalog_is_unique_and_deterministic(self) -> None:
        tools = load_catalog(TOOL_DIR)
        names = [tool["name"] for tool in tools]
        self.assertEqual(names, sorted(names))
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(
            names,
            [
                "ce.artifacts",
                "ce.breakpoints",
                "ce.dbvm_trace",
                "ce.dbvm_watch",
                "ce.debug_control",
                "ce.debug_events",
                "ce.disassembly",
                "ce.memory_analysis",
                "ce.memory_map",
                "ce.memory_read",
                "ce.operations",
                "ce.pointer",
                "ce.process",
                "ce.registers",
                "ce.scan",
                "ce.signature",
                "ce.status",
                "ce.structures",
                "ce.symbols",
                "ce.threads",
            ],
        )
        for tool in tools:
            self.assertIn("inputSchema", tool)
            self.assertIn("outputSchema", tool)
            self.assertIn("annotations", tool)

    def test_catalog_rejects_filename_name_mismatch(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrong.json"
            path.write_text(
                json.dumps(
                    {
                        "name": "ce.status",
                        "description": "test",
                        "inputSchema": {"type": "object"},
                        "outputSchema": {"type": "object"},
                        "annotations": {
                            "readOnlyHint": True,
                            "destructiveHint": False,
                            "idempotentHint": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ContractViolation):
                load_catalog(Path(directory))

    def test_action_contracts_match_service_routing(self) -> None:
        for name in (
            "ce.process", "ce.disassembly", "ce.symbols", "ce.scan", "ce.operations",
            "ce.pointer", "ce.debug_control", "ce.breakpoints", "ce.debug_events",
            "ce.threads", "ce.registers",
            "ce.memory_analysis",
            "ce.signature",
            "ce.dbvm_watch", "ce.dbvm_trace",
        ):
            schema = load_tool(name)["inputSchema"]
            actions = {
                branch["properties"]["action"]["const"]
                for branch in schema["oneOf"]
            }
            self.assertEqual(actions, set(BackendService._METHODS[name]), name)


class StatusContractTests(unittest.TestCase):
    def test_status_input_and_output_fixture(self) -> None:
        tool = load_tool("ce.status")
        validate(tool["inputSchema"], {})
        validate(
            tool["outputSchema"],
            {
                "backend": {"version": "0.1.0", "protocolVersion": 1},
                "bridge": {"connected": False},
                "capabilities": {
                    "available": ["memory.read"],
                    "enabled": ["memory.read"],
                    "disabledReasons": {},
                    "limits": {"maxReadBytes": 1048576},
                },
            },
        )

    def test_status_rejects_unknown_input(self) -> None:
        with self.assertRaises(ContractViolation):
            validate(load_tool("ce.status")["inputSchema"], {"loadDbvm": True})


class ProcessContractTests(unittest.TestCase):
    def test_each_action_has_unambiguous_schema(self) -> None:
        schema = load_tool("ce.process")["inputSchema"]
        for fixture in (
            {"action": "list", "limit": 50},
            {"action": "attach", "pid": 4242},
            {"action": "detach", "expectedGeneration": 7},
            {"action": "get"},
        ):
            validate(schema, fixture)

    def test_attach_requires_unambiguous_pid(self) -> None:
        schema = load_tool("ce.process")["inputSchema"]
        with self.assertRaises(ContractViolation):
            validate(schema, {"action": "attach", "name": "sample.exe"})


class MemoryReadContractTests(unittest.TestCase):
    def test_raw_and_typed_modes(self) -> None:
        schema = load_tool("ce.memory_read")["inputSchema"]
        validate(schema, {"mode": "raw", "address": "sample.exe+0x1234", "size": 4096})
        validate(
            schema,
            {
                "mode": "typed",
                "address": "sample.exe+0x1234",
                "dataType": "pointer",
                "followPointers": ["0x10", "0x20"],
                "expectedGeneration": 7,
            },
        )

    def test_read_limit_and_mode_specific_fields(self) -> None:
        schema = load_tool("ce.memory_read")["inputSchema"]
        with self.assertRaises(ContractViolation):
            validate(schema, {"mode": "raw", "address": "0x1000", "size": 1048577})
        with self.assertRaises(ContractViolation):
            validate(schema, {"mode": "raw", "address": "0x1000", "dataType": "u32"})


class MemoryAnalysisContractTests(unittest.TestCase):
    def test_compare_and_checksum_are_bounded(self) -> None:
        schema = load_tool("ce.memory_analysis")["inputSchema"]
        validate(schema, {"action": "compare", "leftAddress": "0x1000", "rightAddress": "0x2000", "size": 4096, "expectedGeneration": 7})
        validate(schema, {"action": "checksum", "address": "0x1000", "size": 4096, "algorithm": "md5", "expectedGeneration": 7})
        with self.assertRaises(ContractViolation):
            validate(schema, {"action": "checksum", "address": "0x1000", "size": 1048577, "expectedGeneration": 7})


class SignatureContractTests(unittest.TestCase):
    def test_explicit_range_and_bounded_candidate_lengths(self) -> None:
        schema = load_tool("ce.signature")["inputSchema"]
        validate(schema, {"action": "start", "address": "0x1800", "rangeStart": "0x1000", "rangeEnd": "0x2fff", "minBytes": 8, "maxBytes": 32, "expectedGeneration": 7})
        validate(schema, {"action": "result", "operationId": "sig-00000007-00000001", "expectedGeneration": 7})
        with self.assertRaises(ContractViolation):
            validate(schema, {"action": "start", "address": "0x1800", "expectedGeneration": 7})


class StructureContractTests(unittest.TestCase):
    def test_workspace_crud_and_target_read_are_distinct(self) -> None:
        schema = load_tool("ce.structures")["inputSchema"]
        fields = [{"name": "flags", "offset": 4, "type": "u32"}]
        validate(schema, {"action": "create", "name": "Header", "size": 16, "fields": fields})
        validate(schema, {"action": "read", "structureId": "struct-00000001", "base": "module+0x10", "expectedGeneration": 7})
        with self.assertRaises(ContractViolation):
            validate(schema, {"action": "read", "structureId": "struct-00000001", "base": "0x1000"})


class ScanContractTests(unittest.TestCase):
    def test_initial_refine_results_and_close_are_unambiguous(self) -> None:
        schema = load_tool("ce.scan")["inputSchema"]
        fixtures = (
            {"action": "start", "scanType": "exact", "valueType": "i32", "value": "42", "expectedGeneration": 7},
            {"action": "start", "scanType": "between", "valueType": "f32", "value": "1", "value2": "2", "expectedGeneration": 7},
            {"action": "start", "scanType": "unknown", "valueType": "u8", "expectedGeneration": 7},
            {"action": "refine", "operationId": "scan-00000007-00000001", "scanType": "changed", "expectedGeneration": 7},
            {"action": "results", "operationId": "scan-00000007-00000001", "limit": 200, "expectedGeneration": 7},
            {"action": "close", "operationId": "scan-00000007-00000001", "expectedGeneration": 7},
        )
        for fixture in fixtures:
            validate(schema, fixture)

    def test_scan_rejects_unbounded_page_and_missing_generation(self) -> None:
        schema = load_tool("ce.scan")["inputSchema"]
        with self.assertRaises(ContractViolation):
            validate(schema, {"action": "results", "operationId": "scan-00000007-00000001", "limit": 201, "expectedGeneration": 7})
        with self.assertRaises(ContractViolation):
            validate(schema, {"action": "start", "scanType": "exact", "valueType": "i32", "value": "42"})


class PointerContractTests(unittest.TestCase):
    def test_resolve_and_validate_are_bounded_and_unambiguous(self) -> None:
        schema = load_tool("ce.pointer")["inputSchema"]
        validate(
            schema,
            {"action": "resolve", "base": "sample.exe+0x20", "offsets": [16, -4], "expectedGeneration": 7},
        )
        validate(
            schema,
            {
                "action": "validate", "target": "0x1234",
                "chains": [{"base": "sample.exe+0x20", "offsets": [16, -4]}],
                "includeMisses": True, "expectedGeneration": 7,
            },
        )
        with self.assertRaises(ContractViolation):
            validate(
                schema,
                {"action": "validate", "target": "0x1234", "chains": [], "expectedGeneration": 7},
            )
        with self.assertRaises(ContractViolation):
            validate(
                schema,
                {
                    "action": "resolve", "base": "0x1000", "offsets": list(range(17)),
                    "expectedGeneration": 7,
                },
            )


class DebugContractTests(unittest.TestCase):
    def test_control_breakpoints_and_events_require_generations(self) -> None:
        validate(
            load_tool("ce.debug_control")["inputSchema"],
            {"action": "continue", "mode": "step_over", "expectedGeneration": 7, "expectedStopGeneration": 2},
        )
        validate(
            load_tool("ce.breakpoints")["inputSchema"],
            {"action": "set", "address": "sample.exe+0x10", "trigger": "write", "size": 4, "expectedGeneration": 7},
        )
        validate(
            load_tool("ce.debug_events")["inputSchema"],
            {"action": "list", "limit": 50, "expectedGeneration": 7},
        )
        with self.assertRaises(ContractViolation):
            validate(
                load_tool("ce.debug_control")["inputSchema"],
                {"action": "continue", "mode": "run", "expectedGeneration": 7},
            )

    def test_threads_and_registers_are_bounded_and_stop_guarded(self) -> None:
        validate(
            load_tool("ce.threads")["inputSchema"],
            {"action": "list", "limit": 200, "expectedGeneration": 7},
        )
        validate(
            load_tool("ce.registers")["inputSchema"],
            {
                "action": "read", "includeVectors": True,
                "expectedGeneration": 7, "expectedStopGeneration": 2,
            },
        )
        with self.assertRaises(ContractViolation):
            validate(
                load_tool("ce.registers")["inputSchema"],
                {"action": "read", "expectedGeneration": 7},
            )


if __name__ == "__main__":
    unittest.main()
