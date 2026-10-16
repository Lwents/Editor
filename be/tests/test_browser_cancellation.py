"""Cancellation must not release or strand a live browser worker's gate."""
import asyncio
import unittest
from threading import Event
from app.services.ai.gemini_web import GeminiBrowser, GeminiWebError

class BrowserCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_caller_cannot_queue_cleanup_behind_live_worker(self):
        browser=GeminiBrowser();started=Event();release=Event()
        def blocked():
            started.set();release.wait(3);raise GeminiWebError('late cancellation acknowledged')
        task=asyncio.create_task(browser._run(blocked))
        try:
            for _ in range(100):
                if started.is_set():break
                await asyncio.sleep(.01)
            self.assertTrue(started.is_set())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertTrue(browser._gate.locked())
            with self.assertRaises(GeminiWebError):await browser._run(lambda:'unsafe concurrent cleanup')
            release.set()
            for _ in range(100):
                if not browser._gate.locked():break
                await asyncio.sleep(.01)
            self.assertFalse(browser._gate.locked())
            self.assertEqual(await browser._run(lambda:'ready'),'ready')
        finally:
            release.set();browser._executor.shutdown(wait=True)

    async def test_complete_cancellation_signals_worker_and_eventually_unlocks_browser(self):
        browser=GeminiBrowser();started=Event();stopped=Event()
        def request(messages,timeout,cancelled,*args):
            started.set();cancelled.wait(3);stopped.set()
            raise GeminiWebError('cancelled')
        browser._complete=request
        task=asyncio.create_task(browser.complete([],timeout=30))
        try:
            for _ in range(100):
                if started.is_set():break
                await asyncio.sleep(.01)
            self.assertTrue(started.is_set());task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            for _ in range(100):
                if stopped.is_set() and not browser._gate.locked():break
                await asyncio.sleep(.01)
            self.assertTrue(stopped.is_set());self.assertFalse(browser._gate.locked())
            self.assertEqual(await browser._run(lambda:'session available'),'session available')
        finally:browser._executor.shutdown(wait=True)
