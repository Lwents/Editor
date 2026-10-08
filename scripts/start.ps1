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
    $env:PYTHONUTF8 = '1'
    $useVieNeu = Select-String -Path (Join-Path $root 'be\.env') -Pattern '^\s*AUTO_TRANSLATE_VOICE_ENGINE\s*=\s*["'']?vieneu(?:[-_]tts)?["'']?\s*$' -Quiet
    if ($useVieNeu) {
        Assert-OwnedPort 20129
        $speechPython = Join-Path $root 'be\.venv-vieneu\Scripts\python.exe'
        if (-not (Test-Path $speechPython)) { throw 'Chua cai VieNeu. Chay scripts/install-vieneu.ps1 truoc.' }
        $env:VIENEU_PORT = '20129'
        if (-not (Listener 20129)) {
            $previousHfHome = $env:HF_HOME
            try {
                $env:HF_HOME = Join-Path $root 'be\models\vieneu'
                Start-Process $speechPython -ArgumentList '-m','uvicorn','app.services.ai.vieneu_server:app','--app-dir',('"' + (Join-Path $root 'be') + '"'),'--host','127.0.0.1','--port','20129' -WorkingDirectory (Join-Path $root 'be') -NoNewWindow -RedirectStandardOutput (Join-Path $runtime "vieneu-$stamp.out.log") -RedirectStandardError (Join-Path $runtime "vieneu-$stamp.err.log") | Out-Null
            } finally { $env:HF_HOME = $previousHfHome }
        }
    }
    $zeroPython = Join-Path $root 'be\.venv-zerotts\Scripts\python.exe'
    $useZeroTts = Test-Path $zeroPython
    $zeroRequired = Select-String -Path (Join-Path $root 'be\.env') -Pattern '^\s*AUTO_TRANSLATE_VOICE_ENGINE\s*=\s*["'']?zerotts["'']?\s*$' -Quiet
    if ($zeroRequired -and -not $useZeroTts) { throw 'Chua cai ZeroTTS. Chay scripts/install-zerotts.ps1 truoc.' }
    if ($useZeroTts) {
        Assert-OwnedPort 20130
        if (-not (Listener 20130)) {
            Start-Process $zeroPython -ArgumentList '-m','uvicorn','app.services.ai.zerotts_server:app','--app-dir',('"' + (Join-Path $root 'be') + '"'),'--host','127.0.0.1','--port','20130' -WorkingDirectory (Join-Path $root 'be') -NoNewWindow -RedirectStandardOutput (Join-Path $runtime "zerotts-$stamp.out.log") -RedirectStandardError (Join-Path $runtime "zerotts-$stamp.err.log") | Out-Null
        }
    }
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
            $speechReady = $true
            if ($useVieNeu) {
                $speechHealth = Invoke-RestMethod 'http://127.0.0.1:20129/health' -TimeoutSec 2
                $speechReady = $speechHealth.status -in @('ready', 'loading')
            }
            if ($useZeroTts) {
                $zeroHealth = Invoke-RestMethod 'http://127.0.0.1:20130/health' -TimeoutSec 2
                $speechReady = $speechReady -and ($zeroHealth.status -in @('ready', 'loading'))
            }
            if ($health.status -eq 'ok' -and $page.StatusCode -eq 200 -and $speechReady) { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw "Server chua san sang. Xem log tai $runtime" }
    Remove-Item (Join-Path $runtime 'launcher-error.txt') -ErrorAction SilentlyContinue
    Start-Process 'http://127.0.0.1:5175/projects'
} catch {
    $_.Exception.Message | Set-Content (Join-Path $runtime 'launcher-error.txt') -Encoding UTF8
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
