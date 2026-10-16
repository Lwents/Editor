"""Source-linked, bounded handoffs independent of any browser chat history.

Quotes establish provenance, not the truth of the model's interpretation. Keep
conflicts and uncertainty visible, and never infer speakers from a named addressee.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from app.services.persistence import PersistenceError, atomic_json

VERSION = 1
active_memory = ContextVar('translation_memory_archive', default=None)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _text(value, limit=600):
    return unicodedata.normalize('NFC', value.strip())[:limit] if isinstance(value, str) else ''


def source_notes(updates, batch, valid_ids):
    """Only accept exact citations to valid cues of this response, never future IDs."""
    sources = {int(item['id']): unicodedata.normalize('NFC', str(item['source']))
               for item in batch if int(item['id']) in valid_ids}
    result = []
    rows = updates.get('source_notes', []) if isinstance(updates, dict) else []
    for row in rows[:12] if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get('kind') not in {'character', 'relationship', 'addressing', 'glossary', 'scene', 'uncertainty'}:
            continue
        evidence = row.get('evidence')
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 4:
            continue
        citations = []
        for item in evidence:
            if not isinstance(item, dict) or type(item.get('id')) is not int:
                break
            quote = _text(item.get('quote'), 400)
            if item['id'] not in sources or len(quote) < 2 or quote not in sources[item['id']]:
                break
            citations.append({'id': item['id'], 'quote': quote})
        if len(citations) != len(evidence):
            continue
        subject, target, value = _text(row.get('subject'), 100), _text(row.get('target'), 100), _text(row.get('value'), 500)
        if not subject or not value or (row['kind'] in {'relationship', 'addressing'} and not target):
            continue
        quoted = ' '.join(item['quote'] for item in citations)
        identities_explicit = subject in quoted and (not target or target in quoted)
        result.append({'kind': row['kind'], 'subject': subject, 'target': target, 'value': value,
                       'scope': 'local' if row['kind'] in {'scene', 'addressing'} else 'reference',
                       'uncertain': row.get('uncertain') is not False or row['kind'] == 'uncertainty'
                                    or not identities_explicit,
                       'evidence': citations, 'status': 'source_linked_interpretation'})
    return result


class MemoryArchive:
    """Archive all validated bilingual cues and cited notes; select only earlier evidence."""
    def __init__(self, path: Path, namespace: str, phase='translation'):
        self.path, self.namespace, self.phase = Path(path), namespace, phase
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS context_cues (namespace TEXT, phase TEXT, id INTEGER, source TEXT, translated TEXT, time TEXT, PRIMARY KEY(namespace,phase,id))')
            connection.execute('CREATE TABLE IF NOT EXISTS context_notes (namespace TEXT, phase TEXT, key TEXT, last_id INTEGER, note TEXT, PRIMARY KEY(namespace,phase,key))')
            connection.execute('CREATE INDEX IF NOT EXISTS context_notes_before ON context_notes(namespace,last_id)')

    @contextmanager
    def connect(self):
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=10)
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA synchronous=FULL')
            with connection:
                yield connection
        except sqlite3.Error as exc:
            raise PersistenceError('HANDOFF_STORAGE_FAILED: Không lưu/đọc được hồ sơ ngữ cảnh; checkpoint được giữ.') from exc
        finally:
            if connection is not None:
                connection.close()

    def record(self, batch, texts, updates=None, *, seed=False):
        from .caption_evidence import NON_CAPTION
        texts = {i: text for i, text in texts.items() if text != NON_CAPTION}
        rows = [(self.namespace, self.phase, int(item['id']), str(item['source']), str(texts[int(item['id'])]),
                 encoded(item.get('time'))) for item in batch if int(item['id']) in texts]
        if not rows:
            return
        notes = source_notes(updates, batch, set(texts))
        with self.connect() as connection:
            verb = 'INSERT OR IGNORE' if seed else 'INSERT OR REPLACE'
            connection.executemany(verb + ' INTO context_cues VALUES (?,?,?,?,?,?)', rows)
            connection.executemany('INSERT OR IGNORE INTO context_notes VALUES (?,?,?,?,?)',
                [(self.namespace, self.phase, hashlib.sha256(encoded(note).encode()).hexdigest(),
                  max(e['id'] for e in note['evidence']), encoded(note)) for note in notes])

    def seed(self, events, translations, updates=None):
        batch = [{'id':i+1, 'source':unicodedata.normalize('NFC', events[i].text.strip()),
                  'time':[events[i].start, events[i].end]} for i in range(len(translations))]
        self.record(batch, {i+1:text for i,text in enumerate(translations)}, updates, seed=True)

    def recent(self, before, limit=20):
        phases = ('translation', 'review') if self.phase == 'review' else ('translation',)
        placeholders = ','.join('?' for _ in phases)
        with self.connect() as connection:
            rows = connection.execute(f'SELECT id,source,translated,time,phase FROM context_cues WHERE namespace=? AND phase IN ({placeholders}) AND id<? ORDER BY id DESC,phase DESC LIMIT ?',
                                      (self.namespace, *phases, before, limit*2)).fetchall()
        by_id = {}
        for identifier, source, translated, stamp, phase in rows:
            row = {'id':identifier, 'source':source, 'translated':translated, 'time':json.loads(stamp)}
            if identifier not in by_id or phase == self.phase:
                by_id[identifier] = row
        return sorted(by_id.values(), key=lambda row:row['id'])[-limit:]

    def recall(self, before, terms, limit=12):
        if not terms:
            return []
        clauses = ' OR '.join("instr(source,?)>0" for _ in terms)
        with self.connect() as connection:
            query = 'SELECT id,source,translated,time FROM context_cues WHERE namespace=? AND phase=? AND id<? AND ('+clauses+') ORDER BY id '
            args = (self.namespace, 'translation', before, *terms)
            rows = connection.execute(query+'ASC LIMIT 8', args).fetchall()
            rows += connection.execute(query+'DESC LIMIT 24', args).fetchall()
        rows = list({row[0]:row for row in rows}.values())
        # Retrieval hints do not assert relationships; the full quote remains visible.
        rows.sort(key=lambda row:(-int(bool(re.search(r'母|父|夫|妻|师|不是|其实|叫',row[1]))), -row[0]))
        return [{'id':i,'source':source,'translated':target,'time':json.loads(stamp)} for i,source,target,stamp in rows[:limit]]

    def note_candidates(self, before):
        with self.connect() as connection:
            rows = connection.execute('SELECT note FROM context_notes WHERE namespace=? AND last_id<? ORDER BY last_id,key',
                                      (self.namespace,before)).fetchall()
        return [json.loads(raw) for (raw,) in rows]

    def notes(self, before, terms, candidates=None):
        candidates = self.note_candidates(before) if candidates is None else candidates
        selected = []
        seen = set()
        for note in candidates:
            last = max(e['id'] for e in note['evidence'])
            names = ' '.join([note['subject'], note['target']])
            relevant = any(term in names for term in terms)
            if note['scope'] == 'local' and before-last > 40:
                continue
            if not relevant and before-last > 24 and note['kind'] != 'glossary':
                continue
            key = encoded(note)
            if key not in seen:
                selected.append(note);seen.add(key)
        return selected


@contextmanager
def memory_session(archive, mode='balanced'):
    token = active_memory.set((archive, mode) if archive else None)
    try:
        yield
    finally:
        active_memory.reset(token)


def record_context(batch, texts, updates, *, context=None):
    session = active_memory.get()
    if session:
        from .character_names import build_character_name_registry, canonicalize_character_text
        registry=build_character_name_registry(context)
        session[0].record(batch, {i:canonicalize_character_text(text,registry) for i,text in texts.items()}, updates)


def _legacy(memory, window):
    """Legacy character names remain hints, never a forced identity/role assignment."""
    characters = []
    for row in memory.get('characters', [])[-12:]:
        if isinstance(row, dict):
            small = {key:_text(row[key],150) for key in ('id','name') if isinstance(row.get(key),str)}
            if isinstance(row.get('aliases'),list):
                small['aliases'] = [_text(alias,100) for alias in row['aliases'][:6] if isinstance(alias,str)]
            if small:
                characters.append(small)
    glossary_values = memory.get('glossary',{})
    glossary_values = glossary_values if isinstance(glossary_values,dict) else {}
    glossary = [{'source':_text(key,100),'translation':_text(value,150)}
                for key,value in glossary_values.items()
                if isinstance(key,str) and key in window and isinstance(value,str)][:16]
    conflicts = [row for row in memory.get('glossary_conflicts',[])
                 if isinstance(row,dict) and isinstance(row.get('source'),str) and row['source'] in window][:6]
    hints = []
    for kind in ('characters','relationships','addressing_rules'):
        values = memory.get(kind,[])
        if isinstance(values,list):
            for value in values[-2:]:
                if isinstance(value,(str,dict)):
                    hints.append({'kind':kind,'value':encoded(value)[:350],
                                  'status':'unverified_model_note_not_a_fact'})
    return {'characters':characters, 'legacy_glossary':glossary,'legacy_glossary_conflicts':conflicts,
            'legacy_notes':hints,
            'legacy_status':'unverified_reference_only'}


def matched_entities(candidates, window):
    """An alias inside a longer person's name is not an independent mention."""
    occupied, result = [], []
    for name in sorted(set(candidates), key=lambda value:(-len(value),value)):
        if len(name)<2:
            continue
        latin = bool(re.fullmatch(r'[\w\s-]+',name) and not re.search(r'[\u3400-\u9fff]',name))
        pattern = (r'(?<!\w)' if latin else '') + re.escape(name) + (r'(?!\w)' if latin else '')
        spans = [match.span() for match in re.finditer(pattern,window)
                 if not any(start<=match.start() and end>=match.end() for start,end in occupied)]
        if spans:
            result.append(name);occupied.extend(spans)
    return result[:16]


