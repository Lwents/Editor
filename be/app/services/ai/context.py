from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from pathlib import Path

from openai import AsyncOpenAI

from app.core import settings
from app.services.subtitles.timing import SubtitleEvent

CONTEXT_VERSION = 4

_CONTEXT_PROMPT = (
    "Lập hồ sơ ngữ cảnh để dịch phụ đề phim từ transcript nguồn và ảnh theo thứ tự thời gian. "
    "Đọc cả phần đầu, giữa và cuối trước khi kết luận; đây không phải yêu cầu viết lại cốt truyện.\n"
    "Chỉ ghi điều có bằng chứng trong nguồn. Không dùng kiến thức về phim để bịa tình tiết còn thiếu. "
    "Chữ Việt có sẵn trong ảnh có thể dịch sai; ưu tiên lời nguồn, chữ gốc và hành động nhìn thấy.\n"
    "- plot_summary: tóm tắt ngắn các sự kiện ĐÃ CÓ trong clip, giữ đúng ai làm gì, mục đích và nguyên nhân.\n"
    "- characters: [{name, aliases, role, evidence}]. Ưu tiên tên đầy đủ có trong nguồn làm name, "
    "không lấy một cách gọi thân mật thay cho tên đầy đủ. Chọn tên Việt/Hán Việt nhất quán; chữ gốc và cách gọi "
    "đặt trong aliases. Không dịch nghĩa tên riêng, không gộp hai người khi chưa có bằng chứng. "
    "Nếu chưa xác định được tên dùng mô tả trung tính, không tự đặt tên.\n"
    "- Tên được GỌI trong lời thoại thường là người nghe hoặc người được nhắc tới, không tự gán cho người nói. "
    "Không gán mọi hành động/độc thoại sau đó cho nhân vật vừa được gọi tên. Đối chiếu người nói/người nghe "
    "với ảnh từng cảnh; nếu chưa rõ ghi chưa rõ, không khóa một danh tính suy đoán. "
    "Theo dõi ai trong hồi ức xuất hiện lại ở hiện tại; chỉ gộp cách gọi khi đủ bằng chứng cùng người.\n"
    "- relationships: chỉ ghi quan hệ được lời nguồn xác nhận; ở cùng giường hoặc chăm sóc không chứng minh vợ chồng.\n"
    "- honorific_rules: xưng hô theo người nói/người nghe và bối cảnh; với cổ trang thường ta/ngươi, "
    "vương gia/thuộc hạ, mẫu phi/con. Không trộn tao/mày vào thoại cung đình nếu nguồn không cần sắc thái đó.\n"
    "- scene_notes: phân biệt hồi ức/hiện tại, độc thoại nội tâm/lời nói ngoài miệng khi có bằng chứng; "
    "ghi ID câu nguồn làm bằng chứng. Nội tâm bực bội và lời đáp cung kính có thể cùng đúng.\n"
    "- glossary: giải nghĩa thành ngữ, ẩn dụ và thuật ngữ khó trong NGỮ CẢNH; giữ hình ảnh quan trọng "
    "như bị dùng làm bàn đạp, không biến ẩn dụ thành một sự kiện literal.\n"
    "- translation_notes: cảnh báo chỗ ASR/OCR có thể sai và những điều CHƯA rõ; không ép sửa thành phỏng đoán.\n"
    "film_title để null nếu không chắc. Với Doraemon dùng Chaien và Suneo.\n"
    "Viết NGẮN: plot_summary tối đa 600 ký tự; tối đa 12 nhân vật, 6 scene_notes, 8 translation_notes. "
    "Không lặp lại toàn bộ transcript trong hồ sơ. Tự kiểm tra hồ sơ không nhầm người được gọi với người đang nói.\n"
    "Trả hồ sơ gồm film_title, genre, setting, plot_summary, characters, relationships, glossary, "
    "honorific_rules, scene_notes, translation_notes, confidence."
)


