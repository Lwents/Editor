import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.core import settings
from app.services.ai.character_names import (
    build_character_name_registry,
    canonical_character_name,
    canonicalize_character_text,
)
from app.services.ai.context import _sanitize_context
from app.services.ai.translation import GeminiTranslation, get_translation_engine
from app.services.subtitles.timing import SubtitleEvent


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = json.loads(kwargs["messages"][1]["content"])
        items = [
            {"id": item["id"], "text": f"Lục Trạch nói câu {item['id']}"}
            for item in payload["current_items"]
        ]
        updates = {}
        if len(self.calls) == 1:
            updates = {
                "characters": [
                    {
                        "id": "lu_trach",
                        "name": "Lục Trạch",
                        "role": "nam chính",
                        "confidence": 0.98,
                    }
                ],
                "relationships": [],
                "glossary": {"陆泽": "Lục Trạch"},
                "addressing_rules": [],
            }
        content = json.dumps(
            {"items": items, "memory_updates": updates},
            ensure_ascii=False,
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class _FakeClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions())


class GeminiTranslationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_romantic_address_is_rejected_before_checkpoint(self) -> None:
        from unittest.mock import AsyncMock
        from app.services.ai.translation import _translate_gemini_batch, TranslationBatchStructureError
        reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(
            {'items': [{'id': 1, 'text': 'Ta tin chàng.'}], 'memory_updates': {}}, ensure_ascii=False)))])
        create = AsyncMock(return_value=reply)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with self.assertRaises(TranslationBatchStructureError):
            await _translate_gemini_batch(client, [{'id': 1, 'source': '我相信你'}], 'zh', 'vi', {}, {}, [], [])
        payload = json.loads(create.call_args.kwargs['messages'][1]['content'])
        self.assertEqual(payload['addressing_contract']['forbidden_personal_pronouns'], ['chàng', 'thiếp'])
        with self.assertRaises(TranslationBatchStructureError):
            await _translate_gemini_batch(client, [{'id': 1, 'source': '我相信你'}], 'zh', 'vi', {}, {},
                                         [{'id': 2, 'source': '绾绾这是要谋杀亲夫'}], [])
        await _translate_gemini_batch(client, [{'id': 1, 'source': '我相信你夫君'}], 'zh', 'vi', {}, {}, [], [])

    def test_passive_idiom_cleanup_does_not_insert_lam_twice(self) -> None:
        from app.services.ai.translation import _clean_polished_subtitle
        expected = 'Trì ca ca bị ngươi làm mê muội đầu óc.'
        self.assertEqual(_clean_polished_subtitle('Trì ca ca bị ngươi mê muội đầu óc.', ''), expected)
        self.assertEqual(_clean_polished_subtitle(expected, ''), expected)
    def test_calibrated_nickname_does_not_remove_the_surname_or_insult(self) -> None:
        registry = build_character_name_registry({'characters': [
            {'name': 'Sở Oản Oản', 'aliases': ['楚绾绾', '绾绾', '妖女', '妖后', 'yêu nữ', 'yêu hậu']},
        ]})
        self.assertEqual(canonicalize_character_text('楚绾绾, 绾绾!', registry), 'Sở Oản Oản, Oản Oản!')
        self.assertEqual(canonicalize_character_text('Sở Oản Oản là yêu hậu.', registry), 'Sở Oản Oản là yêu hậu.')
    async def test_source_caption_is_not_changed_by_historical_asr_guesses(self) -> None:
        client = _FakeClient()
        events = [SubtitleEvent(0, 1, '我看不清'), SubtitleEvent(1, 2, '这是故事初期')]
        with patch.object(settings, 'translation_engine', 'gemini'), \
                patch.object(settings, 'ninerouter_api_key', 'test-key'), \
                patch('openai.AsyncOpenAI', return_value=client):
            await GeminiTranslation().translate_events(events, 'zh', 'vi')
        payload = json.loads(client.chat.completions.calls[0]['messages'][1]['content'])
        self.assertEqual([item['source'] for item in payload['current_items']], ['我看不清', '这是故事初期'])

    def test_names_do_not_replace_pronouns_family_addresses_or_character_roles(self) -> None:
        registry = build_character_name_registry({'characters': [
            {'name': 'Tiêu Trì', 'aliases': ['hắn', 'phu quân', 'Diêm Vương sống']},
            {'name': 'Oản Oản', 'aliases': ['nữ phản diện số một', 'nàng']},
        ]})
        text = 'hắn gọi nàng; phu quân, nữ phản diện số một và Diêm Vương sống.'
        self.assertEqual(canonicalize_character_text(text, registry), text)

    def test_gemini_web_batches_are_bounded_without_changing_api_profile(self) -> None:
        from app.services.ai.translation import get_translation_profile
        with patch.object(settings, 'translation_engine', 'gemini_web'):
            self.assertEqual(get_translation_profile('balanced').translation_batch_items, 12)
            self.assertEqual(get_translation_profile('fast').translation_batch_items, 12)
            self.assertEqual(get_translation_profile('quality').translation_batch_items, 8)
        with patch.object(settings, 'translation_engine', 'gemini'):
            self.assertEqual(get_translation_profile('balanced').translation_batch_items, 28)

    async def test_fast_prompt_is_shorter_and_keeps_translation_contracts(self):
        from app.services.ai.translation import _translate_gemini_batch
        client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
        args = (client, [{'id': 1, 'source': '小心'}], 'zh', 'vi', {}, {}, [], [])
        await _translate_gemini_batch(*args)
        await _translate_gemini_batch(*args, fast_mode=True)
        full, fast = client.chat.completions.calls
        self.assertLess(len(fast['messages'][0]['content']), len(full['messages'][0]['content']) / 2)
        payload = json.loads(fast['messages'][1]['content'])
        self.assertEqual(payload['required_ids'], [1])
        self.assertEqual(payload['addressing_contract']['forbidden_personal_pronouns'], ['chàng', 'thiếp'])
        self.assertIn('character_name_contract', payload)

    def test_target_idiom_typo_is_cleaned_without_changing_other_words(self) -> None:
        from app.services.ai.translation import _clean_polished_subtitle
        self.assertEqual(_clean_polished_subtitle('Gõ gan tày trời.', ''), 'To gan tày trời.')
        self.assertEqual(_clean_polished_subtitle('Hắn gõ cửa.', ''), 'Hắn gõ cửa.')

    def test_calibrated_source_names_do_not_change_spelling_between_clips(self) -> None:
        registry = build_character_name_registry({'characters': [
            {'name': 'Quản Quản', 'aliases': ['绾绾']},
            {'name': 'Xiao Chi', 'aliases': ['箫池']},
        ]})
        self.assertEqual(canonicalize_character_text('Quản Quản gặp Xiao Chi.', registry), 'Oản Oản gặp Tiêu Trì.')

    def test_tentative_style_field_is_sanitized_without_erasing_confirmed_rules(self) -> None:
        result = _sanitize_context({'honorific_rules': [
            {'speaker': 'Nam', 'style': 'chàng/nàng hoặc ta/nàng'},
            {'speaker': 'Nữ', 'style': 'thiếp/chàng hoặc ta/ngươi (tùy sắc thái)'},
        ]})
        self.assertIn('dùng ta/ngươi', result['honorific_rules'][0]['style'])
        self.assertIn('không tự chọn chàng/thiếp', result['honorific_rules'][1]['style'])

    def test_source_name_does_not_replace_vietnamese_name_with_chinese(self) -> None:
        registry = build_character_name_registry({'characters': [
            {'name': '绾绾', 'aliases': ['Oản Oản', 'Wanwan']},
            {'name': '未知', 'aliases': ['Nữ tử áo trắng']},
        ]})
        self.assertEqual(canonicalize_character_text('Oản Oản!', registry), 'Oản Oản!')
        self.assertEqual(canonicalize_character_text('绾绾!', registry), 'Oản Oản!')
        self.assertNotIn('未知', registry)

    def test_tentative_romantic_address_is_not_used_as_evidence(self) -> None:
        context = _sanitize_context({'honorific_rules': [
            {'speaker': 'Nữ nhân', 'rule': "Có thể dùng chàng - thiếp hoặc ngươi - ta tùy mức độ thân mật."},
            {'speaker': 'Vợ', 'rule': 'Lời nguồn xác nhận vợ chồng gọi chàng/thiếp.'},
        ]})
        self.assertIn('dùng ta/ngươi', context['honorific_rules'][0]['rule'])
        self.assertEqual(context['honorific_rules'][1]['rule'], 'Lời nguồn xác nhận vợ chồng gọi chàng/thiếp.')

    def test_doraemon_names_are_canonical_without_corrupting_vietnamese_words(self) -> None:
        registry = build_character_name_registry(
            {
                "film_title": "Doraemon: Nobita và chuyến thám hiểm Nam Cực",
                "characters": [
                    {"name": "Gian"},
                    {"name": "Suneo", "aliases": ["Xê-kô"]},
                ],
            }
        )

        self.assertEqual(canonical_character_name("Gian", registry), "Chaien")
        self.assertEqual(canonical_character_name("Jaian", registry), "Chaien")
        self.assertEqual(canonical_character_name("Xê-kô", registry), "Suneo")
        self.assertEqual(
            canonicalize_character_text(
                "Gian và Xê-kô mất một thời gian dài để tìm Suneo.",
                registry,
            ),
            "Chaien và Suneo mất một thời gian dài để tìm Suneo.",
        )
        self.assertEqual(
            canonicalize_character_text("A giant monster appears.", registry),
            "A giant monster appears.",
        )

    def test_context_aliases_and_scene_spelling_variants_work_for_any_film(self) -> None:
        registry = build_character_name_registry(
            {
                "characters": [
                    {"name": "Lục Trạch", "aliases": ["Lu Ze"]},
                    {"name": "Carla"},
                ],
                "glossary": [
                    {"source": "Luc Trach", "target": "Lục Trạch"},
                ],
            },
            observed_names=["Cara"],
        )

        self.assertEqual(canonical_character_name("Cara", registry), "Carla")
        self.assertEqual(
            canonicalize_character_text("Lu Ze gặp Luc Trach và Cara.", registry),
            "Lục Trạch gặp Lục Trạch và Carla.",
        )

    async def test_ai_translation_output_is_forced_back_to_canonical_names(self) -> None:
        class _AliasCompletions:
            async def create(self, **kwargs):
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content=json.dumps(
                                    {
                                        "items": [
                                            {"id": 1, "text": "Gian gọi Xê-kô chạy tới."}
                                        ],
                                        "memory_updates": {},
                                    },
                                    ensure_ascii=False,
                                )
                            )
                        )
                    ]
                )

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=_AliasCompletions())
        )
        original_key = settings.ninerouter_api_key
        try:
            settings.ninerouter_api_key = "test-key"
            with patch.object(settings, "translation_engine", "gemini"), patch(
                "openai.AsyncOpenAI", return_value=fake_client
            ):
                translated = await GeminiTranslation().translate_events(
                    [SubtitleEvent(0.0, 1.0, "Gian calls Suneo.")],
                    "en",
                    "vi",
                    context={
                        "film_title": "Doraemon",
                        "characters": [{"name": "Gian"}, {"name": "Suneo"}],
                    },
                )
        finally:
            settings.ninerouter_api_key = original_key

        self.assertEqual(translated[0].text, "Chaien gọi Suneo chạy tới.")

    async def test_old_google_setting_uses_direct_contextual_ai(self) -> None:
        original_key = settings.ninerouter_api_key
        original_engine = settings.translation_engine
        fake_client = _FakeClient()
        events = [
            SubtitleEvent(float(index), float(index + 1), f"陆泽说第{index + 1}句")
            for index in range(25)
        ]
        try:
            settings.ninerouter_api_key = "test-key"
            settings.translation_engine = "google"
            self.assertIsInstance(get_translation_engine(), GeminiTranslation)
            with patch("openai.AsyncOpenAI", return_value=fake_client), patch(
                "app.services.ai.translation._request_google_translate",
                side_effect=AssertionError("Google must not be called"),
            ):
                translated = await get_translation_engine().translate_events(
                    events,
                    "zh",
                    "vi",
                    context={
                        "characters": [{"name": "Lục Trạch", "source": "陆泽"}],
                        "relationships": [],
                    },
                    processing_mode="quality",
                )
        finally:
            settings.ninerouter_api_key = original_key
            settings.translation_engine = original_engine

        self.assertEqual(len(translated), len(events))
        self.assertEqual(
            [(item.start, item.end) for item in translated],
            [(item.start, item.end) for item in events],
        )
        self.assertTrue(all("Lục Trạch" in item.text for item in translated))
        calls = fake_client.chat.completions.calls
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call["model"] == settings.ai_model for call in calls))
        second_payload = json.loads(calls[1]["messages"][1]["content"])
        self.assertTrue(second_payload["previous_context"])
        self.assertEqual(
            second_payload["memory"]["characters"][0]["name"],
            "Lục Trạch",
        )

    async def test_ai_relationship_terms_are_not_rewritten(self) -> None:
        class _RelationshipCompletions:
            async def create(self, **kwargs):
                content = json.dumps(
                    {
                        "items": [{"id": 1, "text": "Đó là vợ của anh ấy."}],
                        "memory_updates": {
                            "characters": [],
                            "relationships": ["hai người là vợ chồng"],
                            "glossary": {},
                            "addressing_rules": [],
                        },
                    },
                    ensure_ascii=False,
                )
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
                )

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=_RelationshipCompletions())
        )
        original_key = settings.ninerouter_api_key
        try:
            settings.ninerouter_api_key = "test-key"
            with patch.object(settings, "translation_engine", "gemini"), patch(
                "openai.AsyncOpenAI", return_value=fake_client
            ):
                translated = await GeminiTranslation().translate_events(
                    [SubtitleEvent(0.0, 1.0, "She is his wife.")],
                    "en",
                    "vi",
                    context={"relationships": ["They are married."]},
                )
        finally:
            settings.ninerouter_api_key = original_key

        self.assertEqual(translated[0].text, "Đó là vợ của anh ấy.")

    def test_context_relationship_evidence_is_preserved(self) -> None:
        context = _sanitize_context(
            {
                "characters": [{"name": "Anna"}, {"name": "Minh"}],
                "relationships": "Anna là vợ của Minh.",
            },
            "She is his wife.",
        )
        self.assertEqual(context["relationships"], "Anna là vợ của Minh.")

    def test_ai_model_is_defined_once_in_python_sources(self) -> None:
        app_dir = Path(__file__).resolve().parents[1] / "app"
        occurrences: list[Path] = []
        for source in app_dir.rglob("*.py"):
            if "ag/gemini-3.8-flash-medium" in source.read_text(encoding="utf-8"):
                occurrences.append(source)
        self.assertEqual(occurrences, [app_dir / "core.py"])


if __name__ == "__main__":
    unittest.main()
