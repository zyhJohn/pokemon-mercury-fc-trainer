param([string]$MgbaPath, [string]$RomPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$settingsPath = Join-Path $scriptDir 'trainer-settings.json'

function Select-LocalFile([string]$title, [string]$filter) {
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = $title
    $dialog.Filter = $filter
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        return $dialog.FileName
    }
    return $null
}

try {
    if (Test-Path -LiteralPath $settingsPath) {
        $settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $MgbaPath) { $MgbaPath = $settings.mgbaPath }
        if (-not $RomPath) { $RomPath = $settings.romPath }
    }
    $running = @(Get-Process -Name mGBA -ErrorAction SilentlyContinue)
    if (-not $MgbaPath -and $running.Count -eq 1) { $MgbaPath = $running[0].Path }
    if ($running.Count -eq 0) {
        if (-not $MgbaPath -or -not (Test-Path -LiteralPath $MgbaPath)) {
            $MgbaPath = Select-LocalFile 'Select mGBA.exe' 'mGBA|mGBA.exe|Executable|*.exe'
            if (-not $MgbaPath) { exit 0 }
        }
        if (-not $RomPath -or -not (Test-Path -LiteralPath $RomPath)) {
            $RomPath = Select-LocalFile 'Select your Mercury FC ROM' 'GBA ROM|*.gba'
            if (-not $RomPath) { exit 0 }
        }
        Start-Process -FilePath $MgbaPath -ArgumentList @("`"$RomPath`"")
    }
    @{mgbaPath=$MgbaPath;romPath=$RomPath} | ConvertTo-Json | Set-Content -LiteralPath $settingsPath -Encoding UTF8
    $exe = Join-Path $scriptDir 'MercuryTrainer.exe'
    if (-not (Test-Path -LiteralPath $exe)) { $exe = Join-Path $scriptDir 'dist\MercuryTrainer.exe' }
    if (Test-Path -LiteralPath $exe) {
        Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe)
    } else {
        $python = Get-Command pythonw.exe -ErrorAction SilentlyContinue
        if (-not $python) { throw 'Build dist/MercuryTrainer.exe or install Python with Tkinter first.' }
        $source = Join-Path $scriptDir 'trainer_gui.py'
        Start-Process -FilePath $python.Source -ArgumentList @("`"$source`"") -WorkingDirectory $scriptDir
    }
} catch {
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Mercury Trainer') | Out-Null
    exit 1
}
