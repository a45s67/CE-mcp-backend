from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import time
import unittest

from ce_mcp.fake_bridge import FakeBridge
from ce_mcp.models import Session
from ce_mcp.service import BackendService, BridgeTransportError
from ce_mcp.protocol import BridgeResponse


ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = ROOT / "ce_mcp" / "contracts" / "v1" / "tools"
SESSION = {
    "sessionId": "ce-01jabcdef",
    "generation": 7,
    "state": "paused",
    "pid": 4242,
    "architecture": "x86_64",
    "pointerWidth": 64,
}


def status_result(session=None):
    value = {
        "bridge": {
            "connected": True,
            "version": "0.0.1",
            "dbvmReadiness": "not-ready",
        },
        "capabilities": {
            "available": ["memory.read"],
            "enabled": ["memory.read"],
            "disabledReasons": {},
            "limits": {"maxReadBytes": 1048576},
        },
    }
    if session is not None:
        value["session"] = session
    return value


class RaisingBridge:
    def call(self, request):
        raise BridgeTransportError("test disconnect")


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = FakeBridge()
        self.service = BackendService(self.bridge, TOOL_DIR)

    def attach(self) -> None:
        self.bridge.register("process.attach", lambda params: {"session": SESSION})
        outcome = self.service.call_tool("ce.process", {"action": "attach", "pid": 4242})
        self.assertIsNone(outcome.error)

    def test_status_is_normalized_and_output_validated(self) -> None:
        self.bridge.register("status.get", lambda params: status_result())
        outcome = self.service.call_tool("ce.status", {})
        assert outcome.result is not None
        self.assertEqual(outcome.result["backend"]["protocolVersion"], 1)
        self.assertEqual(outcome.result["bridge"]["dbvmReadiness"], "not-ready")
        self.assertEqual(self.bridge.calls[0].method, "status.get")

    def test_status_without_session_invalidates_previously_attached_target(self) -> None:
        self.attach()
        self.bridge.register("status.get", lambda params: status_result())
        outcome = self.service.call_tool("ce.status", {})
        self.assertIsNone(outcome.error)
        self.assertIsNone(self.service.session)
        blocked = self.service.call_tool(
            "ce.memory_read", {"mode": "raw", "address": "0x1234", "size": 4},
        )
        self.assertEqual(blocked.error.code, "NO_TARGET")  # type: ignore[union-attr]

    def test_unknown_tool_and_invalid_input_do_not_reach_bridge(self) -> None:
        unknown = self.service.call_tool("ce.lua_eval", {})
        invalid = self.service.call_tool("ce.process", {"action": "attach", "name": "x"})
        self.assertEqual(unknown.error.code, "METHOD_NOT_FOUND")  # type: ignore[union-attr]
        self.assertEqual(invalid.error.code, "INVALID_PARAMS")  # type: ignore[union-attr]
        self.assertEqual(self.bridge.calls, [])

    def test_attach_tracks_session_and_memory_read_passes_session_id(self) -> None:
        self.attach()
        self.bridge.register(
            "memory.read",
            lambda params: {
                "session": SESSION,
                "resolvedAddress": {"address": "0x0000000000001234"},
                "bytes": "01020304",
                "encoding": "hex",
                "complete": True,
            },
        )
        outcome = self.service.call_tool(
            "ce.memory_read",
            {"mode": "raw", "address": "0x1234", "size": 4, "expectedGeneration": 7},
        )
        self.assertIsNone(outcome.error)
        self.assertEqual(self.bridge.calls[-1].method, "memory.read")
        self.assertEqual(self.bridge.calls[-1].session_id, SESSION["sessionId"])

    def test_read_without_target_and_stale_generation_are_preflight_errors(self) -> None:
        no_target = self.service.call_tool(
            "ce.memory_read", {"mode": "raw", "address": "0x1234", "size": 4}
        )
        self.assertEqual(no_target.error.code, "NO_TARGET")  # type: ignore[union-attr]
        self.attach()
        stale = self.service.call_tool(
            "ce.memory_read",
            {"mode": "raw", "address": "0x1234", "size": 4, "expectedGeneration": 6},
        )
        self.assertEqual(stale.error.code, "STALE_SESSION")  # type: ignore[union-attr]
        self.assertTrue(stale.error.safe_to_retry)  # type: ignore[union-attr]
        self.assertEqual(len(self.bridge.calls), 1)

    def test_detach_clears_session(self) -> None:
        self.attach()
        self.bridge.register("process.detach", lambda params: {"detached": True})
        outcome = self.service.call_tool(
            "ce.process", {"action": "detach", "expectedGeneration": 7}
        )
        self.assertIsNone(outcome.error)
        self.assertIsNone(self.service.session)

    def test_malformed_transport_response_is_contract_error(self) -> None:
        class MalformedBridge:
            def call(self, request):
                from ce_mcp.models import ContractViolation

                raise ContractViolation("bad response envelope")

        service = BackendService(MalformedBridge(), TOOL_DIR)
        outcome = service.call_tool("ce.status", {})
        self.assertEqual(outcome.error.code, "BACKEND_CONTRACT_VIOLATION")  # type: ignore[union-attr]
        self.assertFalse(outcome.error.safe_to_retry)  # type: ignore[union-attr]

    def test_invalid_bridge_output_becomes_contract_error(self) -> None:
        self.bridge.register("status.get", lambda params: {"bridge": {"connected": True}})
        outcome = self.service.call_tool("ce.status", {})
        self.assertEqual(outcome.error.code, "BACKEND_CONTRACT_VIOLATION")  # type: ignore[union-attr]

    def test_invalid_session_from_bridge_becomes_contract_error(self) -> None:
        self.bridge.register("process.attach", lambda params: {"session": {"pid": 1}})
        outcome = self.service.call_tool("ce.process", {"action": "attach", "pid": 1})
        self.assertEqual(outcome.error.code, "BACKEND_CONTRACT_VIOLATION")  # type: ignore[union-attr]
        self.assertIsNone(self.service.session)

    def test_transport_failure_retry_semantics(self) -> None:
        service = BackendService(RaisingBridge(), TOOL_DIR, request_deadline_ms=20)
        read = service.call_tool("ce.status", {})
        mutation = service.call_tool("ce.process", {"action": "attach", "pid": 4242})
        self.assertEqual(read.error.code, "BRIDGE_UNAVAILABLE")  # type: ignore[union-attr]
        self.assertTrue(read.error.safe_to_retry)  # type: ignore[union-attr]
        self.assertEqual(mutation.error.code, "OUTCOME_UNKNOWN")  # type: ignore[union-attr]
        self.assertFalse(mutation.error.safe_to_retry)  # type: ignore[union-attr]

    def test_attach_disconnect_is_reconciled_without_repeating_mutation(self) -> None:
        class RebuildingBridge:
            def __init__(self):
                self.methods = []

            def call(self, request):
                self.methods.append(request.method)
                if request.method == "process.attach":
                    raise BridgeTransportError("CE rebuilt Lua state")
                return BridgeResponse(request.request_id, result={"session": SESSION})

        bridge = RebuildingBridge()
        service = BackendService(bridge, TOOL_DIR, request_deadline_ms=200)
        outcome = service.call_tool("ce.process", {"action": "attach", "pid": 4242})
        self.assertIsNone(outcome.error)
        self.assertTrue(outcome.result["reconciled"])  # type: ignore[index]
        self.assertEqual(bridge.methods.count("process.attach"), 1)
        self.assertEqual(bridge.methods.count("status.get"), 1)

    def test_pending_attach_is_reconciled_without_repeating_mutation(self) -> None:
        class PendingBridge:
            def __init__(self):
                self.methods = []

            def call(self, request):
                self.methods.append(request.method)
                if request.method == "process.attach":
                    return BridgeResponse(request.request_id, result={"pending": True, "pid": 4242})
                return BridgeResponse(request.request_id, result={"session": SESSION})

        bridge = PendingBridge()
        service = BackendService(bridge, TOOL_DIR, request_deadline_ms=200)
        outcome = service.call_tool("ce.process", {"action": "attach", "pid": 4242})
        self.assertIsNone(outcome.error)
        self.assertTrue(outcome.result["reconciled"])  # type: ignore[index]
        self.assertEqual(bridge.methods, ["process.attach", "status.get"])

    def test_attach_reconciliation_refuses_a_different_pid(self) -> None:
        other = dict(SESSION, pid=9999)

        class WrongTargetBridge:
            def call(self, request):
                if request.method == "process.attach":
                    raise BridgeTransportError("disconnect")
                return BridgeResponse(request.request_id, result={"session": other})

        service = BackendService(WrongTargetBridge(), TOOL_DIR, request_deadline_ms=50)
        outcome = service.call_tool("ce.process", {"action": "attach", "pid": 4242})
        self.assertEqual(outcome.error.code, "OUTCOME_UNKNOWN")  # type: ignore[union-attr]
        self.assertIsNone(service.session)

    def test_phase1_readonly_tools_route_to_domain_bridge_methods(self) -> None:
        self.attach()
        fixtures = [
            (
                "ce.memory_map",
                {"limit": 10},
                "memory.map",
                {"session": SESSION, "items": [], "truncated": False},
            ),
            (
                "ce.disassembly",
                {"action": "instruction", "address": "0x1234"},
                "disassembly.instruction",
                {"session": SESSION, "instruction": {"address": "0x1234"}},
            ),
            (
                "ce.symbols",
                {"action": "resolve", "expression": "sample.exe+0x10"},
                "symbols.resolve",
                {"session": SESSION, "address": {"address": "0x1234"}},
            ),
        ]
        for tool, arguments, method, result in fixtures:
            self.bridge.register(method, lambda params, value=result: value)
            outcome = self.service.call_tool(tool, arguments)
            self.assertIsNone(outcome.error, tool)
            self.assertEqual(self.bridge.calls[-1].method, method)

    def test_scan_and_operation_handles_route_with_session_generation(self) -> None:
        self.attach()
        operation = {
            "operationId": "scan-00000007-00000001",
            "kind": "scan",
            "state": "running",
            "generation": 7,
            "cancellable": True,
        }
        self.bridge.register(
            "scan.start", lambda params: {"session": SESSION, "operation": operation}
        )
        started = self.service.call_tool(
            "ce.scan",
            {
                "action": "start", "scanType": "exact", "valueType": "i32",
                "value": "42", "expectedGeneration": 7,
            },
        )
        self.assertIsNone(started.error)
        self.assertEqual(self.bridge.calls[-1].method, "scan.start")
        self.assertEqual(self.bridge.calls[-1].session_id, SESSION["sessionId"])

        self.bridge.register(
            "operations.get", lambda params: {"session": SESSION, "operation": operation}
        )
        queried = self.service.call_tool(
            "ce.operations",
            {"action": "get", "operationId": operation["operationId"], "expectedGeneration": 7},
        )
        self.assertIsNone(queried.error)
        self.assertEqual(self.bridge.calls[-1].method, "operations.get")

    def test_scan_start_transport_failure_is_unknown_and_not_retryable(self) -> None:
        service = BackendService(RaisingBridge(), TOOL_DIR, request_deadline_ms=20)
        service._session = Session.from_dict(SESSION)
        outcome = service.call_tool(
            "ce.scan",
            {
                "action": "start", "scanType": "exact", "valueType": "i32",
                "value": "42", "expectedGeneration": 7,
            },
        )
        self.assertEqual(outcome.error.code, "OUTCOME_UNKNOWN")
        self.assertFalse(outcome.error.safe_to_retry)

    def test_pointer_resolve_routes_as_generation_bound_read(self) -> None:
        self.attach()
        self.bridge.register(
            "pointer.resolve",
            lambda params: {
                "session": SESSION,
                "base": {"address": "0x0000000000001000"},
                "offsets": [16],
                "finalAddress": {"address": "0x0000000000002010"},
                "chain": [],
            },
        )
        outcome = self.service.call_tool(
            "ce.pointer",
            {"action": "resolve", "base": "0x1000", "offsets": [16], "expectedGeneration": 7},
        )
        self.assertIsNone(outcome.error)
        self.assertEqual(self.bridge.calls[-1].method, "pointer.resolve")
        self.assertEqual(self.bridge.calls[-1].session_id, SESSION["sessionId"])

    def test_debug_control_and_breakpoints_route_with_stop_guard(self) -> None:
        self.attach()
        self.bridge.register(
            "debug.control.pause",
            lambda params: {
                "session": SESSION,
                "debugger": {"active": True, "stopped": True, "stopKind": "suspend"},
                "pauseRequested": True,
            },
        )
        paused = self.service.call_tool(
            "ce.debug_control",
            {"action": "pause", "expectedGeneration": 7},
        )
        self.assertIsNone(paused.error)
        self.assertEqual(self.bridge.calls[-1].method, "debug.control.pause")

        self.bridge.register(
            "debug.control.continue",
            lambda params: {
                "session": SESSION,
                "debugger": {"active": True, "stopped": False, "stopGeneration": 3},
            },
        )
        continued = self.service.call_tool(
            "ce.debug_control",
            {
                "action": "continue", "mode": "run", "expectedGeneration": 7,
                "expectedStopGeneration": 3,
            },
        )
        self.assertIsNone(continued.error)
        self.assertEqual(self.bridge.calls[-1].method, "debug.control.continue")

        self.bridge.register(
            "debug.breakpoints.list",
            lambda params: {"session": SESSION, "items": [], "truncated": False},
        )
        listed = self.service.call_tool(
            "ce.breakpoints", {"action": "list", "expectedGeneration": 7},
        )
        self.assertIsNone(listed.error)
        self.assertEqual(self.bridge.calls[-1].method, "debug.breakpoints.list")

    def test_threads_and_registers_route_and_normalize_actions(self) -> None:
        self.attach()
        self.bridge.register(
            "threads.list",
            lambda params: {"session": SESSION, "items": [], "truncated": False},
        )
        threads = self.service.call_tool(
            "ce.threads", {"action": "list", "expectedGeneration": 7},
        )
        self.assertIsNone(threads.error)
        self.assertEqual(threads.result["action"], "list")  # type: ignore[index]
        self.assertEqual(self.bridge.calls[-1].method, "threads.list")

        self.bridge.register(
            "debug.registers.read",
            lambda params: {
                "session": dict(SESSION, state="paused"), "stopGeneration": 2,
                "architecture": "x86_64", "general": {"rip": "0x1234"},
            },
        )
        registers = self.service.call_tool(
            "ce.registers",
            {"action": "read", "expectedGeneration": 7, "expectedStopGeneration": 2},
        )
        self.assertIsNone(registers.error)
        self.assertEqual(registers.result["action"], "read")  # type: ignore[index]
        self.assertEqual(self.bridge.calls[-1].method, "debug.registers.read")

    def test_memory_analysis_routes_and_normalizes(self) -> None:
        self.attach()
        self.bridge.register(
            "memory.compare",
            lambda params: {"session": SESSION, "equal": False, "firstDifference": 3, "size": 16},
        )
        compared = self.service.call_tool(
            "ce.memory_analysis",
            {
                "action": "compare", "leftAddress": "0x1000", "rightAddress": "0x2000",
                "size": 16, "expectedGeneration": 7,
            },
        )
        self.assertIsNone(compared.error)
        self.assertEqual(compared.result["action"], "compare")  # type: ignore[index]
        self.assertEqual(self.bridge.calls[-1].method, "memory.compare")

    def test_structure_workspace_crud_is_sidecar_owned_and_read_routes_to_bridge(self) -> None:
        created = self.service.call_tool("ce.structures", {
            "action": "create", "name": "Header", "size": 16,
            "fields": [{"name": "flags", "offset": 4, "type": "u32"}],
        })
        self.assertIsNone(created.error)
        structure_id = created.result["structure"]["structureId"]  # type: ignore[index]
        self.assertEqual(self.bridge.calls, [])

        self.attach()
        self.bridge.register(
            "structures.read",
            lambda params: {
                "session": SESSION, "base": {"address": "0x0000000000001000"},
                "values": [{"name": "flags", "offset": 4, "type": "u32", "value": 7}],
            },
        )
        read = self.service.call_tool("ce.structures", {
            "action": "read", "structureId": structure_id, "base": "0x1000",
            "expectedGeneration": 7,
        })
        self.assertIsNone(read.error)
        self.assertEqual(self.bridge.calls[-1].method, "structures.read")
        self.assertEqual(self.bridge.calls[-1].params["fields"][0]["name"], "flags")

    def test_entire_service_call_is_serialized(self) -> None:
        guard = Lock()
        active = 0
        max_active = 0

        def handler(params):
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with guard:
                active -= 1
            return {"session": SESSION}

        self.bridge.register("process.attach", handler)
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(
                executor.map(
                    lambda pid: self.service.call_tool(
                        "ce.process", {"action": "attach", "pid": pid}
                    ),
                    (4242, 4243),
                )
            )
        self.assertTrue(all(outcome.error is None for outcome in outcomes))
        self.assertEqual(max_active, 1)


if __name__ == "__main__":
    unittest.main()
