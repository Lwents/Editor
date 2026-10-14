# Subtitle reference benchmark

## Native frame performance

Build the C++ mask/comparison library with `python scripts/desktop/build-native.py`
from the repository root. It uses MSVC on Windows (development-only Zig is an
alternative) and a C++ compiler on Linux. The app needs neither compiler nor a
new OpenCV SDK. Release builds compile and test the library automatically.

Run `python be/benchmarks/benchmark_frame_processing.py original.mp4
--output benchmark-results --start 45 --seconds 12` to compare masks on 30 real
frames and OCR output on the same short clip. `results.json` reports stage
timings and asserts identical subtitle text/timecodes. Masks must also match
the reference at thresholds, image edges and strided slices.

The native kernels fuse colour/brightness filtering and glyph-mask comparison;
OpenCV still decodes the video and ONNX Runtime still runs the OCR model.
Frame refinement uses a 32 MiB cache and avoids seeking when the next frame is
already at the decoder cursor. It does not lower the scan rate or remove
caption boundary verification. A kernel speedup is not an end-to-end translation
speedup; model warmup, inference and Gemini waits must be measured separately.
`subtitles.recognition.json` records OCR/mask/refinement timings; each job's
`stage-performance.json` records OCR, image verification, context, translation
and final review timings, including failed attempts.

`run_translation_benchmark.py` translates numbered original videos using the
production OCR → image verification → context → translation → review pipeline.
Uncertain Chinese captions first receive three local OCR reads of the actual
caption pixels at different widths. Only unanimous high-confidence readings
are accepted; empty, conflicting, rare isolated, or shortened readings still
require Gemini. Reports identify the method used for each decision.
For a short caption fragment beside a verified longer caption, an already
installed PP-OCRv5 Server recognizer can read three crops directly. All reads
must agree above .98 and have nearby source-image support; shorter readings
remain unresolved. This avoids adding a network request for some detector
errors. The default full-video scan still uses the Mobile model.
Short gaps between confirmed identical captions are bridged only if both
local OCR crops independently read that caption on every missing frame at
confidence >= .98. A blank, conflicting or unreadable frame keeps the gap.

Chinese translation batches without explicit source evidence of a spousal
address receive an addressing contract that excludes personal pronouns
`chàng`/`thiếp`. Output is validated before checkpointing; only offending IDs
need correction. A model-generated relationship guess or a caring scene does
not override this contract. Ordinary `chàng trai` and explicit source marital
terms are handled separately.

Gemini Web translation and final review use at most 12 cues per request.
The API profile remains unchanged. Optional `--batch-items` and `--vision-items`
are benchmark experiments, not application settings; reports record overrides.

Reference videos named `T<number>.mp4` are only read by the evaluator afterwards.
They are never supplied to the translator.

Gemini Web keeps one conversation for each video translation (including its
OCR checks, context, translation batches, and final review). The evaluator
uses a separate conversation for reference comparisons. Private conversation
URLs are stored in the dedicated browser profile for checkpoint resumption.
If Google requires verification, finish it manually in the browser; the
runner stops sending requests and keeps all completed checkpoints. Refusals and
request timeouts also stop the run without replaying the same expensive request.
Resuming a refused or timed-out job starts a fresh chat; successful requests
within a job continue using its existing conversation.
Scene-context analysis may switch once from a failed image request to a
transcript-only task, recording that the scene images were not verified. A
refused text request, subtitle OCR request or translation request still stops.

Use the backend's Windows Python environment and an existing Gemini Web login:

```powershell
be\.venv\Scripts\python.exe be\benchmarks\run_translation_benchmark.py 12 13 14 15 --root C:\Videos --bench D:\Benchmarks --evaluate
```

The evaluator inspects each recognized cue midpoint plus independent probes
every second, which can expose completely omitted captions. Evidence images
contain matching original and reference frames. Repeated samples of the same
caption count once; that caption passes only when all its sampled occurrences
pass meaning, addressing, and naturalness checks. Similar wording is not the
score criterion.

Reports record uncertain readings separately. `conservative_percent` treats
each uncertain frame as an additional failure. These are automated Gemini
judgements of sampled frames, not human-certified full-video accuracy.
The evaluator also records candidate captions over blank source frames as
spurious captions; those frames must not disappear from the error report.

Keep calibration episodes separate from later validation episodes. Freeze the
translator's code before validation; changes based on validation failures need
to be identified as another tuning iteration, not reported as an untouched
validation set. `--gold-bench` rechecks against previously read images, without
showing the previous pass/fail decisions to the evaluator.

For tuning one episode at a time, pass a single episode number. Finish its
reference comparison, record the source and target hashes and policy version,
fix the observed error category, and rerun that episode before advancing.
This tunes the application prompts and processing rules; it does not train
Gemini's model weights. Preserve earlier round reports rather than replacing
their scores with those from a later target. Manual visual reviews must name
their reviewer/method and sampled coverage separately from Gemini judgements.
Do not count downloaded references supplied to the translator as independent
validation, and do not report cached OCR/context timing as a fresh full run.

Outputs are saved under `--bench`, outside the source tree: SRT files, raw image
readings, scores, timings, and checkpoint files. Videos and subtitle references
should not be committed. Timings using completed checkpoints or simultaneous
OCR workers must not be reported as fresh single-video end-to-end latency.
