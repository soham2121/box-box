@echo off
rem Double-click to start Lights Out (tray icon, no console window) and open the live dashboard.
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0run.pyw"
