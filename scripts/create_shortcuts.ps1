# Puts "F1 Live" (dashboard) and "F1 Tracker" (tray widgets) shortcuts on your desktop, with the F1 icon.
# Run again after moving the project folder:
#   powershell -ExecutionPolicy Bypass -File scripts\create_shortcuts.ps1
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "No $pythonw - run the setup in README.md first." }
$desktop = [Environment]::GetFolderPath('Desktop')  # follows OneDrive desktop redirection
$shell = New-Object -ComObject WScript.Shell
$apps = @(
    @{ Name = 'F1 Live';    Script = 'run_live.pyw'; Description = 'F1 live dashboard: track map, timing, strategy sim' },
    @{ Name = 'F1 Tracker'; Script = 'run.pyw';      Description = 'F1 desktop widgets: standings, next race (tray icon)' }
)
foreach ($a in $apps) {
    $lnk = $shell.CreateShortcut((Join-Path $desktop "$($a.Name).lnk"))
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = '"' + (Join-Path $root $a.Script) + '"'
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = (Join-Path $root 'f1tracker\f1.ico') + ',0'
    $lnk.Description = $a.Description
    $lnk.Save()
    Write-Output "Created $(Join-Path $desktop "$($a.Name).lnk")"
}
