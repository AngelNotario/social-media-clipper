@echo off
setlocal
cd /d "%~dp0"
title SOCIAL MEDIA CLIPPER

where ffmpeg >nul 2>nul || echo [AVISO] FFmpeg no esta en el PATH. La importacion de videos no funcionara.

if not exist "backend\.venv\Scripts\python.exe" (
  echo Creando entorno de Python ^(solo la primera vez^)...
  python -m venv backend\.venv || goto :error
  backend\.venv\Scripts\python -m pip install -q --upgrade pip
  backend\.venv\Scripts\python -m pip install -q -r backend\requirements.txt || goto :error
)

if not exist "frontend\dist\index.html" (
  echo Compilando la interfaz ^(solo la primera vez^)...
  pushd frontend
  call npm install --no-audit --no-fund || goto :error
  call npm run build || goto :error
  popd
)

echo.
echo   SOCIAL MEDIA CLIPPER  -^>  http://localhost:8000
echo   Cierra esta ventana o presiona Ctrl+C para detener la app.
echo.
start "" cmd /c "timeout /t 2 >nul & start http://localhost:8000"
cd backend
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
goto :eof

:error
echo.
echo Ocurrio un error. Revisa los mensajes de arriba.
pause
