"""Verify uncertain burned-in captions against their actual video frames."""
from __future__ import annotations

import asyncio
import hashlib
import json
import unicodedata
import re
from difflib import SequenceMatcher
from pathlib import Path

from app.services.subtitles.timing import SubtitleEvent

VERSION = 2


def _safe_context(context):
    cleaned = dict(context)
    names = []; notes = []
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
        if result and unicodedata.normalize('NFKC', result[-1].text) == unicodedata.normalize('NFKC', event.text) and 0 <= event.start - result[-1].end <= .04:
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
        if evidence.get('needs_review') or float(evidence.get('confidence', 1)) < .85 or event.end - event.start < .3:
            result.append(i + 1)
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


def _frame_sheet(source: Path, events, ids, target: Path):
    import cv2
    from PIL import Image, ImageDraw
    cap = cv2.VideoCapture(str(source))
    tiles = []
    try:
        for item_id in ids:
            event = events[item_id - 1]
            at = (event.start + event.end) / 2
            cap.set(cv2.CAP_PROP_POS_MSEC, at * 1000)
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f'Không đọc được frame để kiểm tra câu {item_id}.')
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            # Keep the whole scene plus a larger caption strip for legibility.
            width, height = image.size
            scene = image.copy(); scene.thumbnail((720, 405))
            strip = image.crop((0, int(height * .65), width, height)); strip.thumbnail((1000, 210))
            tile = Image.new('RGB', (1000, scene.height + strip.height + 35), 'white')
            ImageDraw.Draw(tile).text((8, 8), f'ID {item_id} | {at:.3f}s', fill='black')
            tile.paste(scene, (0, 30)); tile.paste(strip, (0, scene.height + 35))
            tiles.append(tile)
    finally:
        cap.release()
    sheet = Image.new('RGB', (1000, sum(tile.height for tile in tiles)), 'white')
    y = 0
    for tile in tiles:
        sheet.paste(tile, (0, y)); y += tile.height
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, quality=90)


def apply_review(events, ids, items):
    """Only apply complete, high-confidence image-supported decisions."""
    if not isinstance(items, list) or len(items) != len(ids):
        raise RuntimeError('Gemini trả thiếu câu kiểm tra hình ảnh.')
    decisions = {}
    for item in items:
        item_id = item.get('id') if isinstance(item, dict) else None
        if type(item_id) is not int or item_id not in ids or item_id in decisions:
            raise RuntimeError('Gemini trả sai ID kiểm tra hình ảnh.')
        decision = dict(item)
        decision['source'] = item.get('text')
        if type(decision.get('is_caption')) is not bool:
            raise RuntimeError('Gemini trả sai kết quả kiểm tra hình ảnh.')
        confidence = decision.get('confidence')
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise RuntimeError('Gemini trả sai độ chắc chắn kiểm tra hình ảnh.')
        source = decision.get('source')
        if not isinstance(source, str):
            raise RuntimeError('Gemini trả sai chữ gốc kiểm tra hình ảnh.')
        decisions[item_id] = decision
    result = []
    for item_id, event in enumerate(events, 1):
        decision = decisions.get(item_id)
        if decision and decision['confidence'] >= .95 and not decision['is_caption']:
            continue
        text = event.text
        if decision and decision['is_caption'] and decision['confidence'] >= .9 and decision['source'].strip():
            text = decision['source'].strip()
        result.append(SubtitleEvent(event.start, event.end, text))
    return merge_caption_fragments(result), decisions


