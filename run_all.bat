@echo off
setlocal

REM Ejecuta backend (Hypercorn) + frontend (Streamlit) en Windows
REM Uso: doble clic o desde CMD: run_all.bat

cd /d "%~dp0"

set API_HOST=127.0.0.1
set API_PORT=8000
set UI_PORT=8501

REM Si existe venv local, lo usa; si no, usa python global
set PYTHON_CMD=python
if exist ".venv\Scripts\python.exe" set PYTHON_CMD=.venv\Scripts\python.exe

echo [Digestor] Iniciando backend en http://%API_HOST%:%API_PORT% ...
start "Digestor Backend" cmd /k ""%PYTHON_CMD%" -m hypercorn app.main:app --reload --bind %API_HOST%:%API_PORT%"

timeout /t 2 /nobreak >nul

echo [Digestor] Iniciando frontend en http://localhost:%UI_PORT% ...
start "Digestor Frontend" cmd /k ""%PYTHON_CMD%" -m streamlit run upload_ui.py --server.port %UI_PORT% --server.headless true"

echo.
echo [Digestor] Listo.
echo Backend:  http://%API_HOST%:%API_PORT%/health
echo Frontend: http://localhost:%UI_PORT%
echo.
echo Para detenerlos, cierra las dos ventanas abiertas.

endlocal
