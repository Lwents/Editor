"""Apply only desktop/frontend/backend code, with a reversible directory swap."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import time
import zipfile

SCOPES = {'desktop': 'app', 'backend-app': 'bundle/backend/app', 'frontend': 'bundle/frontend'}

def extract(archive: Path, stage: Path):
    total = 0
    with zipfile.ZipFile(archive) as data:
        for info in data.infolist():
            name = info.filename
            parts = PurePosixPath(name).parts
            if not parts or parts[0] not in SCOPES or '\\' in name or ':' in name or '..' in parts or name.startswith('/') or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('Unsafe archive path')
            if any(p.startswith('.env') or p in {'ai.env', 'Cookies', 'Login Data', '__pycache__'} for p in parts):
                raise ValueError('Private data is not allowed in updates')
            total += info.file_size
            if total > 4 * 1024**3:
                raise ValueError('Update is too large')
            data.extract(info, stage)
    for scope in SCOPES:
        if not (stage / scope).is_dir():
            raise ValueError('Incomplete update')
    if not (stage / 'frontend/apps/web/server.js').is_file() or not (stage / 'backend-app/main.py').is_file():
        raise ValueError('Missing application entry points')

def apply(stage: Path, resources: Path, backup: Path):
    backup.mkdir()
    changes = []
    try:
        for scope, relative in SCOPES.items():
            target = resources / relative
            saved = backup / scope
            target.rename(saved)
            changes.append((target, saved))
            (stage / scope).rename(target)
    except Exception:
        for target, saved in reversed(changes):
            if target.exists(): shutil.rmtree(target)
            saved.rename(target)
        raise

def main():
    parser = argparse.ArgumentParser()
    for name in ['zip', 'sha256', 'version', 'resources', 'exe', 'pid', 'user-data']:
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    archive, resources = Path(args.zip), Path(args.resources)
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != args.sha256:
            raise ValueError('Checksum mismatch')
    # Extract on the installation volume so directory renames remain atomic.
    stage = resources / ('.update-stage-' + str(time.time_ns()))
    backup = resources / ('.update-backup-' + str(time.time_ns()))
    stage.mkdir()
    try:
        extract(archive, stage)
        package = json.loads((stage / 'desktop/package.json').read_text(encoding='utf-8'))
        if package['version'] != args.version:
            raise ValueError('Version mismatch')
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, int(args.pid))
        if handle:
            try:
                if ctypes.windll.kernel32.WaitForSingleObject(handle, 60000) != 0:
                    raise TimeoutError('Editor did not close')
            finally: ctypes.windll.kernel32.CloseHandle(handle)
        apply(stage, resources, backup)
        shutil.rmtree(backup, ignore_errors=True)
        print('Update installed:', args.version, flush=True)
    except Exception as error:
        ctypes.windll.user32.MessageBoxW(None, 'Chưa cập nhật được. App cũ được giữ lại.\n' + str(error), 'Lwent Editor', 0x10)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        env = os.environ.copy(); env['EDITOR_USER_DATA'] = args.user_data
        subprocess.Popen([args.exe], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

if __name__ == '__main__':
    main()
