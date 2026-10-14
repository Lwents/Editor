"""Run the production translator without exposing reference subtitles to it."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import settings
from app.services.ai.translation import TRANSLATION_POLICY_VERSION
from app.services.ai.gemini_web import GeminiWebRefusal, GeminiWebTimeout, GeminiWebVerificationRequired
from app.services.subtitles.source import _prepare_target_subtitles
from app.services.subtitles.timing import parse_srt
from app.services.subtitles.visible import ALGORITHM_VERSION
from evaluate_reference_videos import evaluate, recheck_from_gold


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('episodes', type=int, nargs='+')
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--bench', type=Path, required=True)
    parser.add_argument('--evaluate', action='store_true')
    parser.add_argument('--gold-bench', type=Path)
    parser.add_argument('--show-browser', action='store_true', help='Diagnose visible versus background browser latency')
    parser.add_argument('--batch-items', type=int, help='Experiment with translation batch size; recorded in the run report')
    parser.add_argument('--vision-items', type=int, help='Experiment with caption image batch size; recorded in the run report')
    args = parser.parse_args()
    settings.translation_engine = 'gemini_web'
    settings.gemini_web_background = not args.show_browser
    if args.vision_items:
        if not 1 <= args.vision_items <= 6:
            parser.error('--vision-items must be between 1 and 6')
        from app.services.subtitles import vision_review
        vision_review.IMAGE_BATCH_SIZE = args.vision_items
    if args.batch_items:
        if not 1 <= args.batch_items <= 40:
            parser.error('--batch-items must be between 1 and 40')
        from dataclasses import replace
        from app.services.ai import translation
        default_profile = translation.get_translation_profile
        translation.get_translation_profile = lambda mode=None: replace(
            default_profile(mode), translation_batch_items=args.batch_items)
    args.bench.mkdir(parents=True, exist_ok=True)
    (args.bench / 'worker.json').write_text(json.dumps({'pid': os.getpid(), 'episodes': args.episodes,
        'ocr_version': ALGORITHM_VERSION, 'translation_policy': TRANSLATION_POLICY_VERSION}), encoding='utf-8')
    print('WORKER', os.getpid(), 'OCR', ALGORITHM_VERSION, 'POLICY', TRANSLATION_POLICY_VERSION, flush=True)
    for episode in args.episodes:
        work = args.bench / str(episode)
        work.mkdir(parents=True, exist_ok=True)
        source = work / 'source.mp4'
        if not source.exists():
            shutil.copyfile(args.root / f'{episode}.mp4', source)
        (work / 'asr-metadata.json').write_text(json.dumps({'language': 'zh'}), encoding='utf-8')
        started = time.monotonic()
        stages = []
        def progress(stage, percent):
            stages.append({'elapsed_seconds': round(time.monotonic() - started, 3),
                           'stage': stage, 'progress': percent})
            print('EPISODE', episode, stage, flush=True)
        print('EPISODE', episode, 'START', flush=True)
        try:
            result = await _prepare_target_subtitles(work / 'subtitles.visible.srt', work, 'zh',
                progress, source_video=source)
            report = {'episode': episode, 'seconds': round(time.monotonic() - started, 3),
                'cues': len(parse_srt(result)), 'result': str(result), 'ocr_version': ALGORITHM_VERSION,
                'translation_policy': TRANSLATION_POLICY_VERSION,
                'batch_items_experiment': args.batch_items,
                'vision_items_experiment': args.vision_items,
                'stages': stages,
                'timing_note': 'May reuse matching completed checkpoints; do not compare with concurrent runs.'}
            benchmark_path = work / 'benchmark.json'
            if benchmark_path.exists():
                with (work / 'benchmark-history.jsonl').open('a', encoding='utf-8') as history:
                    history.write(json.dumps(json.loads(benchmark_path.read_text(encoding='utf-8')), ensure_ascii=False) + '\n')
            benchmark_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            (work / 'benchmark-error.json').unlink(missing_ok=True)
            print('COMPLETED', json.dumps(report), flush=True)
            if args.evaluate:
                try:
                    from app.services.ai.gemini_web import browser
                    # Never put reference subtitles into the translation chat.
                    with browser.conversation(f'benchmark-reference:{work.resolve()}:policy:{TRANSLATION_POLICY_VERSION}'):
                        if args.gold_bench:
                            await recheck_from_gold(episode, args.bench, args.gold_bench)
                        else:
                            await evaluate(episode, args.root, args.bench)
                finally:
                    from app.services.ai.gemini_web import browser
                    # Keep a Google verification page open for the user.
                    import sys
                    if not isinstance(sys.exception(), GeminiWebVerificationRequired):
                        await browser.close()
        except Exception as exc:
            (work / 'benchmark-error.json').write_text(json.dumps({'error': str(exc),
                'type': type(exc).__name__, 'seconds': round(time.monotonic() - started, 3),
                'cause': str(exc.__cause__) if exc.__cause__ else None,
                'stages': stages}, ensure_ascii=False, indent=2), encoding='utf-8')
            print('FAILED', episode, type(exc).__name__, str(exc), flush=True)
            if isinstance(exc, (GeminiWebVerificationRequired, GeminiWebRefusal, GeminiWebTimeout)):
                print('Stopped for Google verification, refusal or timeout; no further requests sent.', flush=True)
                break
        if (args.bench / 'stop-after-episode').exists():
            print('Stopped between episodes; checkpoints retained.', flush=True)
            break


if __name__ == '__main__':
    asyncio.run(main())
