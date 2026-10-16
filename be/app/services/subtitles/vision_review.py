"""Verify uncertain burned-in captions against their actual video frames."""
from __future__ import annotations
from app.services.ai.provider_status import provider_name, error_prefix
from app.services.ai.web_provider import is_web_engine

import asyncio
import hashlib
import json
import math
import unicodedata
import re
from difflib import SequenceMatcher
from pathlib import Path

from app.services.subtitles.timing import SubtitleEvent
from app.services.persistence import atomic_json, read_json

VERSION = 13
IMAGE_BATCH_SIZE = 6


def _caption_key(text):
    normalized = unicodedata.normalize('NFKC', text)
    compact = re.sub(r'\s+', '', normalized)
    return compact if re.fullmatch(r'[\u3400-\u9fff，。！？、：；“”‘’…—]+', compact) else normalized


def _verified_neighbor_texts(events, item_id, verified):
    index = item_id - 1
    texts = set()
    for direction in (-1, 1):
        previous = index
        for distance in range(1, 4):
            other = index + direction * distance
            if not 0 <= other < len(events):
                break
            before, after = sorted((previous, other))
            if not 0 <= events[after].start - events[before].end <= .04:
                break
            previous = other
            evidence = verified.get(other + 1)
            if evidence and evidence.get('is_caption') and evidence.get('confidence', 0) >= .95:
                texts.add(_caption_key(evidence['text']))
    return texts


def _is_shorter_verified_fragment(events, item, verified):
    text = _caption_key(item['text'])
    return any(len(full) > len(text) and (full.startswith(text) or full.endswith(text))
               for full in _verified_neighbor_texts(events, item['id'], verified))


