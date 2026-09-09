"""Execute extracted bridge code, not CE or its autorun/pipe lifecycle.

These are mocked Lua-runtime regressions, not native debugger verification.
Native API checks are provided separately by bridge/probes/reliability_probe.lua.
"""

import ctypes
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge" / "ce_mcp_bridge.lua"
LUA_PATHS = (
    Path(r"C:\Program Files\Cheat Engine\lua53-64.dll"),
    Path(r"C:\tools\Cheat Engine\lua53-64.dll"),
    Path(r"C:\tools\CE\lua53-64.dll"),
)


def bridge_section(start: str, end: str) -> str:
    """Extract an unchanged, bounded declaration block; missing markers fail."""
    source = BRIDGE.read_text(encoding="utf-8")
    first = source.index(start)
    return source[first:source.index(end, first + len(start))]


DISASSEMBLY = (
    "local function instructionAt(",
    'handlers["symbols.resolve"]',
)
MEMORY_MAP = (("local function regionState(", "local function instructionAt("),)
MEMORY_READ = (('handlers["memory.read"]', 'handlers["memory.compare"]'),)
DEBUG = (
    ("local function refreshDebugState()", "local function session()"),
    ("local function debugSummary()", "local scanValueTypes ="),
)
DEBUG_LIFECYCLE = (
    ("local function cleanupDebugger()", "local function cleanupHypervisor()"),
    ("local function openedProcessStillExists(", "local handlers = {}"),
    DEBUG[1],
    ('handlers["process.detach"]', 'handlers["memory.read"]'),
)

FAKE_CE = """
local handlers = {}
local function requireTarget() return nil end
local function session() return { generation = 7 } end
local function pointerWidth() return 64 end
local function formatAddress(address) return string.format("0x%016X", address) end
local function errorDetail(code, message, recoverable, safeToRetry)
  return { __error = true, code = code, message = message,
    recoverable = recoverable, safeToRetry = safeToRetry }
end
function getAddressSafe(value) return tonumber(value) end
function getNameFromAddress(_) return "fixture" end
"""

DISASSEMBLY_FIXTURE = """
local base = 0x1000
local code = {
  [base] = { opcode = "nop", bytes = {0x90}, extra = "" },
  [base + 1] = { opcode = "ret", bytes = {0xC3}, extra = "" },
}
local decoded, sized, reads = {}, {}, {}
function disassemble(address)
  decoded[#decoded + 1] = address
  local instruction = code[address]
  if not instruction then error("decode outside fixture") end
  return string.format("%X - fixture - %s", address, instruction.opcode)
end
function splitDisassembledString(text)
  local address = tonumber(text:match("^(%x+)"), 16)
  local instruction = assert(code[address])
  local bytes = {}
  for i, byte in ipairs(instruction.bytes) do bytes[i] = string.format("%02X", byte) end
  -- CE 7.7 returns extra, opcode, bytes, address, in that order.
  return instruction.extra, instruction.opcode, table.concat(bytes, " "),
    string.format("%X", address)
end
function getInstructionSize(address)
  sized[#sized + 1] = address
  return #assert(code[address]).bytes
end
function readBytes(address, size, asTable)
  reads[#reads + 1] = { address = address, size = size }
  assert(asTable == true)
  local bytes = assert(code[address]).bytes
  assert(size == #bytes)
  return bytes
end
"""

MEMORY_MAP_FIXTURE = """
local state = {}
local regions, modules, names, queries = {}, {}, {}, {}
local enumerations, moduleEnumerations = 0, 0
function enumMemoryRegions()
  enumerations = enumerations + 1
  error("lazy memory map must not enumerate all regions")
end
function getMemoryRegionInfo(address)
  queries[#queries + 1] = address
  for _, region in ipairs(regions) do
    if address >= region.BaseAddress and address < region.BaseAddress + region.RegionSize then
      return region
    end
  end
  return nil
end
function getOpenedProcessID() return 42 end
function enumModules(pid)
  assert(pid == 42)
  moduleEnumerations = moduleEnumerations + 1
  return modules
end
function getNameFromAddress(address, module, symbol, section)
  assert(module == true and symbol == false and section == false)
  names[#names + 1] = address
  return "unrelated display name"
end
local function region(address, state, kind, protection, size)
  return { BaseAddress = address, AllocationBase = 0x1000, RegionSize = size or 0x100,
    State = state or 0x1000, Type = kind or 0x1000000, Protect = protection or 0x20 }
end
"""

STRING_FIXTURE = """
local base, budget, unit = 0x1000, 5, 1
local memory, reads, converted = {}, {}, {}
function readString() error("native readString must not bypass the byte budget") end
function readBytes(address, size, asTable)
  assert(asTable == true and size == unit)
  assert(address >= base and address + size <= base + budget, "read exceeded byte budget")
  reads[#reads + 1] = { address = address, size = size }
  local bytes = {}
  for i = 1, size do
    local byte = memory[address - base + i]
    if byte == nil then return #bytes > 0 and bytes or nil end
    bytes[i] = byte
  end
  return bytes
end
function byteTableToString(bytes)
  assert(unit == 1)
  converted[#converted + 1] = bytes
  return string.char(table.unpack(bytes))
end
function byteTableToWideString(bytes)
  assert(unit == 2 and #bytes % 2 == 0, "converter received partial UTF-16 unit")
  converted[#converted + 1] = bytes
  local chars = {}
  for i = 1, #bytes, 2 do chars[#chars + 1] = utf8.char(bytes[i] + 256 * bytes[i + 1]) end
  return table.concat(chars)
end
"""

DEBUG_FIXTURE = """
local state = { generation = 7, debug = {
  active = true, stopped = true, stopGeneration = 3, processSuspended = false,
  eventCounter = 0, events = {}, breakpoints = {}, breakpointCounter = 0,
  callbackInstalled = false,
} }
local nativeActive, nativeContext = true, true
local activeCalls, contextCalls, continueCalls, hardwareCalls = 0, {}, {}, 0
RIP, EIP, RAX, EFLAGS = 0x1234, 0x5678, 0x99, 0x202
co_run, co_stepinto, co_stepover = 71, 83, 97
bptExecute, bptWrite, bptAccess, bpmDebugRegister = 11, 12, 13, 14
function debug_isDebugging()
  activeCalls = activeCalls + 1
  return nativeActive
end
function debug_isBroken() error("invalid CE binding must not be used") end
function debug_getContext(vectors)
  contextCalls[#contextCalls + 1] = vectors
  return nativeContext
end
function debug_continueFromBreakpoint(mode)
  continueCalls[#continueCalls + 1] = mode
  nativeContext = false
  return true
end
function createDisassembler() error("must not guess step destinations") end
local breakpointCallback
function debug_setBreakpoint(address, size, trigger, method, callback)
  hardwareCalls = hardwareCalls + 1
  assert(address == 0x1000 and size == 1)
  assert(trigger == bptExecute and method == bpmDebugRegister)
  breakpointCallback = callback
  return true
end
function debug_removeBreakpoint() error("must not remove hardware slots for stepping") end
"""

