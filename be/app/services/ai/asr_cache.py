"""Cache successful transcripts by audio content and recognition settings."""
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
from importlib.metadata import version, PackageNotFoundError

from app.core import settings
from app.services.presets import get_processing_profile

_locks: dict[str, asyncio.Lock] = {}


def package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return 'unavailable'


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def cache_directory(audio: Path, language: str, mode) -> Path | None:
    if not audio.is_file():
        return None
    signature = {
        'version': 1, 'audio': digest(audio), 'language': language,
        'model': settings.whisper_model, 'device': settings.whisper_device,
        'compute_type': settings.whisper_compute_type,
        'beam': get_processing_profile(mode).whisper_beam_size,
        'vad': settings.whisper_vad_filter,
        'align': settings.whisper_align_words and str(mode) != 'fast',
        'engines': [package_version('faster-whisper'), package_version('stable-ts')],
    }
    key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    return Path(settings.storage_dir) / 'recognition-cache' / 'audio' / key


def restore_artifacts(directory: Path, names: dict[str, Path]) -> bool:
    try:
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        for name in names:
            if digest(directory / name) != manifest['files'][name]:
                return False
        for name, target in names.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            pending = target.with_suffix(target.suffix + '.cache-pending')
            shutil.copyfile(directory / name, pending)
            pending.replace(target)
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def save_artifacts(directory: Path, names: dict[str, Path]) -> None:
    try:
        if any(not path.is_file() or not path.stat().st_size for path in names.values()):
            return
        directory.mkdir(parents=True, exist_ok=True)
        files = {}
        for name, source in names.items():
            pending = directory / (name + '.pending')
            shutil.copyfile(source, pending)
            pending.replace(directory / name)
            files[name] = digest(directory / name)
        pending = directory / 'manifest.pending.json'
        pending.write_text(json.dumps({'files': files}), encoding='utf-8')
        pending.replace(directory / 'manifest.json')
    except (OSError, ValueError):
        # Cache failures must not discard a successfully generated transcript.
        return


def restore(directory: Path, output: Path) -> bool:
    return restore_artifacts(directory, {'raw.srt': output, 'words.srt': output.parent / 'subtitles.source.srt',
                                         'metadata.json': output.parent / 'asr-metadata.json'})


def save(directory: Path, output: Path) -> None:
    try:
        metadata = json.loads((output.parent / 'asr-metadata.json').read_text(encoding='utf-8'))
        # Do not pin a fallback transcript after a temporary native-DLL error.
        if metadata.get('engine') != 'faster-whisper':
            return
    except (OSError, ValueError):
        return
    save_artifacts(directory, {'raw.srt': output, 'words.srt': output.parent / 'subtitles.source.srt',
                              'metadata.json': output.parent / 'asr-metadata.json'})


def lock(directory: Path) -> asyncio.Lock:
    return _locks.setdefault(str(directory), asyncio.Lock())
