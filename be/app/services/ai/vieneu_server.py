"""Optional local speech worker: .venv-vieneu/Scripts/python -m app.services.ai.vieneu_server."""
import asyncio
import io
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

log = logging.getLogger("uvicorn.error")
_engine = None
_engine_error = None
_lock = threading.Lock()


def load_engine():
    global _engine, _engine_error
    with _lock:
        if _engine is None:
            from vieneu import Vieneu
            _engine = Vieneu(
                mode="v3turbo", backend=os.environ.get("VIENEU_BACKEND", "auto"),
                device=os.environ.get("VIENEU_DEVICE", "auto"),
                max_batch_size=1, max_streams=1,
            )
            _engine_error = None
            log.info("VieNeu ready: backend=%s, sample_rate=%s", _engine.backend, _engine.sample_rate)
    return _engine


@asynccontextmanager
async def lifespan(app):
    def initialize():
        global _engine_error
        try:
            load_engine()
        except Exception as exc:
            _engine_error = type(exc).__name__
            log.exception("VieNeu model initialization failed")
    task = asyncio.create_task(asyncio.to_thread(initialize))
    yield
    await task


app = FastAPI(title="Local VieNeu Speech", lifespan=lifespan)


class SpeechRequest(BaseModel):
    input: str = Field(min_length=1, max_length=10000)
    voice: str = "Mai Anh"
    response_format: str = "wav"


@app.get("/health")
def health():
    return {"status": "ready" if _engine else "failed" if _engine_error else "loading",
            "backend": getattr(_engine, "backend", None), "error": _engine_error}


@app.get("/v1/voices")
def voices():
    engine = load_engine()
    return {"voices": [{"name": name, "id": voice_id} for name, voice_id in engine.list_preset_voices()]}


@app.post("/v1/audio/speech")
async def speech(request: SpeechRequest):
    if request.response_format != "wav":
        raise HTTPException(400, "Only WAV output is supported")
    if not request.input.strip():
        raise HTTPException(400, "Speech text is empty")

    def generate():
        engine = load_engine()
        with _lock:
            started = time.perf_counter()
            audio = engine.infer(request.input, voice=request.voice)
            buffer = io.BytesIO()
            sf.write(buffer, audio, engine.sample_rate, format="WAV", subtype="PCM_16")
            log.info("Generated %d chars, %.2fs audio in %.2fs", len(request.input), len(audio) / engine.sample_rate, time.perf_counter() - started)
            return buffer.getvalue()
    try:
        audio = await asyncio.to_thread(generate)
    except Exception:
        log.exception("VieNeu synthesis failed")
        raise HTTPException(503, "Local speech generation failed; see worker log") from None
    return Response(audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("VIENEU_PORT", "20129")))
