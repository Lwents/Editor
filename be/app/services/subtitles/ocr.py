"""PP-OCRv5 readers and subtitle-region discovery (RapidAI/RapidOCR)."""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
import json
import pickle
import re
import unicodedata
from threading import Lock

_inference_lock = Lock()

from app.core import BACKEND_DIR


def text_key(text: str) -> str:
    return ''.join(c.casefold() for c in unicodedata.normalize('NFC', text) if c.isalnum())


def reference_agrees(text: str, reference: str, lines: list[str]) -> bool:
    key = text_key(text)
    if len(key) < 4:
        return False
    if key in reference:
        return True
    # OCR may lose an accent. Use anchored near-matches to locate a region;
    # this does not replace its recognized text with the ASR transcript.
    for line in lines:
        if not .75 * len(key) <= len(line) <= 6 * len(key):
            continue
        matcher = SequenceMatcher(None, key, line, autojunk=False)
        anchor = matcher.find_longest_match()
        if anchor.size < max(3, min(8, len(key)//3)):
            continue
        start = max(0, anchor.b-anchor.a)
        if any(SequenceMatcher(None, key, line[start:start+len(key)+delta]).ratio() >= .88
               for delta in (-2, -1, 0, 1, 2)):
            return True
    return False


def recognition_language(work_dir: Path) -> str:
    try:
        language = json.loads((work_dir / 'asr-metadata.json').read_text(encoding='utf-8')).get('language', '')
    except (OSError, ValueError):
        language = ''
    if language in {'zh', 'ja', 'yue'}:
        return 'ch'
    if language == 'vi':
        return 'vi'
    mapping = {'ko': 'korean', 'th': 'th', 'ar': 'arabic', 'fa': 'arabic', 'ru': 'cyrillic', 'uk': 'cyrillic', 'hi': 'devanagari', 'el': 'el'}
    if language in mapping:
        return mapping[language]
    if language:
        return 'latin'
    try:
        text = (work_dir / 'subtitles.source.srt').read_text(encoding='utf-8-sig')
    except OSError:
        return 'ch'
    if re.search(r'[\u3400-\u9fff\u3040-\u30ff]', text):
        return 'ch'
    return 'latin'


@lru_cache(maxsize=3)
def get_reader(language: str, *, accurate: bool = False):
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
    reader = RapidOCR(params={
        'Global.model_root_dir': str(BACKEND_DIR / 'models' / 'ocr'),
        'Global.use_cls': False,
        'Global.log_level': 'error',
        # Keep uncertain detector results available to the caller. Ordinary
        # reads still require .60; Gemini image review can inspect weaker
        # caption evidence rather than silently turning it into a blank gap.
        'Global.text_score': .40,
        'Det.ocr_version': OCRVersion.PPOCRV5,
        'Det.model_type': ModelType.MOBILE,
        'Det.limit_type': 'max',
        'Det.limit_side_len': 1280,
        'Rec.ocr_version': OCRVersion.PPOCRV5,
        'Rec.model_type': ModelType.SERVER if accurate else ModelType.MOBILE,
        'Rec.lang_type': LangRec('latin' if language == 'vi' else language),
        'EngineConfig.onnxruntime.intra_op_num_threads': 4,
        'EngineConfig.onnxruntime.inter_op_num_threads': 1,
    })
    if language == 'vi':
        try:
            from app.services.subtitles.vietnamese import get_predictor
            reader.vietnamese = get_predictor()
        except (ImportError, OSError, RuntimeError, ValueError, EOFError, pickle.UnpicklingError) as exc:
            print(f'[OCR] VietOCR unavailable; retaining Latin OCR and review hints: {exc}', flush=True)
    return reader


def read_lines(reader, image, minimum=.60, *, caption_region=False):
    with _inference_lock:
        result = reader(image)
    if result.boxes is None or result.txts is None:
        return []
    lines = []
    for box, text, confidence in zip(result.boxes, result.txts, result.scores):
        text = unicodedata.normalize('NFC', text.strip())
        letters = sum(c.isalpha() for c in text)
        # A confirmed subtitle band can contain a complete one-character reply
        # (是 / 好 / 嗯). Keep the stricter rule when discovering unknown bands.
        short_caption = caption_region and letters == 1 and float(confidence) >= .85
        if float(confidence) < minimum or (letters < 2 and not short_caption):
            continue
        x1, y1 = map(float, box.min(axis=0))
        x2, y2 = map(float, box.max(axis=0))
        lines.append((x1, y1, x2, y2, text, float(confidence)))
    # Accents make neighbouring word boxes start at different y coordinates.
    # Group by their vertical centres before ordering words from left to right.
    rows = []
    for line in sorted(lines, key=lambda row: ((row[1] + row[3]) / 2, row[0])):
        middle = (line[1] + line[3]) / 2
        match = next((row for row in rows if abs(middle - sum((r[1]+r[3])/2 for r in row)/len(row))
                      <= max(line[3]-line[1], max(r[3]-r[1] for r in row)) * .5), None)
        if match is None:
            rows.append([line])
        else:
            match.append(line)
    predictor = getattr(reader, 'vietnamese', None)
    if predictor is not None:
        from PIL import Image
        import cv2
        combined = []
        for row in rows:
            x1, y1 = min(r[0] for r in row), min(r[1] for r in row)
            x2, y2 = max(r[2] for r in row), max(r[3] for r in row)
            crop = image[max(0, int(y1)-3):min(image.shape[0], int(y2)+3),
                         max(0, int(x1)-3):min(image.shape[1], int(x2)+3)]
            if not crop.size:
                continue
            with _inference_lock:
                text, probability = predictor.predict(Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)), return_prob=True)
            letters = sum(c.isalpha() for c in text)
            valid_text = letters >= 2 or (caption_region and letters == 1 and probability is not None and probability >= .85)
            if probability is not None and probability >= minimum and valid_text:
                combined.append((x1, y1, x2, y2, unicodedata.normalize('NFC', text.strip()), float(probability)))
        return combined
    return [line for row in rows for line in sorted(row, key=lambda r: r[0])]


