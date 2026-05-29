@echo off
cd /d "%~dp0"

:: Cherche le venv dans les emplacements connus (adapte automatiquement selon l'ordi)
set PYTHON=
for %%D in (C: D: H:) do (
    if exist "%%D\DEV\.venv\Scripts\python.exe" (
        set PYTHON=%%D\DEV\.venv\Scripts\python.exe
    )
)

:: Fallback : Python du PATH
if "%PYTHON%"=="" (
    where python >nul 2>&1 && set PYTHON=python
)

if "%PYTHON%"=="" (
    echo ERREUR : Python introuvable. Installez le venv dans C:\DEV\.venv ou ajoutez Python au PATH.
    pause
    exit /b 1
)

echo Utilisation de : %PYTHON%
call "%PYTHON%" build_spec.py
pause
