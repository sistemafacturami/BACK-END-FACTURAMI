@echo off
chcp 65001 >nul
echo ═══════════════════════════════════════════════════════
echo   Instalador de Auto-Inicio — FACTURAMI
echo ═══════════════════════════════════════════════════════
echo.
echo Esto creará un acceso directo en la carpeta de Inicio
echo de Windows para que los servicios se inicien 
echo automáticamente al encender la computadora.
echo.
pause

:: Crear acceso directo en la carpeta de Inicio de Windows
set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set "SCRIPT=%ROOT_DIR%\iniciar_facturami.bat"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$wsh = New-Object -ComObject WScript.Shell; " ^
  "$lnk = $wsh.CreateShortcut('%STARTUP%\FACTURAMI Servicios.lnk'); " ^
  "$lnk.TargetPath = '%SCRIPT%'; " ^
  "$lnk.WorkingDirectory = '%ROOT_DIR%'; " ^
  "$lnk.WindowStyle = 7; " ^
  "$lnk.IconLocation = '%ROOT_DIR%\facturami.ico,0'; " ^
  "$lnk.Description = 'Inicia N8N y Backend Flask para FACTURAMI'; " ^
  "$lnk.Save()"

echo.
echo ═══════════════════════════════════════════════════════
echo   ✓ Acceso directo creado exitosamente en:
echo   %STARTUP%
echo.
echo   Los servicios se iniciarán automáticamente
echo   cada vez que inicies sesión en Windows.
echo.
echo   Para desactivar, elimina "FACTURAMI Servicios"
echo   de la carpeta de Inicio de Windows.
echo ═══════════════════════════════════════════════════════
echo.
pause
