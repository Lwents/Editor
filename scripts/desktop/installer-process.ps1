param([Parameter(Mandatory=$true)][string]$JobFile)
$ErrorActionPreference = 'Stop'
$job = Get-Content -LiteralPath $JobFile -Raw -Encoding UTF8 | ConvertFrom-Json
$encoding = New-Object System.Text.UTF8Encoding($false)
function Write-Atomic([string]$Path, [string]$Value) {
    [IO.File]::WriteAllText($Path + '.new', $Value, $encoding)
    [IO.File]::Move($Path + '.new', $Path)
}
Write-Atomic $job.pid ([string]$PID)
try {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $job.file
    $info.Arguments = $job.arguments
    $info.WorkingDirectory = $job.directory
    $info.UseShellExecute = $false
    $info.CreateNoWindow = -not $job.visible
    if (-not $job.visible) { $info.WindowStyle = 'Hidden' }
    $process = [Diagnostics.Process]::Start($info)
    $process.WaitForExit()
    Write-Atomic $job.result ('EXIT|' + $process.ExitCode)
} catch {
    Write-Atomic $job.result ('ERROR|' + $_.Exception.Message)
    exit 1
}
