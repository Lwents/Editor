"""Compare frozen model output with independently supplied subtitled videos.

The reference is never passed to the translation pipeline. Scores are automated
judgements of sampled frames, not human-certified full-video accuracy.
Run with Windows backend Python; results and image evidence live outside git.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import settings
from app.services.ai.gemini_web import GeminiWebRefusal, GeminiWebTimeout, GeminiWebVerificationRequired, browser
from app.services.subtitles.timing import parse_srt

JUDGE_VERSION = 3
BATCH_SIZE = 12
PROMPT = """Bạn kiểm định phụ đề Trung-Việt. Mỗi ID có ảnh SOURCE gốc và REFERENCE do người dịch.
Đọc chữ Trung trên SOURCE và phụ đề Việt trên REFERENCE. Bỏ watermark/logo, không coi chữ nền là lời thoại.
CANDIDATE là bản model đã dịch trước khi xem reference. Đánh giá nghiêm, không ưu ái model.
Tương đương nghĩa được chấp nhận dù câu chữ khác. Reference cũng có thể sai: căn cứ cuối là chữ Trung và ngữ cảnh.
Kiểm tra đủ chủ thể, hành động, phủ định, số lượng, sắc thái, tên, tước vị, xưng hô và nội tâm/thoại nói ra.
Natural=true chỉ khi câu Việt tự nhiên, đủ rõ và phù hợp sắc thái như reference; không thưởng vì sao chép từ ngữ.
Nếu CANDIDATE rỗng khi ảnh có lời thoại thì semantic_ok/address_ok/natural=false.
Nếu SOURCE không có phụ đề nhưng CANDIDATE có chữ, đánh semantic_ok=false và error_type=spurious_caption.
Nếu không đọc chắc ảnh thì confidence thấp và không tự suy đoán để đạt điểm.
Mỗi item có id, text=lý do ngắn, source_visible=chữ Trung đúng đọc từ ảnh (rỗng nếu không có),
reference=phụ đề Việt đúng đọc từ ảnh (rỗng nếu không có), semantic_ok=boolean,
address_ok=boolean, natural=boolean, reference_supported=boolean,
confidence=0..1, suggested=bản Việt sửa nếu candidate sai, error_type.
text tối đa 12 từ khi đạt (có thể chỉ viết Đạt), tối đa 25 từ khi có lỗi.
Chỉ trả JSON items đủ ID. Không dùng tỷ lệ khớp ký tự để chấm nghĩa.
"""


def evidence_sheet(source, reference, samples, target):
    import cv2
    from PIL import Image, ImageDraw
    captures = [cv2.VideoCapture(str(path)) for path in (source, reference)]
    tiles = []
    try:
        for sample in samples:
            tile = Image.new('RGB', (1000, 290), 'white')
            draw = ImageDraw.Draw(tile)
            for row, cap in enumerate(captures):
                cap.set(cv2.CAP_PROP_POS_MSEC, sample['time'] * 1000)
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError(f"Unreadable evidence at {sample['time']}")
                height, width = frame.shape[:2]
                # Original captions and translated boxes both occupy this band.
                crop = frame[int(height * .77):, :]
                image = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
                image.thumbnail((1000, 125))
                label = 'SOURCE' if row == 0 else 'REFERENCE'
                draw.text((5, row * 145), f"ID {sample['id']} | {sample['time']:.3f}s | {label}", fill='black')
                tile.paste(image, (0, row * 145 + 20))
            tiles.append(tile)
    finally:
        for cap in captures:
            cap.release()
    columns = 3 if len(tiles) >= 16 else 2 if len(tiles) >= 8 else 1
    rows = (len(tiles) + columns - 1) // columns
    sheet = Image.new('RGB', (1000 * columns, 290 * rows), 'white')
    for i, tile in enumerate(tiles):
        sheet.paste(tile, ((i % columns) * 1000, (i // columns) * 290))
    sheet.save(target, quality=95)


def make_samples(work, duration):
    source = parse_srt(work / 'subtitles.source.verified.srt')
    translated = parse_srt(work / 'subtitles.vi.srt')
    # Cue midpoints check every output; independent 1s probes expose gaps in
    # the model's own OCR output, including entirely omitted captions.
    times = {round((e.start + e.end) / 2, 3) for e in source}
    times.update(round(i + .5, 3) for i in range(int(duration)))
    samples = []
    for at in sorted(times):
        active = [e.text for e in translated if e.start <= at < e.end]
        samples.append({'id': len(samples) + 1, 'time': at, 'candidate': ' '.join(active)})
    return samples


def validate(items, batch):
    if not isinstance(items, list) or len(items) != len(batch):
        raise ValueError('Incomplete comparison response')
    expected = [item['id'] for item in batch]
    if [item.get('id') for item in items] != expected:
        raise ValueError('Wrong comparison IDs')
    for item in items:
        for key in ('semantic_ok', 'address_ok', 'natural', 'reference_supported'):
            if type(item.get(key)) is not bool:
                raise ValueError(f'Invalid {key}')
        for key in ('text', 'source_visible', 'reference', 'suggested'):
            if not isinstance(item.get(key), str):
                raise ValueError(f'Invalid {key}')
        score = item.get('confidence')
        if type(score) not in (float, int) or not 0 <= score <= 1:
            raise ValueError('Invalid confidence')


def summarize(samples, items):
    checked = []
    uncertain = []
    blank_frames = []
    for sample, item in zip(samples, items):
        row = {**sample, **item}
        if item['confidence'] < .9:
            uncertain.append(row)
        elif item['source_visible'].strip():
            checked.append(row)
        else:
            blank_frames.append(row)
            if row.get('candidate', '').strip():
                checked.append({**row, 'semantic_ok': False, 'address_ok': False,
                                'natural': False, 'error_type': 'spurious_caption'})
    # Do not overweight long captions repeated in multiple sampled frames.
    groups = []
    for row in checked:
        key = ''.join(c for c in row['source_visible'] if c.isalnum())
        if not key:
            key = '__spurious__' + row.get('candidate', '')
        if groups and groups[-1]['key'] == key and row['time'] - groups[-1]['last_time'] <= 1.1:
            groups[-1]['rows'].append(row)
            groups[-1]['last_time'] = row['time']
        else:
            groups.append({'key': key, 'last_time': row['time'], 'rows': [row]})
    count = len(groups)
    def percent(keys):
        passed = sum(all(all(row[key] for key in keys) for row in group['rows']) for group in groups)
        return {'passed': passed, 'total': count, 'percent': round(100 * passed / count, 2) if count else None}
    return {
        'method': 'Gemini image-based automated judgement; sampled frames, not human-certified accuracy',
        'meaning': percent(['semantic_ok']),
        'meaning_and_address': percent(['semantic_ok', 'address_ok']),
        'meaning_address_and_naturalness': percent(['semantic_ok', 'address_ok', 'natural']),
        'uncertain_frames': len(uncertain),
        'blank_source_frames': len(blank_frames),
        'spurious_caption_frames': sum(bool(row.get('candidate', '').strip()) for row in blank_frames),
        'conservative_percent': round(100 * sum(
            all(all(row[k] for k in ('semantic_ok', 'address_ok', 'natural')) for row in group['rows'])
            for group in groups) / (count + len(uncertain)), 2) if count + len(uncertain) else None,
        'errors': [row for row in checked if not all(row[k] for k in ('semantic_ok', 'address_ok', 'natural'))],
        'uncertain': uncertain,
    }


async def evaluate(episode, root, bench):
    import cv2
    work = bench / str(episode)
    source = work / 'source.mp4'
    reference = root / f'T{episode}.mp4'
    cap = cv2.VideoCapture(str(source))
    duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    samples = make_samples(work, duration)
    fingerprint = hashlib.sha256(json.dumps({'version': JUDGE_VERSION, 'samples': samples,
        'reference_size': reference.stat().st_size, 'reference_mtime': reference.stat().st_mtime_ns},
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    path = work / 'reference-comparison.json'
    saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    items = saved.get('items', []) if saved.get('fingerprint') == fingerprint else []
    if items:
        validate(items, samples[:len(items)])
    context = json.loads((work / 'ai_context.json').read_text(encoding='utf-8'))
    for start in range(len(items), len(samples), BATCH_SIZE):
        batch = samples[start:start + BATCH_SIZE]
        sheet = work / f'reference-evidence-{start // BATCH_SIZE + 1}.jpg'
        await asyncio.to_thread(evidence_sheet, source, reference, batch, sheet)
        payload = {'current_items': batch, 'context': context,
                   'neighboring_source': [{'time': e.start, 'text': e.text} for e in parse_srt(work / 'subtitles.source.verified.srt')
                        if batch[0]['time'] - 3 <= e.start <= batch[-1]['time'] + 3]}
        for attempt in range(2):
            try:
                response = json.loads(await browser.complete([
                    {'role': 'system', 'content': PROMPT},
                    {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
                ], timeout=180, attachments=[sheet]))
                validate(response.get('items'), batch)
                break
            except (GeminiWebVerificationRequired, GeminiWebRefusal, GeminiWebTimeout):
                raise
            except Exception:
                if attempt:
                    raise
                print(f'EPISODE {episode} judge retry {start}', flush=True)
        items.extend(response['items'])
        pending = path.with_suffix('.pending.json')
        pending.write_text(json.dumps({'fingerprint': fingerprint, 'samples': samples, 'items': items},
                                     ensure_ascii=False, indent=2), encoding='utf-8')
        pending.replace(path)
        print(f'EPISODE {episode} compared {len(items)}/{len(samples)} frames', flush=True)
    result = summarize(samples, items)
    (work / 'reference-score.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('SCORE', episode, json.dumps({k: v for k, v in result.items() if k not in ('errors', 'uncertain')}, ensure_ascii=False), flush=True)


async def recheck_from_gold(episode, bench, gold_bench):
    """Reuse image readings already checked, not previous quality decisions."""
    work, gold = bench / str(episode), gold_bench / str(episode)
    if not (work / 'source.mp4').samefile(gold / 'source.mp4'):
        raise ValueError('Gold images must come from the same source video')
    frozen = json.loads((gold / 'reference-comparison.json').read_text(encoding='utf-8'))
    translated = parse_srt(work / 'subtitles.vi.srt')
    groups = []
    for sample, item in zip(frozen['samples'], frozen['items']):
        if item['confidence'] < .9 or not item['source_visible'].strip():
            continue
        at = sample['time']
        candidate = ' '.join(e.text for e in translated if e.start <= at < e.end)
        key = ''.join(c for c in item['source_visible'] if c.isalnum())
        observation = {'time': at, 'candidate': candidate, 'reference': item['reference']}
        if groups and groups[-1]['source_key'] == key and at - groups[-1]['last_time'] <= 1.1:
            groups[-1]['observations'].append(observation)
            groups[-1]['last_time'] = at
        else:
            groups.append({'id': len(groups) + 1, 'source': item['source_visible'],
                           'source_key': key, 'last_time': at, 'observations': [observation]})
    prompt = (PROMPT + '\nLượt này dùng source/reference đã đọc từ ảnh ở lượt trước, nằm trong CURRENT_ITEMS. '
              'Không cần đọc ảnh lại. Mỗi item gồm observations là các mốc của CÙNG câu gốc; '
              'phải kiểm tra TẤT CẢ candidate tại các mốc đó, chỉ cho đạt khi mọi mốc đúng. '
              'Một mốc rỗng là bỏ sót. Câu nguồn chia mảnh có thể tương ứng một câu reference dài hơn. '
              'Không xem kết quả chấm trước; chỉ chấm các candidate mới. '
              'Trả source_visible bằng source, reference bằng reference đã cung cấp, giữ nguyên ID.')
    data = json.loads(await browser.complete([
        {'role': 'system', 'content': prompt},
        {'role': 'user', 'content': json.dumps({'current_items': groups}, ensure_ascii=False)},
    ], timeout=180))
    validate(data.get('items'), groups)
    for group, item in zip(groups, data['items']):
        if any(not row['candidate'] for row in group['observations']):
            item.update(semantic_ok=False, address_ok=False, natural=False)
    total = len(groups)
    result = {'method': 'Automated recheck against frozen image readings; not human-certified accuracy',
              'groups': groups, 'items': data['items'],
              'candidate_sha256': hashlib.sha256((work / 'subtitles.vi.srt').read_bytes()).hexdigest(),
              'gold_readings_sha256': hashlib.sha256((gold / 'reference-comparison.json').read_bytes()).hexdigest(),
              'translation_quality': json.loads((work / 'translation_quality.json').read_text(encoding='utf-8')),
              'coverage_note': 'Only previously confident source-caption observations; no full-video certification.'}
    for title, keys in [('meaning', ['semantic_ok']), ('meaning_and_address', ['semantic_ok', 'address_ok']),
                        ('meaning_address_and_naturalness', ['semantic_ok', 'address_ok', 'natural'])]:
        passed = sum(item['confidence'] >= .9 and all(item[k] for k in keys) for item in data['items'])
        result[title] = {'passed': passed, 'total': total, 'percent': round(100 * passed / total, 2) if total else None}
    (work / 'reference-gold-recheck.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('RECHECK', episode, json.dumps(result['meaning_address_and_naturalness']), flush=True)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('episodes', type=int, nargs='+')
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--bench', type=Path, required=True)
    parser.add_argument('--gold-bench', type=Path)
    args = parser.parse_args()
    settings.translation_engine = 'gemini_web'
    settings.gemini_web_background = True
    try:
        for episode in args.episodes:
            if args.gold_bench:
                await recheck_from_gold(episode, args.bench, args.gold_bench)
            else:
                await evaluate(episode, args.root, args.bench)
    finally:
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
