"""Apply the verified code update after the online installer installs its runtime."""
import argparse
import hashlib
import importlib.util
import json
import logging
import errno
import os
from pathlib import Path
import shutil
import subprocess
import time
import zipfile

logger = logging.getLogger('online-install')


def failure_code(error: Exception) -> int:
    if isinstance(error, PermissionError):
        return 24 if getattr(error, 'winerror', None) in {32, 33} else 22
    if isinstance(error, OSError):
        if error.errno == errno.ENOSPC or getattr(error, 'winerror', None) == 112:
            return 23
        if getattr(error, 'winerror', None) in {32, 33}:
            return 24
    if isinstance(error, (ValueError, zipfile.BadZipFile)):
        return 20 if str(error) == 'Update checksum mismatch' else 21
    if isinstance(error, subprocess.CalledProcessError):
        return 25
    return 29


def install(archive: Path, digest: str, version: str, resources: Path):
    logger.info('Checking update archive for version %s', version)
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
        logger.info('Extracting and validating update')
        updater.extract(archive, stage)
        package = json.loads((stage / 'desktop/package.json').read_text(encoding='utf-8'))
        if package['version'] != version:
            raise ValueError('Update version mismatch')
        updater.apply(stage, resources, backup)
        logger.info('Application code updated successfully')
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
    logger.info('Shortcuts updated; installation finalized')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--zip', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--log', type=Path)
    args = parser.parse_args()
    try:
        if args.log:
            args.log.parent.mkdir(parents=True, exist_ok=True)
            logging.basicConfig(filename=args.log, encoding='utf-8', level=logging.INFO,
                                format='%(asctime)s %(levelname)s %(message)s')
        install(args.zip, args.sha256, args.version, args.resources)
    except Exception as error:
        code = failure_code(error)
        logger.exception('Update failed: LW-UPDATE-%s', code)
        raise SystemExit(code)
