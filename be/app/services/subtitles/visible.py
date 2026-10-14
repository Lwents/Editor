"""Read hard subtitles and their display intervals directly from video frames."""
from __future__ import annotations

import asyncio
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
import json
import re
import time
import hashlib
from queue import SimpleQueue, Empty
from threading import Event
from typing import Callable

from app.core import settings
from app.services.ai import asr_cache

from app.services.subtitles.ass import write_srt
from app.services.subtitles.timing import SubtitleEvent
from app.services.subtitles.ocr import get_reader, read_lines, read_known_band, discover_regions, recognition_language, text_key, has_caption_strokes, read_caption_fallback
from app.services.subtitles.frame_processing import caption_masks, compare_masks, native_library, FrameCache

ALGORITHM_VERSION = 16

_locks: dict[str, asyncio.Lock] = {}


def _same_text(a: str, b: str) -> bool:
    if not a or not b:
        return a == b
    return SequenceMatcher(None, text_key(a), text_key(b)).ratio() >= 0.85


def _caption_reference(work_dir: Path) -> Path | None:
    """Do not turn our own OCR output into a new ASR reference on retry."""
    reference = work_dir / 'subtitles.source.srt'
    if not reference.is_file():
        return None
    try:
        origin = json.loads((work_dir / 'subtitles.source.origin.json').read_text(encoding='utf-8'))
        if isinstance(origin, dict) and origin.get('kind') == 'visible_ocr' and origin.get('sha256') == asr_cache.digest(reference):
            whisper = work_dir / 'subtitles.whisper.srt'
            return whisper if whisper.is_file() else None
    except (OSError, ValueError):
        pass
    try:
        metadata = json.loads((work_dir / 'asr-metadata.json').read_text(encoding='utf-8'))
        if isinstance(metadata, dict) and metadata.get('engine') in {'faster-whisper', 'whisper', 'whisper.cpp', 'stable-ts'}:
            return reference
    except (OSError, ValueError):
        pass
    for name in ('subtitles.source.provisional.srt', 'subtitles.source.verified.srt'):
        native = work_dir / name
        if native.is_file() and asr_cache.digest(native) == asr_cache.digest(reference):
            return None
    return reference


