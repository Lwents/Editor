import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core import settings
from app.services.ai.caption_repair import repair_source, repair_failed_cues
from app.services.ai.provider_status import provider_name, error_prefix
from app.services.ai.translation import GeminiTranslation, TranslationError
from app.services.presets import get_processing_profile
from app.services.subtitles.timing import SubtitleEvent


class CaptionRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_repair_accepts_only_confident_translated_cues(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(settings,'translation_engine','deepseek_web'):
            root=Path(folder);video=root/'source.mp4';video.write_bytes(b'fixture')
            token=repair_source.set((video,root,True))
            data={'items':[{'id':7,'text':'Mời đi lối này','source_verified':'这边请','confidence':.98},
                           {'id':8,'text':'đoán','source_verified':'不清楚','confidence':.2},
                           {'id':9,'text':'chưa dịch 中文','source_verified':'中文','confidence':.99}]}
            remote=SimpleNamespace(complete=AsyncMock(return_value=json.dumps(data)))
            batch=[{'id':i,'time':[i,i+1],'source':'OCR rác'} for i in [7,8,9]]
            try:
                with patch('app.services.subtitles.vision_review._frame_sheet'),patch('app.services.ai.web_provider.browser',remote):
                    texts,_=await repair_failed_cues(batch,context={},memory={},previous=[],following=[])
                self.assertEqual(texts,{7:'Mời đi lối này'})
                self.assertTrue(remote.complete.call_args.kwargs['attachments'])
                evidence=json.loads((root/'caption-repair/decisions.json').read_text())
                self.assertEqual(set(evidence),{'7'})
            finally:repair_source.reset(token)

    async def test_text_only_does_not_upload_images_or_guess_unreadable_ocr(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(settings,'translation_engine','deepseek_web'):
            root=Path(folder);video=root/'source.mp4';video.write_bytes(b'fixture')
            token=repair_source.set((video,root,False))
            remote=SimpleNamespace(complete=AsyncMock())
            try:
                with patch('app.services.subtitles.vision_review._local_consensus',return_value=[]),patch('app.services.ai.web_provider.browser',remote):
                    texts,_=await repair_failed_cues([{'id':1,'time':[0,1],'source':'OCR'}],context={},memory={},previous=[],following=[])
                self.assertEqual(texts,{})
                remote.complete.assert_not_awaited()
            finally:repair_source.reset(token)

    async def test_repaired_batch_finishes_without_retranslating_or_losing_checkpoint(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(settings,'translation_engine','deepseek_web'):
            profile=replace(get_processing_profile('fast'),translation_batch_items=2)
            with patch('app.services.ai.translation.get_translation_profile',return_value=profile),patch('app.services.ai.translation.create_translation_client'),patch('app.services.ai.translation._translate_gemini_batch_with_retries',new=AsyncMock(side_effect=TranslationError('sót chữ Trung'))),patch('app.services.ai.caption_repair.repair_failed_cues',new=AsyncMock(return_value=({1:'Xin chào',2:'Tạm biệt'},{}))):
                result=await GeminiTranslation().translate_events([SubtitleEvent(0,1,'你好'),SubtitleEvent(1,2,'再见')],'zh','vi',processing_mode='fast',checkpoint_file=Path(folder)/'translation.json')
            self.assertEqual([e.text for e in result],['Xin chào','Tạm biệt'])
            self.assertTrue(json.loads((Path(folder)/'translation.json').read_text())['completed'])

    def test_provider_labels_match_selection(self):
        for engine,name,prefix in [('deepseek_web','DeepSeek','DEEPSEEK'),('gemini_web','Gemini','GEMINI'),('gemini',settings.ai_model,'AI')]:
            with patch.object(settings,'translation_engine',engine):
                self.assertEqual((provider_name(),error_prefix()),(name,prefix))

    async def test_selective_repair_flag_is_scoped_and_does_not_enable_full_image_review(self):
        from app.services.subtitles.recovery import get_or_create_subtitles
        from app.services.subtitles.mode import text_only_gemini, repair_failed_captions
        observed=[]
        async def create(*args,**kwargs):
            observed.append((text_only_gemini.get(),repair_failed_captions.get()))
            self.assertNotIn('repair_failed_captions',kwargs)
            return 'done'
        before=(text_only_gemini.get(),repair_failed_captions.get())
        with patch('app.services.subtitles.source.get_or_create_subtitles',side_effect=create):
            await get_or_create_subtitles(None,None,None,None,lambda *a:None,text_only_gemini=True,repair_failed_captions=True)
        self.assertEqual(observed,[(True,True)])
        self.assertEqual((text_only_gemini.get(),repair_failed_captions.get()),before)
