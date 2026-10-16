import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from app.core import settings
from app.services.ai.caption_repair import repair_source, repair_failed_cues
from app.services.ai.caption_evidence import NON_CAPTION, filter_verified_non_captions
from app.services.subtitles.timing import SubtitleEvent
from app.services.ai.translation import _translate_gemini_batch, TranslationBatchStructureError


class NonCaptionTests(unittest.IsolatedAsyncioTestCase):
    async def run_decision(self, decision):
        with tempfile.TemporaryDirectory() as folder, patch.object(settings, 'translation_engine', 'gemini_web'):
            root = Path(folder); video = root/'source.mp4'; video.touch()
            batch = [{'id': 1, 'time': [0, 1], 'source': 'o号'}]
            token = repair_source.set((video, root, True))
            try:
                remote = SimpleNamespace(complete=AsyncMock(return_value=json.dumps({'items': [dict(id=1, **decision)]})))
                with patch('app.services.subtitles.vision_review._frame_sheet') as sheet, patch('app.services.ai.web_provider.browser', remote):
                    texts, _ = await repair_failed_cues(batch, context={}, memory={}, previous=[], following=[])
                self.assertEqual(len(sheet.call_args.args[1]), 3)
                self.assertTrue(sheet.call_args.args[-1])
                self.assertIn('is_caption=false', remote.complete.call_args.args[0][0]['content'])
                if texts.get(1) == NON_CAPTION:
                    source = [SubtitleEvent(0, 1, 'o号'), SubtitleEvent(1, 2, '你好')]
                    target = [SubtitleEvent(0, 1, NON_CAPTION), SubtitleEvent(1, 2, 'Xin chào')]
                    filtered = filter_verified_non_captions(source, target, root)
                    self.assertEqual([e.text for e in filtered[1]], ['Xin chào'])
                    self.assertEqual([e.text for e in filtered[0]], ['你好'])
                return texts
            finally:
                repair_source.reset(token)

    async def test_confirmed_background_text_is_saved_and_filtered_in_alignment(self):
        result = await self.run_decision(dict(is_caption=False, confidence=.99, reason='Only writing on a ribbon in all frames', source_verified='', text='__NON_CAPTION__', non_caption_frame_ids=[1,2,3]))
        self.assertEqual(result, {1: NON_CAPTION})

    async def test_uncertain_or_incomplete_negative_decisions_do_not_remove_caption(self):
        base = dict(is_caption=False, confidence=.99, reason='Background', source_verified='', text='__NON_CAPTION__', non_caption_frame_ids=[1,2,3])
        for change in [dict(confidence=.9), dict(non_caption_frame_ids=[2]), dict(reason=''), dict(source_verified='是')]:
            self.assertEqual(await self.run_decision(base | change), {})

    async def test_real_one_character_caption_is_still_translated(self):
        self.assertEqual(await self.run_decision(dict(is_caption=True, confidence=.99, reason='Visible subtitle', source_verified='是', text='Vâng.')), {1: 'Vâng.'})

    async def test_plain_translation_cannot_forge_a_skip_marker(self):
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'items':[{'id':1,'text':NON_CAPTION}]})))])))))
        with self.assertRaises(TranslationBatchStructureError):
            await _translate_gemini_batch(client, [{'id':1,'source':'是','time':[0,1]}], 'zh','vi',{}, {}, [], [])

    def test_skip_marker_without_source_bound_evidence_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, 'EVIDENCE_MISSING'):
                filter_verified_non_captions([SubtitleEvent(0,1,'是')], [SubtitleEvent(0,1,NON_CAPTION)], folder)
