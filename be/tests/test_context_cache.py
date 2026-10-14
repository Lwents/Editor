import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, AsyncMock

from app.core import settings
from app.services.ai.context import analyze_video_context
from app.services.subtitles.timing import SubtitleEvent


class ContextCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_incomplete_context_is_not_published(self):
        complete = AsyncMock(return_value=json.dumps({'items': [], 'memory_updates': {}}))
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(settings, 'storage_dir', str(root / 'storage')), \
                 patch.object(settings, 'translation_engine', 'gemini_web'), \
                 patch('app.services.ai.gemini_web.browser.complete', complete):
                with self.assertRaises(RuntimeError):
                    await analyze_video_context(None, [SubtitleEvent(0, 1, '你好')], root / 'one')
                self.assertEqual(list(root.rglob('manifest.json')), [])

    async def test_same_evidence_reuses_context_and_changed_source_does_not(self):
        reply = json.dumps({'items': [{'id': 1, 'text': 'Một người chào hỏi.'}],
                            'memory_updates': {'plot_summary': 'Một người chào hỏi.'}})
        complete = AsyncMock(return_value=reply)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(settings, 'storage_dir', str(root / 'storage')), \
                 patch.object(settings, 'translation_engine', 'gemini_web'), \
                 patch('app.services.ai.gemini_web.browser.complete', complete):
                events = [SubtitleEvent(0, 1, '你好')]
                first = await analyze_video_context(None, events, root / 'one')
                second = await analyze_video_context(None, events, root / 'two')
                self.assertEqual(first['plot_summary'], second['plot_summary'])
                self.assertEqual(complete.await_count, 1)
                await analyze_video_context(None, [SubtitleEvent(0, 1, '再见')], root / 'three')
                self.assertEqual(complete.await_count, 2)
                await analyze_video_context(None, events, root / 'four', verified_context={'changed': True})
                self.assertEqual(complete.await_count, 3)

    async def test_corrupt_cached_artifact_is_not_reused(self):
        reply = json.dumps({'items': [{'id': 1, 'text': 'Lời chào.'}],
                            'memory_updates': {'plot_summary': 'Lời chào.'}})
        complete = AsyncMock(return_value=reply)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(settings, 'storage_dir', str(root / 'storage')), \
                 patch.object(settings, 'translation_engine', 'gemini_web'), \
                 patch('app.services.ai.gemini_web.browser.complete', complete):
                events = [SubtitleEvent(0, 1, '你好')]
                await analyze_video_context(None, events, root / 'one')
                cached = next((root / 'storage').rglob('context.json'))
                cached.write_text('{}', encoding='utf-8')
                await analyze_video_context(None, events, root / 'two')
                self.assertEqual(complete.await_count, 2)
