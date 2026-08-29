-- Standalone CE 7.5 relative-refinement lifecycle probe.
-- Development only; requires CE_MCP_MEMSCAN_RELATIVE_PROBE_OUTPUT.

local outputPath = os.getenv("CE_MCP_MEMSCAN_RELATIVE_PROBE_OUTPUT")
if not outputPath or outputPath == "" then return end

local function writeStage(stage, detail)
  local stream = io.open(outputPath, "w")
  if not stream then return end
  stream:write("stage=", stage, "\n")
  if detail then stream:write("detail=", tostring(detail), "\n") end
  stream:close()
end

local function runProbe()
  if rawget(_G, "CE_MCP_MEMSCAN_RELATIVE_PROBE_RUNNING") then return end
  _G.CE_MCP_MEMSCAN_RELATIVE_PROBE_RUNNING = true
  local allocation, memScan, foundList
  local ok, message = pcall(function()
    allocation = allocateMemory(4096)
    assert(allocation, "target allocation failed")
    assert(writeInteger(allocation, 100), "initial write failed")

    writeStage("first-exact")
    memScan = createMemScan()
    -- Scan the complete committed allocation. CE enumerates memory regions
    -- before filtering candidates, so a four-byte stop range can exclude the
    -- containing page even though the requested value itself is four bytes.
    memScan.firstScan(
      soExactValue, vtDword, rtRounded, "100", "", allocation, allocation + 4095,
      "*W*X*C", fsmNotAligned, "1", false, false, false, false
    )
    memScan.waitTillDone()
    foundList = createFoundList(memScan)
    foundList.initialize()
    assert(
      foundList.getCount() == 1,
      "first exact scan result count=" .. tostring(foundList.getCount())
    )

    local function refine(stage, option)
      writeStage(stage)
      local oldFoundList = foundList
      memScan.nextScan(option, rtRounded, "", "", false, false, false, false, false)
      memScan.waitTillDone()
      oldFoundList.destroy()
      foundList = createFoundList(memScan)
      foundList.initialize()
      assert(foundList.getCount() == 1, stage .. " did not retain the allocation")
      assert(getAddressSafe(foundList.Address[0]) == allocation, stage .. " returned wrong address")
    end

    refine("unchanged", soUnchanged)
    assert(writeInteger(allocation, 101), "increased write failed")
    refine("increased", soIncreasedValue)
    assert(writeInteger(allocation, 99), "decreased write failed")
    refine("decreased", soDecreasedValue)
    assert(writeInteger(allocation, 123), "changed write failed")
    refine("changed", soChanged)
  end)

  writeStage(ok and "passed" or "failed", message)
  if foundList then pcall(function() foundList.destroy() end) end
  if memScan then pcall(function() memScan.destroy() end) end
  if allocation then pcall(function() deAlloc(allocation, 4096) end) end
  _G.CE_MCP_MEMSCAN_RELATIVE_PROBE_RUNNING = false
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
