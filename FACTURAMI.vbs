' FACTURAMI - Ejecuta el lanzador sin mostrar ninguna ventana de consola
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & dir & "\abrir_facturami.ps1""", 0, False
