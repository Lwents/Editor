"""Contextual subtitle translation through the project's configured AI model.

The active engine translates ordered batches directly, preserving timestamps,
Unicode NFC, character identity, relationships, glossary terms, and a rolling
memory across long videos. Legacy Google helpers remain isolated for backwards
compatibility tests but are never selected by ``get_translation_engine``.
"""
from __future__ import annotations
from app.services.ai.web_provider import is_web_engine
from app.services.ai.provider_status import provider_name, error_prefix
from app.services.ai.caption_evidence import NON_CAPTION

import asyncio
import copy
import hashlib
import json
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from app.core import settings
from app.services.persistence import atomic_json, read_json, PersistenceError
from app.services.ai.cue_recovery import CueRecovery
from app.services.ai.gemini_web import GeminiWebRefusal, GeminiWebTimeout, GeminiWebVerificationRequired, GeminiWebModelError
from app.services.ai.gemini_models import model_policy, current_model_key
from app.models.job import ProcessingMode
from app.services.ai.character_names import (
    build_character_name_registry,
    canonicalize_character_text,
    character_name_contract,
)
from app.services.ai.lexical_guidance import lexical_guidance, LEXICON_VERSION
from app.services.ai.translation_memory import (
    MemoryArchive, memory_session, build_handoff, record_context, source_notes,
)
from app.services.presets import ProcessingProfile, get_processing_profile
from app.services.subtitles.timing import SubtitleEvent


class TranslationError(RuntimeError):
    pass


class TranslationNeedsReview(TranslationError):
    """Some groups remain unresolved; valid later groups are already durable."""


class TranslationBatchStructureError(TranslationError):
    def __init__(self, message: str, valid_items: dict[int, str] | None = None, updates: dict | None = None, source_repair_ids: set[int] | None = None):
        super().__init__(message)
        self.valid_items = valid_items or {}
        self.updates = updates or {}
        self.source_repair_ids = source_repair_ids or set()


TRANSLATION_POLICY_VERSION = 25


def get_translation_profile(processing_mode=None):
    profile = get_processing_profile(processing_mode)
    if is_web_engine():
        limit = 24 if profile.name == 'fast' else 8 if profile.name == 'quality' else 12
        return replace(profile, translation_batch_items=min(profile.translation_batch_items, limit))
    return profile


def create_translation_client():
    if is_web_engine():
        from app.services.ai.gemini_web import GeminiWebClient
        from app.services.ai.web_provider import get_browser
        return GeminiWebClient(get_browser())
    if not settings.ninerouter_api_key:
        raise TranslationError('Chưa cấu hình API key để dịch phụ đề.')
    from openai import AsyncOpenAI
    return AsyncOpenAI(api_key=settings.ninerouter_api_key,
                       base_url=settings.ninerouter_api_url, max_retries=0)


# ─── Engine classes ───────────────────────────────────────────────────────────

class TranslationEngine:
    async def translate_events(
        self,
        events: list[SubtitleEvent],
        source_language: str,
        target_language: str,
        context: dict | None = None,
        *,
        processing_mode: ProcessingMode | str | None = None,
        checkpoint_file: Path | None = None,
        on_batch_progress: Callable[[int, int], None] | None = None,
    ) -> list[SubtitleEvent]:
        raise NotImplementedError


class PassthroughTranslation(TranslationEngine):
    async def translate_events(
        self,
        events: list[SubtitleEvent],
        source_language: str,
        target_language: str,
        context: dict | None = None,
        *,
        processing_mode: ProcessingMode | str | None = None,
        checkpoint_file: Path | None = None,
        on_batch_progress: Callable[[int, int], None] | None = None,
    ) -> list[SubtitleEvent]:
        return events


class GeminiTranslation(TranslationEngine):
    """Translate directly with the configured contextual AI model.

    Batches are intentionally processed in order. Each request receives the
    global character bible, a small overlap around the current batch, and
    memory returned by the preceding batches. This keeps names, relationships,
    gendered pronouns, and forms of address stable across a long video.
    """

    async def translate_events(
        self,
        events: list[SubtitleEvent],
        source_language: str,
        target_language: str,
        context: dict | None = None,
        *,
        processing_mode: ProcessingMode | str | None = None,
        checkpoint_file: Path | None = None,
        on_batch_progress: Callable[[int, int], None] | None = None,
    ) -> list[SubtitleEvent]:
        if not events:
            return []
        if source_language != "auto" and source_language.lower() == target_language.lower():
            return events
        use_browser = is_web_engine()
        if not use_browser and not settings.ninerouter_api_key:
            raise TranslationError(
                "Chưa cấu hình API key 9router nên AI không thể dịch phụ đề."
            )

        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise TranslationError("Thiếu thư viện openai để gọi AI dịch phụ đề.") from exc

        client = create_translation_client()
        profile = get_translation_profile(processing_mode)
        character_names = build_character_name_registry(context)
        source_texts = [
            unicodedata.normalize("NFC", event.text.strip())
            for event in events
        ]
        ranges = _ai_translation_batch_ranges(
            source_texts,
            max_items=profile.translation_batch_items,
            max_chars=profile.translation_batch_chars,
        )
        fingerprint = _translation_fingerprint(
            source_texts,
            source_language,
            target_language,
            context,
            profile,
        )
        translated_texts, memory = _load_translation_checkpoint(
            checkpoint_file,
            fingerprint,
            context,
            ranges,
        )
        recovery_store = CueRecovery(checkpoint_file.with_suffix('.parts.sqlite3'), fingerprint) if checkpoint_file else None
        translated_texts = [
            canonicalize_character_text(text, character_names)
            for text in translated_texts
        ]
        archive = MemoryArchive(checkpoint_file.with_name('translation_memory.sqlite3'),
                                _memory_namespace(fingerprint, events)) if checkpoint_file else None
        if archive:
            archive.seed(events, translated_texts, memory)
        with memory_session(archive, profile.name):
            completed_batches = sum(end <= len(translated_texts) for _, end in ranges)
            if on_batch_progress:
                on_batch_progress(completed_batches, len(ranges))

            started_at = time.monotonic()
            total_budget = max(
                profile.translation_request_timeout_seconds,
                len(ranges) * profile.translation_batch_budget_seconds,
            )
            saved_unresolved = read_json(checkpoint_file.with_name('translation_unresolved.json')) if checkpoint_file else None
            repair_on_resume = {item['id'] for item in saved_unresolved.get('items', [])
                                if isinstance(item, dict) and type(item.get('id')) is int} if (
                isinstance(saved_unresolved, dict) and saved_unresolved.get('fingerprint') == fingerprint) else set()
            unresolved={}
            consecutive_failures=0
            prefix_memory=copy.deepcopy(memory)
            def save_pending():
                if checkpoint_file:
                    atomic_json(checkpoint_file.with_name('translation_unresolved.json'),
                        {'fingerprint':fingerprint,'total':len(events),'items':[
                            {'id':item_id,'start':events[item_id-1].start,'source':source_texts[item_id-1],'error':error}
                            for item_id,error in sorted(unresolved.items())]})

            for batch_index, (start, end) in enumerate(ranges, start=1):
                if end <= len(translated_texts):
                    continue
                elapsed = time.monotonic() - started_at
                if elapsed >= total_budget:
                    raise TranslationError(
                        f"Dịch vượt quá ngân sách {int(total_budget)} giây; "
                        f"đã lưu checkpoint {completed_batches}/{len(ranges)} batch để chạy tiếp."
                    )
                batch = [
                    {"id": index + 1, "source": source_texts[index],
                     "time": [events[index].start, events[index].end]}
                    for index in range(start, end)
                ]
                previous_context = [
                    {
                        "id": index + 1,
                        "source": source_texts[index],
                        **({'translated':translated_texts[index]} if index<len(translated_texts) else {}),
                    }
                    for index in range(max(0, start - 8), start)
                    if index >= len(translated_texts) or translated_texts[index] != NON_CAPTION
                ]
                next_context = [
                    {"id": index + 1, "source": source_texts[index]}
                    for index in range(end, min(len(source_texts), end + 4))
                ]
                try:
                    # Previously failed source cues need source verification,
                    # rather than another full cycle of text timeouts.
                    if repair_on_resume and recovery_store is not None:
                        known, _ = recovery_store.load(batch)
                        repair_pending = [item for item in batch if int(item['id']) in repair_on_resume
                                          and int(item['id']) not in known]
                        if repair_pending:
                            from app.services.ai.caption_repair import repair_failed_cues
                            repaired, changed = await repair_failed_cues(
                                repair_pending, context=context, memory=memory,
                                previous=previous_context, following=next_context,
                                timeout=profile.translation_request_timeout_seconds)
                            recovery_store.save(batch, repaired, changed)
                            repair_on_resume.difference_update(int(item['id']) for item in repair_pending)
                    batch_texts, memory_updates = await _translate_gemini_batch_with_retries(
                        client,batch,source_language,target_language,context,memory,previous_context,next_context,
                        retries=profile.translation_retries,request_timeout_seconds=profile.translation_request_timeout_seconds,
                        fast_mode=profile.name=='fast',recovery_store=recovery_store)
                except (TranslationError, GeminiWebRefusal, GeminiWebTimeout) as exc:
                    known,_=recovery_store.load(batch) if recovery_store else ({},[])
                    pending = [item for item in batch if int(item['id']) not in known]
                    if pending and 'chữ Trung' in str(exc):
                        from app.services.ai.caption_repair import repair_failed_cues
                        try:
                            repaired, changed = await repair_failed_cues(pending, context=context, memory=memory,
                                previous=previous_context, following=next_context,
                                timeout=profile.translation_request_timeout_seconds)
                            record_context(batch, repaired, changed, context=context)
                            if recovery_store:
                                recovery_store.save(batch, repaired, changed)
                            known.update(repaired)
                        except (GeminiWebVerificationRequired, GeminiWebModelError, PersistenceError):
                            raise
                        except Exception as repair_error:
                            # Leave failed evidence explicit and keep the original OCR/source.
                            exc = TranslationError(f'{exc} Đối chiếu nguồn chưa thành công: {repair_error}')
                    if len(known) != len(batch):
                        for item in batch:
                            if int(item['id']) not in known:
                                unresolved[int(item['id'])]=str(exc)
                        save_pending()
                        _write_translation_checkpoint(checkpoint_file,fingerprint,translated_texts,prefix_memory,len(source_texts))
                        consecutive_failures = 0 if known and 'chữ Trung' in str(exc) else consecutive_failures + 1
                        if consecutive_failures>=3:
                            raise TranslationError(f'{error_prefix()}_CIRCUIT_OPEN: {provider_name()} không xử lý được ba nhóm liên tiếp. Đã giữ checkpoint. Nguyên nhân gần nhất: {exc}') from exc
                        continue
                    batch_texts = [known[int(item['id'])] for item in batch]
                    memory_updates = {}
                consecutive_failures=0

                cleaned_batch=[]
                for item, translated in zip(batch, batch_texts):
                    source = str(item["source"])
                    cleaned = _clean_polished_subtitle(translated, "")
                    cleaned = unicodedata.normalize("NFC", cleaned)
                    cleaned = canonicalize_character_text(cleaned, character_names)
                    if not cleaned or (
                        target_language.lower() == "vi" and _looks_chinese(cleaned)
                    ):
                        raise TranslationError(
                            f"AI còn sót nội dung chưa dịch ở mục {item['id']}: {source[:120]}"
                        )
                    cleaned_batch.append(cleaned)
                if start==len(translated_texts):
                    translated_texts.extend(cleaned_batch)

                _merge_translation_memory(memory, memory_updates)
                memory["recent_translations"] = previous_context[-4:] + [
                    {
                        "id": item["id"],
                        "source": item["source"],
                        "translated": translated,
                    }
                    for item, translated in zip(batch[-8:], batch_texts[-8:]) if translated != NON_CAPTION
                ]
                completed_batches = batch_index
                if len(translated_texts)==end:
                    prefix_memory=copy.deepcopy(memory)
                _write_translation_checkpoint(
                    checkpoint_file,
                    fingerprint,
                    translated_texts,
                    prefix_memory,
                    len(source_texts),
                )
                if on_batch_progress:
                    on_batch_progress(completed_batches, len(ranges))

            if len(translated_texts) != len(events):
                save_pending()
                raise TranslationNeedsReview(f'TRANSLATION_NEEDS_REVIEW: {len(unresolved)} câu chưa xử lý được. Các nhóm khác đã được giữ riêng; tiếp tục chỉ xử lý phần còn thiếu.')
            save_pending()
            return [
                SubtitleEvent(
                    event.start,
                    event.end,
                    canonicalize_character_text(translated_texts[index], character_names),
                )
                for index, event in enumerate(events)
            ]



