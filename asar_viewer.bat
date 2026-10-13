@echo off
cd /d "%~dp0"
python asar_viewer.py
if errorlevel 1 pause
