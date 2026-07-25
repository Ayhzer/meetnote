@echo off
setlocal

set "MN_DIR=%APPDATA%\MeetNote"
if not exist "%MN_DIR%" mkdir "%MN_DIR%" >nul 2>&1

:: Empêche le watchdog de relancer l'appli suite à ce kill volontaire
type nul > "%MN_DIR%\clean_exit.flag"

:: Tue précisément le watchdog et l'appli via leurs PID (évite de tuer
:: d'autres process python.exe/pythonw.exe sans rapport sur la machine)
if exist "%MN_DIR%\watchdog.pid" (
    set /p WPID=<"%MN_DIR%\watchdog.pid"
    if not "%WPID%"=="" taskkill /F /T /PID %WPID% >nul 2>&1
    del "%MN_DIR%\watchdog.pid" >nul 2>&1
)
if exist "%MN_DIR%\meetnote.pid" (
    set /p PID=<"%MN_DIR%\meetnote.pid"
    if not "%PID%"=="" taskkill /F /T /PID %PID% >nul 2>&1
)

:: Filet de sécurité si les fichiers PID sont absents ou périmés
taskkill /F /IM pythonw.exe /T >nul 2>&1

echo MeetNote arrêté.
