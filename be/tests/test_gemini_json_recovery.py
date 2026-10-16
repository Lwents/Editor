import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.ai.gemini_web import (
    GeminiWebClient, GeminiWebMalformedReply, parse_reply, parse_partial_reply,
)
from app.services.ai.translation import TranslationBatchStructureError


class JsonRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_untranslated_chinese_retries_only_the_bad_cue(self):
        from app.services.ai.translation import _translate_gemini_batch_with_retries
        def reply(items):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'items':items,'memory_updates':{}})))])
        create=AsyncMock(side_effect=[
            reply([{'id':1,'text':'Xin chào'},{'id':2,'text':'凯瑟琳'},{'id':3,'text':'Mời vào'}]),
            reply([{'id':2,'text':'Catherine'}]),
        ])
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        result=await _translate_gemini_batch_with_retries(client,
            [{'id':1,'source':'hello'},{'id':2,'source':'Catherine'},{'id':3,'source':'come in'}],
            'en','vi',{}, {},[],[],retries=1)
        self.assertEqual(result[0],['Xin chào','Catherine','Mời vào'])
        self.assertEqual(create.await_count,2)
        retried=json.loads(create.call_args.kwargs['messages'][1]['content'])['current_items']
        self.assertEqual([item['id'] for item in retried],[2])

    def test_actual_failure_shape_raw_quotes_in_one_of_twelve_items(self):
        data = {'request_id': 'nonce', 'items': [
            {'id': i, 'text': 'Anh nói "từng hạt đều gian nan", rồi quay đi.' if i == 39 else f'Câu {i}'}
            for i in range(37,49)], 'memory_updates': {}}
        broken = 'Gemini đã nói\n' + json.dumps(data, ensure_ascii=False).replace('\\"', '"')
        self.assertEqual(parse_reply(broken, 'nonce'), data)

    def test_valid_escaped_quotes_and_backslashes_remain_unchanged(self):
        data = {'request_id': 'nonce', 'items': [{'id': 1, 'text': 'Đường C:\\phim và "lời nói".'}]}
        self.assertEqual(parse_reply(json.dumps(data), 'nonce'), data)

    def test_quote_repair_does_not_accept_an_old_request(self):
        text = '{"request_id":"old","items":[{"id":1,"text":"Anh nói "đi thôi"."}],"memory_updates":{}}'
        self.assertIsNone(parse_reply(text, 'current'))
        self.assertIsNone(parse_partial_reply(text, 'current'))

    def test_truncation_never_invents_the_last_translation(self):
        text = '{"request_id":"nonce","items":[{"id":37,"text":"Đã xong."},{"id":38,"text":"Chưa'
        self.assertIsNone(parse_reply(text, 'nonce'))
        self.assertEqual(parse_partial_reply(text, 'nonce')['items'], [{'id':37,'text':'Đã xong.'}])

    def test_unterminated_first_item_is_not_salvaged(self):
        self.assertIsNone(parse_partial_reply('{"request_id":"nonce","items":[{"id":1,"text":"Chưa', 'nonce'))

    def test_partial_result_does_not_promote_unfinished_memory(self):
        text = '{"request_id":"nonce","items":[{"id":1,"text":"Xong"}],"memory_updates":{"characters":['
        result = parse_partial_reply(text, 'nonce')
        self.assertEqual(result['items'], [{'id':1,'text':'Xong'}])
        self.assertEqual(result['memory_updates'], {})

    async def test_unrecoverable_json_enters_existing_batch_split_recovery(self):
        with patch('app.services.ai.gemini_web.browser.complete', new=AsyncMock(side_effect=GeminiWebMalformedReply('invalid'))):
            with self.assertRaises(TranslationBatchStructureError):
                await GeminiWebClient().create(messages=[])

    async def test_partial_items_go_through_normal_translation_validation(self):
        partial = {'request_id':'nonce','items':[{'id':37,'text':'Xong'}], 'memory_updates':{}}
        with patch('app.services.ai.gemini_web.browser.complete', new=AsyncMock(side_effect=GeminiWebMalformedReply('invalid', partial))):
            result = await GeminiWebClient().create(messages=[])
        self.assertEqual(json.loads(result.choices[0].message.content), partial)
