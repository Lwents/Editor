param([string]$BuildRoot=(Join-Path $env:LOCALAPPDATA 'EditorBuild'))
$ErrorActionPreference='Stop'
$Repo=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Payload=Join-Path $BuildRoot 'app';$Bundle=Join-Path $Payload 'resources\bundle'
function CopyTree($Source,$Destination,$Exclude=@()){
 New-Item -ItemType Directory -Force $Destination|Out-Null
 & robocopy.exe $Source $Destination /E /XD __pycache__ .git .pytest_cache /XF '*.pyc' '*.log' '*.lib' '.env*' @Exclude /NFL /NDL /NJH /NJS /NP | Out-Null
 if($LASTEXITCODE -ge 8){throw "Copy failed: $Source"}
}
if(-not(Test-Path "$BuildRoot\electron\electron.exe")){throw 'Electron not prepared'}
if(-not(Test-Path "$BuildRoot\python\python.exe")){throw 'Portable Python not prepared'}
CopyTree "$BuildRoot\electron" $Payload
Copy-Item "$Payload\electron.exe" "$Payload\Editor.exe" -Force
Remove-Item "$Payload\electron.exe" -Force
CopyTree $PSScriptRoot "$Payload\resources\app"
& "$BuildRoot\python\python.exe" "$PSScriptRoot\package-update.py" $Payload --runtime-only
if($LASTEXITCODE -ne 0){throw 'Runtime manifest failed'}
foreach($Old in @('settings.html','settings.cjs','preload.cjs')) { Remove-Item (Join-Path "$Payload\resources\app" $Old) -Force -ErrorAction SilentlyContinue }
Get-ChildItem "$Payload\resources\app" -File |Where-Object {$_.Extension -in @('.ps1','.iss') -or $_.Name -in @('package-release.py','package-update.py')} |Remove-Item -Force
Copy-Item "$Repo\fe\LICENSE" "$Payload\OPENCUT-LICENSE.txt" -Force
CopyTree "$BuildRoot\python" "$Bundle\python"
# Torch ships the CUDA DLLs required by both Torch and CTranslate2. Avoid
# carrying a second set from NVIDIA wheels; ASR adds torch/lib to its DLL path.
$TorchLib=Join-Path $Bundle 'python\Lib\site-packages\torch\lib'
$Nvidia=Join-Path $Bundle 'python\Lib\site-packages\nvidia'
if(Test-Path $Nvidia){
 Get-ChildItem $Nvidia -Recurse -File -Filter '*.dll'|Where-Object {Test-Path (Join-Path $TorchLib $_.Name)}|Remove-Item -Force
}
CopyTree "$Repo\be\app" "$Bundle\backend\app"
& "$PSScriptRoot\stage-frontend.ps1" -BuildRoot $BuildRoot
CopyTree "$Repo\be\models\vieneu\hub" "$Bundle\models\vieneu\hub" @('token')
CopyTree "$env:USERPROFILE\.cache\huggingface\hub\models--adefossez--HTDemucs" "$Bundle\models\vieneu\hub\models--adefossez--HTDemucs"
CopyTree "$Repo\be\models\zerotts" "$Bundle\models\zerotts"
$Whisper=Get-ChildItem "$Repo\be\models\whisper\models--mobiuslabsgmbh--faster-whisper-large-v3-turbo\snapshots" -Directory|Select-Object -First 1
CopyTree $Whisper.FullName "$Bundle\models\whisper"
CopyTree "$Repo\be\models\ocr" "$Bundle\backend\models\ocr"
Copy-Item "$env:USERPROFILE\.cache\whisper\base.pt" "$Bundle\models\base.pt" -Force
New-Item -ItemType Directory -Force "$Bundle\tools"|Out-Null
Copy-Item "$Repo\be\.venv\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe" "$Bundle\tools\ffmpeg.exe" -Force
Copy-Item (Get-Command ffprobe.exe).Source "$Bundle\tools\ffprobe.exe" -Force
foreach($Name in @('msvcp140.dll','msvcp140_1.dll','msvcp140_2.dll','vcruntime140.dll','vcruntime140_1.dll','concrt140.dll')){
 $Dll=Join-Path $env:WINDIR "System32\$Name"
 if(Test-Path $Dll){Copy-Item $Dll "$Bundle\python\$Name" -Force}
}
Get-ChildItem "$Bundle\frontend" -Recurse -Force -File | Where-Object {$_.Name -like '.env*' -or $_.Name -like '*.map'} | Remove-Item -Force
@'
Lwent Editor 1.3 - Windows 10/11 x64
Open Editor.exe. Editing, subtitles and export run locally.
AI translation supports Gemini Web login or your own OpenAI-compatible API.
Gemini Web requires installed Google Chrome or Microsoft Edge and Internet.
Configure them using the AI settings icon in the editor toolbar.
The app checks GitHub Releases for updates; Editor > Check for updates checks manually.
Code updates keep your models, projects and AI settings.
VieNeu and ZeroTTS models are bundled. Edge voices require Internet.
Projects, settings and logs are stored in your Windows user profile.
GPU support depends on drivers; compatible CPU mode is selected automatically when needed.
Do not distribute personal settings or API keys with this application.

Includes Electron (MIT), Next.js (MIT), Python (PSF), PyTorch (BSD),
FFmpeg GPL builds, GL Transitions (MIT), Microsoft Fluent Emoji (MIT),
Google Fonts (OFL), VieNeu-TTS and ZeroTTS with upstream model licenses.
Upstream license files are retained in the bundled components.
FFmpeg source: https://github.com/FFmpeg/FFmpeg
imageio binary source/build: https://github.com/imageio/imageio-ffmpeg
ffprobe source/build: https://github.com/yt-dlp/FFmpeg-Builds
Electron: https://github.com/electron/electron/tree/v43.4.0
'@ |Set-Content "$Payload\README.txt" -Encoding UTF8
Write-Output "Payload ready: $Payload"
