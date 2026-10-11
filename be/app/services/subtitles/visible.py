"""Read hard subtitles and their display intervals directly from video frames."""
from __future__ import annotations

import asyncio
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
import re
import json

from app.services.subtitles.ass import write_srt
from app.services.subtitles.timing import SubtitleEvent

_locks: dict[str, asyncio.Lock] = {}


def _same_text(a: str, b: str) -> bool:
    if not a or not b:
        return a == b
    return SequenceMatcher(None, a, b).ratio() >= 0.85


def _extract_visible(source: Path, output: Path) -> Path | None:
    import cv2
    from rapidocr_onnxruntime import RapidOCR

    reader = RapidOCR(det_model_path=None, det_limit_type="max", det_limit_side_len=960, use_angle_cls=False)
    capture = cv2.VideoCapture(str(source))
    fps = capture.get(cv2.CAP_PROP_FPS)
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if fps <= 0 or count <= 0:
        capture.release()
        return None
    step = max(1, round(fps / 5))

    def read_text(frame):
        height, width = frame.shape[:2]
        crop = frame[int(height * .82):int(height * .94), int(width * .05):int(width * .95)]
        found, _ = reader(cv2.resize(crop, None, fx=2, fy=2))
        lines = []
        for box, text, confidence in found or []:
            # Ignore scene details and Latin watermarks in this subtitle strip.
            if float(confidence) >= .72 and len(re.findall(r"[\u3400-\u9fff]", text)) >= 2:
                lines.append((min(point[1] for point in box), text.strip(), float(confidence)))
        lines.sort()
        return " ".join(line[1] for line in lines), min((line[2] for line in lines), default=0)

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
            text, confidence = read_text(frame)
            if index % (step * 50) == 0:
                print(f"[Visible subtitles] Scanning {index / fps:.1f}/{count / fps:.1f}s", flush=True)
            samples.append([index, text, confidence])
        if not samples:
            return None
        count = len(timestamps)
        timestamps.append(timestamps[-1] + 1 / fps)
        # A single failed OCR frame must not split a continuously displayed line.
        for i in range(1, len(samples) - 1):
            if not samples[i][1] and samples[i-1][1] and _same_text(samples[i-1][1], samples[i+1][1]):
                samples[i][1:] = samples[i-1][1:]
        groups = []
        for sample in samples:
            if groups and _same_text(groups[-1][-1][1], sample[1]):
                groups[-1].append(sample)
            else:
                groups.append([sample])
        representatives = []
        for group in groups:
            votes = Counter(row[1] for row in group)
            representatives.append(max(votes, key=lambda text: (votes[text], max(row[2] for row in group if row[1] == text))))
        boundaries = [0]
        endings = []
        for i in range(1, len(groups)):
            before, after = groups[i-1][-1][0], groups[i][0][0]
            old, new = representatives[i-1], representatives[i]
            boundary = after
            last_old_frame = before
            found_new = False
            capture.set(cv2.CAP_PROP_POS_FRAMES, before + 1)
            for index in range(before + 1, after + 1):
                ok, frame = capture.read()
                if not ok:
                    break
                text, _ = read_text(frame)
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
                  if text and timestamps[endings[i]] - timestamps[boundaries[i]] >= .08]
        if not events:
            return None
        temp = output.with_suffix('.pending.srt')
        write_srt(events, temp)
        temp.replace(output)
        return output
    finally:
        capture.release()


async def visible_source_subtitles(work_dir: Path) -> Path | None:
    output = work_dir / 'subtitles.visible.srt'
    if output.is_file() and output.stat().st_size:
        return output
    source = work_dir / 'source.mp4'
    if not source.is_file():
        return None
    stamp = source.stat()
    fingerprint = {"size": stamp.st_size, "mtime_ns": stamp.st_mtime_ns}
    marker = work_dir / 'subtitles.visible-scan.json'

    def scanned_without_captions():
        try:
            saved = json.loads(marker.read_text(encoding='utf-8'))
            return saved.get('source') == fingerprint and saved.get('has_captions') is False
        except (OSError, ValueError):
            return False

    if scanned_without_captions():
        return None
    async with _locks.setdefault(str(work_dir), asyncio.Lock()):
        if output.is_file() and output.stat().st_size:
            return output
        if scanned_without_captions():
            return None
        result = await asyncio.to_thread(_extract_visible, source, output)
        temporary = marker.with_suffix('.pending.json')
        temporary.write_text(json.dumps({"source": fingerprint, "has_captions": bool(result)}), encoding='utf-8')
        temporary.replace(marker)
        return result