def read_known_band(reader, image):
    """Recognize an already located line without rerunning the detector."""
    with _inference_lock:
        result = reader.recognize_txt([image])
    if not result.txts or not result.scores:
        return '', 0.
    return unicodedata.normalize('NFC', result.txts[0].strip()), float(result.scores[0])


def discover_regions(capture, reader, count: int, width: int, height: int, references: str):
    """Find changing text bands anywhere on screen; exclude persistent logos."""
    import cv2
    observations = {}
    # Evenly distributed frames prevent the opening title from defining the ROI.
    positions = sorted({round(i * (count - 1) / max(1, min(23, count - 1))) for i in range(min(24, count))})
    for index in positions:
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if not ok:
            continue
        for x1, y1, x2, y2, text, score in read_lines(reader, frame):
            if x2 - x1 < width * .10 or y2 - y1 < max(6, height * .008):
                continue
            band = round((y1 + y2) / (2 * height) / .045)
            observations.setdefault(band, []).append((x1, y1, x2, y2, text, score))
    candidates = []
    reference = text_key(references)
    reference_lines = [text_key(line) for line in references.splitlines()
                       if line.strip() and not line.strip().isdigit() and '-->' not in line]
    for rows in observations.values():
        counts = Counter(text_key(row[4]) for row in rows)
        distinct = len(counts)
        dominant_text = max(counts, key=lambda key: (counts[key], len(key)))
        dominant = sum(n for key, n in counts.items()
                       if re.findall(r'\d+', key) == re.findall(r'\d+', dominant_text)
                       and (SequenceMatcher(None, key, dominant_text).ratio() >= .80
                            or (min(len(key), len(dominant_text)) >= 4
                                and (key in dominant_text or dominant_text in key)))) / len(rows)
        # A subtitle may stay unchanged on a very short clip. Accept it only
        # when the ASR agrees; a permanent logo must not become narration.
        agrees = any(reference_agrees(row[4], reference, reference_lines) for row in rows)
        if distinct < 2 and not agrees:
            continue
        if len(rows) >= 3 and dominant > .80 and not agrees:
            continue
        top = max(0, min(r[1] for r in rows) - height * .025)
        bottom = min(height, max(r[3] for r in rows) + height * .025)
        center = sum((r[0] + r[2]) / (2 * width) for r in rows) / len(rows)
        score = distinct + len(rows) * .10 + (2 if agrees else 0) - abs(center - .5)
        candidates.append((score, int(top), int(bottom)))
    candidates.sort(reverse=True)
    regions = []
    for _, top, bottom in candidates:
        overlapping = next((i for i, (a, b) in enumerate(regions) if min(b, bottom) > max(a, top)), None)
        if overlapping is None:
            regions.append((top, bottom))
        else:
            a, b = regions[overlapping]
            regions[overlapping] = (min(a, top), max(b, bottom))
        if len(regions) >= 4:
            break
    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
    return regions
