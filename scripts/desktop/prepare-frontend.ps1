param([string]$BuildRoot=(Join-Path $env:LOCALAPPDATA 'EditorBuild'))
$ErrorActionPreference='Stop'
$Repo=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
& robocopy.exe "$Repo\fe" "$BuildRoot\fe" /E /XD node_modules .next .next-package target .git .cargo-tools /XF '.env*' '*.tsbuildinfo' /NFL /NDL /NJH /NJS /NP|Out-Null
if($LASTEXITCODE -ge 8){throw 'Frontend copy failed'}
$Bun=@(
 (Join-Path $env:USERPROFILE '.bun\bin\bun.exe'),
 (Join-Path $env:APPDATA 'npm\node_modules\bun\bin\bun.exe')
)|Where-Object {Test-Path $_}|Select-Object -First 1
if(-not $Bun){$Bun=(Get-Command bun.exe -ErrorAction Stop).Source}
Set-Location "$BuildRoot\fe"
& $Bun install --frozen-lockfile
if($LASTEXITCODE -ne 0){throw 'Frontend dependencies failed'}
$env:EDITOR_DESKTOP_BUILD='1';$env:NEXT_TELEMETRY_DISABLED='1';$env:NODE_ENV='production'
$env:NEXT_PUBLIC_SITE_URL='http://127.0.0.1:5176';$env:NEXT_PUBLIC_MARBLE_API_URL='https://api.marblecms.com'
$env:DATABASE_URL='postgres://desktop:desktop@127.0.0.1:5432/desktop'
$env:BETTER_AUTH_SECRET='desktop-build-placeholder-not-a-real-user-secret'
$env:UPSTASH_REDIS_REST_URL='https://desktop.invalid';$env:UPSTASH_REDIS_REST_TOKEN='desktop-placeholder'
$env:MARBLE_WORKSPACE_KEY='desktop-placeholder';$env:FREESOUND_CLIENT_ID='desktop-placeholder';$env:FREESOUND_API_KEY='desktop-placeholder'
$env:RVP_BACKEND_URL='http://127.0.0.1:8101'
Set-Location "$BuildRoot\fe\apps\web"
& node.exe .\node_modules\next\dist\bin\next build
if($LASTEXITCODE -ne 0){throw 'Frontend build failed'}

Write-Output 'Frontend build ready'
