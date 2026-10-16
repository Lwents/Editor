"""Run a 1,599-cue review through durable chat rollover and interrupted resume.

The provider is deterministic: this checks transport/checkpoint correctness, not
translation quality or availability of the live DeepSeek service.
"""
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from app.core import settings
from app.services.ai.deepseek_web import DeepSeekBrowser
from app.services.ai.gemini_web import GeminiBrowser, GeminiWebVerificationRequired
from app.services.ai.translation_review import review_translated_events
from app.services.subtitles.timing import SubtitleEvent


class LongReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_1599_cues_rotate_with_long_thinking_and_resume_without_resending_prefix(self):
        source=[SubtitleEvent(i*2,i*2+1.8,f'这是第{i+1}句。') for i in range(1599)]
        draft=[SubtitleEvent(e.start,e.end,f'Đây là câu {i+1}.') for i,e in enumerate(source)]
        browser=DeepSeekBrowser();page=SimpleNamespace(url='https://chat.deepseek.com/a/chat/s/initial')
        browser._page=page
        sent=[];rotations=[];visible=[0];interrupted=[False]
        page.evaluate=lambda script,*args: 60000 if args else visible[0]
        def rotate(page,scope,cancelled):
            rotations.append(scope);visible[0]=0
            page.url='https://chat.deepseek.com/a/chat/s/chat'+str(len(rotations))
        def provider(messages,timeout,cancelled,background=None,attachments=None,conversation=None,on_wait=None,policy=None):
            payload=json.loads(messages[-1]['content'])
            items=payload['current_items']
            if len(sent)>=25 and not interrupted[0]:
                interrupted[0]=True
                raise GeminiWebVerificationRequired('simulated interruption; preserve checkpoint')
            prompt='\n\n'.join(message['content'] for message in messages)
            browser._before_send(page,prompt,conversation,cancelled,policy)
            browser._on_sent(page,prompt,conversation)
            nonce=f'{len(sent)+1:032x}'
            browser._on_reply(page,nonce,conversation)
            visible[0]+=len(prompt)+60000
            sent.append([item['id'] for item in items])
            return json.dumps({'items':[{'id':item['id'],'text':item['draft']} for item in items],'memory_updates':{}})
        class Client:
            def __init__(self):self.chat=SimpleNamespace(completions=self)
            async def create(self,*,messages,timeout,**kwargs):
                result=browser._complete_unlocked(messages,timeout,Event(),conversation='review-scope',policy=browser._policy('balanced'))
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=result))])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with patch.object(settings,'translation_engine','deepseek_web'),patch.object(browser,'profile_directory',return_value=root/'profile'),patch.object(browser,'_rotate_chat',side_effect=rotate),patch.object(browser,'_select_model'),patch.object(GeminiBrowser,'_complete_unlocked',side_effect=provider),patch('app.services.ai.translation_review.create_translation_client',return_value=Client()):
                with self.assertRaises(GeminiWebVerificationRequired):
                    await review_translated_events(source,draft,{},root,'zh','vi','balanced')
                saved=json.loads((root/'translation_review_checkpoint.json').read_text())
                prefix=len(saved['texts'])
                self.assertGreater(prefix,0)
                result=await review_translated_events(source,draft,{},root,'zh','vi','balanced')
                checkpoint=json.loads((root/'translation_review_checkpoint.json').read_text())
        self.assertEqual([i for group in sent for i in group],list(range(1,1600)))
        self.assertGreater(len(rotations),20)
        self.assertTrue(checkpoint['completed'])
        self.assertEqual(len(checkpoint['texts']),1599)
        self.assertEqual([(e.start,e.end,e.text) for e in result],[(e.start,e.end,e.text) for e in draft])
