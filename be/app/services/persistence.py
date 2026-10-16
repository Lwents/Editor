"""Crash-safe local JSON snapshots. Never silently replace corrupt data with []."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class PersistenceError(RuntimeError):
    pass


def atomic_json(path: Path, value, *, backup=True):
    path = Path(path)
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if backup and path.exists():
            try:
                previous = json.loads(path.read_text(encoding='utf-8'))
            except (ValueError, UnicodeError):
                previous = None
            if previous is not None:
                atomic_json(path.with_suffix(path.suffix + '.bak'), previous, backup=False)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except OSError as exc:
        raise PersistenceError(f'DATA_WRITE_FAILED: Không lưu được {path.name}; kiểm tra dung lượng/quyền ghi. Dữ liệu cũ được giữ.') from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # A file lock must not hide the original persistence failure.
                pass


def read_json(path: Path, validate=lambda value: True):
    path = Path(path)
    found = False
    for candidate in (path, path.with_suffix(path.suffix + '.bak')):
        if not candidate.exists():
            continue
        found = True
        try:
            value = json.loads(candidate.read_text(encoding='utf-8'))
            if validate(value):
                return value
        except (OSError, ValueError, UnicodeError, TypeError, KeyError):
            pass
    if found:
        raise PersistenceError(f'DATA_RECOVERY_REQUIRED: {path.name} và bản sao lưu không đọc được. Đã giữ nguyên tệp; không tự tạo dữ liệu rỗng.')
    return None
