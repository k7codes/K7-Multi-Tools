@echo off
cd /d "%~dp0"
python db_viewer.py
if errorlevel 1 pause
