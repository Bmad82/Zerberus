@echo off
title Zerberus Pro 4.0

echo ============================================================
echo   ZERBERUS PRO 4.0 - Server Start
echo ============================================================
echo.

echo Pruefe Port 5000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5000 ^| findstr LISTENING') do (
    echo Beende Prozess %%a auf Port 5000...
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 2 /nobreak >nul

cd /d C:\Users\chris\Python\Rosa\Nala_Rosa\Zerberus
call venv\Scripts\activate

echo Starte Server (HTTPS, Port 5000)...
echo.
uvicorn zerberus.main:app --host 0.0.0.0 --port 5000 --reload --ssl-keyfile="desktop-rmuhi55.tail79500e.ts.net.key" --ssl-certfile="desktop-rmuhi55.tail79500e.ts.net.crt"

echo.
echo Server beendet.
pause