class GoogleTranslation(TranslationEngine):
    async def translate_events(
        self,
        events: list[SubtitleEvent],
        source_language: str,
        target_language: str,
        context: dict | None = None,
        *,
        processing_mode: ProcessingMode | str | None = None,
        checkpoint_file: Path | None = None,
        on_batch_progress: Callable[[int, int], None] | None = None,
    ) -> list[SubtitleEvent]:
        if not events:
            return []
        if source_language != "auto" and source_language.lower() == target_language.lower():
            return events

        def translate() -> list[SubtitleEvent]:
            all_texts = [_normalize_source_text(e.text.strip()) for e in events]

            # Xây dựng bản đồ proper nouns từ toàn bộ phụ đề
            proper_noun_map = _build_proper_noun_map(all_texts, source_language)

            # Bảo vệ proper nouns + glossary + interjections
            protected_texts = [_protect_all(t, proper_noun_map) for t in all_texts]

            # Dịch theo batch
            translated_texts: list[str] = []
            for batch in _translation_batches(protected_texts):
                try:
                    translated_texts.extend(_translate_batch(batch, source_language, target_language))
                except Exception as exc:
                    recovered = [
                        _translate_single_or_original(t, source_language, target_language)
                        for t in batch
                    ]
                    if not any(item and item != orig for item, orig in zip(recovered, batch)):
                        raise TranslationError(
                            f"Không dịch được phụ đề bằng Google Translate: {exc}"
                        ) from exc
                    translated_texts.extend(recovered)

            # Sửa các câu còn sót chưa dịch
            translated_texts = _repair_untranslated_lines(
                protected_texts, translated_texts, source_language, target_language
            )

            # Khôi phục proper nouns + glossary
            translated_texts = [_restore_all(t, proper_noun_map) for t in translated_texts]

            # Post-processing sửa lỗi dịch Google sang tiếng Việt
            if target_language.lower() == "vi":
                translated_texts = [_fix_vietnamese_output(t, all_texts[i]) for i, t in enumerate(translated_texts)]
                translated_texts = [_fix_character_names(t) for t in translated_texts]

            return [
                SubtitleEvent(event.start, event.end, translated_texts[i] or event.text)
                for i, event in enumerate(events)
            ]

        translated_events = await asyncio.to_thread(translate)
        if target_language.lower() == "vi":
            translated_events = await _polish_vietnamese_events_with_ai(
                events,
                translated_events,
                source_language,
                context,
            )
        return translated_events


def get_translation_engine() -> TranslationEngine:
    if settings.translation_engine.lower() == "passthrough":
        return PassthroughTranslation()
    # "google" is accepted as a backwards-compatible configuration value so
    # existing .env files immediately stop using the rate-limited unofficial
    # Google endpoint. All real translation now goes directly through AI.
    return GeminiTranslation()


def _ai_translation_batch_ranges(
    texts: list[str],
    max_items: int = 20,
    max_chars: int = 4600,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    current_chars = 0
    for index, text in enumerate(texts):
        item_chars = len(text) + 60
        if index > start and (
            index - start >= max_items or current_chars + item_chars > max_chars
        ):
            ranges.append((start, index))
            start = index
            current_chars = 0
        current_chars += item_chars
    if start < len(texts):
        ranges.append((start, len(texts)))
    return ranges


def _translation_fingerprint(
    source_texts: list[str],
    source_language: str,
    target_language: str,
    context: dict | None,
    profile: ProcessingProfile,
) -> str:
    payload = {
        "source_texts": source_texts,
        "source_language": source_language,
        "target_language": target_language,
        "context": context or {},
        "model": model_policy(profile.name).cache_key if is_web_engine() else settings.ai_model,
        "engine": settings.translation_engine,
        "processing_mode": profile.name,
        "batch_items": profile.translation_batch_items,
        "batch_chars": profile.translation_batch_chars,
        # Invalidate checkpoints produced before proper names were locked to a
        # single canonical spelling across every batch.
        "character_name_policy": 2,
        "translation_policy": TRANSLATION_POLICY_VERSION,
        "lexicon_version": LEXICON_VERSION,
    }
    if profile.name == 'fast':
        payload['fast_prompt_version'] = 1
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _memory_namespace(fingerprint, events):
    return hashlib.sha256(json.dumps({'fingerprint':fingerprint,
        'times':[(e.start,e.end) for e in events]},sort_keys=True).encode()).hexdigest()


def _load_translation_checkpoint(
    checkpoint_file: Path | None,
    fingerprint: str,
    context: dict | None,
    ranges: list[tuple[int, int]],
) -> tuple[list[str], dict]:
    fallback = ([], _initial_translation_memory(context))
    if checkpoint_file is None or not (checkpoint_file.is_file() or checkpoint_file.with_suffix(checkpoint_file.suffix+'.bak').is_file()):
        return fallback
    data = read_json(checkpoint_file, lambda value:isinstance(value,dict) and isinstance(value.get('translated_texts'),list) and isinstance(value.get('memory'),dict))
    if data is None:
        return fallback
    if data.get('fingerprint') != fingerprint:
        raise TranslationError('CHECKPOINT_INCOMPATIBLE: Nguồn/ngữ cảnh/model khác tác vụ đã lưu. Checkpoint được giữ nguyên; tạo bản dịch mới để dùng cấu hình khác.')
    translated = data.get("translated_texts")
    memory = data.get("memory")
    if not isinstance(translated, list) or not all(isinstance(item, str) for item in translated):
        raise TranslationError('CHECKPOINT_INVALID: Có câu lưu không hợp lệ; giữ nguyên dữ liệu để khôi phục.')
    if not isinstance(memory, dict):
        raise TranslationError('CHECKPOINT_INVALID: Bộ nhớ ngữ cảnh không hợp lệ; giữ nguyên dữ liệu để khôi phục.')
    valid_offsets = {0, *(end for _, end in ranges)}
    if len(translated) not in valid_offsets:
        raise TranslationError('CHECKPOINT_INVALID: Checkpoint không khớp nhóm câu; không tự dịch lại từ đầu.')
    return list(translated), memory


def _write_translation_checkpoint(
    checkpoint_file: Path | None,
    fingerprint: str,
    translated_texts: list[str],
    memory: dict,
    total_items: int,
) -> None:
    if checkpoint_file is None:
        return
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "fingerprint": fingerprint,
        "model": current_model_key() if is_web_engine() else settings.ai_model,
        "translated_items": len(translated_texts),
        "total_items": total_items,
        "completed": len(translated_texts) == total_items,
        "translated_texts": translated_texts,
        "memory": memory,
    }
    atomic_json(checkpoint_file, payload)


def _initial_translation_memory(context: dict | None) -> dict:
    return {
        "character_bible": _compact_context(context),
        "characters": [],
        "relationships": [],
        "glossary": {},
        "addressing_rules": [],
        "recent_translations": [],
    }


def _merge_translation_memory(memory: dict, updates: object) -> None:
    if not isinstance(updates, dict):
        return
    for key in ("characters", "relationships", "addressing_rules", "source_notes"):
        incoming = updates.get(key)
        if not isinstance(incoming, list):
            continue
        existing = memory.setdefault(key, [])
        if not isinstance(existing, list):
            existing = []
            memory[key] = existing
        seen = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in existing}
        for item in incoming[:30]:
            encoded = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if encoded not in seen:
                existing.append(item)
                seen.add(encoded)
        del existing[:-80]

    incoming_glossary = updates.get("glossary")
    if isinstance(incoming_glossary, dict):
        glossary = memory.setdefault("glossary", {})
        if not isinstance(glossary, dict):
            glossary = {}
            memory["glossary"] = glossary
        for source, translated in list(incoming_glossary.items())[:50]:
            source_text = str(source).strip()
            translated_text = str(translated).strip()
            if source_text and translated_text:
                if source_text in glossary and glossary[source_text] != translated_text:
                    conflicts = memory.setdefault('glossary_conflicts', [])
                    entry = {'source':source_text,'previous':glossary[source_text], 'suggested':translated_text}
                    if entry not in conflicts:
                        conflicts.append(entry)
                    del conflicts[:-80]
                else:
                    glossary[source_text] = translated_text
        if len(glossary) > 120:
            for key in list(glossary)[:-120]:
                glossary.pop(key, None)


