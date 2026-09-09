-- Test only: sole autorun in an isolated copy of CE 7.7, disposable target only.
-- The parent must enforce a timeout and exact-PID cleanup. A main-thread timer
-- cannot recover a blocked native API. Never load this beside the MCP bridge.
-- celua.txt documents the APIs; split order and callback/context behavior below
-- are measured, not borrowed from debugger_lifecycle_probe's assumptions.

local mode = os.getenv("CE_RELIABILITY_MODE")
local step = os.getenv("CE_RELIABILITY_STEP") or ""
local output = os.getenv("CE_RELIABILITY_OUTPUT") or ""
assert(output:match("^%a:[/\\]") or output:match("^\\\\[^\\]+\\[^\\]+\\"),
  "CE_RELIABILITY_OUTPUT must be an absolute append JSONL path")
local stream, openError = io.open(output, "ab")
assert(stream, "Cannot open reliability output: " .. tostring(openError))

local function json(value)
  local kind = type(value)
  if kind == "nil" then return "null" end
  if kind == "boolean" then return tostring(value) end
  if kind == "number" then
    assert(value == value and value ~= math.huge and value ~= -math.huge, "Nonfinite JSON number")
    return tostring(value)
  end
  if kind == "string" then
    return '"' .. value:gsub('[%z\1-\31\\"]', function(c)
      if c == '"' or c == "\\" then return "\\" .. c end
      return string.format("\\u%04x", c:byte())
    end) .. '"'
  end
  assert(kind == "table", "Unsupported JSON type: " .. kind)
  local parts = {}
  if #value > 0 then
    for i = 1, #value do parts[i] = json(value[i]) end
    return "[" .. table.concat(parts, ",") .. "]"
  end
  for key, item in pairs(value) do
    assert(type(key) == "string", "JSON object key must be a string")
    parts[#parts + 1] = json(key) .. ":" .. json(item)
  end
  table.sort(parts)
  return "{" .. table.concat(parts, ",") .. "}"
end

local phase, sequence, pid = "startup", 0, nil
local function log(event, record)
  record = record or {}
  sequence = sequence + 1
  record.event, record.phase, record.sequence = event, phase, sequence
  record.mode, record.pid, record.time = mode, pid, os.time()
  assert(stream:write(json(record), "\n"))
  assert(stream:flush())
end

-- All CE calls, including clock reads and timer methods, have a flushed marker.
local function api(name, fn, ...)
  log("stage", {api = name})
  local result = table.pack(pcall(fn, ...))
  if not result[1] then
    log("api_error", {api = name, error = tostring(result[2])})
    error(name .. ": " .. tostring(result[2]), 0)
  end
  return table.unpack(result, 2, result.n)
end

local function now() return api("getTickCount", getTickCount) end
local function elapsed(start) return (now() - start) % 4294967296 end
local function valueRecord(value)
  local kind = type(value)
  return {type = kind, value = (kind == "function" or kind == "userdata" or kind == "thread")
    and tostring(value) or value}
end

local timer, code, counter, startedAt, pollActive
local finished, debugOwned, breakpointAttempted, scopeLost = false, false, false, false
local cleanupOK, behaviorOK, reason = true, false, "not completed"
local hits, firstCounter, firstAt, callbackActive, callbackError = 0, nil, nil, false, nil
local samples, observationsOK, changed = 0, true, false
local resumeCounter, resumeAt, detachAt, resumed = nil, nil, nil, false
local removalHits = nil
local stepIP, stepAt = nil, nil

local function sameTarget()
  local actual = api("getOpenedProcessID", getOpenedProcessID)
  if actual ~= pid then
    scopeLost = true
    log("scope_lost", {expected = pid, actual = actual})
    error("Target PID changed; no cleanup mutations authorized", 0)
  end
end

local function readCounter()
  local value = api("readInteger(counter)", readInteger, counter)
  assert(type(value) == "number", "Counter is unreadable")
  return value % 4294967296
end

local function finish(ok, detail)
  if finished then return end
  finished = true
  if timer then
    local destroyed = pcall(api, "timer.destroy", timer.destroy)
    timer = nil
    if not destroyed then cleanupOK = false end
  end
  phase = "finished"
  log("result", {result = ok and cleanupOK and "PASS" or "FAIL",
    detail = cleanupOK and detail or (detail .. "; cleanup/resume not verified"),
    behavior_ok = behaviorOK, cleanup_ok = cleanupOK, hits = hits,
    hit_count_kind = mode == "breakpoint-none" and "observed_native_stop" or "callbacks",
    samples = samples, counter_resumed = resumed, scope_lost = scopeLost})
  if scopeLost then
    log("close_skipped", {reason = "Target ownership not exclusive; external runner must reconcile"})
    return
  end
  local scheduled, err = pcall(api, "createTimer(close,200ms)", createTimer, 200, function()
    local closed, closeError = pcall(api, "closeCE", closeCE)
    if not closed then log("close_error", {error = tostring(closeError)}) end
    stream:close()
  end)
  if not scheduled then log("close_error", {error = tostring(err)}) end
end

local function breakpointPresent()
  local list = api("debug_getBreakpointList", debug_getBreakpointList)
  assert(type(list) == "table", "Breakpoint list unavailable")
  local found, count = false, 0
  for _, address in pairs(list) do
    count = count + 1
    if address == counter then found = true end
  end
  log("breakpoint_inventory", {count = count, owned_present = found})
  return found, count
end

local function detach()
  phase = "detach"
  sameTarget()
  local ok, result = pcall(api, "detachIfPossible", detachIfPossible)
  log("detach_return", {call_ok = ok, returned = valueRecord(result)})
  cleanupOK = cleanupOK and ok and result ~= false
  detachAt = now()
end

local function cleanup(ok, detail)
  behaviorOK, reason, phase = ok, detail, "cleanup"
  sameTarget()
  -- Installation can throw after mutation. Reconcile the native list, never
  -- retry installation, and remove only the address this probe attempted.
  local removed = not breakpointAttempted
  if breakpointAttempted then
    local listed, present = pcall(breakpointPresent)
    if listed and present then
      local called, result = pcall(api, "debug_removeBreakpoint", debug_removeBreakpoint, counter)
      log("remove_return", {call_ok = called, returned = valueRecord(result)})
      cleanupOK = cleanupOK and called and result ~= false
    end
    local checked, remaining = pcall(breakpointPresent)
    removed = listed and checked
    cleanupOK = cleanupOK and removed
    log("remove_requested", {still_listed = remaining,
      note = "CE retains marked records until debugger cleanup after continue"})
  end
  local broken = api("debug_isBroken(cleanup)", debug_isBroken)
  local context = api("debug_getContext(cleanup,true)", debug_getContext, true)
  log("cleanup_state", {broken = valueRecord(broken), context = valueRecord(context)})
  -- Never use pcall success or cached register globals as evidence of a stop.
  if removed and context == true then
    if step ~= "" then
      assert(mode == "breakpoint-1" and (step == "into" or step == "over"), "Invalid step mode")
      stepIP, stepAt = RIP or EIP, now()
      removalHits = hits
      api("debug_continueFromBreakpoint(step)", debug_continueFromBreakpoint,
        step == "into" and co_stepinto or co_stepover)
      phase = "step"
      return
    end
    local called, result = pcall(api, "debug_continueFromBreakpoint(co_run)",
      debug_continueFromBreakpoint, co_run)
    log("continue_return", {call_ok = called, returned = valueRecord(result)})
    cleanupOK = cleanupOK and called and result ~= false
  elseif context ~= false then
    cleanupOK = false
    log("continue_skipped", {reason = "Stop not confirmed or breakpoint removal not verified"})
  else
    log("continue_skipped", {reason = "No native context; counter progress will verify running state"})
  end
  if not removed then detach(); return end
  resumeCounter, resumeAt, removalHits = readCounter(), now(), hits
  phase = "resume"
end

local function fail(err)
  log("failure", {error = tostring(err)})
  if finished then return end
  if debugOwned and not scopeLost and phase ~= "cleanup" and phase ~= "resume" and phase ~= "detach" then
    local ok, cleanupError = pcall(cleanup, false, tostring(err))
    if ok then return end
    log("cleanup_error", {error = tostring(cleanupError)})
  end
  cleanupOK = not debugOwned and not scopeLost
  -- A failed cleanup is not retried. A single detach attempt is still allowed
  -- if no detach was attempted and the exact target identity can be rechecked.
  if debugOwned and not scopeLost and phase ~= "detach" then
    local ok, detachError = pcall(detach)
    if ok then behaviorOK, reason = false, tostring(err); return end
    log("cleanup_error", {error = tostring(detachError)})
  end
  finish(false, tostring(err))
end

local function poll()
  if finished or callbackActive or phase == "setup" then return end
  sameTarget()
  if phase == "observe" then
    if callbackError then error(callbackError, 0) end
    local broken = api("debug_isBroken(sample)", debug_isBroken)
    local context = api("debug_getContext(sample,true)", debug_getContext, true)
    local value = readCounter()
    if mode == "breakpoint-none" and firstCounter == nil and context == true then
      hits, firstCounter = 1, value
      log("first_hit", {counter = value, source = "first_native_context_timer"})
    end
    -- First-hit time is latched on a later timer turn, never in the callback.
    if firstCounter ~= nil and firstAt == nil then firstAt = now() end
    if firstAt ~= nil then
      samples = samples + 1
      local runningMode = mode == "breakpoint-0"
      changed = changed or value ~= firstCounter
      observationsOK = observationsOK and
        ((runningMode and context == false) or
        (not runningMode and context == true and value == firstCounter))
      local duration = elapsed(firstAt)
      log("sample", {index = samples, elapsed_ms = duration, counter = value,
        first_counter = firstCounter, hits = hits, broken = valueRecord(broken),
        context = valueRecord(context)})
      if duration >= 1000 then
        local ok = observationsOK and samples >= 10 and
          ((runningMode and changed and hits > 1) or (not runningMode and hits == 1))
        cleanup(ok, ok and "Expected native callback behavior observed" or "Native behavior mismatch")
        return
      end
    else
      log("waiting", {counter = value, hits = hits, broken = valueRecord(broken),
        context = valueRecord(context)})
    end
    if elapsed(startedAt) >= 7000 then cleanup(false, "Native observation deadline exceeded") end
  elseif phase == "step" then
    local context = api("debug_getContext(step,true)", debug_getContext, true)
    local value = readCounter()
    local ip = context == true and (RIP or EIP) or nil
    log("step_sample", {context = valueRecord(context), ip = ip, old_ip = stepIP,
      counter = value, first_counter = firstCounter, hits = hits, mode_name = step})
    if context == true and ip ~= stepIP then
      cleanupOK = cleanupOK and value == firstCounter and hits == removalHits
      api("debug_continueFromBreakpoint(after_step)", debug_continueFromBreakpoint, co_run)
      resumeCounter, resumeAt = value, now()
      phase = "resume"
    elseif elapsed(stepAt) >= 3000 then
      cleanupOK = false
      detach()
    end
  elseif phase == "resume" then
    local value = readCounter()
    local broken = api("debug_isBroken(resume)", debug_isBroken)
    local duration = elapsed(resumeAt)
    local delta = (value - resumeCounter) % 4294967296
    resumed = delta > 0 and delta < 2147483648
    local present = breakpointPresent()
    local context = api("debug_getContext(resume,true)", debug_getContext, true)
    log("resume_sample", {counter = value, baseline = resumeCounter, elapsed_ms = duration,
      increased = resumed, broken = valueRecord(broken), context = valueRecord(context),
      remaining = present, hits = hits, removal_hits = removalHits})
    if hits ~= removalHits or (resumed and not present and duration >= 300) or duration >= 10000 then
      cleanupOK = cleanupOK and resumed and not present and hits == removalHits and context == false
      if not resumed then log("failure", {error = "Counter did not increase after breakpoint removal"}) end
      detach()
    end
  elseif phase == "detach" then
    local active = api("debug_isDebugging(detach_verify)", debug_isDebugging)
    -- Breakpoint absence was checked before detaching; do not require a
    -- breakpoint-list object to survive destruction of the debugger.
    local verified = active == false
    log("detach_verified", {debugging = valueRecord(active), verified = verified})
    if verified or elapsed(detachAt) >= 1000 then
      cleanupOK = cleanupOK and verified
      finish(behaviorOK, reason)
    end
  end
end

local function run()
  log("begin", {output = output, external_timeout_required = true})
  local version = api("getCEVersion", getCEVersion)
  log("version", {returned = valueRecord(version)})
  assert(version == 7.7, "Probe requires exactly CE 7.7")
  assert(mode == "disassembly" or mode == "memory-map" or mode == "breakpoint-0" or
    mode == "breakpoint-1" or mode == "breakpoint-none", "Invalid CE_RELIABILITY_MODE")
  local text = os.getenv("CE_RELIABILITY_PID") or ""
  assert(text:match("^%d+$"), "CE_RELIABILITY_PID must be decimal")
  pid = tonumber(text)
  assert(pid and pid > 0 and pid <= 4294967295 and pid % 1 == 0, "Invalid target PID")
  local function address(name)
    local hex = (os.getenv(name) or ""):gsub("^0[xX]", "")
    assert(hex:match("^[%x]+$"), name .. " must be hexadecimal")
    local value = tonumber(hex, 16)
    assert(#hex <= 16 and value and value > 0 and value % 1 == 0, name .. " is invalid")
    return value
  end
  counter, code = address("CE_RELIABILITY_COUNTER"), address("CE_RELIABILITY_CODE")
  assert(counter % 4 == 0, "Hardware counter must be four-byte aligned")
  local existing = api("getOpenedProcessID(preflight)", getOpenedProcessID)
  if existing ~= 0 and existing ~= pid then
    scopeLost = true
    error("Isolated CE already has a different target", 0)
  end
  if api("debug_isDebugging(preflight)", debug_isDebugging) ~= false then
    scopeLost = true
    error("Probe requires an instance without an existing debugger", 0)
  end
  startedAt = now()
  api("openProcess(envpid)", openProcess, pid)
  sameTarget()
  log("target", {counter = counter, code = code})
  local bytes = api("readBytes(code,2,true)", readBytes, code, 2, true)
  log("fixture_bytes", {raw = bytes})
  assert(mode == "memory-map" or (type(bytes) == "table" and #bytes == 2 and bytes[1] == 0x90 and bytes[2] == 0xC3),
    "Target code fixture is not 90 C3")

  if mode == "disassembly" then
    phase = "disassembly"
    local display = api("disassemble(code)", disassemble, code)
    log("disassembly", {display = display})
    local split = table.pack(api("splitDisassembledString", splitDisassembledString, display))
    local returns, fields = {}, {}
    for i = 1, split.n do returns[i] = valueRecord(split[i]) end
    log("split_returns", {count = split.n, returns = returns, raw = bytes})
    assert(split.n == 4, "Expected four split returns")
    for i = 1, 4 do
      assert(type(split[i]) == "string", "Split return is not a string")
      local trimmed = split[i]:match("^%s*(.-)%s*$")
      if trimmed:lower() == "nop" then fields.opcode = i
      elseif trimmed:gsub("%s", ""):upper() == "90" then fields.bytes = i
      elseif trimmed ~= "" then
        local resolved = api("getAddressSafe(split[" .. i .. "])", getAddressSafe, trimmed)
        log("split_address", {index = i, text = split[i], resolved = resolved, expected = code})
        if resolved == code then fields.address = i end
      end
    end
    assert(fields.opcode and fields.bytes and fields.address, "Split opcode/bytes/address mismatch")
    for i = 1, 4 do
      if i ~= fields.opcode and i ~= fields.bytes and i ~= fields.address then fields.extra = i end
    end
    log("split_mapping", {indices = fields, extra = split[fields.extra]})
    behaviorOK = fields.extra ~= nil
    finish(behaviorOK, "Split fields identified from nop, raw bytes, and numeric address")
  elseif mode == "memory-map" then
    phase = "memory-map"
    local start = now()
    local regions = api("enumMemoryRegions", enumMemoryRegions)
    local enumerationMs = elapsed(start)
    assert(type(regions) == "table" and #regions > 0, "Memory enumeration returned no regions")
    local states, types = {}, {}
    for _, region in ipairs(regions) do
      local state, kind = tostring(region.State), tostring(region.Type)
      states[state], types[kind] = (states[state] or 0) + 1, (types[kind] or 0) + 1
    end
    log("regions", {count = #regions, states = states, types = types, elapsed_ms = enumerationMs})
    for i = 1, math.min(3, #regions) do
      local queried = api("getMemoryRegionInfo", getMemoryRegionInfo, regions[i].BaseAddress)
      log("single_region", {index = i, region = queried})
      for _, field in ipairs({"BaseAddress", "AllocationBase", "RegionSize", "State", "Protect", "Type"}) do
        assert(queried[field] == regions[i][field], "Single-region query differs: " .. field)
      end
    end
    local total, maximum, first = 0, 0, {}
    for i, region in ipairs(regions) do
      assert(type(region.BaseAddress) == "number", "Region lacks numeric BaseAddress")
      start = now()
      local name = api(string.format("getNameFromAddress[%d]@%X", i, region.BaseAddress), getNameFromAddress,
        region.BaseAddress, true, false, false)
      local duration = elapsed(start)
      assert(type(name) == "string", "Region name is not a string")
      total, maximum = total + duration, math.max(maximum, duration)
      if i <= 3 then first[i] = {base = region.BaseAddress, name = name, elapsed_ms = duration} end
      local expired = elapsed(startedAt) >= 9000
      if i == 3 or i % 100 == 0 or i == #regions or expired then
        log("names_progress", {named = i, count = #regions, total_ms = total, max_ms = maximum,
          first_three = first, timing_includes_stage_io = true})
      end
      assert(not expired, "Memory-map deadline exceeded (partial naming evidence)")
    end
    behaviorOK = true
    finish(true, "Enumeration and every per-region name completed")
  else
    phase = "setup"
    -- Establish the cleanup timer before any debugger mutation can fail.
    timer = api("createTimer(poll,disabled)", createTimer, nil, false)
    api("timer.Interval=100", function() timer.Interval = 100 end)
    api("timer.OnTimer=poll", function()
      timer.OnTimer = function()
        if pollActive then return end
        pollActive = true
        local ok, err = pcall(poll)
        if not ok then fail(err) end
        pollActive = false
      end
    end)
    api("timer.Enabled=true", function() timer.Enabled = true end)
    log("global_callback_unset", {previous_type = type(rawget(_G, "debugger_onBreakpoint"))})
    rawset(_G, "debugger_onBreakpoint", nil)
    debugOwned = true
    local result = api("debugProcess(1)", debugProcess, 1)
    log("debug_start_return", {returned = valueRecord(result)})
    assert(result ~= false and api("debug_isDebugging(start)", debug_isDebugging) == true,
      "Windows debugger did not start")
    local interface = api("debug_getCurrentDebuggerInterface", debug_getCurrentDebuggerInterface)
    log("debug_interface", {returned = valueRecord(interface)})
    assert(interface == 1, "Debugger interface is not Windows")
    local _, count = breakpointPresent()
    if count ~= 0 then
      scopeLost = true
      error("Isolated debugger contains unowned breakpoints; cleanup withheld", 0)
    end
    local initialBroken = api("debug_isBroken(preinstall)", debug_isBroken)
    log("preinstall_broken", {returned = valueRecord(initialBroken)})
    assert(initialBroken ~= true, "Debugger already stopped")
    log("counter_baseline", {counter = readCounter()})
    local callbackMode = mode == "breakpoint-0" and 0 or 1
    local function onHit()
      callbackActive = true
      hits = hits + 1
      if hits == 1 then
        local ok, err = pcall(function()
          sameTarget()
          firstCounter = readCounter()
          log("first_hit", {counter = firstCounter, source = "callback", return_mode = callbackMode})
        end)
        if not ok then callbackError = tostring(err) end
      end
      callbackActive = false
      return callbackMode
    end
    breakpointAttempted = true
    if mode == "breakpoint-none" then
      result = api("debug_setBreakpoint(no_callback)", debug_setBreakpoint,
        counter, 4, bptWrite, bpmDebugRegister)
    else
      result = api("debug_setBreakpoint(callback)", debug_setBreakpoint,
        counter, 4, bptWrite, bpmDebugRegister, onHit)
    end
    log("breakpoint_set_return", {returned = valueRecord(result)})
    local present, installedCount = breakpointPresent()
    assert(result ~= false and present and installedCount == 1, "Owned breakpoint installation not verified")
    phase = "observe"
  end
end

pollActive = true
local ok, err = pcall(run)
if not ok then fail(err) end
pollActive = false
