"""Bounded, source-matched sense notes; never perform global word substitution."""
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

LEXICON_VERSION = 2
_RESOURCE = Path(__file__).resolve().parents[2] / 'resources' / 'subtitle_lexicon.json'


@lru_cache(maxsize=1)
def _entries():
    data = json.loads(_RESOURCE.read_text(encoding='utf-8'))
    if data['version'] != LEXICON_VERSION:
        raise ValueError('Subtitle lexicon version mismatch')
    return tuple(data['entries'])


def lexical_guidance(source_texts, target_language, max_entries=16, max_chars=3200):
    if str(target_language).lower() not in {'vi', 'vie', 'vietnamese', 'tiếng việt'}:
        return []
    # Keep sentence boundaries: two unrelated cues cannot form a matched term.
    texts = [unicodedata.normalize('NFC', str(text)).casefold() for text in source_texts]
    result, used = [], 0
    for entry in sorted(_entries(), key=lambda row: -len(row['source'])):
        matched_form = None
        for form in [entry['source'], *entry.get('aliases', [])]:
            term = form.casefold()
            if entry['language'] == 'en':
                pattern = r'(?<!\w)' + r'\s+'.join(re.escape(part) for part in term.split()) + r'(?!\w)'
                matched = any(re.search(pattern, text) for text in texts)
            else:
                matched = any(term in text for text in texts)
            if matched:
                matched_form = form
                break
        if matched_form is None:
            continue
        hint = {key: entry[key] for key in ('source', 'vi', 'usage')}
        hint['source'] = matched_form
        size = len(json.dumps(hint, ensure_ascii=False))
        if len(result) >= max_entries or used + size > max_chars:
            break
        result.append(hint)
        used += size
    return result
