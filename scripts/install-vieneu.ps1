param([switch]$CpuOnly, [string]$WheelDirectory)
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
$venv = Join-Path $root 'be\.venv-vieneu'
$python = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $python)) {
    & $uv venv $venv --python 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Khong tao duoc runtime Python 3.12.' }
}
$torchIndex = if ($CpuOnly) { 'https://download.pytorch.org/whl/cpu' } else { 'https://download.pytorch.org/whl/cu128' }
& $uv pip install --python $python torch==2.8.0 torchaudio==2.8.0 --index-url $torchIndex
if ($LASTEXITCODE -ne 0) { throw 'Khong cai duoc PyTorch.' }
$packageArgs = @('pip', 'install', '--python', $python, '-r', (Join-Path $root 'be\requirements-vieneu.txt'))
if ($WheelDirectory) { $packageArgs += @('--no-index', '--find-links', $WheelDirectory) }
& $uv @packageArgs
if ($LASTEXITCODE -ne 0) { throw 'Khong cai duoc VieNeu.' }
Write-Host 'Da cai VieNeu. Dat AUTO_TRANSLATE_VOICE_ENGINE=vieneu trong be/.env roi mo Editor.exe.'
