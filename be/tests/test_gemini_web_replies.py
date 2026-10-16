import re
import json
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.services.ai.gemini_web import GeminiBrowser, GeminiWebError, GeminiWebRefusal, GeminiWebTimeout, GeminiWebVerificationRequired, GeminiWebMalformedReply, is_refusal


class FakePage:
    def __init__(self, *, belongs=True, generating_until=0):
        self.seconds = 0.
        self.stop_clicks = 0
        self.belongs = belongs
        self.generating_until = generating_until
        self.visible_request = False
        self.url = 'https://gemini.google.com/app'
        self.navigations = []
        self.response = SimpleNamespace(inner_text=lambda: 'Tôi không được thiết kế để trợ giúp về điều đó.',
                                        evaluate=lambda script, nonce: self.belongs)
        self.stop = SimpleNamespace(is_visible=lambda: self.seconds < self.generating_until,
                                    click=lambda **kwargs: setattr(self, "stop_clicks", self.stop_clicks + 1))
        self.editor = SimpleNamespace(fill=self.fill, press=lambda key: None)

    def fill(self, prompt, **kwargs):
        self.nonce = re.search(r'request_id chính xác: "([a-f0-9]+)"', prompt).group(1)

    def locator(self, selector):
        if selector == 'model-response':
            return SimpleNamespace(count=lambda: 1, last=self.response)
        if selector == 'user-query':
            return SimpleNamespace(count=lambda:1,last=SimpleNamespace(evaluate=lambda script,nonce:self.visible_request))
        return SimpleNamespace(count=lambda: 1, nth=lambda index: self.stop)

    def goto(self, *args, **kwargs):
        self.navigations.append(args[0])
        self.url = args[0]

    def wait_for_timeout(self, milliseconds):
        self.seconds += milliseconds / 1000

    def is_closed(self):
        return False


