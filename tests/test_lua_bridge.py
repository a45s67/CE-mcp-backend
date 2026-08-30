import ctypes
from pathlib import Path
import re
import unittest

from ce_mcp.service import BackendService


ROOT = Path(__file__).resolve().parents[1]
LUA_DLL = next(
    (path for path in (
        Path(r"C:\tools\Cheat Engine\lua53-64.dll"),
        Path(r"C:\tools\CE\lua53-64.dll"),
    ) if path.exists()),
    Path(r"C:\tools\Cheat Engine\lua53-64.dll"),
)
BRIDGE = ROOT / "bridge" / "ce_mcp_bridge.lua"
PROBES = ROOT / "bridge" / "probes"


class LuaBridgeTests(unittest.TestCase):
    @unittest.skipUnless(LUA_DLL.exists(), "Cheat Engine Lua runtime is not installed")
    def test_bridge_compiles_with_ce_lua_53(self) -> None:
        dll = ctypes.WinDLL(str(LUA_DLL))
        dll.luaL_newstate.restype = ctypes.c_void_p
        dll.luaL_loadfilex.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        dll.luaL_loadfilex.restype = ctypes.c_int
        dll.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        dll.lua_tolstring.restype = ctypes.c_char_p
        dll.lua_close.argtypes = [ctypes.c_void_p]
        state = dll.luaL_newstate()
        self.assertTrue(state)
        try:
            result = dll.luaL_loadfilex(state, str(BRIDGE).encode("utf-8"), None)
            message = ""
            if result:
                raw = dll.lua_tolstring(state, -1, None)
                message = raw.decode("utf-8", "replace") if raw else "unknown Lua error"
            self.assertEqual(result, 0, message)
        finally:
            dll.lua_close(state)

    @unittest.skipUnless(LUA_DLL.exists(), "Cheat Engine Lua runtime is not installed")
    def test_lifecycle_probes_compile_with_ce_lua_53(self) -> None:
        dll = ctypes.WinDLL(str(LUA_DLL))
        dll.luaL_newstate.restype = ctypes.c_void_p
        dll.luaL_loadfilex.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        dll.luaL_loadfilex.restype = ctypes.c_int
        dll.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        dll.lua_tolstring.restype = ctypes.c_char_p
        dll.lua_close.argtypes = [ctypes.c_void_p]
        for probe in sorted(PROBES.glob("*.lua")):
            state = dll.luaL_newstate()
            self.assertTrue(state)
            try:
                result = dll.luaL_loadfilex(state, str(probe).encode("utf-8"), None)
                raw = dll.lua_tolstring(state, -1, None) if result else None
                message = raw.decode("utf-8", "replace") if raw else ""
                self.assertEqual(result, 0, f"{probe.name}: {message}")
            finally:
                dll.lua_close(state)

    def test_lua_dispatch_covers_every_current_sidecar_bridge_method(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        registered = set(re.findall(r'handlers\["([a-z0-9_.]+)"\]\s*=', source))
        expected = {
            method
            for tool, actions in BackendService._METHODS.items()
            if tool != "ce.artifacts"
            for method in actions.values()
        }
        self.assertEqual(expected - registered, set())

    def test_dangerous_escape_hatch_handlers_are_absent(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        registered = set(re.findall(r'handlers\["([a-z0-9_.]+)"\]\s*=', source))
        forbidden = {
            "lua.evaluate",
            "shell.execute",
            "host.command",
            "file.delete",
            "inject.dll",
            "dbvm.physical_write",
        }
        self.assertTrue(forbidden.isdisjoint(registered))

    def test_status_queries_dbvm_state_without_initializing_it(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        start = source.index('handlers["status.get"]')
        end = source.index('handlers["process.list"]', start)
        handler = source[start:end]
        self.assertIn('rawget(_G, "dbk_initialized")', handler)
        self.assertIn('rawget(_G, "dbvm_initialized")', handler)
        self.assertNotIn("dbk_initialize(", handler)
        self.assertNotIn("dbvm_initialize(", handler)

    def test_dbvm_methods_have_independent_bridge_policy_and_token_guard(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        self.assertIn('rawget(_G, "CE_MCP_POLICY")', source)
        self.assertIn('request.method:match("^dbvm%.")', source)
        self.assertIn('params._policyProfile ~= "hypervisor"', source)
        self.assertIn("constantTimeEqual(params._authorizationToken", source)
        self.assertLess(
            source.index('request.method:match("^dbvm%.")'),
            source.index("local handler = handlers[request.method]"),
        )

    def test_bridge_policy_never_initializes_dbk_or_dbvm(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        self.assertNotIn("dbk_initialize(", source)
        self.assertNotIn("dbvm_initialize(", source)

    def test_hypervisor_resources_are_cleaned_at_all_ownership_boundaries(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        self.assertIn("local function cleanupHypervisor()", source)
        refresh = source[source.index("local function refreshTarget"):source.index("local function session()")]
        self.assertIn("cleanupHypervisor()", refresh)
        disconnect = source[source.index("local function worker"):source.index("function StopCEMCPBridge")]
        self.assertIn("cleanupHypervisor()", disconnect)
        stop = source[source.index("function StopCEMCPBridge"):source.index("function StartCEMCPBridge")]
        self.assertIn("cleanupHypervisor()", stop)

    def test_debug_pause_uses_synchronous_process_suspend_and_cleanup(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        handler = source[source.index('handlers["debug.control.pause"]'):source.index('handlers["debug.control.continue"]')]
        self.assertIn("pcall(pause)", handler)
        self.assertIn('recordDebugStop("pause", nil)', handler)
        callback = source[source.index("local function recordDebugStop"):source.index('handlers["debug.control.status"]')]
        self.assertIn("debugger_onBreakpoint", callback)
        self.assertIn("stopGeneration = debugState.stopGeneration + 1", callback)
        start = source[source.index('handlers["debug.control.start"]'):source.index('handlers["debug.control.pause"]')]
        self.assertLess(start.index("installDebuggerCallback()"), start.index("debugProcess"))
        cleanup = source[source.index("local function cleanupDebugger"):source.index("local function cleanupHypervisor")]
        self.assertIn("previousOnBreakpoint", cleanup)
        self.assertIn("pcall(unpause)", cleanup)

    def test_step_uses_bounded_temporary_hardware_breakpoints(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        helper = source[source.index("local function clearStepBreakpoints"):source.index('handlers["debug.control.continue"]')]
        self.assertIn("createDisassembler()", helper)
        self.assertIn("data.isConditionalJump", helper)
        self.assertIn("occupied + #targets > 4", helper)
        self.assertIn("recordDebugStop(\"step\", nil)", helper)
        handler = source[source.index('handlers["debug.control.continue"]'):source.index('handlers["debug.control.detach"]')]
        self.assertLess(handler.index("prepareHardwareStep(mode)"), handler.index("debug_continueFromBreakpoint"))
        cleanup = source[source.index("local function cleanupDebugger"):source.index("local function cleanupHypervisor")]
        self.assertIn("stepAddresses", cleanup)

    def test_dbvm_start_uses_optional_readiness_then_physical_gate_without_implicit_load(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        self.assertIn("local function requireDBVMReady()", source)
        self.assertIn('handlers["dbvm.watch.start"]', source)
        self.assertIn('handlers["dbvm.trace.start"]', source)
        self.assertNotIn("dbk_initialize(", source)
        self.assertNotIn("dbvm_initialize(", source)
        self.assertIn("DBK/DBVM may not be enabled or loaded by the user", source)
        self.assertIn("suggestedAction", source)
        self.assertIn("local function dbvmApiError", source)
        readiness = source[source.index("local function requireDBVMReady"):source.index("local function dbvmApiError")]
        self.assertIn('if type(dbkQuery) == "function" then', readiness)
        self.assertIn('if type(dbvmQuery) == "function" then', readiness)
        self.assertNotIn("readiness cannot be proved", readiness)
        self.assertIn("local function physicalTranslationError", source)
        self.assertIn('dbvmReadiness = readiness', source)

    def test_pipe_override_is_name_only_and_defaults_to_versioned_local_pipe(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        self.assertIn('os.getenv("CE_MCP_PIPE_NAME")', source)
        self.assertIn('"CE_MCP_Backend_v1_"', source)
        self.assertIn("getCheatEngineProcessID()", source)
        self.assertIn('PIPE_NAME:match("^[A-Za-z0-9_.-]+$")', source)

    def test_refine_preserves_attached_results_until_next_scan_finishes(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        start = source.index('handlers["scan.refine"]')
        end = source.index('handlers["scan.results"]', start)
        handler = source[start:end]
        self.assertIn("operation.command =", handler)
        next_scan = source.index("operation.memScan.nextScan(")
        wait_done = source.index("operation.memScan.waitTillDone()", next_scan)
        destroy_old = source.index("oldFoundList.destroy()", wait_done)
        self.assertLess(next_scan, wait_done)
        self.assertLess(wait_done, destroy_old)

    def test_only_probe_verified_relative_refinements_are_enabled(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        self.assertIn('["scan.refine.comparison"]', source)
        self.assertIn('["pointer.scan"]', source)
        self.assertNotIn("CE 7.5 Lua exposes", source)
        for mode in ("increased", "decreased", "changed", "unchanged"):
            self.assertIn(f"{mode} = true", source)
        self.assertNotIn("between = true", source)
        self.assertNotIn("bigger = true", source)
        self.assertNotIn("smaller = true", source)

    def test_signature_summary_never_queries_live_memscan_progress(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        self.assertIn(
            'if operation.kind == "scan" and operation.memScan and operation.state == "running" then',
            source,
        )
        self.assertIn('if operation.kind ~= "signature"', source)

    def test_pipe_disconnect_cleans_operation_handles(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        marker = source.index(
            'state.diagnostic = "client-disconnected:debugger-and-operations-cleaned"'
        )
        cleanup = source.rfind("cleanupOperations()", 0, marker)
        debug_cleanup = source.rfind("cleanupDebugger()", 0, marker)
        self.assertGreater(cleanup, source.index("local function worker"))
        self.assertGreater(debug_cleanup, source.index("local function worker"))

    def test_target_liveness_does_not_trust_stale_opened_process_id_alone(self) -> None:
        source = BRIDGE.read_text(encoding="utf-8")
        helper = source[source.index("local function openedProcessStillExists"):source.index("local function refreshTarget")]
        self.assertIn("pcall(getProcesslist)", helper)
        refresh = source[source.index("local function refreshTarget"):source.index("local function session()")]
        self.assertIn("openedProcessStillExists(pid)", refresh)
        self.assertIn("if queried and not exists then pid = 0 end", refresh)


if __name__ == "__main__":
    unittest.main()
