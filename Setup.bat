@echo off
rem Double-click to set up Lights Out: installs Python if needed, the packages, the desktop shortcut. Run again to update.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
pause