DEBUG_LIFECYCLE_FIXTURE = DEBUG_FIXTURE + """
local nativePid = 42
state.pid, state.sessionId, state.logicalDetached = nativePid, "ce-0000002a-00000007", false
function getOpenedProcessID() return nativePid end
function getProcesslist() return { [nativePid] = "fixture.exe" } end
function detachIfPossible() nativeActive, nativeContext = false, false end
local function cleanupOperations() end
local function cleanupHypervisor() end
"""


class LuaBridgeReliabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if sys.platform != "win32" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise unittest.SkipTest("Cheat Engine Lua DLL requires 64-bit Windows Python")
        failures = []
        for path in LUA_PATHS:
            if not path.is_file():
                continue
            try:
                # Same Lua C API as test_lua_bridge.py, extended to execute chunks.
                dll = ctypes.WinDLL(str(path))
                dll.luaL_newstate.argtypes = []
                dll.luaL_newstate.restype = ctypes.c_void_p
                dll.luaL_openlibs.argtypes = [ctypes.c_void_p]
                dll.luaL_openlibs.restype = None
                dll.luaL_loadbufferx.argtypes = [
                    ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t,
                    ctypes.c_char_p, ctypes.c_char_p,
                ]
                dll.luaL_loadbufferx.restype = ctypes.c_int
                dll.lua_pcallk.argtypes = [
                    ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                    ctypes.c_ssize_t, ctypes.c_void_p,
                ]
                dll.lua_pcallk.restype = ctypes.c_int
                dll.lua_tolstring.argtypes = [
                    ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_size_t),
                ]
                dll.lua_tolstring.restype = ctypes.c_char_p
                dll.lua_close.argtypes = [ctypes.c_void_p]
                dll.lua_close.restype = None
            except (OSError, AttributeError) as exc:
                failures.append(f"{path}: {exc}")
                continue
            cls.dll = dll
            cls.lua_path = path
            return
        raise unittest.SkipTest(
            "Cheat Engine Lua 5.3 runtime unavailable" +
            (": " + "; ".join(failures) if failures else " (no DLL installed)")
        )

    def assert_lua(
        self, body: str, *, sections: tuple[tuple[str, str], ...] = (DISASSEMBLY,),
        setup: str = DISASSEMBLY_FIXTURE,
    ) -> None:
        """Run setup + extracted declarations + assertions in a fresh Lua state.

        Add cases with self.assert_lua('...Lua assertions...'). For other handlers,
        supply sections=((start_marker, exclusive_end_marker), ...) and setup with
        deterministic CE globals/dependencies. Never include the autorun tail.
        """
        chunk = "\n".join((
            FAKE_CE, setup,
            *(bridge_section(*markers) for markers in sections), body,
        )).encode("utf-8")
        state = self.dll.luaL_newstate()
        self.assertTrue(state, "luaL_newstate failed")
        try:
            self.dll.luaL_openlibs(state)
            status = self.dll.luaL_loadbufferx(
                state, chunk, len(chunk), self.id().encode("utf-8"), b"t",
            )
            phase = "compile"
            if status == 0:
                phase = "execute"
                status = self.dll.lua_pcallk(state, 0, 0, 0, 0, None)
            raw = self.dll.lua_tolstring(state, -1, None) if status else None
            message = raw.decode("utf-8", "replace") if raw else "unknown Lua error"
            self.assertEqual(status, 0, f"{phase} ({self.lua_path}): {message}")
        finally:
            self.dll.lua_close(state)

    def test_instruction_at_uses_native_split_order(self) -> None:
        self.assert_lua("""
local result = assert(instructionAt(base))
assert(result.opcode == "nop", "opcode must not contain byte text")
assert(result.extra == "", "extra must not contain the address")
assert(result.bytes == "90")
assert(result.size == 1)
assert(result.address.address == formatAddress(base))
assert(result.address.pointerWidth == 64)
assert(result.nextAddress.address == formatAddress(base + 1))
assert(result.display == "1000 - fixture - nop")
assert(#decoded == 1 and #sized == 1 and #reads == 1)
""")

    def test_instruction_handler_preserves_extra_and_raw_bytes(self) -> None:
        self.assert_lua("""
code[base] = { opcode = "mov eax,1234", bytes = {0xB8,0x34,0x12,0,0},
  extra = "fixture annotation" }
local result = handlers["disassembly.instruction"]({address = "0x1000"})
assert(not result.__error)
assert(result.instruction.opcode == "mov eax,1234")
assert(result.instruction.extra == "fixture annotation")
assert(result.instruction.bytes == "B834120000")
assert(result.instruction.nextAddress.address == formatAddress(base + 5))
""")

    def test_function_stops_decoding_at_first_return(self) -> None:
        for detail in ("summary", "full"):
            with self.subTest(detail=detail):
                self.assert_lua(f'local detail = "{detail}"\n' + """
local result = handlers["disassembly.function"]({address = base, detail = detail})
assert(#decoded == 2, "function decoded beyond its first return")
assert(#sized == 2 and #reads == 2)
assert(result["function"].instructionCount == 2)
assert(result["function"].boundaryConfidence == "heuristic-return")
assert(result["function"].endAddress.address == formatAddress(base + 2))
assert(result.truncated == false)
if detail == "full" then
  assert(#result.items == 2)
  assert(result.items[1].opcode == "nop" and result.items[2].opcode == "ret")
else
  assert(result.items == nil)
end
""")

    def test_function_without_return_remains_bounded_incomplete(self) -> None:
        for detail, count in (("summary", 100), ("full", 500)):
            with self.subTest(detail=detail):
                self.assert_lua(f'local detail, count = "{detail}", {count}\n' + """
for i = 0, count do code[base + i] = code[base] end
local result = handlers["disassembly.function"]({address = base, detail = detail})
assert(#decoded == count and #reads == count)
assert(result["function"].instructionCount == count)
assert(result["function"].boundaryConfidence == "bounded-incomplete")
assert(result.truncated == true)
""")

    def test_list_does_not_decode_at_or_after_exhausted_byte_limit(self) -> None:
        for limit in (1, 2):
            with self.subTest(byte_limit=limit):
                self.assert_lua(f"local limit = {limit}\n" + """
local result = handlers["disassembly.list"]({
  address = base, instructionCount = 50, byteLimit = limit,
})
assert(#decoded == limit, "byte limit must be checked before the next decode")
assert(#sized == limit and #reads == limit)
assert(#result.items == limit and result.truncated == true)
for _, read in ipairs(reads) do
  assert(read.address + read.size <= base + limit, "read exceeded exact byte boundary")
end
""")

    def test_list_excludes_instruction_crossing_byte_limit(self) -> None:
        self.assert_lua("""
code[base + 1] = { opcode = "mov eax,1234", bytes = {0xB8,0x34,0x12,0,0}, extra = "" }
local result = handlers["disassembly.list"]({
  address = base, instructionCount = 50, byteLimit = 3,
})
assert(#result.items == 1 and result.items[1].size == 1)
assert(result.truncated == true)
-- One candidate may be decoded to discover its size; it must not be returned,
-- and no subsequent instruction may be decoded or read.
assert(#decoded == 2 and decoded[2] == base + 1)
assert(#reads <= 2)
""")

    def test_list_count_limit_and_default_byte_limit(self) -> None:
        self.assert_lua("""
local result = handlers["disassembly.list"]({address = base, instructionCount = 2})
assert(#decoded == 2 and #reads == 2)
assert(#result.items == 2 and result.truncated == false)
""")

    def test_instruction_list_optional_limits_preserve_legacy_calls(self) -> None:
        self.assert_lua("""
local result = instructionList(base, 2)
assert(#result == 2 and #decoded == 2)
assert(result[2].opcode == "ret")
""")

    def test_instruction_list_return_stop_is_opt_in(self) -> None:
        self.assert_lua("""
code[base + 2] = code[base]
local result = instructionList(base, 3, nil, false)
assert(#result == 3 and #decoded == 3)
""")

    def test_memory_map_filters_by_module_ranges_not_display_names(self) -> None:
        self.assert_lua("""
modules = {
  { Name = "TARGET[1].DLL", Address = 0x1000, Size = 0x1000 },
  { Name = "target1Xdll", Address = 0x3000, Size = 0x1000 },
  { Name = "target[1].dll", Address = 0x5000, Size = 0x100 },
}
regions = { region(0xF00), region(0x1000, nil, nil, nil, 0xFFF),
  region(0x1FFF, nil, nil, nil, 1), region(0x2000),
  region(0x3000), region(0x5000), region(0x5100) }
local result = handlers["memory.map"]({ moduleFilter = "target[1].dll" })
assert(not result.__error and #result.items == 3)
assert(enumerations == 0 and moduleEnumerations == 1 and #names == 3)
assert(#queries == 3 and queries[1] == 0x1000 and queries[2] == 0x1FFF
  and queries[3] == 0x5000, "must skip disjoint module gaps and end boundaries")
assert(names[1] == 0x1000 and names[2] == 0x1FFF and names[3] == 0x5000)
for i, item in ipairs(result.items) do
  assert(item.base.address == formatAddress(names[i]) and item.base.pointerWidth == 64)
  assert(item.name == "unrelated display name")
end
assert(result.truncated == false and result.nextCursor == nil)
""", sections=MEMORY_MAP, setup=MEMORY_MAP_FIXTURE)

    def test_memory_map_filters_before_offset_and_page_name_lookups(self) -> None:
        for cursor, expected, more, query_count in (
            (0, 0x1100, True, 4), (1, 0x1300, True, 6),
            (2, 0x1500, False, 6), (3, None, False, 6), (99, None, False, 6),
        ):
            with self.subTest(cursor=cursor):
                self.assert_lua(f"""
local cursor, expected, more = {cursor}, {expected or 'nil'}, {str(more).lower()}
local queryCount = {query_count}
""" + """
modules = { { Name = "target.dll", Address = 0x1000, Size = 0x600 } }
regions = { region(0x1000, 0x2000), region(0x1100),
  region(0x1200, nil, 0x20000), region(0x1300),
  region(0x1400, nil, nil, 0x04), region(0x1500), region(0x2000) }
local result = handlers["memory.map"]({ moduleFilter = "target.dll",
  stateFilter = "commit", typeFilter = "image", protectionFilter = "r-x",
  cursor = tostring(cursor), limit = 1 })
assert(not result.__error and result.truncated == more)
assert(enumerations == 0 and moduleEnumerations == 1 and #queries == queryCount)
assert(#result.items == (expected and 1 or 0) and #names == #result.items)
if expected then
  assert(names[1] == expected and result.items[1].base.address == formatAddress(expected))
  assert(result.items[1].state == "commit" and result.items[1].type == "image")
  assert(result.items[1].protection == "r-x")
end
assert(result.nextCursor == (more and tostring(cursor + 1) or nil))
""", sections=MEMORY_MAP, setup=MEMORY_MAP_FIXTURE)

    def test_memory_map_empty_matches_never_resolve_names(self) -> None:
        for filters in ('moduleFilter = "missing"', 'stateFilter = "free"',
                        'typeFilter = "mapped"', 'protectionFilter = "rw-"'):
            with self.subTest(filters=filters):
                self.assert_lua("""
regions = { region(0), region(0x100) }
modules = { { Name = "target.dll", Address = 0x1000, Size = 0x1000 } }
""" + f'local params = {{ {filters} }}\n' + """
local result = handlers["memory.map"](params)
assert(not result.__error and #result.items == 0 and #names == 0)
assert(result.truncated == false and result.nextCursor == nil)
assert(enumerations == 0 and moduleEnumerations == (params.moduleFilter and 1 or 0))
assert(#queries == (params.moduleFilter and 0 or 3))
""", sections=MEMORY_MAP, setup=MEMORY_MAP_FIXTURE)

    def test_memory_map_requested_limit_cannot_bypass_page_bounds(self) -> None:
        for limit, count in (("nil", 100), ("10000", 200), ("5", 5), ('"2"', 2),
                             ("0", 1), ("-1", 1)):
            with self.subTest(limit=limit):
                self.assert_lua(f"local limit, count = {limit}, {count}\n" + """
for i = 1, 205 do regions[i] = region((i - 1) * 0x100) end
local result = handlers["memory.map"]({ limit = limit })
assert(not result.__error and #result.items == count and #names == count)
assert(enumerations == 0 and moduleEnumerations == 0 and #queries == count + 1)
assert(result.truncated == true and result.nextCursor == tostring(count))
assert(names[1] == 0 and names[count] == (count - 1) * 0x100)
assert(queries[#queries] == count * 0x100, "query only the page plus one lookahead")
""", sections=MEMORY_MAP, setup=MEMORY_MAP_FIXTURE)

    def test_memory_map_module_enumeration_failure_is_structured(self) -> None:
        for outcome in ('error("fixture enumeration failure")', "return false", "return nil"):
            with self.subTest(outcome=outcome):
                self.assert_lua("""
regions = { region(0x1000) }
function enumModules(pid)
  assert(pid == 42)
  moduleEnumerations = moduleEnumerations + 1
""" + outcome + """
end
local result = handlers["memory.map"]({ moduleFilter = "target" })
assert(result.__error and result.code == "CE_API_UNAVAILABLE")
assert(result.message == "Module enumeration failed")
assert(result.recoverable == true and result.safeToRetry == true)
assert(enumerations == 0 and moduleEnumerations == 1 and #names == 0 and #queries == 0)
""", sections=MEMORY_MAP, setup=MEMORY_MAP_FIXTURE)

    def test_memory_map_merges_duplicate_overlapping_and_adjacent_modules(self) -> None:
        self.assert_lua("""
modules = {
  { Name = "target.dll", Address = 0x1200, Size = 0x200 },
  { Name = "target.dll", Address = 0x1000, Size = 0x300 },
  { Name = "target.dll", Address = 0x1000, Size = 0x300 },
  { Name = "target.dll", Address = 0x1400, Size = 0x100 },
  { Name = "target.dll", Address = 0x9000, Size = 0x100 },
}
for i = 1, 5 do regions[i] = region(0x1000 + (i - 1) * 0x100) end
regions[6] = region(0x9000)
local result = handlers["memory.map"]({ moduleFilter = "target", limit = 5 })
assert(not result.__error and #result.items == 5 and result.truncated == true)
assert(result.nextCursor == "5")
assert(enumerations == 0 and moduleEnumerations == 1 and #queries == 6 and #names == 5)
for i, item in ipairs(result.items) do
  local expected = 0x1000 + (i - 1) * 0x100
  assert(item.base.address == formatAddress(expected))
  assert(queries[i] == expected and names[i] == expected, "merged ranges must not repeat rows")
end
assert(queries[6] == 0x9000, "lookahead must jump directly to the next module")
""", sections=MEMORY_MAP, setup=MEMORY_MAP_FIXTURE)

    def test_memory_map_containing_region_before_module_start_is_not_returned(self) -> None:
        self.assert_lua("""
modules = { { Name = "target.dll", Address = 0x1080, Size = 0x180 } }
regions = { region(0x1000), region(0x1100), region(0x1200) }
local result = handlers["memory.map"]({ moduleFilter = "target" })
assert(not result.__error and #result.items == 1 and result.truncated == false)
assert(result.items[1].base.address == formatAddress(0x1100))
assert(enumerations == 0 and moduleEnumerations == 1 and #names == 1)
assert(#queries == 2 and queries[1] == 0x1080 and queries[2] == 0x1100)
""", sections=MEMORY_MAP, setup=MEMORY_MAP_FIXTURE)

    def test_memory_map_invalid_or_nonadvancing_region_fails_closed(self) -> None:
        for metadata in (
            "true", '"invalid"', "{}",
            '{ BaseAddress = "256", RegionSize = 256 }',
            '{ BaseAddress = 256, RegionSize = "256" }',
            "{ BaseAddress = 256, RegionSize = 0 }",
            "{ BaseAddress = 256, RegionSize = -1 }",
            "{ BaseAddress = 0, RegionSize = 256 }",
            "{ BaseAddress = 0, RegionSize = 128 }",
        ):
            with self.subTest(metadata=metadata):
                self.assert_lua("""
regions = { region(0) }
local lookup = getMemoryRegionInfo
function getMemoryRegionInfo(address)
  local valid = lookup(address)
  if valid then return valid end
""" + f"  return {metadata}\n" + """
end
local result = handlers["memory.map"]({ limit = 1 })
assert(result.__error and result.code == "CE_API_UNAVAILABLE")
assert(result.recoverable == false and result.safeToRetry == false)
assert(result.items == nil and result.nextCursor == nil, "must not publish a partial page")
assert(enumerations == 0 and moduleEnumerations == 0 and #queries == 2 and #names == 1)
""", sections=MEMORY_MAP, setup=MEMORY_MAP_FIXTURE)

    def test_memory_map_region_query_exception_is_structured(self) -> None:
        self.assert_lua("""
function getMemoryRegionInfo(address)
  queries[#queries + 1] = address
  error("fixture query failure")
end
local result = handlers["memory.map"]({})
assert(result.__error and result.code == "CE_API_UNAVAILABLE")
assert(result.recoverable == true and result.safeToRetry == true)
assert(result.items == nil and result.nextCursor == nil)
assert(enumerations == 0 and moduleEnumerations == 0 and #queries == 1 and #names == 0)
""", sections=MEMORY_MAP, setup=MEMORY_MAP_FIXTURE)

    def test_memory_map_query_budget_is_exact_and_fails_closed(self) -> None:
        for mode in ("exact", "filtered", "cursor", "lookahead"):
            with self.subTest(mode=mode):
                self.assert_lua(f'local mode = "{mode}"\n' + """
for i = 1, 8193 do regions[i] = region((i - 1) * 0x100) end
modules = { { Name = "target.dll", Address = 0, Size = 8192 * 0x100 } }
local params = { limit = 1 }
if mode == "exact" then
  params.moduleFilter, params.cursor = "target", "8191"
elseif mode == "filtered" then
  params.stateFilter = "reserve"
elseif mode == "cursor" then
  params.cursor = "8192"
else
  params.cursor = "8191"
end
local result = handlers["memory.map"](params)
assert(enumerations == 0 and moduleEnumerations == (mode == "exact" and 1 or 0))
assert(#queries == 8192 and queries[8192] == 8191 * 0x100)
assert(#names == ((mode == "exact" or mode == "lookahead") and 1 or 0))
if mode == "exact" then
  assert(not result.__error and #result.items == 1 and result.truncated == false)
  assert(result.items[1].base.address == formatAddress(8191 * 0x100))
else
  assert(result.__error and result.code == "LIMIT_EXCEEDED")
  assert(result.recoverable == true and result.safeToRetry == false)
  assert(result.items == nil, "budget exhaustion must not return an incomplete page")
end
assert(result.nextCursor == nil)
""", sections=MEMORY_MAP, setup=MEMORY_MAP_FIXTURE)

    def test_modules_deduplicate_exact_metadata_only_before_pagination(self) -> None:
        self.assert_lua("""
local original = { Name = "target.dll", Address = 0x1000, Size = 0x100,
  Is64Bit = true, PathToFile = "C:/fixture/target.dll" }
local variants = { Name = "target-alias.dll", Address = 0x2000, Size = 0x200,
  Is64Bit = false, PathToFile = "C:/alias/target.dll" }
modules = { original, original }
for field, value in pairs(variants) do
  local row, duplicate = {}, {}
  for key, item in pairs(original) do row[key], duplicate[key] = item, item end
  row[field], duplicate[field] = value, value
  modules[#modules + 1], modules[#modules + 2] = row, duplicate
end
local result = handlers["symbols.modules"]({ nameFilter = "TARGET", limit = 100 })
assert(not result.__error and #result.items == 6 and not result.truncated)
assert(result.nextCursor == nil and moduleEnumerations == 1)
local unique = {}
for _, item in ipairs(result.items) do
  local key = table.concat({item.name, item.base.address, tostring(item.size),
    item.architecture, item.path}, "|")
  assert(not unique[key], "exact duplicate survived")
  unique[key] = true
end
local first = handlers["symbols.modules"]({ nameFilter = "target", limit = 5 })
assert(#first.items == 5 and first.truncated and first.nextCursor == "5")
local last = handlers["symbols.modules"]({ nameFilter = "target", limit = 5,
  cursor = first.nextCursor })
assert(#last.items == 1 and not last.truncated and last.nextCursor == nil)
assert(last.items[1].base.address == formatAddress(0x2000))
""", sections=(
            ("local function page(", 'handlers["memory.map"]'),
            ('handlers["symbols.modules"]', 'handlers["symbols.list"]'),
        ), setup=MEMORY_MAP_FIXTURE)

    def test_string_read_odd_wide_budget_only_converts_complete_units(self) -> None:
        for budget, value, read_count in ((1, '""', 0), (5, '"A" .. utf8.char(256)', 2)):
            with self.subTest(budget=budget):
                self.assert_lua(f"budget, unit = {budget}, 2\n" + """
memory = {65, 0, 0, 1, 0, 0}
local result = handlers["memory.read"]({ mode = "typed", address = base,
  dataType = "wstring", maxStringBytes = budget })
assert(not result.__error and result.complete == false)
assert(#result.unreadableRanges == 0 and #converted == 1)
assert(result.dataType == "wstring" and result.resolvedAddress.address == formatAddress(base))
""" + f"""
assert(result.value == {value})
assert(#reads == {read_count} and #converted[1] == {read_count * 2})
""", sections=MEMORY_READ, setup=STRING_FIXTURE)

    def test_string_read_exact_limit_does_not_probe_following_terminator(self) -> None:
        for data_type, unit, memory in (
            ("string", 1, "65, 66, 0"), ("wstring", 2, "65, 0, 66, 0, 0, 0"),
        ):
            with self.subTest(data_type=data_type):
                self.assert_lua(f"""
unit, budget, memory = {unit}, {2 * unit}, {{ {memory} }}
local result = handlers["memory.read"]({{ mode = "typed", address = base,
  dataType = "{data_type}", maxStringBytes = budget }})
""" + """
assert(not result.__error and result.value == "AB" and result.complete == false)
assert(#reads == 2 and reads[1].address == base and reads[2].address == base + unit)
assert(#converted == 1 and #converted[1] == budget and #result.unreadableRanges == 0)
""", sections=MEMORY_READ, setup=STRING_FIXTURE)

    def test_string_read_terminator_within_budget_is_complete_and_stops_reads(self) -> None:
        for data_type, unit in (("string", 1), ("wstring", 2)):
            for count in (0, 1, 2):
                with self.subTest(data_type=data_type, characters=count):
                    self.assert_lua(f"""
unit, budget = {unit}, {3 * unit}
local count = {count}
for i = 0, count - 1 do
  memory[i * unit + 1] = 65
  if unit == 2 then memory[i * unit + 2] = 0 end
end
for i = 1, unit do memory[count * unit + i] = 0 end
local result = handlers["memory.read"]({{ mode = "typed", address = base,
  dataType = "{data_type}", maxStringBytes = budget }})
""" + """
assert(not result.__error and result.value == string.rep("A", count))
assert(result.complete == true and #result.unreadableRanges == 0)
assert(#reads == count + 1 and #converted == 1 and #converted[1] == count * unit)
assert(reads[#reads].address == base + count * unit, "read beyond terminator")
""", sections=MEMORY_READ, setup=STRING_FIXTURE)

    def test_string_read_unreadable_unit_reports_error_or_partial_range(self) -> None:
        for data_type, unit in (("string", 1), ("wstring", 2)):
            for prefix in (0, 1):
                for partial_unit in range(unit):
                    with self.subTest(data_type=data_type, prefix=prefix, partial_unit=partial_unit):
                        self.assert_lua(f"""
unit, budget = {unit}, {2 * unit + 1}
local prefix, partial = {prefix}, {partial_unit}
if prefix == 1 then
  memory[1] = 65
  if unit == 2 then memory[2] = 0 end
end
if partial == 1 then memory[prefix * unit + 1] = 66 end
local result = handlers["memory.read"]({{ mode = "typed", address = base,
  dataType = "{data_type}", maxStringBytes = budget }})
""" + """
assert(#reads == prefix + 1 and reads[#reads].address == base + prefix * unit)
if prefix == 0 then
  assert(result.__error and result.code == "ACCESS_DENIED")
  assert(result.message:lower():find("string") and result.message:lower():find("read"))
  assert(result.recoverable == true and result.safeToRetry == true and #converted == 0)
else
  assert(not result.__error and result.value == "A" and result.complete == false)
  assert(#converted == 1 and #converted[1] == unit, "partial unit leaked to converter")
  assert(#result.unreadableRanges == 1)
  assert(result.unreadableRanges[1].address == formatAddress(base + unit))
  assert(result.unreadableRanges[1].size == unit)
end
""", sections=MEMORY_READ, setup=STRING_FIXTURE)

    def test_debug_events_generation_changes_reset_sequence_without_false_drops(self) -> None:
        for transition in ("pid", "forced", "detach"):
            with self.subTest(transition=transition):
                self.assert_lua(f'local transition = "{transition}"\n' + """
recordDebugStop("fixture", nil)
recordDebugStop("fixture", nil)
local old = handlers["debug.events.list"]({})
assert(state.debug.eventCounter == 2 and #old.items == 2)
if transition == "detach" then
  local detached = handlers["process.detach"]({})
  assert(detached.detached and state.generation == 8 and state.sessionId == nil)
  assert(state.debug.eventCounter == 0 and #state.debug.events == 0)
  state.logicalDetached = false
  refreshTarget(true)
elseif transition == "forced" then
  refreshTarget(true)
else
  nativePid = 43
  refreshTarget(false)
end
local generation = transition == "detach" and 9 or 8
assert(state.generation == generation and state.debug.eventCounter == 0)
assert(#state.debug.events == 0)
local tail = handlers["debug.events.list"]({})
assert(tail.session.generation == generation and #tail.items == 0)
assert(tail.nextCursor == string.format("dbg-%08x-00000000", generation))
assert(tail.droppedEvents == 0 and not tail.truncated)
nativeActive, nativeContext = true, true
recordDebugStop("fixture", nil)
recordDebugStop("fixture", nil)
local first = handlers["debug.events.list"]({ limit = 1 })
local polled = handlers["debug.events.list"]({ cursor = tail.nextCursor, limit = 1 })
assert(#first.items == 1 and first.droppedEvents == 0 and first.truncated)
assert(first.nextCursor == string.format("dbg-%08x-00000001", generation))
assert(#polled.items == 1 and polled.nextCursor == first.nextCursor and polled.droppedEvents == 0)
local second = handlers["debug.events.list"]({ cursor = first.nextCursor })
assert(#second.items == 1 and second.droppedEvents == 0 and not second.truncated)
assert(second.nextCursor == string.format("dbg-%08x-00000002", generation))
local stale = handlers["debug.events.list"]({ cursor = old.nextCursor })
assert(stale.__error and stale.code == "INVALID_CURSOR")
""", sections=DEBUG_LIFECYCLE, setup=DEBUG_LIFECYCLE_FIXTURE)

    def test_debugger_only_cleanup_preserves_event_sequence_and_tail_polling(self) -> None:
        self.assert_lua("""
recordDebugStop("fixture", nil)
recordDebugStop("fixture", nil)
local old = handlers["debug.events.list"]({})
local detached = handlers["debug.control.detach"]({})
assert(detached.detached and not detached.debugger.active)
assert(state.generation == 7 and state.debug.eventCounter == 2 and #state.debug.events == 0)
local tail = handlers["debug.events.list"]({ cursor = old.nextCursor })
assert(#tail.items == 0 and tail.nextCursor == old.nextCursor and not tail.truncated)
nativeActive, nativeContext = true, true
recordDebugStop("fixture", nil)
local nextPage = handlers["debug.events.list"]({ cursor = old.nextCursor })
assert(#nextPage.items == 1 and nextPage.droppedEvents == 0 and not nextPage.truncated)
assert(nextPage.items[1].eventId == "dbg-00000007-00000003")
assert(nextPage.nextCursor ~= old.nextCursor and state.debug.eventCounter == 3)
""", sections=DEBUG_LIFECYCLE, setup=DEBUG_LIFECYCLE_FIXTURE)

    def test_debug_events_initial_empty_poll_returns_generation_bound_tail_cursor(self) -> None:
        self.assert_lua("""
local result = handlers["debug.events.list"]({})
assert(not result.__error and #result.items == 0 and result.truncated == false)
assert(result.droppedEvents == 0)
assert(result.nextCursor == "dbg-00000007-00000000", "initial empty poll needs a tail cursor")
recordDebugStop("fixture", nil)
local nextPage = handlers["debug.events.list"]({ cursor = result.nextCursor })
assert(#nextPage.items == 1 and nextPage.items[1].eventId == "dbg-00000007-00000001")
assert(nextPage.droppedEvents == 0 and nextPage.truncated == false)
""", sections=DEBUG, setup=DEBUG_FIXTURE)

    def test_debug_events_rollover_preserves_sequence_without_duplicates_or_skips(self) -> None:
        for producer in ("recordDebugStop", "breakpointCallback"):
            with self.subTest(producer=producer):
                self.assert_lua("""
local installed = handlers["debug.breakpoints.set"]({ address = 0x1000 })
assert(not installed.__error)
""" + f"local function emit() {producer}(\"fixture\", nil) end\n" + """
for i = 1, 256 do emit() end
local page = handlers["debug.events.list"]({ limit = 200 })
assert(#page.items == 200 and page.truncated == true and page.droppedEvents == 0)
for i, event in ipairs(page.items) do
  assert(event.eventId == string.format("dbg-00000007-%08x", i))
end
assert(page.nextCursor == page.items[200].eventId)
for i = 1, 100 do emit() end
assert(#state.debug.events == 256 and state.debug.eventCounter == 356)
local nextPage = handlers["debug.events.list"]({ cursor = page.nextCursor, limit = 200 })
assert(#nextPage.items == 156 and nextPage.truncated == false and nextPage.droppedEvents == 0)
for i, event in ipairs(nextPage.items) do
  assert(event.eventId == string.format("dbg-00000007-%08x", 200 + i))
end
assert(nextPage.nextCursor == nextPage.items[156].eventId)
assert(nextPage.session.generation == 7)
""", sections=DEBUG, setup=DEBUG_FIXTURE)

    def test_debug_events_rollover_reports_only_lost_undelivered_events(self) -> None:
        self.assert_lua("""
for i = 1, 10 do recordDebugStop("fixture", nil) end
local first = handlers["debug.events.list"]({ limit = 10 })
assert(first.truncated == false and first.droppedEvents == 0)
for i = 11, 300 do recordDebugStop("fixture", nil) end
local page = handlers["debug.events.list"]({ cursor = first.nextCursor, limit = 100 })
assert(page.droppedEvents == 34 and #page.items == 100 and page.truncated == true)
for i, event in ipairs(page.items) do
  assert(event.eventId == string.format("dbg-00000007-%08x", 44 + i))
end
local nextPage = handlers["debug.events.list"]({ cursor = page.nextCursor, limit = 200 })
assert(nextPage.droppedEvents == 0 and #nextPage.items == 156 and nextPage.truncated == false)
for i, event in ipairs(nextPage.items) do
  assert(event.eventId == string.format("dbg-00000007-%08x", 144 + i))
end
local fromStart = handlers["debug.events.list"]({ limit = 1 })
assert(fromStart.droppedEvents == 44 and fromStart.items[1].eventId == page.items[1].eventId)
""", sections=DEBUG, setup=DEBUG_FIXTURE)

    def test_debug_events_empty_tail_retains_cursor_then_delivers_new_events_once(self) -> None:
        self.assert_lua("""
recordDebugStop("fixture", nil)
local page = handlers["debug.events.list"]({ limit = 1 })
assert(#page.items == 1 and page.truncated == false)
local cursor = assert(page.nextCursor)
for i = 1, 3 do
  page = handlers["debug.events.list"]({ cursor = cursor, limit = 1 })
  assert(#page.items == 0 and page.nextCursor == cursor)
  assert(page.truncated == false and page.droppedEvents == 0)
end
recordDebugStop("fixture", nil)
page = handlers["debug.events.list"]({ cursor = cursor, limit = 1 })
assert(#page.items == 1 and page.items[1].eventId == "dbg-00000007-00000002")
assert(page.nextCursor == page.items[1].eventId and page.truncated == false)
assert(page.droppedEvents == 0)
local tail = handlers["debug.events.list"]({ cursor = page.nextCursor })
assert(#tail.items == 0 and tail.nextCursor == page.nextCursor and tail.droppedEvents == 0)
""", sections=DEBUG, setup=DEBUG_FIXTURE)

    def test_debug_events_reject_stale_future_and_legacy_numeric_cursors(self) -> None:
        for cursor in ("dbg-00000006-00000001", "dbg-00000007-00000002", "0", "1", "", "garbage"):
            with self.subTest(cursor=cursor):
                self.assert_lua(f'local cursor = "{cursor}"\n' + """
recordDebugStop("fixture", nil)
local result = handlers["debug.events.list"]({ cursor = cursor })
assert(result.__error and result.code == "INVALID_CURSOR")
assert(result.recoverable == true and result.safeToRetry == false)
assert(result.items == nil and result.nextCursor == nil)
assert(state.debug.eventCounter == 1 and #state.debug.events == 1)
""", sections=DEBUG, setup=DEBUG_FIXTURE)

    def test_debug_events_page_limit_is_bounded(self) -> None:
        for limit, count in (("nil", 100), ("10000", 200), ("0", 1), ("-1", 1)):
            with self.subTest(limit=limit):
                self.assert_lua(f"local limit, count = {limit}, {count}\n" + """
for i = 1, 256 do recordDebugStop("fixture", nil) end
local page = handlers["debug.events.list"]({ limit = limit })
assert(#page.items == count and page.truncated == true and page.droppedEvents == 0)
assert(page.nextCursor == page.items[count].eventId)
""", sections=DEBUG, setup=DEBUG_FIXTURE)

    def test_debug_refresh_requires_literal_true_context_despite_cached_registers(self) -> None:
        for context in ("true", "false", "function() return true end", "nil", "1"):
            with self.subTest(context=context):
                self.assert_lua(f"nativeContext = {context}\n" + """
refreshDebugState()
assert(state.debug.active == true)
assert(state.debug.stopped == (nativeContext == true))
assert(activeCalls == 1 and #contextCalls == 1 and contextCalls[1] == false)
assert(RIP == 0x1234 and EIP == 0x5678, "cached registers remain populated")
""", sections=DEBUG, setup=DEBUG_FIXTURE)

    def test_debug_refresh_context_exception_is_not_a_stop(self) -> None:
        self.assert_lua("""
function debug_getContext(vectors)
  assert(vectors == false)
  error("fixture context unavailable")
end
refreshDebugState()
assert(state.debug.active == true and state.debug.stopped == false)
""", sections=DEBUG, setup=DEBUG_FIXTURE)

    def test_debug_refresh_preserves_process_suspension_without_context_query(self) -> None:
        self.assert_lua("""
state.debug.processSuspended, nativeContext = true, false
local summary = debugSummary()
assert(summary.stopped == true and summary.stopKind == "suspend")
assert(state.debug.processSuspended == true and state.debug.stopGeneration == 3)
assert(activeCalls == 1 and #contextCalls == 0)
nativeActive = false
refreshDebugState()
assert(state.debug.active == false and state.debug.stopped == true)
assert(state.debug.processSuspended == true and #contextCalls == 0)
""", sections=DEBUG, setup=DEBUG_FIXTURE)

    def test_require_debugger_stopped_rechecks_active_context_and_generation(self) -> None:
        for active, context, generation, code in (
            ("false", "true", "3", "DEBUGGER_NOT_ACTIVE"),
            ("function() return true end", "true", "3", "DEBUGGER_NOT_ACTIVE"),
            ("nil", "true", "3", "DEBUGGER_NOT_ACTIVE"),
            ("true", "false", "3", "TARGET_RUNNING"),
            ("true", "true", "2", "STALE_STOP"),
            ("true", "true", "nil", "STALE_STOP"),
            ("true", "true", '"3"', None),
        ):
            with self.subTest(active=active, context=context, generation=generation):
                self.assert_lua(f"""
nativeActive, nativeContext = {active}, {context}
local result = requireDebuggerStopped({generation})
local expected = {repr(code) if code else 'nil'}
""" + """
if expected then assert(result.__error and result.code == expected)
else assert(result == nil) end
assert(activeCalls == 1 and #contextCalls == (nativeActive == true and 1 or 0))
assert(state.debug.stopGeneration == 3)
""", sections=DEBUG, setup=DEBUG_FIXTURE)

    def test_register_read_refuses_suspended_process_context(self) -> None:
        self.assert_lua("""
state.debug.processSuspended = true
local result = handlers["debug.registers.read"]({ expectedStopGeneration = 3 })
assert(result.__error and result.code == "CONTEXT_UNAVAILABLE")
assert(#contextCalls == 0 and state.debug.stopped == true)
assert(state.debug.processSuspended == true)
""", sections=DEBUG, setup=DEBUG_FIXTURE)

    def test_register_read_requires_fresh_literal_true_after_stop_validation(self) -> None:
        for outcome in ("return false", "return nil", "return function() end",
                        'error("fixture context lost")', "return true"):
            with self.subTest(outcome=outcome):
                self.assert_lua("""
function debug_getContext(vectors)
  contextCalls[#contextCalls + 1] = vectors
  if #contextCalls == 1 then return true end
  RAX = 0xABCD
""" + outcome + """
end
local result = handlers["debug.registers.read"]({ expectedStopGeneration = 3 })
assert(#contextCalls == 2 and contextCalls[1] == false and contextCalls[2] == false)
""" + ("""
assert(not result.__error and result.general.rax == formatAddress(0xABCD))
assert(result.general.rip == formatAddress(RIP) and result.stopGeneration == 3)
""" if outcome == "return true" else """
assert(result.__error and result.code == "CE_API_UNAVAILABLE")
assert(result.general == nil and result.safeToRetry == false)
"""), sections=DEBUG, setup=DEBUG_FIXTURE)

    def test_breakpoint_callback_returns_one_global_default_returns_zero(self) -> None:
        self.assert_lua("""
installDebuggerCallback()
local result = handlers["debug.breakpoints.set"]({ address = 0x1000 })
assert(not result.__error and hardwareCalls == 1)
assert(type(breakpointCallback) == "function" and breakpointCallback() == 1)
local event = state.debug.events[1]
assert(event.kind == "breakpoint" and event.breakpointId == result.breakpoint.breakpointId)
assert(event.stopGeneration == 4 and event.generation == 7)
assert(event.address.address == formatAddress(RIP) and state.debug.stopped == true)
assert(debugger_onBreakpoint() == 0)
assert(#state.debug.events == 2 and state.debug.events[2].kind == "external_break")
assert(state.debug.stopGeneration == 5)
""", sections=DEBUG, setup=DEBUG_FIXTURE)

    def test_prior_wait_callback_records_step_before_delegation_and_rejects_old_stop(self) -> None:
        self.assert_lua("""
local calls = 0
local prior = function()
  calls = calls + 1
  assert(state.debug.stopGeneration == 4 and state.debug.pendingStep == nil)
  assert(#state.debug.events == 1 and state.debug.events[1].kind == "step")
  return 0
end
debugger_onBreakpoint = prior
installDebuggerCallback()
local continued = handlers["debug.control.continue"]({ mode = "step_into", expectedStopGeneration = 3 })
assert(not continued.__error and continueCalls[1] == co_stepinto)
nativeContext, RIP = true, 0x1235
assert(debugger_onBreakpoint() == 0 and calls == 1)
assert(state.debug.previousOnBreakpoint == prior)
local summary = debugSummary()
assert(summary.stopped and summary.stopKind == "debugger" and summary.stopGeneration == 4)
assert(state.debug.events[1].address.address == formatAddress(RIP))
local staleRead = handlers["debug.registers.read"]({ expectedStopGeneration = 3 })
local staleRun = handlers["debug.control.continue"]({ mode = "run", expectedStopGeneration = 3 })
assert(staleRead.__error and staleRead.code == "STALE_STOP")
assert(staleRun.__error and staleRun.code == "STALE_STOP" and #continueCalls == 1)
local current = handlers["debug.registers.read"]({ expectedStopGeneration = 4 })
assert(not current.__error and current.stopGeneration == 4 and current.general.rip == formatAddress(RIP))
""", sections=DEBUG, setup=DEBUG_FIXTURE)

    def test_prior_handled_callback_preserves_return_without_claiming_native_stop(self) -> None:
        self.assert_lua("""
local calls = 0
debugger_onBreakpoint = function()
  calls = calls + 1
  assert(state.debug.stopGeneration == 3 + calls and state.debug.pendingStep == nil)
  assert(#state.debug.events == calls)
  nativeContext = false
  return 1
end
installDebuggerCallback()
local continued = handlers["debug.control.continue"]({ mode = "step_over", expectedStopGeneration = 3 })
assert(not continued.__error and continueCalls[1] == co_stepover)
RIP = 0x1235
assert(debugger_onBreakpoint() == 1 and calls == 1)
assert(state.debug.events[1].kind == "step" and state.debug.events[1].stopGeneration == 4)
local summary = debugSummary()
assert(not summary.stopped and summary.stopKind == "none" and summary.stopGeneration == 4)
local read = handlers["debug.registers.read"]({ expectedStopGeneration = 4 })
local run = handlers["debug.control.continue"]({ mode = "run", expectedStopGeneration = 4 })
assert(read.__error and read.code == "TARGET_RUNNING")
assert(run.__error and run.code == "TARGET_RUNNING" and #continueCalls == 1)
assert(debugger_onBreakpoint() == 1 and calls == 2)
assert(state.debug.events[2].kind == "external_break", "handled callback must consume pending step")
assert(not debugSummary().stopped)
""", sections=DEBUG, setup=DEBUG_FIXTURE)

    def test_continue_uses_native_modes_and_pending_step_global_event(self) -> None:
        for mode, native in (("run", "co_run"), ("step_into", "co_stepinto"),
                             ("step_over", "co_stepover")):
            with self.subTest(mode=mode):
                self.assert_lua(f'local mode, native = "{mode}", {native}\n' + """
-- Full hardware slots must not prevent native stepping.
for i = 1, 4 do state.debug.breakpoints[i] = {} end
installDebuggerCallback()
local result = handlers["debug.control.continue"]({ mode = mode, expectedStopGeneration = 3 })
assert(not result.__error and #continueCalls == 1 and continueCalls[1] == native)
assert(hardwareCalls == 0 and state.debug.stopped == false)
assert(state.debug.pendingStep == (mode ~= "run" and mode or nil))
assert(#state.debug.events == 0 and state.debug.stopGeneration == 3)
nativeContext, RIP = true, 0x1235
assert(debugger_onBreakpoint() == 0)
local event = state.debug.events[1]
assert(event.kind == (mode == "run" and "external_break" or "step"))
assert(event.address.address == formatAddress(RIP) and event.stopGeneration == 4)
assert(state.debug.pendingStep == nil and state.debug.stopped == true)
assert(debugger_onBreakpoint() == 0)
assert(state.debug.events[2].kind == "external_break", "pending step must be consumed once")
""", sections=DEBUG, setup=DEBUG_FIXTURE)

    def test_failed_continue_clears_pending_step(self) -> None:
        for outcome in ("return false", 'error("fixture continue failure")'):
            with self.subTest(outcome=outcome):
                self.assert_lua("""
function debug_continueFromBreakpoint(mode)
  assert(mode == co_stepinto and state.debug.pendingStep == "step_into")
""" + outcome + """
end
local result = handlers["debug.control.continue"]({ mode = "step_into", expectedStopGeneration = 3 })
assert(result.__error and result.code == "CE_API_UNAVAILABLE")
assert(state.debug.pendingStep == nil and state.debug.stopped == true)
assert(hardwareCalls == 0 and #state.debug.events == 0)
""", sections=DEBUG, setup=DEBUG_FIXTURE)


if __name__ == "__main__":
    unittest.main()
