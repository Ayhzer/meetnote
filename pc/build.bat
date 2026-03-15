@echo off
cd /d "%~dp0"
call h:\DEV\.venv\Scripts\python.exe build_spec.py
pause
