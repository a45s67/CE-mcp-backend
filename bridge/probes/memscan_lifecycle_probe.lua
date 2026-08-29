-- Standalone CE 7.5 MemScan lifecycle probe.
--
-- This is deliberately not a bridge handler. Load it only in an isolated test
-- CE process with CE_MCP_MEMSCAN_PROBE_OUTPUT set to a local result path. After
-- a target is opened, its OnProcessOpened hook probes the exact native order:
-- firstScan -> wait -> FoundList1 -> nextScan -> wait -> destroy FoundList1 ->
-- FoundList2 -> cleanup.

local outputPath = os.getenv("CE_MCP_MEMSCAN_PROBE_OUTPUT")
if not outputPath or outputPath == "" then return end

local function writeStage(stage, detail)
  local stream = io.open(outputPath, "w")
  if not stream then return end
  stream:write("stage=", stage, "\n")
  if detail then stream:write("detail=", tostring(detail), "\n") end
  stream:close()
end

local function runProbe()
  if rawget(_G, "CE_MCP_MEMSCAN_PROBE_RUNNING") then return end
  _G.CE_MCP_MEMSCAN_PROBE_RUNNING = true
  local memScan, firstFoundList, secondFoundList
  local ok, message = pcall(function()
    local modules = enumModules()
    assert(type(modules) == "table" and modules[1], "target has no module")
    local base = modules[1].Address
    assert(type(base) == "number", "module base is unavailable")
    local bytes = readBytes(base, 1, true)
    assert(bytes and bytes[1], "module base is unreadable")
    local value = tostring(bytes[1])

    writeStage("first-scan", string.format("base=%X value=%s", base, value))
    memScan = createMemScan()
    memScan.firstScan(
      soExactValue, vtByte, rtRounded, value, "", base, base + 4095,
      "*W*X*C", fsmNotAligned, "1", false, false, false, false
    )
    memScan.waitTillDone()

    writeStage("first-found-list")
    firstFoundList = createFoundList(memScan)
    firstFoundList.initialize()
    assert(firstFoundList.getCount() > 0, "first scan returned no results")

    writeStage("next-scan")
    memScan.nextScan(
      soExactValue, rtRounded, value, "", false, false, false, false, false
    )
    memScan.waitTillDone()

    writeStage("replace-found-list")
    firstFoundList.destroy()
    firstFoundList = nil
    secondFoundList = createFoundList(memScan)
    secondFoundList.initialize()
    assert(secondFoundList.getCount() > 0, "refined scan returned no results")
    local foundBase = false
    for index = 0, math.min(secondFoundList.getCount(), 200) - 1 do
      if getAddressSafe(secondFoundList.Address[index]) == base then
        foundBase = true
        break
      end
    end
    assert(foundBase, "refined scan did not retain module base")
  end)

  writeStage(ok and "passed" or "failed", message)
  if secondFoundList then pcall(function() secondFoundList.destroy() end) end
  if firstFoundList then pcall(function() firstFoundList.destroy() end) end
  if memScan then pcall(function() memScan.destroy() end) end
  _G.CE_MCP_MEMSCAN_PROBE_RUNNING = false
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
