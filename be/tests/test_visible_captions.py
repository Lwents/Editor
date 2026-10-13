import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import numpy as np

from app.core import settings
from app.services.ai.asr_cache import digest
from app.services.ai.translation import TRANSLATION_POLICY_VERSION
from app.services.ai.character_names import build_character_name_registry, canonicalize_character_text
from app.services.subtitles.ocr import read_lines
from app.services.subtitles.source import _quality_signature, _validated_target
from app.services.subtitles.visible import ALGORITHM_VERSION
from app.services.subtitles.timing import SubtitleEvent
from app.services.subtitles.vision_review import _candidate_ids, apply_review


class VisibleCaptionTests(unittest.TestCase):
    def test_character_aliases_do_not_replace_titles_or_affectionate_address(self):
        registry = build_character_name_registry({'characters': [{
            'name': 'Tiêu Trì', 'aliases': ['箫池', '王爷', 'Vương gia', '池儿', 'Trì nhi'],
        }]})
        self.assertEqual(canonicalize_character_text('Vương gia nói phải. Trì nhi.', registry),
                         'Vương gia nói phải. Trì nhi.')
        self.assertEqual(canonicalize_character_text('箫池', registry), 'Tiêu Trì')

    def test_small_caption_gap_requires_explicit_image_confirmation(self):
        events = [SubtitleEvent(1, 1.2, '楚绾绾'), SubtitleEvent(1.267, 1.7, '楚绾绾')]
        items = [{'id': i, 'text': '楚绾绾', 'is_caption': True, 'confidence': .99} for i in (1, 2)]
        self.assertEqual(apply_review(events, [1, 2], items)[0], events)
        items[1]['gap_is_caption'] = True
        self.assertEqual(apply_review(events, [1, 2], items)[0], [SubtitleEvent(1, 1.7, '楚绾绾')])
        items[1]['gap_is_caption'] = 'true'
        self.assertEqual(apply_review(events, [1, 2], items)[0], events)

    def test_confident_ocr_flicker_is_verified_before_merging(self):
        events = [SubtitleEvent(1, 2, '爭取一点时间'), SubtitleEvent(2, 2.5, '争取一点时间')]
        report = {'cues': [{'start': e.start, 'end': e.end, 'confidence': .98} for e in events]}
        self.assertEqual(_candidate_ids(events, report), [1, 2])
        reviewed, _ = apply_review(events, [1, 2], [
            {'id': i, 'text': '争取一点时间', 'is_caption': True, 'confidence': .99}
            for i in (1, 2)
        ])
        self.assertEqual(reviewed, [SubtitleEvent(1, 2.5, '争取一点时间')])

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


if __name__ == '__main__':
    unittest.main()
