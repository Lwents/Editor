import asyncio
import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

from app.services.ai.gemini_web import GeminiWebRefusal, GeminiWebTimeout, GeminiWebVerificationRequired
from app.services.subtitles.recovery import recover_subtitle_timeout


class SubtitleRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_groups_are_recovered_automatically_once(self):
        from app.services.ai.translation import TranslationNeedsReview
        operation=AsyncMock(side_effect=[TranslationNeedsReview('remaining group'), 'completed'])
        with patch('app.services.subtitles.recovery.asyncio.sleep',AsyncMock()):
            self.assertEqual(await recover_subtitle_timeout(operation,Mock()),'completed')
        self.assertEqual(operation.await_count,2)

    async def test_unresolved_groups_do_not_retry_forever(self):
        from app.services.ai.translation import TranslationNeedsReview
        operation=AsyncMock(side_effect=TranslationNeedsReview('remaining group'))
        with patch('app.services.subtitles.recovery.asyncio.sleep',AsyncMock()):
            with self.assertRaises(TranslationNeedsReview):
                await recover_subtitle_timeout(operation,Mock())
        self.assertEqual(operation.await_count,2)

    async def test_fast_omits_final_review_and_deep_keeps_it(self):
        from app.core import settings
        from app.services.subtitles import source
        from app.services.subtitles.timing import SubtitleEvent
        from types import SimpleNamespace
        for mode in ('fast', 'quality'):
            with TemporaryDirectory() as directory:
                root = Path(directory)
                original = root / 'input.srt'
                original.write_text('1\n00:00:00,000 --> 00:00:01,000\n你好\n', encoding='utf-8')
                translated = [SubtitleEvent(0, 1, 'Xin chào')]
                engine = SimpleNamespace(translate_events=AsyncMock(return_value=translated))
                review = AsyncMock(return_value=translated)
                with patch.object(settings, 'translation_engine', 'gemini_web'), \
                     patch.object(settings, 'target_language', 'vi'), \
                     patch.object(source, 'analyze_video_context', AsyncMock(return_value={})), \
                     patch.object(source, 'get_translation_engine', return_value=engine), \
                     patch('app.services.ai.gemini_web.browser.close', AsyncMock()), \
                     patch('app.services.ai.translation_review.review_translated_events', review):
                    target = await source._prepare_target_subtitles(original, root, 'zh', Mock(), processing_mode=mode)
                    self.assertEqual(review.await_count, 0 if mode == 'fast' else 1)
                    marker = json.loads((root / 'translation_quality.json').read_text(encoding='utf-8'))
                    self.assertEqual(marker['review_completed'], mode != 'fast')
                    self.assertTrue(source._validated_target(target, root, None, mode))
                    self.assertFalse(source._validated_target(target, root, None, 'balanced'))

    async def test_timeout_resumes_and_preserves_completed_checkpoint(self):
        saved = ['đã dịch']
        calls = 0

        async def operation():
            nonlocal calls
            calls += 1
            self.assertEqual(saved, ['đã dịch'])
            if calls == 1:
                raise GeminiWebTimeout('120s')
            saved.append('câu tiếp theo')
            return saved

        progress = Mock()
        with patch('app.services.subtitles.recovery.asyncio.sleep', AsyncMock()):
            self.assertEqual(await recover_subtitle_timeout(operation, progress), ['đã dịch', 'câu tiếp theo'])
        self.assertEqual(calls, 2)
        progress.assert_called_once()

    async def test_second_timeout_stops_instead_of_looping(self):
        operation = AsyncMock(side_effect=GeminiWebTimeout('120s'))
        with patch('app.services.subtitles.recovery.asyncio.sleep', AsyncMock()):
            with self.assertRaisesRegex(GeminiWebTimeout, 'AI_TIMEOUT'):
                await recover_subtitle_timeout(operation, Mock())
        self.assertEqual(operation.await_count, 2)

    async def test_refusal_verification_and_cancel_are_not_replayed(self):
        for error in (GeminiWebRefusal('refusal'), GeminiWebVerificationRequired('login'), asyncio.CancelledError()):
            operation = AsyncMock(side_effect=error)
            with self.assertRaises(type(error)):
                await recover_subtitle_timeout(operation, Mock())
            operation.assert_awaited_once()
