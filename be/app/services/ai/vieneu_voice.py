"""Local VieNeu speech client. The model runs in its own optional Python runtime."""
import asyncio
from pathlib import Path

import requests

from app.core import settings
from app.models.job import VoiceGender
from app.services.ai.voice import (
    VoiceEngine, VoiceError, _concat_wav_parts, _normalize_tts_text,
    _split_tts_chunks, _synthesize_srt_timeline,
)
from app.services.media.ffmpeg import find_ffmpeg, run_command


class VieNeuVoiceEngine(VoiceEngine):
    label = "VieNeu"
    parts_name = "vieneu_parts"

    @property
    def api_url(self):
        return settings.vieneu_api_url

    @property
    def timeout_seconds(self):
        return settings.vieneu_timeout_seconds

    def __init__(self, voice_name: str | None = None):
        self.voice_name = voice_name

    def get_voice(self, gender: VoiceGender) -> str:
        if self.voice_name:
            return self.voice_name
        return settings.vieneu_voice_female if gender == VoiceGender.female else settings.vieneu_voice_male

    async def _save_wav(self, text: str, output_file: Path, gender: VoiceGender) -> None:
        def request_audio() -> bytes:
            try:
                response = requests.post(
                    self.api_url.rstrip("/") + "/v1/audio/speech",
                    json={"input": text, "voice": self.get_voice(gender), "response_format": "wav"},
                    timeout=(10, self.timeout_seconds),
                )
                response.raise_for_status()
            except requests.RequestException as exc:
                raise VoiceError(f"Không tạo được giọng {self.label}. Kiểm tra dịch vụ local {self.api_url} và log .runtime.") from exc
            audio = response.content
            if len(audio) <= 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
                raise VoiceError(f"{self.label} không trả về file WAV hợp lệ.")
            return audio

        audio = await asyncio.to_thread(request_audio)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(audio)

    async def synthesize(self, text, output_file, voice_gender, progress=None, timing_file=None):
        normalized = _normalize_tts_text(text)
        if not normalized:
            raise VoiceError(f"Không có nội dung để tạo giọng {self.label}.")
        chunks = _split_tts_chunks(normalized, min(settings.tts_chunk_chars, 500))
        parts_dir = output_file.parent / self.parts_name
        parts_dir.mkdir(parents=True, exist_ok=True)
        parts = []
        for index, chunk in enumerate(chunks):
            if progress:
                progress(f"Tạo giọng {self.label} ({index + 1}/{len(chunks)})", 70 + int(index / len(chunks) * 8))
            part = parts_dir / f"{index:04d}.wav"
            await self._save_wav(chunk, part, voice_gender)
            parts.append(part)
        wav_output = output_file if output_file.suffix.lower() == ".wav" else parts_dir / "combined.wav"
        await _concat_wav_parts(parts, wav_output)
        if wav_output != output_file:
            ffmpeg = find_ffmpeg()
            if not ffmpeg:
                raise VoiceError(f"Không tìm thấy FFmpeg để chuyển đổi audio {self.label}.")
            await run_command([ffmpeg, "-y", "-i", str(wav_output), str(output_file)], f"Không chuyển đổi được giọng {self.label}.")
        # VieNeu provides audio, not word-level alignment; stale Edge timing must not be reused.
        if timing_file is not None:
            timing_file.unlink(missing_ok=True)
        return output_file

    async def synthesize_srt(self, subtitle_file, output_file, voice_gender, duration, progress=None):
        async def synthesize_event(text: str, destination: Path) -> None:
            await self._save_wav(text, destination, voice_gender)

        return await _synthesize_srt_timeline(
            subtitle_file, output_file, duration, progress, synthesize_event,
            concurrency=1, raw_suffix=".wav",
        )
