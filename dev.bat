@echo off
REM Modo desarrollo: backend con recarga automatica (8000) + Vite con recarga en caliente (5173).
REM Ejecuta start.bat al menos una vez antes, para crear el entorno e instalar dependencias.
cd /d "%~dp0"
start "SMC backend" cmd /k "cd backend && .venv\Scripts\python -m uvicorn app.main:app --reload --port 8000"
start "SMC frontend" cmd /k "cd frontend && npm run dev"
timeout /t 3 >nul
start "" http://localhost:5173
