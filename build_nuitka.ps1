[CmdletBinding()]
param(
    [string]$CondaEnv = "smallplayer",
    [string]$OutputDir = "dist\nuitka-fixed",
    [string]$CacheDir = ".nuitka-cache",
    [switch]$KeepBuildDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

$resolvedOutputDir = Join-Path $projectRoot $OutputDir
$resolvedCacheDir = Join-Path $projectRoot $CacheDir

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    throw "Conda is not available in PATH."
}

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "main.py"))) {
    throw "main.py was not found in the project root."
}

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "resources\icon.ico"))) {
    throw "resources\icon.ico was not found."
}

New-Item -ItemType Directory -Force -Path $resolvedCacheDir | Out-Null

if (Test-Path -LiteralPath $resolvedOutputDir) {
    # Remove the previous output so the standalone folder always matches
    # the current build inputs and does not keep stale DLLs around.
    Remove-Item -LiteralPath $resolvedOutputDir -Recurse -Force
}

$arguments = @(
    "run", "-n", $CondaEnv,
    "python", "-m", "nuitka",
    "--standalone",
    "--enable-plugin=pyside6",
    "--include-module=av.utils",
    "--windows-console-mode=disable",
    "--include-data-dir=resources=resources",
    "--windows-icon-from-ico=resources\icon.ico",
    "--product-name=LunaPlayer",
    "--company-name=LunaPlayer",
    "--file-description=LunaPlayer - 音乐播放器",
    "--file-version=1.0.0.0",
    "--product-version=1.0.0.0",
    "--output-filename=LunaPlayer.exe",
    "--output-dir=$OutputDir"
)

if (-not $KeepBuildDir) {
    $arguments += "--remove-output"
}

$arguments += "main.py"

$previousCacheDir = $env:NUITKA_CACHE_DIR
$env:NUITKA_CACHE_DIR = $resolvedCacheDir

try {
    Write-Host "Building LunaPlayer with Nuitka..."
    Write-Host "  Conda env : $CondaEnv"
    Write-Host "  Output dir: $resolvedOutputDir"
    Write-Host "  Cache dir : $resolvedCacheDir"

    & conda @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Nuitka build failed with exit code $LASTEXITCODE."
    }

    $distDir = Join-Path $resolvedOutputDir "main.dist"
    if (-not (Test-Path -LiteralPath $distDir)) {
        throw "Build finished without producing $distDir."
    }

    Write-Host ""
    Write-Host "Build completed successfully."
    Write-Host "Executable: $(Join-Path $distDir 'LunaPlayer.exe')"
}
finally {
    if ($null -eq $previousCacheDir) {
        Remove-Item Env:NUITKA_CACHE_DIR -ErrorAction SilentlyContinue
    }
    else {
        $env:NUITKA_CACHE_DIR = $previousCacheDir
    }
}
