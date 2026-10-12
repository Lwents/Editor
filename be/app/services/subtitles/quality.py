"""Explain recognition uncertainty without claiming a measured accuracy score."""
from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
import json

from app.services.subtitles.ocr import text_key
from app.services.subtitles.timing import parse_srt


def recognition_report(work_dir: Path) -> dict:
    try:
        report = json.loads((work_dir / 'subtitles.recognition.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'assessed': False, 'cues': [], 'review_count': 0,
                'message': 'Chưa có đánh giá OCR; phụ đề có thể đang dùng mốc lời thoại.'}
    try:
        speech = parse_srt(work_dir / 'subtitles.source.srt')
    except OSError:
        speech = []
    for cue in report.get('cues', []):
        nearby = [e for e in speech if e.start < cue['end'] + .15 and e.end > cue['start'] - .15]
        recognized = text_key(' '.join(e.text for e in nearby))
        displayed = text_key(cue['text'])
        reasons = []
        if cue.get('needs_review'):
            reasons.append('Chữ mờ hoặc kết quả giữa các khung hình chưa ổn định')
        # Subtitles can legitimately paraphrase speech. This is a review hint,
        # never permission to replace displayed text with an ASR guess.
        if recognized and len(displayed) >= 4:
            agreement = SequenceMatcher(None, displayed, recognized).ratio()
            cue['speech_agreement'] = round(agreement, 3)
            if agreement < .50 and displayed not in recognized:
                reasons.append('Chữ trên hình khác kết quả nhận diện lời thoại')
        cue['needs_review'] = bool(reasons)
        cue['review_reasons'] = reasons
    report['assessed'] = True
    report['review_count'] = sum(bool(c.get('needs_review')) for c in report.get('cues', []))
    return report
