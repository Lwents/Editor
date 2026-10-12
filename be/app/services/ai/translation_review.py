"""Compare completed drafts with source cues; checkpoint each validated batch."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.core import settings
from app.services.ai.character_names import build_character_name_registry, canonicalize_character_text
from app.services.ai.translation import (
    TRANSLATION_POLICY_VERSION, TranslationError, _ai_translation_batch_ranges,
    _compact_context, _initial_translation_memory, _merge_translation_memory,
    _translate_gemini_batch_with_retries,
    create_translation_client,
)
from app.services.presets import get_processing_profile
from app.services.subtitles.timing import SubtitleEvent


async def review_translated_events(source, draft, context, work_dir: Path, source_language,
                                   target_language, processing_mode=None, on_progress=None):
    if len(source) != len(draft) or any((a.start, a.end) != (b.start, b.end)
                                      for a, b in zip(source, draft)):
        raise TranslationError('Bản dịch không khớp số câu/thời gian nguồn để đối chiếu.')
    if not source or target_language.lower() != 'vi':
        return draft
    profile = get_processing_profile(processing_mode)
    fingerprint = hashlib.sha256(json.dumps({
        'version': TRANSLATION_POLICY_VERSION,
        'source': [(e.start, e.end, e.text) for e in source],
        'draft': [e.text for e in draft], 'context': _compact_context(context),
        'engine': settings.translation_engine,
        'source_language': source_language, 'target_language': target_language,
        'model': 'Gemini Web' if settings.translation_engine == 'gemini_web' else settings.ai_model,
        'mode': profile.name,
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    path = work_dir / 'translation_review_checkpoint.json'
    ranges = _ai_translation_batch_ranges(
        [json.dumps([a.text, b.text], ensure_ascii=False) for a, b in zip(source, draft)],
        max_items=40 if profile.name == 'quality' else 80, max_chars=12000)
    texts = []
    memory = _initial_translation_memory(context)
    try:
        cached = json.loads(path.read_text(encoding='utf-8'))
        offsets = {0, *(end for _, end in ranges)}
        if (cached.get('fingerprint') == fingerprint and isinstance(cached.get('texts'), list)
                and len(cached['texts']) in offsets
                and all(isinstance(t, str) and t.strip() for t in cached['texts'])):
            texts = cached['texts']
            if isinstance(cached.get('memory'), dict):
                memory = cached['memory']
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    completed = sum(end <= len(texts) for _, end in ranges)
    if on_progress:
        on_progress(completed, len(ranges))
    registry = build_character_name_registry(context)
    client = None
    for index, (start, end) in enumerate(ranges, 1):
        if end <= len(texts):
            continue
        if client is None:
            client = create_translation_client()
        batch = [{'id': i+1, 'source': source[i].text, 'draft': draft[i].text,
                  'time': [source[i].start, source[i].end]} for i in range(start, end)]
        previous = [{'id': i+1, 'source': source[i].text, 'translated': texts[i]}
                    for i in range(max(0, start-8), start)]
        following = [{'id': i+1, 'source': source[i].text} for i in range(end, min(len(source), end+8))]
        try:
            reviewed, updates = await _translate_gemini_batch_with_retries(
                client, batch, source_language, target_language, context,
                memory, previous, following,
                retries=profile.translation_retries,
                request_timeout_seconds=max(120, profile.translation_request_timeout_seconds),
                review_mode=True)
        except Exception as exc:
            raise TranslationError(f'Đối chiếu bản dịch chưa hoàn tất ({completed}/{len(ranges)} batch). '
                                   'Bản dịch và checkpoint đã được giữ để chạy tiếp.') from exc
        texts.extend(canonicalize_character_text(t, registry) for t in reviewed)
        _merge_translation_memory(memory, updates)
        pending = path.with_suffix('.pending.json')
        pending.write_text(json.dumps({'version': TRANSLATION_POLICY_VERSION,
            'fingerprint': fingerprint, 'texts': texts, 'memory': memory,
            'completed': end == len(source),
            'changed_ids': [i+1 for i, text in enumerate(texts) if text != draft[i].text]},
            ensure_ascii=False, indent=2), encoding='utf-8')
        pending.replace(path)
        completed = index
        if on_progress:
            on_progress(completed, len(ranges))
    return [SubtitleEvent(e.start, e.end, texts[i]) for i, e in enumerate(source)]
