param([string]$BuildRoot=(Join-Path $env:LOCALAPPDATA 'EditorBuild'))
$ErrorActionPreference='Stop'
$Repo=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
New-Item -ItemType Directory -Force "$BuildRoot\tools"|Out-Null
function CopyTree($Source,$Destination){
 & robocopy.exe $Source $Destination /E /XD __pycache__ /NFL /NDL /NJH /NJS /NP|Out-Null
 if($LASTEXITCODE -ge 8){throw "Copy failed: $Source"}
}
# Build prerequisites only: the installed app never depends on them.
$Uv=Join-Path $Repo '.runtime\bin\uv.exe'
if(-not(Test-Path $Uv)){$Uv=(Get-Command uv.exe -ErrorAction Stop).Source}
$Venv=Join-Path $Repo 'be\.venv-vieneu'
$Base=(& "$Venv\Scripts\python.exe" -c 'import sys;print(sys.base_prefix)').Trim()
& "$Venv\Scripts\python.exe" "$Repo\scripts\desktop\build-native.py"
if($LASTEXITCODE -ne 0){throw 'Native frame kernel build failed'}
if(-not(Test-Path "$BuildRoot\python\python.exe")){
 CopyTree $Base "$BuildRoot\python"
 CopyTree "$Venv\Lib\site-packages" "$BuildRoot\python\Lib\site-packages"
}
$env:PYTHONNOUSERSITE='1';$env:PYTHONUTF8='1'
& $Uv pip install --break-system-packages --python "$BuildRoot\python\python.exe" -r "$Repo\be\requirements.txt" -r "$Repo\be\requirements-zerotts.txt"
if($LASTEXITCODE -ne 0){throw 'Python dependencies failed'}
& $Uv pip install --break-system-packages --python "$BuildRoot\python\python.exe" torchvision==0.23.0 gdown einops
if($LASTEXITCODE -ne 0){throw 'VietOCR dependencies failed'}
& $Uv pip install --break-system-packages --no-deps --python "$BuildRoot\python\python.exe" vietocr==0.3.13
if($LASTEXITCODE -ne 0){throw 'VietOCR installation failed'}
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
$Electron='electron-v43.4.0-win32-x64.zip'
$Inno='innosetup-7.1.0-x64.exe'
$Assets=@(
 @{File=$Electron;Url="https://github.com/electron/electron/releases/download/v43.4.0/$Electron";Hash='ef0709cfa719739acce73de6f9b684304baf38c6454376638a70d34a7cecffe0'},
 @{File=$Inno;Url="https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/$Inno";Hash='0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f'}
)
foreach($Asset in $Assets){
 $File=Join-Path "$BuildRoot\tools" $Asset.File
 if(-not(Test-Path $File)){& curl.exe -L --fail --retry 3 -o $File $Asset.Url;if($LASTEXITCODE -ne 0){throw 'Tool download failed'}}
 if((Get-FileHash $File -Algorithm SHA256).Hash.ToLower() -ne $Asset.Hash){throw "Checksum mismatch: $($Asset.File)"}
}
if(-not(Test-Path "$BuildRoot\electron\electron.exe")){Expand-Archive "$BuildRoot\tools\$Electron" "$BuildRoot\electron" -Force}
if(-not(Test-Path "$BuildRoot\tools\inno\ISCC.exe")){
 if((Get-AuthenticodeSignature "$BuildRoot\tools\$Inno").Status -ne 'Valid'){throw 'Installer signature invalid'}
 $Install=Start-Process "$BuildRoot\tools\$Inno" -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER',"/DIR=$BuildRoot\tools\inno" -Wait -PassThru
 if($Install.ExitCode -ne 0){throw 'Inno preparation failed'}
}
Write-Output 'Desktop prerequisites prepared'
