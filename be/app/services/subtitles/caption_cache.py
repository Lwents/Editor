"""Reuse real caption evidence by video, pixels' region and exact cue, not ID."""
import hashlib
import json
import uuid
from pathlib import Path

from app.core import settings


def _evidence(video_digest, event, regions):
    return {'schema': 1, 'video': video_digest, 'regions': regions or [],
            'start_ms': round(event.start * 1000), 'end_ms': round(event.end * 1000),
            'raw_text': event.text}


def _path(evidence):
    key = hashlib.sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return Path(settings.storage_dir) / 'recognition-cache' / 'captions' / evidence['video'] / (key + '.json')


def _trusted(item):
    if not isinstance(item, dict):
        return False
    score = item.get('confidence')
    method = item.get('method', 'gemini_image_review')
    return (type(item.get('is_caption')) is bool and isinstance(item.get('text'), str)
            and isinstance(score, (int, float)) and not isinstance(score, bool) and 0 <= score <= 1
            and isinstance(method, str) and method in {'gemini_image_review', 'local_multiscale_ocr', 'local_server_recognition'}
            and score >= (.98 if method != 'gemini_image_review' else .9 if item['is_caption'] else .95)
            and (not item['is_caption'] or bool(item['text'].strip())))


def restore_decisions(video_digest, events, ids, regions):
    result = []
    for item_id in ids:
        evidence = _evidence(video_digest, events[item_id - 1], regions)
        try:
            payload = json.loads(_path(evidence).read_text(encoding='utf-8'))
            item = payload['decision']
            if payload['evidence'] == evidence and _trusted(item):
                # Gap proofs and narrative context depend on neighbouring cues;
                # they are deliberately not transferred with a single image.
                result.append(dict(item, id=item_id))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return result


def save_decisions(video_digest, events, items, regions):
    for item in items:
        item_id = item.get('id')
        if type(item_id) is not int or not 1 <= item_id <= len(events) or not _trusted(item):
            continue
        evidence = _evidence(video_digest, events[item_id - 1], regions)
        path = _path(evidence)
        decision = {key: item[key] for key in ('text', 'confidence', 'is_caption')}
        decision['method'] = item.get('method', 'gemini_image_review')
        try:
            existing = restore_decisions(video_digest, events, [item_id], regions)
            def rank(row):
                return (row.get('method', 'gemini_image_review') == 'gemini_image_review', row['confidence'])
            if existing and rank(existing[0]) >= rank(decision):
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            pending = path.with_suffix('.' + uuid.uuid4().hex + '.pending')
            pending.write_text(json.dumps({'evidence': evidence, 'decision': decision}, ensure_ascii=False), encoding='utf-8')
            pending.replace(path)
        except (OSError, ValueError):
            continue


def preserve_legacy_decisions(source, old_subtitles, work_dir, video_digest=None):
    """Publish an old checkpoint only when its original content key verifies."""
    from app.services.ai.asr_cache import digest
    from app.services.subtitles.timing import parse_srt
    from app.services.subtitles.vision_review import _candidate_ids, _is_shorter_verified_fragment, apply_review, VERSION
    try:
        events = parse_srt(old_subtitles)
        report = json.loads((work_dir / 'subtitles.recognition.json').read_text(encoding='utf-8'))
        stored = json.loads((work_dir / 'subtitles.vision-review.json').read_text(encoding='utf-8'))
        items = stored['items']
        saved_ids = [row['id'] for row in items]
        apply_review(events, saved_ids, items)  # Validate the legacy schema and IDs.
        video_digest = video_digest or digest(source)
        signatures = set()
        current_signature = None
        for version in {6, 7, 8, 9, 10, 11, 12, VERSION}:
            for ids in (_candidate_ids(events, report), saved_ids):
                data = {'version': version, 'events': [(e.start, e.end, e.text) for e in events],
                        'video_digest': video_digest, 'ids': ids}
                signature = hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()
                signatures.add(signature)
                if version == VERSION and ids == _candidate_ids(events, report):
                    current_signature = signature
        if stored.get('fingerprint') in signatures:
            if stored['fingerprint'] != current_signature:
                verified = {row['id']: row for row in items}
                items = [row for row in items if not (
                    row.get('is_caption') and _is_shorter_verified_fragment(events, row, verified))]
            save_decisions(video_digest, events, items, report.get('regions'))
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, IndexError):
        return
