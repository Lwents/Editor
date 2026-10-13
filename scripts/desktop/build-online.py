"""Compile a small online installer using verified runtime files and a code release."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = 'https://github.com/Lwents/Editor/releases/download/installer-runtime-1.3.0/'


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
    lines = [f'#define UpdateVersion "{version}"', f'#define UpdateHash "{digest}"', '[Code]', 'procedure AddInstallationDownloads(Page: TDownloadWizardPage);', 'begin']
    for filename, checksum in checksums.items():
        lines.append(f"  RestoreRuntimeFile('{filename}', '{checksum}');")
        lines.append(f"  Page.Add('{BASE_URL}{filename}', '{filename}', '{checksum}');")
    lines.append(f"  Page.Add('https://github.com/Lwents/Editor/releases/download/v{version}/Editor-Update-Windows-x64.zip', 'Editor-Update-Windows-x64.zip', '{digest}');")
    lines.append('end;')
    lines.extend(['procedure PreserveRuntimeDownloads;', 'begin'])
    for filename, checksum in checksums.items():
        lines.append(f"  PreserveRuntimeFile('{filename}', '{checksum}');")
    lines.append('end;')
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
    include.write_text(generate(args.runtime, json.loads(args.manifest.read_text()), args.metadata_only), encoding='utf-8')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    version = json.loads(args.manifest.read_text())['version']
    subprocess.run([str(args.compiler), '/Qp', '/DUpdateVersion=' + version, '/DOutput=' + str(output), str(Path(__file__).with_name('uninstall-maintenance.iss'))], check=True)
    subprocess.run([str(args.compiler), '/Qp', '/DDataInclude=' + str(include), '/DOutput=' + str(output), str(Path(__file__).with_name('online-installer.iss'))], check=True)
    print('Online installer: ' + str(output / 'Lwent-Editor-Online-Setup.exe'))
