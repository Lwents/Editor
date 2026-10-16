import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core import settings
from app.models.job import DubbingRequest, JobStatus
from app.services.persistence import atomic_json, read_json, PersistenceError
from app.services.store import JobStore
from app.services.ai.cue_recovery import CueRecovery
from app.services.ai.translation import (
    GeminiTranslation, TranslationError, TranslationBatchStructureError,
    _translate_gemini_batch_with_retries, _load_translation_checkpoint,
)
from app.services.ai.gemini_web import GeminiWebVerificationRequired, GeminiWebTimeout, GeminiWebRefusal, GeminiWebModelError
from app.services.presets import get_processing_profile
from app.services.subtitles.timing import SubtitleEvent


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)

    def test_interrupted_replace_keeps_primary_and_valid_backup(self):
        file=self.root/'state.json'
        atomic_json(file,{'version':1})
        original=os.replace
        def denied(source,destination):
            if Path(destination)==file:
                raise PermissionError('simulated antivirus file lock')
            return original(source,destination)
        with patch('app.services.persistence.os.replace',side_effect=denied):
            with self.assertRaises(PersistenceError):
                atomic_json(file,{'version':2})
        self.assertEqual(read_json(file),{'version':1})
        self.assertEqual(read_json(file.with_suffix('.json.bak')),{'version':1})

    def test_corrupt_primary_uses_backup_and_never_silently_empties(self):
        file=self.root/'state.json'
        atomic_json(file,{'saved':12});atomic_json(file,{'saved':24})
        file.write_text('{broken',encoding='utf-8')
        self.assertEqual(read_json(file),{'saved':12})
        file.with_suffix('.json.bak').write_text('broken',encoding='utf-8')
        with self.assertRaises(PersistenceError):read_json(file)
        self.assertEqual(file.read_text(),'{broken')

    def test_hard_exit_after_fsync_before_rename_preserves_old_state(self):
        file=self.root/'state.json';atomic_json(file,{'saved':12})
        script='''import os,sys\nfrom pathlib import Path\nfrom app.services.persistence import atomic_json\np=Path(sys.argv[1]);original=os.replace\ndef replace(a,b):\n if Path(b)==p: os._exit(23)\n return original(a,b)\nos.replace=replace\natomic_json(p,{"saved":24})\n'''
        process=subprocess.run([sys.executable,'-c',script,str(file)],cwd=Path(__file__).resolve().parents[1],capture_output=True)
        self.assertEqual(process.returncode,23,process.stderr.decode(errors='replace'))
        self.assertEqual(read_json(file),{'saved':12})
        atomic_json(file,{'saved':24})
        self.assertEqual(read_json(file),{'saved':24})

    def test_job_database_survives_broken_json_snapshot(self):
        with patch.object(settings,'storage_dir',str(self.root)):
            store=JobStore();job=store.create(DubbingRequest(local_file_path='source.mp4'))
            store.update(job.job_id,status=JobStatus.completed,progress=100)
            (self.root/'jobs_index.json').write_text('broken',encoding='utf-8')
            restored=JobStore()
            self.assertEqual(restored.get(job.job_id).status,JobStatus.completed)

    def test_corrupt_database_rebuilds_from_snapshot_and_keeps_corrupt_file(self):
        with patch.object(settings,'storage_dir',str(self.root)):
            store=JobStore();job=store.create(DubbingRequest(local_file_path='source.mp4'))
            database=self.root/'jobs_index.sqlite3'
            database.write_bytes(b'not a sqlite database')
            restored=JobStore()
            self.assertIsNotNone(restored.get(job.job_id))
            self.assertTrue(list(self.root.glob('*.corrupt-*')))

    def test_index_without_database_imports_only_once(self):
        with patch.object(settings,'storage_dir',str(self.root)):
            first=JobStore();job=first.create(DubbingRequest(local_file_path='source.mp4'))
            with first._connect() as database:database.execute('DELETE FROM jobs')
            second=JobStore();third=JobStore()
            self.assertEqual([j.job_id for j in third.list()],[job.job_id])
            self.assertEqual(len(second.list()),1)

    def test_failed_database_write_does_not_publish_uncommitted_progress(self):
        with patch.object(settings,'storage_dir',str(self.root)):
            store=JobStore();job=store.create(DubbingRequest(local_file_path='source.mp4'))
            with patch.object(store,'_connect',side_effect=sqlite3.OperationalError('disk full')):
                with self.assertRaises(sqlite3.OperationalError):store.update(job.job_id,progress=90)
            self.assertEqual(store.get(job.job_id).progress,0)
            store.emergency_failure(job.job_id,'disk full')
            self.assertEqual(store.get(job.job_id).status,JobStatus.failed)

    def test_restart_marks_only_interrupted_jobs_for_resume(self):
        with patch.object(settings,'storage_dir',str(self.root)):
            store=JobStore();active=store.create(DubbingRequest(local_file_path='source.mp4'))
            stopped=store.create(DubbingRequest(local_file_path='source.mp4'))
            store.update(active.job_id,status=JobStatus.processing)
            store.update(stopped.job_id,status=JobStatus.failed,error='USER_CANCELLED')
            restored=JobStore();restored._mark_interrupted_jobs()
            self.assertEqual(restored.get(active.job_id).status,JobStatus.queued)
            self.assertEqual(restored.get(stopped.job_id).error,'USER_CANCELLED')

    def test_incompatible_checkpoint_requires_explicit_new_translation(self):
        file=self.root/'checkpoint.json'
        atomic_json(file,{'fingerprint':'old','translated_texts':['Xong'],'memory':{}})
        with self.assertRaisesRegex(TranslationError,'CHECKPOINT_INCOMPATIBLE'):
            _load_translation_checkpoint(file,'new',{},[(0,1)])
        self.assertEqual(read_json(file)['fingerprint'],'old')


class CueRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.path=Path(self.temporary.name)/'cues.sqlite3'
        self.batch=[{'id':1,'source':'one'},{'id':2,'source':'two'}]

    async def test_valid_partial_survives_new_worker_and_only_missing_cue_is_sent(self):
        store=CueRecovery(self.path,'same-input')
        with patch('app.services.ai.translation._translate_gemini_batch',new=AsyncMock(side_effect=[
                TranslationBatchStructureError('missing',{1:'Một'}),GeminiWebVerificationRequired('login')])):
            with self.assertRaises(GeminiWebVerificationRequired):
                await _translate_gemini_batch_with_retries(None,self.batch,'en','vi',{}, {},[],[],recovery_store=store)
        restarted=CueRecovery(self.path,'same-input')
        with patch('app.services.ai.translation._translate_gemini_batch',new=AsyncMock(return_value=(['Hai'],{}))) as remote:
            result=await _translate_gemini_batch_with_retries(None,self.batch,'en','vi',{}, {},[],[],recovery_store=restarted)
        self.assertEqual(result[0],['Một','Hai'])
        self.assertEqual([item['id'] for item in remote.call_args.args[1]],[2])

    async def test_disk_full_stops_new_remote_requests(self):
        store=CueRecovery(self.path,'input')
        with patch.object(store,'save',side_effect=PersistenceError('disk full')),patch(
                'app.services.ai.translation._translate_gemini_batch',new=AsyncMock(return_value=(['Một','Hai'],{}))) as remote:
            with self.assertRaises(PersistenceError):
                await _translate_gemini_batch_with_retries(None,self.batch,'en','vi',{}, {},[],[],recovery_store=store)
        self.assertEqual(remote.await_count,1)

    async def test_timeout_reduces_request_size_with_bounded_recovery(self):
        with patch('app.services.ai.translation._translate_gemini_batch',new=AsyncMock(side_effect=[
                GeminiWebTimeout('slow'),(['Một'],{}),(['Hai'],{})])) as remote:
            result=await _translate_gemini_batch_with_retries(None,self.batch,'en','vi',{}, {},[],[])
        self.assertEqual(result[0],['Một','Hai'])
        self.assertEqual([len(call.args[1]) for call in remote.call_args_list],[2,1,1])

    def test_source_or_namespace_change_cannot_reuse_other_translation(self):
        store=CueRecovery(self.path,'input');store.save(self.batch,{1:'Một'}, {})
        self.assertEqual(CueRecovery(self.path,'new-model').load(self.batch)[0],{})
        self.assertEqual(store.load([{'id':1,'source':'a different sentence'}])[0],{})

    def test_uncommitted_sqlite_transaction_is_not_promoted_after_process_exit(self):
        store=CueRecovery(self.path,'input');store.save(self.batch,{1:'Một'}, {})
        script='''import sqlite3,os,sys\nc=sqlite3.connect(sys.argv[1]);c.execute("DELETE FROM cues");os._exit(27)'''
        process=subprocess.run([sys.executable,'-c',script,str(self.path)],capture_output=True)
        self.assertEqual(process.returncode,27)
        self.assertEqual(CueRecovery(self.path,'input').load(self.batch)[0],{1:'Một'})


class QuarantineTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_middle_batch_does_not_discard_later_good_batches(self):
        await self._check_middle_failure(GeminiWebRefusal)

    async def test_timed_out_middle_batch_keeps_later_batches_and_retries_only_missing(self):
        await self._check_middle_failure(GeminiWebTimeout)

    async def _check_middle_failure(self, failure):
        with tempfile.TemporaryDirectory() as temporary:
            file=Path(temporary)/'checkpoint.json'
            events=[SubtitleEvent(i,i+1,f'source {i}') for i in range(5)]
            profile=replace(get_processing_profile('balanced'),translation_batch_items=2)
            async def first(client,batch,*args,**kwargs):
                if batch[0]['id']==3:raise failure('injected provider failure')
                return [f'Câu {item["id"]}' for item in batch],{}
            with patch.object(settings,'translation_engine','gemini'),patch.object(settings,'ninerouter_api_key','test'), \
                    patch('app.services.ai.translation.get_translation_profile',return_value=profile), \
                    patch('app.services.ai.translation.create_translation_client',return_value=None), \
                    patch('app.services.ai.translation._translate_gemini_batch',side_effect=first):
                with self.assertRaisesRegex(TranslationError,'TRANSLATION_NEEDS_REVIEW'):
                    await GeminiTranslation().translate_events(events,'en','vi',checkpoint_file=file)
                self.assertFalse(read_json(file)['completed'])
                self.assertEqual([item['id'] for item in read_json(file.with_name('translation_unresolved.json'))['items']],[3,4])
                with patch('app.services.ai.translation._translate_gemini_batch',new=AsyncMock(return_value=(['Câu 3','Câu 4'],{}))) as remote:
                    result=await GeminiTranslation().translate_events(events,'en','vi',checkpoint_file=file)
                self.assertEqual([e.text for e in result],[f'Câu {i}' for i in range(1,6)])
                self.assertEqual(remote.await_count,1)
                self.assertEqual([item['id'] for item in remote.call_args.args[1]],[3,4])
                self.assertTrue(read_json(file)['completed'])

    async def test_missing_model_stops_immediately_without_quarantining_the_film(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(settings,'translation_engine','gemini'),patch.object(settings,'ninerouter_api_key','test'), \
                    patch('app.services.ai.translation.create_translation_client',return_value=None), \
                    patch('app.services.ai.translation._translate_gemini_batch',new=AsyncMock(side_effect=GeminiWebModelError('unavailable'))) as remote:
                with self.assertRaises(GeminiWebModelError):
                    await GeminiTranslation().translate_events([SubtitleEvent(0,1,'source')],'en','vi',checkpoint_file=Path(temporary)/'checkpoint.json')
                self.assertEqual(remote.await_count,1)


class BackupAndScanTests(unittest.TestCase):
    def test_scan_resume_overlaps_two_seconds_and_discards_no_completed_prefix(self):
        from app.services.subtitles.scan_checkpoint import ScanCheckpoint
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'scan.json';cache=ScanCheckpoint(path,'source-and-engines',3,30)
            samples=[[i,'字',.99] for i in range(0,100,3)]
            cache.save(100,[[10,20]],[i/30 for i in range(100)],samples,{i:('字',.99) for i in range(0,100,3)},set(range(0,100,3)),set())
            saved=cache.load()
            self.assertEqual(saved['start'],39)
            self.assertEqual(len(saved['timestamps']),39)
            self.assertTrue(all(row[0]<39 for row in saved['samples']))
            self.assertIsNone(ScanCheckpoint(path,'another-video',3,30).load())

    def test_old_media_is_not_deleted_and_referenced_temporary_is_kept(self):
        from app.services.maintenance import cleanup_upload_temporaries
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);uploads=root/'uploads/videos';uploads.mkdir(parents=True)
            for name in ('movie.mp4','keep.tmp','orphan.tmp'):
                file=uploads/name;file.write_bytes(b'video');os.utime(file,(1,1))
            job=SimpleNamespace(request=SimpleNamespace(local_file_path=str(uploads/'keep.tmp')))
            self.assertEqual(cleanup_upload_temporaries(root,[job],now=1000000),['orphan.tmp'])
            self.assertTrue((uploads/'movie.mp4').exists());self.assertTrue((uploads/'keep.tmp').exists())

    def test_backup_restores_source_and_committed_sqlite_wal(self):
        from app.services.job_backup import create_backup,restore_backup
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);work=root/'original';work.mkdir()
            source=work/'source.mp4';source.write_bytes(b'test-video')
            ledger=CueRecovery(work/'translation.parts.sqlite3','namespace')
            ledger.save([{'id':1,'source':'one'}],{1:'Một'}, {})
            atomic_json(work/'translation_checkpoint.json',{'fingerprint':'x','translated_texts':['Một'],'memory':{}})
            job=SimpleNamespace(request=DubbingRequest(local_file_path=str(source)))
            archive=root/'backup.zip';create_backup(job,work,archive)
            with patch.object(settings,'storage_dir',str(root/'restored')):
                store=JobStore();created=restore_backup(archive,store,root/'restored')
                result=Path(created.request.local_file_path)
                self.assertEqual(result.read_bytes(),b'test-video')
                recovered=CueRecovery(result.parent/'translation.parts.sqlite3','namespace')
                self.assertEqual(recovered.load([{'id':1,'source':'one'}])[0],{1:'Một'})

    def test_path_traversal_backup_is_rejected_before_any_job_is_created(self):
        from app.services.job_backup import restore_backup
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);archive=root/'bad.zip'
            with zipfile.ZipFile(archive,'w') as output:output.writestr('../outside.json','{}')
            with patch.object(settings,'storage_dir',str(root/'storage')):
                store=JobStore()
                with self.assertRaisesRegex(RuntimeError,'BACKUP_UNSAFE'):restore_backup(archive,store,root/'storage')
                self.assertEqual(store.list(),[])
                self.assertFalse((root/'outside.json').exists())

    def test_audit_flags_repetition_and_never_calls_it_accuracy(self):
        from app.services.subtitles.translation_audit import audit_translation
        source=[SubtitleEvent(i,i+1,f'原句{i}') for i in range(3)]
        target=[SubtitleEvent(i,i+1,'Một câu rất dài bị lặp lại không đổi.') for i in range(3)]
        result=audit_translation(source,target)
        self.assertIsNone(result['accuracy_percent'])
        self.assertEqual(len([w for w in result['warnings'] if w['code']=='REPEATED_TRANSLATION']),3)

    def test_changed_backup_bytes_are_rejected_without_creating_a_job(self):
        from app.services.job_backup import create_backup,restore_backup
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);work=root/'original';work.mkdir()
            (work/'source.mp4').write_bytes(b'video')
            original=root/'valid.zip'
            create_backup(SimpleNamespace(request=DubbingRequest(local_file_path='source.mp4')),work,original)
            corrupt=root/'corrupt.zip'
            with zipfile.ZipFile(original) as incoming,zipfile.ZipFile(corrupt,'w') as outgoing:
                for name in incoming.namelist():
                    outgoing.writestr(name,b'other' if name=='source.mp4' else incoming.read(name))
            with patch.object(settings,'storage_dir',str(root/'storage')):
                store=JobStore()
                with self.assertRaisesRegex(RuntimeError,'BACKUP_HASH_MISMATCH'):restore_backup(corrupt,store,root/'storage')
                self.assertEqual(store.list(),[])


class QueueAndJournalTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_heavy_jobs_are_serialized_and_cancelling_waiter_releases_it(self):
        from app.services.task_manager import job_slot
        running=0;maximum=0;entered=asyncio.Event();release=asyncio.Event()
        async def work(wait=False):
            nonlocal running,maximum
            async with job_slot():
                running+=1;maximum=max(maximum,running);entered.set()
                if wait:await release.wait()
                running-=1
        first=asyncio.create_task(work(True));await entered.wait()
        second=asyncio.create_task(work());await asyncio.sleep(0)
        second.cancel()
        await asyncio.gather(second,return_exceptions=True)
        release.set();await first
        await work()
        self.assertEqual(maximum,1)

    async def test_startup_resumes_queued_jobs_once_and_does_not_resume_user_cancelled(self):
        from app.services import task_manager
        from app.services.store import job_store
        original=job_store
        with tempfile.TemporaryDirectory() as temporary,patch.object(settings,'storage_dir',temporary):
            isolated=JobStore();active=isolated.create(DubbingRequest(local_file_path='source.mp4'))
            isolated.update(active.job_id,status=JobStatus.processing)
            cancelled=isolated.create(DubbingRequest(local_file_path='source.mp4'))
            isolated.update(cancelled.job_id,status=JobStatus.failed,error='USER_CANCELLED')
            hold=asyncio.Event()
            async def worker(job_id):await hold.wait()
            with patch('app.services.store.job_store',isolated),patch('app.services.processor.process_job',side_effect=worker) as process:
                await task_manager.resume_pending();await task_manager.resume_pending()
                await asyncio.sleep(0)
                self.assertEqual(len([t for key,t in task_manager._tasks.items() if key==active.job_id]),1)
                self.assertNotIn(cancelled.job_id,task_manager._tasks)
                hold.set();await task_manager._tasks[active.job_id]
                task_manager.unregister_task(active.job_id)

    def test_received_request_is_replayed_after_restart_without_connecting_browser(self):
        from app.services.ai.request_journal import RequestJournal
        from app.services.ai.gemini_models import model_policy
        from app.services.ai.gemini_web import GeminiBrowser,profile_directory
        from threading import Event
        policy=model_policy('balanced');messages=[{'content':'same input'}]
        journal=RequestJournal(profile_directory(),'test-scope',messages,policy)
        response=json.dumps({'request_id':'a'*32,'items':[{'id':1,'text':'Đã xong'}]})
        journal.save('received','a'*32,'https://gemini.google.com/app/saved',response)
        restarted=GeminiBrowser()
        with patch.object(restarted,'_connect',side_effect=AssertionError('must not reconnect')):
            result=restarted._complete_unlocked(messages,30,Event(),conversation='test-scope',policy=policy)
        self.assertEqual(json.loads(result)['items'][0]['text'],'Đã xong')

    def test_pending_request_reads_late_reply_before_sending_again(self):
        from app.services.ai.request_journal import RequestJournal
        from app.services.ai.gemini_models import model_policy
        from app.services.ai.gemini_web import GeminiBrowser,profile_directory
        from tests.test_gemini_web_replies import FakePage
        from threading import Event
        messages=[{'content':'pending input'}];policy=model_policy('balanced');nonce='b'*32
        journal=RequestJournal(profile_directory(),'pending-scope',messages,policy)
        journal.save('sent',nonce,'https://gemini.google.com/app')
        page=FakePage();page.response.inner_text=lambda:json.dumps({'request_id':nonce,'items':[{'id':1,'text':'Đến muộn'}]})
        browser=GeminiBrowser()
        with patch.object(browser,'_connect',return_value=page),patch.object(browser,'_set_visible'), \
                patch.object(browser,'_wait_ready',return_value=True),patch.object(browser,'_conversation_urls',return_value={}), \
                patch('app.services.ai.gemini_web.select_model') as choose,patch.object(page.editor,'press') as send:
            result=browser._complete_unlocked(messages,30,Event(),conversation='pending-scope',policy=policy)
        self.assertEqual(json.loads(result)['items'][0]['text'],'Đến muộn')
        choose.assert_not_called();send.assert_not_called()

    def test_other_input_cannot_replay_a_cached_response(self):
        from app.services.ai.request_journal import RequestJournal
        from app.services.ai.gemini_models import model_policy
        from app.services.ai.gemini_web import GeminiBrowser,profile_directory,GeminiWebError
        from threading import Event
        policy=model_policy('balanced')
        journal=RequestJournal(profile_directory(),'different-scope',[{'content':'old'}],policy)
        journal.save('received','a'*32,'https://gemini.google.com/app/saved',json.dumps({'request_id':'a'*32,'items':[{'id':1,'text':'Cũ'}]}))
        browser=GeminiBrowser()
        with patch.object(browser,'_connect',side_effect=GeminiWebError('must connect for new input')):
            with self.assertRaisesRegex(GeminiWebError,'must connect'):
                browser._complete_unlocked([{'content':'new'}],30,Event(),conversation='different-scope',policy=policy)


class ScanCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_waits_for_native_worker_before_releasing_scan(self):
        from threading import Event
        from app.services.subtitles.visible import _scan_with_progress
        entered, exited = Event(), Event()

        def scanner(source, output, progress, cancelled, resume_key):
            entered.set()
            if not cancelled.wait(5):
                raise AssertionError('Scanner did not receive cancellation')
            exited.set()
            raise RuntimeError('Visible subtitle scan cancelled')

        with patch('app.services.subtitles.visible._extract_visible', side_effect=scanner):
            task = asyncio.create_task(_scan_with_progress(Path('source.mp4'), Path('output.srt'), None))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(exited.is_set(), 'The previous scanner must finish before retry can start')
