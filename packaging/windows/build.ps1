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
uv run pytest -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run --group build python packaging/build.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Installer ready in: $(Resolve-Path dist)" -ForegroundColor Green
Get-ChildItem dist\*-Setup.exe | ForEach-Object { $_.Name }
