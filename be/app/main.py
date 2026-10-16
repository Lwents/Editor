import socket
# Force IPv4 to bypass broken local IPv6 network configurations on Windows
orig_getaddrinfo = socket.getaddrinfo
def patched_getaddrinfo(*args, **kwargs):
    responses = orig_getaddrinfo(*args, **kwargs)
    return [r for r in responses if r[0] == socket.AF_INET]
socket.getaddrinfo = patched_getaddrinfo

import sys
if sys.platform.startswith("win"):
    import io
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.api.ai_settings import router as ai_settings_router
from app.core import settings

import asyncio
import time
from contextlib import asynccontextmanager

async def cleanup_task():
    while True:
        try:
            now = time.time()
            
            from app.services.maintenance import cleanup_upload_temporaries
            from app.services.store import job_store
            cleanup_upload_temporaries(Path(settings.storage_dir), job_store.list(), now)
                            
        except Exception:
            pass
        await asyncio.sleep(3600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services import task_manager
    from app.services.runtime_lease import RuntimeLease
    lease=RuntimeLease(settings.storage_dir)
    lease.acquire()
    task=None
    import tempfile
    previous_temp=tempfile.tempdir
    temporary=Path(settings.storage_dir)/'tmp';temporary.mkdir(parents=True,exist_ok=True)
    tempfile.tempdir=str(temporary)
    try:
        await task_manager.resume_pending()
        task = asyncio.create_task(cleanup_task())
        yield
    finally:
        if task:
            task.cancel()
        running = [value for value in task_manager._tasks.values() if not value.done()]
        task_manager.cancel_all_tasks(reason='shutdown')
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        lease.release()
        tempfile.tempdir=previous_temp

app = FastAPI(
    title="Auto-Translate AI API",
    version="1.0.0",
    description="Backend for video dubbing, translation, subtitle, and review workflows.",
    lifespan=lifespan,
)


@app.middleware('http')
async def upload_space_guard(request,call_next):
    if request.method=='POST' and request.url.path in {'/api/uploads/video','/api/jobs/restore'}:
        length=request.headers.get('content-length')
        if length:
            try:
                size=int(length)
                if size<0:raise ValueError()
            except ValueError:
                return JSONResponse({'detail':'UPLOAD_LENGTH_INVALID: Kích thước tải lên không hợp lệ.'},status_code=400)
            import shutil
            if size*3+256*1024**2>shutil.disk_usage(settings.storage_dir).free:
                return JSONResponse({'detail':'UPLOAD_DISK_FULL: Không đủ chỗ cho tệp tạm, video nguồn và dữ liệu khôi phục. Giải phóng dung lượng trước khi tải.'},status_code=507)
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=r"https://.*\.trycloudflare\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
app.include_router(ai_settings_router, prefix="/api")

storage_videos = Path(settings.storage_dir) / "uploads" / "videos"
storage_videos.mkdir(parents=True, exist_ok=True)
app.mount("/api/uploads/video", StaticFiles(directory=str(storage_videos)), name="videos")

storage_watermarks = Path(settings.storage_dir)
app.mount("/api/uploads/watermark", StaticFiles(directory=str(storage_watermarks)), name="watermarks")


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}

frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
