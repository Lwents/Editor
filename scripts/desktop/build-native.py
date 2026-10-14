"""Build frame kernels without an OpenCV SDK or a new app runtime dependency."""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
source = root / 'be/app/native/frame_kernels.cpp'
output = source.with_suffix('.dll' if os.name == 'nt' else '.so')

if os.name == 'nt':
    vswhere = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    installation = subprocess.check_output([str(vswhere), '-latest', '-products', '*', '-requires',
        'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'], text=True).strip() if vswhere.is_file() else ''
    if installation:
        vcvars = Path(installation) / 'VC/Auxiliary/Build/vcvars64.bat'
        obj = output.with_suffix('.obj')
        command = f'call "{vcvars}" >nul && cl /nologo /O2 /LD /MT /EHsc "{source}" /Fo"{obj}" /link /OUT:"{output}"'
        # cmd has its own quoting rules. Passing this as a list would turn
        # the inner quotes into literal backslash-quotes on Windows.
        subprocess.run('cmd.exe /d /c ' + command, check=True)
    else:
        zig = importlib.util.find_spec('ziglang')
        compiler = Path(zig.origin).parent / 'zig.exe' if zig else root / 'be/.venv/Lib/site-packages/ziglang/zig.exe'
        if not compiler.is_file():
            raise RuntimeError('Install Visual C++ Build Tools or development-only ziglang to build frame kernels')
        subprocess.run([str(compiler), 'c++', '-O3', '-shared',
            '-fno-exceptions', '-fno-rtti', str(source), '-o', str(output)], check=True)
else:
    compiler = shutil.which('c++') or shutil.which('clang++')
    if not compiler:
        raise RuntimeError('A C++ compiler is required to build frame kernels')
    subprocess.run([compiler, '-O3', '-shared', '-fPIC', str(source), '-o', str(output)], check=True)
print('Built native frame kernels:', output)
