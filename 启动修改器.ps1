# Pokemon Mercury FC Trainer Launcher (no GDB needed)
# Usage: double-click 启动修改器.bat (which calls this script)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$mgbaDir   = Split-Path -Parent $scriptDir   # parent = mGBA root

# 1. Find ROM
$rom = Get-ChildItem -Path $mgbaDir -Filter '*.gba' | Select-Object -First 1
if (-not $rom) {
    Write-Host '[ERROR] No .gba ROM found.' -ForegroundColor Red
    Read-Host 'Press Enter to exit'
    exit 1
}

# 2. Find mGBA.exe
$mgba = Join-Path $mgbaDir 'mGBA.exe'
if (-not (Test-Path $mgba)) {
    Write-Host '[ERROR] mGBA.exe not found.' -ForegroundColor Red
    Read-Host 'Press Enter to exit'
    exit 1
}

# 3. Launch mGBA normally (no -g, no GDB). The bridge script is loaded inside mGBA.
Write-Host '[1/2] Launching mGBA...' -ForegroundColor Cyan
Start-Process -FilePath $mgba -ArgumentList "`"$($rom.FullName)`""
Start-Sleep -Seconds 3

# 4. Find and launch trainer exe
$trainer = Get-ChildItem -Path $scriptDir -Filter '*.exe' | Select-Object -First 1
if (-not $trainer) {
    Write-Host '[ERROR] Trainer exe not found.' -ForegroundColor Red
    Read-Host 'Press Enter to exit'
    exit 1
}

Write-Host '[2/2] Launching trainer...' -ForegroundColor Cyan
Start-Process -FilePath $trainer.FullName
Start-Sleep -Seconds 1

Write-Host ''
Write-Host 'Done!' -ForegroundColor Green
Write-Host 'IMPORTANT: in mGBA, load the bridge script once:' -ForegroundColor Yellow
Write-Host '  Tools -> Scripting -> File -> Load Script -> mercury_bridge.lua' -ForegroundColor Yellow
Write-Host 'Then click "Connect" in the trainer window.' -ForegroundColor Green
