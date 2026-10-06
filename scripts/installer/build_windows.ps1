<#
.SYNOPSIS
    Builds the Iya Ashabi Bukka POS Windows executable with PyInstaller.

.DESCRIPTION
    Produces dist\IyaAshabiBukkaPOS\IyaAshabiBukkaPOS.exe, a self-contained
    folder that runs on a Windows till with no Python installed.

    The build is a one-folder build: copy the whole dist\IyaAshabiBukkaPOS
    folder to the till. Business data is NOT in that folder - it lives in
    %PROGRAMDATA%\IyaAshabiBukkaPOS, so replacing the folder keeps the sales.

.EXAMPLE
    pwsh -File scripts\installer\build_windows.ps1
    pwsh -File scripts\installer\build_windows.ps1 -Zip
#>
[CmdletBinding()]
param(
    # Also produce dist\IyaAshabiBukkaPOS-<date>.zip ready to hand to the till.
    [switch]$Zip,
    # Skip the confirmation prompt before deleting previous build output.
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$SpecFile = Join-Path $RepoRoot "IyaAshabiBukkaPOS.spec"
$DistDir = Join-Path $RepoRoot "dist\IyaAshabiBukkaPOS"
$BuildDir = Join-Path $RepoRoot "build"

Write-Host "Iya Ashabi Bukka POS - Windows build" -ForegroundColor Cyan
Write-Host "  project : $RepoRoot"

if (-not (Test-Path $SpecFile)) {
    throw "Build definition not found: $SpecFile"
}

# --- Toolchain -------------------------------------------------------------
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) {
    throw "Python was not found on PATH. Install Python 3.10 or newer from python.org and tick 'Add python.exe to PATH'."
}
Write-Host "  python  : $($Python.Source)"

& python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed. Run: python -m pip install -r requirements.txt"
}

# --- Output folders --------------------------------------------------------
if (Test-Path $DistDir) {
    if (-not $Force) {
        $answer = Read-Host "Existing build in dist\IyaAshabiBukkaPOS will be replaced. Continue? (y/N)"
        if ($answer -notmatch '^[Yy]') {
            Write-Host "Build cancelled." -ForegroundColor Yellow
            exit 1
        }
    }
    Remove-Item $DistDir -Recurse -Force
    Write-Host "  removed : previous dist output"
}
if (Test-Path $BuildDir) { Remove-Item $BuildDir -Recurse -Force }

# --- Build -----------------------------------------------------------------
Push-Location $RepoRoot
try {
    Write-Host "`nRunning PyInstaller (this takes a few minutes)..." -ForegroundColor Cyan
    & python -m PyInstaller --clean --noconfirm $SpecFile
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }
}
finally {
    Pop-Location
}

# --- Verify the bundle is actually usable ----------------------------------
$Exe = Join-Path $DistDir "IyaAshabiBukkaPOS.exe"
$Required = @(
    $Exe,
    (Join-Path $DistDir "_internal\app\templates\setup\step.html"),
    (Join-Path $DistDir "_internal\app\static\css\pos.css"),
    (Join-Path $DistDir "_internal\app\static\js\pos-init.js")
)

$Missing = @($Required | Where-Object { -not (Test-Path $_) })
if ($Missing.Count -gt 0) {
    Write-Host "`nBuild finished but the bundle is incomplete:" -ForegroundColor Red
    $Missing | ForEach-Object { Write-Host "  missing: $_" -ForegroundColor Red }
    Write-Host "Packaged builds fail to render pages when templates/static are absent." -ForegroundColor Yellow
    exit 1
}

$SizeMb = [math]::Round(((Get-ChildItem $DistDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB), 1)
Write-Host "`nBuild OK" -ForegroundColor Green
Write-Host "  executable : $Exe"
Write-Host "  size       : $SizeMb MB"
Write-Host "  data folder: %PROGRAMDATA%\IyaAshabiBukkaPOS (created on first launch)"

if ($Zip) {
    $Stamp = Get-Date -Format "yyyy-MM-dd"
    $ZipPath = Join-Path $RepoRoot "dist\IyaAshabiBukkaPOS-$Stamp.zip"
    if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
    Compress-Archive -Path (Join-Path $DistDir "*") -DestinationPath $ZipPath
    Write-Host "  archive    : $ZipPath"
}

Write-Host "`nNext steps" -ForegroundColor Cyan
Write-Host "  1. Copy the dist\IyaAshabiBukkaPOS folder to the till (e.g. C:\IyaAshabiBukkaPOS)."
Write-Host "  2. Right-click IyaAshabiBukkaPOS.exe > Send to > Desktop (create shortcut)."
Write-Host "  3. Launch it once: the setup wizard opens in the browser for the owner account."
