import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import numpy as np

from app.core import settings
from app.services.ai.asr_cache import digest
from app.services.ai.translation import TRANSLATION_POLICY_VERSION
from app.services.ai.character_names import build_character_name_registry, canonicalize_character_text
from app.services.ai.context import analyze_video_context
from app.services.ai.gemini_web import GeminiWebError, GeminiWebRefusal, GeminiWebTimeout, GeminiWebVerificationRequired
from app.services.subtitles.ocr import read_lines
from app.services.subtitles.source import _quality_signature, _validated_target
from app.services.subtitles.visible import ALGORITHM_VERSION
from app.services.subtitles.timing import SubtitleEvent
from app.services.subtitles.vision_review import VERSION as VISION_REVIEW_VERSION, _candidate_ids, _verify_caption_gaps, apply_review, review_visible_captions


class VisibleCaptionTests(unittest.TestCase):
    def test_high_confidence_single_character_still_checks_caption_presence(self):
        events = [SubtitleEvent(1, 2, '井')]
        report = {'cues': [{'start': 1, 'end': 2, 'confidence': .999, 'needs_review': False}]}
        self.assertEqual(_candidate_ids(events, report), [1])

    def test_suffix_fragment_is_verified_even_when_ocr_reports_high_confidence(self):
        events = [SubtitleEvent(1, 2, '一个不留'), SubtitleEvent(2, 3, '个不留')]
        report = {'cues': [{'start': e.start, 'end': e.end, 'confidence': .999} for e in events]}
        self.assertEqual(_candidate_ids(events, report), [1, 2])

    def test_noisy_previous_caption_does_not_hide_its_short_suffix(self):
        events = [SubtitleEvent(1, 2, '小器 一个不留'), SubtitleEvent(2, 3, '个不留')]
        report = {'cues': [{'start': e.start, 'end': e.end, 'confidence': .999} for e in events]}
        self.assertEqual(_candidate_ids(events, report), [1, 2])

    def test_spacing_in_chinese_does_not_duplicate_adjacent_caption(self):
        from app.services.subtitles.vision_review import merge_caption_fragments
        merged = merge_caption_fragments([SubtitleEvent(1, 2, '啊 你这'), SubtitleEvent(2, 3, '啊你这')])
        self.assertEqual(len(merged), 1)
        self.assertEqual((merged[0].start, merged[0].end), (1, 3))
        self.assertEqual(len(merge_caption_fragments([SubtitleEvent(1, 2, 'New York'),
                                                     SubtitleEvent(2, 3, 'NewYork')])), 2)

    def test_character_aliases_do_not_replace_titles_or_affectionate_address(self):
        registry = build_character_name_registry({'characters': [{
            'name': 'Tiêu Trì', 'aliases': ['箫池', '王爷', 'Vương gia', '池儿', 'Trì nhi'],
        }]})
        self.assertEqual(canonicalize_character_text('Vương gia nói phải. Trì nhi.', registry),
                         'Vương gia nói phải. Trì nhi.')
        self.assertEqual(canonicalize_character_text('箫池', registry), 'Tiêu Trì')

    def test_unknown_character_description_does_not_replace_court_address(self):
        registry = build_character_name_registry({'characters': [{
            'name': 'Nữ tử áo trắng', 'aliases': ['娘娘', 'Nương nương'],
        }]})
        self.assertEqual(canonicalize_character_text('Bẩm Nương nương.', registry),
                         'Bẩm Nương nương.')
        self.assertNotIn('娘娘', registry)

    def test_scene_labels_do_not_erase_faction_or_role_information(self):
        registry = build_character_name_registry({'characters': [
            {'name': 'Nhóm thích khách', 'aliases': ['梁氏余孽', 'tàn dư họ Lương']},
            {'name': 'Oản Oản', 'aliases': ['绾绾', '女反一']},
            {'name': 'Nam tử áo đen', 'aliases': ['箫池']},
        ]})
        self.assertEqual(canonicalize_character_text('Hóa ra cũng là tàn dư họ Lương!', registry),
                         'Hóa ra cũng là tàn dư họ Lương!')
        self.assertNotIn('梁氏余孽', registry)
        self.assertNotIn('女反一', registry)
        self.assertNotIn('箫池', registry)
        self.assertEqual(canonicalize_character_text('绾绾', registry), 'Oản Oản')

    def test_scene_description_does_not_replace_game_term_npc(self):
        registry = build_character_name_registry({'characters': [
            {'name': 'Tên lính áp giải', 'aliases': ['NPC']},
        ]})
        self.assertNotIn('NPC', registry)
        self.assertEqual(canonicalize_character_text('Đám NPC sẽ ra tay chí mạng.', registry),
                         'Đám NPC sẽ ra tay chí mạng.')

    def test_specific_royal_titles_remain_titles_even_when_person_is_known(self):
        registry = build_character_name_registry({'characters': [
            {'name': 'Tiêu Trì', 'aliases': ['箫池', '摄政王', 'Nhiếp chính vương', '燕王', '主子', 'chủ tử']},
        ]})
        self.assertEqual(canonicalize_character_text('Nhiếp chính vương máu lạnh. Chủ tử!', registry),
                         'Nhiếp chính vương máu lạnh. Chủ tử!')
        self.assertNotIn('摄政王', registry)
        self.assertNotIn('燕王', registry)
        self.assertNotIn('主子', registry)
        self.assertEqual(canonicalize_character_text('箫池', registry), 'Tiêu Trì')

    def test_small_caption_gap_requires_explicit_image_confirmation(self):
        events = [SubtitleEvent(1, 1.2, '楚绾绾'), SubtitleEvent(1.267, 1.7, '楚绾绾')]
        items = [{'id': i, 'text': '楚绾绾', 'is_caption': True, 'confidence': .99} for i in (1, 2)]
        self.assertEqual(apply_review(events, [1, 2], items)[0], events)

        items[1]['gap_is_caption'] = True
        self.assertEqual(apply_review(events, [1, 2], items)[0], [SubtitleEvent(1, 1.7, '楚绾绾')])
        items[1]['gap_is_caption'] = 'true'
        self.assertEqual(apply_review(events, [1, 2], items)[0], events)

    def test_gap_scan_rejects_a_blank_frame_between_matching_captions(self):
        events = [SubtitleEvent(1, 1.2, '嗯'), SubtitleEvent(1.4, 1.7, '嗯')]
        items = [{'id': i, 'text': '嗯', 'is_caption': True, 'confidence': .99} for i in (1, 2)]
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        cap = SimpleNamespace(get=lambda key: 10, set=lambda *args: None,
                              read=lambda: (True, frame), release=lambda: None)
        reading = [(0, 0, 10, 10, '嗯', .999)]
        with patch('cv2.VideoCapture', return_value=cap), patch('app.services.subtitles.ocr.get_reader', return_value=object()), patch('app.services.subtitles.ocr.read_lines', side_effect=[reading, reading, []]):
            checked = _verify_caption_gaps(Path('test.mp4'), events, items, [{'top': .7, 'bottom': 1}])
        self.assertFalse(checked[1].get('gap_is_caption', False))
        with patch('cv2.VideoCapture', return_value=cap), patch('app.services.subtitles.ocr.get_reader', return_value=object()), patch('app.services.subtitles.ocr.read_lines', return_value=reading):
            checked = _verify_caption_gaps(Path('test.mp4'), events, items, [{'top': .7, 'bottom': 1}])
        self.assertTrue(checked[1]['gap_is_caption'])
        self.assertEqual(apply_review(events, [1, 2], checked)[0], [SubtitleEvent(1, 1.7, '嗯')])

    def test_confused_short_names_can_only_bridge_image_verified_gap(self):
        events = [SubtitleEvent(1, 1.1, '缩馆'), SubtitleEvent(1.5, 1.8, '绾绾')]
        report = {'cues': [{'start': e.start, 'end': e.end, 'confidence': .99} for e in events]}
        self.assertEqual(_candidate_ids(events, report), [1, 2])
        items = [{'id': i, 'text': '绾绾', 'is_caption': True, 'confidence': .99} for i in (1, 2)]
        self.assertEqual(len(apply_review(events, [1, 2], items)[0]), 2)
        items[1]['gap_is_caption'] = True
        self.assertEqual(apply_review(events, [1, 2], items)[0], [SubtitleEvent(1, 1.8, '绾绾')])
        items[1]['gap_is_caption'] = 'true'
        self.assertEqual(len(apply_review(events, [1, 2], items)[0]), 2)

    def test_confident_ocr_flicker_is_verified_before_merging(self):
        events = [SubtitleEvent(1, 2, '爭取一点时间'), SubtitleEvent(2, 2.5, '争取一点时间')]
        report = {'cues': [{'start': e.start, 'end': e.end, 'confidence': .98} for e in events]}
        self.assertEqual(_candidate_ids(events, report), [1, 2])
        reviewed, _ = apply_review(events, [1, 2], [
            {'id': i, 'text': '争取一点时间', 'is_caption': True, 'confidence': .99}
            for i in (1, 2)
        ])
        self.assertEqual(reviewed, [SubtitleEvent(1, 2.5, '争取一点时间')])

    def test_short_prefix_fragment_requires_checking_both_caption_images(self):
        events = [SubtitleEvent(1, 1.7, '主'), SubtitleEvent(1.7, 1.8, '主子')]
        report = {'cues': [{'start': e.start, 'end': e.end, 'confidence': .99} for e in events]}
        self.assertEqual(_candidate_ids(events, report), [1, 2])
        corrected, _ = apply_review(events, [1, 2], [
            {'id': i, 'text': '主子', 'is_caption': True, 'confidence': .99} for i in (1, 2)])
        self.assertEqual(corrected, [SubtitleEvent(1, 1.8, '主子')])
        actual_prefix, _ = apply_review(events, [1, 2], [
            {'id': i, 'text': event.text, 'is_caption': True, 'confidence': .99}
            for i, event in enumerate(events, 1)])
        self.assertEqual(actual_prefix, events)
        spaced = [SubtitleEvent(1, 1.7, '主'), SubtitleEvent(1.7, 1.8, '主 子')]
        self.assertEqual(_candidate_ids(spaced, report), [1, 2])

    def reader(self, text, confidence):
        output = SimpleNamespace(
            boxes=np.array([[[10, 5], [28, 5], [28, 25], [10, 25]]]),
            txts=[text], scores=[confidence],
        )
        return lambda image: output

    def test_short_reply_kept_only_inside_confirmed_caption_region(self):
        image = np.zeros((40, 80, 3), dtype=np.uint8)
        reader = self.reader('是', .999)
        self.assertEqual(read_lines(reader, image), [])
        self.assertEqual(read_lines(reader, image, caption_region=True)[0][4], '是')

    def test_uncertain_single_glyph_and_numeric_noise_remain_filtered(self):
        image = np.zeros((40, 80, 3), dtype=np.uint8)
        for text, confidence in [('是', .70), ('1', .999), ('!', .999)]:
            self.assertEqual(read_lines(self.reader(text, confidence), image,
                                        caption_region=True), [])

    def test_weak_caption_is_provisional_until_image_review(self):
        image = np.zeros((40, 80, 3), dtype=np.uint8)
        reader = self.reader('缩绾', .45)
        self.assertEqual(read_lines(reader, image, caption_region=True), [])
        self.assertEqual(read_lines(reader, image, minimum=.4, caption_region=True)[0][4], '缩绾')
        events = [SubtitleEvent(1, 2, '缩绾')]
        checked, _ = apply_review(events, [1], [
            {'id': 1, 'text': '绾绾', 'is_caption': True, 'confidence': .99},
        ])
        self.assertEqual(checked, [SubtitleEvent(1, 2, '绾绾')])

    def test_ocr_upgrade_invalidates_otherwise_valid_translation_cache(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / 'source.mp4'; video.write_bytes(b'video')
            target = root / 'subtitles.vi.srt'; target.write_text('translated')
            grouped = root / 'subtitles.grouped.srt'; grouped.write_text('source')
            (root / 'subtitles.recognition.json').write_text('{}')
            marker = {'policy': TRANSLATION_POLICY_VERSION, 'review_completed': True,
                      'engine': settings.translation_engine,
                      'model': 'Gemini Web' if settings.translation_engine == 'gemini_web' else settings.ai_model,
                      'video': _quality_signature(video), 'sha256': digest(target),
                      'source_sha256': digest(grouped)}
            quality = root / 'translation_quality.json'
            quality.write_text(json.dumps(marker))
            self.assertFalse(_validated_target(target, root, video))
            marker['recognition_version'] = ALGORITHM_VERSION
            quality.write_text(json.dumps(marker))
            self.assertTrue(_validated_target(target, root, video))
            (root / 'subtitles.vision-review.json').write_text('{}')
            self.assertFalse(_validated_target(target, root, video))
            marker['vision_review_version'] = VISION_REVIEW_VERSION
            quality.write_text(json.dumps(marker))
            self.assertTrue(_validated_target(target, root, video))


class ContextRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_caption_verification_does_not_retry_captcha(self):
        complete = AsyncMock(side_effect=GeminiWebVerificationRequired('CAPTCHA'))
        with TemporaryDirectory() as directory, \
                patch('app.services.subtitles.vision_review._frame_sheet'), \
                patch('app.services.ai.gemini_web.browser.complete', complete):
            root = Path(directory)
            source = root / 'source.mp4'; source.write_bytes(b'video')
            (root / 'subtitles.recognition.json').write_text(json.dumps({'cues': [
                {'start': 1, 'end': 2, 'needs_review': True, 'confidence': .4}]}))
            with self.assertRaises(GeminiWebVerificationRequired):
                await review_visible_captions(source, [SubtitleEvent(1, 2, '缩绾')], root, lambda *args: None)
            self.assertEqual(complete.await_count, 1)
            self.assertFalse((root / 'subtitles.vision-review.json').exists())

    async def test_caption_verification_does_not_retry_refusal(self):
        complete = AsyncMock(side_effect=GeminiWebRefusal('Gemini từ chối'))
        with TemporaryDirectory() as directory, \
                patch('app.services.subtitles.vision_review._frame_sheet'), \
                patch('app.services.ai.gemini_web.browser.complete', complete):
            root = Path(directory)
            source = root / 'source.mp4'; source.write_bytes(b'video')
            (root / 'subtitles.recognition.json').write_text(json.dumps({'cues': [
                {'start': 1, 'end': 2, 'needs_review': True, 'confidence': .4}]}))
            with self.assertRaises(GeminiWebRefusal):
                await review_visible_captions(source, [SubtitleEvent(1, 2, '缩绾')], root, lambda *args: None)
            self.assertEqual(complete.await_count, 1)
            self.assertFalse((root / 'subtitles.vision-review.json').exists())

    async def test_text_context_timeout_is_not_replayed(self):
        complete = AsyncMock(side_effect=GeminiWebTimeout('Quá thời gian'))
        with TemporaryDirectory() as directory, patch.object(settings, 'translation_engine', 'gemini_web'), \
                patch('app.services.ai.gemini_web.browser.complete', complete):
            with self.assertRaises(GeminiWebTimeout):
                await analyze_video_context(None, [SubtitleEvent(1, 2, '小心')], Path(directory))
            self.assertEqual(complete.await_count, 1)

    async def test_image_context_refusal_has_one_text_only_fallback(self):
        reply = json.dumps({'items': [{'id': 1, 'text': 'Có lời cảnh báo.'}],
                            'memory_updates': {'plot_summary': 'Có lời cảnh báo.'}})
        complete = AsyncMock(side_effect=[GeminiWebRefusal('Không đọc được ảnh'), reply])
        with TemporaryDirectory() as directory, patch.object(settings, 'translation_engine', 'gemini_web'), \
                patch('app.services.ai.context._context_frame_sheet', return_value=True), \
                patch('app.services.ai.gemini_web.browser.complete', complete):
            context = await analyze_video_context(None, [SubtitleEvent(1, 2, '小心')], Path(directory))
            self.assertEqual(complete.await_count, 2)
            self.assertTrue(complete.await_args_list[0].kwargs['attachments'])
            self.assertIsNone(complete.await_args_list[1].kwargs['attachments'])
            self.assertTrue(any('Ảnh cảnh chưa được kiểm tra' in note for note in context['translation_notes']))

    async def test_refused_text_context_stops_without_another_request(self):
        complete = AsyncMock(side_effect=GeminiWebRefusal('Không thể trợ giúp'))
        with TemporaryDirectory() as directory, patch.object(settings, 'translation_engine', 'gemini_web'), \
                patch('app.services.ai.gemini_web.browser.complete', complete):
            with self.assertRaises(GeminiWebRefusal):
                await analyze_video_context(None, [SubtitleEvent(1, 2, '小心')], Path(directory))
            self.assertEqual(complete.await_count, 1)

    async def test_captcha_requires_a_person_and_does_not_retry(self):
        complete = AsyncMock(side_effect=GeminiWebVerificationRequired('CAPTCHA'))
        with TemporaryDirectory() as directory, patch.object(settings, 'translation_engine', 'gemini_web'), \
                patch('app.services.ai.gemini_web.browser.complete', complete):
            with self.assertRaises(GeminiWebVerificationRequired):
                await analyze_video_context(None, [SubtitleEvent(1, 2, '小心')], Path(directory))
            self.assertEqual(complete.await_count, 1)
            self.assertFalse((Path(directory) / 'ai_context.json').exists())

    async def test_image_timeout_retry_does_not_invent_visual_context(self):
        reply = json.dumps({'items': [{'id': 1, 'text': 'Có lời cảnh báo nguy hiểm.'}],
                            'memory_updates': {'plot_summary': 'Có lời cảnh báo nguy hiểm.'}})
        complete = AsyncMock(side_effect=[GeminiWebError('timeout'), reply])
        with TemporaryDirectory() as directory, patch.object(settings, 'translation_engine', 'gemini_web'), \
                patch('app.services.ai.context._context_frame_sheet', return_value=True), \
                patch('app.services.ai.gemini_web.browser.complete', complete):
            await analyze_video_context(None, [SubtitleEvent(1, 2, '小心')], Path(directory))
            first, retry = complete.await_args_list
            self.assertTrue(first.kwargs['attachments'])
            self.assertIsNone(retry.kwargs['attachments'])
            self.assertIn('chưa kiểm tra được ảnh cảnh', retry.args[0][-1]['content'])

    async def test_transient_gemini_failure_retries_context_and_caches_success(self):
        reply = json.dumps({'items': [{'id': 1, 'text': 'Một người cảnh báo nguy hiểm.'}],
                            'memory_updates': {'plot_summary': 'Một người cảnh báo nguy hiểm.', 'characters': []}})
        complete = AsyncMock(side_effect=[GeminiWebError('timeout'), reply])
        with TemporaryDirectory() as directory, patch.object(settings, 'translation_engine', 'gemini_web'), \
                patch('app.services.ai.gemini_web.browser.complete', complete):
            events = [SubtitleEvent(1, 2, '小心')]
            context = await analyze_video_context(None, events, Path(directory))
            self.assertEqual(complete.await_count, 2)
            self.assertEqual(context['plot_summary'], 'Một người cảnh báo nguy hiểm.')
            await analyze_video_context(None, events, Path(directory))
            self.assertEqual(complete.await_count, 2)




class LocalConsensusTests(unittest.TestCase):
    def test_stronger_recognizer_reads_pixels_and_does_not_fill_a_short_read(self):
        from unittest.mock import Mock
        from app.services.subtitles.vision_review import _local_consensus
        events = [SubtitleEvent(1, 2, '一个不留'), SubtitleEvent(2, 3, '个不留')]
        known = [{'id': 1, 'text': '一个不留', 'is_caption': True, 'confidence': .99}]
        for text, expected in [('一个不留', True), ('个不留', False), ('一个不死', False)]:
            with self.subTest(text=text), TemporaryDirectory() as directory:
                root = Path(directory)
                model = root / 'models' / 'ocr' / 'ch_PP-OCRv5_rec_server.onnx'
                model.parent.mkdir(parents=True); model.touch()
                capture = Mock()
                capture.read.return_value = (True, np.zeros((120, 200, 3), dtype=np.uint8))
                with patch('app.core.BACKEND_DIR', root), patch('cv2.VideoCapture', return_value=capture), \
                        patch('app.services.subtitles.ocr.get_reader'), \
                        patch('app.services.subtitles.ocr.read_lines', return_value=[]), \
                        patch('app.services.subtitles.ocr.read_known_band', return_value=(text, .99)):
                    result = _local_consensus(Path('source.mp4'), events, [2],
                                              [{'top': .75, 'bottom': .95}], known)
                self.assertEqual(bool(result), expected)
                if result:
                    self.assertEqual(result[0]['text'], text)
                    self.assertEqual(result[0]['method'], 'local_server_recognition')
                capture.release.assert_called_once()

    def read(self, readings):
        import numpy as np
        from unittest.mock import Mock
        from app.services.subtitles.vision_review import _local_consensus
        capture = Mock()
        capture.read.return_value = (True, np.zeros((120, 200, 3), dtype=np.uint8))
        lines = [[(1, 1, 50, 20, text, score)] if text else [] for text, score in readings]
        with patch('cv2.VideoCapture', return_value=capture), \
             patch('app.services.subtitles.ocr.get_reader'), \
             patch('app.services.subtitles.ocr.read_lines', side_effect=lines):
            result = _local_consensus(Path('source.mp4'), [SubtitleEvent(1, 2, '主')], [1],
                                      [{'top': .75, 'bottom': .95}])
        capture.release.assert_called_once()
        return result

    def test_actual_image_agreement_can_recover_a_missing_character(self):
        result = self.read([('主子', .995), ('主子', .999), ('主子', .998)])
        self.assertEqual(result[0]['text'], '主子')
        self.assertEqual(result[0]['method'], 'local_multiscale_ocr')

    def test_two_confident_truncated_crops_do_not_overrule_the_full_caption(self):
        self.assertEqual(self.read([('一个不留', .999), ('个不留', .999), ('个不留', .999)]), [])

    def test_shortened_read_requires_gemini_when_neighbor_image_has_full_phrase(self):
        from app.services.subtitles.vision_review import _is_shorter_verified_fragment
        events = [SubtitleEvent(1, 2, '一个不留'), SubtitleEvent(2, 3, '个不留')]
        full = {'id': 1, 'text': '一个不留', 'is_caption': True, 'confidence': .99}
        short = {'id': 2, 'text': '个不留', 'is_caption': True, 'confidence': .999}
        self.assertTrue(_is_shorter_verified_fragment(events, short, {1: full}))
        self.assertFalse(_is_shorter_verified_fragment(events, short, {}))

    def test_isolated_rare_character_still_needs_caption_classification(self):
        self.assertEqual(self.read([('井', .999), ('井', .999), ('井', .999)]), [])

    def test_empty_reads_do_not_claim_there_is_no_caption(self):
        self.assertEqual(self.read([('', 0), ('', 0), ('', 0)]), [])

    def test_low_confidence_agreement_still_requires_gemini(self):
        self.assertEqual(self.read([('主子', .97), ('主子', .999), ('主子', .999)]), [])

if __name__ == '__main__':
    unittest.main()
