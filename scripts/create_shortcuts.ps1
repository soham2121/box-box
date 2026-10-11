# Puts a "Box Box" shortcut on your desktop, with the F1 icon: starts the app (tray, widgets, live feed) and opens
# the live dashboard; double-clicked while the app runs, it just opens the dashboard.
# Run again after moving the project folder:
#   powershell -ExecutionPolicy Bypass -File scripts\create_shortcuts.ps1
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "No $pythonw - run the setup in README.md first." }
$desktop = [Environment]::GetFolderPath('Desktop')  # follows OneDrive desktop redirection
$shell = New-Object -ComObject WScript.Shell
foreach ($n in 'F1 Live', 'F1 Tracker') {  # the app's shortcuts under its old name
    $old = Join-Path $desktop "$n.lnk"
    if (Test-Path $old) { Remove-Item $old; Write-Output "Removed $old" }
}
$apps = @(
    @{ Name = 'Box Box'; Script = 'run.pyw'; Description = 'Box Box: F1 live dashboard, desktop widgets (tray icon)' }
)
foreach ($a in $apps) {
    $lnk = $shell.CreateShortcut((Join-Path $desktop "$($a.Name).lnk"))
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = '"' + (Join-Path $root $a.Script) + '"'
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = (Join-Path $root 'boxbox\f1.ico') + ',0'
    $lnk.Description = $a.Description
    $lnk.Save()
    Write-Output "Created $(Join-Path $desktop "$($a.Name).lnk")"
}
