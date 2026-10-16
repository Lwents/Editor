"""Gemini can show Stop before mounting any new model-response element."""
import json
import unittest
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
from app.services.ai.gemini_web import GeminiBrowser,GeminiWebTimeout
from tests.test_gemini_web_replies import FakePage

class InitialGenerationTests(unittest.TestCase):
    def run_response(self,current=True,answer_at=170,generating_until=170):
        page=FakePage(belongs=False,generating_until=generating_until)
        page.visible_request=current
        original=page.locator
        def locator(selector):
            if selector=='model-response':return SimpleNamespace(count=lambda:int(page.seconds>=answer_at),last=page.response)
            return original(selector)
        page.locator=locator
        page.response.inner_text=lambda:json.dumps({'request_id':page.nonce,'items':[{'id':1,'text':'Đã dịch'}]})
        browser=GeminiBrowser()
        with patch.object(browser,'_connect',return_value=page),patch.object(browser,'_set_visible'),patch.object(browser,'_wait_ready',return_value=True),patch.object(browser,'_editor',return_value=page.editor),patch.object(browser,'_verification_message',return_value=None),patch.object(browser,'_remember_conversation'),patch('app.services.ai.gemini_web.time.monotonic',side_effect=lambda:page.seconds):
            result=browser._complete_unlocked([{'content':'current_items=[1]'}],120,Event())
        return result,page.seconds

    def test_current_request_can_think_170_seconds_before_response_exists(self):
        result,seconds=self.run_response()
        self.assertEqual(json.loads(result)['items'][0]['text'],'Đã dịch')
        self.assertGreater(seconds,170)
        self.assertLess(seconds,180)

    def test_stop_button_without_matching_current_query_gets_no_extra_window(self):
        with self.assertRaises(GeminiWebTimeout):self.run_response(current=False)

    def test_stop_disappears_just_before_first_answer_is_mounted(self):
        result,seconds=self.run_response(generating_until=169)
        self.assertEqual(json.loads(result)['items'][0]['text'],'Đã dịch')
        self.assertGreater(seconds,170)

    def test_stuck_stop_and_matched_query_still_have_bounded_silence(self):
        with self.assertRaisesRegex(GeminiWebTimeout,'240'):
            self.run_response(answer_at=10000,generating_until=10000)