def build_handoff(memory, context, batch, previous, following, registry):
    """Deterministic handoff: identical input survives chat rotation and restart."""
    before = min(int(item['id']) for item in batch)
    session = active_memory.get()
    archive, mode = session if session else (None, 'balanced')
    archived_recent = archive.recent(before) if archive else []
    # A large untranslated gap is not continuous dialogue. Older evidence can
    # still be recalled by entity, but must not identify the current speaker.
    recent = [row for row in archived_recent if before-row['id'] <= 32]
    # Previous context includes source-only cues across a failed translation gap.
    by_id = {row['id']:row for row in recent}
    for row in previous:
        if type(row.get('id')) is int and row['id'] < before:
            if row['id'] not in by_id:
                by_id[row['id']] = dict(row)
    recent = sorted(by_id.values(), key=lambda row:row['id'])[-20:]
    boundaries = []
    timed = [row for row in [*recent,*batch] if isinstance(row.get('time'),list) and len(row['time'])==2]
    timed.sort(key=lambda row:row['id'])
    for left,right in zip(timed,timed[1:]):
        gap = right['time'][0]-left['time'][1]
        if gap >= 8:
            boundaries.append({'before_id':right['id'],'gap_seconds':round(gap,3),
                               'status':'possible_scene_boundary_not_confirmed'})
    boundary_id = max((row['before_id'] for row in boundaries if row['before_id']<=before),default=0)
    window = ' '.join(str(row.get('source','')) for row in [*[r for r in recent[-8:] if before-r['id']<=16],*batch,*following])
    candidates = archive.note_candidates(before) if archive else []
    from .character_names import _ADDRESS_ALIASES
    new_names = {name for note in candidates for name in (note['subject'],note['target'])
                 if len(name)>=2 and name in window and name.casefold() not in _ADDRESS_ALIASES}
    terms = matched_entities(set(registry) | new_names, window)
    recalled = archive.recall(before, terms) if archive else []
    recalled = [row for row in recalled if row['id'] not in by_id]
    notes = archive.notes(before, terms, candidates) if archive else []
    notes = [row for row in notes if row['scope']!='local' or max(e['id'] for e in row['evidence'])>=boundary_id]
    notes.sort(key=lambda row:(-int(any(term in row['subject']+' '+row['target'] for term in terms)),
                               -max(e['id'] for e in row['evidence']), encoded(row)))
    groups = {}
    for note in notes:
        key = (note['kind'],note['subject'],note['target'],note['scope'])
        groups.setdefault(key, []).append(note)
    conflicts = [{'kind':key[0],'subject':key[1],'target':key[2], 'alternatives':values,
                  'action':'recheck_source_do_not_overwrite'} for key,values in groups.items()
                 if len({row['value'] for row in values})>1]
    gaps = []
    ids = [row['id'] for row in recent if row.get('translated')]
    if not ids and archived_recent:
        ids = [archived_recent[-1]['id']]
    for left,right in zip(ids,ids[1:]+[before]):
        if right>left+1:
            gaps.append({'from':left+1,'to':right-1})
    packet = {'version':VERSION,'namespace':archive.namespace if archive else 'ephemeral', 'before_id':before,
              'priority':'current_source > quoted_source > fixed_name_spellings > prior_translation_or_model_notes',
              'speaker_policy':'unknown_unless_source_identifies_speaker_and_addressee; a_called_name_is_not_the_speaker',
              'recent_cues':[], 'recalled_cues':[], 'source_linked_notes':[], 'conflicts':[],
              'open_questions':[], 'untranslated_gaps':gaps,
              'scene_boundary_hints':boundaries[-4:],
              'translation_status':'prior_translations_are_references_not_semantic_ground_truth'}
    budget = {'fast':7000,'balanced':11000,'quality':15000}.get(mode,11000)
    def add(key, row):
        packet[key].append(row)
        if len(encoded(packet)) > budget-400:
            packet[key].pop();return False
        return True
    # Reserve room for long-range relationships and conflicts instead of filling
    # the entire window with recent dialogue. All omitted detail stays on disk.
    for row in reversed(recent[-4:]):
        if len(encoded(packet))+len(encoded(row)) < budget*.4:
            add('recent_cues',row)
    conflict_keys = {encoded(note) for row in conflicts for note in row['alternatives']}
    for conflict in conflicts[:6]:
        if not add('conflicts',conflict):
            add('open_questions',{'kind':'conflict','subject':conflict['subject'],
                'target':conflict['target'],'action':'conflicting_history_do_not_force_a_rule'})
    for note in notes[:32]:
        if encoded(note) not in conflict_keys:
            add('open_questions' if note['uncertain'] else 'source_linked_notes', note)
    for row in recalled:add('recalled_cues',row)
    included = {row['id'] for row in packet['recent_cues']}
    for row in reversed(recent):
        if row['id'] not in included:
            add('recent_cues',row)
    packet['recent_cues'].sort(key=lambda row:row['id'])
    packet['selection']={'archived_notes':len(notes), 'included_notes':len(packet['source_linked_notes']),
                         'conflicts_detected':len(conflicts), 'budget_chars':budget}
    packet['digest'] = hashlib.sha256(encoded(packet).encode()).hexdigest()
    if archive:
        snapshot = archive.path.with_name('translation_handoff.json' if archive.phase=='translation' else 'review_handoff.json')
        atomic_json(snapshot,packet)
    return {**_legacy(memory,window), 'handoff':packet}
