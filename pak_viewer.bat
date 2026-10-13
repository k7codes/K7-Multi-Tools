@echo off
cd /d "%~dp0"
python pak_viewer.py
if errorlevel 1 pause
