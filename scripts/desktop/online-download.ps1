param(
  [Parameter(Mandatory=$true)][string]$Manifest,
  [Parameter(Mandatory=$true)][string]$Cache,
  [Parameter(Mandatory=$true)][string]$Aria,
  [Parameter(Mandatory=$true)][string]$CancelFile,
  [Parameter(Mandatory=$true)][string]$LogFile,
  [int]$ParentId = 0,
  [string]$StatusFile,
  [string]$ResultFile,
  [string]$PidFile
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$engine = $null
$mutex = $null
$ownsMutex = $false
$exitCode = 1
$utf8 = New-Object Text.UTF8Encoding($false)
function Output([string]$Line) {
  if ($StatusFile) { [IO.File]::WriteAllText($StatusFile, $Line, $utf8) }
  else { [Console]::WriteLine($Line) }
}
function Event([string]$Message) {
  Add-Content -LiteralPath $LogFile -Value "$(Get-Date -Format o) $Message" -Encoding UTF8
}
function Rpc([string]$Method, [object[]]$Arguments = @()) {
  $body = @{jsonrpc='2.0'; id='installer'; method="aria2.$Method"; params=@("token:$token") + $Arguments} | ConvertTo-Json -Depth 12 -Compress
  $reply = Invoke-RestMethod -Uri $endpoint -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 3
  if ($reply.error) { throw "RPC $Method : $($reply.error.message)" }
  return $reply.result
}
try {
  if ($PidFile) { [IO.File]::WriteAllText("$PidFile.new", [string]$PID); [IO.File]::Move("$PidFile.new", $PidFile) }
  Output 'STATE|Starting download manager'
  New-Item -ItemType Directory -Force $Cache, (Split-Path $LogFile) | Out-Null
  $mutex = New-Object Threading.Mutex($false, 'Local\LwentEditorInstallerDownload')
  try { $ownsMutex = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
  if (-not $ownsMutex) { throw 'Another Lwent Editor installer is downloading. Close it and retry.' }
  $jobs = Get-Content -LiteralPath $Manifest -Raw | ConvertFrom-Json
  $jobs = @($jobs)
  if ($jobs.Count -eq 0) { throw 'Empty download manifest' }
  foreach ($job in $jobs) {
    if ($job.name -notmatch '^[A-Za-z0-9._-]+$' -or $job.sha256 -notmatch '^[a-f0-9]{64}$' -or [long]$job.size -le 0) { throw 'Invalid download metadata' }
    $uri = [Uri]$job.url
    if ($uri.Scheme -ne 'https') { throw 'Download URL must use HTTPS' }
  }
  $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
  $listener.Start(); $port = $listener.LocalEndpoint.Port; $listener.Stop()
  $token = [Guid]::NewGuid().ToString('N')
  $endpoint = "http://127.0.0.1:$port/jsonrpc"
  # Configuration avoids placing the RPC credential in the command line.
  $config = Join-Path (Split-Path $Manifest) 'aria2-installer.conf'
  @(
    'enable-rpc=true', 'rpc-listen-all=false', "rpc-listen-port=$port", "rpc-secret=$token",
    'disable-ipv6=true', 'max-concurrent-downloads=2', 'split=4', 'max-connection-per-server=4',
    'min-split-size=8M', 'continue=true', 'always-resume=true', 'auto-file-renaming=false',
    'allow-overwrite=true', 'check-integrity=true', 'file-allocation=none', 'disk-cache=16M',
    'auto-save-interval=1', 'max-tries=5', 'retry-wait=3', 'connect-timeout=15', 'timeout=30',
    'check-certificate=true', 'enable-color=false', 'console-log-level=warn', 'summary-interval=0'
  ) | Set-Content -LiteralPath $config -Encoding ASCII
  $engine = Start-Process -FilePath $Aria -ArgumentList @("--conf-path=`"$config`"") -WindowStyle Hidden -PassThru -RedirectStandardOutput "$LogFile.stdout" -RedirectStandardError "$LogFile.stderr"
  $ready = $false
  for ($attempt=0; $attempt -lt 40; $attempt++) {
    if ($engine.HasExited) { throw "Download manager exited: $($engine.ExitCode). See $LogFile.stderr" }
    try { $null = Rpc 'getVersion'; $ready = $true; break } catch { Start-Sleep -Milliseconds 250 }
  }
  if (-not $ready) { throw 'Download manager did not start its local connection' }
  $gids = @()
  foreach ($job in $jobs) {
    $options = @{dir=$Cache; out=$job.name; checksum="sha-256=$($job.sha256)"}
    $gids += Rpc 'addUri' @(@([string]$job.url), $options)
  }
  Event 'Started: 2 concurrent files, 4 connections per file; SHA256 verification enabled'
  $total = [long]($jobs | Measure-Object -Property size -Sum).Sum
  while ($true) {
    if ((Test-Path -LiteralPath $CancelFile) -or ($ParentId -gt 0 -and -not (Get-Process -Id $ParentId -ErrorAction SilentlyContinue))) {
      Event 'Download stopped; partial files and aria2 control files retained'
      $exitCode = 2
      break
    }
    if ($engine.HasExited) { throw "Download manager unexpectedly exited: $($engine.ExitCode)" }
    $completed = [long]0; $speed = [long]0; $active = 0; $connections = 0; $finished = 0; $verifying = $false
    for ($i=0; $i -lt $gids.Count; $i++) {
      $status = Rpc 'tellStatus' @($gids[$i])
      if ($status.status -eq 'error' -or $status.status -eq 'removed') {
        if ([int]$status.errorCode -eq 32) { $exitCode = 20 }
        throw "File $($jobs[$i].name): aria2 error $($status.errorCode): $($status.errorMessage)"
      }
      $completed += [Math]::Min([long]$status.completedLength, [long]$jobs[$i].size)
      $speed += [long]$status.downloadSpeed
      $connections += [int]$status.connections
      if ($status.status -eq 'complete') { $finished++ }
      if ($status.status -eq 'active') { $active++; if ($status.verifiedLength -or $status.verifyIntegrityPending) { $verifying = $true } }
    }
    $percent = [Math]::Min(99, [int][Math]::Floor(100.0 * $completed / $total))
    $eta = '--'
    if ($speed -gt 0) { $eta = [string][Math]::Ceiling(($total - $completed) / [double]$speed) + ' s' }
    $rate = ([double]$speed / 1MB).ToString('F2', [Globalization.CultureInfo]::InvariantCulture)
    $amount = [string]::Format([Globalization.CultureInfo]::InvariantCulture, '{0:F1}/{1:F1} MiB', ([double]$completed / 1MB), ([double]$total / 1MB))
    $phase = 'download'; if ($verifying) { $phase = 'verify' }
    Output ("PROGRESS|$percent|$rate|$amount|$eta|$active|$connections|$phase")
    if ($finished -eq $jobs.Count) {
      Event 'All files complete; SHA256 checks passed'
      Output ("PROGRESS|100|0.00|$amount|0 s|0|0|complete")
      $exitCode = 0
      break
    }
    Start-Sleep -Milliseconds 700
  }
} catch {
  $message = $_.Exception.Message
  try { Event ($_ | Out-String) } catch {}
  Output ('FAIL|' + $message.Replace("`r", ' ').Replace("`n", ' '))
} finally {
  if ($engine -and -not $engine.HasExited) {
    try { $null = Rpc 'shutdown' } catch {}
    if (-not $engine.WaitForExit(10000)) { $engine.Kill(); $engine.WaitForExit() }
  }
  if ($ownsMutex) { $mutex.ReleaseMutex() }
  if ($mutex) { $mutex.Dispose() }
  if ($config -and (Test-Path -LiteralPath $config)) { Remove-Item -LiteralPath $config -Force }
  if ($ResultFile) { [IO.File]::WriteAllText("$ResultFile.new", [string]$exitCode); [IO.File]::Move("$ResultFile.new", $ResultFile) }
}
exit $exitCode
