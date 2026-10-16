"""Compare completed drafts with source cues; checkpoint each validated batch."""
from __future__ import annotations
from app.services.ai.web_provider import is_web_engine

import hashlib
import json
import unicodedata
from pathlib import Path

from app.core import settings
from app.services.persistence import atomic_json, read_json, PersistenceError
from app.services.ai.cue_recovery import CueRecovery
from app.services.ai.translation_memory import MemoryArchive, memory_session
from app.services.ai.gemini_web import GeminiWebRefusal, GeminiWebTimeout, GeminiWebVerificationRequired, GeminiWebModelError
from app.services.ai.character_names import build_character_name_registry, canonicalize_character_text
from app.services.ai.translation import (
    TRANSLATION_POLICY_VERSION, TranslationError, _ai_translation_batch_ranges,
    _compact_context, _initial_translation_memory, _merge_translation_memory,
    _translate_gemini_batch_with_retries,
    create_translation_client, _translation_fingerprint, _memory_namespace,
)
from app.services.presets import get_processing_profile
from app.services.ai.gemini_models import model_policy
from app.services.subtitles.timing import SubtitleEvent


async def review_translated_events(source, draft, context, work_dir: Path, source_language,
                                   target_language, processing_mode=None, on_progress=None):
    if len(source) != len(draft) or any((a.start, a.end) != (b.start, b.end)
                                      for a, b in zip(source, draft)):
        raise TranslationError('Bản dịch không khớp số câu/thời gian nguồn để đối chiếu.')
    if not source or target_language.lower() != 'vi':
        return draft
    from app.services.ai.translation import get_translation_profile
    profile = get_translation_profile(processing_mode)
    fingerprint = hashlib.sha256(json.dumps({
        'version': TRANSLATION_POLICY_VERSION,
        'source': [(e.start, e.end, e.text) for e in source],
        'draft': [e.text for e in draft], 'context': _compact_context(context),
        'engine': settings.translation_engine,
        'source_language': source_language, 'target_language': target_language,
        'model': model_policy(profile.name).cache_key if is_web_engine() else settings.ai_model,
        'mode': profile.name,
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    path = work_dir / 'translation_review_checkpoint.json'
    recovery_store = CueRecovery(work_dir / 'review.parts.sqlite3', fingerprint)
    review_items = 40 if profile.name == 'quality' else 80
    if is_web_engine():
        review_items = min(review_items, profile.translation_batch_items)
    ranges = _ai_translation_batch_ranges(
        [json.dumps([a.text, b.text], ensure_ascii=False) for a, b in zip(source, draft)],
        max_items=review_items, max_chars=12000)
    texts = []
    memory = _initial_translation_memory(context)
    cached = read_json(path, lambda value:isinstance(value,dict)
        and isinstance(value.get('texts'),list) and isinstance(value.get('memory'),dict))
    if cached is not None:
        if cached.get('fingerprint') != fingerprint:
            raise TranslationError('REVIEW_CHECKPOINT_INCOMPATIBLE: Nguồn/bản dịch thay đổi; giữ bản rà soát cũ, không tự ghi đè.')
        offsets = {0, *(end for _, end in ranges)}
        if (len(cached['texts']) not in offsets
                or not all(isinstance(t, str) and t.strip() for t in cached['texts'])):
            raise TranslationError('REVIEW_CHECKPOINT_INVALID: Bản rà soát không khớp câu nguồn; đã giữ dữ liệu, không tự chạy lại từ đầu.')
        texts = cached['texts']
        memory = cached['memory']
    completed = sum(end <= len(texts) for _, end in ranges)
    if on_progress:
        on_progress(completed, len(ranges))
    base_fingerprint = _translation_fingerprint(
        [unicodedata.normalize('NFC',e.text.strip()) for e in source], source_language,
        target_language, context, profile)
    namespace = _memory_namespace(base_fingerprint, source)
    inherited = MemoryArchive(work_dir / 'translation_memory.sqlite3', namespace)
    inherited.seed(source, [e.text for e in draft])
    archive = MemoryArchive(work_dir / 'translation_memory.sqlite3', namespace, phase='review')
    archive.seed(source, texts, memory)
    with memory_session(archive, profile.name):
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
                    review_mode=True,recovery_store=recovery_store)
            except (GeminiWebVerificationRequired, GeminiWebRefusal, GeminiWebTimeout, GeminiWebModelError, PersistenceError):
                raise
            except Exception as exc:
                raise TranslationError(f'Đối chiếu bản dịch chưa hoàn tất ({completed}/{len(ranges)} batch). '
                                       f'Bản dịch và checkpoint đã được giữ để chạy tiếp. Chi tiết: {exc}') from exc
            texts.extend(canonicalize_character_text(t, registry) for t in reviewed)
            _merge_translation_memory(memory, updates)
            atomic_json(path, {'version': TRANSLATION_POLICY_VERSION,
                'fingerprint': fingerprint, 'texts': texts, 'memory': memory,
                'completed': end == len(source),
                'changed_ids': [i+1 for i, text in enumerate(texts) if text != draft[i].text]})
            completed = index
            if on_progress:
                on_progress(completed, len(ranges))
        from app.services.ai.translation import _clean_polished_subtitle
        return [SubtitleEvent(e.start, e.end,
                              canonicalize_character_text(_clean_polished_subtitle(texts[i], ''), registry))
                for i, e in enumerate(source)]
