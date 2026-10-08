$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$runtime = Join-Path $root '.runtime'
New-Item -ItemType Directory -Force $runtime | Out-Null
function Listener([int]$Port) {
    return Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
}
function Assert-OwnedPort([int]$Port) {
    $listener = Listener $Port
    if ($listener) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
        if (-not $process.CommandLine -or $process.CommandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            throw "Cong $Port dang duoc chuong trinh khac su dung. Khong tu dong tat chuong trinh do."
        }
    }
}
try {
    Assert-OwnedPort 8100
    Assert-OwnedPort 5175
    $python = Join-Path $root 'be\.venv\Scripts\python.exe'
    $next = Join-Path $root 'fe\apps\web\node_modules\next\dist\bin\next'
    if (-not (Test-Path $python)) { throw 'Chua cai Python runtime: tao be/.venv va cai be/requirements.txt.' }
    if (-not (Test-Path $next)) { throw 'Chua cai frontend. Chay bun install trong fe.' }
    if (-not (Test-Path (Join-Path $root 'be\.env'))) { throw 'Thieu be/.env. Sao chep .env.example va dien cau hinh rieng.' }
    $stamp = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
    $env:PYTHONUNBUFFERED = '1'
    if (-not (Listener 8100)) {
        Start-Process $python -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8100' -WorkingDirectory (Join-Path $root 'be') -NoNewWindow -RedirectStandardOutput (Join-Path $runtime "be-$stamp.out.log") -RedirectStandardError (Join-Path $runtime "be-$stamp.err.log") | Out-Null
    }
    $env:RVP_BACKEND_URL = 'http://127.0.0.1:8100'
    if (-not (Listener 5175)) {
        $node = (Get-Command node.exe -ErrorAction Stop).Source
        Start-Process $node -ArgumentList ('"' + $next + '"'),'dev','--turbopack','--hostname','127.0.0.1','--port','5175' -WorkingDirectory (Join-Path $root 'fe\apps\web') -NoNewWindow -RedirectStandardOutput (Join-Path $runtime "fe-$stamp.out.log") -RedirectStandardError (Join-Path $runtime "fe-$stamp.err.log") | Out-Null
    }
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $health = Invoke-RestMethod 'http://127.0.0.1:8100/health' -TimeoutSec 2
            $page = Invoke-WebRequest 'http://127.0.0.1:5175/projects' -UseBasicParsing -TimeoutSec 3
            if ($health.status -eq 'ok' -and $page.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw "Server chua san sang. Xem log tai $runtime" }
    Start-Process 'http://127.0.0.1:5175/projects'
} catch {
    $_.Exception.Message | Set-Content (Join-Path $runtime 'launcher-error.txt') -Encoding UTF8
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
