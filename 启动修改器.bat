@echo off
cd /d "%~dp0"
cd ..
for %%f in (*.gba) do set "ROM=%%f"
echo Starting mGBA in GDB mode...
start "" "mGBA.exe" -g "%ROM%"
echo Waiting for mGBA (3s)...
timeout /t 3 /nobreak >nul
echo Starting trainer...
for %%f in ("%~dp0*.exe") do start "" "%%f"
