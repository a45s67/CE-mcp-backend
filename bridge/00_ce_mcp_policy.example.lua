-- Copy to Cheat Engine's autorun directory as 00_ce_mcp_policy.lua and replace
-- the token only when the separately configured sidecar uses profile
-- "hypervisor". Ordinary inspect/debug users do not install this file.
-- Selecting this policy does not initialize DBK or DBVM.
_G.CE_MCP_POLICY = {
  hypervisor = false,
  authorizationToken = "replace-with-at-least-32-random-characters",
}
