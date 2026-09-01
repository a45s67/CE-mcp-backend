param(
    [string]$ReleaseRoot = "dist\ce-mcp-windows-x64"
)

$ErrorActionPreference = "Stop"
$repoRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$release = [IO.Path]::GetFullPath((Join-Path $repoRoot $ReleaseRoot))
if (-not $release.StartsWith($repoRoot + [IO.Path]::DirectorySeparatorChar)) {
    throw "release root escaped repository"
}
$temporary = Join-Path ([IO.Path]::GetTempPath()) ("ce-mcp-installer-" + [guid]::NewGuid())
$fakeCe = Join-Path $temporary "Cheat Engine"

# The production installer must refuse a live CE process. This isolated verifier
# replaces only that read-only observation so it can exercise file installation
# without closing the developer's real CE instance.
function Get-Process {
    [CmdletBinding()]
    param()
    return @()
}

try {
    New-Item -ItemType Directory -Force $fakeCe | Out-Null
    [IO.File]::WriteAllBytes((Join-Path $fakeCe "cheatengine-x86_64.exe"), [byte[]]@(0))
    & (Join-Path $release "install.ps1") -CheatEngineDir $fakeCe

    $bridge = Join-Path $fakeCe "autorun\ce_mcp_bridge.lua"
    $server = Join-Path $fakeCe "mcp\server.exe"
    $controller = Join-Path $fakeCe "mcp\ce-mcp-control.exe"
    $config = Join-Path $fakeCe "mcp\config.json"
    $token = Join-Path $fakeCe "mcp\http.token"
    foreach ($path in ($bridge, $server, $controller, $config, $token)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "installer omitted $path"
        }
    }
    $firstToken = [IO.File]::ReadAllText($token)
    if ($firstToken.Length -lt 64) { throw "installer generated a short token" }
    $customConfig = '{"transport":"streamable-http","host":"127.0.0.1","port":43180,"tokenFile":"http.token","requestDeadlineMs":5000,"exitWhenCeExits":true}'
    [IO.File]::WriteAllText($config, $customConfig)

    & (Join-Path $release "install.ps1") -CheatEngineDir $fakeCe
    if ([IO.File]::ReadAllText($token) -ne $firstToken) {
        throw "normal upgrade rotated the HTTP token"
    }
    if ([IO.File]::ReadAllText($config) -ne $customConfig) {
        throw "normal upgrade replaced config.json"
    }

    & (Join-Path $release "install.ps1") -CheatEngineDir $fakeCe -RotateToken
    if ([IO.File]::ReadAllText($token) -eq $firstToken) {
        throw "RotateToken did not replace the HTTP token"
    }
    Write-Output "installer verification passed"
} finally {
    $fullTemporary = [IO.Path]::GetFullPath($temporary)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if ($fullTemporary.StartsWith($tempRoot) -and (Test-Path -LiteralPath $fullTemporary)) {
        Remove-Item -LiteralPath $fullTemporary -Recurse -Force
    }
}
