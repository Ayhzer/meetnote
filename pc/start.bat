@echo off
cd /d "%~dp0"

:: Trouve Python 3.12 ou 3.11
set PYTHON=
py -3.12 --version >nul 2>&1 && set PYTHON=py -3.12
if "%PYTHON%"=="" py -3.11 --version >nul 2>&1 && set PYTHON=py -3.11
if "%PYTHON%"=="" python --version >nul 2>&1 && set PYTHON=python

if "%PYTHON%"=="" (
    echo Python introuvable. Installe Python 3.11 ou 3.12 depuis python.org
    pause
    exit /b 1
)

:: Installe les dépendances si pystray manque
%PYTHON% -c "import pystray" >nul 2>&1 || %PYTHON% -m pip install -r ..\requirements.txt -q

:: Lance l'application sans fenêtre console
start /B %PYTHON% meetnote-tray.py
