$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$runtime = Join-Path $root '.runtime'
$uv = Join-Path $runtime 'bin\uv.exe'
New-Item -ItemType Directory -Force $runtime | Out-Null
if (-not (Test-Path $uv)) {
    $archive = Join-Path $runtime 'uv-windows.zip'
    Invoke-WebRequest 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile $archive
    Expand-Archive $archive (Join-Path $runtime 'bin') -Force
}
$venv = Join-Path $root 'be\.venv-zerotts'
$python = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $python)) {
    & $uv venv $venv --python 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Khong tao duoc runtime Python 3.12.' }
}
& $uv pip install --python $python -r (Join-Path $root 'be\requirements-zerotts.txt')
if ($LASTEXITCODE -ne 0) { throw 'Khong cai duoc ZeroTTS.' }
# Resolve the revision pinned by the installed SDK, rather than mutable HF main.
$env:EDITOR_ZEROTTS_MODEL_DIR = Join-Path $root 'be\models\zerotts'
try {
    $downloadScript = Join-Path $runtime 'download-zerotts-model.py'
    @'
import os
from huggingface_hub import snapshot_download
from zerotts.hub import DEFAULT_REVISION, _ALLOW_PATTERNS
snapshot_download('zeroweight-ai/ZeroTTS', revision=DEFAULT_REVISION,
                  local_dir=os.environ['EDITOR_ZEROTTS_MODEL_DIR'], allow_patterns=_ALLOW_PATTERNS)
'@ | Set-Content $downloadScript -Encoding UTF8
    & $python $downloadScript
    if ($LASTEXITCODE -ne 0) { throw 'Khong tai duoc model ZeroTTS. Kiem tra Internet roi chay lai.' }
} finally { Remove-Item Env:\EDITOR_ZEROTTS_MODEL_DIR -ErrorAction SilentlyContinue }
Write-Host 'Da cai ZeroTTS. Mo lai Editor.exe va chon ZeroTTS trong Dich vu giong doc.'
