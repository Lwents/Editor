$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root 'be\.venv\Scripts\python.exe'
# Upstream metadata pins training dependencies and Pillow 10.2; this app only
# uses inference, with its existing modern Pillow and PyTorch environment.
& $python -m pip install torchvision gdown einops
if ($LASTEXITCODE -ne 0) { throw 'Khong cai duoc dependency VietOCR.' }
& $python -m pip install --no-deps vietocr==0.3.13
if ($LASTEXITCODE -ne 0) { throw 'Khong cai duoc VietOCR.' }
& $python -c "from vietocr.tool.predictor import Predictor; print('VietOCR ready')"
if ($LASTEXITCODE -ne 0) { throw 'VietOCR khong khoi tao duoc.' }
