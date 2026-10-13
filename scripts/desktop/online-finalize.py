"""Apply the verified code update after the online installer installs its runtime."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def install(archive: Path, digest: str, version: str, resources: Path):
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
            raise ValueError('Update checksum mismatch')
    spec = importlib.util.spec_from_file_location('editor_update', Path(__file__).with_name('apply-update.py'))
    updater = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(updater)
    stage = resources / ('.online-stage-' + str(time.time_ns()))
    backup = resources / ('.online-backup-' + str(time.time_ns()))
    stage.mkdir()
    try:
        updater.extract(archive, stage)
        package = json.loads((stage / 'desktop/package.json').read_text(encoding='utf-8'))
        if package['version'] != version:
            raise ValueError('Update version mismatch')
        updater.apply(stage, resources, backup)
        shutil.rmtree(backup, ignore_errors=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    # The base installer creates shortcuts before the new icon is available.
    # Update only this application's shortcuts in the current user's folders.
    env = os.environ.copy()
    env['LWENT_INSTALLED_EXE'] = str(resources.parent / 'Editor.exe')
    env['LWENT_INSTALLED_ICON'] = str(resources / 'app/assets/app-logo.ico')
    subprocess.run(['powershell.exe', '-NoProfile', '-Command',
        "$shell=New-Object -ComObject WScript.Shell; "
        "$folders=@([Environment]::GetFolderPath('Desktop'),(Join-Path ([Environment]::GetFolderPath('Programs')) 'Editor')); "
        "foreach($folder in $folders){$file=Join-Path $folder 'Lwent Editor.lnk'; "
        "if(Test-Path $file){$link=$shell.CreateShortcut($file); "
        "if($link.TargetPath -eq $env:LWENT_INSTALLED_EXE){$link.IconLocation=$env:LWENT_INSTALLED_ICON+',0';$link.Save()}}}"],
        env=env, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--zip', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--resources', type=Path, required=True)
    args = parser.parse_args()
    install(args.zip, args.sha256, args.version, args.resources)
