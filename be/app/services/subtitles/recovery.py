"""Bounded timeout recovery using the same job's disk checkpoints."""
from app.services.ai.provider_status import provider_name, error_prefix
import asyncio

from app.services.ai.gemini_web import GeminiWebTimeout


async def recover_subtitle_timeout(operation, progress):
    from app.services.ai.translation import TranslationNeedsReview
    for attempt in range(2):
        try:
            return await operation()
        except GeminiWebTimeout as exc:
            if attempt:
                raise GeminiWebTimeout(
                    f'{error_prefix()}_TIMEOUT: {provider_name()} vẫn chưa trả kết quả sau một lần tự tiếp tục. '
                    'Đã giữ checkpoint; bấm Tiếp tục từ checkpoint để thử lại. ' + str(exc)
                ) from exc
            progress(f'{provider_name()} hết thời gian chờ; tự tiếp tục từ checkpoint (1/1)', 0)
            await asyncio.sleep(3)
        except TranslationNeedsReview:
            if attempt:
                raise
            progress('Đã giữ các nhóm hợp lệ; tự kiểm tra lại những câu còn thiếu (1/1)', 0)
            await asyncio.sleep(3)


async def get_or_create_subtitles(*args, **kwargs):
    from app.services.subtitles.source import get_or_create_subtitles as create
    from app.services.subtitles.mode import text_only_gemini, repair_failed_captions
    repair_token = repair_failed_captions.set(kwargs.pop('repair_failed_captions', False))
    token = text_only_gemini.set(kwargs.pop('text_only_gemini', False))
    progress = kwargs.get('progress') or args[4]
    try:
        return await recover_subtitle_timeout(lambda: create(*args, **kwargs), progress)
    finally:
        text_only_gemini.reset(token)
        repair_failed_captions.reset(repair_token)