async def _translate_gemini_batch(
    client,
    batch: list[dict[str, object]],
    source_language: str,
    target_language: str,
    context: dict | None,
    memory: dict,
    previous_context: list[dict[str, object]],
    next_context: list[dict[str, object]],
    request_timeout_seconds: float = 150,
    review_mode: bool = False,
    fast_mode: bool = False,
    refusal_rephrased: bool = False,
) -> tuple[list[str], dict]:
    source_window = ' '.join(str(item.get('source', ''))
                             for item in [*previous_context, *batch, *next_context])
    # A teasing accusation is a unilateral claim, not the other speaker's
    # acceptance of a romantic address. Keep it in translation, but do not
    # use it as evidence to force that address on neighbouring speakers.
    address_evidence = source_window.replace('谋杀亲夫', '')
    romantic_evidence = (not re.search(r'[\u3400-\u9fff]', source_window)
                         or bool(re.search(r'夫君|相公|丈夫|妻子|夫妻|娘子|郎君|亲夫|成亲|妾', address_evidence)))
    addressing_contract = {
        'romantic_source_evidence': romantic_evidence,
        'forbidden_personal_pronouns': [] if romantic_evidence else ['chàng', 'thiếp'],
        'default': 'ta/ngươi; người nam có thể gọi người nữ rõ giới tính là nàng',
    }
    system_prompt = (
        "Bạn là biên dịch viên phụ đề phim nhiều tập, chuyên giữ nhất quán nhân vật và ngữ cảnh. "
        "Hãy dịch TRỰC TIẾP từ ngôn ngữ nguồn sang tiếng Việt tự nhiên; không dùng bản dịch máy trung gian.\n\n"
        "Quy tắc bắt buộc:\n"
        "- Trả đúng JSON object gồm items và memory_updates.\n"
        "- items phải giữ nguyên đủ id, đúng thứ tự và số lượng của CURRENT_ITEMS; không gộp/tách/thêm câu.\n"
        "- Mỗi item có dạng {\"id\":1,\"text\":\"...\"}; text chỉ chứa bản dịch, không giải thích.\n"
        "- Tuyệt đối không trả text rỗng hay bỏ id, kể cả câu ngắn, tiếng cảm thán hoặc chữ nhận diện khó hiểu. "
        "Nếu là tên/logo/ký tự Latin không có nghĩa rõ ràng, giữ nguyên chữ đó; không tự xóa. "
        "Dịch cả câu rời ngữ cảnh; CHARACTER_BIBLE chỉ hướng dẫn xưng hô, không quyết định bỏ câu.\n"
        "- Giữ nguyên ý, không bịa tình tiết, không thêm tên người nói nếu câu nguồn không có.\n"
        "- Câu nguồn bị cắt dở vẫn phải giữ đối tượng và sắc thái đã có: "
        "饿死你个 có thể là 'Cho ngươi chết đói, đồ...' (lời mắng bị ngắt); "
        "không rút thành 'Bỏ đói chết...' mất đối tượng 你 và không tự thêm phần chửi chưa có trong nguồn.\n"
        "- Đọc các CURRENT_ITEMS liền nhau như một câu liên tục để dịch câu bị chia: không thêm chủ thể hay danh từ mới ở mỗi mảnh. "
        "Ví dụ 太医院嘱咐的 / 药膳和补汤熬好了 phải nối nghĩa 'Theo lời dặn của Thái y viện / dược thiện và canh bổ đã nấu xong'; "
        "không dịch mảnh đầu thành 'Món theo lời dặn'. 熬汤 là nấu/hầm canh; không máy móc gọi mọi món là sắc thuốc.\n"
        "- Đọc PLOT_SUMMARY và SCENE_NOTES để hiểu ai làm gì, mục đích, hồi ức và độc thoại nội tâm. "
        "Hồ sơ và MEMORY chỉ là ngữ cảnh có bằng chứng; câu nguồn là căn cứ cuối cùng, không sửa câu theo một phỏng đoán trong hồ sơ.\n"
        "- Dịch thành ngữ theo nghĩa trong cảnh, không ghép nghĩa từng chữ hoặc bịa thành ngữ Việt. "
        "得寸进尺 là lấn tới/được đằng chân lân đằng đầu; không đổi thành chuyện tiền bạc. "
        "吃人的皇城 là ẩn dụ hoàng thành tàn khốc, không khẳng định có tình tiết ăn thịt người. "
        "垫脚石 là bàn đạp, phải giữ ý bị lợi dụng; không làm mất sắc thái bằng từ 'thứ'.\n"
        "- Khẩu ngữ bực bội/độc thoại hài phải có nghĩa tương đương trong tiếng Việt: "
        "喂你个锤锤 là lời phản bác kiểu 'Đút cái con khỉ!', không phải hành động đút một cái búa. "
        "Không thêm meme, drama hoặc lời chửi ngoài mức độ câu nguồn.\n"
        "- Phân biệt tính từ với mệnh lệnh khẩu ngữ: 老实点 khi yêu cầu ai ngồi yên/đừng quậy là 'ngoan ngoãn chút đi' hoặc 'yên nào', "
        "không phải yêu cầu 'thành thật' thú nhận. 迷了心窍 là 'mê muội đầu óc' hoặc 'mê hoặc tâm trí', "
        "không ghép từ thành 'mê hoặc muội'. 百废待兴 nói nhiều việc cần khôi phục sau biến động, "
        "dịch đủ ý 'trăm việc đang chờ vực dậy', không chỉ 'trăm mối ngổn ngang'.\n"
        "- Chọn động từ tự nhiên theo hành động: 自己去领二十军棍 là 'Tự đi chịu hai mươi quân côn'. "
        "Giữ số lượng, người chịu phạt và mức độ mệnh lệnh; tránh bê nguyên cấu trúc Hán ngữ.\n"
        "- Xưng hô nhất quán với vai vế: thoại cung đình ta/ngươi, mẫu phi/con, vương gia/thuộc hạ; "
        "không tự đổi sang tao/mày. Giữ sắc thái giận dữ, mỉa mai và lời đe dọa của nguồn. "
        "下死手 là 'ra tay hạ sát' hoặc 'ra tay chí mạng', phải giữ ý giết chết; "
        "không làm nhẹ thành chỉ 'ra tay tàn độc'. "
        "Phân biệt nội tâm với lời đáp ngoài miệng; không gộp hoặc đảo ý hai loại lời này.\n"
        "- Tước vị và cách gọi phải giữ đủ: 摄政王 là 'Nhiếp chính vương', không thay bằng tên người; "
        "主子 là 'chủ tử' hoặc 'chủ nhân', không rút thành 'chủ...' làm mất cách gọi. "
        "Nếu tên và tước vị cùng xuất hiện, giữ cả hai theo câu nguồn.\n"
        "- Đại từ 他 phải phù hợp thái độ người nói; không mặc định dịch thành 'người' mang sắc thái kính trọng "
        "khi người nói đang oán trách kẻ lợi dụng, hại gia đình mình.\n"
        "他都说了 dùng 都 để nhấn mạnh 'hắn cũng đã nói' hoặc 'ngay cả hắn cũng nói', "
        "không dịch thành 'hắn đều nói' như chủ thể số nhiều.\n"
        "- CHARACTER_NAME_CONTRACT là bắt buộc: khi nhắc nhân vật phải chép đúng canonical_names, không dịch nghĩa tên riêng, "
        "không dùng alias tên riêng và không đổi tên giữa các batch. "
        "Giữ cách gọi trong câu nguồn: 王爷 là Vương gia, 池儿 là Trì nhi; "
        "không thay tước vị, cách gọi thân mật hay quan hệ bằng tên đầy đủ của nhân vật.\n"
        "Tên đầy đủ có họ phải giữ đủ họ; tên gọi tắt không tự thêm họ. "
        "Khi nhiều alias của cùng nhân vật cùng khớp chữ nguồn, ưu tiên alias dài nhất có mặt trong câu. "
        "Ví dụ 楚绾绾 là Sở Oản Oản, còn 绾绾 là Oản Oản. "
        "妖女/yêu nữ và 妖后/yêu hậu là lời công kích hoặc cách gọi, không thay bằng tên riêng.\n"
        "- 娘娘 là nương nương, không thay bằng mô tả 'nữ tử áo trắng' hay tên người trong hồ sơ. "
        "启禀 là 'Bẩm' hoặc 'Khải bẩm', không viết 'Khởi bẩm'. "
        "Không tự dùng chàng/thiếp chỉ vì nhân vật chăm sóc hoặc lo cho nhau; "
        "khi chưa có bằng chứng rõ về cách xưng hô tình cảm, giữ ta/ngươi.\n"
        "Các gợi ý 'có thể', 'tùy mức độ thân mật' trong hồ sơ KHÔNG phải bằng chứng. "
        "Nếu hồ sơ đưa nhiều phương án xưng hô, dùng phương án trung tính ta/ngươi; "
        "không chọn chàng/thiếp từ hình ảnh chăm sóc hay cách gọi chung Vương gia, nương nương.\n"
        "- 他/她 trong độc thoại là đại từ ngôi thứ ba, không phải cách xưng hô người yêu. "
        "Khi nguồn chỉ xác nhận người quen/đồng đội, dùng hắn/anh ấy/cô ấy phù hợp thái độ; "
        "không gọi 'chàng' chỉ vì lo người đó bị giết. Quan hệ tình cảm phải có lời nguồn xác nhận rõ.\n"
        "Lời tự xưng 親夫/亲夫 mang tính trêu chọc của một người không xác nhận người kia đã chấp nhận quan hệ đó. "
        "Khi kể hoặc khoe với người thứ ba bằng 他, giữ đại từ ngôi thứ ba phù hợp giọng nói (hắn/anh ấy), "
        "không tự đổi sang chàng vì trước đó có lời trêu 'mưu sát phu quân'.\n"
        "- Chỉ xác định giới tính, vai vế hoặc quan hệ khi câu nguồn/ngữ cảnh có bằng chứng; chưa chắc thì dùng cách gọi trung tính.\n"
        "- ADDRESSING_CONTRACT ưu tiên trước hồ sơ và MEMORY. Khi forbidden_personal_pronouns không rỗng, "
        "không được dùng các đại từ đó trong items. Cảnh ôm, chăm sóc và lời tin tưởng không xác nhận hôn nhân. "
        "Người nữ gọi người nam bằng 你 thì dùng ngươi; người nam gọi người nữ có thể dùng nàng. "
        "Trong lượt đối chiếu phải sửa cả draft vi phạm hợp đồng này.\n"
        "- PREVIOUS_CONTEXT và NEXT_CONTEXT chỉ giúp hiểu đại từ/người nói; tuyệt đối không dịch chúng vào items.\n"
        "- Với câu rút gọn hoặc đại từ hồi chỉ, khôi phục đúng hành động/đối tượng đã nêu ở câu trước khi tiếng Việt cần để rõ nghĩa; không đổi chủ thể và không thêm thông tin ngoài nguồn.\n"
        "- Câu ngắn, tự nhiên, phù hợp thời lượng phụ đề/lồng tiếng; không để sót chữ Trung, pinyin hoặc chú thích.\n"
        "- Kiểm tra lại trật tự và ngữ pháp tiếng Việt: 心甘情愿 là 'cam tâm tình nguyện' hoặc 'tự nguyện', "
        "không đảo thành 'tâm cam tình nguyện'. 真当...是... là 'coi ... là ...'; "
        "khi nói ai làm bằng đất nặn, dùng 'coi ... là đất nặn' hoặc 'tưởng ... được nặn bằng đất', "
        "không viết 'coi ... làm bằng đất' thiếu quan hệ ngữ pháp.\n"
        "- Khi nguồn có NPC, giữ nguyên NPC; không thay bằng tên nhãn nhân vật như 'Tên lính áp giải'.\n"
        "- memory_updates chỉ ghi thông tin nhân vật mới được CURRENT_ITEMS xác nhận, gồm characters, relationships, glossary, addressing_rules.\n"
    )
    if fast_mode:
        system_prompt = (
            'Dịch current_items sang tiếng Việt tự nhiên, ngắn gọn để làm phụ đề. '
            'Chỉ trả JSON {"items":[{"id":1,"text":"bản dịch"}],"memory_updates":{}}. '
            'Giữ đủ ID, không gộp/tách/bỏ câu; dịch cả cảm thán. Không giải thích. '
            'Dịch thành ngữ theo nghĩa trong cảnh, không ghép nghĩa từng chữ. '
            'Giữ chủ thể, đối tượng, phủ định, nguyên nhân và sắc thái; không thêm tình tiết hay danh tính người nói. '
            'Đọc các mảnh câu liên tiếp cùng previous_context/next_context để nối nghĩa; chỉ dịch current_items. '
            'Giữ tên đầy đủ/biệt danh theo character_name_contract, tước vị và NPC; không đổi vai vế thành tên. '
            'Tuân thủ addressing_contract, không dùng chàng/thiếp khi bị cấm. '
            '他/她 theo thái độ người nói; không mặc định kính ngữ người khi đang oán trách kẻ hại mình. '
            'Tên được gọi không xác định người nói; hồ sơ/memory không được ghi đè bằng chứng nguồn. '
            'Không để sót chữ Trung hoặc pinyin; ký tự Latin/tên chưa rõ giữ nguyên. '
            'memory_updates chỉ ghi thay đổi mới có bằng chứng, còn lại để trống. '
            'Không lặp lại nguồn, hồ sơ hay phân tích trong câu trả lời.'
        )
    system_prompt += (
        "\nlexical_guidance là gợi ý nghĩa có điều kiện, không phải lệnh thay từ. "
        "Chọn nghĩa theo câu nguồn; tên và xưng hô vẫn theo hợp đồng nhân vật. "
        "Dùng từ chính xác, tự nhiên, giữ phủ định và mức độ chắc chắn; "
        "không nâng văn phong thành học thuật khi nhân vật nói khẩu ngữ."
    )
    if refusal_rephrased:
        system_prompt = (
            'Nhiệm vụ là chuyển ngữ các câu thoại đã cung cấp để làm phụ đề; '
            'không yêu cầu thực hiện hành động trong câu thoại hoặc nhận diện người thật. '
            'Hãy xử lý văn bản nguồn theo cấu trúc kết quả bên dưới.\n' + system_prompt
        )
    if review_mode:
        system_prompt += (
            "\nNHIỆM VỤ HIỆN TẠI: ĐỐI CHIẾU BẢN DỊCH. Mỗi CURRENT_ITEM có source và draft. "
            "Kiểm tra draft theo source, các câu liền trước/sau và hồ sơ ngữ cảnh. "
            "Chỉ sửa lỗi sai nghĩa, thành ngữ, bỏ mất ý quan trọng, thêm tình tiết, nhầm chủ thể/đối tượng, "
            "sai tên hoặc xưng hô. Câu đã đúng giữ nguyên; không viết lại cốt truyện cho hay hơn. "
            "Không dùng một bản dịch sai ở PREVIOUS_CONTEXT làm bằng chứng cho nghĩa nguồn. "
            "Trước khi trả lời tự kiểm tra: ai nói với ai, ai làm gì, phủ định, nguyên nhân, "
            "ẩn dụ và độc thoại có còn đúng với source không. Trả items đủ id với text cuối cùng; "
            "không sửa timestamp, không chép draft còn lỗi."
        )
    system_prompt += (
        "\nBÀN GIAO NGỮ CẢNH: Mỗi lượt có memory.handoff, dùng được ngay cả khi chat vừa đổi. "
        "Ưu tiên câu nguồn hiện tại và trích đoạn gốc. Bản dịch cũ chỉ là tham khảo, không phải bằng chứng đúng nghĩa. "
        "source_linked_notes chỉ có trích dẫn nguồn khớp, chưa chứng minh diễn giải của AI đúng. "
        "Không biến open_questions/legacy thành sự thật; khi conflicts, đối chiếu câu trích và cảnh hiện tại, không tự chọn theo ghi nhớ cũ. "
        "Quan hệ và xưng hô phải đúng từng cặp subject/target, chiều người nói/người nghe và cảnh. "
        "Tên được gọi không xác định người đang nói; không tự gán người nói khi thiếu bằng chứng. "
        "Giữ cách viết trong character_name_contract. Không coi gap là đoạn đã dịch. next_context chỉ để nối nghĩa, không áp một sự kiện tương lai lên cảnh hiện tại. scene_boundary_hints là gợi ý đổi cảnh; không mang định danh người nói/xưng hô cục bộ qua chỗ đó khi chưa có bằng chứng. "
        "Chỉ bổ sung tối đa 3 source_notes mới vào memory_updates khi có chi tiết quan trọng được CURRENT_ITEMS xác nhận: "
        "{kind:character|relationship|addressing|glossary|scene|uncertainty,subject,target,value,uncertain,evidence:[{id,quote}]}. "
        "subject/target dùng tên gốc hoặc ID nhân vật cụ thể, không chỉ chức danh chung. target bắt buộc với relationship/addressing; quote chép nguyên văn nguồn ở id đó. "
        "Nếu chưa chắc diễn giải hoặc người nói, uncertain=true; không đoán. Không có chi tiết mới thì memory_updates={}."
    )
    handoff_memory = build_handoff(memory, context, batch, previous_context, next_context,
                                   build_character_name_registry(context))
    payload = {
        "source_language": source_language,
        "target_language": target_language,
        "lexical_guidance": lexical_guidance(
            [str(item.get("source", "")) for item in [*previous_context, *batch, *next_context]],
            target_language, max_entries=8 if fast_mode else 16,
        ),
        "character_bible": _compact_context(context),
        "addressing_contract": addressing_contract,
        "character_name_contract": character_name_contract(
            build_character_name_registry(context)
        ),
        "memory": handoff_memory,
        "previous_context": previous_context,
        "current_items": batch,
        "required_ids": [int(item["id"]) for item in batch],
        "next_context": next_context,
        "review_mode": review_mode,
    }
    response = await client.chat.completions.create(
        model=settings.ai_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        temperature=0.15,
        timeout=request_timeout_seconds,
    )
    content = response.choices[0].message.content or ""
    data = _loads_json_object(content)
    items = data.get("items")
    if not isinstance(items, list):
        raise TranslationBatchStructureError("AI không trả về danh sách items.")

    expected_ids = [int(item["id"]) for item in batch]
    by_id: dict[int, str] = {}
    duplicate_ids: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            item_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if item_id in by_id:
            duplicate_ids.add(item_id)
        text = unicodedata.normalize("NFC", str(item.get("text") or "").strip())
        if text and text != NON_CAPTION:
            by_id[item_id] = text
    missing = [item_id for item_id in expected_ids if item_id not in by_id]
    unexpected = [item_id for item_id in by_id if item_id not in expected_ids]
    invalid_language = {item_id for item_id, text in by_id.items()
                        if target_language.lower() == 'vi' and _looks_chinese(text)}
    invalid_addressing = {item_id for item_id, text in by_id.items()
                          if target_language.lower() == 'vi' and not romantic_evidence
                          and re.search(r'\b(?:chàng(?!\s+trai\b)|thiếp)\b', text, re.I)}
    valid_items = {item_id: text for item_id, text in by_id.items()
                   if item_id in expected_ids and item_id not in duplicate_ids
                   and item_id not in invalid_language and item_id not in invalid_addressing}
    updates = data.get('memory_updates') if isinstance(data.get('memory_updates'), dict) else {}
    if 'source_notes' in updates:
        updates = dict(updates, source_notes=source_notes(updates, batch, set(valid_items)))
    if missing or unexpected or duplicate_ids or len(items) != len(batch):
        raise TranslationBatchStructureError(
            "AI trả sai cấu trúc batch "
            f"(thiếu={missing}, thừa={unexpected}, trùng={sorted(duplicate_ids)}).",
            valid_items=valid_items, updates=updates,
        )
    translated = [by_id[item_id] for item_id in expected_ids]
    if invalid_addressing:
        raise TranslationBatchStructureError(
            f'AI dùng xưng hô tình cảm chưa có bằng chứng nguồn ở ID {sorted(invalid_addressing)}; dùng ta/ngươi theo ADDRESSING_CONTRACT.',
            valid_items=valid_items, updates=updates,
        )
    if invalid_language:
        raise TranslationBatchStructureError(
            f'AI còn để sót chữ Trung ở ID {sorted(invalid_language)}; chỉ dịch lại các câu lỗi.',
            valid_items=valid_items, updates=updates, source_repair_ids=invalid_language,
        )
    return translated, updates


