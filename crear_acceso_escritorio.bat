@echo off
setlocal
chcp 65001 >nul
title FACTURAMI - Crear Acceso Directo

set "ROOT_DIR=%~dp0"
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"

echo =======================================================
echo   FACTURAMI - Creando Acceso Directo en el Escritorio
echo   Ubicacion: %ROOT_DIR%
echo =======================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$wsh = New-Object -ComObject WScript.Shell; " ^
  "$desktop = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop); " ^
  "$psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'; " ^
  "$lnk = $wsh.CreateShortcut((Join-Path $desktop 'FACTURAMI.lnk')); " ^
  "$lnk.TargetPath = $psExe; " ^
  "$lnk.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""%ROOT_DIR%\abrir_facturami.ps1""'; " ^
  "$lnk.WorkingDirectory = '%ROOT_DIR%'; " ^
  "$lnk.IconLocation = '%ROOT_DIR%\facturami.ico,0'; " ^
  "$lnk.WindowStyle = 7; " ^
  "$lnk.Description = 'FACTURAMI - Sistema Contable'; " ^
  "$lnk.Save(); " ^
  "Write-Host 'Acceso directo en el Escritorio creado exitosamente.' -ForegroundColor Green"

echo.
echo Listo. Ya puedes abrir FACTURAMI directamente desde tu Escritorio.
ping -n 3 127.0.0.1 >nul