async def review_visible_captions(source: Path, events, work_dir: Path, progress):
    from app.services.ai.gemini_web import browser
    report_file = work_dir / 'subtitles.recognition.json'
    report = json.loads(report_file.read_text(encoding='utf-8')) if report_file.is_file() else {}
    ids = _candidate_ids(events, report)
    if not ids:
        return merge_caption_fragments(events), {}
    from app.services.ai.asr_cache import digest
    video_digest = await asyncio.to_thread(digest, source)
    fingerprint = hashlib.sha256(json.dumps({'version': VERSION, 'events': [(e.start, e.end, e.text) for e in events], 'video_digest': video_digest, 'ids': ids}, ensure_ascii=False).encode()).hexdigest()
    cache = work_dir / 'subtitles.vision-review.json'
    items = []; context = {}
    if cache.is_file():
        try:
            stored = json.loads(cache.read_text(encoding='utf-8'))
            if stored.get('fingerprint') == fingerprint:
                saved = stored.get('items', [])
                apply_review(events, ids[:len(saved)], saved)
                items = saved
                context = stored.get('context', {})
        except (ValueError, TypeError, RuntimeError, KeyError):
            items = []; context = {}
    for start in range(len(items), len(ids), 6):
        batch_ids = ids[start:start + 6]
        progress(f'Gemini kiểm tra chữ trên ảnh ({start}/{len(ids)} đoạn)', 66)
        sheet = work_dir / f'caption-evidence-{start // 6 + 1}.jpg'
        await asyncio.to_thread(_frame_sheet, source, events, batch_ids, sheet)
        context_ids = set(range(1, min(len(events), 12) + 1)) | set(range(max(1, len(events) - 11), len(events) + 1))
        for item_id in batch_ids:
            context_ids.update(range(max(1, item_id - 4), min(len(events), item_id + 4) + 1))
        payload = {'current_items': [{'id': i, 'source': events[i - 1].text, 'time': [events[i - 1].start, events[i - 1].end]} for i in batch_ids], 'caption_context': [{'id': i, 'source': events[i - 1].text} for i in sorted(context_ids)]}
        prompt = (
            'Kiểm tra OCR phụ đề phim bằng ẢNH đính kèm. Ảnh có từng ô ID, cảnh đầy đủ và phần chữ phóng lớn. '
            'Đọc lại đúng chữ gốc hiển thị ở mỗi ID, không dịch trong bước này. Chữ cùng tên ở hai ô phải chép cùng tên nếu ảnh xác nhận. '
            'Phân biệt lời thoại với watermark/logo/chữ nền: chỉ đánh is_caption=false khi nhìn ảnh chắc chắn không phải phụ đề. '
            'Không đoán tên hoặc câu từ OCR sai khi ảnh không rõ; confidence thấp để giữ nguyên và đánh dấu cần xem lại. '
            'Mỗi item có id, text (chỉ chữ gốc đã đọc trên ảnh; nếu không phải phụ đề dùng chính xác __NON_CAPTION__), is_caption (boolean), confidence (0..1), reason. Không lồng chuỗi JSON vào text. '
            'items đủ id, không rỗng. memory_updates có thể ghi characters, glossary, addressing_rules khi ảnh/chữ có bằng chứng, không tự suy đoán vợ chồng. Characters.name phải là tên tiếng Việt chuẩn; chữ gốc đặt trong aliases, không dùng tên chữ Trung làm tên tiếng Việt bắt buộc. '
            'Chỉ trả JSON items và memory_updates.'
        )
        reply = json.loads(await browser.complete([{'role': 'system', 'content': prompt}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}], timeout=180, attachments=[sheet]))
        apply_review(events, batch_ids, reply.get('items'))
        items.extend(reply['items'])
        memory = reply.get('memory_updates')
        if isinstance(memory, dict):
            if isinstance(memory.get('characters'), list): context['characters'] = context.get('characters', []) + memory['characters']
            if isinstance(memory.get('glossary'), dict): context.setdefault('glossary', {}).update(memory['glossary'])
            if isinstance(memory.get('addressing_rules'), list): context['honorific_rules'] = context.get('honorific_rules', []) + memory['addressing_rules']
        pending = cache.with_suffix('.pending.json')
        pending.write_text(json.dumps({'fingerprint': fingerprint, 'items': items, 'context': context}, ensure_ascii=False, indent=2), encoding='utf-8'); pending.replace(cache)
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
                              'confidence': min((cue.get('confidence', 0) for cue in original_cues), default=0),
                              'needs_review': needs_review, 'vision_checked': bool(checked)})
    verified_report = work_dir / 'subtitles.recognition.verified.json'
    pending = verified_report.with_suffix('.pending.json')
    pending.write_text(json.dumps({**report, 'cues': verified_cues, 'vision_review': True}, ensure_ascii=False, indent=2), encoding='utf-8'); pending.replace(verified_report)
    progress(f'Gemini đã kiểm tra {len(ids)}/{len(ids)} đoạn chữ đáng ngờ', 66)
    return reviewed, _safe_context(context)
