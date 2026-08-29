-- Direct bounded DBVM API probe. Never initializes DBK or DBVM.

local outputPath = os.getenv("CE_MCP_DBVM_DIRECT_OUTPUT")
local targetInfoPath = os.getenv("CE_MCP_DBVM_DIRECT_TARGET_INFO")
if not outputPath or outputPath == "" or not targetInfoPath or targetInfoPath == "" then return end

local observations = {}
local function record(key, value)
  observations[#observations + 1] = tostring(key) .. "=" .. tostring(value):gsub("[\r\n]", " ")
end
local function finish(stage)
  local stream = io.open(outputPath, "w")
  if not stream then return end
  stream:write("stage=", stage, "\n", table.concat(observations, "\n"), "\n")
  stream:close()
end
local function readTarget()
  local stream = io.open(targetInfoPath, "r")
  if not stream then return nil, nil end
  local text = stream:read("*a")
  stream:close()
  return tonumber(text:match("pid=(%d+)")), tonumber(text:match("address=([0-9A-Fa-f]+)"), 16)
end

local function run()
  record("has_trace_status", type(rawget(_G, "dbvm_traceonbp_getstatus")) == "function")
  record("has_physical_query", type(rawget(_G, "dbk_getPhysicalAddress")) == "function")
  record("has_watch_start", type(rawget(_G, "dbvm_watch_writes")) == "function")
  record("has_watch_log", type(rawget(_G, "dbvm_watch_retrievelog")) == "function")
  record("has_watch_disable", type(rawget(_G, "dbvm_watch_disable")) == "function")
  record("has_dbk_readiness", type(rawget(_G, "dbk_initialized")) == "function")
  record("has_dbvm_readiness", type(rawget(_G, "dbvm_initialized")) == "function")
  if type(rawget(_G, "dbk_initialized")) == "function" then
    local ok, value = pcall(dbk_initialized)
    record("dbk_readiness_ok", ok)
    record("dbk_initialized", value)
  end
  if type(rawget(_G, "dbvm_initialized")) == "function" then
    local ok, value = pcall(dbvm_initialized)
    record("dbvm_readiness_ok", ok)
    record("dbvm_initialized", value)
  end

  if type(rawget(_G, "dbvm_traceonbp_getstatus")) == "function" then
    local ok, status, count, maximum = pcall(dbvm_traceonbp_getstatus)
    record("trace_status_ok", ok)
    record("trace_status", status)
    record("trace_count", count)
    record("trace_maximum", maximum)
  end

  local pid, address = readTarget()
  if not pid or not address then record("target", "unavailable"); finish("failed"); return end
  local attached, attachResult = pcall(openProcess, pid)
  record("attach_ok", attached)
  record("attach_result", attachResult)
  if not attached then finish("completed"); return end

  local physicalOk, physical = pcall(dbk_getPhysicalAddress, address)
  record("physical_ok", physicalOk)
  record("physical", physical and string.format("0x%X", physical) or physical)
  if not physicalOk or not physical or physical == 0 then finish("completed"); return end

  local watchOk, watchId = pcall(dbvm_watch_writes, physical, 1, 1, 16)
  record("watch_start_ok", watchOk)
  record("watch_id", watchId)
  if watchOk and watchId ~= nil then
    sleep(100)
    local logOk, entries = pcall(dbvm_watch_retrievelog, watchId)
    record("watch_log_ok", logOk)
    record("watch_event_count", type(entries) == "table" and #entries or entries)
    local disableOk, disabled = pcall(dbvm_watch_disable, watchId)
    record("watch_disable_ok", disableOk)
    record("watch_disabled", disabled)
  end
  finish("completed")
end

local ok, message = pcall(run)
if not ok then record("probe_error", message); finish("failed") end
