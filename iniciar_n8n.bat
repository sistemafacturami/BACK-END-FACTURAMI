@echo off
chcp 65001 >nul
title N8N FACTURAMI

set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"

:: 1. Redireccion total de temporales a la unidad E:
set "TEMP=%ROOT_DIR%\tmp"
set "TMP=%ROOT_DIR%\tmp"

:: 2. Redireccion de cache NPM
set "NPM_CONFIG_CACHE=%ROOT_DIR%\cache\npm"

:: 3. Directorios de datos y base de datos n8n sobre E:
set "N8N_USER_FOLDER=%ROOT_DIR%\.n8n"
set "DB_SQLITE_DATABASE=%ROOT_DIR%\.n8n\database.sqlite"
set "N8N_DEFAULT_BINARY_DATA_MODE=filesystem"
set "N8N_BINARY_DATA_STORAGE_PATH=%ROOT_DIR%\.n8n\binaryData"
set "N8N_PORT=5678"

:: 4. Politicas de purgado automatico para no saturar disco
set "EXECUTIONS_DATA_PRUNE=true"
set "EXECUTIONS_DATA_MAX_AGE=168"
set "EXECUTIONS_DATA_PRUNE_MAX_COUNT=3000"

:: 5. Node.js portable
if exist "%ROOT_DIR%\..\Node JS\node.exe" (
    set "PATH=%ROOT_DIR%\..\Node JS;%PATH%"
)

echo ========================================================
echo   Iniciando N8N (100%% en E:)
echo   Carpeta datos: %N8N_USER_FOLDER%
echo   Temporales:    %TEMP%
echo   Base de datos: %DB_SQLITE_DATABASE%
echo   Prune activo:  %EXECUTIONS_DATA_PRUNE% (max age: %EXECUTIONS_DATA_MAX_AGE%h)
echo ========================================================
echo.

where n8n >nul 2>nul
if not errorlevel 1 (
    n8n start --open
) else (
    npx n8n start --open
)