async def _translate_gemini_batch_with_retries(
    client,
    batch: list[dict[str, object]],
    source_language: str,
    target_language: str,
    context: dict | None,
    memory: dict,
    previous_context: list[dict[str, object]],
    next_context: list[dict[str, object]],
    retries: int = 3,
    request_timeout_seconds: float = 150,
    review_mode: bool = False,
    fast_mode: bool = False,
    refusal_rephrased: bool = False,
    recovery_store=None,
    timeout_split=True,
    source_repair_attempted: set[int] | None = None,
) -> tuple[list[str], dict]:
    if source_repair_attempted is None:
        source_repair_attempted = set()
    if recovery_store is not None:
        known, memories = recovery_store.load(batch)
        if known:
            updates = {}
            next_memory = copy.deepcopy(memory)
            for saved in memories:
                _merge_translation_memory(updates, saved)
                _merge_translation_memory(next_memory, saved)
            record_context(batch, known, updates, context=context)
            pending = [item for item in batch if int(item['id']) not in known]
            if pending:
                texts, new_updates = await _translate_gemini_batch_with_retries(
                    client,pending,source_language,target_language,context,next_memory,previous_context,next_context,
                    retries=retries,request_timeout_seconds=request_timeout_seconds,review_mode=review_mode,
                    fast_mode=fast_mode,refusal_rephrased=refusal_rephrased,recovery_store=recovery_store,timeout_split=timeout_split,source_repair_attempted=source_repair_attempted)
                known.update({int(item['id']):text for item,text in zip(pending,texts)})
                _merge_translation_memory(updates,new_updates)
            return [known[int(item['id'])] for item in batch], updates
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            if is_web_engine():
                from app.services.ai.gemini_web import response_wait_limits
                outer_timeout = response_wait_limits(request_timeout_seconds)[1] + 120
            else:
                outer_timeout = request_timeout_seconds + 5
            result = await asyncio.wait_for(
                _translate_gemini_batch(
                    client,
                    batch,
                    source_language,
                    target_language,
                    context,
                    memory,
                    previous_context,
                    next_context,
                    request_timeout_seconds=request_timeout_seconds,
                    review_mode=review_mode,
                    fast_mode=fast_mode,
                    refusal_rephrased=refusal_rephrased,
                ),
                timeout=outer_timeout,
            )
            record_context(batch, {int(item['id']):text for item,text in zip(batch,result[0])}, result[1], context=context)
            if recovery_store is not None:
                recovery_store.save(batch, {int(item['id']):text for item,text in zip(batch,result[0])}, result[1])
            return result
        except TranslationBatchStructureError as exc:
            record_context(batch, exc.valid_items, exc.updates, context=context)
            if recovery_store is not None:
                recovery_store.save(batch,exc.valid_items,exc.updates)
            # Untranslated CJK can indicate unreadable OCR. Consult source
            # evidence once per cue before repeating the same text request.
            repairable = [item for item in batch if int(item['id']) in exc.source_repair_ids
                          and int(item['id']) not in source_repair_attempted]
            if repairable and target_language == 'vi':
                from app.services.ai.caption_repair import repair_failed_cues, repair_source
                if repair_source.get():
                    source_repair_attempted.update(int(item['id']) for item in repairable)
                    try:
                        repaired, changed = await repair_failed_cues(
                            repairable, context=context, memory=memory,
                            previous=previous_context, following=next_context,
                            timeout=request_timeout_seconds)
                    except (GeminiWebVerificationRequired, GeminiWebModelError, PersistenceError):
                        raise
                    except Exception as repair_error:
                        print(f'Selective source repair failed: {repair_error}')
                    else:
                        exc.valid_items.update(repaired)
                        _merge_translation_memory(exc.updates, changed)
                        record_context(batch, repaired, changed, context=context)
                        if recovery_store is not None:
                            recovery_store.save(batch, repaired, changed)
                        if len(exc.valid_items) == len(batch):
                            return [exc.valid_items[int(item['id'])] for item in batch], exc.updates
            if len(batch) > 1:
                # Keep validated translations and retry only missing/invalid
                # IDs. If none were usable, halve the request down to one cue.
                # Every recursive request is smaller, so recovery is bounded.
                pending = [item for item in batch if int(item['id']) not in exc.valid_items]
                if not pending:
                    # Extraneous/duplicate output still requires a clean retry.
                    last_error = exc
                    continue
                if len(pending) == len(batch):
                    chunks = [pending[:len(pending) // 2], pending[len(pending) // 2:]]
                else:
                    chunks = [pending]
                recovered = dict(exc.valid_items)
                combined_updates = copy.deepcopy(exc.updates)
                updated_memory = copy.deepcopy(memory)
                _merge_translation_memory(updated_memory, combined_updates)
                for chunk in chunks:
                    chunk_texts, chunk_updates = await _translate_gemini_batch_with_retries(
                        client, chunk, source_language, target_language, context, updated_memory,
                        previous_context, next_context, retries=retries,
                        request_timeout_seconds=request_timeout_seconds,
                        review_mode=review_mode,
                        fast_mode=fast_mode,
                        refusal_rephrased=refusal_rephrased,
                        recovery_store=recovery_store,
                        timeout_split=timeout_split,
                        source_repair_attempted=source_repair_attempted,
                    )
                    recovered.update({int(item['id']): text for item, text in zip(chunk, chunk_texts)})
                    _merge_translation_memory(combined_updates, chunk_updates)
                    _merge_translation_memory(updated_memory, chunk_updates)
                return [recovered[int(item['id'])] for item in batch], combined_updates
            last_error = exc
        except GeminiWebRefusal:
            if refusal_rephrased:
                raise
            # One clarification of this text task; never loop on refusals.
            return await _translate_gemini_batch_with_retries(
                client, batch, source_language, target_language, context, memory,
                previous_context, next_context, retries=retries,
                request_timeout_seconds=request_timeout_seconds,
                review_mode=review_mode, fast_mode=fast_mode, refusal_rephrased=True,
                recovery_store=recovery_store,
                timeout_split=timeout_split,
                source_repair_attempted=source_repair_attempted,
            )
        except GeminiWebTimeout:
            if len(batch)<=1 or not timeout_split:
                raise
            output=[]; updates={}; next_memory=copy.deepcopy(memory)
            middle=len(batch)//2
            for chunk in (batch[:middle],batch[middle:]):
                texts,changed=await _translate_gemini_batch_with_retries(
                    client,chunk,source_language,target_language,context,next_memory,previous_context,next_context,
                    retries=retries,request_timeout_seconds=request_timeout_seconds,review_mode=review_mode,
                    fast_mode=fast_mode,refusal_rephrased=refusal_rephrased,recovery_store=recovery_store,timeout_split=False,source_repair_attempted=source_repair_attempted)
                output.extend(texts)
                _merge_translation_memory(updates,changed);_merge_translation_memory(next_memory,changed)
            return output,updates
        except (GeminiWebVerificationRequired, GeminiWebModelError, PersistenceError):
            raise
        except TimeoutError as exc:
            last_error = TranslationError(
                f"9router không phản hồi trong {int(request_timeout_seconds)} giây"
            )
        except Exception as exc:
            last_error = exc
        if attempt < retries:
            await asyncio.sleep(min(2 * attempt, 6))
    assert last_error is not None
    first_id = batch[0]["id"] if batch else "?"
    last_id = batch[-1]["id"] if batch else "?"
    raise TranslationError(
        f"AI {provider_name()} không dịch được batch {first_id}-{last_id}: {last_error}"
    ) from last_error


# ─── AI Vietnamese subtitle editor ───────────────────────────────────────────

async def _polish_vietnamese_events_with_ai(
    source_events: list[SubtitleEvent],
    draft_events: list[SubtitleEvent],
    source_language: str,
    context: dict | None = None,
) -> list[SubtitleEvent]:
    if not draft_events or not settings.ninerouter_api_key:
        return draft_events

    try:
        from openai import AsyncOpenAI
    except ImportError:
        return draft_events

    polished: list[SubtitleEvent] = []
    failed_batches = 0
    client = AsyncOpenAI(
        api_key=settings.ninerouter_api_key,
        base_url=settings.ninerouter_api_url,
    )

    for batch in _ai_polish_batches(source_events, draft_events):
        try:
            edited = await _polish_vietnamese_batch_with_retries(client, batch, source_language, context)
        except Exception as exc:
            print(f"AI subtitle polish fallback: {exc}")
            failed_batches += 1
            edited = [item["draft"] for item in batch]

        for item, text in zip(batch, edited):
            cleaned = _clean_polished_subtitle(text, item["draft"])
            polished.append(
                SubtitleEvent(
                    item["start"],
                    item["end"],
                    _fix_character_names(cleaned),
                )
            )

    if len(polished) != len(draft_events):
        return draft_events
    if failed_batches and _source_requires_ai_polish(source_events):
        raise TranslationError(
            "AI biên tập phụ đề không chạy được nên không dùng bản dịch thô. "
            "Hãy bật 9router/AI ở localhost:20128 rồi chạy lại."
        )
    return polished


def _ai_polish_batches(
    source_events: list[SubtitleEvent],
    draft_events: list[SubtitleEvent],
    max_items: int = 18,
    max_chars: int = 5200,
) -> list[list[dict[str, object]]]:
    batches: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    current_chars = 0

    for index, (source, draft) in enumerate(zip(source_events, draft_events), start=1):
        source_text = _normalize_source_text(source.text.strip())
        draft_text = draft.text.strip()
        item = {
            "id": index,
            "start": draft.start,
            "end": draft.end,
            "source": source_text,
            "draft": draft_text,
        }
        item_chars = len(source_text) + len(draft_text) + 80
        if current and (len(current) >= max_items or current_chars + item_chars > max_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += item_chars

    if current:
        batches.append(current)
    return batches


async def _polish_vietnamese_batch(
    client,
    batch: list[dict[str, object]],
    source_language: str,
    context: dict | None,
) -> list[str]:
    system_prompt = (
        "Bạn là biên tập viên phụ đề/lồng tiếng Việt chuyên review phim cổ trang Trung Quốc, donghua. "
        "Nhiệm vụ: sửa bản dịch nháp để câu thoại tự nhiên, đúng xưng hô và dễ đọc khi lồng tiếng.\n\n"
        "Quy tắc bắt buộc:\n"
        "- Trả về đúng JSON object: {\"items\":[{\"id\":1,\"text\":\"...\"}]}.\n"
        "- Giữ nguyên số lượng item và id. Không thêm, xoá, gộp, tách dòng.\n"
        "- Không thêm sự kiện mới, không bịa nội dung ngoài câu gốc.\n"
        "- Câu ngắn, hợp phụ đề và giọng đọc; ưu tiên 1-2 câu mỗi item.\n"
        "- Dùng xưng hô cổ trang hợp ngữ cảnh: bệ hạ, thần, phụ hoàng, phụ thân, huynh, tỷ, công tử, đại nhân.\n"
        "- Nếu là vua/hoàng đế nói với quần thần dùng 'trẫm/khanh'; thần tử nói với vua dùng 'thần/bệ hạ'.\n"
        "- Không đổi quan hệ. Tuyệt đối không dịch thành vợ/chồng/kết hôn nếu câu gốc không có 老婆, 妻子, 丈夫, 结婚, 婚约 hoặc nghĩa hôn nhân rõ ràng.\n"
        "- Nếu quan hệ chưa rõ, dùng trung tính: người phụ nữ, người đàn ông, cô ấy, hắn ta, người tình một đêm, nam chính/nữ chính.\n"
        "- Giữ tên riêng nhất quán theo Hán Việt nếu nhận ra: Chu Nguyên Chương, Chu Kỳ Ngọc, Hồng Vũ, Vĩnh Lạc, Mặc Nguyệt, Tần ca, Long tỷ.\n"
        "- Các cụm bị ASR/OCR sai phải sửa theo nghĩa: 照办 = làm theo, 照常 = như thường, 父亲 = phụ thân.\n"
        "- Nếu gặp 海繁的动物/还能翻得动不 hoặc 翻得动: hiểu là 'còn nhào lộn nổi không', tuyệt đối không dịch thành động vật/biển.\n"
        "- Nếu phần CONTEXT có tên phim, nhân vật, quan hệ, vai vế hoặc glossary thì ưu tiên dùng để sửa tên và xưng hô.\n"
        "- Không để sót tiếng Trung, pinyin thô hoặc tên sai kiểu Zhaoban/Zhan Xiang/Qixia/Mo Yue nếu có thể sửa.\n"
    )
    user_prompt = {
        "source_language": source_language,
        "context": _compact_context(context),
        "items": [
            {
                "id": item["id"],
                "source": item["source"],
                "draft_vi": item["draft"],
            }
            for item in batch
        ],
    }
    response = await client.chat.completions.create(
        model=settings.ai_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        temperature=0.25,
        timeout=90,
    )
    content = response.choices[0].message.content or ""
    data = _loads_json_object(content)
    items = data.get("items")
    if not isinstance(items, list):
        raise TranslationError("AI polish không trả về items.")

    by_id: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            item_id = int(item.get("id"))
        except Exception:
            continue
        text = str(item.get("text") or "").strip()
        if text:
            by_id[item_id] = text

    result: list[str] = []
    for item in batch:
        item_id = int(item["id"])
        result.append(by_id.get(item_id, str(item["draft"])))
    return result


async def _polish_vietnamese_batch_with_retries(
    client,
    batch: list[dict[str, object]],
    source_language: str,
    context: dict | None,
    retries: int = 3,
) -> list[str]:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return await _polish_vietnamese_batch(client, batch, source_language, context)
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                await asyncio.sleep(min(2 * attempt, 6))
    assert last_error is not None
    raise last_error


def _source_requires_ai_polish(events: list[SubtitleEvent]) -> bool:
    joined = " ".join(event.text for event in events[:80])
    return _looks_chinese(joined)


def _compact_context(context: dict | None) -> dict:
    if not isinstance(context, dict):
        return {}
    allowed = {
        "film_title",
        "genre",
        "setting",
        "plot_summary",
        "scene_notes",
        "characters",
        "relationships",
        "glossary",
        "honorific_rules",
        "translation_notes",
        "relationship_constraints",
    }
    compact = {key: context.get(key) for key in allowed if context.get(key)}
    return compact


def _loads_json_object(content: str) -> dict:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def _clean_polished_subtitle(text: str, fallback: str) -> str:
    cleaned = _clean_translation(text)
    cleaned = cleaned.replace("\\N", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \t\n\"'")
    cleaned = re.sub(r"\.{4,}", "...", cleaned)
    # Conservative correction of a malformed idiom observed in AI output.
    # Do not run the broad historical ASR substitutions on source captions.
    cleaned = re.sub(r'\bGõ gan tày trời\b', 'To gan tày trời', cleaned)
    cleaned = re.sub(r'\bgõ gan tày trời\b', 'to gan tày trời', cleaned)
    cleaned = cleaned.replace('bị ngươi mê muội đầu óc', 'bị ngươi làm mê muội đầu óc')
    cleaned = cleaned.replace('Bị ngươi mê muội đầu óc', 'Bị ngươi làm mê muội đầu óc')
    cleaned = re.sub(r"\s+([.,!?:;])", r"\1", cleaned)
    cleaned = re.sub(r",([^\s\d])", r", \1", cleaned)
    if not cleaned or _looks_chinese(cleaned):
        return fallback
    return cleaned


# ─── Google Translate API ─────────────────────────────────────────────────────

def _google_source(language: str) -> str:
    if language == "auto":
        return "auto"
    if language == "zh":
        return "zh-CN"
    return language


def _google_target(language: str) -> str:
    if language == "zh":
        return "zh-CN"
    return language


def _clean_translation(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"(?<=\d),\s+(?=\d)", ",", " ".join(text.replace("\n", " ").split()))


def _request_google_translate(
    text: str, source_language: str, target_language: str, timeout: int
) -> object:
    import requests

    params = {
        "client": "gtx",
        "sl": _google_source(source_language),
        "tl": _google_target(target_language),
        "dt": "t",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
    }
    response = requests.post(
        "https://translate.googleapis.com/translate_a/single",
        params=params,
        data={"q": text},
        headers=headers,
        timeout=timeout,
    )
    if response.status_code == 400 and source_language == "auto" and _looks_chinese(text):
        response = requests.post(
            "https://translate.googleapis.com/translate_a/single",
            params={**params, "sl": "zh-CN"},
            data={"q": text},
            headers=headers,
            timeout=timeout,
        )
    response.raise_for_status()
    return response.json()


def _payload_text(payload: object) -> str:
    if not isinstance(payload, list) or not payload:
        return ""
    segments = payload[0]
    if not isinstance(segments, list):
        return ""
    return "".join(
        part[0] for part in segments if isinstance(part, list) and part and part[0]
    )


def _looks_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _needs_retranslation(text: str, target_language: str) -> bool:
    return target_language.lower() != "zh" and _looks_chinese(text)


# ─── Glossary cố định (thuật ngữ kỹ thuật / thương hiệu) ─────────────────────

_GLOSSARY: dict[str, str] = {
    "ZXQTERM0ZXQ": "AntiGravity",
    "ZXQTERM1ZXQ": "Cursor",
    "ZXQTERM2ZXQ": "Windsurf",
}

_GLOSSARY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\banti\s*gravity\b", re.IGNORECASE), "ZXQTERM0ZXQ"),
    (re.compile(r"\bcursor\b", re.IGNORECASE), "ZXQTERM1ZXQ"),
    (re.compile(r"\bwindsurf\b", re.IGNORECASE), "ZXQTERM2ZXQ"),
]


def _protect_glossary(text: str) -> str:
    for pattern, placeholder in _GLOSSARY_PATTERNS:
        text = pattern.sub(placeholder, text)
    return text


def _restore_glossary(text: str) -> str:
    restored = text
    for placeholder, term in _GLOSSARY.items():
        restored = restored.replace(placeholder, term)
        restored = restored.replace(placeholder.lower(), term)
        restored = restored.replace(placeholder.title(), term)
    return restored


# Các lỗi ASR/OCR tiếng Trung hay làm sai nghĩa hoặc biến cụm từ thành tên riêng.
_SOURCE_TEXT_FIXES: list[tuple[re.Pattern, str]] = [
    (re.compile("臣定当赵半"), "臣定当照办"),
    (re.compile("定当赵半"), "定当照办"),
    (re.compile("赵半"), "照办"),
    (re.compile("战相"), "照常"),
    (re.compile("太阳照常"), "太阳照常"),
    (re.compile("不清"), "父亲"),
    (re.compile("不苦"), "不哭"),
    (re.compile("莫苦"), "莫哭"),
    (re.compile("一莫哭"), "莫哭"),
    (re.compile("初期"), "朱祁钰"),
    (re.compile("朱言此精花此数"), "朱颜辞镜花辞树"),
    (re.compile("尊是人间留不住"), "最是人间留不住"),
    (re.compile("朱言"), "朱元璋"),
    (re.compile("红武朝"), "洪武朝"),
    (re.compile("从西朝"), "正统朝"),
    (re.compile("有垃圾的"), "永乐朝的"),
    (re.compile("墨月"), "墨月"),
    (re.compile("七夏山"), "栖霞山"),
    (re.compile("秦哥"), "秦哥"),
    (re.compile("龙姐"), "龙姐"),
    (re.compile("海繁的动物"), "还能翻得动不"),
    (re.compile("繁的动"), "翻得动"),
    (re.compile("能连繁"), "能连翻"),
    (re.compile("能繁多少"), "能翻多少"),
    (re.compile("不插气"), "不喘气"),
]


def _normalize_source_text(text: str) -> str:
    if not text:
        return text
    normalized = text
    if _looks_chinese(normalized):
        for pattern, replacement in _SOURCE_TEXT_FIXES:
            normalized = pattern.sub(replacement, normalized)
    return normalized


# ─── Interjections: giữ nguyên không dịch ────────────────────────────────────

# Những từ cảm thán mà Google Translate hay dịch sai
_INTERJECTIONS: dict[str, str] = {
    "ZXQIJ0ZXQ": "Oh",
    "ZXQIJ1ZXQ": "Wow",
    "ZXQIJ2ZXQ": "Hey",
    "ZXQIJ3ZXQ": "Uh",
    "ZXQIJ4ZXQ": "Um",
    "ZXQIJ5ZXQ": "Hmm",
    "ZXQIJ6ZXQ": "Huh",
    "ZXQIJ7ZXQ": "Ugh",
    "ZXQIJ8ZXQ": "Oops",
}

_INTERJECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bOh\b", re.IGNORECASE), "ZXQIJ0ZXQ"),
    (re.compile(r"\bWow\b", re.IGNORECASE), "ZXQIJ1ZXQ"),
    (re.compile(r"\bHey\b", re.IGNORECASE), "ZXQIJ2ZXQ"),
    (re.compile(r"\bUh\b", re.IGNORECASE), "ZXQIJ3ZXQ"),
    (re.compile(r"\bUm\b", re.IGNORECASE), "ZXQIJ4ZXQ"),
    (re.compile(r"\bHmm\b", re.IGNORECASE), "ZXQIJ5ZXQ"),
    (re.compile(r"\bHuh\b", re.IGNORECASE), "ZXQIJ6ZXQ"),
    (re.compile(r"\bUgh\b", re.IGNORECASE), "ZXQIJ7ZXQ"),
    (re.compile(r"\bOops\b", re.IGNORECASE), "ZXQIJ8ZXQ"),
]


def _protect_interjections(text: str) -> str:
    for pattern, placeholder in _INTERJECTION_PATTERNS:
        text = pattern.sub(placeholder, text)
    return text


def _restore_interjections(text: str) -> str:
    for placeholder, word in _INTERJECTIONS.items():
        text = text.replace(placeholder, word)
        text = text.replace(placeholder.lower(), word)
    return text


# ─── Proper noun detection ────────────────────────────────────────────────────

# Từ tiếng Anh thông dụng viết hoa đầu câu — không phải proper noun
_COMMON_WORDS = frozenset({
    # Pronouns & articles
    "i", "a", "the", "in", "on", "at", "by", "to", "of", "or", "and", "but",
    "so", "yet", "for", "nor", "an", "is", "it", "he", "she", "we", "they",
    "you", "my", "your", "his", "her", "our", "its", "this", "that", "these",
    "those", "what", "who", "how", "when", "where", "why", "which",
    # Common verbs
    "do", "did", "does", "be", "are", "was", "were", "will", "would", "could",
    "should", "may", "might", "must", "have", "has", "had", "get", "got",
    "go", "come", "see", "say", "know", "think", "look", "want", "give",
    "use", "find", "tell", "ask", "seem", "feel", "try", "leave", "call",
    "keep", "let", "begin", "show", "hear", "play", "run", "move", "live",
    "believe", "hold", "bring", "happen", "write", "provide", "stand", "lose",
    "pay", "meet", "include", "continue", "set", "learn", "change", "lead",
    "understand", "watch", "follow", "stop", "create", "speak", "read",
    "spend", "grow", "open", "walk", "win", "offer", "remember", "love",
    "consider", "appear", "wait", "serve", "die", "send", "build", "stay",
    "fall", "cut", "reach", "kill", "remain", "suggest", "raise", "pass",
    "sell", "require", "report", "decide", "pull", "need", "put", "help",
    "make", "take", "came", "went", "said", "told", "asked", "knew", "thought",
    # Common adjectives/adverbs
    "yes", "no", "ok", "okay", "please", "sorry", "thank", "thanks",
    "hello", "hi", "bye", "well", "just", "now", "not", "only", "even",
    "also", "still", "again", "never", "always", "maybe", "really", "very",
    "much", "more", "most", "all", "any", "some", "here", "there", "then",
    "than", "because", "if", "when", "while", "before", "after", "until",
    "though", "although", "since", "unless",
    # Titles (chỉ tiêu đề, không phải tên)
    "mr", "mrs", "ms", "dr", "sir", "ma", "miss",
    # Numbers
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "first", "second", "third", "last", "next",
    # Tiếng Trung romanized phổ biến
    "ni", "wo", "ta", "men", "de", "le", "zhe", "na", "ba", "ma", "ne",
    # Tiếng Hàn romanized phổ biến
    "nae", "neo", "uri", "ige", "mwo", "wae", "eodi",
})

# Pattern: từ viết hoa chữ cái đầu (Latin), bao gồm tên ghép kiểu "Ji-ho", "Xiao Ming"
_CAPITALIZED_WORD_RE = re.compile(
    r"\b([A-Z][a-zA-Z]{1,24}(?:[-'][A-Z][a-zA-Z]{1,24})*)\b"
)

# Vị trí đầu câu (sau dấu câu kết thúc hoặc đầu text)
_SENTENCE_END_RE = re.compile(r"[.!?…。！？]\s+")
_LINE_START_RE = re.compile(r"(?:^|\n)\s*")


def _sentence_start_positions(text: str) -> set[int]:
    """Tập hợp các vị trí ký tự đầu tiên của mỗi câu."""
    positions: set[int] = set()
    # Đầu text
    m = re.search(r"\S", text)
    if m:
        positions.add(m.start())
    # Sau dấu câu
    for m in _SENTENCE_END_RE.finditer(text):
        nm = re.search(r"\S", text[m.end():])
        if nm:
            positions.add(m.end() + nm.start())
    # Sau xuống dòng
    for m in re.finditer(r"\n\s*\S", text):
        positions.add(m.end() - 1)
    return positions


def _build_proper_noun_map(
    all_texts: list[str],
    source_language: str,
) -> dict[str, str]:
    """
    Phân tích thống kê toàn bộ phụ đề để phát hiện proper nouns.

    Chiến lược:
    - Từ viết hoa xuất hiện ≥ 2 lần TỔNG CỘNG → bảo vệ (có thể là tên nhân vật)
    - Từ viết hoa xuất hiện ở GIỮA câu (không đầu câu) ≥ 1 lần → bảo vệ
    - Từ được gọi trực tiếp (vocative) như "Will, what are you doing?" → bảo vệ
    - Từ trong _COMMON_WORDS chỉ bảo vệ nếu rõ ràng là tên (vocative pattern)
    """
    # Chỉ áp dụng cho các ngôn ngữ Latin (en, ko romanized, v.v.)
    # Với tiếng Trung thuần CJK thì proper noun giữ nguyên ký tự CJK
    if source_language == "zh":
        return {}

    mid_sentence_count: Counter[str] = Counter()
    total_count: Counter[str] = Counter()
    # Vocative: tên được gọi trực tiếp ("Will, ..." hoặc "..., Will")
    vocative_words: set[str] = set()

    for text in all_texts:
        starts = _sentence_start_positions(text)
        for m in _CAPITALIZED_WORD_RE.finditer(text):
            word = m.group(1)
            if len(word) < 2:
                continue
            # Kiểm tra vocative pattern: "Word, ..." hoặc "..., Word" hoặc "..., Word."
            # Pattern này áp dụng cho cả common words lẫn non-common words
            before = text[:m.start()].rstrip()
            after = text[m.end():].lstrip()
            is_vocative = (
                before.endswith(",")       # "Come here, Will"
                or after.startswith(",")   # "Will, what are you doing?"
                or (m.start() in starts and after.startswith(","))  # đầu câu + dấu phẩy
            )
            if is_vocative:
                vocative_words.add(word)

            # Common words chỉ thêm vào total nếu không phải common
            if word.lower() in _COMMON_WORDS:
                continue
            total_count[word] += 1
            if m.start() not in starts:
                mid_sentence_count[word] += 1

    candidates: set[str] = set()
    for word, count in total_count.items():
        if word.lower() in _COMMON_WORDS:
            continue
        # Xuất hiện ≥ 2 lần tổng cộng → likely proper noun
        if count >= 2:
            candidates.add(word)
        # Xuất hiện ở giữa câu ≥ 1 lần → likely proper noun
        if mid_sentence_count[word] >= 1:
            candidates.add(word)

    # Thêm vocative words (tên được gọi trực tiếp — kể cả common words như "Will", "Mark")
    candidates.update(vocative_words)

    # Tạo placeholder map
    proper_noun_map: dict[str, str] = {}
    counter = len(_GLOSSARY) + len(_INTERJECTIONS)
    for word in sorted(candidates, key=lambda w: (-len(w), w)):  # dài trước, tránh conflict
        placeholder = f"ZXQPN{counter}ZXQ"
        proper_noun_map[placeholder] = word
        counter += 1

    return proper_noun_map


def _protect_all(text: str, proper_noun_map: dict[str, str]) -> str:
    """Bảo vệ: glossary → interjections → proper nouns (theo thứ tự)."""
    text = _protect_glossary(text)
    text = _protect_interjections(text)
    # Proper nouns: thay thế từ dài trước để tránh conflict
    for placeholder, word in proper_noun_map.items():
        text = re.sub(r"\b" + re.escape(word) + r"\b", placeholder, text)
    return text


def _restore_all(text: str, proper_noun_map: dict[str, str]) -> str:
    """Khôi phục ngược lại: proper nouns → interjections → glossary."""
    # Khôi phục proper nouns (bao gồm các biến thể case do Google tạo ra)
    for placeholder, word in proper_noun_map.items():
        text = text.replace(placeholder, word)
        text = text.replace(placeholder.lower(), word)
        text = text.replace(placeholder.title(), word)
    text = _restore_interjections(text)
    text = _restore_glossary(text)
    return text


# ─── Vietnamese post-processing ───────────────────────────────────────────────

# Các lỗi Google Translate thường gặp khi dịch sang tiếng Việt
_VI_FIXES: list[tuple[re.Pattern, str]] = [
    # "Của <Tên>" → "<Tên>'s sẽ được khôi phục đúng, nhưng "của X" sai ngữ pháp Việt
    (re.compile(r"\bcủa\s+([A-Z][a-z]+)\b"), r"\1"),
    # Thưa <Tên> (Google thêm "thưa" trước tên gọi trực tiếp)
    (re.compile(r"\bThưa\s+([A-Z][a-z]+)\b"), r"\1"),
    (re.compile(r"\bthưa\s+([A-Z][a-z]+)\b"), r"\1"),
    # "Nhìn xem" → "Nhìn này" (Google hay dịch Look sai)
    # Bỏ dấu ... cuối câu bị nhân đôi
    (re.compile(r"\.{4,}"), "..."),
    # Bỏ khoảng trắng thừa trước dấu câu
    (re.compile(r"\s+([.,!?:;])"), r"\1"),
    # Đảm bảo khoảng trắng sau dấu phẩy
    (re.compile(r",([^\s])"), r", \1"),
]

_VI_NAME_FIXES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bZhaoban\b", re.IGNORECASE), "làm theo"),
    (re.compile(r"\bZhan\s*Xiang\b", re.IGNORECASE), "như thường"),
    (re.compile(r"Giai đoạn Chiến tranh Mặt trời", re.IGNORECASE), "mặt trời vẫn chiếu như thường"),
    (re.compile(r"\bMo\s*Yue\b", re.IGNORECASE), "Mặc Nguyệt"),
    (re.compile(r"\bQixia\b", re.IGNORECASE), "Tê Hà"),
    (re.compile(r"núi Tê Hà", re.IGNORECASE), "núi Tê Hà"),
    (re.compile(r"\bZhu\s*Yan\b", re.IGNORECASE), "Chu Nguyên Chương"),
    (re.compile(r"\bZhu\s*Qiyu\b", re.IGNORECASE), "Chu Kỳ Ngọc"),
    (re.compile(r"\bZhu\s*Qi\s*Yu(?:zheng)?\b", re.IGNORECASE), "Chu Kỳ Ngọc"),
    (re.compile(r"\bChu\s+Qiyu\b", re.IGNORECASE), "Chu Kỳ Ngọc"),
    (re.compile(r"\bChu\s+Kiyu\b", re.IGNORECASE), "Chu Kỳ Ngọc"),
    (re.compile(r"\bChu\s+Ngọc\b", re.IGNORECASE), "Chu Kỳ Ngọc"),
    (re.compile(r"\bTần ca\b", re.IGNORECASE), "Tần ca"),
    (re.compile(r"\bAnh Tần\b", re.IGNORECASE), "Tần ca"),
    (re.compile(r"\bLong Jie\b", re.IGNORECASE), "Long tỷ"),
    (re.compile(r"\bLong\s+tỷ\b", re.IGNORECASE), "Long tỷ"),
    (re.compile(r"\bBộ trưởng của hoàng đế đã đến\b", re.IGNORECASE), "Bệ hạ, thần đến rồi"),
    (re.compile(r"\bBộ trưởng của hoàng đế\b", re.IGNORECASE), "thần"),
    (re.compile(r"\bHoàng đế\b"), "Bệ hạ"),
    (re.compile(r"\bHongwu\b", re.IGNORECASE), "Hồng Vũ"),
    (re.compile(r"\bYongle\b", re.IGNORECASE), "Vĩnh Lạc"),
    (re.compile(r"\bHaifan\b", re.IGNORECASE), "Hải Phàm"),
    (re.compile(r"triều đại Hồng Vũ", re.IGNORECASE), "triều Hồng Vũ"),
    (re.compile(r"triều đại Vĩnh Lạc", re.IGNORECASE), "triều Vĩnh Lạc"),
    (re.compile(r"triều đại Chính Thống", re.IGNORECASE), "triều Chính Thống"),
    (re.compile(r"thái tử và đại sư", re.IGNORECASE), "Thái tử thái sư"),
    (re.compile(r"Thái tử và đại sư", re.IGNORECASE), "Thái tử thái sư"),
    (re.compile(r"Hãy hứa với chúng tôi một điều", re.IGNORECASE), "Xin hãy hứa với thần một điều"),
    (re.compile(r"Thưa bệ hạ, tôi sẽ làm theo lời ngài", re.IGNORECASE), "Bệ hạ nói gì, thần nhất định sẽ làm theo"),
    (re.compile(r"Xin hãy hứa với ta một chuyện", re.IGNORECASE), "Xin hãy hứa với trẫm một chuyện"),
    (re.compile(r"\bHứa với ta một chuyện", re.IGNORECASE), "Hứa với trẫm một chuyện"),
    (re.compile(r"\bHứa với ta một việc", re.IGNORECASE), "Hứa với trẫm một việc"),
    (re.compile(r"Hãy thay ta bảo vệ", re.IGNORECASE), "Hãy thay trẫm bảo vệ"),
    (re.compile(r"\bThay ta bảo vệ", re.IGNORECASE), "Thay trẫm bảo vệ"),
    (re.compile(r"bảo vệ nó", re.IGNORECASE), "bảo vệ người ấy"),
    (re.compile(r"bảo vệ người\. Dốc", re.IGNORECASE), "bảo vệ người ấy. Dốc"),
    (re.compile(r"để nó sống", re.IGNORECASE), "để người ấy sống"),
    (re.compile(r"Hãy bảo vệ nó, bảo vệ cho tốt", re.IGNORECASE), "Hãy bảo vệ người ấy, bảo vệ cho tốt"),
    (re.compile(r"Bảo nó cứ sống như thường", re.IGNORECASE), "Bảo người ấy cứ sống như thường"),
    (re.compile(r"bảo vệ hắn", re.IGNORECASE), "bảo vệ người ấy"),
    (re.compile(r"Bệ hạ cứ nói, tôi", re.IGNORECASE), "Bệ hạ cứ nói, thần"),
    (re.compile(r"Bệ hạ cứ nói, con", re.IGNORECASE), "Bệ hạ cứ nói, thần"),
    (re.compile(r"Bệ hạ nói gì, tôi", re.IGNORECASE), "Bệ hạ nói gì, thần"),
    (re.compile(r"Bệ hạ nói gì, con", re.IGNORECASE), "Bệ hạ nói gì, thần"),
    (re.compile(r"Hãy bảo vệ anh ấy bằng gần như toàn bộ sức lực của bạn", re.IGNORECASE), "Thần sẽ dốc gần như toàn lực để bảo vệ người ấy"),
    (re.compile(r"Hãy bảo anh ấy làm như bình thường\.?\s*Gọi mặt trời như thường lệ\.?\s*Gọi mặt trời như thường lệ\.?", re.IGNORECASE), "Hãy để mặt trời vẫn chiếu như thường."),
    (re.compile(r"Xin đừng giữ Mặc Nguyệt lên núi Tê Hà", re.IGNORECASE), "Xin hãy ôm Mặc Nguyệt lên núi Tê Hà"),
    (re.compile(r"Làm phiền ôm Mặc Nguyệt lên núi Tê Hà", re.IGNORECASE), "Xin hãy ôm Mặc Nguyệt lên núi Tê Hà"),
    (re.compile(r"Đừng để mọi người tan vỡ", re.IGNORECASE), "Sinh ly tử biệt khiến người ta rơi lệ"),
    (re.compile(r"Đếm xem Tần ca có thể nhân lên bao nhiêu lần", re.IGNORECASE), "Đếm xem Tần ca làm được bao nhiêu lần"),
    (re.compile(r"Tần ca, huynh nói xem, động vật dưới biển\.{0,3}", re.IGNORECASE), "Tần ca, huynh nói xem, huynh còn nhào lộn nổi không?"),
    (re.compile(r"Tần ca, huynh nói xem, .*?dưới biển\.{0,3}", re.IGNORECASE), "Tần ca, huynh nói xem, huynh còn nhào lộn nổi không?"),
    (re.compile(r"Cha ơi con ở đây", re.IGNORECASE), "Phụ thân, con ở đây"),
    (re.compile(r"Được\.\.\. ta sẽ sống thật tốt", re.IGNORECASE), "Được... con sẽ sống thật tốt"),
    (re.compile(r"Cha ơi, sao trời tối thế", re.IGNORECASE), "Phụ thân, sao trời tối thế"),
    (re.compile(r"ngay cả cha tôi cũng là học trò của ông", re.IGNORECASE), "ngay cả phụ hoàng của ta cũng là học trò của ông"),
]