async def analyze_video_context(
    video_file: Path | None,
    events: list[SubtitleEvent],
    work_dir: Path,
    *,
    verified_context: dict | None = None,
) -> dict:
    use_browser = settings.translation_engine.lower() == 'gemini_web'
    if not events or (not use_browser and not settings.ninerouter_api_key):
        return {}

    video_hash = None
    if video_file and video_file.is_file():
        from app.services.ai.asr_cache import digest
        video_hash = await asyncio.to_thread(digest, video_file)
    fingerprint = hashlib.sha256(json.dumps({
        'version': CONTEXT_VERSION, 'video': video_hash,
        'events': [(e.start, e.end, e.text) for e in events],
        'verified_context': verified_context or {},
        'engine': settings.translation_engine,
        'model': 'Gemini Web' if use_browser else settings.ai_model,
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    context_file = work_dir / "ai_context.json"
    if context_file.is_file() and context_file.stat().st_size > 0:
        try:
            cached = json.loads(context_file.read_text(encoding="utf-8"))
            if (isinstance(cached, dict) and isinstance(cached.get('_context_cache'), dict)
                    and cached['_context_cache'].get('fingerprint') == fingerprint):
                return _sanitize_context(cached)
        except (OSError, json.JSONDecodeError):
            pass

    transcript = _compact_transcript(events)
    if use_browser:
        from app.services.ai.gemini_web import browser
        sheet = work_dir / 'context-frames.jpg'
        attached = await asyncio.to_thread(_context_frame_sheet, video_file, sheet)
        payload = {'current_items': [{'id': 1, 'source': 'Tóm tắt ngữ cảnh có bằng chứng của clip này.'}],
                   'source_transcript': transcript, 'verified_caption_hints': verified_context or {}}
        # Keep the browser's request nonce and item validation contract; the
        # structured character/plot bible travels in memory_updates.
        raw = await browser.complete([
            {'role': 'system', 'content': _CONTEXT_PROMPT + '\nĐặt hồ sơ trong memory_updates. '
             'items chỉ gồm id=1, text=tóm tắt ngắn bằng tiếng Việt. Không dịch từng câu ở bước này.'},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
        ], timeout=180, attachments=[sheet] if attached else None)
        data = _loads_json_object(raw)
        if not isinstance(data, dict) or not isinstance(data.get('memory_updates'), dict):
            raise RuntimeError('Gemini chưa trả hồ sơ ngữ cảnh hợp lệ. Phụ đề nguồn đã được giữ để chạy tiếp.')
        context = _sanitize_context(data['memory_updates'], transcript)
        if not isinstance(context.get('plot_summary'), str) or not context['plot_summary'].strip():
            items = data.get('items', [])
            if (isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict)
                    and items[0].get('id') == 1 and isinstance(items[0].get('text'), str)):
                context['plot_summary'] = items[0]['text'].strip()
        if not isinstance(context.get('plot_summary'), str) or not context['plot_summary'].strip():
            raise RuntimeError('Gemini trả thiếu ngữ cảnh cốt truyện. Phụ đề nguồn đã được giữ để chạy tiếp.')
        _save_context(context_file, context, fingerprint)
        return context

    frames = await asyncio.to_thread(_sample_video_frames, video_file) if video_file else []

    client = AsyncOpenAI(
        api_key=settings.ninerouter_api_key,
        base_url=settings.ninerouter_api_url,
    )
    prompt = _CONTEXT_PROMPT + '\nTrả đúng JSON object hồ sơ, không Markdown.'

    content: list[dict] = [
        {"type": "text", "text": prompt},
        {"type": "text", "text": f"Transcript ASR thô (mẫu xuyên suốt video):\n{transcript[:24000]}"},
        {"type": "text", "text": 'Chữ/tên đã kiểm tra từ ảnh phụ đề: ' + json.dumps(verified_context or {}, ensure_ascii=False)},
    ]
    for image_b64 in frames:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
            }
        )

    try:
        response = await _create_context_completion_with_retries(client, content)
        raw = response.choices[0].message.content or "{}"
        context = _loads_json_object(raw)
    except Exception as exc:
        print(f"AI context fallback: {exc}")
        return {}

    context = _sanitize_context(context, transcript)
    if context:
        _save_context(context_file, context, fingerprint)
    return context


