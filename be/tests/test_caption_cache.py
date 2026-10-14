import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.core import settings
from app.services.subtitles.caption_cache import save_decisions, restore_decisions, preserve_legacy_decisions
from app.services.subtitles.timing import SubtitleEvent


class CaptionCacheTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.root = Path(directory.name)
        self.addCleanup(directory.cleanup)
        storage = patch.object(settings, 'storage_dir', str(self.root / 'storage'))
        storage.start()
        self.addCleanup(storage.stop)
        self.events = [SubtitleEvent(1, 2, '井')]
        self.regions = [{'top': .8, 'bottom': .95}]
        self.decision = {'id': 1, 'text': '是', 'confidence': .99, 'is_caption': True,
                         'method': 'gemini_image_review', 'gap_is_caption': True}

    def test_inserting_a_cue_remaps_id_without_transferring_gap_proof(self):
        save_decisions('abc', self.events, [self.decision], self.regions)
        events = [SubtitleEvent(0, .5, '新'), *self.events]
        restored = restore_decisions('abc', events, [2], self.regions)
        self.assertEqual(restored[0]['id'], 2)
        self.assertEqual(restored[0]['text'], '是')
        self.assertNotIn('gap_is_caption', restored[0])

    def test_different_video_region_timing_or_raw_text_cannot_reuse_evidence(self):
        save_decisions('abc', self.events, [self.decision], self.regions)
        for video, events, regions in [('other', self.events, self.regions),
                                      ('abc', self.events, []),
                                      ('abc', [SubtitleEvent(1.1, 2, '井')], self.regions),
                                      ('abc', [SubtitleEvent(1, 2, '主')], self.regions)]:
            self.assertEqual(restore_decisions(video, events, [1], regions), [])

    def test_unverified_and_low_confidence_reads_do_not_become_trusted(self):
        for extra in ({'method': 'unverified_ocr'}, {'confidence': 0}, {'confidence': True}):
            save_decisions('abc', self.events, [{**self.decision, **extra}], self.regions)
        self.assertEqual(restore_decisions('abc', self.events, [1], self.regions), [])

    def test_image_rejection_is_not_overwritten_by_confident_local_texture(self):
        negative = {**self.decision, 'text': '', 'is_caption': False}
        save_decisions('abc', self.events, [negative], self.regions)
        local = {**self.decision, 'text': '井', 'confidence': .999, 'method': 'local_multiscale_ocr'}
        save_decisions('abc', self.events, [local], self.regions)
        self.assertFalse(restore_decisions('abc', self.events, [1], self.regions)[0]['is_caption'])

    def test_legacy_checkpoint_requires_matching_content_fingerprint(self):
        source = self.root / 'source.mp4'
        source.write_bytes(b'video')
        subtitles = self.root / 'subtitles.visible.srt'
        subtitles.write_text('1\n00:00:01,000 --> 00:00:02,000\n井\n', encoding='utf-8')
        (self.root / 'subtitles.recognition.json').write_text(json.dumps({'regions': self.regions}))
        checkpoint = self.root / 'subtitles.vision-review.json'
        checkpoint.write_text(json.dumps({'fingerprint': 'wrong', 'items': [self.decision]}))
        preserve_legacy_decisions(source, subtitles, self.root)
        video = hashlib.sha256(b'video').hexdigest()
        self.assertEqual(restore_decisions(video, self.events, [1], self.regions), [])
        data = {'version': 13, 'events': [(1., 2., '井')], 'video_digest': video, 'ids': [1]}
        fingerprint = hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()
        checkpoint.write_text(json.dumps({'fingerprint': fingerprint, 'items': [self.decision]}))
        preserve_legacy_decisions(source, subtitles, self.root)
        self.assertEqual(restore_decisions(video, self.events, [1], self.regions)[0]['text'], '是')
