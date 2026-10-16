"""Handoff provenance, conflicting roles, isolation, restart and bounded context."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.core import settings
from app.services.ai.translation import (GeminiTranslation, _initial_translation_memory,
    _merge_translation_memory, _translate_gemini_batch_with_retries)
from app.services.ai.translation_memory import (MemoryArchive, memory_session, build_handoff,
    source_notes, active_memory, encoded)
from app.services.presets import get_processing_profile
from app.services.subtitles.timing import SubtitleEvent


def cue(i, source):
    return {'id':i,'source':source,'time':[i,i+1]}


def note(i, quote, subject='萧妃', target='萧衡', value='mother_of', kind='relationship'):
    return {'kind':kind,'subject':subject,'target':target,'value':value,'uncertain':False,
            'evidence':[{'id':i,'quote':quote}]}


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'translation_memory.sqlite3'
        self.archive=MemoryArchive(self.path,'film-model-mode-A')
        self.registry={'萧妃':'Tiêu Phi','萧衡':'Tiêu Hành','赵妃':'Triệu Phi','赵宁':'Triệu Ninh'}
        self.quote='萧妃是萧衡的生母'

    def packet(self, archive=None, before=100, source='萧妃对萧衡说', previous=(), mode='balanced'):
        with memory_session(archive or self.archive,mode):
            return build_handoff({}, {}, [cue(before,source)], list(previous), [], self.registry)['handoff']

    def test_citations_require_exact_source_and_valid_ids(self):
        batch=[cue(1,self.quote),cue(2,'赵妃不是赵宁的母亲')]
        updates={'source_notes':[note(1,self.quote),note(1,'并非原文'),note(99,self.quote),
                                 note(2,'赵妃不是赵宁的母亲','赵妃','赵宁')]}
        accepted=source_notes(updates,batch,{1})
        self.assertEqual(len(accepted),1)
        self.assertFalse(accepted[0]['uncertain'])
        self.assertEqual(accepted[0]['status'],'source_linked_interpretation')

    def test_named_addressee_does_not_establish_speaker_identity(self):
        accepted=source_notes({'source_notes':[note(1,'母妃，孩儿知道了')]},[cue(1,'母妃，孩儿知道了')],{1})
        self.assertTrue(accepted[0]['uncertain'])
        packet=self.packet()
        self.assertIn('a_called_name_is_not_the_speaker',packet['speaker_policy'])

    def test_old_relationship_is_available_after_restart_without_chat_history(self):
        self.archive.record([cue(1,self.quote)],{1:'Tiêu Phi là mẹ của Tiêu Hành'}, {'source_notes':[note(1,self.quote)]})
        self.archive.record([cue(i,'沉默') for i in range(2,100)],{i:'Im lặng' for i in range(2,100)})
        first=self.packet()
        restored=self.packet(MemoryArchive(self.path,'film-model-mode-A'))
        self.assertEqual(first['digest'],restored['digest'])
        self.assertEqual(restored['source_linked_notes'][0]['evidence'][0]['id'],1)
        self.assertTrue(any(row['id']==1 for row in restored['recalled_cues']))

    def test_other_film_or_model_namespace_cannot_leak_history(self):
        self.archive.record([cue(1,self.quote)],{1:'Mẹ con'}, {'source_notes':[note(1,self.quote)]})
        packet=self.packet(MemoryArchive(self.path,'film-model-mode-B'))
        self.assertFalse(packet['source_linked_notes']);self.assertFalse(packet['recalled_cues'])

    def test_later_completed_groups_do_not_leak_future_facts(self):
        self.archive.record([cue(200,self.quote)],{200:'Mẹ con'}, {'source_notes':[note(200,self.quote)]})
        packet=self.packet(before=100)
        self.assertFalse(packet['recent_cues']);self.assertFalse(packet['source_linked_notes'])

    def test_conflicting_roles_are_preserved_instead_of_overwritten(self):
        self.archive.record([cue(1,self.quote)],{1:'Mẹ'}, {'source_notes':[note(1,self.quote)]})
        quote='萧妃不是萧衡的妻子'
        self.archive.record([cue(2,quote)],{2:'Không phải vợ'}, {'source_notes':[note(2,quote,value='wife_of')]})
        packet=self.packet()
        self.assertEqual(len(packet['conflicts']),1)
        self.assertEqual({row['value'] for row in packet['conflicts'][0]['alternatives']},{'mother_of','wife_of'})
        self.assertFalse(packet['source_linked_notes'])

    def test_same_title_does_not_transfer_a_relationship_to_another_pair(self):
        self.archive.record([cue(1,self.quote)],{1:'Mẹ con'}, {'source_notes':[note(1,self.quote)]})
        other='赵妃与赵宁并无亲属关系'
        self.archive.record([cue(2,other)],{2:'Không có quan hệ họ hàng'}, {'source_notes':[note(2,other,'赵妃','赵宁','not_family')]})
        packet=self.packet(source='赵宁向赵妃问好')
        self.assertEqual([(n['subject'],n['target']) for n in packet['source_linked_notes']],[('赵妃','赵宁')])

    def test_old_local_addressing_is_not_a_global_rule_in_a_later_scene(self):
        self.archive.record([cue(1,self.quote)],{1:'Mẹ con'}, {'source_notes':[note(1,self.quote,kind='addressing',value='mẫu phi/con')]})
        self.assertFalse(self.packet(before=100)['source_linked_notes'])
        self.assertTrue(self.archive.notes(before=10,terms=['萧妃']))

    def test_review_inherits_translation_evidence_and_prefers_reviewed_nearby_text(self):
        self.archive.record([cue(1,self.quote)],{1:'Bản nháp'}, {'source_notes':[note(1,self.quote)]})
        review=MemoryArchive(self.path,'film-model-mode-A',phase='review')
        review.record([cue(1,self.quote)],{1:'Bản đã rà soát'})
        packet=self.packet(review,before=5)
        self.assertEqual(packet['recent_cues'][0]['translated'],'Bản đã rà soát')
        self.assertTrue(packet['source_linked_notes'])

    def test_partial_cues_and_context_gaps_remain_explicit(self):
        self.archive.record([cue(1,self.quote),cue(2,'坏字'),cue(3,'再见')],{1:'Mẹ con',3:'Tạm biệt'})
        packet=self.packet(before=5)
        self.assertEqual([r['id'] for r in packet['recent_cues']],[1,3])
        self.assertEqual(packet['untranslated_gaps'],[{'from':2,'to':2},{'from':4,'to':4}])

    def test_large_conflicting_history_is_bounded_without_treating_one_side_as_truth(self):
        for i in range(1,50):
            self.archive.record([cue(i,self.quote)],{i:'Bản dịch '*30},
                {'source_notes':[note(i,self.quote,value='Giả thuyết '+str(i)+' '+('x'*450))]})
        for mode,maximum in [('fast',7000),('balanced',11000),('quality',15000)]:
            packet=self.packet(before=60,mode=mode)
            self.assertLessEqual(len(encoded(packet)),maximum)
            self.assertEqual(packet['selection']['conflicts_detected'],1)
            self.assertFalse(packet['source_linked_notes'])
            self.assertTrue(packet['conflicts'] or any(n['kind']=='conflict' for n in packet['open_questions']))
        with self.archive.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM context_notes').fetchone()[0],49)

    def test_glossary_conflicts_do_not_silently_replace_a_previous_choice(self):
        memory=_initial_translation_memory({})
        _merge_translation_memory(memory,{'glossary':{'母妃':'mẫu phi'}})
        _merge_translation_memory(memory,{'glossary':{'母妃':'vương phi'}})
        self.assertEqual(memory['glossary']['母妃'],'mẫu phi')
        self.assertEqual(len(memory['glossary_conflicts']),1)
        self.assertIsNone(active_memory.get())

    def test_legacy_roles_are_retained_as_unverified_notes_and_cannot_become_source_facts(self):
        memory={'characters':[{'name':'Tiêu Ngọc Nương','role':'mother'}],
                'relationships':['Một phỏng đoán về quan hệ'], 'addressing_rules':['mẫu phi/con']}
        with memory_session(self.archive):
            packet=build_handoff(memory,{},[cue(3,'母妃')],[],[],{})
        self.assertTrue(packet['legacy_notes'])
        self.assertTrue(all(row['status']=='unverified_model_note_not_a_fact' for row in packet['legacy_notes']))
        self.assertFalse(packet['handoff']['source_linked_notes'])
        self.assertNotIn('role',packet['characters'][0])

    def test_short_names_inside_other_names_are_not_confused_with_independent_mentions(self):
        from app.services.ai.translation_memory import matched_entities
        self.assertEqual(matched_entities({'李明','李明远'},'李明远说话'),['李明远'])
        self.assertEqual(matched_entities({'李明','李明远'},'李明远望着李明'),['李明远','李明'])
        self.assertEqual(matched_entities({'Ann','Anna'},'Anna speaks'),['Anna'])


class HandoffPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_fresh_chat_receives_past_source_evidence_without_extra_ai_calls(self):
        source=['萧妃是萧衡的生母','萧衡称萧妃为母妃']+['大家静静地等候']*8+['萧妃对萧衡说','你也得给我好好活下去']
        events=[SubtitleEvent(i,i+1,text) for i,text in enumerate(source)]
        calls=[]
        async def create(**kwargs):
            payload=json.loads(kwargs['messages'][1]['content']);calls.append(payload)
            items=payload['current_items']
            if items[0]['id']>2:
                notes=payload['memory']['handoff']['source_linked_notes']
                self.assertTrue(any(n['value']=='mother_of' for n in notes))
            updates={'source_notes':[note(1,source[0])]} if items[0]['id']==1 else {}
            result=[{'id':item['id'],'text':'Con cũng phải sống cho thật tốt' if item['id']==12 else 'Đã rõ'} for item in items]
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'items':result,'memory_updates':updates},ensure_ascii=False)))])
        fake=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        profile=replace(get_processing_profile('fast'),translation_batch_items=2)
        with tempfile.TemporaryDirectory() as folder,patch.object(settings,'translation_engine','gemini'),patch.object(settings,'ninerouter_api_key','test'),patch('app.services.ai.translation.create_translation_client',return_value=fake),patch('app.services.ai.translation.get_translation_profile',return_value=profile):
            result=await GeminiTranslation().translate_events(events,'zh','vi',context={'characters':[{'name':'Tiêu Phi','aliases':['萧妃']},{'name':'Tiêu Hành','aliases':['萧衡']}]},processing_mode='fast',checkpoint_file=Path(folder)/'translation.json')
            snapshot=json.loads((Path(folder)/'translation_handoff.json').read_text())
            self.assertEqual(snapshot['before_id'],11)
        self.assertEqual(len(calls),6)
        self.assertEqual(result[-1].text,'Con cũng phải sống cho thật tốt')
        self.assertIsNone(active_memory.get())

class HandoffBoundaryTests(unittest.TestCase):
    def test_a_time_gap_withholds_local_addressing_but_retains_relation_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            archive=MemoryArchive(Path(folder)/'memory.sqlite3','film')
            quote='萧玉娘是萧衡的生母'
            rows=[dict(cue(1,quote),time=[0,1])]
            archive.record(rows,{1:'Mẹ con'},{'source_notes':[note(1,quote,'萧玉娘','萧衡',kind='addressing'),note(1,quote,'萧玉娘','萧衡')]})
            with memory_session(archive):
                packet=build_handoff({}, {},[dict(cue(2,'萧玉娘望着萧衡'),time=[40,41])],[],[],{})['handoff']
            self.assertEqual(packet['scene_boundary_hints'][0]['gap_seconds'],39)
            self.assertEqual([n['kind'] for n in packet['source_linked_notes']],['relationship'])

    def test_new_character_names_are_recalled_without_requiring_the_initial_character_bible(self):
        with tempfile.TemporaryDirectory() as folder:
            archive=MemoryArchive(Path(folder)/'memory.sqlite3','film')
            quote='萧玉娘是萧衡的生母'
            archive.record([cue(1,quote)],{1:'Mẹ con'},{'source_notes':[note(1,quote,'萧玉娘','萧衡')]})
            with memory_session(archive):
                packet=build_handoff({}, {},[cue(100,'萧玉娘望着萧衡')],[],[],{})['handoff']
            self.assertTrue(packet['source_linked_notes'])
            self.assertTrue(any(row['id']==1 for row in packet['recalled_cues']))
