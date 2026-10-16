import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.services.ai.caption_repair import repair_source
from app.services.ai.translation import (
    TranslationBatchStructureError, TranslationError,
    _translate_gemini_batch_with_retries,
)


class EarlySourceRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_repairs_only_invalid_cue_and_saves_before_return(self):
        batch = [{'id': 191, 'source': '你好'}, {'id': 192, 'source': '坏OCR'}]
        error = TranslationBatchStructureError('chữ Trung', {191: 'Xin chào'}, source_repair_ids={192})
        store = SimpleNamespace(load=Mock(return_value=({}, [])), save=Mock())
        token = repair_source.set(('fixture.mp4', 'fixture', True))
        try:
            with patch('app.services.ai.translation._translate_gemini_batch', AsyncMock(side_effect=error)) as translate, patch('app.services.ai.caption_repair.repair_failed_cues', AsyncMock(return_value=({192: 'Không chỉ có đàn piano'}, {}))) as repair:
                result, _ = await _translate_gemini_batch_with_retries(None, batch, 'zh', 'vi', {}, {}, [], [], recovery_store=store)
            self.assertEqual(result, ['Xin chào', 'Không chỉ có đàn piano'])
            translate.assert_awaited_once()
            self.assertEqual(repair.call_args.args[0], [batch[1]])
            self.assertEqual(store.save.call_args.args[1], {192: 'Không chỉ có đàn piano'})
        finally:
            repair_source.reset(token)

    async def test_failed_repair_is_bounded_across_recursive_text_retries(self):
        batch = [{'id': 1, 'source': '好'}, {'id': 2, 'source': '坏'}]
        errors = [TranslationBatchStructureError('chữ Trung', {1: 'Được'}, source_repair_ids={2}),
                  TranslationBatchStructureError('chữ Trung', source_repair_ids={2}),
                  TranslationBatchStructureError('chữ Trung', source_repair_ids={2})]
        token = repair_source.set(('fixture.mp4', 'fixture', True))
        try:
            with patch('app.services.ai.translation._translate_gemini_batch', AsyncMock(side_effect=errors)), patch('app.services.ai.caption_repair.repair_failed_cues', AsyncMock(return_value=({}, {}))) as repair, patch('app.services.ai.translation.asyncio.sleep', AsyncMock()):
                with self.assertRaises(TranslationError):
                    await _translate_gemini_batch_with_retries(None, batch, 'zh', 'vi', {}, {}, [], [], retries=2)
            repair.assert_awaited_once()
        finally:
            repair_source.reset(token)

    async def test_structure_error_without_cjk_does_not_trigger_source_repair(self):
        with patch('app.services.ai.translation._translate_gemini_batch', AsyncMock(side_effect=TranslationBatchStructureError('missing id'))), patch('app.services.ai.caption_repair.repair_failed_cues', AsyncMock()) as repair:
            with self.assertRaises(TranslationError):
                await _translate_gemini_batch_with_retries(None, [{'id': 1}], 'zh', 'vi', {}, {}, [], [], retries=1)
        repair.assert_not_awaited()
