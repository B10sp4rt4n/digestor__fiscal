@echo off
setlocal

REM Detiene procesos de Digestor (Hypercorn + Streamlit) en Windows
REM Uso: doble clic o desde CMD: stop_all.bat

echo [Digestor] Deteniendo procesos...

REM Mata procesos por imagen + comando para minimizar falsos positivos
for /f "tokens=2 delims=," %%P in ('tasklist /fo csv /nh ^| findstr /i "python.exe"') do (
    wmic process where "ProcessId=%%~P" get CommandLine /value 2>nul | findstr /i "hypercorn app.main:app" >nul
    if not errorlevel 1 taskkill /PID %%~P /F >nul 2>&1
)

for /f "tokens=2 delims=," %%P in ('tasklist /fo csv /nh ^| findstr /i "python.exe"') do (
    wmic process where "ProcessId=%%~P" get CommandLine /value 2>nul | findstr /i "streamlit run upload_ui.py" >nul
    if not errorlevel 1 taskkill /PID %%~P /F >nul 2>&1
)

echo [Digestor] Listo. Si habia procesos activos, ya fueron detenidos.

endlocal
