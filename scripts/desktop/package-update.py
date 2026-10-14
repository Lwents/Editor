"""Create the small Windows code update; models and Python stay installed."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_VERSION = '1.3.0'

def runtime_hash():
    files = ['requirements.txt', 'requirements-vieneu.txt', 'requirements-zerotts.txt']
    contract = '\n'.join(name + '\n' + (ROOT / 'be' / name).read_text(encoding='utf-8').replace('\r\n', '\n') for name in files)
    return hashlib.sha256(contract.encode()).hexdigest()

def write_runtime(payload):
    (payload / 'resources/runtime.json').write_text(json.dumps({'version': RUNTIME_VERSION, 'hash': runtime_hash()}), encoding='utf-8')

def package(payload, output, version):
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:\.\d+)?', version): raise ValueError('Invalid version')
    output.mkdir(parents=True, exist_ok=True)
    archive = output / 'Editor-Update-Windows-x64.zip'
    scopes = {'desktop': payload / 'resources/app', 'backend-app': payload / 'resources/bundle/backend/app', 'frontend': payload / 'resources/bundle/frontend'}
    for entry in ['frontend/apps/web/server.js', 'frontend/node_modules/next/package.json', 'backend-app/main.py', 'desktop/updates.cjs']:
        scope, relative = entry.split('/', 1)
        if not (scopes[scope] / relative).is_file(): raise ValueError('Missing entry point: ' + entry)
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as data:
        for scope, directory in scopes.items():
            if not directory.is_dir(): raise ValueError('Missing scope: ' + scope)
            for file in sorted(directory.rglob('*')):
                if not file.is_file(): continue
                relative = file.relative_to(directory)
                if scope == 'desktop' and 'tests' in relative.parts: continue
                if any(part.startswith('.env') or part in {'__pycache__', 'ai.env', 'Cookies', 'Login Data'} for part in relative.parts): continue
                if file.suffix in {'.pyc', '.log', '.map', '.ps1', '.iss', '.obj', '.lib', '.exp', '.pdb', '.cpp'} or file.name in {'package-release.py', 'package-update.py'}: continue
                if scope == 'desktop' and relative.as_posix() == 'package.json':
                    config = json.loads(file.read_text()); config['version'] = version
                    data.writestr(scope + '/package.json', json.dumps(config)); continue
                data.write(file, scope + '/' + relative.as_posix())
    with archive.open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    manifest = {'schema': 1, 'version': version, 'runtimeVersion': RUNTIME_VERSION, 'runtimeHash': runtime_hash(), 'sha256': digest, 'size': archive.stat().st_size}
    (output / 'update.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(f'Code update ready: {archive} ({archive.stat().st_size/1048576:.1f} MB)')

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('payload', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--version', default='1.3.0')
    parser.add_argument('--runtime-only', action='store_true')
    args=parser.parse_args()
    if args.runtime_only: write_runtime(args.payload)
    else: package(args.payload, args.output or ROOT/'release/update', args.version)
