# Lights Out setup (run by Setup.bat): finds or installs Python, installs the packages, asks live dashboard only or
# with desktop widgets, makes the desktop shortcut and starts the app. Run again to update: settings are kept.
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 [-Mode live|full]
param([ValidateSet('live', 'full', '')][string]$Mode = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py312 = '3.12.10'  # the last 3.12 with a python.org installer, for PCs without winget

function Version-Of($exe, $pre) {
    # "3.12" from an interpreter, or $null (missing, or the Microsoft Store stub that only opens the Store)
    try { $v = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null } catch { return $null }
    if ($LASTEXITCODE -eq 0 -and $v -match '^3\.\d+$') { return $v }
    return $null
}

function Find-Python {
    # Python 3.11-3.13: the packages (PySide6, numpy, scipy, faster-whisper) all have wheels for these
    $tries = @()
    foreach ($v in '3.13', '3.12', '3.11') { $tries += , @('py', @("-$v")) }
    $tries += , @('python', @())
    foreach ($v in '313', '312', '311') { $tries += , @("$env:LOCALAPPDATA\Programs\Python\Python$v\python.exe", @()) }
    foreach ($t in $tries) {
        $v = Version-Of $t[0] $t[1]
        if ($v -and [version]$v -ge [version]'3.11' -and [version]$v -le [version]'3.13') {
            $exe = & $t[0] @($t[1]) -c "import sys; print(sys.executable)"
            Write-Host "Using Python $v ($exe)"
            return $exe
        }
    }
    return $null
}

function Install-Python {
    Write-Host 'No Python 3.11-3.13 found: installing Python 3.12 for this Windows user (no admin needed)...'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    }
    if (-not (Find-Python)) {  # no winget, or it failed: python.org's installer
        $exe = Join-Path $env:TEMP "python-$py312-amd64.exe"
        Write-Host "Downloading Python $py312 from python.org..."
        Invoke-WebRequest "https://www.python.org/ftp/python/$py312/python-$py312-amd64.exe" -OutFile $exe -UseBasicParsing
        Start-Process $exe -Wait -ArgumentList '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_launcher=1', 'Include_test=0'
        Remove-Item $exe -ErrorAction SilentlyContinue
    }
}

Write-Host "`n== Lights Out setup ==`n"
# a running copy holds the packages' files open (pip can't replace them) and would save over the new setting
while (Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
       Where-Object { $_.CommandLine -like '*run.pyw*' -or $_.CommandLine -like '*lightsout.main*' }) {
    Read-Host 'Lights Out is running: quit it first (tray icon > Quit), then press Enter'
}
$venvPy = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Version-Of $venvPy @())) {
    $python = Find-Python
    if (-not $python) { Install-Python; $python = Find-Python }
    if (-not $python) { throw 'Python could not be installed. Install Python 3.12 from python.org, then run Setup.bat again.' }
    Write-Host 'Creating .venv...'
    & $python -m venv (Join-Path $root '.venv')
    if ($LASTEXITCODE) { throw 'Could not create .venv' }
}

Write-Host "`nInstalling packages (a few minutes the first time)..."
& $venvPy -m pip install --upgrade pip --disable-pip-version-check -q
& $venvPy -m pip install -r (Join-Path $root 'requirements.txt') --disable-pip-version-check
if ($LASTEXITCODE) { throw 'Package install failed (see above). Check the internet connection and run Setup.bat again.' }

if (-not $Mode) {
    Write-Host "`nWhat do you want?"
    Write-Host '  [1] Live dashboard only'
    Write-Host '  [2] Live dashboard + desktop widgets (standings, next race, map, timing on the desktop)'
    do { $pick = Read-Host 'Type 1 or 2' } until ($pick -in '1', '2')
    $Mode = @{ '1' = 'live'; '2' = 'full' }[$pick]
}
# through the app's own config code: it also moves an old install's settings folder (%APPDATA%\boxbox) first
$on = @{ live = 'False'; full = 'True' }[$Mode]
Push-Location $root
& $venvPy -c "import json; from lightsout import data, main; c = main.load_config(); c['desktop_widgets'] = $on; data.APP_DIR.mkdir(parents=True, exist_ok=True); main.CONFIG.write_text(json.dumps(c, indent=1))"
$ok = $LASTEXITCODE
Pop-Location
if ($ok) { throw 'Could not save the setting' }

Write-Host ''
& (Join-Path $PSScriptRoot 'create_shortcuts.ps1')

Write-Host "`nDone. Starting Lights Out: look for the red F1 icon in the tray (maybe under ^)."
Write-Host 'Sign in to F1 TV from the dashboard (or tray > Sign in to F1 TV) for the live map and timing.'
Write-Host 'Desktop widgets can be switched on or off later: tray > Desktop widgets.'
Start-Process (Join-Path $root '.venv\Scripts\pythonw.exe') -ArgumentList ('"' + (Join-Path $root 'run.pyw') + '"') -WorkingDirectory $root
