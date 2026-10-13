"""Compile a small online installer using verified runtime files and a code release."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = 'https://github.com/Lwents/Editor/releases/download/installer-runtime-1.3.0/'
ARIA_URL = 'https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip'
ARIA_SHA256 = '67d015301eef0b612191212d564c5bb0a14b5b9c4796b76454276a4d28d9b288'
RUNTIME_SIZES = {
    'Editor-Setup-Windows-x64.exe': 6567437,
    'Editor-Setup-Windows-x64-1.bin': 1993432064,
    'Editor-Setup-Windows-x64-2.bin': 2000000000,
    'Editor-Setup-Windows-x64-3.bin': 2000000000,
    'Editor-Setup-Windows-x64-4.bin': 582781617,
}

def download_manifest(runtime: Path, manifest: dict) -> list[dict]:
    jobs = []
    for line in (runtime / 'Editor-Setup-Windows-x64.sha256').read_text().splitlines():
        digest, name = line.split()
        jobs.append({'url': BASE_URL + name, 'name': name, 'sha256': digest, 'size': RUNTIME_SIZES[name]})
    version = manifest['version']
    jobs.append({'url': f'https://github.com/Lwents/Editor/releases/download/v{version}/Editor-Update-Windows-x64.zip',
                 'name': f'Editor-Update-{version}.zip', 'sha256': manifest['sha256'], 'size': manifest['size']})
    return jobs

def prepare_aria2() -> Path:
    archive = ROOT / '.runtime/aria2-download.zip'
    if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != ARIA_SHA256:
        with urllib.request.urlopen(ARIA_URL, timeout=60) as response:
            archive.write_bytes(response.read())
    if hashlib.sha256(archive.read_bytes()).hexdigest() != ARIA_SHA256:
        raise ValueError('aria2 archive checksum mismatch')
    tools = ROOT / '.runtime/aria2/aria2-1.37.0-win-64bit-build1'
    with zipfile.ZipFile(archive) as source:
        for name in ('aria2c.exe', 'COPYING', 'LICENSE.OpenSSL'):
            tools.mkdir(parents=True, exist_ok=True)
            (tools / name).write_bytes(source.read('aria2-1.37.0-win-64bit-build1/' + name))
    return tools


def generate(runtime: Path, manifest: dict, metadata_only: bool = False) -> str:
    version = manifest['version']
    digest = manifest['sha256']
    if not re.fullmatch(r'1\.3\.0\.\d+', version) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('Invalid update manifest')
    checksums = {}
    for line in (runtime / 'Editor-Setup-Windows-x64.sha256').read_text().splitlines():
        checksum, filename = line.split()
        if not re.fullmatch(r'[a-f0-9]{64}', checksum) or not re.fullmatch(r'Editor-Setup-Windows-x64(?:-[1-4]\.bin|\.exe)', filename):
            raise ValueError('Invalid runtime checksum list')
        if not metadata_only:
            with (runtime / filename).open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != checksum:
                    raise ValueError('Runtime file checksum mismatch: ' + filename)
        checksums[filename] = checksum
    if len(checksums) != 5:
        raise ValueError('Incomplete runtime')
    lines = [f'#define UpdateVersion "{version}"', f'#define UpdateHash "{digest}"',
             f'#define UpdateArchive "Editor-Update-{version}.zip"', '[Code]']
    lines.extend(['function RuntimeCacheComplete: Boolean;', 'begin', '  Result := True;'])
    for filename in checksums:
        lines.append(f"  Result := Result and FileExists(InstallerCache + '\\{filename}');")
    lines.append('end;')
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--compiler', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'release/online')
    parser.add_argument('--metadata-only', action='store_true', help='Use the checked-in hashes of the immutable runtime release (CI only)')
    args = parser.parse_args()
    include = ROOT / '.runtime/online-installer-data.iss'
    include.parent.mkdir(exist_ok=True)
    manifest = json.loads(args.manifest.read_text())
    include.write_text(generate(args.runtime, manifest, args.metadata_only), encoding='utf-8')
    downloads = include.with_name('installation-downloads.json')
    downloads.write_text(json.dumps(download_manifest(args.runtime, manifest)), encoding='utf-8')
    tools = prepare_aria2()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    version = json.loads(args.manifest.read_text())['version']
    subprocess.run([str(args.compiler), '/Qp', '/DUpdateVersion=' + version, '/DOutput=' + str(output), str(Path(__file__).with_name('uninstall-maintenance.iss'))], check=True)
    subprocess.run([str(args.compiler), '/Qp', '/DDataInclude=' + str(include), '/DDownloadManifest=' + str(downloads), '/DDownloadTools=' + str(tools), '/DOutput=' + str(output), str(Path(__file__).with_name('online-installer.iss'))], check=True)
    print('Online installer: ' + str(output / 'Lwent-Editor-Online-Setup.exe'))