def _save_context(path: Path, context: dict, fingerprint: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix('.pending.json')
    pending.write_text(json.dumps({**context, '_context_cache': {'version': CONTEXT_VERSION,
                       'fingerprint': fingerprint}}, ensure_ascii=False, indent=2), encoding='utf-8')
    pending.replace(path)


def _context_frame_sheet(video_file: Path | None, target: Path) -> bool:
    if not video_file or not video_file.is_file():
        return False
    import cv2
    from PIL import Image, ImageDraw
    cap = cv2.VideoCapture(str(video_file))
    tiles = []
    try:
        count, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS)
        if count <= 0 or fps <= 0:
            return False
        for ratio in (.015, .08, .18, .34, .5, .65, .82, .97):
            index = min(count - 1, round(count * ratio))
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = cap.read()
            if not ok:
                continue
            scene = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            scene.thumbnail((640, 360))
            tile = Image.new('RGB', (640, 390), 'white')
            ImageDraw.Draw(tile).text((8, 8), f'Frame {len(tiles)+1} | {index/fps:.2f}s', fill='black')
            tile.paste(scene, ((640-scene.width)//2, 30))
            tiles.append(tile)
    finally:
        cap.release()
    if not tiles:
        return False
    sheet = Image.new('RGB', (1280, 390 * ((len(tiles)+1)//2)), 'white')
    for i, tile in enumerate(tiles):
        sheet.paste(tile, ((i % 2)*640, (i//2)*390))
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, quality=88)
    return True


async def _create_context_completion_with_retries(client: AsyncOpenAI, content: list[dict], retries: int = 3):
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return await client.chat.completions.create(
                model=settings.ai_model,
                messages=[{"role": "user", "content": content}],
                response_format={"type": "json_object"},
                temperature=0.2,
                timeout=90,
            )
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                await asyncio.sleep(min(2 * attempt, 6))
    assert last_error is not None
    raise last_error


def _sample_video_frames(video_file: Path, count: int = 4) -> list[str]:
    try:
        import cv2
    except ImportError:
        return []

    cap = cv2.VideoCapture(str(video_file))
    if not cap.isOpened():
        return []
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    if total <= 0:
        cap.release()
        return []

    positions = [int(total * ratio) for ratio in (0.08, 0.25, 0.55, 0.82)]
    frames: list[str] = []
    for pos in positions[:count]:
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, min(total - 1, pos)))
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        if w > 720:
            scale = 720 / w
            frame = cv2.resize(frame, (720, max(1, int(h * scale))))
        ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        if ok:
            frames.append(base64.b64encode(buffer).decode("utf-8"))
    cap.release()
    return frames


def _compact_transcript(events: list[SubtitleEvent], max_events: int = 240, max_chars: int = 24000) -> str:
    """Sample coherent windows across the whole video, not only its opening."""
    if not events:
        return ""
    if len(events) <= max_events:
        selected = list(range(len(events)))
    else:
        window_count = 12
        per_window = max(4, max_events // window_count)
        selected_set: set[int] = set()
        for window in range(window_count):
            center = round(window * (len(events) - 1) / max(window_count - 1, 1))
            start = max(0, min(len(events) - per_window, center - per_window // 2))
            selected_set.update(range(start, min(len(events), start + per_window)))
        selected = sorted(selected_set)[:max_events]

    lines: list[str] = []
    for index in selected:
        text = re.sub(r"\s+", " ", events[index].text).strip()
        if text:
            lines.append(f"#{index + 1} [{events[index].start:.2f}-{events[index].end:.2f}s]: {text}")
    # Reduce the sample evenly instead of clipping the tail of a long video.
    while len(lines) > 1 and sum(len(line)+1 for line in lines) > max_chars:
        size = max(2, len(lines)//2)
        if size == len(lines):
            break
        lines = [lines[round(i*(len(lines)-1)/(size-1))] for i in range(size)]
    if lines and sum(len(line)+1 for line in lines) > max_chars:
        limit = max(1, max_chars//len(lines)-1)
        lines = [line[:limit] for line in lines]
    return "\n".join(lines)


def _loads_json_object(content: str) -> dict:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def _sanitize_context(data: object, transcript: str = "") -> dict:
    if not isinstance(data, dict):
        return {}
    allowed = {
        "film_title",
        "genre",
        "setting",
        "plot_summary",
        "scene_notes",
        "characters",
        "relationships",
        "glossary",
        "honorific_rules",
        "translation_notes",
        "confidence",
    }
    return {key: data.get(key) for key in allowed if data.get(key) not in (None, "", [], {})}
