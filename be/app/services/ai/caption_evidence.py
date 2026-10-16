"""Keep explicit non-caption decisions separate from uncertain OCR failures."""
from pathlib import Path
from app.services.persistence import read_json
from .cue_recovery import CueRecovery

NON_CAPTION = '__VERIFIED_NON_CAPTION__'


def verified_non_caption(item, directory):
    decision = (read_json(Path(directory) / 'caption-repair' / 'decisions.json') or {}).get(str(item['id']), {})
    confidence = decision.get('confidence')
    frames = decision.get('non_caption_frame_ids')
    return (decision.get('is_caption') is False
            and type(confidence) in (int, float) and .95 <= confidence <= 1
            and decision.get('input_key') == CueRecovery.key(item)
            and isinstance(frames, list) and len(frames) == 3
            and all(type(i) is int for i in frames) and len(set(frames)) == 3
            and isinstance(decision.get('reason'), str) and bool(decision['reason'].strip()))


def filter_verified_non_captions(source_events, translated_events, directory):
    """Filter both sides together so review/audit IDs remain aligned."""
    import unicodedata
    if len(source_events) != len(translated_events):
        raise RuntimeError('Non-caption filtering requires aligned source and translation.')
    source, target = [], []
    for i, (original, translated) in enumerate(zip(source_events, translated_events), 1):
        if translated.text == NON_CAPTION:
            item = {'id': i, 'source': unicodedata.normalize('NFC', original.text.strip()),
                    'time': [original.start, original.end]}
            if not verified_non_caption(item, directory):
                raise RuntimeError(f'NON_CAPTION_EVIDENCE_MISSING: ID {i}; no subtitle was removed.')
            continue
        source.append(original)
        target.append(translated)
    return source, target
