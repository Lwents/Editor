$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
foreach ($port in @(5175, 8100, 20129, 20130)) {
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { continue }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    if (-not $process.CommandLine -or $process.CommandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw "Cong $port khong thuoc du an nay; khong tat."
    }
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.ParentProcessId)" -ErrorAction SilentlyContinue
    $target = $process.ProcessId
    # Stop the Next CLI parent too, so it cannot respawn the listener.
    if ($port -eq 5175 -and $parent -and $parent.CommandLine -like '*next*dev*5175*' -and $parent.CommandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
        $target = $parent.ProcessId
    }
    & taskkill.exe /PID $target /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Khong dung duoc tien trinh $target." }
}
for ($attempt=0; $attempt -lt 20; $attempt++) {
    $remaining = Get-NetTCPConnection -LocalPort 5175,8100,20129,20130 -State Listen -ErrorAction SilentlyContinue
    if (-not $remaining) { exit 0 }
    Start-Sleep -Milliseconds 250
}
throw 'Cong chua duoc giai phong; khong khoi dong them server.'
