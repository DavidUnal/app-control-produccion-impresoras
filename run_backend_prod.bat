@echo off
title Control de Produccion - Backend (Produccion)

echo =============================================
echo   Iniciando Backend FastAPI en PRODUCCION
echo =============================================
echo.

rem Ir a la carpeta donde está este BAT
cd /d "%~dp0"

rem Ejecutar Uvicorn usando el Python del entorno virtual
".venv\Scripts\python.exe" -m uvicorn backend.server:app --host 0.0.0.0 --port 8000

pause
