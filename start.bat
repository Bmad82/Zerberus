@echo off
cd /d C:\Users\chris\Python\Rosa\Nala_Rosa\Zerberus
call venv\Scripts\activate
uvicorn zerberus.main:app --host 0.0.0.0 --port 5000 --ssl-keyfile="desktop-rmuhi55.tail79500e.ts.net.key" --ssl-certfile="desktop-rmuhi55.tail79500e.ts.net.crt"
pause
