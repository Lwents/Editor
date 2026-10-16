"""Only remove abandoned upload temporaries; project media is user-owned."""
import time
from pathlib import Path


def cleanup_upload_temporaries(storage: Path, jobs, now=None):
    now = time.time() if now is None else now
    protected = {Path(job.request.local_file_path).resolve() for job in jobs if job.request.local_file_path}
    removed = []
    directory = Path(storage) / 'uploads/videos'
    for path in directory.glob('*'):
        if (path.is_file() and path.suffix in {'.uploading', '.tmp'}
                and path.resolve() not in protected and now-path.stat().st_mtime > 86400):
            path.unlink()
            removed.append(path.name)
    return removed
