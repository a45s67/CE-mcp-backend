-- Standalone bounded signature lifecycle probe for CE 7.5.

local outputPath = os.getenv("CE_MCP_SIGNATURE_PROBE_OUTPUT")
local targetInfoPath = os.getenv("CE_MCP_SIGNATURE_TARGET_INFO")
if not outputPath or outputPath == "" or not targetInfoPath or targetInfoPath == "" then return end

local function writeResult(stage, detail)
  local stream = io.open(outputPath, "w")
  if not stream then return end
  stream:write("stage=", stage, "\n")
  if detail then stream:write("detail=", tostring(detail), "\n") end
  stream:close()
end

local function readInfo()
  local stream = io.open(targetInfoPath, "r")
  if not stream then return nil end
  local text = stream:read("*a")
  stream:close()
  local values = {}
  for key, value in text:gmatch("([%w_]+)=([0-9A-Fa-f]+)") do values[key] = value end
  return values
end

local function patternAt(address, length)
  local bytes = readBytes(address, length, true)
  if not bytes or #bytes ~= length then return nil end
  local encoded = {}
  for index, byte in ipairs(bytes) do encoded[index] = string.format("%02X", byte) end
  return table.concat(encoded, " ")
end

local function runProbe()
  local info = readInfo()
  if not info then writeResult("failed", "target info unavailable"); return end
  local target = tonumber(info.target, 16)
  local rangeStart = tonumber(info.base, 16)
  local rangeEnd = tonumber(info["end"], 16)
  local expectedMinimum = tonumber(info.expected_min, 16)
  if not target or not rangeStart or not rangeEnd then
    writeResult("failed", "invalid target info")
    return
  end

  local observedEight = nil
  local uniqueLength = nil
  for length = 8, 16 do
    local scan = createMemScan()
    local found = nil
    local ok, message = pcall(function()
      scan.firstScan(
        soExactValue, vtByteArray, rtRounded, patternAt(target, length), "",
        rangeStart, rangeEnd, "*W*X*C", fsmNotAligned, "1", true, false, false, false
      )
      scan.waitTillDone()
      found = createFoundList(scan)
      found.initialize()
    end)
    if not ok then
      if found then pcall(function() found.destroy() end) end
      pcall(function() scan.destroy() end)
      writeResult("failed", "scan failed: " .. tostring(message))
      return
    end
    local count = tonumber(found.getCount()) or 0
    local first = count > 0 and getAddressSafe(found.Address[0]) or nil
    if length == 8 then observedEight = count end
    if count == 1 and first == target then uniqueLength = length end
    found.destroy()
    scan.destroy()
    if uniqueLength then break end
  end
  local passed = observedEight and observedEight >= 2
    and uniqueLength == expectedMinimum
  writeResult(
    passed and "passed" or "failed",
    string.format("eightCount=%s uniqueLength=%s expected=%s", tostring(observedEight), tostring(uniqueLength), tostring(expectedMinimum))
  )
end

createThread(function(thread)
  for _ = 1, 300 do
    local opened = false
    thread.synchronize(function() opened = getOpenedProcessID() ~= 0 end)
    if opened then thread.synchronize(runProbe); return end
    sleep(100)
  end
  thread.synchronize(function() writeResult("failed", "target attach was not observed") end)
end)
