-- Standalone CE 7.5 debugger lifecycle probe.
-- Development only; requires output and cooperative-target info paths.

local outputPath = os.getenv("CE_MCP_DEBUG_PROBE_OUTPUT")
local targetInfoPath = os.getenv("CE_MCP_DEBUG_TARGET_INFO")
if not outputPath or outputPath == "" or not targetInfoPath or targetInfoPath == "" then return end

local function writeStage(stage, detail)
  local stream = io.open(outputPath, "w")
  if not stream then return end
  stream:write("stage=", stage, "\n")
  if detail then stream:write("detail=", tostring(detail), "\n") end
  stream:close()
end

local function readTargetAddress()
  local stream = io.open(targetInfoPath, "r")
  if not stream then return nil end
  local text = stream:read("*a")
  stream:close()
  local value = text and text:match("address=([0-9A-Fa-f]+)")
  return value and tonumber(value, 16) or nil
end

local function runProbe()
  if rawget(_G, "CE_MCP_DEBUG_PROBE_RUNNING") then return end
  _G.CE_MCP_DEBUG_PROBE_RUNNING = true
  local address = readTargetAddress()
  if not address then
    writeStage("failed", "cooperative target address is unavailable")
    _G.CE_MCP_DEBUG_PROBE_RUNNING = false
    return
  end

  local breakpointInstalled = false
  local hit = false
  local valueAtHit = nil
  local finished = false
  local function cleanup()
    if breakpointInstalled then pcall(debug_removeBreakpoint, address) end
    breakpointInstalled = false
    if debug_isDebugging() then pcall(detachIfPossible) end
  end
  local function finish(ok, detail)
    if finished then return end
    finished = true
    cleanup()
    writeStage(ok and "passed" or "failed", detail)
    _G.CE_MCP_DEBUG_PROBE_RUNNING = false
  end

  writeStage("debug-start")
  local started, startError = pcall(debugProcess, 1)
  if not started or not debug_isDebugging() then
    finish(false, "debugProcess failed: " .. tostring(startError))
    return
  end

  writeStage("breakpoint-install", string.format("address=%X", address))
  local installed, installError = pcall(
    debug_setBreakpoint, address, 4, bptWrite, bpmDebugRegister,
    function()
      valueAtHit = readInteger(address)
      hit = true
      -- Return 0 so CE enters its normal stopped state. Context inspection and
      -- continue must happen in a later main-thread turn, matching MCP control.
      return 0
    end
  )
  if not installed then
    finish(false, "debug_setBreakpoint failed: " .. tostring(installError))
    return
  end
  breakpointInstalled = true
  writeStage("waiting-for-hit")

  local polls = 0
  local timer = createTimer(nil)
  timer.Interval = 100
  timer.OnTimer = function(sender)
    polls = polls + 1
    if hit then
      sender.destroy()
      local broken = debug_isBroken() == true
      local valueBeforeContinue = readInteger(address)
      local targetStopped = valueAtHit ~= nil and valueBeforeContinue == valueAtHit
      local contextOk, contextError = pcall(debug_getContext, true)
      local xmmOk, xmmPointer = pcall(debug_getXMMPointer, 0)
      local xmmBytes = xmmOk and xmmPointer and readBytesLocal(xmmPointer, 16, true) or nil
      if breakpointInstalled then pcall(debug_removeBreakpoint, address) end
      breakpointInstalled = false
      local continued, continueError = pcall(debug_continueFromBreakpoint, co_run)
      local detail = string.format(
        "broken=%s targetStopped=%s context=%s continued=%s rip=%s xmm=%s",
        tostring(broken), tostring(targetStopped), tostring(contextOk), tostring(continued),
        tostring(RIP or EIP), tostring(xmmBytes and #xmmBytes or 0)
      )
      if not contextOk then detail = detail .. " contextError=" .. tostring(contextError) end
      if not continued then detail = detail .. " continueError=" .. tostring(continueError) end
      finish(targetStopped and contextOk and continued and xmmBytes and #xmmBytes == 16, detail)
    elseif polls >= 150 then
      sender.destroy()
      finish(false, "breakpoint did not hit before timeout")
    end
  end
end

local function scheduleProbe()
  local timer = createTimer(nil)
  timer.Interval = 250
  timer.OnTimer = function(sender)
    sender.destroy()
    runProbe()
  end
end

if getOpenedProcessID() == 0 then
  writeStage("waiting-for-target")
  -- A worker-backed poll is independent of startup timers and other autorun
  -- scripts replacing MainForm.OnProcessOpened. Every CE state read and the
  -- eventual probe entry still execute on the synchronized main thread.
  createThread(function(thread)
    for _ = 1, 300 do
      local opened = false
      thread.synchronize(function() opened = getOpenedProcessID() ~= 0 end)
      if opened then
        thread.synchronize(runProbe)
        return
      end
      sleep(100)
    end
    thread.synchronize(function() writeStage("failed", "target attach was not observed") end)
  end)
else
  scheduleProbe()
end
