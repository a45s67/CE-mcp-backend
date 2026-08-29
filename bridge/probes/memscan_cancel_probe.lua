-- Standalone CE 7.5 running-MemScan destruction probe.
-- Development only; requires CE_MCP_MEMSCAN_CANCEL_PROBE_OUTPUT.

local outputPath = os.getenv("CE_MCP_MEMSCAN_CANCEL_PROBE_OUTPUT")
if not outputPath or outputPath == "" then return end

local function writeStage(stage, detail)
  local stream = io.open(outputPath, "w")
  if not stream then return end
  stream:write("stage=", stage, "\n")
  if detail then stream:write("detail=", tostring(detail), "\n") end
  stream:close()
end

local function runProbe()
  if rawget(_G, "CE_MCP_MEMSCAN_CANCEL_PROBE_RUNNING") then return end
  _G.CE_MCP_MEMSCAN_CANCEL_PROBE_RUNNING = true
  local memScan = createMemScan()
  writeStage("scan-started")
  memScan.firstScan(
    soUnknownValue, vtByte, rtRounded, "", "", 0, 0x7FFFFFFFFFFFFFFF,
    "*W*X*C", fsmNotAligned, "1", false, false, false, false
  )

  local cancelTimer = createTimer(nil)
  cancelTimer.Interval = 1
  cancelTimer.OnTimer = function(sender)
    sender.destroy()
    local progress = memScan.getProgress()
    local total = tonumber(progress.TotalAddressesToScan) or 0
    local completed = tonumber(progress.CurrentlyScanned) or 0
    if total <= 0 or completed >= total then
      writeStage("inconclusive", string.format("completed=%d total=%d", completed, total))
      pcall(function() memScan.destroy() end)
      _G.CE_MCP_MEMSCAN_CANCEL_PROBE_RUNNING = false
      return
    end

    writeStage("destroy-running", string.format("completed=%d total=%d", completed, total))
    local ok, message = pcall(function() memScan.destroy() end)
    if not ok then
      writeStage("failed", message)
      _G.CE_MCP_MEMSCAN_CANCEL_PROBE_RUNNING = false
      return
    end

    writeStage("destroy-returned")
    local responsiveTimer = createTimer(nil)
    responsiveTimer.Interval = 100
    responsiveTimer.OnTimer = function(responsiveSender)
      responsiveSender.destroy()
      writeStage("passed", "running MemScan destroyed and GUI timer resumed")
      _G.CE_MCP_MEMSCAN_CANCEL_PROBE_RUNNING = false
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
  local previousOnProcessOpened = MainForm.OnProcessOpened
  MainForm.OnProcessOpened = function(processId, processHandle, caption)
    if previousOnProcessOpened then
      previousOnProcessOpened(processId, processHandle, caption)
    end
    scheduleProbe()
  end
else
  scheduleProbe()
end