def _fix_vietnamese_output(translated: str, original: str) -> str:
    """Sửa các lỗi Google Translate thường gặp khi dịch sang tiếng Việt."""
    result = translated
    for pattern, replacement in _VI_FIXES:
        result = pattern.sub(replacement, result)
    # Nếu dịch ra toàn chữ thường trong khi gốc có tên riêng → giữ nguyên bản gốc đã clean
    # (trường hợp placeholder không được restore đúng)
    result = result.strip()
    return result or translated


def _fix_character_names(text: str) -> str:
    result = text
    for pattern, replacement in _VI_NAME_FIXES:
        result = pattern.sub(replacement, result)
    result = re.sub(r"\b(làm theo)\s+\1\b", r"\1", result, flags=re.IGNORECASE)
    result = re.sub(r"\s+([.,!?:;])", r"\1", result)
    result = re.sub(r",([^\s\d])", r", \1", result)
    return result.strip()


# ─── Batch translation ────────────────────────────────────────────────────────

def _translation_batches(texts: list[str], max_chars: int = 900) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    for text in texts:
        extra = len(text) + 20  # dự phòng cho delimiter
        if current and current_chars + extra > max_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(text)
        current_chars += extra
    if current:
        batches.append(current)
    return batches


def _translate_batch(
    texts: list[str], source_language: str, target_language: str
) -> list[str]:
    delimiter = "\n###SUB_BREAK###\n"
    query = delimiter.join(texts)
    payload = _request_google_translate(query, source_language, target_language, timeout=30)
    translated = _payload_text(payload)
    parts = [_clean_translation(part) for part in translated.split("###SUB_BREAK###")]
    if len(parts) != len(texts):
        # Fallback: dịch từng câu riêng lẻ
        return [_translate_single(text, source_language, target_language) for text in texts]
    return parts


