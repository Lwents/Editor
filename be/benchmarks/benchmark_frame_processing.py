"""Compare native/reference masks and OCR output on a short original clip."""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import time
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from app.core import settings
from app.services.media.ffmpeg import find_ffmpeg
from app.services.subtitles import frame_processing as frames
from app.services.subtitles.visible import _extract_visible

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('video', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--start', type=float, default=0)
    parser.add_argument('--seconds', type=float, default=12)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(args.video))
    samples = []
    for i in range(30):
        cap.set(cv2.CAP_PROP_POS_MSEC, (args.start + i * args.seconds / 30) * 1000)
        ok, image = cap.read()
        if ok:
            band = image[int(image.shape[0] * .7):]
            samples.append((band, cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)))
    cap.release()
    if not samples:
        raise RuntimeError('No video frames')
    results = {'scan_order': ['python', 'cpp'],
               'scan_note': 'Python scan runs first and includes cold OCR setup; do not attribute the full scan difference solely to native kernels.'}
    masks = {}
    for engine in ('python', 'cpp'):
        os.environ['EDITOR_NATIVE_FRAMES'] = '1' if engine == 'cpp' else '0'
        frames.native_library.cache_clear()
        if engine == 'cpp' and frames.native_library() is None:
            raise RuntimeError('Native library missing')
        elapsed = []
        for _ in range(5):
            before = time.perf_counter()
            masks[engine] = [frames.caption_masks(*sample) for sample in samples]
            elapsed.append(time.perf_counter() - before)
        results[engine] = {'masks_median_seconds': statistics.median(elapsed)}
    for a, b in zip(masks['python'], masks['cpp']):
        for left, right in zip(a, b):
            np.testing.assert_array_equal(left, right)
    results['mask_speedup'] = results['python']['masks_median_seconds'] / results['cpp']['masks_median_seconds']
    results['mask_frames_equal'] = len(samples)
    clip = args.output / 'clip.mp4'
    subprocess.run([find_ffmpeg(), '-y', '-ss', str(args.start), '-i', str(args.video),
                    '-t', str(args.seconds), '-an', '-c:v', 'libx264', '-preset', 'ultrafast',
                    '-crf', '18', str(clip)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    settings.translation_engine = 'gemini_web'
    for engine in ('python', 'cpp'):
        os.environ['EDITOR_NATIVE_FRAMES'] = '1' if engine == 'cpp' else '0'
        frames.native_library.cache_clear()
        work = args.output / engine
        work.mkdir(exist_ok=True)
        target = work / 'visible.srt'
        before = time.perf_counter()
        _extract_visible(clip, target)
        results[engine]['scan_seconds'] = time.perf_counter() - before
        report = json.loads((work / 'subtitles.recognition.json').read_text(encoding='utf-8'))
        results[engine]['performance'] = report['performance']
    results['srt_identical'] = (args.output / 'python/visible.srt').read_bytes() == (args.output / 'cpp/visible.srt').read_bytes()
    assert results['srt_identical'], 'Native OCR changed subtitle text or timing'
    (args.output / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps(results, indent=2), flush=True)

if __name__ == '__main__':
    main()
