"""Optional local CPU speech worker in .venv-zerotts; model files stay on disk."""
import asyncio
import io
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

log = logging.getLogger("uvicorn.error")
_engine = None
_engine_error = None
_lock = threading.Lock()
_model_dir = Path(__file__).resolve().parents[3] / "models" / "zerotts"


def load_engine():
    global _engine, _engine_error
    with _lock:
        if _engine is None:
            from zerotts import ZeroTTS
            _engine = ZeroTTS.from_pretrained(
                _model_dir, local_files_only=True,
                intra_op_num_threads=min(4, os.cpu_count() or 1),
            )
            _engine_error = None
            log.info("ZeroTTS CPU ready: %d voices", len(_engine.list_voices()))
    return _engine


@asynccontextmanager
async def lifespan(app):
    def initialize():
        global _engine_error
        try:
            load_engine()
        except Exception as exc:
            _engine_error = type(exc).__name__
            log.exception("ZeroTTS initialization failed")
    task = asyncio.create_task(asyncio.to_thread(initialize))
    yield
    await task


app = FastAPI(title="Local ZeroTTS Speech", lifespan=lifespan)


class SpeechRequest(BaseModel):
    input: str = Field(min_length=1, max_length=10000)
    voice: str = Field(default="maichi", max_length=100)
    response_format: str = "wav"


@app.get("/health")
def health():
    return {"status": "ready" if _engine else "failed" if _engine_error else "loading",
            "backend": "cpu", "error": _engine_error}


@app.post("/v1/audio/speech")
async def speech(request: SpeechRequest):
    if request.response_format != "wav" or not request.input.strip():
        raise HTTPException(400, "WAV output and nonempty speech text are required")

    def generate():
        from zerotts import normalize_vi_text
        from zerotts.chunking import chunk_text, clean_segment_punctuation
        import numpy as np
        engine = load_engine()
        if request.voice not in engine.list_voices():
            raise HTTPException(422, "Unknown ZeroTTS voice")
        with _lock:
            started = time.perf_counter()
            segments = chunk_text(normalize_vi_text(request.input), max_chunk_sec=15)
            parts = []
            for segment in segments:
                text = clean_segment_punctuation(segment)
                if text.strip():
                    audio = engine.synthesize(text, voice=request.voice).reshape(-1)
                    if audio.size:
                        parts.append(audio)
            if not parts:
                raise ValueError("No speech samples generated")
            # Shared outer timeline adjusts duration; here retain each generated sentence.
            gap = np.zeros(int(engine.sample_rate * 0.12), dtype=np.float32)
            audio = np.concatenate([piece for i, part in enumerate(parts) for piece in ((gap, part) if i else (part,))])
            buffer = io.BytesIO()
            sf.write(buffer, audio, engine.sample_rate, format="WAV", subtype="PCM_16")
            log.info("ZeroTTS %s: %.2fs audio in %.2fs", request.voice, len(audio) / engine.sample_rate, time.perf_counter() - started)
            return buffer.getvalue()
    try:
        audio = await asyncio.to_thread(generate)
    except HTTPException:
        raise
    except Exception:
        log.exception("ZeroTTS speech generation failed")
        raise HTTPException(503, "Local ZeroTTS generation failed; see worker log") from None
    return Response(audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})
