from __future__ import annotations

from datetime import UTC, datetime
import json
import sqlite3
import time
import logging
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from uuid import uuid4

from app.core import settings
from app.models.job import DubbingRequest, JobProgress, JobStatus
from app.services.persistence import atomic_json, read_json, PersistenceError


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, JobProgress] = {}
        self._lock = Lock()
        self._index_file = Path(settings.storage_dir) / "jobs_index.json"
        self._database = self._index_file.with_suffix('.sqlite3')
        self._last_snapshot = 0.
        self._database.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as connection:
                connection.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
                check=connection.execute('PRAGMA quick_check').fetchone()
                if check and check[0]!='ok':
                    error=sqlite3.DatabaseError(str(check[0]))
                    error.sqlite_errorcode=sqlite3.SQLITE_CORRUPT
                    raise error
        except sqlite3.DatabaseError as exc:
            if getattr(exc,'sqlite_errorcode',None) not in {sqlite3.SQLITE_CORRUPT,sqlite3.SQLITE_NOTADB}:
                raise PersistenceError('JOB_DATABASE_UNAVAILABLE: Kiểm tra ổ đĩa/quyền ghi, dữ liệu được giữ nguyên.') from exc
            # A verified JSON snapshot can rebuild the index; original corrupt
            # files are quarantined rather than erased. Cue ledgers are separate.
            recovery=read_json(self._index_file,lambda value:isinstance(value,list))
            if recovery is None:
                raise PersistenceError('JOB_DATABASE_CORRUPT: Không có snapshot để khôi phục; đã giữ nguyên database.') from exc
            stamp=str(time.time_ns())
            for suffix in ('','-wal','-shm'):
                file=Path(str(self._database)+suffix)
                if file.exists():
                    file.replace(file.with_name(file.name+'.corrupt-'+stamp))
            with self._connect() as connection:
                connection.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self._load()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self._database, timeout=15)
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA synchronous=FULL')
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, request: DubbingRequest, *, job_id=None) -> JobProgress:
        now = datetime.now(UTC)
        job = JobProgress(
            job_id=job_id or str(uuid4()),
            status=JobStatus.queued,
            progress=0,
            stage="Đã nhận cấu hình",
            request=request,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[job.job_id] = job
            try:
                self._save_unlocked(job.job_id, force_snapshot=True)
            except Exception:
                self._jobs.pop(job.job_id, None)
                raise
        return job

    def get(self, job_id: str) -> JobProgress | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[JobProgress]:
        with self._lock:
            return sorted(
                self._jobs.values(),
                key=lambda item: item.completed_at or item.updated_at,
                reverse=True,
            )

    def clear(self) -> list[str]:
        with self._lock:
            if any(job.status in {JobStatus.processing, JobStatus.queued} for job in self._jobs.values()):
                raise RuntimeError('Cần dừng tác vụ đang chạy trước khi dọn dữ liệu.')
            warnings = self._trash_job_artifacts_unlocked()
            self._jobs.clear()
            self._save_unlocked(force_snapshot=True)
            return warnings

    def update(self, job_id: str, **changes: object) -> JobProgress:
        with self._lock:
            job = self._jobs[job_id]
            data = job.model_dump()
            if "stage" in changes and changes["stage"] != job.stage and "stage_percent" not in changes:
                changes["stage_percent"] = None
            data.update(changes)
            now = datetime.now(UTC)
            if any(data.get(key) != getattr(job, key) for key in ("stage", "progress", "stage_percent", "status")):
                data["progress_changed_at"] = now
            data["updated_at"] = now
            updated = JobProgress.model_validate(data)
            self._jobs[job_id] = updated
            try:
                self._save_unlocked(job_id, force_snapshot=updated.status != job.status)
            except Exception:
                self._jobs[job_id] = job
                raise
            return updated

    def _load(self) -> None:
        with self._connect() as connection:
            records = connection.execute('SELECT payload FROM jobs').fetchall()
        data = [json.loads(record[0]) for record in records] if records else read_json(self._index_file, lambda value:isinstance(value,list))
        if data is None:
            return
        loaded = {}
        for item in data:
            try:
                job = JobProgress.model_validate(
                    {
                        **item,
                        "completed_at": item.get("completed_at") or (
                            item.get("updated_at") if item.get("status") == JobStatus.completed.value else None
                        ),
                    }
                )
                loaded[job.job_id] = job
            except (ValueError, TypeError, KeyError):
                raise PersistenceError('JOB_DATA_INVALID: Có tác vụ không đọc được; giữ nguyên dữ liệu để khôi phục.')
        self._jobs = loaded
        if not records:
            self._save_unlocked(force_snapshot=True)

    def emergency_failure(self, job_id, message):
        """Keep the UI truthful even when the disk cannot accept an error row."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                self._jobs[job_id] = job.model_copy(update={'status':JobStatus.failed,
                    'stage':'Không lưu được tiến độ; cần kiểm tra ổ đĩa','error':message})

    def _mark_interrupted_jobs(self) -> None:
        changed = False
        for job_id, job in list(self._jobs.items()):
            if job.status not in {JobStatus.queued, JobStatus.processing}:
                continue
            data = job.model_dump()
            data.update(
                {
                    "status": JobStatus.queued,
                    "stage": "Đang chờ tự tiếp tục từ checkpoint sau gián đoạn",
                    "error": None,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._jobs[job_id] = JobProgress.model_validate(data)
            changed = True
        if changed:
            self._save_unlocked(force_snapshot=True)

    def _save_unlocked(self, changed_id=None, force_snapshot=False) -> None:
        self._index_file.parent.mkdir(parents=True, exist_ok=True)
        jobs = [self._jobs[changed_id]] if changed_id else list(self._jobs.values())
        with self._connect() as connection:
            if changed_id is None:
                connection.execute('DELETE FROM jobs')
            connection.executemany('INSERT OR REPLACE INTO jobs VALUES (?, ?)',
                [(job.job_id, job.model_dump_json()) for job in jobs])
        # SQLite is authoritative; JSON remains a recovery/compatibility export.
        # A failure after a committed transaction must not roll back RAM alone.
        try:
            if force_snapshot or time.monotonic() - self._last_snapshot >= 2:
                atomic_json(self._index_file, [job.model_dump(mode='json') for job in self._jobs.values()])
                self._last_snapshot = time.monotonic()
        except PersistenceError:
            logging.getLogger(__name__).exception('Job database committed; JSON snapshot could not be refreshed')

    def _trash_job_artifacts_unlocked(self) -> list[str]:
        paths = self._artifact_paths_unlocked()
        if not paths:
            return []
        try:
            from send2trash import send2trash
        except ImportError as exc:
            raise RuntimeError("Thiếu Send2Trash. Chạy pip install -r requirements.txt trong backend.") from exc

        warnings: list[str] = []
        for path in paths:
            try:
                send2trash(str(path))
            except Exception as exc:
                warnings.append(f"{path}: {exc}")
        return warnings[:10]

    def _artifact_paths_unlocked(self) -> list[Path]:
        storage_dir = Path(settings.storage_dir).resolve()
        jobs_dir = storage_dir / "jobs"
        paths: list[Path] = []

        if jobs_dir.exists():
            paths.extend(path.resolve() for path in jobs_dir.iterdir() if path.is_dir())

        for job in self._jobs.values():
            if not job.output_file_path:
                continue
            output_file = Path(job.output_file_path).resolve()
            if not output_file.exists():
                continue
            if _is_relative_to(output_file, jobs_dir):
                continue
            if _is_relative_to(output_file, storage_dir):
                paths.append(output_file)

        return _dedupe_existing_paths(paths)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _dedupe_existing_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path in seen or not path.exists():
            continue
        if any(_is_relative_to(path, parent) for parent in unique if parent.is_dir()):
            continue
        unique.append(path)
        seen.add(path)
    return unique


job_store = JobStore()