def _extract_visible(source: Path, output: Path, on_progress=None, cancelled=None) -> Path | None:
    import cv2
    import numpy as np
    started = time.perf_counter()
    def report(stage, completed=0, total=0):
        if cancelled is not None and cancelled.is_set():
            raise RuntimeError('Visible subtitle scan cancelled')
        if on_progress is not None:
            on_progress((stage, completed, total))

    report('Đang nạp bộ nhận diện chữ trên video')
    language = recognition_language(source.parent)
    reader = get_reader(language)
    capture = cv2.VideoCapture(str(source))
    fps = capture.get(cv2.CAP_PROP_FPS)
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if fps <= 0 or count <= 0:
        capture.release()
        return None
    # A strong reading on one frame may anchor a caption that weaker frames
    # miss. Image review must inspect these anchors before they can be used.
    minimum_duration = .99 / fps if language == 'ch' and settings.translation_engine.lower() == 'gemini_web' else .08
    step = max(1, round(fps / 10))
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    try:
        reference = _caption_reference(source.parent)
        references = reference.read_text(encoding='utf-8-sig') if reference else ''
    except OSError:
        references = ''
    try:
        report('Đang tìm vùng phụ đề trên video')
        regions = discover_regions(capture, reader, count, width, height, references)
    except Exception:
        capture.release()
        raise
    if not regions:
        capture.release()
        return None

    frame_results = {}
    fresh_frames = set()
    fallback_frames = set()
    last_signature = None
    last_result = ('', 0.)
    last_ocr_frame = -count
    last_box = None
    last_white_points = None
    last_dark_points = None
    ocr_calls = 0
    reused_frames = 0
    recognition_only_calls = 0
    fallback_calls = 0
    masks_seconds = 0.0
    ocr_seconds = 0.0
    frame_cache = FrameCache(capture)

    def timed_ocr(operation, *args, **kwargs):
        nonlocal ocr_seconds
        before = time.perf_counter()
        try:
            return operation(*args, **kwargs)
        finally:
            ocr_seconds += time.perf_counter() - before

    def read_text(frame, index, boundary_labels=None):
        nonlocal last_signature, last_result, last_ocr_frame, ocr_calls, reused_frames, last_box, last_white_points, last_dark_points, recognition_only_calls, fallback_calls, masks_seconds, ocr_seconds
        if cancelled is not None and cancelled.is_set():
            raise RuntimeError('Visible subtitle scan cancelled')
        if index in frame_results:
            return frame_results[index]
        crops = [frame[top:bottom, :] for top, bottom in regions if bottom > top]
        if not crops:
            return '', 0.
        # One detector pass for all discovered bands; keep their identities so
        # unrelated signs in another part of the frame are not merged as text.
        stack = cv2.vconcat(crops)
        # Compare bright glyphs beside dark strokes, not the moving picture.
        # Keep the 10 Hz sampling and force a fresh reading every 0.5 s; any
        # visual change or uncertain reading still invokes the full OCR model.
        gray = cv2.cvtColor(stack, cv2.COLOR_BGR2GRAY)
        mask_started = time.perf_counter()
        white, signature = caption_masks(stack, gray)
        masks_seconds += time.perf_counter() - mask_started
        if boundary_labels is not None and last_box is not None and getattr(reader, 'vietnamese', None) is None:
            _, y1, _, y2 = last_box
            recognition_only_calls += 1
            quick_text, quick_confidence = timed_ocr(read_known_band, reader, stack[y1:y2, :])
            matches = [label for label in boundary_labels if label and text_key(label) == text_key(quick_text)]
            if matches and quick_confidence >= .90:
                result = (matches[0], quick_confidence)
                fresh_frames.add(index)
                frame_results[index] = result
                return result
        reliable = not last_result[0] or last_result[1] >= .85
        if language == 'ch' and last_result[0] and not re.search(r'[\u3400-\u9fff\u3040-\u30ff]', last_result[0]):
            reliable = False
        if last_signature is not None and reliable and 0 < index-last_ocr_frame < max(1, round(fps*.5)):
            if last_box is not None and last_result[0]:
                x1, y1, x2, y2 = last_box
                current = signature[y1:y2, x1:x2]
                previous = last_signature[y1:y2, x1:x2]
            else:
                current, previous = signature, last_signature
            changed, union = compare_masks(current, previous)
            same_glyphs = False
            if last_white_points is not None and last_dark_points is not None:
                # Verify the interiors of known glyphs and their dark strokes.
                # Motion between letters must not invalidate a stable caption.
                same_glyphs = (np.mean(white[last_white_points] != 0) >= .998
                               and np.mean(gray[last_dark_points] < 135) >= .995)
            if changed == 0 or (union >= 30 and changed / union < .005) or same_glyphs:
                reused_frames += 1
                frame_results[index] = last_result
                return last_result
            if last_box is not None and last_result[0] and getattr(reader, 'vietnamese', None) is None:
                # Full width catches appended words and horizontal movement;
                # a mismatch falls back to detection, including other bands.
                _, y1, _, y2 = last_box
                recognition_only_calls += 1
                quick_text, quick_confidence = timed_ocr(read_known_band, reader, stack[y1:y2, :])
                if quick_confidence >= .90 and text_key(quick_text) == text_key(last_result[0]):
                    reused_frames += 1
                    frame_results[index] = last_result
                    return last_result
        ocr_calls += 1
        fresh_frames.add(index)
        scale = min(2., 1280 / width)
        detector_input = cv2.resize(stack, None, fx=scale, fy=scale)
        padding = 0
        if language == 'ch':
            # Thin coloured subtitle strips made the detector miss clear white
            # captions. Grayscale plus vertical context recovers these without
            # an additional model pass (including short names and replies).
            detector_input = cv2.cvtColor(detector_input, cv2.COLOR_BGR2GRAY)
            padding = max(16, round(detector_input.shape[0] * .5))
            detector_input = cv2.copyMakeBorder(detector_input, padding, padding, 0, 0,
                                                cv2.BORDER_CONSTANT, value=127)
        # Weak CJK font recognition must remain provisional until the actual
        # image is checked. API-only translation retains the stricter threshold.
        minimum = .40 if language == 'ch' and settings.translation_engine.lower() == 'gemini_web' else .60
        lines = timed_ocr(read_lines, reader, detector_input, minimum=minimum, caption_region=True)
        lines = [(x1, y1-padding, x2, y2-padding, text, confidence)
                 for x1, y1, x2, y2, text, confidence in lines
                 if y2 > padding and y1 < padding + stack.shape[0] * scale]
        if language == 'ch' and not lines and has_caption_strokes(signature):
            fallback_calls += 1
            recovered = timed_ocr(read_caption_fallback, reader, stack)
            if recovered:
                # Convert original-pixel coordinates into the same coordinate
                # system as the primary detector before selecting a band.
                lines = [(x1*scale, y1*scale, x2*scale, y2*scale, text, confidence)
                         for x1, y1, x2, y2, text, confidence in recovered]
                fallback_frames.add(index)
        offsets, cursor = [], 0
        for crop in crops:
            offsets.append((cursor * scale, (cursor + crop.shape[0]) * scale))
            cursor += crop.shape[0]
        candidates = []
        for a, b in offsets:
            band_lines = [line for line in lines if a <= (line[1] + line[3]) / 2 < b]
            # An English logo can overlap Chinese captions (e.g. "ZAR FILMS").
            # Prefer the matching script when both occur in the same band;
            # retain Latin-only bands for videos that actually display them.
            if language == 'ch':
                cjk_lines = [line for line in band_lines if re.search(r'[\u3400-\u9fff\u3040-\u30ff]', line[4])]
                if cjk_lines:
                    band_lines = cjk_lines
                else:
                    # In a CJK subtitle band, texture can look like Latin
                    # letters when the caption is absent. A genuine Latin
                    # caption still needs strong independent OCR evidence.
                    band_lines = [line for line in band_lines
                                  if line[5] >= .95 and sum(c.isalpha() for c in line[4]) >= 2]
            if not band_lines:
                continue
            text = ' '.join(line[4] for line in band_lines)
            confidence = sum(line[5] for line in band_lines) / len(band_lines)
            center = sum((line[0] + line[2]) / (2 * width * scale) for line in band_lines) / len(band_lines)
            box = (max(0, int(min(line[0] for line in band_lines)/scale)-5),
                   max(0, int(min(line[1] for line in band_lines)/scale)-5),
                   min(stack.shape[1], int(max(line[2] for line in band_lines)/scale)+5),
                   min(stack.shape[0], int(max(line[3] for line in band_lines)/scale)+5))
            candidates.append((confidence + min(len(text_key(text)), 20) * .005 - abs(center-.5)*.1, text, confidence, box))
        if not candidates:
            result = ('', 0.)
            last_box = None
        else:
            _, text, confidence, last_box = max(candidates, key=lambda row: row[0])
            result = (text, confidence)
        frame_results[index] = result
        last_signature, last_result, last_ocr_frame = signature, result, index
        last_white_points = last_dark_points = None
        if result[0] and last_box is not None:
            x1, y1, x2, y2 = last_box
            glyphs = np.zeros_like(signature)
            glyphs[y1:y2, x1:x2] = signature[y1:y2, x1:x2]
            interiors = cv2.erode(glyphs, np.ones((2, 2), np.uint8))
            strokes = (cv2.dilate(glyphs, np.ones((3, 3), np.uint8)) != 0) & (gray < 80)
            if np.count_nonzero(interiors) >= 30 and np.count_nonzero(strokes) >= 30:
                last_white_points = np.where(interiors != 0)
                last_dark_points = np.where(strokes)
        return result

    samples = []
    timestamps = []

    def remember_timestamp(index):
        measured = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
        if index and (measured <= timestamps[-1]):
            measured = max(index / fps, timestamps[-1] + 1 / fps)
        timestamps.append(measured)

    try:
        for index in range(count):
            if index % step and index != count - 1:
                if not capture.grab():
                    break
                remember_timestamp(index)
                continue
            ok, frame = capture.read()
            if not ok:
                break
            remember_timestamp(index)
            frame_cache.remember(index, frame)
            text, confidence = read_text(frame, index)
            if index % (step * 50) == 0:
                print(f"[Visible subtitles] Scanning {index / fps:.1f}/{count / fps:.1f}s", flush=True)
                report(f'Đọc chữ gốc: đã quét {(index + 1) / fps:.1f}/{count / fps:.1f} giây video', index + 1, count)
            samples.append([index, text, confidence])
        if not samples:
            return None
        count = len(timestamps)
        timestamps.append(timestamps[-1] + 1 / fps)
        groups = []
        for sample in samples:
            if groups and _same_text(groups[-1][-1][1], sample[1]):
                groups[-1].append(sample)
            else:
                groups.append([sample])
        # Reused labels are not independent evidence. Fully recheck an
        # unstable group at the original 10 Hz rate; keep stable groups fast.
        for group_index, group in enumerate(groups):
            report(f'Kiểm tra chữ nhận diện: {group_index}/{len(groups)} đoạn', group_index, len(groups))
            if len({text_key(row[1]) for row in group if row[1]}) < 2:
                continue
            for row in group:
                index = row[0]
                if index in fresh_frames:
                    continue
                ok, frame = frame_cache.read_at(index)
                if not ok:
                    continue
                frame_results.pop(index, None)
                last_ocr_frame = -count
                text, confidence = read_text(frame, index)
                if _same_text(row[1], text):
                    row[1], row[2] = text, confidence
        observations = [[row for row in group if row[0] in fresh_frames] or group for group in groups]
        representatives = []
        for group in observations:
            votes = Counter(row[1] for row in group)
            representatives.append(max(votes, key=lambda text: (votes[text], max(row[2] for row in group if row[1] == text))))
        boundaries = [0]
        endings = []
        for i in range(1, len(groups)):
            report(f'Căn thời gian phụ đề: {i - 1}/{len(groups) - 1} ranh giới', i - 1, len(groups) - 1)
            # Reused labels may conceal a small visual edit until the next
            # mandatory reading. Refine from the last actual OCR observation,
            # not the last reused label, to retain frame-level boundaries.
            before = next((row[0] for row in reversed(groups[i-1]) if row[0] in fresh_frames), groups[i-1][0][0])
            after = groups[i][0][0]
            old, new = representatives[i-1], representatives[i]
            boundary = after
            last_old_frame = before
            found_new = False
            for index in range(before + 1, after + 1):
                ok, frame = frame_cache.read_at(index)
                if not ok:
                    break
                # Boundary frames require a real reading unless that exact
                # decoded frame was already read during the first pass.
                last_ocr_frame = -count
                if index not in fresh_frames:
                    frame_results.pop(index, None)
                text, _ = read_text(frame, index, boundary_labels=(old, new))
                if _same_text(text, old):
                    last_old_frame = index
                if not found_new and _same_text(text, new) and not _same_text(text, old):
                    boundary = index
                    found_new = True
            boundaries.append(boundary)
            endings.append(min(boundary, last_old_frame + 1))
        endings.append(count)
        boundaries.append(count)
        events = [SubtitleEvent(timestamps[boundaries[i]], timestamps[endings[i]], text)
                  for i, text in enumerate(representatives)
                  if text and timestamps[endings[i]] - timestamps[boundaries[i]] >= minimum_duration]
        if not events:
            return None
        report = {
            'version': ALGORITHM_VERSION, 'engine': 'RapidOCR / PP-OCRv5 + VietOCR' if getattr(reader, 'vietnamese', None) is not None else 'RapidOCR / PP-OCRv5', 'language_model': language,
            'regions': [{'top': top / height, 'bottom': bottom / height} for top, bottom in regions],
            'frame_rate': fps, 'sample_rate': fps / step,
            'performance': {'ocr_calls': ocr_calls, 'reused_frames': reused_frames,
                            'fallback_calls': fallback_calls,
                            'recognition_only_calls': recognition_only_calls,
                            'scan_seconds': round(time.perf_counter() - started, 3),
                            'frame_processing_engine': 'cpp' if native_library() is not None else 'python',
                            'mask_seconds': round(masks_seconds, 3),
                            'ocr_seconds': round(ocr_seconds, 3),
                            'refinement_decode_seconds': round(frame_cache.decode_seconds, 3),
                            'frame_cache_hits': frame_cache.hits,
                            'refinement_frame_reads': frame_cache.reads,
                            'refinement_seeks': frame_cache.seeks,
                            'frame_cache_budget_bytes': frame_cache.budget},
            'cues': [
                {'start': timestamps[boundaries[i]], 'end': timestamps[endings[i]], 'text': text,
                 'confidence': round(sum(row[2] for row in observations[i]) / len(observations[i]), 4),
                 'needs_review': any(row[0] in fallback_frames for row in groups[i])
                    or sum(row[2] for row in observations[i]) / len(observations[i]) < .85
                    or Counter(text_key(row[1]) for row in observations[i]).most_common(1)[0][1] / len(observations[i]) < .65}
                for i, text in enumerate(representatives)
                if text and timestamps[endings[i]] - timestamps[boundaries[i]] >= minimum_duration
            ],
        }
        report_path = output.parent / 'subtitles.recognition.json'
        pending_report = report_path.with_suffix('.pending.json')
        pending_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        pending_report.replace(report_path)
        temp = output.with_suffix('.pending.srt')
        write_srt(events, temp)
        temp.replace(output)
        return output
    finally:
        capture.release()