def _local_consensus(source, events, ids, regions, known_items=None):
    """Accept only unanimous high-confidence reads of the actual caption pixels.

    An empty read is inconclusive. Conflicting or truncated crops go to Gemini.
    Scores are OCR scores, not a claim of translation accuracy.
    """
    import cv2
    from app.services.subtitles.ocr import get_reader, read_lines, read_known_band
    valid = [r for r in (regions or []) if isinstance(r, dict)
             and 0 <= r.get('top', -1) < r.get('bottom', -1) <= 1]
    if not valid:
        return []
    top = min(r['top'] for r in valid)
    bottom = max(r['bottom'] for r in valid)
    cap = cv2.VideoCapture(str(source))
    reader = get_reader('ch')
    decisions = []
    try:
        for item_id in ids:
            event = events[item_id - 1]
            if not re.search(r'[\u3400-\u9fff]', event.text):
                continue
            cap.set(cv2.CAP_PROP_POS_MSEC, (event.start + event.end) * 500)
            ok, frame = cap.read()
            if not ok:
                continue
            height, width = frame.shape[:2]
            band = frame[int(height * top):int(height * bottom)]
            readings = []
            for crop in (band, band[:, width // 4:width * 3 // 4], band[:, width // 3:width * 2 // 3]):
                lines = read_lines(reader, crop, minimum=.4, caption_region=True)
                if len(lines) != 1 or lines[0][5] < .98:
                    break
                readings.append((_caption_key(lines[0][4]), lines[0][5]))
            text = readings[0][0] if readings else ''
            plausible_reply = len(text) > 1 or text in {'是', '好', '嗯', '对', '不', '别', '滚', '杀'}
            if len(readings) == 3 and plausible_reply and len({r[0] for r in readings}) == 1:
                decisions.append({'id': item_id, 'text': readings[0][0], 'is_caption': True,
                    'confidence': min(r[1] for r in readings), 'method': 'local_multiscale_ocr',
                    'reason': 'Ba kích thước ảnh đọc cùng chữ, mỗi điểm OCR >= .98.'})
        verified = {item['id']: item for item in [*(known_items or []), *decisions]}
        decisions = [item for item in decisions if not _is_shorter_verified_fragment(events, item, verified)]
        # Recheck shortened captions against their own pixels with the stronger
        # recognizer, without asking the detector to find the line again. Only
        # use an already installed model and corroborate all three crops with
        # a previously verified neighbouring caption; never copy its text.
        from app.core import BACKEND_DIR
        server = None
        if (BACKEND_DIR / 'models' / 'ocr' / 'ch_PP-OCRv5_rec_server.onnx').is_file():
            accepted = {item['id'] for item in decisions}
            for item_id in ids:
                raw = {'id': item_id, 'text': events[item_id - 1].text}
                if item_id in accepted or not _is_shorter_verified_fragment(events, raw, verified):
                    continue
                event = events[item_id - 1]
                cap.set(cv2.CAP_PROP_POS_MSEC, (event.start + event.end) * 500)
                ok, frame = cap.read()
                if not ok:
                    continue
                height, width = frame.shape[:2]
                band = frame[int(height * top):int(height * bottom)]
                server = server or get_reader('ch', accurate=True)
                readings = [read_known_band(server, band[:, int(width*(.5-fraction/2)):int(width*(.5+fraction/2))])
                            for fraction in (.20, .25, .333)]
                texts = {_caption_key(text) for text, score in readings}
                if len(texts) != 1 or min(score for text, score in readings) < .98:
                    continue
                text = next(iter(texts))
                if not (2 <= len(text) <= 4 and re.fullmatch(r'[\u3400-\u9fff]+', text)):
                    continue
                # The independently read text must have source-image support
                # nearby, while a shorter reading remains inconclusive.
                probe = {'id': item_id, 'text': text}
                if _is_shorter_verified_fragment(events, probe, verified):
                    continue
                if text not in _verified_neighbor_texts(events, item_id, verified):
                    continue
                decisions.append({'id': item_id, 'text': text, 'is_caption': True,
                    'confidence': min(score for _, score in readings), 'method': 'local_server_recognition',
                    'reason': 'Ba vùng chữ được PP-OCRv5 Server đọc trùng nhau >= .98; có ảnh phụ đề lân cận đã xác minh.'})
        return decisions
    finally:
        cap.release()


def _caption_gap(events, index):
    if index <= 0:
        return None
    previous, current = events[index - 1], events[index]
    # Short names may be misread as wholly different glyphs on a bright
    # background. Both endpoint images and EVERY gap frame must agree before
    # apply_review can join them; raw OCR similarity is not visual evidence.
    short_cjk = all(1 <= len(e.text) <= 4 and re.fullmatch(r'[\u3400-\u9fff]+', e.text)
                    for e in (previous, current))
    if (previous.text == current.text or short_cjk) and .04 < current.start - previous.end <= .50:
        return {'start': previous.end, 'end': current.start}
    return None


def _verify_caption_gaps(source, events, items, regions):
    """Bridge only gaps whose every frame independently reads the same caption."""
    import cv2
    from app.services.subtitles.ocr import get_reader, read_lines
    valid = [r for r in (regions or []) if isinstance(r, dict)
             and 0 <= r.get('top', -1) < r.get('bottom', -1) <= 1]
    if not valid:
        return items
    verified = {item['id']: dict(item) for item in items}
    cap = cv2.VideoCapture(str(source))
    reader = None
    from app.services.subtitles.frame_processing import FrameCache
    frame_cache = FrameCache(cap)
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 0:
            return items
        for item_id, current in verified.items():
            gap = _caption_gap(events, item_id - 1)
            previous = verified.get(item_id - 1)
            if (not gap or not previous or current.get('local_gap_checked')
                    or not all(d.get('is_caption') and d.get('confidence', 0) >= .95
                               for d in (previous, current))
                    or _caption_key(current['text']) != _caption_key(previous['text'])):
                continue
            reader = reader or get_reader('ch')
            target = _caption_key(current['text'])
            matches = []
            for frame_index in range(math.ceil(gap['start'] * fps), math.ceil(gap['end'] * fps)):
                ok, frame = frame_cache.read_at(frame_index)
                if not ok:
                    matches.append(False)
                    break
                height, width = frame.shape[:2]
                band = frame[int(height * min(r['top'] for r in valid)):
                             int(height * max(r['bottom'] for r in valid))]
                agreed = True
                for crop in (band, band[:, width // 4:width * 3 // 4]):
                    lines = read_lines(reader, crop, minimum=.4, caption_region=True)
                    if (len(lines) != 1 or lines[0][5] < .98
                            or _caption_key(lines[0][4]) != target):
                        agreed = False
                        break
                matches.append(agreed)
                if not agreed:
                    break
            if matches and all(matches):
                current['gap_is_caption'] = True
                current['gap_method'] = 'local_every_frame_ocr'
            current['local_gap_checked'] = True
        return sorted(verified.values(), key=lambda item: item['id'])
    finally:
        cap.release()


def _safe_context(context):
    cleaned = dict(context)
    names = []; notes = list(context.get('translation_notes', [])) if isinstance(context.get('translation_notes'), list) else []
    for character in context.get('characters', []):
        if not isinstance(character, dict):
            continue
        name = str(character.get('name') or character.get('canonical_name') or '')
        if re.search(r'[\u3400-\u9fff]', name):
            notes.append(f'Tên chữ gốc được ảnh xác nhận: {name}. Chọn tên tiếng Việt nhất quán, không dịch nghĩa tên.')
        elif name:
            names.append(character)
    cleaned['characters'] = names
    if notes:
        cleaned['translation_notes'] = notes
    return cleaned


def merge_caption_fragments(events: list[SubtitleEvent]) -> list[SubtitleEvent]:
    """Merge only adjacent identical visual captions, preserving their union."""
    result: list[SubtitleEvent] = []
    for event in events:
        if result and _caption_key(result[-1].text) == _caption_key(event.text) and 0 <= event.start - result[-1].end <= .04:
            previous = result[-1]
            result[-1] = SubtitleEvent(previous.start, event.end, previous.text)
        else:
            result.append(event)
    return result


def _candidate_ids(events, report):
    cues = report.get('cues', [])
    result = []
    for i, event in enumerate(events):
        evidence = next((cue for cue in cues if abs(float(cue.get('start', -10)) - event.start) < .06 and abs(float(cue.get('end', -10)) - event.end) < .06), {})
        single_cjk = bool(re.fullmatch(r'[\u3400-\u9fff]', _caption_key(event.text)))
        if single_cjk or evidence.get('needs_review') or float(evidence.get('confidence', 1)) < .85 or event.end - event.start < .3:
            result.append(i + 1)
    # High-confidence OCR can alternate one glyph (爭/争, 王/主) while the
    # displayed caption stays unchanged. Verify both images before merging;
    # don't translate that flicker as two repeated lines of dialogue.
    for i, (before, after) in enumerate(zip(events, events[1:])):
        before_key = re.sub(r'\s+', '', unicodedata.normalize('NFKC', before.text))
        after_key = re.sub(r'\s+', '', unicodedata.normalize('NFKC', after.text))
        if (0 <= after.start - before.end <= .04
                and before.text != after.text
                and min(len(before.text), len(after.text)) >= 4
                and SequenceMatcher(None, before.text, after.text).ratio() >= .80):
            result.extend((i + 1, i + 2))
        # A confident one-glyph reading can still have missed the second glyph
        # of an address/name (主 -> 主子). Check both actual frames before
        # treating the prefix as a separate word or translating it as "Chủ...".
        if (0 <= after.start - before.end <= .04 and before_key != after_key
                and all(re.fullmatch(r'[\u3400-\u9fff]+', key) for key in (before_key, after_key))
                and min(len(before_key), len(after_key)) <= 3
                and max(len(before_key), len(after_key)) <= 8
                and (before_key.startswith(after_key) or after_key.startswith(before_key)
                     or before_key.endswith(after_key) or after_key.endswith(before_key))):
            result.extend((i + 1, i + 2))
        if _caption_gap(events, i + 1):
            result.extend((i + 1, i + 2))
    selected = set(result)
    for item_id in list(selected):
        event = events[item_id - 1]
        for neighbor in (item_id - 1, item_id + 1):
            if not 1 <= neighbor <= len(events):
                continue
            other = events[neighbor - 1]
            gap = max(event.start, other.start) - min(event.end, other.end)
            if 0 <= gap <= .04 and SequenceMatcher(None, event.text, other.text).ratio() >= .7:
                selected.add(neighbor)
    return sorted(selected)


def _frame_sheet(source: Path, events, ids, target: Path, regions=None, include_gaps=True, include_full_frame=False):
    import cv2
    from PIL import Image, ImageDraw
    cap = cv2.VideoCapture(str(source))
    tiles = []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    valid_regions = [region for region in (regions or []) if isinstance(region, dict)
                     and 0 <= region.get('top', -1) < region.get('bottom', -1) <= 1]
    top = min((region['top'] for region in valid_regions), default=.75)
    bottom = max((region['bottom'] for region in valid_regions), default=.96)

    def caption_tile(image, label):
        width, height = image.size
        # OCR verification needs the caption band, not the entire movie scene.
        # Retain the full width and a centre detail for tiny names/replies.
        band = image.crop((0, int(height * top), width, min(height, math.ceil(height * bottom))))
        full = band.copy(); full.thumbnail((1000, 210))
        detail = band.crop((width // 4, 0, width * 3 // 4, band.height))
        detail.thumbnail((1000, 210))
        tile = Image.new('RGB', (1000, 55 + full.height + detail.height), 'white')
        draw = ImageDraw.Draw(tile)
        draw.text((8, 8), label + ' | full caption band', fill='black')
        tile.paste(full, (0, 30))
        draw.text((8, 33 + full.height), 'Centre detail (full caption above)', fill='black')
        tile.paste(detail, (0, 55 + full.height))
        if include_full_frame:
            scene = image.copy()
            scene.thumbnail((1000, 562))
            evidence = Image.new('RGB', (1000, scene.height + 30 + tile.height), 'white')
            ImageDraw.Draw(evidence).text((8, 8), label + ' | full frame (check other caption positions)', fill='black')
            evidence.paste(scene, (0, 30))
            evidence.paste(tile, (0, scene.height + 30))
            return evidence
        return tile

    try:
        # A gap can cross batch boundaries. Include the previous endpoint
        # image in this sheet even if its decision was checked in an earlier
        # batch; raw OCR text alone is not evidence of the displayed caption.
        evidence_ids = set(ids)
        for item_id in ids:
            if include_gaps and _caption_gap(events, item_id - 1):
                evidence_ids.add(item_id - 1)
        for item_id in sorted(evidence_ids):
            event = events[item_id - 1]
            at = (event.start + event.end) / 2
            cap.set(cv2.CAP_PROP_POS_MSEC, at * 1000)
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f'Không đọc được frame để kiểm tra câu {item_id}.')
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            tiles.append(caption_tile(image, f'ID {item_id} | {at:.3f}s'))
            gap = _caption_gap(events, item_id - 1) if include_gaps and item_id in ids else None
            if gap:
                # Evidence must include EVERY missing frame before a gap can
                # be bridged, rather than guessing from its two neighbours.
                for frame_index in range(math.ceil(gap['start'] * fps), math.ceil(gap['end'] * fps)):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                    ok, gap_frame = cap.read()
                    if not ok:
                        raise RuntimeError(f'Không đọc được frame trống trước câu {item_id}.')
                    gap_image = Image.fromarray(cv2.cvtColor(gap_frame, cv2.COLOR_BGR2RGB))
                    tiles.append(caption_tile(gap_image,
                        f'ID {item_id} GAP BEFORE | {frame_index / fps:.3f}s'))
    finally:
        cap.release()
    # Very tall single-column sheets shrink short Chinese captions to a few
    # pixels when the provider resizes the image. Keep every evidence frame,
    # arranged in balanced columns with its explicit ID/time label.
    total_height = sum(tile.height for tile in tiles)
    columns = max(1, min(3, round(math.sqrt(total_height / 1000))))
    heights = [0] * columns
    positions = []
    for tile in tiles:
        column = min(range(columns), key=lambda i: heights[i])
        positions.append((column * 1000, heights[column]))
        heights[column] += tile.height
    sheet = Image.new('RGB', (1000 * columns, max(heights)), 'white')
    for tile, position in zip(tiles, positions):
        sheet.paste(tile, position)
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, quality=90)


def apply_review(events, ids, items):
    """Only apply complete, high-confidence image-supported decisions."""
    if not isinstance(items, list) or len(items) != len(ids):
        raise RuntimeError(f'{provider_name()} trả thiếu câu kiểm tra hình ảnh.')
    decisions = {}
    for item in items:
        item_id = item.get('id') if isinstance(item, dict) else None
        if type(item_id) is not int or item_id not in ids or item_id in decisions:
            raise RuntimeError(f'{provider_name()} trả sai ID kiểm tra hình ảnh.')
        decision = dict(item)
        decision['source'] = item.get('text')
        if type(decision.get('is_caption')) is not bool:
            raise RuntimeError(f'{provider_name()} trả sai kết quả kiểm tra hình ảnh.')
        confidence = decision.get('confidence')
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise RuntimeError(f'{provider_name()} trả sai độ chắc chắn kiểm tra hình ảnh.')
        source = decision.get('source')
        if not isinstance(source, str):
            raise RuntimeError(f'{provider_name()} trả sai chữ gốc kiểm tra hình ảnh.')
        decisions[item_id] = decision
    result = []
    for item_id, event in enumerate(events, 1):
        decision = decisions.get(item_id)
        if decision and decision['confidence'] >= .95 and not decision['is_caption']:
            continue
        text = event.text
        if decision and decision['is_caption'] and decision['confidence'] >= .9 and decision['source'].strip():
            text = decision['source'].strip()
        previous_decision = decisions.get(item_id - 1)
        if (_caption_gap(events, item_id - 1) and result and decision and previous_decision
                and decision.get('gap_is_caption') is True
                and decision['is_caption'] and previous_decision['is_caption']
                and min(decision['confidence'], previous_decision['confidence']) >= .95
                and result[-1].text == text):
            previous = result.pop()
            result.append(SubtitleEvent(previous.start, event.end, text))
        else:
            result.append(SubtitleEvent(event.start, event.end, text))
    return merge_caption_fragments(result), decisions


async def review_visible_captions(source: Path, events, work_dir: Path, progress):
    from app.services.subtitles.mode import text_only_gemini
    text_only = text_only_gemini.get()
    from app.services.ai.gemini_web import GeminiWebError, GeminiWebRefusal, GeminiWebTimeout, GeminiWebVerificationRequired, GeminiWebModelError
    from app.services.ai.web_provider import browser
    report_file = work_dir / 'subtitles.recognition.json'
    report = json.loads(report_file.read_text(encoding='utf-8')) if report_file.is_file() else {}
    ids = _candidate_ids(events, report)
    if not ids:
        return merge_caption_fragments(events), {}
    from app.services.ai.asr_cache import digest
    video_digest = await asyncio.to_thread(digest, source)
    cache_input = {'events': [(e.start, e.end, e.text) for e in events], 'video_digest': video_digest, 'ids': ids}
    def cache_fingerprint(version, selected_ids=None):
        data = {'version': version, **cache_input}
        if selected_ids is not None:
            data['ids'] = selected_ids
        return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()
    fingerprint = cache_fingerprint(VERSION)
    cache = work_dir / 'subtitles.vision-review.json'
    items = []; context = {}
    local_attempted=set()
    if cache.is_file() or cache.with_suffix(cache.suffix+'.bak').is_file():
        stored = read_json(cache,lambda value:isinstance(value,dict))
        try:
            saved = stored.get('items', [])
            saved_ids = [item['id'] for item in saved]
            compatible = {cache_fingerprint(v) for v in (6, 7, 8, 9, 10, 11, 12, VERSION)}
            compatible.update(cache_fingerprint(v, saved_ids) for v in (6, 7, 8, 9, 10, 11, 12, VERSION))
            if stored.get('fingerprint') in compatible:
                apply_review(events, saved_ids, saved)
                items = [dict(item, method=item.get('method', 'gemini_image_review')) for item in saved
                         if item['id'] in ids]
                if stored.get('fingerprint') != fingerprint:
                    verified = {item['id']: item for item in items}
                    # Legacy prompts exposed the suspect OCR text. Re-read
                    # shortened neighbour captions without that anchoring hint;
                    # do not fill missing glyphs by guessing from neighbours.
                    items = [item for item in items if not (
                        item.get('is_caption') and _is_shorter_verified_fragment(events, item, verified))]
                # Use earlier image decisions; rebuild narrative memory from source.
                context = stored.get('context', {}) if stored.get('fingerprint') == fingerprint else {}
                if stored.get('fingerprint') == fingerprint:
                    local_attempted={i for i in stored.get('local_attempted',[]) if type(i) is int and i in ids}
        except (ValueError, TypeError, RuntimeError, KeyError):
            items = []; context = {}
    pending_ids = [i for i in ids if i not in {item['id'] for item in items}]
    from app.services.subtitles.caption_cache import restore_decisions, save_decisions
    await asyncio.to_thread(save_decisions, video_digest, events, items, report.get('regions'))
    reused = await asyncio.to_thread(restore_decisions, video_digest, events, pending_ids, report.get('regions'))
    if reused:
        items.extend(reused)
        progress(f'Dùng lại {len(reused)} kết quả đối chiếu chữ đã có bằng chứng', 66)
        pending_ids = [i for i in ids if i not in {item['id'] for item in items}]
    local_pending=[i for i in pending_ids if i not in local_attempted]
    if local_pending and report.get('language_model') == 'ch':
        progress(f'OCR đọc lại chữ ở ba kích thước ảnh ({len(items)}/{len(ids)} đoạn)', 66)
        items.extend(await asyncio.to_thread(_local_consensus, source, events, local_pending, report.get('regions'), items))
        local_attempted.update(local_pending)
    items.sort(key=lambda item: item['id'])
    pending_ids = [i for i in ids if i not in {item['id'] for item in items}]
    # Persist local decisions too, even if the following Gemini request fails.
    if items or local_attempted:
        await asyncio.to_thread(save_decisions, video_digest, events, items, report.get('regions'))
        atomic_json(cache, {'fingerprint': fingerprint, 'items': items, 'context': context, 'local_attempted': sorted(local_attempted)})
    for start in range(0, 0 if text_only else len(pending_ids), IMAGE_BATCH_SIZE):
        batch_ids = pending_ids[start:start + IMAGE_BATCH_SIZE]
        completed = len(items)
        progress(f'{provider_name()} kiểm tra chữ còn chưa rõ ({completed}/{len(ids)} đoạn)', 66)
        sheet = work_dir / f'caption-evidence-{start // IMAGE_BATCH_SIZE + 1}.jpg'
        await asyncio.to_thread(_frame_sheet, source, events, batch_ids, sheet, report.get('regions'))
        payload = {'current_items': [{'id': i, 'source': 'Chép toàn bộ phụ đề nhìn thấy trong ô ảnh này.', 'time': [events[i - 1].start, events[i - 1].end], 'gap_before': _caption_gap(events, i - 1)} for i in batch_ids]}
        prompt = (
            'Chép lại chữ tiếng Trung nhìn thấy trong ảnh đính kèm, theo từng ô ID. Mỗi ô có dòng chữ đầy đủ và phần giữa phóng lớn. '
            'Đọc lại đúng chữ gốc hiển thị ở mỗi ID, không dịch trong bước này. Chữ cùng tên ở hai ô phải chép cùng tên nếu ảnh xác nhận. '
            'Phân biệt lời thoại với watermark/logo/chữ nền: chỉ đánh is_caption=false khi nhìn ảnh chắc chắn không phải phụ đề. '
            'Không đoán tên hoặc câu từ OCR sai khi ảnh không rõ; confidence thấp để giữ nguyên và đánh dấu cần xem lại. '
            'Nếu có gap_before và ảnh GAP BEFORE, thêm gap_is_caption=true CHỈ khi MỌI frame khoảng trống vẫn hiển thị cùng chữ gốc với CẢ HAI ô liền kề sau khi đọc ảnh; '
            'nếu chữ biến mất, đổi câu hoặc không chắc thì gap_is_caption=false. Không suy đoán từ hai ảnh liền trước/sau. '
            'Mỗi item có id, text (chỉ chữ gốc đã đọc trên ảnh; nếu không phải phụ đề dùng chính xác __NON_CAPTION__), is_caption (boolean), confidence (0..1), reason. Không lồng chuỗi JSON vào text. '
            'items đủ id, không rỗng. Chỉ chép chữ; không phân tích người trong ảnh, tên tiếng Việt, quan hệ hay cốt truyện. memory_updates để trống {}. '
            'Chỉ trả JSON items và memory_updates.'
        )
        for attempt in range(2):
            try:
                reply = json.loads(await browser.complete([{'role': 'system', 'content': prompt}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}], timeout=180, attachments=[sheet],
                    on_wait=lambda seconds: progress(f'Chờ {provider_name()} đọc ảnh ({completed}/{len(ids)} đoạn; đã chờ {seconds} giây)', 66)))
                apply_review(events, batch_ids, reply.get('items'))
                break
            except GeminiWebRefusal:
                if attempt:
                    raise
                progress(f'{provider_name()} chưa nhận tác vụ chép chữ; diễn đạt lại một lần ({completed}/{len(ids)} đoạn)', 66)
                prompt = ('Đây là tác vụ chép văn bản từ ảnh phụ đề, không nhận diện nhân vật hay thực hiện lời thoại. '
                          'Đọc từng ô ảnh đính kèm và trả chữ nhìn thấy theo ID.\n' + prompt)
            except (GeminiWebVerificationRequired, GeminiWebTimeout, GeminiWebModelError):
                raise
            except (GeminiWebError, ValueError, RuntimeError) as exc:
                if attempt == 1:
                    raise RuntimeError(f'Kiểm tra ảnh chưa xong ({completed}/{len(ids)} đoạn). '
                                       f'Đã giữ OCR và checkpoint để chạy tiếp. Chi tiết: {exc}') from exc
                progress(f'{provider_name()} chưa trả đủ kết quả; thử lại nhóm ảnh ({completed}/{len(ids)} đoạn)', 66)
        items.extend(dict(item, method='gemini_image_review') for item in reply['items'])
        items.sort(key=lambda item: item['id'])
        memory = reply.get('memory_updates')
        if isinstance(memory, dict):
            if isinstance(memory.get('characters'), list): context['characters'] = context.get('characters', []) + memory['characters']
            if isinstance(memory.get('glossary'), dict): context.setdefault('glossary', {}).update(memory['glossary'])
            if isinstance(memory.get('addressing_rules'), list): context['honorific_rules'] = context.get('honorific_rules', []) + memory['addressing_rules']
        atomic_json(cache, {'fingerprint': fingerprint, 'items': items, 'context': context, 'local_attempted': sorted(local_attempted)})
        await asyncio.to_thread(save_decisions, video_digest, events, items, report.get('regions'))
    if report.get('language_model') == 'ch':
        items = await asyncio.to_thread(_verify_caption_gaps, source, events, items, report.get('regions'))
        atomic_json(cache, {'fingerprint': fingerprint, 'items': items, 'context': context, 'local_attempted': sorted(local_attempted)})
    provisional_ids = set(pending_ids) if text_only else set()
    if provisional_ids:
        items = [*items, *({'id': i, 'text': events[i-1].text, 'is_caption': True,
                           'confidence': 0, 'method': 'unverified_ocr'} for i in provisional_ids)]
        context = {**context, 'translation_notes': [*context.get('translation_notes', []),
            f'{len(provisional_ids)} đoạn chưa đối chiếu ảnh vì người dùng chọn chỉ gửi văn bản. '
            'Giữ chữ OCR và đánh dấu cần kiểm tra, không suy đoán tên/người nói từ ảnh chưa đọc.']}
    reviewed, decisions = apply_review(events, ids, items)
    verified_cues = []
    for event in reviewed:
        original_ids = [i for i, original in enumerate(events, 1) if original.start < event.end and original.end > event.start]
        original_cues = [cue for cue in report.get('cues', []) if cue['start'] < event.end and cue['end'] > event.start]
        checked = [decisions[i] for i in original_ids if i in decisions]
        needs_review = any(cue.get('needs_review') for cue in original_cues)
        if checked and len(checked) == len(original_ids):
            needs_review = not all(d['is_caption'] and d['confidence'] >= .9 for d in checked)
        verified_cues.append({'start': event.start, 'end': event.end, 'text': event.text,
                           'image_verification_skipped': bool(provisional_ids.intersection(original_ids)),
                              'confidence': min((cue.get('confidence', 0) for cue in original_cues), default=0),
                              'needs_review': needs_review,
                              'vision_checked': any(not d.get('method', '').startswith('local_') for d in checked),
                              'local_review_checked': any(d.get('method', '').startswith('local_') for d in checked),
                              'review_methods': sorted({d.get('method', 'gemini_image_review') for d in checked})})
    verified_report = work_dir / 'subtitles.recognition.verified.json'
    pending = verified_report.with_suffix('.pending.json')
    pending.write_text(json.dumps({**report, 'cues': verified_cues, 'vision_review': True,
                                  'vision_review_complete': not provisional_ids,
                                  'text_only_gemini': text_only}, ensure_ascii=False, indent=2), encoding='utf-8'); pending.replace(verified_report)
    local_count = sum(item.get('method', '').startswith('local_') for item in items)
    if provisional_ids:
        progress(f'Đọc chữ gốc xong; {len(provisional_ids)} đoạn chưa đối chiếu ảnh, sẽ đánh dấu cần kiểm tra', 66)
    else:
        progress(f'Đã kiểm tra {len(ids)}/{len(ids)} đoạn chữ ({local_count} bằng OCR, {len(ids)-local_count} bằng {provider_name()})', 66)
    return reviewed, _safe_context(context)
