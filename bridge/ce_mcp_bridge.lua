-- CE MCP Backend bridge v0.1
--
-- Scope: a deliberately small, local-only CE 7.5 adapter. Public MCP,
-- authentication, policy, schemas, analysis, and workflows remain in the Python
-- sidecar. Blocking pipe I/O runs on a worker; all CE API calls run on CE's main
-- thread through thread.synchronize.

local existingInstance = rawget(_G, "CE_MCP_BRIDGE_INSTANCE")
if existingInstance and existingInstance.running then
  existingInstance.diagnostic = "autorun-reentry-ignored"
  print("[CE MCP bridge] autorun re-entry ignored; existing worker remains active")
  return
end

local PIPE_NAME = os.getenv("CE_MCP_PIPE_NAME")
  or ("CE_MCP_Backend_v1_" .. tostring(getCheatEngineProcessID()))
if #PIPE_NAME > 128 or not PIPE_NAME:match("^[A-Za-z0-9_.-]+$") then
  error("CE_MCP_PIPE_NAME must contain only local pipe-name characters")
end
local BRIDGE_VERSION = "0.2.0"
local PROTOCOL_VERSION = 1
local MAX_FRAME_BYTES = 8 * 1024 * 1024

-- Snapshot bridge policy at startup. Changing the global later does not elevate
-- an already-running bridge; reload the bridge after intentionally changing it.
local configuredPolicy = rawget(_G, "CE_MCP_POLICY")
local bridgeHypervisorEnabled = type(configuredPolicy) == "table"
  and configuredPolicy.hypervisor == true
  and type(configuredPolicy.authorizationToken) == "string"
  and #configuredPolicy.authorizationToken >= 32
local bridgeAuthorizationToken = bridgeHypervisorEnabled
  and configuredPolicy.authorizationToken or nil

