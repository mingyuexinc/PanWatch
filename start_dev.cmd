@echo off
rem PanWatch local dev launcher: backend (FastAPI :8000) + frontend (Vite :5183)
rem Usage: double-click this file or run from a terminal. Two console windows
rem will open (one per server); close them to stop.
setlocal
set ROOT=%~dp0

start "PanWatch Backend :8000" cmd /k "cd /d %ROOT% && .venv\Scripts\python server.py"
start "PanWatch Frontend :5183" cmd /k "cd /d %ROOT%frontend && npx -y pnpm@9.15.9 dev"

rem wait for servers, then open the browser
timeout /t 8 /nobreak >nul
start http://127.0.0.1:5183

endlocal