class GeminiReplyTests(unittest.TestCase):
    def test_one_video_reuses_chat_and_another_video_starts_a_separate_chat(self):
        browser = GeminiBrowser()
        page = FakePage()
        page.response.inner_text = lambda: json.dumps({'request_id': page.nonce, 'items': [{'id': 1, 'text': 'Đạt'}]})
        with patch.object(browser, '_connect', return_value=page), \
                patch.object(browser, '_set_visible'), \
                patch.object(browser, '_wait_ready', return_value=True), \
                patch.object(browser, '_editor', return_value=page.editor), \
                patch.object(browser, '_verification_message', return_value=None), \
                patch.object(browser, '_conversation_urls', return_value={}), \
                patch.object(browser, '_remember_conversation'), \
                patch('app.services.ai.gemini_web.time.monotonic', side_effect=lambda: page.seconds):
            for scope in ('video-a', 'video-a', 'video-b'):
                browser._complete_unlocked([{'content': 'current_items=[]'}], 30, Event(), conversation=scope)
        self.assertEqual(len(page.navigations), 2)

    def test_resume_restores_only_the_chat_for_the_same_video(self):
        browser = GeminiBrowser()
        page = FakePage()
        page.response.inner_text = lambda: json.dumps({'request_id': page.nonce, 'items': [{'id': 1, 'text': 'Đạt'}]})
        saved = 'https://gemini.google.com/app/saved-video-a'
        with patch.object(browser, '_connect', return_value=page), \
                patch.object(browser, '_set_visible'), \
                patch.object(browser, '_wait_ready', return_value=True), \
                patch.object(browser, '_editor', return_value=page.editor), \
                patch.object(browser, '_verification_message', return_value=None), \
                patch.object(browser, '_conversation_urls', return_value={'video-a': saved}), \
                patch.object(browser, '_remember_conversation'), \
                patch('app.services.ai.gemini_web.time.monotonic', side_effect=lambda: page.seconds):
            browser._complete_unlocked([{'content': 'current_items=[]'}], 30, Event(), conversation='video-a')
        self.assertEqual(page.navigations, [saved])

    def complete(self, page, on_wait=None):
        browser = GeminiBrowser()
        with patch.object(browser, '_connect', return_value=page), \
                patch.object(browser, '_set_visible'), \
                patch.object(browser, '_wait_ready', return_value=True), \
                patch.object(browser, '_verification_message', return_value=None), \
                patch.object(browser, '_editor', return_value=page.editor), \
                patch('app.services.ai.gemini_web.time.monotonic', side_effect=lambda: page.seconds):
            return browser._complete_unlocked([{'content': 'current_items=[]'}], 30, Event(), on_wait=on_wait)

    def test_complete_json_does_not_wait_for_a_stuck_generation_indicator(self):
        page = FakePage(generating_until=50)
        page.response.inner_text = lambda: json.dumps({'request_id': page.nonce, 'items': [{'id': 1, 'text': 'Đạt'}]})
        result = self.complete(page)
        self.assertEqual(json.loads(result)['items'][0]['text'], 'Đạt')
        self.assertEqual(page.stop_clicks, 1)
        self.assertLess(page.seconds, 10)

    def test_finished_refusal_is_reported_without_waiting_full_timeout(self):
        page = FakePage()
        with self.assertRaisesRegex(GeminiWebRefusal, 'Gemini từ chối yêu cầu'):
            self.complete(page)
        self.assertLess(page.seconds, 5)

    def test_screenshot_refusal_is_recognized(self):
        self.assertTrue(is_refusal('Tôi là một mô hình ngôn ngữ nên điều đó nằm ngoài mục đích mà tôi được tạo ra.'))
        self.assertTrue(is_refusal('Tôi không thể trợ giúp về điều đó, vì tôi chỉ là một mô hình ngôn ngữ.'))
        self.assertTrue(is_refusal('Tôi không được lập trình để làm điều đó.'))
        self.assertTrue(is_refusal('Yêu cầu của bạn nằm ngoài khả năng tôi được lập trình. Tôi chỉ có thể tạo văn bản.'))
        self.assertTrue(is_refusal('Tôi chỉ là một công nghệ trí tuệ nhân tạo dựa trên văn bản, nên không thể trợ giúp về điều đó.'))
        self.assertFalse(is_refusal('Chữ nhìn thấy là 主子.'))

    def test_formatting_error_is_distinct_from_refusal(self):
        page = FakePage()
        page.response.inner_text = lambda: 'Đây là bản dịch nhưng thiếu JSON.'
        with self.assertRaisesRegex(GeminiWebError, 'không đúng JSON') as error:
            self.complete(page)
        self.assertNotIsInstance(error.exception, GeminiWebRefusal)

    def test_stream_pause_without_stop_button_does_not_fail_after_three_seconds(self):
        page = FakePage()
        page.response.inner_text = lambda: (
            '{"request_id":"' + page.nonce + '","items":[{"id":1,"text":"Đang'
            if page.seconds < 10 else json.dumps({'request_id':page.nonce,'items':[{'id':1,'text':'Đã xong'}]})
        )
        result = self.complete(page)
        self.assertEqual(json.loads(result)['items'][0]['text'], 'Đã xong')
        self.assertGreaterEqual(page.seconds, 12)

    def test_refused_job_discards_only_its_chat_pointer_for_resume(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as directory, patch('app.services.ai.gemini_web.profile_directory', return_value=Path(directory)):
            path = Path(directory) / 'conversations.json'
            path.write_text(json.dumps({'failed': 'https://gemini.google.com/app/a',
                                        'other': 'https://gemini.google.com/app/b'}))
            browser = GeminiBrowser()
            browser._active_conversation = 'failed'
            browser._forget_failed_conversation('failed')
            self.assertEqual(json.loads(path.read_text()), {'other': 'https://gemini.google.com/app/b'})
            self.assertIsNone(browser._active_conversation)

    def test_still_generating_response_is_not_rejected_early(self):
        page = FakePage(generating_until=5)
        with self.assertRaisesRegex(GeminiWebRefusal, 'Gemini từ chối yêu cầu'):
            self.complete(page)
        self.assertGreaterEqual(page.seconds, 8)

    def test_response_from_an_old_query_is_not_treated_as_current_refusal(self):
        page = FakePage(belongs=False)
        with self.assertRaisesRegex(GeminiWebError, 'Quá thời gian'):
            self.complete(page)
        self.assertGreaterEqual(page.seconds, 30)

    def test_waiting_status_reports_elapsed_seconds_without_accepting_old_response(self):
        page = FakePage(belongs=False)
        updates = []
        with self.assertRaisesRegex(GeminiWebError, 'Quá thời gian'):
            self.complete(page, on_wait=updates.append)
        self.assertEqual(updates, [15, 30])

    def test_active_stream_can_finish_after_the_old_absolute_timeout(self):
        page = FakePage(generating_until=100)
        def response():
            if page.seconds >= 60:
                return json.dumps({'request_id':page.nonce,'items':[{'id':1,'text':'Đã hoàn tất'}]})
            return '{"request_id":"'+page.nonce+'","items":[{"id":1,"text":"'+('chữ ' * (1+int(page.seconds//10)))
        page.response.inner_text = response
        result = self.complete(page)
        self.assertEqual(json.loads(result)['items'][0]['text'],'Đã hoàn tất')
        self.assertGreater(page.seconds,30)
        self.assertLess(page.seconds,90)

    def test_changing_old_response_does_not_extend_current_wait(self):
        page = FakePage(belongs=False,generating_until=1000)
        page.response.inner_text = lambda:'old reply '+str(page.seconds)
        with self.assertRaises(GeminiWebTimeout):
            self.complete(page)
        self.assertEqual(page.seconds,30)

    def test_neverending_current_stream_has_a_hard_limit(self):
        page = FakePage(generating_until=1000)
        page.response.inner_text = lambda:'{"request_id":"'+page.nonce+'","items":[{"id":1,"text":"'+str(page.seconds)
        with self.assertRaises(GeminiWebTimeout):
            self.complete(page)
        self.assertEqual(page.seconds,90)

    def test_confirmed_current_thinking_gets_one_bounded_extra_window(self):
        page = FakePage(generating_until=1000)
        page.response.inner_text = lambda: 'Đang suy nghĩ'
        with self.assertRaises(GeminiWebTimeout):
            self.complete(page)
        self.assertEqual(page.seconds,60)

    def test_submitted_query_without_generation_reconnects_same_chat_once(self):
        page=FakePage(belongs=False)
        page.visible_request=True
        reloads=[]
        def reload(**kwargs):
            reloads.append(page.url)
            page.belongs=True
            page.response.inner_text=lambda:json.dumps({'request_id':page.nonce,'items':[{'id':1,'text':'Khôi phục được'}]})
        page.reload=reload
        result=self.complete(page)
        self.assertEqual(json.loads(result)['items'][0]['text'],'Khôi phục được')
        self.assertEqual(len(reloads),1)

    def test_stalled_transport_reconnect_is_bounded_and_does_not_resubmit(self):
        page=FakePage(belongs=False)
        page.visible_request=True
        reloads=[]
        page.reload=lambda **kwargs:reloads.append(page.url)
        with self.assertRaisesRegex(GeminiWebTimeout,'không bắt đầu trả lời'):
            self.complete(page)
        self.assertEqual(len(reloads),1)
        self.assertEqual(page.seconds,60)

    def test_idle_truncated_reply_keeps_only_complete_current_items(self):
        page = FakePage(generating_until=1000)
        page.response.inner_text = lambda:'{"request_id":"'+page.nonce+'","items":[{"id":1,"text":"Đã xong"},{"id":2,"text":"chưa'
        with self.assertRaises(GeminiWebMalformedReply) as error:
            self.complete(page)
        self.assertEqual(error.exception.partial['items'],[{'id':1,'text':'Đã xong'}])

    def test_timeout_retains_chat_address_for_late_reply_recovery(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from app.services.ai.gemini_models import model_policy
        page=FakePage(belongs=False,generating_until=1000)
        original_wait=page.wait_for_timeout
        def waiting(milliseconds):
            original_wait(milliseconds)
            page.url='https://gemini.google.com/app/late-reply'
        page.wait_for_timeout=waiting
        browser=GeminiBrowser()
        with TemporaryDirectory() as directory, \
                patch('app.services.ai.gemini_web.profile_directory',return_value=Path(directory)), \
                patch('app.services.ai.gemini_web.select_model',return_value={'model':'Flash','thinking':'medium'}), \
                patch.object(browser,'_connect',return_value=page), \
                patch.object(browser,'_set_visible'), \
                patch.object(browser,'_wait_ready',return_value=True), \
                patch.object(browser,'_editor',return_value=page.editor), \
                patch.object(browser,'_verification_message',return_value=None), \
                patch.object(browser,'_conversation_urls',return_value={}), \
                patch.object(browser,'_remember_conversation') as remember, \
                patch.object(browser,'_forget_failed_conversation') as forget, \
                patch('app.services.ai.gemini_web.time.monotonic',side_effect=lambda:page.seconds):
            with self.assertRaises(GeminiWebTimeout):
                browser._complete_unlocked([{'content':'fixture'}],30,Event(),conversation='late',policy=model_policy('balanced'))
            saved=json.loads((Path(directory)/'request-journal/late.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['url'],'https://gemini.google.com/app/late-reply')
            self.assertEqual(saved['status'],'sent')
            self.assertEqual(saved['request_id'],page.nonce)
            remember.assert_called()
            forget.assert_not_called()

    def test_google_challenge_is_identified_before_translation(self):
        page = SimpleNamespace(url='https://gemini.google.com/app', locator=lambda selector:
            SimpleNamespace(inner_text=lambda **kwargs: 'Our systems have detected unusual traffic from your computer network.'))
        self.assertIn('CAPTCHA', GeminiBrowser._verification_message(page))

    def test_normal_gemini_page_does_not_report_a_challenge(self):
        page = SimpleNamespace(url='https://gemini.google.com/app', locator=lambda selector:
            SimpleNamespace(inner_text=lambda **kwargs: 'Trò chuyện với Gemini'))
        self.assertIsNone(GeminiBrowser._verification_message(page))


class GeminiVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_translation_does_not_replay_a_timed_out_request(self):
        from app.services.ai.translation import _translate_gemini_batch_with_retries
        complete = AsyncMock(side_effect=GeminiWebTimeout('Quá thời gian'))
        with patch('app.services.ai.translation._translate_gemini_batch', complete):
            with self.assertRaises(GeminiWebTimeout):
                await _translate_gemini_batch_with_retries(None, [{'id': 1, 'source': '小心'}],
                                                          'zh', 'vi', {}, {}, [], [], retries=3)
        self.assertEqual(complete.await_count, 1)

    async def test_translation_clarifies_refusal_once_then_stops(self):
        from app.services.ai.translation import _translate_gemini_batch_with_retries
        complete = AsyncMock(side_effect=GeminiWebRefusal('Gemini từ chối'))
        with patch('app.services.ai.translation._translate_gemini_batch', complete):
            with self.assertRaises(GeminiWebRefusal):
                await _translate_gemini_batch_with_retries(None, [{'id': 1, 'source': '小心'}],
                                                          'zh', 'vi', {}, {}, [], [], retries=3)
        self.assertEqual(complete.await_count, 2)
        self.assertFalse(complete.await_args_list[0].kwargs['refusal_rephrased'])
        self.assertTrue(complete.await_args_list[1].kwargs['refusal_rephrased'])

    async def test_translation_recovers_after_one_task_clarification(self):
        from app.services.ai.translation import _translate_gemini_batch_with_retries
        complete = AsyncMock(side_effect=[GeminiWebRefusal('Không thể trợ giúp'), (['Cẩn thận'], {})])
        with patch('app.services.ai.translation._translate_gemini_batch', complete):
            result = await _translate_gemini_batch_with_retries(None, [{'id': 1, 'source': '小心'}],
                                                              'zh', 'vi', {}, {}, [], [], retries=1)
        self.assertEqual(result, (['Cẩn thận'], {}))
        self.assertEqual(complete.await_count, 2)

    async def test_translation_does_not_retry_google_verification(self):
        from app.services.ai.translation import _translate_gemini_batch_with_retries
        complete = AsyncMock(side_effect=GeminiWebVerificationRequired('CAPTCHA'))
        with patch('app.services.ai.translation._translate_gemini_batch', complete):
            with self.assertRaises(GeminiWebVerificationRequired):
                await _translate_gemini_batch_with_retries(None, [{'id': 1, 'source': '小心'}],
                                                          'zh', 'vi', {}, {}, [], [], retries=3)
        self.assertEqual(complete.await_count, 1)


if __name__ == '__main__':
    unittest.main()
