import asyncio
from contextlib import asynccontextmanager
from weakref import WeakKeyDictionary

_tasks: dict[str, asyncio.Task] = {}
_slots = WeakKeyDictionary()
_cancel_reasons = {}


@asynccontextmanager
async def job_slot():
    """One heavy video pipeline per backend, so OCR jobs do not compete."""
    loop = asyncio.get_running_loop()
    gate = _slots.setdefault(loop, asyncio.Lock())
    async with gate:
        yield


def start(job_id, coroutine):
    existing = _tasks.get(job_id)
    if existing and not existing.done():
        coroutine.close()
        return existing
    task = asyncio.create_task(coroutine)
    register_task(job_id, task)
    return task


async def resume_pending():
    from app.services.store import job_store
    from app.services.processor import process_job
    from app.models.job import JobStatus
    job_store._mark_interrupted_jobs()
    for job in sorted(job_store.list(), key=lambda value:value.created_at):
        if job.status == JobStatus.queued:
            start(job.job_id, process_job(job.job_id))


def register_task(job_id: str, task: asyncio.Task) -> None:
    _tasks[job_id] = task


def unregister_task(job_id: str) -> None:
    _tasks.pop(job_id, None)
    _cancel_reasons.pop(job_id,None)


def cancel_task(job_id: str) -> bool:
    task = _tasks.get(job_id)
    if task and not task.done():
        _cancel_reasons[job_id]='user'
        task.cancel()
        return True
    return False


def cancel_all_tasks(reason='user') -> int:
    cancelled = 0
    for job_id,task in list(_tasks.items()):
        if task and not task.done():
            _cancel_reasons[job_id]=reason
            task.cancel()
            cancelled += 1
    return cancelled
