param(
    [Parameter(Mandatory = $true)]
    [string]$CheatEngineDir,
    [switch]$RotateToken
)

$ErrorActionPreference = "Stop"
$candidateRoot = $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $candidateRoot "mcp\server.exe"))) {
    $candidateRoot = Split-Path -Parent $PSScriptRoot
}
$packageRoot = [IO.Path]::GetFullPath($candidateRoot)
$ceRoot = [IO.Path]::GetFullPath($CheatEngineDir)
$sourceMcp = Join-Path $packageRoot "mcp"
$sourceBridge = Join-Path $packageRoot "autorun\ce_mcp_bridge.lua"

if (-not (Test-Path -LiteralPath $sourceBridge -PathType Leaf)) {
    throw "release bridge is missing: $sourceBridge"
}
if (-not (Test-Path -LiteralPath (Join-Path $sourceMcp "server.exe") -PathType Leaf)) {
    throw "release server is missing: $sourceMcp\server.exe"
}
if (-not (Test-Path -LiteralPath $ceRoot -PathType Container)) {
    throw "Cheat Engine directory does not exist: $ceRoot"
}
$ceExecutables = Get-ChildItem -LiteralPath $ceRoot -File -ErrorAction Stop |
    Where-Object { $_.Name -match '^(cheat engine|cheatengine-.+)\.exe$' }
if (-not $ceExecutables) {
    throw "directory does not contain a recognized Cheat Engine executable: $ceRoot"
}
$running = Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessName -eq "cheat engine" -or $_.ProcessName -like "cheatengine-*" }
if ($running) {
    throw "close all Cheat Engine instances before installing CE MCP"
}

$destinationAutorun = Join-Path $ceRoot "autorun"
$destinationMcp = Join-Path $ceRoot "mcp"
New-Item -ItemType Directory -Force $destinationAutorun, $destinationMcp | Out-Null

Get-ChildItem -LiteralPath $sourceMcp -Force | Where-Object {
    $_.Name -ne "config.example.json"
} | ForEach-Object {
    $destination = Join-Path $destinationMcp $_.Name
    Copy-Item -LiteralPath $_.FullName -Destination $destination -Recurse -Force
}

$configPath = Join-Path $destinationMcp "config.json"
if (-not (Test-Path -LiteralPath $configPath)) {
    Copy-Item -LiteralPath (Join-Path $sourceMcp "config.example.json") -Destination $configPath
}

$tokenPath = Join-Path $destinationMcp "http.token"
if ($RotateToken -or -not (Test-Path -LiteralPath $tokenPath)) {
    $bytes = [byte[]]::new(48)
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    [IO.File]::WriteAllText(
        $tokenPath,
        [Convert]::ToBase64String($bytes),
        [Text.UTF8Encoding]::new($false)
    )
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$grant = "*$identity`:(F)"
& icacls.exe $tokenPath "/inheritance:r" "/grant:r" $grant | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "failed to restrict access to HTTP token: $tokenPath"
}

$bridgeDestination = Join-Path $destinationAutorun "ce_mcp_bridge.lua"
$bridgeTemporary = "$bridgeDestination.new"
Copy-Item -LiteralPath $sourceBridge -Destination $bridgeTemporary -Force
Move-Item -LiteralPath $bridgeTemporary -Destination $bridgeDestination -Force

Write-Output "Installed CE MCP into $ceRoot"
Write-Output "Restart Cheat Engine to start the MCP server."
