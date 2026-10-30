# ============================================================
#  FACTURAMI - Lanzador de escritorio
#  1. Inicia N8N (5678) y el backend Flask (5001) si no estan corriendo
#  2. Abre FACTURAMI.html en una ventana de aplicacion (sin barra del navegador)
#  Usa rutas relativas a esta carpeta, asi funciona aunque cambie la letra de la unidad.
# ============================================================
$ErrorActionPreference = 'SilentlyContinue'
$root = $PSScriptRoot
$html = Join-Path $root 'FACTURAMI.html'
$python = Join-Path $root 'python\python.exe'
if (-not (Test-Path $python)) {
    $python = Join-Path $root '.venv\Scripts\python.exe'
}
if (-not (Test-Path $python)) {
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pyCmd) {
        $python = $pyCmd.Source
    } elseif (Test-Path "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe") {
        $python = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    } elseif (Test-Path "C:\Python311\python.exe") {
        $python = "C:\Python311\python.exe"
    }
}
$app = Join-Path $root 'app.py'

function Test-Port([int]$port) {
    $c = New-Object Net.Sockets.TcpClient
    try { $c.Connect('127.0.0.1', $port); return $true } catch { return $false } finally { $c.Close() }
}

# --- Redireccion 100% de temporales y caches a E: ---
$env:TEMP = Join-Path $root 'tmp'
$env:TMP = Join-Path $root 'tmp'
$env:NPM_CONFIG_CACHE = Join-Path $root 'cache\npm'
$env:PIP_CACHE_DIR = Join-Path $root 'cache\pip'

# --- N8N ---
if (-not (Test-Port 5678)) {
    $env:N8N_USER_FOLDER = Join-Path $root '.n8n'
    $env:DB_SQLITE_DATABASE = Join-Path $root '.n8n\database.sqlite'
    $env:N8N_DEFAULT_BINARY_DATA_MODE = 'filesystem'
    $env:N8N_BINARY_DATA_STORAGE_PATH = Join-Path $root '.n8n\binaryData'
    $env:N8N_PORT = '5678'
    $env:EXECUTIONS_DATA_PRUNE = 'true'
    $env:EXECUTIONS_DATA_MAX_AGE = '168'
    $env:EXECUTIONS_DATA_PRUNE_MAX_COUNT = '3000'

    $nodeDir = Join-Path $root '..\Node JS'
    if (Test-Path $nodeDir) {
        $env:PATH = "$nodeDir;$env:PATH"
    }
    $n8n = Get-Command n8n -ErrorAction SilentlyContinue
    if ($n8n) {
        Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'n8n start' -WindowStyle Hidden
    } else {
        $npx = Get-Command npx -ErrorAction SilentlyContinue
        if ($npx) {
            Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'npx n8n start' -WindowStyle Hidden
        }
    }
}

# --- Backend Flask ---
if (-not (Test-Port 5001)) {
    if ((Test-Path $python) -and (Test-Path $app)) {
        Start-Process -FilePath $python -ArgumentList "`"$app`"" -WorkingDirectory $root -WindowStyle Hidden
    }
}

# --- Abrir la app en ventana independiente (Chrome, Edge o Brave) guardando perfil/cache en E: ---
$url = 'file:///' + ($html -replace '\\', '/')
$browserCacheDir = Join-Path $root 'cache\browser'

$candidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\BraveSoftware\Brave-Browser\Application\brave.exe"
)

$browser = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($browser) {
    Start-Process -FilePath $browser -ArgumentList "--app=`"$url`"", "--user-data-dir=`"$browserCacheDir`"", "--disk-cache-dir=`"$browserCacheDir`"", '--start-maximized'
} else {
    Start-Process $html
}

