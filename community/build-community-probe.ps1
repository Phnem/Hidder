# PowerShell build script for Peripheral Community Research Probe
param (
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "Building PeripheralResearch.exe..." -ForegroundColor Cyan

python "$ScriptDir/build_exe.py"

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nBuild complete! Executable is located in community/dist/PeripheralResearch.exe" -ForegroundColor Green
} else {
    Write-Host "`nBuild failed with code $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}
