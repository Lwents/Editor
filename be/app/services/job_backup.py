"""Portable task backups, with hashes and strict extraction boundaries."""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from uuid import uuid4

from app.models.job import DubbingRequest

MANIFEST='backup-manifest.json'


def allowed(name):
    if name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
        return False
    return bool(re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,119}\.(json|srt|ass|sqlite3)',name)
                or re.fullmatch(r'source\.(mp4|mkv|mov|webm|avi|m4v)',name)) and name!=MANIFEST


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def create_backup(job,work:Path,output:Path):
    source=next((p for p in work.glob('source.*') if p.suffix.lower() in {'.mp4','.mkv','.mov','.webm','.avi','.m4v'}),None)
    if source is None:
        raise RuntimeError('BACKUP_SOURCE_MISSING: Chưa có video nguồn trong tác vụ.')
    files=[p for p in work.iterdir() if p.is_file() and allowed(p.name)]
    required=sum(p.stat().st_size for p in files)+256*1024**2
    if shutil.disk_usage(output.parent).free<required:
        raise RuntimeError('BACKUP_DISK_FULL: Không đủ dung lượng tạo bản sao kèm video nguồn.')
    with tempfile.TemporaryDirectory(dir=output.parent,prefix='backup-snapshot-') as temporary:
        entries={}
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
            for file in files:
                stamp=file.stat()
                snapshot=Path(temporary)/file.name
                if file.suffix=='.sqlite3':
                    original=sqlite3.connect(f'{file.as_uri()}?mode=ro',uri=True)
                    destination=sqlite3.connect(snapshot)
                    try:
                        original.backup(destination)
                    finally:
                        original.close();destination.close()
                elif file!=source:
                    shutil.copyfile(file,snapshot)
                else:
                    snapshot=file
                hasher=hashlib.sha256();size=0
                with snapshot.open('rb') as incoming,archive.open(file.name,'w',force_zip64=True) as outgoing:
                    while chunk:=incoming.read(1024**2):
                        outgoing.write(chunk);hasher.update(chunk);size+=len(chunk)
                if file==source and (file.stat().st_mtime_ns!=stamp.st_mtime_ns or size!=stamp.st_size):
                    raise RuntimeError('BACKUP_SOURCE_CHANGED: Video bị thay đổi khi sao lưu; chưa xuất bản sao không nhất quán.')
                entries[file.name]={'sha256':hasher.hexdigest(),'size':size,'mtime_ns':stamp.st_mtime_ns}
                if snapshot!=file:
                    snapshot.unlink()
            manifest={'version':1,'request':job.request.model_dump(mode='json'),'source':source.name,'files':entries}
            archive.writestr(MANIFEST,json.dumps(manifest,ensure_ascii=False))


def restore_backup(archive_path:Path,store,storage:Path):
    root=Path(storage)/'jobs';root.mkdir(parents=True,exist_ok=True)
    identifier=str(uuid4());final=root/identifier
    with zipfile.ZipFile(archive_path) as archive:
        names=archive.namelist()
        if len(names)>1000 or len({name.casefold() for name in names})!=len(names) or any(name!=MANIFEST and not allowed(name) for name in names):
            raise RuntimeError('BACKUP_UNSAFE: Tên tệp trùng/không hợp lệ; chưa khôi phục dữ liệu.')
        info=archive.getinfo(MANIFEST)
        if info.file_size>1024**2:
            raise RuntimeError('BACKUP_INVALID: Manifest quá lớn.')
        manifest=json.loads(archive.read(MANIFEST))
        files=manifest.get('files',{})
        if manifest.get('version')!=1 or not isinstance(files,dict) or set(files)!=(set(names)-{MANIFEST}):
            raise RuntimeError('BACKUP_INVALID: Danh sách tệp không khớp manifest.')
        source=manifest.get('source','')
        if not re.fullmatch(r'source\.(mp4|mkv|mov|webm|avi|m4v)',source) or source not in files:
            raise RuntimeError('BACKUP_INVALID: Thiếu video nguồn hợp lệ.')
        required=sum(archive.getinfo(name).file_size for name in files)+64*1024**2
        if required>shutil.disk_usage(root).free:
            raise RuntimeError('RESTORE_DISK_FULL: Không đủ dung lượng; chưa thay đổi tác vụ hiện có.')
        request=DubbingRequest.model_validate({**manifest['request'],'source_url':None,'local_file_path':str(final/source)})
        with tempfile.TemporaryDirectory(dir=root,prefix='.restore-') as temporary:
            staging=Path(temporary)
            for name,metadata in files.items():
                size=archive.getinfo(name).file_size
                limit=64*1024**3 if name==source else 512*1024**2 if name.endswith('.sqlite3') else 32*1024**2
                if size!=metadata.get('size') or size>limit:
                    raise RuntimeError('BACKUP_INVALID: Kích thước tệp không khớp/không an toàn.')
                destination=staging/name
                with archive.open(name) as incoming,destination.open('wb') as outgoing:
                    shutil.copyfileobj(incoming,outgoing,1024**2)
                if digest(destination)!=metadata.get('sha256'):
                    raise RuntimeError('BACKUP_HASH_MISMATCH: Bản sao bị thay đổi/hỏng; tác vụ cũ được giữ nguyên.')
                timestamp=metadata.get('mtime_ns')
                if type(timestamp) is int and timestamp>0:
                    os.utime(destination,ns=(timestamp,timestamp))
            staging.rename(final)
            try:
                job=store.create(request,job_id=identifier)
            except Exception:
                shutil.rmtree(final)
                raise
        return job
