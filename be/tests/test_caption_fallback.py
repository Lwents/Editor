import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import cv2
import numpy as np

from app.services.subtitles.ocr import has_caption_strokes, read_caption_fallback


class CaptionFallbackTests(unittest.TestCase):
    def test_broken_optional_model_keeps_mobile_reading(self):
        image = np.zeros((100, 800, 3), dtype=np.uint8)
        lines = [(280, 30, 520, 70, '主爷多心了', .88)]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / 'models/ocr/ch_PP-OCRv5_rec_server.onnx'
            model.parent.mkdir(parents=True)
            model.touch()
            with patch('app.services.subtitles.ocr.BACKEND_DIR', root), \
                 patch('app.services.subtitles.ocr.read_lines', return_value=lines), \
                 patch('app.services.subtitles.ocr.get_reader', side_effect=RuntimeError('broken')) as load:
                self.assertEqual(read_caption_fallback(Mock(), image), lines)
                self.assertEqual(read_caption_fallback(Mock(), image), lines)
                self.assertEqual(load.call_count, 1)

    def test_aligned_strokes_allow_reading_but_bright_background_does_not(self):
        mask = np.zeros((100, 800), dtype=np.uint8)
        self.assertFalse(has_caption_strokes(mask))
        cv2.rectangle(mask, (80, 10), (700, 90), 255, -1)
        self.assertFalse(has_caption_strokes(mask))
        mask[:] = 0
        for x in (280, 360, 440):
            cv2.rectangle(mask, (x, 35), (x+25, 65), 255, 2)
        self.assertTrue(has_caption_strokes(mask))

    def test_absent_server_is_not_downloaded(self):
        image = np.zeros((100, 800, 3), dtype=np.uint8)
        lines = [(280, 30, 520, 70, '主爷多心了', .88)]
        with TemporaryDirectory() as directory, \
             patch('app.services.subtitles.ocr.BACKEND_DIR', Path(directory)), \
             patch('app.services.subtitles.ocr.read_lines', return_value=lines), \
             patch('app.services.subtitles.ocr.get_reader') as load:
            self.assertEqual(read_caption_fallback(Mock(), image), lines)
            load.assert_not_called()

    def test_complete_pixel_read_can_improve_uncertain_text_but_not_drop_tail(self):
        image = np.zeros((100, 800, 3), dtype=np.uint8)
        lines = [(280, 30, 520, 70, '主爷多心了', .88)]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / 'models/ocr/ch_PP-OCRv5_rec_server.onnx'
            model.parent.mkdir(parents=True)
            model.touch()
            with patch('app.services.subtitles.ocr.BACKEND_DIR', root), \
                 patch('app.services.subtitles.ocr.read_lines', return_value=lines), \
                 patch('app.services.subtitles.ocr.get_reader', return_value=Mock()), \
                 patch('app.services.subtitles.ocr.read_known_bands', return_value=[('王爷多心了', .958), ('王爷多心了', .99)]) as recognize:
                self.assertEqual(read_caption_fallback(Mock(), image)[0][4], '王爷多心了')
                recognize.return_value = [('王爷多心', .99), ('王爷多心', .99)]
                self.assertEqual(read_caption_fallback(Mock(), image)[0][4], '主爷多心了')

    def test_uncertain_latin_texture_is_not_recovered_as_a_chinese_caption(self):
        with patch('app.services.subtitles.ocr.read_lines', return_value=[(0, 0, 50, 20, 'ABC', .99)]):
            self.assertEqual(read_caption_fallback(Mock(), np.zeros((50, 100, 3), dtype=np.uint8)), [])
