param(
    [string]$OutputRoot = "dist\ce-mcp-windows-x64"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$repoRoot = [IO.Path]::GetFullPath($repoRoot)
$buildRoot = Join-Path $repoRoot "build\nuitka"
$releaseRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot $OutputRoot))
$distRoot = Join-Path $buildRoot "server.dist"
$zipPath = "$releaseRoot.zip"

function Remove-OwnedPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($repoRoot + [IO.Path]::DirectorySeparatorChar)) {
        throw "refusing to remove path outside repository: $full"
    }
    if (Test-Path -LiteralPath $full) {
        Remove-Item -LiteralPath $full -Recurse -Force
    }
}

Remove-OwnedPath $buildRoot
Remove-OwnedPath $releaseRoot
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
New-Item -ItemType Directory -Force $buildRoot | Out-Null

Push-Location $repoRoot
try {
    & uv run --locked --group build python -m nuitka `
        --mode=standalone `
        --assume-yes-for-downloads `
        --output-dir=$buildRoot `
        --output-filename=server.exe `
        --include-package-data=ce_mcp `
        --windows-console-mode=force `
        --remove-output `
        server.py
    if ($LASTEXITCODE -ne 0) { throw "Nuitka compilation failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath (Join-Path $distRoot "server.exe"))) {
    throw "Nuitka did not produce server.dist\server.exe"
}

$autorunRoot = Join-Path $releaseRoot "autorun"
$mcpRoot = Join-Path $releaseRoot "mcp"
New-Item -ItemType Directory -Force $autorunRoot, $mcpRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $repoRoot "bridge\ce_mcp_bridge.lua") -Destination $autorunRoot
Copy-Item -Path (Join-Path $distRoot "*") -Destination $mcpRoot -Recurse
Copy-Item -LiteralPath (Join-Path $repoRoot "config.example.json") -Destination (Join-Path $mcpRoot "config.example.json")
Copy-Item -LiteralPath (Join-Path $repoRoot "README.md") -Destination $releaseRoot
Copy-Item -LiteralPath (Join-Path $repoRoot "CE_MCP_TOOLS.md") -Destination $releaseRoot
Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\install.ps1") -Destination $releaseRoot

$project = Get-Content -Raw (Join-Path $repoRoot "pyproject.toml")
$versionMatch = [regex]::Match($project, '(?m)^version\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) { throw "project version was not found" }
[IO.File]::WriteAllText((Join-Path $releaseRoot "VERSION"), $versionMatch.Groups[1].Value + "`n")

$checksumPath = Join-Path $releaseRoot "SHA256SUMS"
$lines = Get-ChildItem -LiteralPath $releaseRoot -File -Recurse |
    Where-Object { $_.FullName -ne $checksumPath } |
    Sort-Object FullName |
    ForEach-Object {
        if (-not $_.FullName.StartsWith($releaseRoot + [IO.Path]::DirectorySeparatorChar)) {
            throw "release file escaped release root: $($_.FullName)"
        }
        $relative = $_.FullName.Substring($releaseRoot.Length + 1).Replace('\', '/')
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
        "$hash  $relative"
    }
[IO.File]::WriteAllLines($checksumPath, $lines, [Text.UTF8Encoding]::new($false))
Compress-Archive -LiteralPath $releaseRoot -DestinationPath $zipPath -CompressionLevel Optimal
Write-Output $releaseRoot
Write-Output $zipPath
