"""Regressions for context limits, virtualized history and live Thinking progress."""
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from app.services.ai.deepseek_web import DeepSeekBrowser, DeepSeekRateLimit
from app.services.ai.gemini_web import GeminiBrowser, GeminiWebTimeout, WebConversationLimit
from app.services.ai.request_journal import RequestJournal
from tests.test_gemini_web_replies import FakePage


class DeepSeekRecoveryTests(unittest.TestCase):
    def test_explicit_chat_limit_invalidates_only_pending_request_and_retries_once(self):
        browser=DeepSeekBrowser();browser._page=SimpleNamespace(url='https://chat.deepseek.com/a/chat/s/old')
        messages=[{'content':'unfinished cue'}];policy=browser._policy('balanced');cancelled=Event()
        with tempfile.TemporaryDirectory() as folder,patch.object(browser,'profile_directory',return_value=Path(folder)):
            journal=RequestJournal(Path(folder),'scope',messages,policy)
            journal.save('sent','a'*32,browser._page.url)
            with patch.object(GeminiBrowser,'_complete_unlocked',side_effect=[WebConversationLimit('limit'),'ok']) as send,patch.object(browser,'_rotate_chat') as rotate:
                result=browser._complete_unlocked(messages,40,cancelled,None,None,'scope',None,policy)
            self.assertEqual(result,'ok');self.assertEqual(send.call_count,2)
            rotate.assert_called_once_with(browser._page,'scope',cancelled)
            self.assertEqual(journal.load()['status'],'chat_limit')

    def test_repeated_context_limit_is_not_an_infinite_retry(self):
        browser=DeepSeekBrowser();browser._page=SimpleNamespace(url='https://chat.deepseek.com/a/chat/s/old')
        with patch.object(GeminiBrowser,'_complete_unlocked',side_effect=WebConversationLimit('limit')) as send,patch.object(browser,'_rotate_chat') as rotate:
            with self.assertRaises(WebConversationLimit):browser._complete_unlocked([],40,Event())
        self.assertEqual(send.call_count,2);self.assertEqual(rotate.call_count,1)

    def test_turn_budget_survives_browser_restart_and_ignores_virtualized_dom_count(self):
        page=SimpleNamespace(url='https://chat.deepseek.com/a/chat/s/old')
        with tempfile.TemporaryDirectory() as folder:
            first,second=DeepSeekBrowser(),DeepSeekBrowser()
            with patch.object(first,'profile_directory',return_value=Path(folder)),patch.object(second,'profile_directory',return_value=Path(folder)):
                for _ in range(8):first._on_sent(page,'short prompt','scope')
                with patch.object(second,'_rotate_chat') as rotate,patch.object(second,'_select_model') as select:
                    second._before_send(page,'next','scope',Event(),second._policy('balanced'))
                rotate.assert_called_once();select.assert_called_once()
                self.assertEqual(second._usage()['scope']['turns'],8)

    def test_prompt_character_budget_rotates_before_next_upload(self):
        browser=DeepSeekBrowser();page=SimpleNamespace(url='https://chat.deepseek.com/a/chat/s/old')
        with patch.object(browser,'_usage',return_value={'scope':{'url':page.url,'turns':2,'chars':79000}}),patch.object(browser,'_rotate_chat') as rotate,patch.object(browser,'_select_model'):
            browser._before_send(page,'x'*2000,'scope',Event(),browser._policy('fast'))
        rotate.assert_called_once()

    def test_long_answers_and_thinking_are_counted_once_across_restart(self):
        page=SimpleNamespace(url='https://chat.deepseek.com/a/chat/s/old',evaluate=lambda *a:65000)
        with tempfile.TemporaryDirectory() as folder:
            first,second=DeepSeekBrowser(),DeepSeekBrowser()
            with patch.object(first,'profile_directory',return_value=Path(folder)),patch.object(second,'profile_directory',return_value=Path(folder)):
                first._on_sent(page,'x'*14000,'scope')
                first._on_reply(page,'a'*32,'scope')
                second._on_reply(page,'a'*32,'scope')
                self.assertEqual(second._usage()['scope']['chars'],79000)
                with patch.object(second,'_rotate_chat') as rotate,patch.object(second,'_select_model'):
                    second._before_send(page,'x'*2000,'scope',Event(),second._policy('balanced'))
                rotate.assert_called_once()

    def test_visible_reply_history_protects_usage_created_by_older_version(self):
        page=SimpleNamespace(url='https://chat.deepseek.com/a/chat/s/old',evaluate=lambda *a:79000)
        browser=DeepSeekBrowser()
        with patch.object(browser,'_usage',return_value={'scope':{'url':page.url,'turns':2,'chars':5000}}),patch.object(browser,'_rotate_chat') as rotate,patch.object(browser,'_select_model'):
            browser._before_send(page,'x'*2000,'scope',Event(),browser._policy('balanced'))
        rotate.assert_called_once()

    def run_stream(self, *, changing=True, owned=True):
        page=FakePage(belongs=False,generating_until=95)
        page.url='https://chat.deepseek.com/'
        original=page.locator
        def locator(selector):
            if selector=='.ds-assistant-message-main-content':
                return SimpleNamespace(count=lambda:int(page.seconds>=95),last=page.response)
            return original(selector)
        page.locator=locator
        page.response.inner_text=lambda:json.dumps({'request_id':page.nonce,'items':[{'id':1,'text':'Đạt'}]})
        browser=DeepSeekBrowser()
        def activity(*args):
            result={'generating':page.seconds<95}
            if owned and page.seconds<95:result['thinking']=str(int(page.seconds)) if changing else 'unchanged'
            return result
        with patch.object(browser,'_connect',return_value=page),patch.object(browser,'_set_visible'),patch.object(browser,'_wait_ready',return_value=True),patch.object(browser,'_editor',return_value=page.editor),patch.object(browser,'_verification_message',return_value=None),patch.object(browser,'_remember_conversation'),patch.object(browser,'_before_send'),patch.object(browser,'_on_sent'),patch.object(browser,'_generation_activity',side_effect=activity),patch('app.services.ai.gemini_web.time.monotonic',side_effect=lambda:page.seconds):
            result=browser._complete_unlocked([{'content':'test'}],40,Event())
        return result,page.seconds

    def test_growing_thinking_before_any_final_answer_does_not_time_out(self):
        reply,seconds=self.run_stream()
        self.assertEqual(json.loads(reply)['items'][0]['text'],'Đạt')
        self.assertGreater(seconds,95)

    def test_unchanged_thinking_has_bounded_idle_wait(self):
        with self.assertRaises(GeminiWebTimeout):self.run_stream(changing=False)

    def test_old_thinking_cannot_extend_the_current_request(self):
        with self.assertRaises(GeminiWebTimeout):self.run_stream(owned=False)

    def test_length_limit_notice_is_a_distinct_error_not_a_timeout(self):
        limit=SimpleNamespace(count=lambda:1,nth=lambda i:SimpleNamespace(is_visible=lambda:True))
        page=SimpleNamespace(get_by_text=lambda *a,**kw:limit)
        with self.assertRaises(WebConversationLimit):DeepSeekBrowser()._verification_message(page)

    def test_inline_rate_limit_is_distinct_and_not_retried_as_timeout(self):
        browser = DeepSeekBrowser(); browser._rate_monitoring = True
        browser._active_request_id = 'a'*32
        page = SimpleNamespace(evaluate=lambda script,nonce:'Messages too frequent. Try again later.' if nonce=='a'*32 else None)
        with self.assertRaises(DeepSeekRateLimit): browser._verification_message(page)

    def test_rate_limit_retries_once_after_cooldown_without_rotating_chat(self):
        browser = DeepSeekBrowser()
        reloads=[]
        browser._page=SimpleNamespace(url='https://chat.deepseek.com/a/chat/s/old',reload=lambda **kw:reloads.append(kw))
        policy=browser._policy('balanced');messages=[{'content':'preserve these source cues'}]
        with tempfile.TemporaryDirectory() as folder, patch.object(browser,'profile_directory',return_value=Path(folder)), patch.object(browser,'_wait_cooldown') as wait, patch.object(browser,'_rotate_chat') as rotate:
            journal=RequestJournal(Path(folder),'scope',messages,policy)
            journal.save('sent','a'*32,browser._page.url)
            with patch.object(GeminiBrowser,'_complete_unlocked',side_effect=[DeepSeekRateLimit('rate'),'ok']) as send:
                self.assertEqual(browser._complete_unlocked(messages,40,Event(),conversation='scope',policy=policy),'ok')
            self.assertEqual(send.call_count,2);self.assertEqual(wait.call_count,2)
            self.assertEqual(journal.load()['status'],'rate_limited')
            self.assertEqual(len(reloads),1);rotate.assert_not_called()

    def test_repeated_rate_limit_stops_ordinary_retries_and_persists_cooldown(self):
        from app.services.ai.gemini_web import GeminiWebVerificationRequired
        browser=DeepSeekBrowser();browser._page=SimpleNamespace(reload=lambda **kw:None)
        with tempfile.TemporaryDirectory() as folder, patch.object(browser,'profile_directory',return_value=Path(folder)), patch.object(browser,'_wait_cooldown'), patch.object(GeminiBrowser,'_complete_unlocked',side_effect=DeepSeekRateLimit('rate')) as send:
            with self.assertRaises(GeminiWebVerificationRequired):browser._complete_unlocked([],40,Event())
            self.assertEqual(send.call_count,4)
            self.assertGreater(json.loads((Path(folder)/'send-cooldown.json').read_text())['until'],0)

    def test_rate_monitor_is_bound_to_nonce_from_the_prompt_suffix(self):
        browser=DeepSeekBrowser()
        browser._on_sent(SimpleNamespace(), 'old context {"request_id":"'+'a'*32+'"}\ncurrent {"request_id":"'+'b'*32+'"}',None)
        self.assertEqual(browser._active_request_id,'b'*32)

    def test_rejected_sends_do_not_exhaust_the_conversation_budget(self):
        browser=DeepSeekBrowser();page=SimpleNamespace(url='https://chat.deepseek.com/a/chat/s/old',evaluate=lambda *a:0)
        browser._page=page;policy=browser._policy('balanced')
        with tempfile.TemporaryDirectory() as folder,patch.object(browser,'profile_directory',return_value=Path(folder)):
            for i in range(8):
                nonce=f'{i:032x}';prompt='x'*21000+'{"request_id":"'+nonce+'"}'
                browser._on_sent(page,prompt,'scope')
                browser._reject_delivery(([],40,Event(),None,None,'scope',None,policy),{},'rate_limited')
                browser._reject_delivery(([],40,Event(),None,None,'scope',None,policy),{},'rate_limited')
            record=browser._usage()['scope']
            self.assertEqual((record['turns'],record['chars']),(0,0))
            self.assertEqual(len(record['rejected_ids']),8)
            with patch.object(browser,'_rotate_chat') as rotate:
                browser._before_send(page,'next valid request','scope',Event(),policy)
            rotate.assert_not_called()

    def test_backoff_progress_exposes_reason_and_clears_it_on_cancel(self):
        import time
        from app.services.ai.gemini_web import GeminiWebError
        browser=DeepSeekBrowser();cancel=Event();reasons=[]
        def progress(seconds):
            reasons.append(browser.wait_reason);cancel.set()
        with tempfile.TemporaryDirectory() as folder,patch.object(browser,'profile_directory',return_value=Path(folder)):
            (Path(folder)/'send-cooldown.json').write_text(json.dumps({'until':time.time()+60}))
            with self.assertRaises(GeminiWebError):browser._wait_cooldown(cancel,progress)
        self.assertIn('giới hạn tần suất',reasons[0]);self.assertIsNone(browser.wait_reason)

    def test_cooldown_survives_restart_and_can_be_cancelled(self):
        import time
        from app.services.ai.gemini_web import GeminiWebError
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'send-cooldown.json').write_text(json.dumps({'until':time.time()+60}))
            browser=DeepSeekBrowser();cancel=Event();cancel.set()
            with patch.object(browser,'profile_directory',return_value=Path(folder)), self.assertRaisesRegex(GeminiWebError,'hủy'):
                browser._wait_cooldown(cancel)
