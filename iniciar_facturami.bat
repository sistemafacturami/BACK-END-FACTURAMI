@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title FACTURAMI Servicios

set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"

echo -------------------------------------------------------
echo   FACTURAMI -- Iniciando Servicios
echo   %date% %time%
echo   Carpeta: %ROOT_DIR%
echo -------------------------------------------------------
echo.

:: 1. Redireccion total de temporales y caches a la unidad E:
set "TEMP=%ROOT_DIR%\tmp"
set "TMP=%ROOT_DIR%\tmp"
set "NPM_CONFIG_CACHE=%ROOT_DIR%\cache\npm"
set "PIP_CACHE_DIR=%ROOT_DIR%\cache\pip"

:: 2. Configuracion N8N Portable 100% sobre E:
set "N8N_USER_FOLDER=%ROOT_DIR%\.n8n"
set "DB_SQLITE_DATABASE=%ROOT_DIR%\.n8n\database.sqlite"
set "N8N_DEFAULT_BINARY_DATA_MODE=filesystem"
set "N8N_BINARY_DATA_STORAGE_PATH=%ROOT_DIR%\.n8n\binaryData"
set "N8N_PORT=5678"

:: Politicas de purgado de n8n
set "EXECUTIONS_DATA_PRUNE=true"
set "EXECUTIONS_DATA_MAX_AGE=168"
set "EXECUTIONS_DATA_PRUNE_MAX_COUNT=3000"

:: Deteccion Node.js / n8n si no esta en PATH
if exist "%ROOT_DIR%\..\Node JS\node.exe" (
    set "PATH=%ROOT_DIR%\..\Node JS;!PATH!"
)

echo [1/3] Verificando N8N (puerto 5678)...
curl -s -o nul http://localhost:5678/healthz >nul 2>&1
if not errorlevel 1 (
    echo       OK - N8N ya esta activo en puerto 5678
) else (
    echo       Iniciando N8N en segundo plano...
    where n8n >nul 2>nul
    if not errorlevel 1 (
        start "N8N-Server" /MIN n8n start
    ) else (
        start "N8N-Server" /MIN npx n8n start
    )
    
    echo [2/3] Esperando a que N8N este listo...
    set N8N_READY=0
    for /L %%i in (1,1,20) do (
        if !N8N_READY! EQU 0 (
            ping -n 3 127.0.0.1 >nul
            curl -s -o nul -w "%%{http_code}" http://localhost:5678/healthz >nul 2>&1 && (
                set N8N_READY=1
                echo       OK - N8N listo
            ) || (
                echo       ... esperando [intento %%i de 20]
            )
        )
    )
)

:: 2. Deteccion inteligente de Python (Prioridad 1: Python Portable en la USB)
set "PYTHON_EXE="
if exist "%ROOT_DIR%\python\python.exe" (
    "%ROOT_DIR%\python\python.exe" --version >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=%ROOT_DIR%\python\python.exe"
)
if not defined PYTHON_EXE (
    if exist "%ROOT_DIR%\.venv\Scripts\python.exe" (
        "%ROOT_DIR%\.venv\Scripts\python.exe" --version >nul 2>nul
        if not errorlevel 1 set "PYTHON_EXE=%ROOT_DIR%\.venv\Scripts\python.exe"
    )
)
if not defined PYTHON_EXE (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=python"
)
if not defined PYTHON_EXE (
    if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
        set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    )
)
if not defined PYTHON_EXE (
    py -3.11 --version >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=py -3.11"
)
if not defined PYTHON_EXE (
    py --version >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=py"
)

echo.
echo [3/3] Verificando Backend Facturami (puerto 5001)...
curl -s -o nul http://localhost:5001/api/health >nul 2>&1
if not errorlevel 1 (
    echo       OK - Backend ya esta activo en puerto 5001
) else (
    if defined PYTHON_EXE (
        start "Backend-Facturami" /MIN cmd /c "cd /d "%ROOT_DIR%" && "!PYTHON_EXE!" "%ROOT_DIR%\app.py""
        echo       OK - Backend Flask lanzado con !PYTHON_EXE!
    ) else (
        echo       [ERROR] No se pudo encontrar un ejecutable de Python.
    )
)

ping -n 3 127.0.0.1 >nul

echo.
echo -------------------------------------------------------
echo   OK - Todos los servicios iniciados
echo.
echo   N8N:     http://localhost:5678
echo   Backend: http://localhost:5001
echo.
echo   Abriendo FACTURAMI en ventana de aplicacion...
echo -------------------------------------------------------

powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%ROOT_DIR%\abrir_facturami.ps1"

ping -n 3 127.0.0.1 >nul
exit

