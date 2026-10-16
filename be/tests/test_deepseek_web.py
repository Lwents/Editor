"""Provider isolation and DeepSeek-specific UI gates over the durable pipeline."""
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core import settings
from app.services.ai.deepseek_web import DeepSeekBrowser
from app.services.ai.gemini_web import GeminiBrowser, GeminiWebTimeout, _conversation_scope
from app.services.ai.gemini_models import current_model_key, model_policy, ModelSelectionError
from app.services.ai.web_provider import get_browser, is_web_engine
from app.services.ai.translation import create_translation_client


class DeepSeekTests(unittest.IsolatedAsyncioTestCase):
    def test_dispatch_and_conversation_are_separate_from_gemini(self):
        deep, gemini = DeepSeekBrowser(), GeminiBrowser()
        with patch.object(settings, 'translation_engine', 'deepseek_web'):
            self.assertTrue(is_web_engine())
            self.assertIsInstance(get_browser(), DeepSeekBrowser)
            self.assertIs(create_translation_client().browser, get_browser())
            with deep.conversation('same-video', processing_mode='fast'):
                scope, key = _conversation_scope.get(), current_model_key()
                self.assertEqual(key, 'DeepSeek Web:DeepSeek:off')
                with gemini.conversation('same-video', processing_mode='fast'):
                    self.assertNotEqual(_conversation_scope.get(), scope)
                    self.assertNotEqual(current_model_key(), key)
                self.assertEqual(current_model_key(), key)
            self.assertEqual([model_policy(m).thinking for m in ('fast','balanced','quality')], ['off','on','on'])

    def test_model_toggle_verified_and_search_disabled(self):
        class Toggle:
            def __init__(self, pressed, stuck=False): self.pressed, self.stuck = pressed, stuck
            def wait_for(self, **kw): pass
            def count(self): return 1
            def get_attribute(self, key): return str(self.pressed).lower()
            def click(self, **kw):
                if not self.stuck: self.pressed = not self.pressed
        toggles = {'Search':Toggle(True), 'DeepThink':Toggle(False)}
        page = SimpleNamespace(locator=lambda sel:SimpleNamespace(filter=lambda has_text:toggles[has_text]))
        browser = DeepSeekBrowser()
        browser._select_model(page, browser._policy('quality'))
        self.assertFalse(toggles['Search'].pressed)
        self.assertTrue(toggles['DeepThink'].pressed)
        toggles['Search']=Toggle(True,stuck=True)
        with self.assertRaisesRegex(ModelSelectionError,'DEEPSEEK_MODEL_MISMATCH'):
            browser._select_model(page,browser._policy('fast'))

    def test_resume_urls_reject_other_providers_and_external_hosts(self):
        browser=DeepSeekBrowser()
        with tempfile.TemporaryDirectory() as folder, patch.object(browser,'profile_directory',return_value=Path(folder)):
            saved={'deep':'https://chat.deepseek.com/a/chat/s/123',
                   'gemini':'https://gemini.google.com/app/123',
                   'other':'https://chat.deepseek.com.evil.test/a/chat/s/123',
                   'login':'https://chat.deepseek.com/sign_in'}
            (Path(folder)/'conversations.json').write_text(json.dumps(saved))
            self.assertEqual(browser._conversation_urls(),{'deep':saved['deep']})

    def test_received_journal_recovers_without_open_or_resend(self):
        from app.services.ai.request_journal import RequestJournal
        browser=DeepSeekBrowser();policy=browser._policy('fast');messages=[{'content':'test'}]
        with tempfile.TemporaryDirectory() as folder, patch.object(browser,'profile_directory',return_value=Path(folder)), patch.object(browser,'_connect') as connect:
            journal=RequestJournal(Path(folder),'scope',messages,policy,None)
            reply=json.dumps({'request_id':'a'*32,'items':[{'id':1,'text':'Xin chào'}]})
            journal.save('received','a'*32,'https://chat.deepseek.com/a/chat/s/123',reply)
            self.assertEqual(browser._complete_unlocked(messages,30,Event(),conversation='scope',policy=policy),reply)
            connect.assert_not_called()

    def test_timeout_preserves_typed_error_for_existing_checkpoint_recovery(self):
        browser=DeepSeekBrowser()
        with patch.object(GeminiBrowser,'_complete_unlocked',side_effect=GeminiWebTimeout('Gemini quá hạn')):
            with self.assertRaisesRegex(GeminiWebTimeout,'DeepSeek quá hạn'):
                browser._complete_unlocked([],30,Event())

    async def test_settings_roundtrip_and_browser_route_select_unsaved_provider(self):
        from app.api.ai_settings import AiConfig, save_config, public_config, open_browser, browser_status
        fake=SimpleNamespace(open=AsyncMock(return_value={'ready':True}),status=AsyncMock(return_value={'ready':True}))
        with tempfile.TemporaryDirectory() as folder, patch('app.api.ai_settings.AI_CONFIG_FILE',Path(folder)/'.env'), patch('app.api.ai_settings.job_store.list',return_value=[]), patch.object(settings,'translation_engine','gemini'), patch.object(settings,'gemini_web_background',False):
            config=AiConfig(engine='deepseek_web',api_url='http://127.0.0.1:1/v1',model='unused')
            await save_config(config)
            self.assertEqual(public_config()['engine'],'deepseek_web')
            self.assertIn('deepseek_web',(Path(folder)/'.env').read_text())
            with patch('app.services.ai.web_provider.get_browser',return_value=fake) as choose:
                await open_browser('deepseek_web');await browser_status('deepseek_web')
            self.assertEqual([c.args[0] for c in choose.call_args_list],['deepseek_web','deepseek_web'])

    async def test_connection_test_is_blocked_while_translation_runs(self):
        from fastapi import HTTPException
        from app.api.ai_settings import AiConfig, test_config
        with patch('app.api.ai_settings.job_store.list',return_value=[SimpleNamespace(status='processing')]):
            with self.assertRaises(HTTPException) as error:
                await test_config(AiConfig(engine='deepseek_web',api_url='http://127.0.0.1:1/v1',model='unused'))
            self.assertEqual(error.exception.status_code,409)
