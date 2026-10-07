import tempfile
import unittest
from pathlib import Path

from PIL import ImageFont

from app.models.job import DubbingRequest
from app.services.subtitles.ass import (
    SUBTITLE_FONT_DIR, SUBTITLE_FONT_FILE, _wrap_subtitle_text, srt_to_positioned_ass,
)


class SubtitleLayoutTests(unittest.TestCase):
    def test_clause_boundary_keeps_screw_phrase_together(self):
        text = 'NHƯNG VIỆC THIẾU MỘT CON ỐC VÍT KHIẾN CỖ MÁY KHÔNG THỂ HOẠT ĐỘNG.'
        result = _wrap_subtitle_text(text, 42)
        self.assertEqual(result, 'NHƯNG VIỆC THIẾU MỘT CON ỐC VÍT\\NKHIẾN CỖ MÁY KHÔNG THỂ HOẠT ĐỘNG.')

    def test_real_font_width_distinguishes_equal_character_counts(self):
        font = ImageFont.truetype(str(SUBTITLE_FONT_DIR / SUBTITLE_FONT_FILE), 64)
        narrow = ' '.join(['III'] * 8)
        wide = ' '.join(['WWW'] * 8)
        budget = (font.getlength(narrow) + font.getlength(wide)) / 2
        self.assertNotIn(r'\N', _wrap_subtitle_text(narrow, budget, measure=font.getlength))
        self.assertIn(r'\N', _wrap_subtitle_text(wide, budget, measure=font.getlength))

    def test_long_cue_fits_two_lines_without_changing_text_or_time(self):
        text = 'NHƯNG VIỆC THIẾU MỘT CON ỐC VÍT KHIẾN CỖ MÁY KHÔNG THỂ HOẠT ĐỘNG. ' * 3
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / 'input.srt', Path(folder) / 'output.ass'
            source.write_text(f'1\n00:00:01,000 --> 00:00:05,000\n{text}\n', encoding='utf-8')
            srt_to_positioned_ass(source, target, 1280, 720, DubbingRequest(local_file_path='test.mp4'))
            content = target.read_text(encoding='utf-8')
        self.assertIn('WrapStyle: 2', content)
        cue = content.split('Dialogue: ')[1]
        self.assertIn('0:00:01.00,0:00:05.00', cue)
        self.assertIn(r'\fs', cue)
        self.assertEqual(cue.count(r'\N'), 1)
        displayed = cue.split('}', 1)[1].strip().replace(r'\N', ' ')
        self.assertEqual(displayed, text.strip())


if __name__ == '__main__':
    unittest.main()
