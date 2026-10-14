# Language grounding for subtitle translation

Checked 2026-10-15. HSK and IELTS describe human language proficiency, not a
certificate for this application or a way to change Gemini Web model weights.
No corpus below has been certified as HSK 9 or IELTS 9 bilingual training data.

## Primary sources

| Resource | Use | Limits / reuse |
| --- | --- | --- |
| [Official HSK 7–9 sample](https://www.chinesetest.cn/userfiles/file/HSK/level7-9/HSK7-9.pdf) | Reading, language and translation assessment reference | Do not assume modern exam vocabulary covers historical drama address forms. No exam text copied into runtime prompts. |
| [HSK word lists](https://github.com/krmanik/HSK-3.0) | Advanced term coverage reference | [Separate data licenses](https://github.com/krmanik/HSK-3.0/blob/main/License.md), including CC BY-SA 4.0 for HSK word lists. Chinese lists are not Vietnamese sense dictionaries. |
| [IELTS scoring](https://ielts.org/take-a-test/your-results/ielts-scoring-in-detail) | Accurate, appropriate and fluent wording | Band 9 is a proficiency score, not a validated fixed vocabulary list. No IELTS copyrighted essays imported. |
| [Tatoeba downloads](https://tatoeba.org/en/downloads) | Chinese–Vietnamese and English–Vietnamese sentence pairs for review/evaluation | CC BY 2.0 FR by default, some CC0. Preserve both sentence URLs for attribution. Community translations can be ambiguous, outdated or incorrect. |
| [Tatoeba Challenge](https://github.com/Helsinki-NLP/Tatoeba-Challenge) | Candidate evaluation data and multilingual MT benchmark infrastructure | Check each actual pair's origin/license. A general MT corpus is not automatically an advanced exam corpus. |

## Implementation

`app/resources/subtitle_lexicon.json` contains 37 **original editorial sense
notes**, including historical drama terms and ambiguous English phrases. They
are not copied from or labeled as an official HSK/IELTS dictionary. Runtime
retrieval sends only source-matching notes (8 for fast, 16 otherwise; 3,200
character cap). Notes are conditional, not mandatory substitutions; names,
address forms and actual source evidence take precedence.
Explicit simplified/traditional variants retrieve the same sense. A later
full-video test caught a literal translation of `喂你个锤锤` in fast mode; an
original colloquial sense note now covers that phrase. Translation policy 24
and lexicon version 2 invalidate earlier translation results when reprocessing.

Research downloads remain outside the release in `.runtime/language-research`.
The HSK source is pinned to commit
`182692ce5a11bc30bdc771835d2f0f27491c25de`. Inspection found repeated entries and
numeric sense suffixes in the raw list; it must not be treated as a clean
bilingual dictionary. Initial Tatoeba searches retrieved 30 candidate pairs;
some search results did not contain the requested term (e.g. `hard` for
`hardly`). These unreviewed pairs are **not** injected into translation prompts.

## Accuracy acceptance

Test Chinese idioms, English polysemy, negation, tense/modality, speaker versus
addressee, names and titles independently. Use original video text as evidence
and the friend's T videos as evaluation references; do not inject a T reference
into the model and report its reproduction as an independent accuracy result.
Measure OCR omissions and timing separately from translation meaning. A few
passing vocabulary cases do not establish 90% accuracy across episodes.

## Stability changes

New UI jobs default to local OCR + Gemini text. An explicit optional image
verification checkbox retains the slower image path. Unverified image cues
remain marked for review. This removes an optional image round trip from the
default path, but does not guarantee Gemini text response times.

Successful context can be reused across jobs only under the same content key:
complete transcript/timing, image video digest (or text-only mode), verified
caption evidence, context policy, engine and model. Artifact hashes are
verified; failed or empty context is not published as a reusable result.

Caption decisions can also be reused independently of their batch IDs. Keys
include the complete video digest, exact cue times, raw OCR text and caption
regions. Only confirmed image/local readings are stored; provisional text,
gap proofs and narrative context are not transferred. When OCR is refreshed,
an old checkpoint must verify its original content fingerprint before its
confirmed decisions can be preserved.

OCR algorithm 16 conditionally retries an empty detection at original pixel
resolution. A stroke gate rejects empty/solid bright regions. An installed
Server recognizer reads tight normal/monochrome crops in a two-image batch;
no model download or expected transcript is supplied. These recovered cues
still require review. A failed optional model retains the Mobile reading.

Native-generated source text is not fed back as a new ASR reference on retry,
which avoids an unnecessary OCR rescan. Speech agreement is reported only
when a separate ASR transcript exists, never by comparing OCR with its copy.

Fast mode caps Gemini Web batches at 24 (balanced 12, quality 8). One actual
24-cue experiment took 24.53 seconds using two 12-cue requests and 14.13 seconds
using one 24-cue request. This is a small latency observation, not a universal
speed multiplier or accuracy equivalence claim.
