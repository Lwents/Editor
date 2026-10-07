$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$be = Join-Path $root 'be'
$fe = Join-Path $root 'fe'
$logs = Join-Path $root 'integration-logs'
$python = Join-Path $be '.venv\Scripts\python.exe'
$next = Join-Path $fe 'apps\web\node_modules\next\dist\bin\next'
if (-not (Test-Path $python)) { throw 'Chua cai BE. Xem README.md de tao be/.venv va cai requirements.txt.' }
if (-not (Test-Path $next)) { throw 'Chua cai FE. Chay bun install trong thu muc fe.' }
if (-not (Test-Path (Join-Path $be '.env'))) { Copy-Item (Join-Path $be '.env.example') (Join-Path $be '.env'); throw 'Da tao be/.env. Hay dien API key 9router cua ban roi chay lai.' }
if (-not (Test-Path (Join-Path $fe 'apps\web\.env.local'))) { Copy-Item (Join-Path $fe 'apps\web\.env.example') (Join-Path $fe 'apps\web\.env.local') }
New-Item -ItemType Directory -Force $logs | Out-Null
function Has-Port([int]$Port) { return $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) }
if (Has-Port 8100) { throw 'Cong 8100 dang duoc su dung. Tat BE cu truoc khi chay ban moi.' }
if (Has-Port 5175) { throw 'Cong 5175 dang duoc su dung. Tat editor cu truoc khi chay ban moi.' }
$env:PYTHONUNBUFFERED = '1'
Start-Process $python -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8100' -WorkingDirectory $be -RedirectStandardOutput (Join-Path $logs 'be.out.log') -RedirectStandardError (Join-Path $logs 'be.err.log')
$env:RVP_BACKEND_URL = 'http://127.0.0.1:8100'
Start-Process (Get-Command node.exe).Source -ArgumentList ('"' + $next + '"'),'dev','--turbopack','--hostname','127.0.0.1','--port','5175' -WorkingDirectory (Join-Path $fe 'apps\web') -RedirectStandardOutput (Join-Path $logs 'fe.out.log') -RedirectStandardError (Join-Path $logs 'fe.err.log')
Write-Host 'FE: http://127.0.0.1:5175/projects | BE: http://127.0.0.1:8100/health'
