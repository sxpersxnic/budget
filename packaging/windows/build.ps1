# Build the Windows installer (dist\Budget-<version>-Windows-Setup.exe) on this PC.
# Usage: double-click packaging\windows\build.bat, or run
#        powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")

function Require-Command($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Write-Host "$name is required to build the installer (end users do not need it)." -ForegroundColor Yellow
        Write-Host "Install it with:  $hint"
        exit 1
    }
}

Require-Command "uv" "winget install --id astral-sh.uv -e   (then open a new terminal)"

$iscc = @($env:ISCC,
          "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
        Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    Write-Host "Inno Setup 6 is required to create the installer." -ForegroundColor Yellow
    Write-Host "Install it with:  winget install --id JRSoftware.InnoSetup -e"
    exit 1
}
$env:ISCC = $iscc

uv sync --locked --group build --group dev
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# The tests get a fresh temp folder: Python 3.13 makes pytest's %TEMP%\pytest-of-<user> owner-only, so one left
# by an elevated shell (an admin terminal, an SSH session) refuses a normal one with "Access is denied".
$basetemp = Join-Path $env:TEMP "budget-build-tests-$PID"
try {
    uv run pytest -q --basetemp $basetemp
} finally {
    Remove-Item -Recurse -Force $basetemp -ErrorAction SilentlyContinue
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run --group build python packaging/build.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Installer ready (attach it and its .sha256 to the release):" -ForegroundColor Green
Get-ChildItem dist\*-Setup.exe, dist\*-Setup.exe.sha256 | ForEach-Object { $_.FullName }
