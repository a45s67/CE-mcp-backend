param(
    [string]$OutputRoot = "dist\ce-mcp-windows-x64"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$repoRoot = [IO.Path]::GetFullPath($repoRoot)
$buildRoot = Join-Path $repoRoot "build\nuitka"
$controllerBuildRoot = Join-Path $repoRoot "build\nuitka-controller"
$releaseRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot $OutputRoot))
$distRoot = Join-Path $buildRoot "server.dist"
$controllerDistRoot = Join-Path $controllerBuildRoot "controller.dist"
$zipPath = "$releaseRoot.zip"
$zipChecksumPath = "$zipPath.sha256"

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
Remove-OwnedPath $controllerBuildRoot
Remove-OwnedPath $releaseRoot
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
if (Test-Path -LiteralPath $zipChecksumPath) {
    Remove-Item -LiteralPath $zipChecksumPath -Force
}
New-Item -ItemType Directory -Force $buildRoot, $controllerBuildRoot | Out-Null

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
    & uv run --locked --group build python -m nuitka `
        --mode=standalone `
        --assume-yes-for-downloads `
        --output-dir=$controllerBuildRoot `
        --output-filename=ce-mcp-control.exe `
        --windows-console-mode=force `
        --remove-output `
        controller.py
    if ($LASTEXITCODE -ne 0) { throw "controller compilation failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath (Join-Path $distRoot "server.exe"))) {
    throw "Nuitka did not produce server.dist\server.exe"
}
if (-not (Test-Path -LiteralPath (Join-Path $controllerDistRoot "ce-mcp-control.exe"))) {
    throw "Nuitka did not produce controller.dist\ce-mcp-control.exe"
}

$autorunRoot = Join-Path $releaseRoot "autorun"
$mcpRoot = Join-Path $releaseRoot "mcp"
New-Item -ItemType Directory -Force $autorunRoot, $mcpRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $repoRoot "bridge\ce_mcp_bridge.lua") -Destination $autorunRoot
Copy-Item -Path (Join-Path $distRoot "*") -Destination $mcpRoot -Recurse
$controllerFiles = Get-ChildItem -LiteralPath $controllerDistRoot -File -Recurse
foreach ($source in $controllerFiles) {
    $relative = $source.FullName.Substring($controllerDistRoot.Length + 1)
    $destination = Join-Path $mcpRoot $relative
    if (Test-Path -LiteralPath $destination -PathType Leaf) {
        $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $source.FullName).Hash
        $destinationHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $destination).Hash
        if ($sourceHash -ne $destinationHash) {
            throw "controller runtime conflicts with server runtime: $relative"
        }
    } else {
        New-Item -ItemType Directory -Force (Split-Path -Parent $destination) | Out-Null
        Copy-Item -LiteralPath $source.FullName -Destination $destination
    }
}
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
$zipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLowerInvariant()
[IO.File]::WriteAllText(
    $zipChecksumPath,
    "$zipHash  $([IO.Path]::GetFileName($zipPath))`n",
    [Text.UTF8Encoding]::new($false)
)
Write-Output $releaseRoot
Write-Output $zipPath
Write-Output $zipChecksumPath
