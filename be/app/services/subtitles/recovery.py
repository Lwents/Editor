"""Bounded timeout recovery using the same job's disk checkpoints."""
import asyncio

from app.services.ai.gemini_web import GeminiWebTimeout


async def recover_subtitle_timeout(operation, progress):
    for attempt in range(2):
        try:
            return await operation()
        except GeminiWebTimeout as exc:
            if attempt:
                raise GeminiWebTimeout(
                    'GEMINI_TIMEOUT: Gemini vẫn chưa trả kết quả sau một lần tự tiếp tục. '
                    'Đã giữ checkpoint; bấm Tiếp tục từ checkpoint để thử lại. ' + str(exc)
                ) from exc
            progress('Gemini hết thời gian chờ; tự tiếp tục từ checkpoint (1/1)', 0)
            await asyncio.sleep(3)


async def get_or_create_subtitles(*args, **kwargs):
    from app.services.subtitles.source import get_or_create_subtitles as create
    progress = kwargs.get('progress') or args[4]
    return await recover_subtitle_timeout(lambda: create(*args, **kwargs), progress)