async def _scan_with_progress(source: Path, output: Path, on_progress, on_measurement=None) -> Path | None:
    updates = SimpleQueue()
    cancelled = Event()
    worker = asyncio.create_task(asyncio.to_thread(_extract_visible, source, output, updates.put, cancelled))
    stage, completed, total = 'Đang chuẩn bị nhận diện chữ trên video', 0, 0
    try:
        while True:
            while True:
                try:
                    stage, completed, total = updates.get_nowait()
                except Empty:
                    break
            if on_progress is not None:
                on_progress(stage)
            if on_measurement is not None:
                on_measurement(completed, total)
            if worker.done():
                return await worker
            await asyncio.wait({worker}, timeout=2)
    finally:
        cancelled.set()
        if not worker.done():
            worker.cancel()


async def visible_source_subtitles(work_dir: Path, on_progress: Callable[[str], None] | None = None, on_measurement: Callable[[float, float], None] | None = None) -> Path | None:
    output = work_dir / 'subtitles.visible.srt'
    source = work_dir / 'source.mp4'
    if not source.is_file():
        return None
    stamp = source.stat()
    fingerprint = {'size': stamp.st_size, 'mtime_ns': stamp.st_mtime_ns,
                   'version': ALGORITHM_VERSION, 'language': recognition_language(work_dir),
                   'uncertain_caption_review': settings.translation_engine.lower() == 'gemini_web',
                   'engines': [asr_cache.package_version('rapidocr'), asr_cache.package_version('vietocr')]}
    reference = await asyncio.to_thread(_caption_reference, work_dir)
    fingerprint['reference'] = await asyncio.to_thread(asr_cache.digest, reference) if reference else ''
    marker = work_dir / 'subtitles.visible-scan.json'

    def cached():
        try:
            saved = json.loads(marker.read_text(encoding='utf-8'))
            if saved.get('source') != fingerprint:
                return False, None
            if saved.get('has_captions') is False:
                return True, None
            if output.is_file() and output.stat().st_size:
                return True, output
        except (OSError, ValueError):
            pass
        return False, None

    valid, result = cached()
    if valid:
        return result
    async with _locks.setdefault(str(work_dir), asyncio.Lock()):
        valid, result = cached()
        if valid:
            return result
        # Cross-job reuse is based on full video content, language and ASR
        # reference, not a filename that might refer to another movie.
        def directory():
            video_digest = asr_cache.digest(source)
            key = hashlib.sha256(json.dumps({
                'video': video_digest, 'version': ALGORITHM_VERSION,
                'language': fingerprint['language'], 'reference': fingerprint['reference'],
                'uncertain_caption_review': fingerprint['uncertain_caption_review'],
                'engines': fingerprint['engines'],
            }, sort_keys=True).encode()).hexdigest()
            return Path(settings.storage_dir) / 'recognition-cache' / 'visible' / key, video_digest

        shared, video_digest = await asyncio.to_thread(directory)
        if output.is_file() and (work_dir / 'subtitles.vision-review.json').is_file():
            from app.services.subtitles.caption_cache import preserve_legacy_decisions
            await asyncio.to_thread(preserve_legacy_decisions, source, output, work_dir, video_digest)
        artifacts = {'visible.srt': output, 'recognition.json': work_dir / 'subtitles.recognition.json'}
        async with asr_cache.lock(shared):
            if await asyncio.to_thread(asr_cache.restore_artifacts, shared, artifacts):
                result = output
                print('[Visible subtitles] Reusing verified video captions.', flush=True)
            else:
                result = await _scan_with_progress(source, output, on_progress, on_measurement)
                if result:
                    await asyncio.to_thread(asr_cache.save_artifacts, shared, artifacts)
        temporary = marker.with_suffix('.pending.json')
        temporary.write_text(json.dumps({'source': fingerprint, 'has_captions': bool(result)}), encoding='utf-8')
        temporary.replace(marker)
        return result