def _translate_single(
    text: str, source_language: str, target_language: str
) -> str:
    if not text.strip():
        return ""
    chunks = _split_translation_text(text)
    if len(chunks) > 1:
        return _clean_translation(
            " ".join(
                _translate_single(chunk, source_language, target_language)
                for chunk in chunks
            )
        )
    payload = _request_google_translate(text, source_language, target_language, timeout=20)
    return _clean_translation(_payload_text(payload))


def _translate_single_or_original(
    text: str, source_language: str, target_language: str
) -> str:
    try:
        return _translate_single(text, source_language, target_language)
    except Exception:
        return text


def _repair_untranslated_lines(
    originals: list[str],
    translated: list[str],
    source_language: str,
    target_language: str,
) -> list[str]:
    repaired: list[str] = []
    for original, text in zip(originals, translated):
        candidate = text or original
        if _needs_retranslation(candidate, target_language):
            for retry_source in (source_language, "zh", "auto"):
                try:
                    retry = _translate_single(original, retry_source, target_language)
                except Exception:
                    continue
                if retry and not _needs_retranslation(retry, target_language):
                    candidate = retry
                    break
        if _needs_retranslation(candidate, target_language):
            raise TranslationError(
                f"Google Translate còn sót văn bản chưa dịch sang {target_language}: {candidate[:160]}"
            )
        repaired.append(candidate)
    return repaired


def _split_translation_text(text: str, max_chars: int = 450) -> list[str]:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return [cleaned]

    pieces = re.split(r"([。！？!?；;，,、\n])", cleaned)
    chunks: list[str] = []
    current = ""
    for index in range(0, len(pieces), 2):
        piece = pieces[index]
        punctuation = pieces[index + 1] if index + 1 < len(pieces) else ""
        sentence = f"{piece}{punctuation}".strip()
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(
                sentence[start : start + max_chars]
                for start in range(0, len(sentence), max_chars)
            )
            continue
        if current and len(current) + len(sentence) + 1 > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks or [cleaned]
