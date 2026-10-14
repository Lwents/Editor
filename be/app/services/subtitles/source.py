import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from app.core import settings
from app.models.job import ProcessingMode
from app.services.ai.context import analyze_video_context
from app.services.ai.asr import get_asr_engine
from app.services.ai.translation import get_translation_engine, TRANSLATION_POLICY_VERSION
from app.services.media.ffmpeg import find_ffmpeg
from app.services.subtitles.ass import write_srt
from app.services.subtitles.timing import group_subtitle_events, parse_srt, split_long_subtitle_events
from app.services.subtitles.visible import ALGORITHM_VERSION, visible_source_subtitles
from app.services.subtitles.performance import measure_stage
from app.services.subtitles.mode import text_only_gemini


class SubtitleSourceError(RuntimeError):
    pass


def _quality_signature(source_video: Path | None) -> dict | None:
    if source_video and source_video.is_file():
        stat = source_video.stat()
        return {'path': str(source_video.resolve()), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
    return None


def _validated_target(target: Path, work_dir: Path, source_video: Path | None, processing_mode=None) -> bool:
    from app.services.ai.asr_cache import digest
    try:
        marker = json.loads((work_dir / 'translation_quality.json').read_text(encoding='utf-8'))
        from app.services.presets import get_processing_profile
        mode = get_processing_profile(processing_mode).name
        if marker.get('processing_mode', 'balanced') != mode:
            return False
        if marker.get('text_only_gemini', False) != text_only_gemini.get():
            return False
        if (source_video is not None and (work_dir / 'subtitles.recognition.json').is_file()
                and marker.get('recognition_version') != ALGORITHM_VERSION):
            return False
        if (work_dir / 'subtitles.vision-review.json').is_file():
            from app.services.subtitles.vision_review import VERSION
            if marker.get('vision_review_version') != VERSION:
                return False
        return (marker.get('policy') == TRANSLATION_POLICY_VERSION
                and (marker.get('review_completed') is True or mode == 'fast')
                and marker.get('engine') == settings.translation_engine
                and marker.get('model') == ('Gemini Web' if settings.translation_engine == 'gemini_web' else settings.ai_model)
                and marker.get('video') == _quality_signature(source_video)
                and marker.get('sha256') == digest(target)
                and marker.get('source_sha256') == digest(work_dir / 'subtitles.grouped.srt'))
    except (OSError, ValueError, AttributeError, TypeError):
        return False


def _merge_context(context: dict, visual: dict) -> dict:
    """Keep the whole-clip bible when an OCR review contributes local clues."""
    merged = dict(context)
    for key, value in visual.items():
        previous = merged.get(key)
        if isinstance(previous, list) and isinstance(value, list):
            merged[key] = previous + [item for item in value if item not in previous]
        elif isinstance(previous, dict) and isinstance(value, dict):
            merged[key] = {**previous, **value}
        elif not previous:
            merged[key] = value
    return merged


def translation_progress_percent(
    completed: int,
    total: int,
    start_percent: int,
    end_percent: int,
) -> int:
    ratio = completed / max(total, 1)
    return start_percent + int(
        max(0.0, min(1.0, ratio)) * max(0, end_percent - start_percent)
    )


def _platform_name(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "douyin.com" in host:
        return "Douyin"
    if "tiktok.com" in host:
        return "TikTok"
    if "youtube.com" in host or "youtu.be" in host:
        return "YouTube"
    return "nền tảng"


def _find_js_runtime() -> str | None:
    if settings.ytdlp_js_runtime:
        return settings.ytdlp_js_runtime

    bundled_node = Path(__file__).resolve().parents[4] / "tools" / "node" / "node.exe"
    if bundled_node.exists():
        return f"node:{bundled_node.as_posix()}"

    node = shutil.which("node")
    if node:
        return f"node:{Path(node).as_posix()}"

    deno = shutil.which("deno")
    if deno:
        return f"deno:{Path(deno).as_posix()}"

    return None


def _without_browser_cookies(command: list[str]) -> list[str]:
    cleaned: list[str] = []
    skip_next = False
    for item in command:
        if skip_next:
            skip_next = False
            continue
        if item == "--cookies-from-browser":
            skip_next = True
            continue
        cleaned.append(item)
    return cleaned


def _run_ytdlp(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)
    detail = completed.stderr.strip() or completed.stdout.strip()
    if completed.returncode != 0 and "--cookies-from-browser" in command and "could not copy chrome cookie database" in detail.lower():
        return subprocess.run(_without_browser_cookies(command), capture_output=True, text=True, check=False, timeout=timeout)
    return completed


async def _run_with_heartbeat(
    task,
    progress: Callable[[str, int], None],
    stage: str,
    start_percent: int,
    end_percent: int,
    timeout_seconds: int,
):
    running_task = asyncio.create_task(task)
    elapsed = 0
    try:
        while not running_task.done():
            percent = start_percent
            progress(stage, percent)
            await asyncio.sleep(5)
            elapsed += 1
            if elapsed * 5 >= timeout_seconds:
                running_task.cancel()
                raise SubtitleSourceError(
                    f"{stage} quá {timeout_seconds} giây. Kiểm tra model Whisper hoặc giảm độ dài video."
                )
        return await running_task
    except asyncio.CancelledError:
        running_task.cancel()
        raise


async def get_or_create_subtitles(
    source_url: str | None,
    audio_file: Path,
    work_dir: Path,
    source_language: str,
    progress: Callable[[str, int], None],
    source_video: Path | None = None,
    asr_timeout_seconds: int | None = None,
    processing_mode: ProcessingMode | str | None = None,
    translation_progress_start: int = 68,
    translation_progress_end: int = 69,
    on_measurement: Callable[[float, float], None] | None = None,
) -> Path:
    target_file = work_dir / f"subtitles.{settings.target_language}.srt"
    if target_file.exists() and target_file.stat().st_size > 0 and (
        settings.translation_engine.lower() == 'passthrough'
        or await asyncio.to_thread(_validated_target, target_file, work_dir, source_video, processing_mode)
    ):
        progress("Dùng lại phụ đề đã dịch xong", translation_progress_end)
        return target_file

    if source_url and settings.prefer_youtube_subtitles:
        try:
            subtitle_file = await download_platform_subtitles(source_url, work_dir, progress, source_language)
            return await _prepare_target_subtitles(
                subtitle_file,
                work_dir,
                source_language,
                progress,
                source_video,
                processing_mode,
                translation_progress_start,
                translation_progress_end,
                on_measurement=on_measurement,
            )
        except SubtitleSourceError:
            progress("Nền tảng không có phụ đề phù hợp, tự tạo phụ đề bằng Whisper", 58)

    whisper_file = work_dir / "subtitles.whisper.srt"
    if whisper_file.exists() and whisper_file.stat().st_size > 0:
        progress("Dùng lại phụ đề Whisper đã nhận diện xong", 66)
        return await _prepare_target_subtitles(
            whisper_file,
            work_dir,
            source_language,
            progress,
            source_video,
            processing_mode,
            translation_progress_start,
            translation_progress_end,
            on_measurement=on_measurement,
        )

    asr_task = get_asr_engine().transcribe_to_srt(
        audio_file,
        whisper_file,
        source_language,
        processing_mode,
    )
    subtitle_file = await _run_with_heartbeat(
        asr_task,
        progress,
        "Đang nghe audio và tạo phụ đề tự động",
        62,
        70,
        asr_timeout_seconds or settings.asr_timeout_seconds,
    )
    return await _prepare_target_subtitles(
        subtitle_file,
        work_dir,
        source_language,
        progress,
        source_video,
        processing_mode,
        translation_progress_start,
        translation_progress_end,
        on_measurement=on_measurement,
    )


def asr_timeout_for_duration(video_duration_seconds: float) -> int:
    if video_duration_seconds <= 0:
        return max(settings.asr_timeout_seconds, 1800)
    duration_based_timeout = int(video_duration_seconds * 2.5)
    return min(max(settings.asr_timeout_seconds, duration_based_timeout, 1800), 6 * 60 * 60)


async def _prepare_target_subtitles(
    subtitle_file: Path,
    work_dir: Path,
    source_language: str,
    progress: Callable[[str, int], None],
    source_video: Path | None = None,
    processing_mode: ProcessingMode | str | None = None,
    translation_progress_start: int = 68,
    translation_progress_end: int = 69,
    on_measurement: Callable[[float, float], None] | None = None,
) -> Path:
    from app.services.ai.gemini_web import browser, GeminiWebVerificationRequired
    verification_required = False
    try:
        with browser.conversation(f'translation:{work_dir.resolve()}:policy:{TRANSLATION_POLICY_VERSION}'):
            return await _prepare_target_subtitles_impl(
                subtitle_file, work_dir, source_language, progress, source_video,
                processing_mode, translation_progress_start, translation_progress_end,
                on_measurement=on_measurement,
            )
    except GeminiWebVerificationRequired:
        verification_required = True
        raise
    finally:
        if settings.translation_engine.lower() == 'gemini_web' and not verification_required:
            from app.services.ai.gemini_web import browser
            try:
                await browser.close()
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Could not close dedicated Gemini browser')


async def _prepare_target_subtitles_impl(
    subtitle_file: Path,
    work_dir: Path,
    source_language: str,
    progress: Callable[[str, int], None],
    source_video: Path | None = None,
    processing_mode: ProcessingMode | str | None = None,
    translation_progress_start: int = 68,
    translation_progress_end: int = 69,
    on_measurement: Callable[[float, float], None] | None = None,
) -> Path:
    grouped_max_chars = max(settings.subtitle_group_max_chars, 42)
    grouped_max_duration = max(settings.subtitle_group_max_duration, 3.2)
    grouped_max_gap = max(settings.subtitle_group_max_gap, 0.45)

    visible_file = None
    if source_video is not None and subtitle_file.name in {"subtitles.whisper.srt", "subtitles.visible.srt"}:
        progress("Đọc chữ gốc và thời gian hiển thị trên video", max(0, translation_progress_start - 2))
        try:
            visible_file = await measure_stage('visible_ocr', visible_source_subtitles(
                work_dir,
                on_progress=lambda stage: progress(stage, max(0, translation_progress_start - 2)),
                on_measurement=on_measurement,
            ), work_dir)
        except (ImportError, RuntimeError, OSError):
            visible_file = None
    source_file = work_dir / "subtitles.source.srt"
    timed_source = source_file if subtitle_file.name == 'subtitles.whisper.srt' and source_file.is_file() else subtitle_file
    raw_events = parse_srt(visible_file or timed_source)
    visual_context = {}
    if visible_file and source_video is not None and settings.translation_engine.lower() == 'gemini_web':
        from app.services.subtitles.vision_review import review_visible_captions
        raw_events, visual_context = await measure_stage('caption_verification', review_visible_captions(source_video, raw_events, work_dir, progress), work_dir)
        write_srt(raw_events, work_dir / ('subtitles.source.provisional.srt' if text_only_gemini.get() else 'subtitles.source.verified.srt'))
    source_file = work_dir / "subtitles.source.srt"
    if not source_file.is_file():
        write_srt(raw_events, source_file)
        if visible_file:
            from app.services.ai.asr_cache import digest
            (work_dir / 'subtitles.source.origin.json').write_text(
                json.dumps({'kind': 'visible_ocr', 'sha256': digest(source_file)}), encoding='utf-8')
    # Keep each displayed caption separate. Grouping and redistributing a long
    # ASR segment invents timings and fills silent, caption-free scenes.
    events = raw_events if visible_file else group_subtitle_events(
        raw_events,
        max_chars=grouped_max_chars,
        max_duration=grouped_max_duration,
        max_gap=grouped_max_gap,
    )
    events = split_long_subtitle_events(
        events,
        max_chars=grouped_max_chars,
        max_duration=grouped_max_duration,
    )
    grouped_file = work_dir / "subtitles.grouped.srt"
    write_srt(events, grouped_file)

    target_language = settings.target_language
    if settings.translation_engine.lower() == "passthrough" or (
        source_language != "auto" and source_language.lower() == target_language.lower()
    ):
        return grouped_file

    progress("Gemini đọc ngữ cảnh cốt truyện, nhân vật và xưng hô" if settings.translation_engine == 'gemini_web' else "AI nhận diện tên phim, nhân vật và vai vế", max(0, translation_progress_start - 1))
    if on_measurement is not None:
        on_measurement(0, 1)
    context = await measure_stage('context', analyze_video_context(None if text_only_gemini.get() else source_video, events, work_dir, verified_context=visual_context), work_dir)
    if on_measurement is not None:
        on_measurement(1, 1)
    context = _merge_context(context, visual_context)

    review_start = translation_progress_start + (translation_progress_end - translation_progress_start)//2

    progress("Dịch phụ đề sang tiếng Việt", translation_progress_start)
    def translation_progress(completed: int, total: int) -> None:
        percent = translation_progress_percent(
            completed,
            total,
            translation_progress_start,
            review_start,
        )
        progress(
            f"Dịch phụ đề sang tiếng Việt ({completed}/{max(total, 1)} batch, đã lưu checkpoint)",
            percent,
        )

        if on_measurement is not None:
            on_measurement(completed, total)

    translated_events = await measure_stage('translation', get_translation_engine().translate_events(
        events,
        source_language,
        target_language,
        context=context,
        processing_mode=processing_mode,
        checkpoint_file=work_dir / "translation_checkpoint.json",
        on_batch_progress=translation_progress,
    ), work_dir)
    from app.services.ai.translation_review import review_translated_events
    def review_progress(completed: int, total: int) -> None:
        percent = translation_progress_percent(completed, total, review_start, translation_progress_end)
        progress(f'Đối chiếu nghĩa, thành ngữ và xưng hô ({completed}/{max(total, 1)} batch)', percent)
        if on_measurement is not None:
            on_measurement(completed, total)

    from app.services.presets import get_processing_profile
    mode = get_processing_profile(processing_mode).name
    if mode != 'fast':
        translated_events = await measure_stage('translation_review', review_translated_events(
            events, translated_events, context, work_dir, source_language,
            target_language, processing_mode, review_progress), work_dir)
    translated_events = split_long_subtitle_events(
        translated_events,
        max_chars=grouped_max_chars,
        max_duration=grouped_max_duration,
    )
    target = write_srt(translated_events, work_dir / f"subtitles.{target_language}.srt")
    from app.services.ai.asr_cache import digest
    pending = work_dir / 'translation_quality.pending.json'
    from app.services.subtitles.vision_review import VERSION as VISION_REVIEW_VERSION
    pending.write_text(json.dumps({'policy': TRANSLATION_POLICY_VERSION,
        'recognition_version': ALGORITHM_VERSION if visible_file else None,
        'vision_review_version': VISION_REVIEW_VERSION if (work_dir / 'subtitles.vision-review.json').is_file() else None,
        'engine': settings.translation_engine,
        'model': 'Gemini Web' if settings.translation_engine == 'gemini_web' else settings.ai_model,
        'video': _quality_signature(source_video), 'sha256': digest(target),
        'source_sha256': digest(grouped_file), 'processing_mode': mode,
        'text_only_gemini': text_only_gemini.get(),
        'review_completed': mode != 'fast'},
        ensure_ascii=False, indent=2), encoding='utf-8')
    pending.replace(work_dir / 'translation_quality.json')
    return target


async def download_platform_subtitles(url: str, work_dir: Path, progress: Callable[[str, int], None], source_language: str = "auto") -> Path:
    progress(f"Tải phụ đề có sẵn từ {_platform_name(url)}", 55)
    languages = [source_language] if source_language in {"en", "zh", "vi"} else ["en", "zh", "vi"]
    output_template = str(work_dir / "subtitles.%(ext)s")
    ffmpeg = find_ffmpeg()
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--force-ipv4",
        "--socket-timeout",
        "25",
        "--retries",
        "5",
        "--skip-download",
        "--remote-components",
        "ejs:github",
        "--write-sub",
        "--write-auto-sub",
        "--sub-langs",
        ",".join(f"{language}.*" for language in languages),
        "--sub-format",
        "srt/vtt/best",
        "--convert-subs",
        "srt",
        "--output",
        output_template,
    ]
    if ffmpeg:
        command.extend(["--ffmpeg-location", str(Path(ffmpeg).parent)])
    js_runtime = _find_js_runtime()
    if js_runtime:
        command.extend(["--js-runtimes", js_runtime])
    if settings.ytdlp_cookies_file:
        command.extend(["--cookies", settings.ytdlp_cookies_file])
    if settings.ytdlp_cookies_from_browser:
        command.extend(["--cookies-from-browser", settings.ytdlp_cookies_from_browser])
    command.append(url)

    def download() -> Path:
        completed = _run_ytdlp(command, timeout=90)
        matches = [p for p in work_dir.glob("subtitles*.srt") if p.stat().st_size > 0 and p.name not in {"subtitles.vi.srt", "subtitles.whisper.srt", "subtitles.grouped.srt"}]
        # Vietnamese is a source track only when explicitly requested; never
        # feed a platform's machine-translated Vietnamese track to English ASR.
        if source_language == "vi":
            matches.extend(p for p in work_dir.glob("subtitles.vi.srt") if p.stat().st_size > 0)
        preferred_languages = languages
        if source_language == "auto":
            preferred_languages = sorted(languages, key=lambda language: not any(
                p.name.startswith(f"subtitles.{language}") and p.stem.endswith("-orig")
                for p in matches
            ))
        for language in preferred_languages:
            candidates = [p for p in matches if p.name.startswith(f"subtitles.{language}.") or p.name.startswith(f"subtitles.{language}-")]
            if candidates:
                # Prefer a supplied locale track over automatic recognition;
                # the original-language marker still guides auto detection.
                return sorted(candidates, key=lambda p: (p.stem.endswith("-orig"), p.name))[0]
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise SubtitleSourceError(f"yt-dlp không tải được phụ đề. {detail}")
        raise SubtitleSourceError("Video này không có phụ đề phù hợp để ghi vào video.")

    try:
        return await asyncio.to_thread(download)
    except subprocess.TimeoutExpired as exc:
        raise SubtitleSourceError("yt-dlp quá thời gian chờ khi tải phụ đề.") from exc
