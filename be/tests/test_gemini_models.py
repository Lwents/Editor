from __future__ import annotations

import unittest
import json
from threading import Event
from unittest.mock import AsyncMock, patch

from app.services.ai.gemini_models import active_model_policy, current_model_key, model_policy, ModelSelectionError
from app.services.ai.gemini_web import GeminiBrowser, GeminiWebModelError
from tests.test_gemini_web_replies import FakePage


class GeminiModelTests(unittest.IsolatedAsyncioTestCase):
    def test_mode_and_thinking_mapping(self):
        self.assertEqual([(model_policy(m).model, model_policy(m).thinking) for m in ('fast', 'balanced', 'quality')],
                         [('3.8 Flash', 'low'), ('3.8 Flash', 'medium'), ('3.8 Flash', 'high')])

    def test_nested_modes_restore_and_separate_conversations(self):
        browser = GeminiBrowser()
        self.assertIsNone(active_model_policy.get())
        with browser.conversation('same-video', processing_mode='fast'):
            from app.services.ai.gemini_web import _conversation_scope
            fast_scope = _conversation_scope.get()
            fast_key = current_model_key()
            with browser.conversation('same-video', processing_mode='quality'):
                self.assertNotEqual(_conversation_scope.get(), fast_scope)
                self.assertNotEqual(current_model_key(), fast_key)
            self.assertEqual(current_model_key(), fast_key)
        self.assertIsNone(active_model_policy.get())

    async def test_policy_is_explicitly_transferred_to_browser_thread(self):
        browser = GeminiBrowser()
        with patch.object(browser, '_run', new=AsyncMock(return_value='ok')) as run:
            with browser.conversation('clip', processing_mode='quality'):
                await browser.complete([{'content': 'test'}])
        self.assertEqual(run.call_args.args[-1], model_policy('quality'))

    def test_missing_model_prevents_prompt_fill_and_send(self):
        browser, page = GeminiBrowser(), FakePage()
        with patch.object(browser, '_connect', return_value=page), \
                patch.object(browser, '_set_visible'), \
                patch.object(browser, '_wait_ready', return_value=True), \
                patch('app.services.ai.gemini_web.select_model', side_effect=ModelSelectionError('GEMINI_MODEL_UNAVAILABLE')), \
                patch.object(page.editor, 'fill') as fill, \
                patch.object(page.editor, 'press') as send:
            with self.assertRaises(GeminiWebModelError):
                browser._complete_unlocked([], 30, Event(), policy=model_policy('quality'))
        fill.assert_not_called()
        send.assert_not_called()

    async def test_missing_model_is_not_retried_as_bad_json(self):
        from app.services.ai.translation import _translate_gemini_batch_with_retries
        with patch('app.services.ai.translation._translate_gemini_batch', new=AsyncMock(side_effect=GeminiWebModelError('missing'))) as call:
            with self.assertRaises(GeminiWebModelError):
                await _translate_gemini_batch_with_retries(None, [{'id': 1, 'source': '是'}], 'zh', 'vi', {}, {}, [], [], retries=3)
        self.assertEqual(call.await_count, 1)

    def test_menu_dom_failure_is_rechecked_before_the_only_send(self):
        browser, page = GeminiBrowser(), FakePage()
        page.response.inner_text = lambda: json.dumps({'request_id':page.nonce,'items':[{'id':1,'text':'Đạt'}]})
        transient = ModelSelectionError('menu replaced')
        transient.__cause__ = TimeoutError('DOM replaced')
        with patch.object(browser, '_connect', return_value=page), \
                patch.object(browser, '_set_visible'), \
                patch.object(browser, '_wait_ready', return_value=True), \
                patch.object(browser, '_verification_message', return_value=None), \
                patch.object(browser, '_editor', return_value=page.editor), \
                patch('app.services.ai.gemini_web.time.monotonic', side_effect=lambda: page.seconds), \
                patch('app.services.ai.gemini_web.select_model', side_effect=[transient, {'model':'3.8 Flash','thinking':'medium'}]) as select, \
                patch.object(page.editor, 'press') as send:
            result = browser._complete_unlocked([], 30, Event(), policy=model_policy('balanced'))
        self.assertEqual(select.call_count, 2)
        send.assert_called_once_with('Enter')
        self.assertEqual(json.loads(result)['items'][0]['text'], 'Đạt')
