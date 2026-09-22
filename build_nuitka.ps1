[CmdletBinding()]
param(
    [string]$Python = "python",
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

if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    throw "Python interpreter '$Python' was not found in PATH. Activate the environment that has the dependencies, or pass -Python <path to python.exe>."
}

# The build only needs the packages installed in the chosen interpreter,
# so verify them here instead of requiring a specific environment manager.
$savedErrorPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $Python -c "import nuitka, PySide6, av" 2>$null
$importExitCode = $LASTEXITCODE
$ErrorActionPreference = $savedErrorPreference
if ($importExitCode -ne 0) {
    throw "The interpreter '$Python' cannot import nuitka, PySide6 and av. Install the dependencies from requirements.txt into it first, or pass -Python <path to python.exe>."
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
    "-m", "nuitka",
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
    "--python-flag=no_docstrings",
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
    Write-Host "  Python    : $(& $Python -c 'import sys; print(sys.executable)')"
    Write-Host "  Output dir: $resolvedOutputDir"
    Write-Host "  Cache dir : $resolvedCacheDir"

    & $Python @arguments
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