local function constantTimeEqual(left, right)
  if type(left) ~= "string" or type(right) ~= "string" then return false end
  local difference = #left == #right and 0 or 1
  local length = math.max(#left, #right)
  for index = 1, length do
    local a = left:byte(index) or 0
    local b = right:byte(index) or 0
    if a ~= b then difference = difference + 1 end
  end
  return difference == 0
end

local state = {
  running = false,
  connected = false,
  worker = nil,
  pipe = nil,
  serverLaunchAttempted = false,
  pid = 0,
  generation = 0,
  sessionId = nil,
  logicalDetached = false,
  operations = {},
  operationCounter = 0,
  debug = {
    active = false, stopped = false, stopGeneration = 0,
    eventCounter = 0, events = {}, breakpoints = {}, breakpointCounter = 0,
    previousOnBreakpoint = nil, callbackInstalled = false,
    stepAddresses = {}, processSuspended = false,
  },
  hypervisor = { watches = {}, watchCounter = 0, trace = nil, traceCounter = 0 },
  diagnostic = "startup",
}
_G.CE_MCP_BRIDGE_INSTANCE = state

local function destroyOperation(operation)
  if operation.startTimer then
    pcall(function() operation.startTimer.destroy() end)
    operation.startTimer = nil
  end
  if operation.waitThread then
    pcall(function() operation.waitThread.terminate() end)
    operation.waitThread = nil
  end
  if operation.scanThread then
    if operation.kind ~= "signature"
      or operation.state == "queued" or operation.state == "running" then
      pcall(function() operation.scanThread.terminate() end)
    end
    operation.scanThread = nil
  end
  if operation.foundList then
    pcall(function() operation.foundList.destroy() end)
    operation.foundList = nil
  end
  if operation.memScan then
    pcall(function() operation.memScan.destroy() end)
    operation.memScan = nil
  end
end

local function cleanupOperations()
  for _, operation in pairs(state.operations) do destroyOperation(operation) end
  state.operations = {}
end

local function cleanupDebugger()
  local debugState = state.debug
  if debugState.callbackInstalled then _G.debugger_onBreakpoint = debugState.previousOnBreakpoint end
  debugState.callbackInstalled = false
  for _, address in ipairs(debugState.stepAddresses) do pcall(debug_removeBreakpoint, address) end
  debugState.stepAddresses = {}
  debugState.previousOnBreakpoint = nil
  for _, breakpoint in pairs(debugState.breakpoints) do
    pcall(debug_removeBreakpoint, breakpoint.address)
  end
  debugState.breakpoints = {}
  if debugState.processSuspended then
    pcall(unpause)
  elseif debugState.stopped then
    pcall(debug_continueFromBreakpoint, co_run)
  end
  debugState.processSuspended = false
  local ok, active = pcall(debug_isDebugging)
  if ok and active then pcall(detachIfPossible) end
  debugState.active = false
  debugState.stopped = false
  debugState.events = {}
end

local function cleanupHypervisor()
  local hypervisor = state.hypervisor
  for _, watch in pairs(hypervisor.watches) do
    pcall(dbvm_watch_disable, watch.nativeId)
  end
  hypervisor.watches = {}
  if hypervisor.trace then
    pcall(dbvm_traceonbp_stoptrace)
    pcall(dbvm_traceonbp_remove, hypervisor.trace.physicalAddress, true)
    hypervisor.trace = nil
  end
end

-- Minimal JSON codec adapted from the pure-Lua codec in the surveyed
-- cheatengine-mcp-bridge. It is kept local so the bridge has no Lua module
-- dependency. The bridge protocol only permits JSON objects at frame roots.
local json = {}
local encode
local escapes = {
  ["\\"] = "\\", ["\""] = "\"", ["\b"] = "b", ["\f"] = "f",
  ["\n"] = "n", ["\r"] = "r", ["\t"] = "t"
}
local unescapes = { ["/"] = "/" }
for k, v in pairs(escapes) do unescapes[v] = k end

local function escapeChar(c)
  return "\\" .. (escapes[c] or string.format("u%04x", c:byte()))
end

local function encodeTable(value, stack)
  local output = {}
  stack = stack or {}
  if stack[value] then error("circular JSON value") end
  stack[value] = true
  if rawget(value, 1) ~= nil or next(value) == nil then
    for _, item in ipairs(value) do output[#output + 1] = encode(item, stack) end
    stack[value] = nil
    return "[" .. table.concat(output, ",") .. "]"
  end
  for key, item in pairs(value) do
    if type(key) ~= "string" then key = tostring(key) end
    output[#output + 1] = encode(key, stack) .. ":" .. encode(item, stack)
  end
  stack[value] = nil
  return "{" .. table.concat(output, ",") .. "}"
end

local encoders = {
  ["nil"] = function() return "null" end,
  ["table"] = encodeTable,
  ["string"] = function(v) return '"' .. v:gsub('[%z\1-\31\\"]', escapeChar) .. '"' end,
  ["number"] = function(v)
    if v ~= v or v <= -math.huge or v >= math.huge then return "null" end
    return string.format("%.14g", v)
  end,
  ["boolean"] = tostring,
}
encode = function(value, stack)
  local encoder = encoders[type(value)]
  if not encoder then error("unsupported JSON type: " .. type(value)) end
  return encoder(value, stack)
end
json.encode = encode

local function skipWhitespace(text, position)
  return text:find("%S", position) or #text + 1
end

local decode
local function decodeString(text, position)
  local start = position + 1
  local finish = position
  while true do
    finish = text:find('["\\]', finish + 1)
    if not finish then error("unterminated JSON string") end
    if text:sub(finish, finish) == '"' then break end
    finish = finish + 1
  end
  local value = text:sub(start, finish - 1)
  value = value:gsub("\\.", function(c) return unescapes[c:sub(2)] or c end)
  value = value:gsub("\\u(%x%x%x%x)", function(hex)
    local code = tonumber(hex, 16)
    if code <= 0x7F then return string.char(code) end
    if code <= 0x7FF then
      return string.char(0xC0 + math.floor(code / 0x40), 0x80 + code % 0x40)
    end
    return string.char(
      0xE0 + math.floor(code / 0x1000),
      0x80 + math.floor(code / 0x40) % 0x40,
      0x80 + code % 0x40
    )
  end)
  return value, finish + 1
end

local function decodeNumber(text, position)
  local token = text:match("^-?%d+%.?%d*[eE]?[+-]?%d*", position)
  local value = token and tonumber(token)
  if not value then error("invalid JSON number") end
  return value, position + #token
end

local function decodeLiteral(text, position)
  if text:sub(position, position + 3) == "true" then return true, position + 4 end
  if text:sub(position, position + 4) == "false" then return false, position + 5 end
  if text:sub(position, position + 3) == "null" then return nil, position + 4 end
  error("invalid JSON literal")
end

local function decodeArray(text, position)
  local result = {}
  position = skipWhitespace(text, position + 1)
  if text:sub(position, position) == "]" then return result, position + 1 end
  while true do
    local value
    value, position = decode(text, position)
    result[#result + 1] = value
    position = skipWhitespace(text, position)
    local c = text:sub(position, position)
    if c == "]" then return result, position + 1 end
    if c ~= "," then error("expected ',' or ']' in JSON array") end
    position = skipWhitespace(text, position + 1)
  end
end

local function decodeObject(text, position)
  local result = {}
  position = skipWhitespace(text, position + 1)
  if text:sub(position, position) == "}" then return result, position + 1 end
  while true do
    if text:sub(position, position) ~= '"' then error("expected JSON object key") end
    local key
    key, position = decodeString(text, position)
    position = skipWhitespace(text, position)
    if text:sub(position, position) ~= ":" then error("expected ':' after JSON key") end
    local value
    value, position = decode(text, skipWhitespace(text, position + 1))
    result[key] = value
    position = skipWhitespace(text, position)
    local c = text:sub(position, position)
    if c == "}" then return result, position + 1 end
    if c ~= "," then error("expected ',' or '}' in JSON object") end
    position = skipWhitespace(text, position + 1)
  end
end

decode = function(text, position)
  position = skipWhitespace(text, position or 1)
  local c = text:sub(position, position)
  if c == '"' then return decodeString(text, position) end
  if c == "{" then return decodeObject(text, position) end
  if c == "[" then return decodeArray(text, position) end
  if c == "-" or c:match("%d") then return decodeNumber(text, position) end
  return decodeLiteral(text, position)
end
json.decode = function(text)
  local value, position = decode(text, 1)
  if skipWhitespace(text, position) <= #text then error("trailing JSON data") end
  return value
end

local function log(message)
  print("[CE MCP bridge " .. BRIDGE_VERSION .. "] " .. tostring(message))
end

local function errorDetail(code, message, recoverable, safeToRetry, suggestedAction)
  local detail = {
    __error = true,
    code = code,
    message = message,
    recoverable = recoverable == true,
    safeToRetry = safeToRetry == true,
  }
  if suggestedAction then detail.suggestedAction = suggestedAction end
  return detail
end

local function pointerWidth()
  local ok, is64 = pcall(targetIs64Bit)
  return (ok and is64) and 64 or 32
end

local function formatAddress(address)
  if pointerWidth() == 64 then return string.format("0x%016X", address) end
  return string.format("0x%08X", address)
end

local function openedProcessStillExists(pid)
  if not pid or pid == 0 then return true, false end
  local ok, processList = pcall(getProcesslist)
  if not ok or type(processList) ~= "table" then return false, false end
  for key, value in pairs(processList) do
    if type(key) == "number" and key == pid then return true, true end
    if type(value) == "string" then
      local hexPid = value:match("^(%x+)%-")
      if hexPid and tonumber(hexPid, 16) == pid then return true, true end
    end
  end
  return true, false
end

local function refreshTarget(forceGeneration)
  local pid = getOpenedProcessID() or 0
  if pid > 0 then
    local queried, exists = openedProcessStillExists(pid)
    if queried and not exists then pid = 0 end
  end
  if forceGeneration or pid ~= state.pid then
    cleanupDebugger()
    cleanupOperations()
    cleanupHypervisor()
    state.generation = state.generation + 1
    state.pid = pid
    state.sessionId = pid > 0 and string.format("ce-%08x-%08x", pid, state.generation) or nil
  end
  return pid
end

local function session()
  local pid = refreshTarget(false)
  if pid == 0 or state.logicalDetached then return nil end
  local width = pointerWidth()
  return {
    sessionId = state.sessionId,
    generation = state.generation,
    state = state.debug.stopped and "paused" or "running",
    pid = pid,
    architecture = width == 64 and "x86_64" or "x86",
    pointerWidth = width,
  }
end

local function requireTarget()
  if refreshTarget(false) == 0 or state.logicalDetached then
    return errorDetail("NO_TARGET", "No target process is attached", true, true)
  end
  return nil
end

local handlers = {}

handlers["status.get"] = function(_)
  local current = session()
  local dbkQuery = rawget(_G, "dbk_initialized")
  local dbvmQuery = rawget(_G, "dbvm_initialized")
  local watchApi = type(rawget(_G, "dbvm_watch_writes")) == "function"
    and type(rawget(_G, "dbvm_watch_reads")) == "function"
    and type(rawget(_G, "dbvm_watch_executes")) == "function"
    and type(rawget(_G, "dbvm_watch_retrievelog")) == "function"
    and type(rawget(_G, "dbvm_watch_disable")) == "function"
  local traceApi = type(rawget(_G, "dbvm_traceonbp")) == "function"
    and type(rawget(_G, "dbvm_traceonbp_getstatus")) == "function"
    and type(rawget(_G, "dbvm_traceonbp_stoptrace")) == "function"
    and type(rawget(_G, "dbvm_traceonbp_remove")) == "function"
    and type(rawget(_G, "dbvm_traceonbp_retrievelog")) == "function"
  local dbkLoaded, dbvmLoaded = false, false
  local readiness = "unverified"
  if type(dbkQuery) == "function" then
    local ok, value = pcall(dbkQuery)
    dbkLoaded = ok and value == true
  end
  if type(dbvmQuery) == "function" then
    local ok, value = pcall(dbvmQuery)
    dbvmLoaded = ok and value == true
  end
  if type(dbkQuery) == "function" and type(dbvmQuery) == "function" then
    readiness = dbkLoaded and dbvmLoaded and "ready" or "not-ready"
  end
  local disabledReasons = {
    ["scan.refine.comparison"] = "between/bigger/smaller modes are not probe-verified",
    ["pointer.scan"] = "CE Lua exposes no verified result-bearing, cancellable pointer scanner lifecycle",
  }
  local hypervisorReason
  if not bridgeHypervisorEnabled then
    hypervisorReason = "bridge hypervisor policy is disabled"
  elseif type(dbkQuery) == "function" and not dbkLoaded then
    hypervisorReason = "DBK driver is not loaded; status did not initialize it"
  elseif type(dbvmQuery) == "function" and not dbvmLoaded then
    hypervisorReason = "DBVM is not loaded; status did not initialize it"
  end
  disabledReasons["dbvm.watch"] = watchApi and hypervisorReason or "DBVM watch API is unavailable"
  disabledReasons["dbvm.trace"] = traceApi and hypervisorReason or "DBVM trace API is unavailable"
  local result = {
    bridge = { connected = true, version = BRIDGE_VERSION, diagnostic = state.diagnostic,
      dbvmReadiness = readiness },
    capabilities = {
      available = {
        "memory.read", "memory.map", "memory.compare", "memory.checksum", "process.list", "process.attach",
        "disassembly.read", "symbols.resolve", "symbols.enumerate",
        "scan.initial", "scan.refine.exact", "scan.refine.relative",
        "scan.results", "operations.cancel", "pointer.resolve", "pointer.validate"
        , "debug.control", "debug.breakpoints", "debug.events", "threads.list", "debug.registers.read"
        , "signature.generate"
        , "structures.workspace"
      },
      enabled = {
        "memory.read", "memory.map", "memory.compare", "memory.checksum", "process.list", "process.attach",
        "disassembly.read", "symbols.resolve", "symbols.enumerate",
        "scan.initial", "scan.refine.exact", "scan.refine.relative",
        "scan.results", "operations.cancel", "pointer.resolve", "pointer.validate"
        , "debug.control", "debug.breakpoints", "debug.events", "threads.list", "debug.registers.read"
        , "signature.generate"
        , "structures.workspace"
      },
      disabledReasons = disabledReasons,
      limits = {
        maxReadBytes = 1048576, maxFrameBytes = MAX_FRAME_BYTES,
        maxMemoryAnalysisBytes = 1048576,
        maxSignatureRangeBytes = 67108864, maxSignatureBytes = 64,
        maxStructures = 128, maxStructureFields = 256, maxStructureBytes = 65536,
        maxPageItems = 200, maxScanHandles = 8, maxConcurrentScans = 1,
        maxHardwareBreakpoints = 4, maxDebugEvents = 256,
        maxDbvmWatches = 8, maxDbvmWatchBytes = 8,
        maxDbvmWatchEntries = 1024, maxDbvmTraceSteps = 1024,
      },
    },
  }
  if watchApi then result.capabilities.available[#result.capabilities.available + 1] = "dbvm.watch" end
  if traceApi then result.capabilities.available[#result.capabilities.available + 1] = "dbvm.trace" end
  if hypervisorReason == nil then
    if watchApi then result.capabilities.enabled[#result.capabilities.enabled + 1] = "dbvm.watch" end
    if traceApi then result.capabilities.enabled[#result.capabilities.enabled + 1] = "dbvm.trace" end
    result.capabilities.disabledReasons["dbvm.watch"] = nil
    result.capabilities.disabledReasons["dbvm.trace"] = nil
  end
  if current then result.session = current end
  return result
end

handlers["process.list"] = function(params)
  local ok, processList = pcall(getProcesslist)
  if not ok then return errorDetail("CE_API_UNAVAILABLE", tostring(processList), true, true) end
  local items = {}
  for key, value in pairs(processList or {}) do
    local pid, name
    if type(key) == "number" and type(value) == "string" then
      pid, name = key, value
    elseif type(value) == "string" then
      local hexPid, parsedName = value:match("^(%x+)%-(.+)$")
      if hexPid then pid, name = tonumber(hexPid, 16), parsedName end
    end
    local filter = params.nameFilter and tostring(params.nameFilter):lower() or nil
    if pid and name and (not filter or name:lower():find(filter, 1, true)) then
      items[#items + 1] = { pid = pid, name = name }
    end
  end
  table.sort(items, function(a, b)
    if a.pid == b.pid then return a.name < b.name end
    return a.pid < b.pid
  end)
  local offset = tonumber(params.cursor or "0") or 0
  local limit = math.max(1, math.min(tonumber(params.limit) or 100, 200))
  local page = {}
  for i = offset + 1, math.min(offset + limit, #items) do page[#page + 1] = items[i] end
  local nextOffset = offset + #page
  local result = { items = page, truncated = nextOffset < #items }
  if result.truncated then result.nextCursor = tostring(nextOffset) end
  return result
end

handlers["process.attach"] = function(params)
  local pid = tonumber(params.pid)
  if not pid or pid < 1 then return errorDetail("INVALID_PARAMS", "pid must be positive", true, true) end
  state.diagnostic = "attach-scheduled:" .. tostring(pid)
  local timer = createTimer(nil)
  -- Give the pipe worker enough time to flush the accepted response before CE
  -- rebuilds the Lua state during openProcess.
  timer.Interval = 1000
  timer.OnTimer = function(sender)
    sender.destroy()
    state.diagnostic = "attach-opening:" .. tostring(pid)
    state.logicalDetached = false
    -- Explicit reattach to the same CE-opened PID must still create a fresh MCP
    -- session. openProcess may otherwise leave CE's PID unchanged and no target
    -- transition would regenerate sessionId.
    refreshTarget(true)
    local ok, message = pcall(openProcess, pid)
    state.diagnostic = "attach-open-returned:" .. tostring(ok) .. ":" .. tostring(message)
  end
  return { pending = true, pid = pid }
end

handlers["process.get"] = function(_)
  local missing = requireTarget()
  if missing then return missing end
  return { session = session() }
end

handlers["process.detach"] = function(_)
  local missing = requireTarget()
  if missing then return missing end
  -- CE 7.5 exposes debugger detach, but no reliable Lua operation that closes
  -- the currently opened process handle. End the MCP session immediately and
  -- report that CE itself retains its handle instead of claiming otherwise.
  state.logicalDetached = true
  cleanupDebugger()
  cleanupOperations()
  state.generation = state.generation + 1
  state.sessionId = nil
  state.diagnostic = "logical-detach:ce-handle-retained"
  return { detached = true, ceHandleRetained = true }
end

handlers["memory.read"] = function(params)
  local missing = requireTarget()
  if missing then return missing end
  local address = getAddressSafe(params.address)
  if not address then return errorDetail("ADDRESS_UNRESOLVED", "Address could not be resolved", true, true) end
  local pointerPath = {
    { address = formatAddress(address), pointerWidth = pointerWidth() }
  }
  for _, rawOffset in ipairs(params.followPointers or {}) do
    local pointer = readPointer(address)
    local offset = tonumber(rawOffset) or tonumber(tostring(rawOffset):gsub("^0[xX]", ""), 16)
    if not pointer or not offset then
      return errorDetail("ADDRESS_UNRESOLVED", "Pointer chain could not be resolved", true, true)
    end
    address = pointer + offset
    pointerPath[#pointerPath + 1] = {
      address = formatAddress(address), pointerWidth = pointerWidth()
    }
  end
  if params.mode == "raw" then
    local size = tonumber(params.size)
    if not size or size < 1 or size > 1048576 then
      return errorDetail("LIMIT_EXCEEDED", "size must be between 1 and 1048576", true, true)
    end
    local bytes = readBytes(address, size, true)
    if not bytes then return errorDetail("ACCESS_DENIED", "Target memory could not be read", true, true) end
    local hex = {}
    for i, byte in ipairs(bytes) do hex[i] = string.format("%02X", byte) end
    return {
      session = session(),
      resolvedAddress = { address = formatAddress(address), pointerWidth = pointerWidth() },
      pointerPath = pointerPath,
      bytes = table.concat(hex),
      encoding = "hex",
      complete = #bytes == size,
      unreadableRanges = {},
    }
  end

  local dataType = params.dataType
  local count = math.max(1, math.min(tonumber(params.count) or 1, 65536))
  local widths = {
    u8 = 1, i8 = 1, u16 = 2, i16 = 2, u32 = 4, i32 = 4,
    u64 = 8, i64 = 8, f32 = 4, f64 = 8, pointer = pointerWidth() / 8,
  }
  local width = widths[dataType]
  if dataType == "string" or dataType == "wstring" then
    local maxBytes = math.max(1, math.min(tonumber(params.maxStringBytes) or 256, 65536))
    local value = readString(address, maxBytes, dataType == "wstring")
    if value == nil then return errorDetail("ACCESS_DENIED", "Target string could not be read", true, true) end
    return {
      session = session(),
      resolvedAddress = { address = formatAddress(address), pointerWidth = pointerWidth() },
      pointerPath = pointerPath,
      dataType = dataType,
      value = value,
      complete = true,
      unreadableRanges = {},
    }
  end
  if not width then return errorDetail("INVALID_PARAMS", "Unsupported dataType", true, true) end

  local function integerFromBytes(bytes, signed)
    local value = 0
    for index = #bytes, 1, -1 do value = (value << 8) | bytes[index] end
    if signed and #bytes < 8 then
      local sign = 1 << (#bytes * 8 - 1)
      if (value & sign) ~= 0 then value = value - (1 << (#bytes * 8)) end
    end
    return value
  end

  local values = {}
  for index = 0, count - 1 do
    local itemAddress = address + index * width
    local value
    if dataType == "f32" then value = readFloat(itemAddress)
    elseif dataType == "f64" then value = readDouble(itemAddress)
    elseif dataType == "pointer" then
      local pointer = readPointer(itemAddress)
      value = pointer and formatAddress(pointer) or nil
    else
      local bytes = readBytes(itemAddress, width, true)
      if bytes then
        if dataType == "u64" then
          local parts = {}
          for byteIndex = #bytes, 1, -1 do parts[#parts + 1] = string.format("%02X", bytes[byteIndex]) end
          value = "0x" .. table.concat(parts)
        else
          value = integerFromBytes(bytes, dataType:sub(1, 1) == "i")
          if dataType == "i64" then value = tostring(value) end
        end
      end
    end
    if value == nil then return errorDetail("ACCESS_DENIED", "Typed target memory could not be read", true, true) end
    values[#values + 1] = value
  end
  return {
    session = session(),
    resolvedAddress = { address = formatAddress(address), pointerWidth = pointerWidth() },
    pointerPath = pointerPath,
    dataType = dataType,
    value = count == 1 and values[1] or values,
    complete = true,
    unreadableRanges = {},
  }
end

handlers["memory.compare"] = function(params)
  local left = getAddressSafe(params.leftAddress)
  local right = getAddressSafe(params.rightAddress)
  local size = tonumber(params.size)
  if not left or not right then
    return errorDetail("ADDRESS_UNRESOLVED", "Both target addresses must resolve", true, true)
  end
  if not size or size < 1 or size > 1048576 then
    return errorDetail("LIMIT_EXCEEDED", "size must be between 1 and 1048576", true, true)
  end
  local called, equal, firstDifference = pcall(compareMemory, left, right, size, 0)
  if not called then
    return errorDetail("ACCESS_DENIED", "Target memory comparison failed: " .. tostring(equal), true, true)
  end
  local result = { session = session(), equal = equal == true, size = size }
  if equal ~= true then
    local offset = tonumber(firstDifference)
    if not offset or offset < 0 or offset >= size then
      return errorDetail("ACCESS_DENIED", "Comparison did not return a valid difference offset", true, true)
    end
    result.firstDifference = offset
  end
  return result
end

handlers["memory.checksum"] = function(params)
  local address = getAddressSafe(params.address)
  local size = tonumber(params.size)
  if not address then
    return errorDetail("ADDRESS_UNRESOLVED", "Target address could not be resolved", true, true)
  end
  if not size or size < 1 or size > 1048576 then
    return errorDetail("LIMIT_EXCEEDED", "size must be between 1 and 1048576", true, true)
  end
  if params.algorithm ~= nil and params.algorithm ~= "md5" then
    return errorDetail("INVALID_PARAMS", "Only md5 is supported by CE 7.5", true, true)
  end
  local called, digest = pcall(md5memory, address, size)
  digest = called and digest and tostring(digest):lower() or nil
  if not digest or #digest ~= 32 or not digest:match("^[0-9a-f]+$") then
    return errorDetail("ACCESS_DENIED", "Target memory checksum failed", true, true)
  end
  return { session = session(), size = size, algorithm = "md5", digest = digest }
end

local structureFixedWidths = {
  u8 = 1, i8 = 1, u16 = 2, i16 = 2, u32 = 4, i32 = 4,
  u64 = 8, i64 = 8, f32 = 4, f64 = 8,
}

local function structureInteger(address, width, signed)
  local bytes = readBytes(address, width, true)
  if not bytes or #bytes ~= width then return nil end
  if width == 8 then
    local encoded = {}
    for index = width, 1, -1 do encoded[#encoded + 1] = string.format("%02X", bytes[index]) end
    if signed then
      local value = readQword(address)
      return value ~= nil and tostring(value) or nil
    end
    return "0x" .. table.concat(encoded)
  end
  local value = 0
  for index = width, 1, -1 do value = (value << 8) | bytes[index] end
  if signed then
    local sign = 1 << (width * 8 - 1)
    if (value & sign) ~= 0 then value = value - (1 << (width * 8)) end
  end
  return value
end

handlers["structures.read"] = function(params)
  local base = getAddressSafe(params.base)
  local fields = params.fields
  if not base then return errorDetail("ADDRESS_UNRESOLVED", "Structure base did not resolve", true, true) end
  if type(fields) ~= "table" or #fields < 1 or #fields > 256 then
    return errorDetail("LIMIT_EXCEEDED", "Structure read requires 1 to 256 fields", true, true)
  end
  local values = {}
  for _, field in ipairs(fields) do
    local name, fieldType = field.name, field.type
    local offset = tonumber(field.offset)
    if type(name) ~= "string" or #name < 1 or #name > 128
      or not offset or offset < 0 or offset > 65535 or offset % 1 ~= 0 then
      return errorDetail("INVALID_PARAMS", "Invalid structure field name or offset", true, true)
    end
    local address = base + offset
    local value, encoding
    local width = structureFixedWidths[fieldType]
    if width then
      if fieldType == "f32" then value = readFloat(address)
      elseif fieldType == "f64" then value = readDouble(address)
      else value = structureInteger(address, width, fieldType:sub(1, 1) == "i") end
    elseif fieldType == "pointer" then
      local pointer = readPointer(address)
      value = pointer and formatAddress(pointer) or nil
    elseif fieldType == "bytes" then
      width = tonumber(field.size)
      if not width or width < 1 or width > 4096 then
        return errorDetail("INVALID_PARAMS", "Invalid byte field size", true, true)
      end
      local bytes = readBytes(address, width, true)
      if bytes and #bytes == width then
        local encoded = {}
        for index, byte in ipairs(bytes) do encoded[index] = string.format("%02X", byte) end
        value, encoding = table.concat(encoded), "hex"
      end
    elseif fieldType == "string" or fieldType == "wstring" then
      width = tonumber(field.size)
      if not width or width < 1 or width > 4096 then
        return errorDetail("INVALID_PARAMS", "Invalid string field size", true, true)
      end
      value = readString(address, fieldType == "wstring" and math.floor(width / 2) or width, fieldType == "wstring")
      encoding = fieldType == "wstring" and "utf-16le" or "utf-8"
    else
      return errorDetail("INVALID_PARAMS", "Unsupported structure field type", true, true)
    end
    if value == nil then
      return errorDetail("ACCESS_DENIED", "Structure field could not be read: " .. name, true, true)
    end
    local item = {
      name = name, offset = offset, type = fieldType,
      address = { address = formatAddress(address), pointerWidth = pointerWidth() },
      value = value,
    }
    if encoding then item.encoding = encoding end
    values[#values + 1] = item
  end
  return {
    session = session(), base = { address = formatAddress(base), pointerWidth = pointerWidth() },
    values = values,
  }
end

local function regionState(value)
  if value == 0x1000 then return "commit" end
  if value == 0x2000 then return "reserve" end
  if value == 0x10000 then return "free" end
  return "unknown"
end

local function regionType(value)
  if value == 0x1000000 then return "image" end
  if value == 0x40000 then return "mapped" end
  if value == 0x20000 then return "private" end
  return "unknown"
end

local function regionProtection(value)
  local base = value & 0xFF
  if base == 0x01 then return "---"
  elseif base == 0x02 then return "r--"
  elseif base == 0x04 or base == 0x08 then return "rw-"
  elseif base == 0x10 then return "--x"
  elseif base == 0x20 then return "r-x"
  elseif base == 0x40 or base == 0x80 then return "rwx"
  end
  return string.format("0x%X", value)
end

local function page(items, cursor, requestedLimit)
  local offset = tonumber(cursor or "0") or 0
  if offset < 0 then offset = 0 end
  local limit = math.max(1, math.min(tonumber(requestedLimit) or 100, 200))
  local output = {}
  for i = offset + 1, math.min(offset + limit, #items) do output[#output + 1] = items[i] end
  local nextOffset = offset + #output
  local result = { items = output, truncated = nextOffset < #items }
  if result.truncated then result.nextCursor = tostring(nextOffset) end
  return result
end

handlers["memory.map"] = function(params)
  local missing = requireTarget()
  if missing then return missing end
  local ok, regions = pcall(enumMemoryRegions)
  if not ok or not regions then
    return errorDetail("CE_API_UNAVAILABLE", "enumMemoryRegions failed", true, true)
  end
  local items = {}
  local moduleFilter = params.moduleFilter and tostring(params.moduleFilter):lower() or nil
  for _, region in ipairs(regions) do
    local base = region.BaseAddress or 0
    local stateName = regionState(region.State or 0)
    local typeName = regionType(region.Type or 0)
    local protection = regionProtection(region.Protect or 0)
    local symbol = getNameFromAddress(base, true, false, false) or formatAddress(base)
    local matches = (not moduleFilter or symbol:lower():find(moduleFilter, 1, true))
      and (not params.stateFilter or params.stateFilter == stateName)
      and (not params.typeFilter or params.typeFilter == typeName)
      and (not params.protectionFilter or params.protectionFilter == protection)
    if matches then
      items[#items + 1] = {
        base = { address = formatAddress(base), pointerWidth = pointerWidth() },
        allocationBase = {
          address = formatAddress(region.AllocationBase or 0),
          pointerWidth = pointerWidth(),
        },
        size = region.RegionSize or 0,
        state = stateName,
        type = typeName,
        protection = protection,
        protectionValue = string.format("0x%X", region.Protect or 0),
        name = symbol,
      }
    end
  end
  local result = page(items, params.cursor, params.limit)
  result.session = session()
  return result
end

local function instructionAt(address)
  local ok, text = pcall(disassemble, address)
  if not ok or not text then return nil end
  local addressText, bytesText, opcode, extra = splitDisassembledString(text)
  local size = getInstructionSize(address) or 1
  local raw = readBytes(address, size, true) or {}
  local bytes = {}
  for i, byte in ipairs(raw) do bytes[i] = string.format("%02X", byte) end
  return {
    address = { address = formatAddress(address), pointerWidth = pointerWidth() },
    bytes = table.concat(bytes),
    opcode = opcode or text,
    extra = extra or "",
    size = size,
    nextAddress = {
      address = formatAddress(address + size),
      pointerWidth = pointerWidth(),
    },
    display = text,
  }
end

local function instructionList(startAddress, count)
  local items = {}
  local current = startAddress
  for _ = 1, math.max(1, math.min(count or 32, 500)) do
    local instruction = instructionAt(current)
    if not instruction then break end
    items[#items + 1] = instruction
    current = current + instruction.size
  end
  return items
end

local function resolveAddress(value)
  if type(value) == "number" then return value end
  if type(value) ~= "string" then return nil end
  return getAddressSafe(value)
end

handlers["disassembly.instruction"] = function(params)
  local missing = requireTarget()
  if missing then return missing end
  local address = resolveAddress(params.address)
  if not address then return errorDetail("ADDRESS_UNRESOLVED", "Address could not be resolved", true, true) end
  local instruction = instructionAt(address)
  if not instruction then return errorDetail("ACCESS_DENIED", "Instruction could not be read", true, true) end
  return { session = session(), instruction = instruction }
end

handlers["disassembly.list"] = function(params)
  local missing = requireTarget()
  if missing then return missing end
  local address = resolveAddress(params.address)
  if not address then return errorDetail("ADDRESS_UNRESOLVED", "Address could not be resolved", true, true) end
  local count = tonumber(params.instructionCount) or 32
  local items = instructionList(address, count)
  local byteLimit = tonumber(params.byteLimit) or 65536
  local bounded, consumed = {}, 0
  for _, instruction in ipairs(items) do
    if consumed + instruction.size > byteLimit then break end
    bounded[#bounded + 1] = instruction
    consumed = consumed + instruction.size
  end
  items = bounded
  return { session = session(), items = items, truncated = #items < count }
end

handlers["disassembly.next"] = function(params)
  local missing = requireTarget()
  if missing then return missing end
  local address = resolveAddress(params.address)
  if not address then return errorDetail("ADDRESS_UNRESOLVED", "Address could not be resolved", true, true) end
  local current = instructionAt(address)
  if not current then return errorDetail("ACCESS_DENIED", "Instruction could not be read", true, true) end
  local count = tonumber(params.count) or 1
  return { session = session(), items = instructionList(address + current.size, count), truncated = false }
end

handlers["disassembly.previous"] = function(params)
  local missing = requireTarget()
  if missing then return missing end
  local address = resolveAddress(params.address)
  if not address then return errorDetail("ADDRESS_UNRESOLVED", "Address could not be resolved", true, true) end
  local count = math.max(1, math.min(tonumber(params.count) or 1, 100))
  local reverse = {}
  local current = address
  for _ = 1, count do
    current = getPreviousOpcode(current)
    if not current then break end
    reverse[#reverse + 1] = current
  end
  local items = {}
  for i = #reverse, 1, -1 do
    local instruction = instructionAt(reverse[i])
    if instruction then items[#items + 1] = instruction end
  end
  return { session = session(), items = items, truncated = false }
end

handlers["disassembly.function"] = function(params)
  local missing = requireTarget()
  if missing then return missing end
  local address = resolveAddress(params.address)
  if not address then return errorDetail("ADDRESS_UNRESOLVED", "Address could not be resolved", true, true) end
  local items = instructionList(address, params.detail == "full" and 500 or 100)
  local stoppedAtReturn = false
  local retained = {}
  for _, instruction in ipairs(items) do
    retained[#retained + 1] = instruction
    local opcode = tostring(instruction.opcode):lower()
    if opcode:match("^ret") then stoppedAtReturn = true; break end
  end
  local endAddress = { address = formatAddress(address), pointerWidth = pointerWidth() }
  if #retained > 0 then
    local last = retained[#retained]
    endAddress = last.nextAddress
  end
  local result = {
    session = session(),
    ["function"] = {
      entry = { address = formatAddress(address), pointerWidth = pointerWidth() },
      endAddress = endAddress,
      name = getNameFromAddress(address, true, true, false) or formatAddress(address),
      instructionCount = #retained,
      boundaryConfidence = stoppedAtReturn and "heuristic-return" or "bounded-incomplete",
    },
    truncated = not stoppedAtReturn,
  }
  if params.detail == "full" then result.items = retained end
  return result
end

handlers["symbols.resolve"] = function(params)
  local missing = requireTarget()
  if missing then return missing end
  local address = getAddressSafe(params.expression)
  if not address then return errorDetail("ADDRESS_UNRESOLVED", "Symbol expression could not be resolved", true, true) end
  return {
    session = session(),
    address = {
      expression = params.expression,
      address = formatAddress(address),
      pointerWidth = pointerWidth(),
    },
  }
end

handlers["symbols.describe"] = function(params)
  local missing = requireTarget()
  if missing then return missing end
  local address = resolveAddress(params.address)
  if not address then return errorDetail("ADDRESS_UNRESOLVED", "Address could not be resolved", true, true) end
  return {
    session = session(),
    symbol = {
      name = getNameFromAddress(address, true, true, false) or formatAddress(address),
      address = { address = formatAddress(address), pointerWidth = pointerWidth() },
      inModule = inModule(address) == true,
    },
  }
end

handlers["symbols.modules"] = function(params)
  local missing = requireTarget()
  if missing then return missing end
  local ok, modules = pcall(enumModules, getOpenedProcessID())
  if not ok or not modules then return errorDetail("CE_API_UNAVAILABLE", "Module enumeration failed", true, true) end
  local items = {}
  local filter = params.nameFilter and tostring(params.nameFilter):lower() or nil
  for _, module in ipairs(modules) do
    local name = module.Name or "unknown"
    if not filter or name:lower():find(filter, 1, true) then
      items[#items + 1] = {
        name = name,
        base = { address = formatAddress(module.Address or 0), pointerWidth = pointerWidth() },
        size = module.Size or 0,
        path = module.PathToFile or "",
        architecture = module.Is64Bit and "x86_64" or "x86",
      }
    end
  end
  table.sort(items, function(a, b) return a.base.address < b.base.address end)
  local result = page(items, params.cursor, params.limit)
  result.session = session()
  return result
end

handlers["symbols.list"] = function(params)
  local missing = requireTarget()
  if missing then return missing end
  local ok, symbols = pcall(function() return getMainSymbolList().getSymbolList() end)
  if not ok or not symbols then return errorDetail("CE_API_UNAVAILABLE", "Symbol enumeration failed", true, true) end
  local items = {}
  local filter = params.nameFilter and tostring(params.nameFilter):lower() or nil
  local moduleFilter = params.moduleFilter and tostring(params.moduleFilter):lower() or nil
  for key, value in pairs(symbols) do
    local name, moduleName, address, size
    if type(value) == "table" then
      name = value.searchkey or value.name or tostring(key)
      moduleName = value.modulename or ""
      address = value.address
      size = value.symbolsize or 0
    elseif type(value) == "number" then
      name, moduleName, address, size = tostring(key), "", value, 0
    end
    if address and (not filter or name:lower():find(filter, 1, true))
      and (not moduleFilter or moduleName:lower():find(moduleFilter, 1, true)) then
      items[#items + 1] = {
        name = name,
        module = moduleName,
        address = { address = formatAddress(address), pointerWidth = pointerWidth() },
        size = size,
      }
    end
  end
  table.sort(items, function(a, b)
    if a.address.address == b.address.address then return a.name < b.name end
    return a.address.address < b.address.address
  end)
  local result = page(items, params.cursor, params.limit)
  result.session = session()
  return result
end

local function debugSummary()
  local ok, active = pcall(debug_isDebugging)
  state.debug.active = ok and active == true
  return {
    active = state.debug.active,
    interface = state.debug.active and "windows" or "none",
    stopped = state.debug.stopped,
    stopKind = state.debug.processSuspended and "suspend" or (state.debug.stopped and "debugger" or "none"),
    stopGeneration = state.debug.stopGeneration,
    breakpointCount = (function()
      local count = 0
      for _ in pairs(state.debug.breakpoints) do count = count + 1 end
      return count
    end)(),
  }
end

local function requireDebuggerStopped(expectedStopGeneration)
  if not state.debug.active then
    return errorDetail("DEBUGGER_NOT_ACTIVE", "Windows debugger is not active", true, true)
  end
  if not state.debug.stopped then
    return errorDetail("TARGET_RUNNING", "Debugger target is not stopped", true, true)
  end
  if tonumber(expectedStopGeneration) ~= state.debug.stopGeneration then
    return errorDetail("STALE_STOP", "Debugger stop generation does not match", true, false)
  end
  return nil
end

local function recordDebugStop(kind, threadId)
  local debugState = state.debug
  debugState.stopGeneration = debugState.stopGeneration + 1
  debugState.eventCounter = debugState.eventCounter + 1
  debugState.stopped = true
  local instructionPointer = RIP or EIP or 0
  local event = {
    eventId = string.format("dbg-%08x-%08x", state.generation, debugState.eventCounter),
    kind = kind, generation = state.generation, stopGeneration = debugState.stopGeneration,
    address = { address = formatAddress(instructionPointer), pointerWidth = pointerWidth() },
  }
  if threadId then event.threadId = threadId end
  debugState.events[#debugState.events + 1] = event
  if #debugState.events > 256 then table.remove(debugState.events, 1) end
end

local function installDebuggerCallback()
  local debugState = state.debug
  if debugState.callbackInstalled then return end
  debugState.previousOnBreakpoint = rawget(_G, "debugger_onBreakpoint")
  _G.debugger_onBreakpoint = function()
    if type(debugState.previousOnBreakpoint) == "function" then
      return debugState.previousOnBreakpoint()
    end
    recordDebugStop("external_break", nil)
    return 0
  end
  debugState.callbackInstalled = true
end

handlers["debug.control.status"] = function(_)
  return { session = session(), debugger = debugSummary() }
end

handlers["debug.control.start"] = function(params)
  if params.interface ~= nil and params.interface ~= "windows" then
    return errorDetail("CAPABILITY_UNAVAILABLE", "Only the Windows debugger is verified", true, false)
  end
  local summary = debugSummary()
  if summary.active then return { session = session(), debugger = summary } end
  installDebuggerCallback()
  local called, result = pcall(debugProcess, 1)
  if not called or result == false then
    cleanupDebugger()
    return errorDetail("CE_API_UNAVAILABLE", "debugProcess failed: " .. tostring(result), true, false)
  end
  state.debug.active = true
  state.debug.stopped = false
  return { session = session(), debugger = debugSummary() }
end

handlers["debug.control.pause"] = function(params)
  local summary = debugSummary()
  if not summary.active then return errorDetail("DEBUGGER_NOT_ACTIVE", "Windows debugger is not active", true, true) end
  if summary.stopped then return { session = session(), debugger = summary, pauseRequested = false } end
  local debugState = state.debug
  local called, result = pcall(pause)
  if not called or result == false then
    return errorDetail("CE_API_UNAVAILABLE", "process pause failed: " .. tostring(result), true, false)
  end
  debugState.processSuspended = true
  recordDebugStop("pause", nil)
  return { session = session(), debugger = debugSummary(), pauseRequested = true }
end

local function clearStepBreakpoints()
  for _, address in ipairs(state.debug.stepAddresses) do pcall(debug_removeBreakpoint, address) end
  state.debug.stepAddresses = {}
end

local function prepareHardwareStep(mode)
  local instructionPointer = RIP or EIP
  if not instructionPointer then return errorDetail("CONTEXT_UNAVAILABLE", "Stopped instruction pointer is unavailable", true, true) end
  local disassembler = createDisassembler()
  local ok, _ = pcall(function() disassembler.disassemble(instructionPointer) end)
  local data = ok and disassembler.getLastDisassembleData() or nil
  pcall(function() disassembler.destroy() end)
  if type(data) ~= "table" or type(data.bytes) ~= "table" or #data.bytes < 1 then
    return errorDetail("CE_API_UNAVAILABLE", "Current instruction could not be decoded for stepping", true, true)
  end
  local nextAddress = instructionPointer + #data.bytes
  local targets, seen = {}, {}
  local function addTarget(address)
    if type(address) == "number" and address > 0 and not seen[address] then
      seen[address], targets[#targets + 1] = true, address
    end
  end
  if data.isRet then
    local stackPointer = RSP or ESP
    local read = pointerWidth() == 64 and readQword or readInteger
    local readOk, target = pcall(read, stackPointer)
    if readOk then addTarget(target) end
  elseif data.isConditionalJump then
    addTarget(data.parameterValue)
    addTarget(nextAddress)
  elseif data.isCall then
    addTarget(mode == "step_into" and data.parameterValue or nextAddress)
  elseif data.isJump then
    addTarget(data.parameterValue)
  else
    addTarget(nextAddress)
  end
  if #targets == 0 then return errorDetail("ADDRESS_UNRESOLVED", "Step destination could not be resolved", true, true) end
  local occupied = 0
  for _ in pairs(state.debug.breakpoints) do occupied = occupied + 1 end
  if occupied + #targets > 4 then return errorDetail("BREAKPOINT_LIMIT", "Insufficient hardware slots for bounded step", true, true) end
  local function onStep()
    clearStepBreakpoints()
    recordDebugStop("step", nil)
    return 0
  end
  for _, target in ipairs(targets) do
    local installed, installResult = pcall(debug_setBreakpoint, target, 1, bptExecute, bpmDebugRegister, onStep)
    if not installed or installResult == false then
      clearStepBreakpoints()
      return errorDetail("CE_API_UNAVAILABLE", "Temporary step breakpoint install failed", true, false)
    end
    state.debug.stepAddresses[#state.debug.stepAddresses + 1] = target
  end
  return nil
end

handlers["debug.control.continue"] = function(params)
  local failure = requireDebuggerStopped(params.expectedStopGeneration)
  if failure then return failure end
  local mode = params.mode or "run"
  if mode ~= "run" and mode ~= "step_into" and mode ~= "step_over" then return errorDetail("INVALID_PARAMS", "Unknown continue mode", true, true) end
  if state.debug.processSuspended then
    if params.mode ~= "run" then return errorDetail("INVALID_STATE", "A suspended process can only resume with mode=run", true, true) end
    local resumed, resumeResult = pcall(unpause)
    if not resumed or resumeResult == false then return errorDetail("CE_API_UNAVAILABLE", "process resume failed: " .. tostring(resumeResult), true, false) end
    state.debug.processSuspended = false
    state.debug.stopped = false
    return { session = session(), debugger = debugSummary() }
  end
  if mode ~= "run" then
    local stepFailure = prepareHardwareStep(mode)
    if stepFailure then return stepFailure end
  end
  local called, result = pcall(debug_continueFromBreakpoint, co_run)
  if not called or result == false then
    clearStepBreakpoints()
    return errorDetail("CE_API_UNAVAILABLE", "debug continue failed: " .. tostring(result), true, false)
  end
  state.debug.stopped = false
  return { session = session(), debugger = debugSummary() }
end

handlers["debug.control.detach"] = function(_)
  cleanupDebugger()
  return { session = session(), debugger = debugSummary(), detached = true }
end

local debugTriggers = { execute = bptExecute, write = bptWrite, access = bptAccess }

local function debugBreakpointItems()
  local items = {}
  for _, breakpoint in pairs(state.debug.breakpoints) do
    items[#items + 1] = {
      breakpointId = breakpoint.id,
      address = { address = formatAddress(breakpoint.address), pointerWidth = pointerWidth() },
      trigger = breakpoint.trigger, size = breakpoint.size,
      generation = breakpoint.generation,
    }
  end
  table.sort(items, function(a, b) return a.breakpointId < b.breakpointId end)
  return items
end

handlers["debug.breakpoints.list"] = function(_)
  return { session = session(), items = debugBreakpointItems(), truncated = false }
end

handlers["debug.breakpoints.set"] = function(params)
  if not state.debug.active then
    return errorDetail("DEBUGGER_NOT_ACTIVE", "Start the Windows debugger first", true, true)
  end
  local count = #debugBreakpointItems()
  if count >= 4 then return errorDetail("BREAKPOINT_LIMIT", "All hardware breakpoint slots are in use", true, true) end
  local address = getAddressSafe(params.address)
  local trigger = params.trigger or "execute"
  local triggerValue = debugTriggers[trigger]
  local size = tonumber(params.size) or 1
  if not address or not triggerValue then
    return errorDetail("INVALID_PARAMS", "Invalid breakpoint address or trigger", true, true)
  end
  if trigger == "execute" then size = 1 end
  if trigger ~= "execute" and size ~= 1 and size ~= 2 and size ~= 4 and size ~= 8 then
    return errorDetail("INVALID_PARAMS", "Data breakpoint size must be 1, 2, 4, or 8", true, true)
  end
  state.debug.breakpointCounter = state.debug.breakpointCounter + 1
  local id = string.format("bp-%08x-%08x", state.generation, state.debug.breakpointCounter)
  local breakpoint = {
    id = id, address = address, trigger = trigger, size = size, generation = state.generation,
  }
  local installed, installResult = pcall(
    debug_setBreakpoint, address, size, triggerValue, bpmDebugRegister,
    function()
      state.debug.stopGeneration = state.debug.stopGeneration + 1
      state.debug.eventCounter = state.debug.eventCounter + 1
      state.debug.stopped = true
      local instructionPointer = RIP or EIP or address
      local event = {
        eventId = string.format("dbg-%08x-%08x", state.generation, state.debug.eventCounter),
        kind = "breakpoint", breakpointId = id, generation = state.generation,
        stopGeneration = state.debug.stopGeneration,
        address = { address = formatAddress(instructionPointer), pointerWidth = pointerWidth() },
      }
      state.debug.events[#state.debug.events + 1] = event
      if #state.debug.events > 256 then table.remove(state.debug.events, 1) end
      return 0
    end
  )
  if not installed or installResult == false then
    return errorDetail("CE_API_UNAVAILABLE", "Hardware breakpoint install failed", true, false)
  end
  state.debug.breakpoints[id] = breakpoint
  return { session = session(), breakpoint = debugBreakpointItems()[count + 1] }
end

handlers["debug.breakpoints.remove"] = function(params)
  local breakpoint = state.debug.breakpoints[params.breakpointId]
  if not breakpoint then return errorDetail("BREAKPOINT_NOT_FOUND", "Breakpoint handle does not exist", true, true) end
  pcall(debug_removeBreakpoint, breakpoint.address)
  state.debug.breakpoints[breakpoint.id] = nil
  return { session = session(), removed = true }
end

handlers["debug.events.list"] = function(params)
  local result = page(state.debug.events, params.cursor, params.limit)
  result.session = session()
  return result
end

handlers["threads.list"] = function(params)
  local list = createStringlist()
  local called, message = pcall(getThreadlist, list)
  if not called then
    list.destroy()
    return errorDetail("CE_API_UNAVAILABLE", "getThreadlist failed: " .. tostring(message), true, true)
  end
  local items = {}
  for index = 0, list.Count - 1 do
    local threadId = tonumber(list[index], 16)
    if threadId and threadId > 0 then
      items[#items + 1] = {
        threadId = threadId, threadIdHex = string.format("0x%08X", threadId),
      }
    end
  end
  list.destroy()
  table.sort(items, function(a, b) return a.threadId < b.threadId end)
  local result = page(items, params.cursor, params.limit)
  result.session = session()
  return result
end

local function registerHex(value, width)
  if value == nil then return nil end
  if width == 64 then return string.format("0x%016X", value) end
  return string.format("0x%08X", value)
end

handlers["debug.registers.read"] = function(params)
  local failure = requireDebuggerStopped(params.expectedStopGeneration)
  if failure then return failure end
  local contextOk, contextResult = pcall(debug_getContext, params.includeVectors == true)
  -- CE 7.5 documents no return value for debug_getContext and can return false
  -- even after populating the register globals. An exception or missing IP is
  -- the failure signal; the undocumented boolean is not.
  if not contextOk then
    return errorDetail("CE_API_UNAVAILABLE", "debug_getContext failed: " .. tostring(contextResult), true, false)
  end
  local width = pointerWidth()
  local general = {}
  local names
  if width == 64 then
    names = { "RAX", "RBX", "RCX", "RDX", "RSI", "RDI", "RBP", "RSP", "RIP",
      "R8", "R9", "R10", "R11", "R12", "R13", "R14", "R15" }
  else
    names = { "EAX", "EBX", "ECX", "EDX", "ESI", "EDI", "EBP", "ESP", "EIP" }
  end
  for _, name in ipairs(names) do
    local value = rawget(_G, name)
    if value ~= nil then general[name:lower()] = registerHex(value, width) end
  end
  local instructionName = width == 64 and "rip" or "eip"
  if general[instructionName] == nil then
    return errorDetail("CONTEXT_UNAVAILABLE", "Stopped thread context omitted the instruction pointer", true, true)
  end
  if EFLAGS ~= nil then general.eflags = registerHex(EFLAGS, 32) end
  local result = {
    session = session(), stopGeneration = state.debug.stopGeneration,
    architecture = width == 64 and "x86_64" or "x86", general = general,
  }
  if params.includeVectors == true then
    local vectors = {}
    local maximum = width == 64 and 15 or 7
    for index = 0, maximum do
      local pointerOk, pointer = pcall(debug_getXMMPointer, index)
      local bytes = pointerOk and pointer and readBytesLocal(pointer, 16, true) or nil
      if bytes and #bytes == 16 then
        local encoded = {}
        for byteIndex, byte in ipairs(bytes) do encoded[byteIndex] = string.format("%02X", byte) end
        vectors["xmm" .. tostring(index)] = table.concat(encoded)
      end
    end
    result.vectors = vectors
  end
  return result
end

local scanValueTypes = {
  u8 = vtByte, i16 = vtWord, i32 = vtDword, i64 = vtQword,
  f32 = vtSingle, f64 = vtDouble, string = vtString, aob = vtByteArray,
}

local initialScanTypes = {
  exact = soExactValue, between = soValueBetween, unknown = soUnknownValue,
}

local refineScanTypes = {
  exact = soExactValue, between = soValueBetween, bigger = soBiggerThan,
  smaller = soSmallerThan, increased = soIncreasedValue,
  decreased = soDecreasedValue, changed = soChanged, unchanged = soUnchanged,
}

local relativeRefineTypes = {
  increased = true, decreased = true, changed = true, unchanged = true,
}

local function resolvePointerChain(baseExpression, offsets)
  local current = getAddressSafe(baseExpression)
  if not current then
    return nil, errorDetail("ADDRESS_UNRESOLVED", "Pointer-chain base did not resolve", true, true)
  end
  local base = current
  local chain = {
    { step = 0, address = { address = formatAddress(current), pointerWidth = pointerWidth() } }
  }
  for index, offset in ipairs(offsets or {}) do
    local readOk, pointer = pcall(readPointer, current)
    if not readOk or pointer == nil then
      return nil, errorDetail(
        "MEMORY_READ_FAILED", "Pointer dereference failed at step " .. tostring(index),
        true, true
      )
    end
    current = pointer + offset
    chain[#chain + 1] = {
      step = index, offset = offset,
      pointer = { address = formatAddress(pointer), pointerWidth = pointerWidth() },
      address = { address = formatAddress(current), pointerWidth = pointerWidth() },
    }
  end
  local finalOk, finalPointer = pcall(readPointer, current)
  local result = {
    base = { address = formatAddress(base), pointerWidth = pointerWidth() },
    offsets = offsets or {},
    finalAddress = { address = formatAddress(current), pointerWidth = pointerWidth() },
    chain = chain,
  }
  if finalOk and finalPointer ~= nil then
    result.finalPointer = { address = formatAddress(finalPointer), pointerWidth = pointerWidth() }
  end
  return result, nil
end

handlers["pointer.resolve"] = function(params)
  local result, failure = resolvePointerChain(params.base, params.offsets)
  if failure then return failure end
  result.session = session()
  return result
end

handlers["pointer.validate"] = function(params)
  local target = getAddressSafe(params.target)
  if not target then
    return errorDetail("ADDRESS_UNRESOLVED", "Pointer validation target did not resolve", true, true)
  end
  local matches, misses = {}, {}
  local unreadable = 0
  for _, candidate in ipairs(params.chains or {}) do
    local resolved, failure = resolvePointerChain(candidate.base, candidate.offsets)
    if failure then
      unreadable = unreadable + 1
      if params.includeMisses then
        misses[#misses + 1] = { base = candidate.base, offsets = candidate.offsets, error = failure.code }
      end
    elseif getAddressSafe(resolved.finalAddress.address) == target then
      matches[#matches + 1] = resolved
    elseif params.includeMisses then
      misses[#misses + 1] = resolved
    end
  end
  local result = {
    session = session(),
    target = { address = formatAddress(target), pointerWidth = pointerWidth() },
    total = #(params.chains or {}), matched = #matches,
    unreadable = unreadable, matches = matches,
  }
  if params.includeMisses then result.misses = misses end
  return result
end

local function materializeCompletedScan(operation)
  if operation.kind ~= "scan" then return end
  if operation.state ~= "running" or not operation.scanDone then return end
  operation.scanDone = false
  local ok, value = pcall(function()
    local foundList = createFoundList(operation.memScan)
    foundList.initialize()
    return foundList
  end)
  if ok and value then
    operation.foundList = value
    operation.resultCount = tonumber(value.getCount()) or 0
    operation.state = "completed"
  else
    operation.state = "failed"
    operation.message = "Found-list initialization failed: " .. tostring(value)
  end
end

local function operationSummary(operation)
  local observedProgress = nil
  if operation.kind == "scan" and operation.memScan and operation.state == "running" then
    local ok, progress = pcall(function() return operation.memScan.getProgress() end)
    if ok and type(progress) == "table" then
      observedProgress = {
        total = tonumber(progress.TotalAddressesToScan) or 0,
        completed = tonumber(progress.CurrentlyScanned) or 0,
        resultsFound = tonumber(progress.ResultsFound) or 0,
      }
      if observedProgress.total > 0
        and observedProgress.completed >= observedProgress.total then
        operation.scanDone = true
      end
    end
  end
  materializeCompletedScan(operation)
  local summary = {
    operationId = operation.id,
    kind = operation.kind or "scan",
    state = operation.state,
    generation = operation.generation,
    cancellable = operation.state == "queued" or operation.state == "running",
  }
  if operation.resultCount ~= nil then summary.resultCount = operation.resultCount end
  if operation.message then summary.message = operation.message end
  if observedProgress then summary.progress = observedProgress end
  if operation.kind == "signature" then
    summary.progress = {
      completed = tonumber(operation.attemptedBytes) or 0,
      total = tonumber(operation.maximumBytes) or 0,
    }
  end
  return summary
end

local function getOperation(operationId)
  local operation = state.operations[tostring(operationId or "")]
  if not operation then
    return nil, errorDetail("OPERATION_NOT_FOUND", "Operation handle does not exist", true, true)
  end
  if operation.generation ~= state.generation then
    return nil, errorDetail("STALE_SESSION", "Operation belongs to an old target generation", true, true)
  end
  return operation, nil
end

local function activeScanCount()
  local active, total = 0, 0
  for _, operation in pairs(state.operations) do
    total = total + 1
    if operation.state == "queued" or operation.state == "running" then active = active + 1 end
  end
  return active, total
end

handlers["scan.start"] = function(params)
  local missing = requireTarget()
  if missing then return missing end
  local active, total = activeScanCount()
  if active >= 1 then
    return errorDetail("OPERATION_LIMIT", "Only one scan may run at a time", true, true)
  end
  if total >= 8 then
    return errorDetail("OPERATION_LIMIT", "Close an existing scan before creating another", true, true)
  end
  local variableType = scanValueTypes[params.valueType]
  local scanOption = initialScanTypes[params.scanType]
  if not variableType or not scanOption then
    return errorDetail("INVALID_PARAMS", "Unsupported initial scan type", true, true)
  end
  local startAddress = getAddressSafe(params.rangeStart or "0")
  local stopAddress = getAddressSafe(params.rangeEnd or "7FFFFFFFFFFFFFFF")
  if not startAddress or not stopAddress or startAddress > stopAddress then
    return errorDetail("INVALID_PARAMS", "Invalid scan address range", true, true)
  end
  state.operationCounter = state.operationCounter + 1
  local id = string.format("scan-%08x-%08x", state.generation, state.operationCounter)
  local operation = {
    id = id, generation = state.generation, state = "queued",
    memScan = nil, valueType = params.valueType, scanDone = false, kind = "scan",
  }
  state.operations[id] = operation
  local alignment = tonumber(params.alignment) or 1
  operation.scanThread = createThread(function(thread)
    operation.state = "running"
    local firstFoundList
    local started, message = pcall(function()
      operation.memScan = createMemScan()
      operation.memScan.firstScan(
        scanOption, variableType, rtRounded, tostring(params.value or ""),
        tostring(params.value2 or ""), startAddress, stopAddress,
        params.protection or "*W*X*C", alignment > 1 and fsmAligned or fsmNotAligned,
        tostring(alignment), params.hexadecimal == true or params.valueType == "aob", false,
        params.unicode == true, params.caseSensitive == true
      )
      operation.memScan.waitTillDone()
      firstFoundList = createFoundList(operation.memScan)
      firstFoundList.initialize()
    end)
    thread.synchronize(function()
      if operation.state == "running" then
        if started then
          operation.foundList = firstFoundList
          operation.resultCount = tonumber(firstFoundList.getCount()) or 0
          operation.state = "completed"
        else
          operation.state = "failed"
          operation.message = "firstScan failed: " .. tostring(message)
        end
      end
    end)
    -- Keep the Lua thread context that owns MemScan/FoundList alive until the
    -- operation is explicitly closed or cancelled. CE userdata created by a
    -- finished Lua thread is not safe to consume from later bridge requests.
    while not thread.Terminated do
      local command = operation.command
      if command then
        operation.command = nil
        operation.state = "running"
        local oldFoundList = operation.foundList
        local refinedFoundList
        local refined, refineMessage = pcall(function()
          operation.memScan.nextScan(
            command.scanOption, rtRounded, command.value, command.value2,
            command.hexadecimal, false, command.unicode,
            command.caseSensitive, false
          )
          operation.memScan.waitTillDone()
          -- nextScan consumes the attached result source. Keep it alive until
          -- waitTillDone has completely written the replacement scan files.
          if oldFoundList then oldFoundList.destroy() end
          refinedFoundList = createFoundList(operation.memScan)
          refinedFoundList.initialize()
        end)
        thread.synchronize(function()
          if operation.state ~= "running" then
            if refinedFoundList then pcall(function() refinedFoundList.destroy() end) end
            return
          end
          if refined then
            operation.foundList = refinedFoundList
            operation.resultCount = tonumber(refinedFoundList.getCount()) or 0
            operation.state = "completed"
          else
            operation.state = "failed"
            operation.message = "nextScan failed: " .. tostring(refineMessage)
          end
        end)
      else
        sleep(20)
      end
    end
  end)
  return { session = session(), operation = operationSummary(operation) }
end

handlers["scan.refine"] = function(params)
  local operation, failure = getOperation(params.operationId)
  if failure then return failure end
  if operation.state ~= "completed" then
    return errorDetail("OPERATION_NOT_READY", "Scan must be completed before refinement", true, true)
  end
  if operation.valueType == "aob" then
    return errorDetail("INVALID_PARAMS", "AOB scans are immutable", true, true)
  end
  if params.scanType ~= "exact" and not relativeRefineTypes[params.scanType] then
    return errorDetail(
      "CAPABILITY_UNAVAILABLE",
      "This refinement mode has not passed the CE lifecycle and MCP integration gates",
      true,
      false
    )
  end
  local scanOption = refineScanTypes[params.scanType]
  if not scanOption then return errorDetail("INVALID_PARAMS", "Unsupported refinement type", true, true) end
  local needsValue = params.scanType == "exact" or params.scanType == "between"
    or params.scanType == "bigger" or params.scanType == "smaller"
  if needsValue and not params.value then
    return errorDetail("INVALID_PARAMS", "This refinement requires value", true, true)
  end
  if params.scanType == "between" and not params.value2 then
    return errorDetail("INVALID_PARAMS", "Between refinement requires value2", true, true)
  end
  operation.state = "queued"
  operation.message = nil
  operation.command = {
    scanOption = scanOption,
    value = tostring(params.value or ""),
    value2 = tostring(params.value2 or ""),
    hexadecimal = params.hexadecimal == true,
    unicode = params.unicode == true,
    caseSensitive = params.caseSensitive == true,
  }
  return { session = session(), operation = operationSummary(operation) }
end

handlers["scan.results"] = function(params)
  local operation, failure = getOperation(params.operationId)
  if failure then return failure end
  if operation.state ~= "completed" or not operation.foundList then
    return errorDetail("OPERATION_NOT_READY", "Scan results are not ready", true, true)
  end
  local offset = tonumber(params.cursor or "0") or -1
  local limit = math.max(1, math.min(tonumber(params.limit) or 100, 200))
  if offset < 0 or offset > operation.resultCount then
    return errorDetail("INVALID_CURSOR", "Result cursor is outside the scan", true, true)
  end
  local items = {}
  local last = math.min(offset + limit, operation.resultCount)
  for index = offset, last - 1 do
    local rawAddress = operation.foundList.Address[index]
    local address = rawAddress and getAddressSafe(rawAddress) or nil
    if address then
      items[#items + 1] = {
        address = { address = formatAddress(address), pointerWidth = pointerWidth() },
        value = tostring(operation.foundList.Value[index] or ""),
      }
    end
  end
  local result = {
    session = session(), operation = operationSummary(operation), items = items,
    total = operation.resultCount, truncated = last < operation.resultCount,
  }
  if result.truncated then result.nextCursor = tostring(last) end
  return result
end

handlers["scan.close"] = function(params)
  local operation, failure = getOperation(params.operationId)
  if failure then return failure end
  destroyOperation(operation)
  operation.state = "closed"
  state.operations[operation.id] = nil
  return { session = session(), closed = true }
end

handlers["operations.get"] = function(params)
  local operation, failure = getOperation(params.operationId)
  if failure then return failure end
  return { session = session(), operation = operationSummary(operation) }
end

handlers["operations.list"] = function(params)
  local items = {}
  for _, operation in pairs(state.operations) do
    if operation.generation == state.generation then
      items[#items + 1] = operationSummary(operation)
    end
  end
  table.sort(items, function(a, b) return a.operationId < b.operationId end)
  local result = page(items, params.cursor, params.limit)
  result.session = session()
  return result
end

handlers["operations.cancel"] = function(params)
  local operation, failure = getOperation(params.operationId)
  if failure then return failure end
  if operation.state == "queued" or operation.state == "running" then
    destroyOperation(operation)
    operation.state = "cancelled"
  end
  return { session = session(), operation = operationSummary(operation) }
end

local function exactPattern(bytes, length)
  local encoded = {}
  for index = 1, length do encoded[index] = string.format("%02X", bytes[index]) end
  return table.concat(encoded, " ")
end

handlers["signature.start"] = function(params)
  local active, total = activeScanCount()
  if active >= 1 then
    return errorDetail("OPERATION_LIMIT", "Only one scan-backed operation may run at a time", true, true)
  end
  if total >= 8 then
    return errorDetail("OPERATION_LIMIT", "Close an existing operation before creating another", true, true)
  end
  local address = getAddressSafe(params.address)
  local rangeStart = getAddressSafe(params.rangeStart)
  local rangeEnd = getAddressSafe(params.rangeEnd)
  local minimumBytes = tonumber(params.minBytes) or 8
  local maximumBytes = tonumber(params.maxBytes) or 64
  if not address or not rangeStart or not rangeEnd or rangeStart > rangeEnd
    or address < rangeStart or address > rangeEnd then
    return errorDetail("INVALID_PARAMS", "Address must be inside a valid explicit range", true, true)
  end
  if rangeEnd - rangeStart + 1 > 67108864 then
    return errorDetail("LIMIT_EXCEEDED", "Signature range may not exceed 64 MiB", true, true)
  end
  if minimumBytes < 4 or maximumBytes > 64 or minimumBytes > maximumBytes
    or address + maximumBytes - 1 > rangeEnd then
    return errorDetail("INVALID_PARAMS", "Candidate byte limits or target range tail are invalid", true, true)
  end
  local bytes = readBytes(address, maximumBytes, true)
  if not bytes or #bytes ~= maximumBytes then
    return errorDetail("ACCESS_DENIED", "Candidate bytes could not be read", true, true)
  end

  state.operationCounter = state.operationCounter + 1
  local id = string.format("sig-%08x-%08x", state.generation, state.operationCounter)
  local operation = {
    id = id, kind = "signature", generation = state.generation, state = "queued",
    attemptedBytes = 0, maximumBytes = maximumBytes, memScan = nil,
  }
  state.operations[id] = operation
  local function startSignatureWorker()
    if operation.state ~= "queued" then return end
    operation.scanThread = createThread(function(thread)
      operation.state = "running"
    local uniquePattern, uniqueLength
    local failedMessage
    for length = minimumBytes, maximumBytes do
      if thread.Terminated or operation.state ~= "running" then return end
      local scan = createMemScan()
      operation.memScan = scan
      local found
      local ok, message = pcall(function()
        scan.firstScan(
          soExactValue, vtByteArray, rtRounded, exactPattern(bytes, length), "",
          rangeStart, rangeEnd, params.protection or "*W*X*C",
          fsmNotAligned, "1", true, false, false, false
        )
        scan.waitTillDone()
        found = createFoundList(scan)
        found.initialize()
      end)
      if not ok then
        failedMessage = "signature scan failed: " .. tostring(message)
      else
        local count = tonumber(found.getCount()) or 0
        local first = count == 1 and getAddressSafe(found.Address[0]) or nil
        if count == 1 and first == address then
          uniquePattern, uniqueLength = exactPattern(bytes, length), length
        end
      end
      if found then pcall(function() found.destroy() end) end
      pcall(function() scan.destroy() end)
      operation.memScan = nil
      operation.attemptedBytes = length
      if failedMessage or uniquePattern then break end
    end
      thread.synchronize(function()
        if operation.state ~= "running" then return end
        if failedMessage then
          operation.state = "failed"
          operation.message = failedMessage
        else
          operation.state = "completed"
          operation.unique = uniquePattern ~= nil
          operation.pattern = uniquePattern
          operation.byteCount = uniqueLength
        end
      end)
    end)
  end
  operation.startTimer = createTimer(nil)
  -- Match process.attach's proved response-flush boundary. A short timer can
  -- fire before the pipe worker writes the accepted handle and re-enter a
  -- fast MemScan completion on the main thread.
  operation.startTimer.Interval = 1000
  operation.startTimer.OnTimer = function(sender)
    sender.destroy()
    operation.startTimer = nil
    startSignatureWorker()
  end
  return { session = session(), operation = operationSummary(operation) }
end

handlers["signature.result"] = function(params)
  local operation, failure = getOperation(params.operationId)
  if failure then return failure end
  if operation.kind ~= "signature" then
    return errorDetail("INVALID_PARAMS", "Operation is not a signature search", true, true)
  end
  local result = { session = session(), operation = operationSummary(operation) }
  if operation.state == "completed" then
    result.unique = operation.unique == true
    if operation.pattern then
      result.pattern = operation.pattern
      result.byteCount = operation.byteCount
      result.offset = 0
    end
  end
  return result
end

handlers["signature.close"] = function(params)
  local operation, failure = getOperation(params.operationId)
  if failure then return failure end
  if operation.kind ~= "signature" then
    return errorDetail("INVALID_PARAMS", "Operation is not a signature search", true, true)
  end
  destroyOperation(operation)
  state.operations[operation.id] = nil
  return { session = session(), closed = true }
end

local function requireDBVMReady()
  local enableHint = "DBK/DBVM may not be enabled or loaded by the user. Enable them manually in Cheat Engine, then retry; MCP never initializes DBK or DBVM."
  local dbkQuery, dbvmQuery = rawget(_G, "dbk_initialized"), rawget(_G, "dbvm_initialized")
  if type(dbkQuery) == "function" then
    local dbkOk, dbkLoaded = pcall(dbkQuery)
    if not dbkOk or dbkLoaded ~= true then return errorDetail("CAPABILITY_UNAVAILABLE", "DBK is not already loaded; the user may not have enabled DBK/DBVM in Cheat Engine", true, true, enableHint) end
  end
  if type(dbvmQuery) == "function" then
    local dbvmOk, dbvmLoaded = pcall(dbvmQuery)
    if not dbvmOk or dbvmLoaded ~= true then return errorDetail("CAPABILITY_UNAVAILABLE", "DBVM is not already loaded; the user may not have enabled DBVM in Cheat Engine", true, true, enableHint) end
  end
  return nil
end

local function physicalTranslationError()
  return errorDetail(
    "CAPABILITY_UNAVAILABLE",
    "Virtual address could not be translated to resident physical memory; DBK/DBVM may not be enabled, or the page may be unavailable",
    true,
    true,
    "Confirm DBK/DBVM are enabled in Cheat Engine, touch the target page, call ce.status, and retry; MCP will not initialize them."
  )
end

local function dbvmApiError(message, safeToRetry)
  return errorDetail(
    "CE_API_ERROR",
    message .. "; DBVM may have been disabled, unloaded, or unavailable after the readiness check",
    true,
    safeToRetry,
    "Confirm DBK/DBVM are still enabled in Cheat Engine and retry from ce.status; MCP will not initialize them."
  )
end

local function dbvmContext(entry, index)
  local item, registers = { index = index }, {}
  local names = { "RAX", "RBX", "RCX", "RDX", "RSI", "RDI", "RBP", "RSP", "RIP",
    "R8", "R9", "R10", "R11", "R12", "R13", "R14", "R15" }
  for _, name in ipairs(names) do
    if entry[name] ~= nil then registers[name:lower()] = formatAddress(entry[name]) end
  end
  if next(registers) ~= nil then item.registers = registers end
  if entry.RIP ~= nil then item.instructionAddress = formatAddress(entry.RIP) end
  return item
end

local function watchSummary(watch)
  return { watchId = watch.id, state = watch.state, mode = watch.mode, size = watch.size,
    capacity = watch.capacity, virtualAddress = formatAddress(watch.virtualAddress),
    physicalAddress = formatAddress(watch.physicalAddress), generation = watch.generation }
end

local function getWatch(id)
  local watch = state.hypervisor.watches[tostring(id or "")]
  if not watch then return nil, errorDetail("NOT_FOUND", "DBVM watch was not found", true, true) end
  if watch.generation ~= state.generation then return nil, errorDetail("STALE_SESSION", "DBVM watch belongs to another target generation", true, true) end
  return watch, nil
end

handlers["dbvm.watch.start"] = function(params)
  local readiness = requireDBVMReady()
  if readiness then return readiness end
  if type(dbk_getPhysicalAddress) ~= "function" or type(dbvm_watch_retrievelog) ~= "function"
    or type(dbvm_watch_disable) ~= "function" then
    return errorDetail("CAPABILITY_UNAVAILABLE", "Required DBVM watch API is unavailable", true, true)
  end
  local watchCount = 0
  for _ in pairs(state.hypervisor.watches) do watchCount = watchCount + 1 end
  if watchCount >= 8 then return errorDetail("OUT_OF_RESOURCES", "At most 8 DBVM watches may be active", true, true) end
  local address = resolveAddress(params.address)
  if not address then return errorDetail("ADDRESS_UNRESOLVED", "Address could not be resolved", true, true) end
  local size, capacity = math.max(1, math.min(tonumber(params.size) or 1, 8)), math.max(1, math.min(tonumber(params.capacity) or 256, 1024))
  local physicalOk, physical = pcall(dbk_getPhysicalAddress, address)
  if not physicalOk or not physical or physical == 0 then return physicalTranslationError() end
  if math.floor(physical / 4096) ~= math.floor((physical + size - 1) / 4096) then return errorDetail("INVALID_PARAMS", "DBVM watch range must not cross a physical page", true, true) end
  local options = params.captureStack == true and 9 or 1
  local api = params.mode == "execute" and dbvm_watch_executes or params.mode == "access" and dbvm_watch_reads or dbvm_watch_writes
  if type(api) ~= "function" then return errorDetail("CAPABILITY_UNAVAILABLE", "Requested DBVM watch mode API is unavailable", true, true) end
  local ok, nativeId = pcall(api, physical, size, options, capacity)
  if not ok or nativeId == nil then return dbvmApiError("DBVM watch start failed: " .. tostring(nativeId), false) end
  local hypervisor = state.hypervisor
  hypervisor.watchCounter = hypervisor.watchCounter + 1
  local id = string.format("watch-%08x-%08x", state.generation, hypervisor.watchCounter)
  local watch = { id = id, nativeId = nativeId, state = "active", mode = params.mode, size = size,
    capacity = capacity, virtualAddress = address, physicalAddress = physical, generation = state.generation }
  hypervisor.watches[id] = watch
  return { session = session(), watch = watchSummary(watch) }
end

handlers["dbvm.watch.status"] = function(params)
  if params.watchId then
    local watch, failure = getWatch(params.watchId)
    if failure then return failure end
    return { session = session(), watch = watchSummary(watch) }
  end
  local items = {}
  for _, watch in pairs(state.hypervisor.watches) do items[#items + 1] = watchSummary(watch) end
  table.sort(items, function(a, b) return a.watchId < b.watchId end)
  return { session = session(), items = items, truncated = false }
end

handlers["dbvm.watch.events"] = function(params)
  local watch, failure = getWatch(params.watchId)
  if failure then return failure end
  local ok, entries = pcall(dbvm_watch_retrievelog, watch.nativeId)
  if not ok then return dbvmApiError("DBVM watch log retrieval failed", true) end
  entries = type(entries) == "table" and entries or {}
  local offset, limit = tonumber(params.cursor or "0") or 0, math.max(1, math.min(tonumber(params.limit) or 100, 200))
  local items = {}
  for index = offset + 1, math.min(offset + limit, #entries) do items[#items + 1] = dbvmContext(entries[index], index) end
  local nextOffset = offset + #items
  local result = { session = session(), watch = watchSummary(watch), items = items, truncated = nextOffset < #entries }
  if result.truncated then result.nextCursor = tostring(nextOffset) end
  return result
end

handlers["dbvm.watch.stop"] = function(params)
  local watch, failure = getWatch(params.watchId)
  if failure then return failure end
  local ok, disabled = pcall(dbvm_watch_disable, watch.nativeId)
  if not ok or disabled == false then return dbvmApiError("DBVM watch disable failed", false) end
  state.hypervisor.watches[watch.id], watch.state = nil, "stopped"
  return { session = session(), watch = watchSummary(watch), stopped = true }
end

local function traceSummary(trace)
  local status, count, maximum = dbvm_traceonbp_getstatus()
  local states = { [0] = "absent", [1] = "armed", [2] = "running", [3] = "completed" }
  return { traceId = trace.id, state = states[status] or "unknown", count = count or 0,
    maxCount = maximum or trace.stepCount, stepCount = trace.stepCount,
    virtualAddress = formatAddress(trace.virtualAddress), physicalAddress = formatAddress(trace.physicalAddress), generation = trace.generation }
end

local function getTrace(id)
  local trace = state.hypervisor.trace
  if not trace or trace.id ~= tostring(id or "") then return nil, errorDetail("NOT_FOUND", "DBVM trace was not found", true, true) end
  if trace.generation ~= state.generation then return nil, errorDetail("STALE_SESSION", "DBVM trace belongs to another target generation", true, true) end
  return trace, nil
end

handlers["dbvm.trace.start"] = function(params)
  local readiness = requireDBVMReady()
  if readiness then return readiness end
  if type(dbk_getPhysicalAddress) ~= "function" or type(dbvm_traceonbp) ~= "function"
    or type(dbvm_traceonbp_getstatus) ~= "function" or type(dbvm_traceonbp_remove) ~= "function" then
    return errorDetail("CAPABILITY_UNAVAILABLE", "Required DBVM trace API is unavailable", true, true)
  end
  if state.hypervisor.trace then return errorDetail("RESOURCE_BUSY", "Only one DBVM trace may exist at a time", true, true) end
  local address = resolveAddress(params.address)
  if not address then return errorDetail("ADDRESS_UNRESOLVED", "Address could not be resolved", true, true) end
  local okPhysical, physical = pcall(dbk_getPhysicalAddress, address)
  if not okPhysical or not physical or physical == 0 then return physicalTranslationError() end
  local steps = math.max(1, math.min(tonumber(params.stepCount) or 1, 1024))
  local options = { logFPU = params.captureFpu == true, logStack = params.captureStack == true }
  local ok, started = pcall(dbvm_traceonbp, physical, steps, address, options)
  if not ok or started == false then return dbvmApiError("DBVM trace start failed: " .. tostring(started), false) end
  local statusOk, traceStatus = pcall(dbvm_traceonbp_getstatus)
  if not statusOk or traceStatus == 0 then
    pcall(dbvm_traceonbp_remove, physical, true)
    return dbvmApiError("DBVM trace did not enter a configured state", false)
  end
  local hypervisor = state.hypervisor
  hypervisor.traceCounter = hypervisor.traceCounter + 1
  local trace = { id = string.format("trace-%08x-%08x", state.generation, hypervisor.traceCounter), physicalAddress = physical,
    virtualAddress = address, stepCount = steps, generation = state.generation }
  hypervisor.trace = trace
  return { session = session(), trace = traceSummary(trace) }
end

handlers["dbvm.trace.status"] = function(params)
  local trace = state.hypervisor.trace
  if params.traceId then
    local failure
    trace, failure = getTrace(params.traceId)
    if failure then return failure end
  end
  if not trace then return { session = session(), items = {}, truncated = false } end
  return { session = session(), trace = traceSummary(trace) }
end

handlers["dbvm.trace.results"] = function(params)
  local trace, failure = getTrace(params.traceId)
  if failure then return failure end
  local ok, entries = pcall(dbvm_traceonbp_retrievelog)
  if not ok then return dbvmApiError("DBVM trace log retrieval failed", true) end
  entries = type(entries) == "table" and entries or {}
  local offset, limit = tonumber(params.cursor or "0") or 0, math.max(1, math.min(tonumber(params.limit) or 100, 200))
  local items = {}
  for index = offset + 1, math.min(offset + limit, #entries) do items[#items + 1] = dbvmContext(entries[index], index) end
  local nextOffset = offset + #items
  local result = { session = session(), trace = traceSummary(trace), items = items, truncated = nextOffset < #entries }
  if result.truncated then result.nextCursor = tostring(nextOffset) end
  return result
end

handlers["dbvm.trace.stop"] = function(params)
  local trace, failure = getTrace(params.traceId)
  if failure then return failure end
  local ok, stopped = pcall(dbvm_traceonbp_stoptrace)
  if not ok or stopped == false then return dbvmApiError("DBVM trace stop failed", false) end
  return { session = session(), trace = traceSummary(trace), stopRequested = true }
end

handlers["dbvm.trace.remove"] = function(params)
  local trace, failure = getTrace(params.traceId)
  if failure then return failure end
  pcall(dbvm_traceonbp_stoptrace)
  local ok, removed = pcall(dbvm_traceonbp_remove, trace.physicalAddress, true)
  if not ok or removed == false then return dbvmApiError("DBVM trace removal failed", false) end
  state.hypervisor.trace = nil
  return { session = session(), trace = { traceId = trace.id, state = "removed", generation = trace.generation }, removed = true }
end

local targetMethods = {
  ["process.get"] = true,
  ["process.detach"] = true,
  ["memory.read"] = true,
  ["memory.compare"] = true,
  ["memory.checksum"] = true,
  ["memory.map"] = true,
  ["disassembly.instruction"] = true,
  ["disassembly.list"] = true,
  ["disassembly.next"] = true,
  ["disassembly.previous"] = true,
  ["disassembly.function"] = true,
  ["symbols.resolve"] = true,
  ["symbols.describe"] = true,
  ["symbols.modules"] = true,
  ["symbols.list"] = true,
  ["scan.start"] = true,
  ["scan.refine"] = true,
  ["scan.results"] = true,
  ["scan.close"] = true,
  ["operations.get"] = true,
  ["operations.list"] = true,
  ["operations.cancel"] = true,
  ["pointer.resolve"] = true,
  ["pointer.validate"] = true,
  ["debug.control.status"] = true,
  ["debug.control.start"] = true,
  ["debug.control.pause"] = true,
  ["debug.control.continue"] = true,
  ["debug.control.detach"] = true,
  ["debug.breakpoints.list"] = true,
  ["debug.breakpoints.set"] = true,
  ["debug.breakpoints.remove"] = true,
  ["debug.events.list"] = true,
  ["threads.list"] = true,
  ["debug.registers.read"] = true,
  ["signature.start"] = true,
  ["signature.result"] = true,
  ["signature.close"] = true,
  ["structures.read"] = true,
  ["dbvm.watch.status"] = true,
  ["dbvm.watch.start"] = true,
  ["dbvm.watch.events"] = true,
  ["dbvm.watch.stop"] = true,
  ["dbvm.trace.status"] = true,
  ["dbvm.trace.start"] = true,
  ["dbvm.trace.results"] = true,
  ["dbvm.trace.stop"] = true,
  ["dbvm.trace.remove"] = true,
}

local function executeRequest(payload)
  local ok, request = pcall(json.decode, payload)
  if not ok or type(request) ~= "table" then
    return json.encode({
      protocolVersion = PROTOCOL_VERSION,
      requestId = "invalid-request",
      error = { code = "INVALID_REQUEST", message = "Invalid JSON request", recoverable = true, safeToRetry = true },
    })
  end
  local requestId = tostring(request.requestId or "invalid-request")
  if request.protocolVersion ~= PROTOCOL_VERSION then
    return json.encode({
      protocolVersion = PROTOCOL_VERSION,
      requestId = requestId,
      error = { code = "PROTOCOL_MISMATCH", message = "Unsupported bridge protocol", recoverable = false, safeToRetry = false },
    })
  end
  if type(request.method) == "string" and request.method:match("^dbvm%.") then
    local params = type(request.params) == "table" and request.params or {}
    if not bridgeHypervisorEnabled then
      return json.encode({
        protocolVersion = PROTOCOL_VERSION, requestId = requestId,
        error = { code = "PROFILE_DISABLED", message = "Bridge hypervisor policy is disabled", recoverable = true, safeToRetry = true },
      })
    end
    if params._policyProfile ~= "hypervisor"
      or not constantTimeEqual(params._authorizationToken, bridgeAuthorizationToken) then
      return json.encode({
        protocolVersion = PROTOCOL_VERSION, requestId = requestId,
        error = { code = "AUTHORIZATION_FAILED", message = "Bridge hypervisor authorization failed", recoverable = false, safeToRetry = false },
      })
    end
  end
  local handler = handlers[request.method]
  if not handler then
    return json.encode({
      protocolVersion = PROTOCOL_VERSION,
      requestId = requestId,
      error = { code = "METHOD_NOT_FOUND", message = "Bridge method is not registered", recoverable = false, safeToRetry = true },
    })
  end
  if targetMethods[request.method] then
    refreshTarget(false)
    local expected = request.params and request.params.expectedGeneration
    if request.sessionId ~= state.sessionId or (expected and expected ~= state.generation) then
      return json.encode({
        protocolVersion = PROTOCOL_VERSION,
        requestId = requestId,
        error = {
          code = "STALE_SESSION",
          message = "Bridge session or generation does not match the current target",
          recoverable = true,
          safeToRetry = request.method ~= "process.detach",
          currentState = state.pid > 0 and "running" or "online",
        },
      })
    end
  end
  local ran, result = pcall(handler, request.params or {})
  if not ran then result = errorDetail("INTERNAL_ERROR", tostring(result), false, false) end
  if result.__error then
    result.__error = nil
    return json.encode({ protocolVersion = PROTOCOL_VERSION, requestId = requestId, error = result })
  end
  return json.encode({ protocolVersion = PROTOCOL_VERSION, requestId = requestId, result = result })
end

local function worker(thread)
  log("worker started")
  while not thread.Terminated do
    local pipe = createPipe(PIPE_NAME, 262144, 262144)
    if not pipe then log("createPipe failed"); return end
    state.pipe = pipe
    pcall(function() pipe.acceptConnection() end)
    if pipe.Connected and not thread.Terminated then
      state.connected = true
      while pipe.Connected and not thread.Terminated do
        local ok, header = pcall(function() return pipe.readBytes(4) end)
        if not ok or not header or #header ~= 4 then break end
        local length = header[1] + header[2] * 256 + header[3] * 65536 + header[4] * 16777216
        if length < 1 or length > MAX_FRAME_BYTES then break end
        local payload = pipe.readString(length)
        if not payload then break end
        local response
        thread.synchronize(function()
          local ran, value = pcall(executeRequest, payload)
          if ran then
            response = value
          else
            response = json.encode({
              protocolVersion = PROTOCOL_VERSION,
              requestId = payload:match('"requestId"%s*:%s*"([^\"]+)"') or "bridge-failure",
              error = {
                code = "INTERNAL_ERROR",
                message = "Bridge request failed: " .. tostring(value),
                recoverable = false,
                safeToRetry = false,
              },
            })
          end
        end)
        local responseLength = #response
        pipe.writeBytes({
          responseLength % 256,
          math.floor(responseLength / 256) % 256,
          math.floor(responseLength / 65536) % 256,
          math.floor(responseLength / 16777216) % 256,
        })
        pipe.writeString(response)
      end
      -- Operation handles belong to the sidecar connection. Never allow a
      -- disconnected client to leave MemScan/FoundList workers behind or to
      -- recover stale handles on a later connection.
      thread.synchronize(function()
        cleanupDebugger()
        cleanupOperations()
        cleanupHypervisor()
        state.diagnostic = "client-disconnected:debugger-and-operations-cleaned"
      end)
    end
    state.connected = false
    state.pipe = nil
    pcall(function() pipe.destroy() end)
    if not thread.Terminated then sleep(50) end
  end
  log("worker stopped")
end

function StopCEMCPBridge()
  cleanupDebugger()
  cleanupOperations()
  cleanupHypervisor()
  if state.worker then state.worker.terminate() end
  if state.pipe then pcall(function() state.pipe.destroy() end) end
  state.worker, state.pipe = nil, nil
  state.running, state.connected = false, false
  log("stopped")
end

local function startMCPServer()
  if state.serverLaunchAttempted then return end
  state.serverLaunchAttempted = true
  local ceDirectory = getCheatEngineDir()
  local mcpDirectory = ceDirectory .. "mcp\\"
  local serverPath = mcpDirectory .. "server.exe"
  local configPath = mcpDirectory .. "config.json"
  if not fileExists(serverPath) then
    log("mcp\\server.exe is not installed; bridge remains available for stdio clients")
    return
  end
  if not fileExists(configPath) then
    log("mcp\\config.json is not installed; automatic HTTP server was not started")
    return
  end
  local parameters = string.format(
    '--config "%s" --ce-pid %d', configPath, getCheatEngineProcessID()
  )
  local launched, result = pcall(shellExecute, serverPath, parameters, mcpDirectory, 0)
  if not launched or result == false then
    log("automatic MCP server launch failed: " .. tostring(result))
    return
  end
  log("automatic MCP server launch requested")
end

function StartCEMCPBridge()
  StopCEMCPBridge()
  refreshTarget(true)
  state.running = true
  state.worker = createThread(worker)
  log("listening on " .. PIPE_NAME)
  startMCPServer()
end

StartCEMCPBridge()
