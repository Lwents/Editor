from pathlib import Path
import unicodedata
from typing import Callable

from PIL import ImageFont

from app.models.job import DubbingRequest
from app.services.subtitles.timing import SubtitleEvent, ass_time, normalize_events, parse_srt


SUBTITLE_FONT_NAME = "Be Vietnam Pro ExtraBold"
SUBTITLE_FONT_FILE = "BeVietnamPro-ExtraBoldItalic.ttf"
SUBTITLE_FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"


def subtitle_font_dir() -> Path:
    font_file = SUBTITLE_FONT_DIR / SUBTITLE_FONT_FILE
    if not font_file.is_file():
        raise FileNotFoundError(f"Khong tim thay font phu de: {font_file}")
    return SUBTITLE_FONT_DIR


def srt_to_positioned_ass(
    srt_file: Path,
    ass_file: Path,
    width: int,
    height: int,
    request: DubbingRequest,
    *,
    single_line: bool = False,
) -> Path:
    x = round(width * request.subtitle_x_percent / 100)
    y = round(height * request.subtitle_y_percent / 100)
    # ASS font metrics have a smaller cap-height than CSS pixels. The 1.25
    # correction yields a visible glyph height close to 5.5-6.5% of the frame.
    font_size = max(16, round(request.subtitle_font_size * height / 1080 * 1.25))
    outline = max(2, round(6 * height / 1080))
    shadow = max(1, round(2.5 * height / 1080))
    events = normalize_events(parse_srt(srt_file))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{SUBTITLE_FONT_NAME},{font_size},&H0000F2FF,&H0000F2FF,&H00000000,&H96000000,-1,-1,0,0,93,100,0,0,1,{outline},{shadow},2,{round(width * 0.08)},{round(width * 0.08)},{round(height * 0.08)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    font = ImageFont.truetype(str(subtitle_font_dir() / SUBTITLE_FONT_FILE), font_size)
    measure = lambda text: font.getlength(text) * 0.93
    line_width = width * 0.83 - 2 * (outline + shadow)
    lines = [header]
    for event in events:
        normalized_text = unicodedata.normalize("NFC", event.text).upper()
        display_text = (
            " ".join(normalized_text.replace(r"\N", " ").split())
            if single_line
            else _wrap_subtitle_text(normalized_text, line_width, measure=measure)
        )
        # Explicit breaks are final: libass must not rewrap them into extra lines.
        # Reduce only unusually long cues that cannot fit in two lines.
        size_override = ""
        if not single_line:
            widest = max(measure(line) for line in display_text.split(r"\N"))
            if widest > line_width:
                size_override = rf"\fs{max(1, int(font_size * line_width / widest))}"
        safe_text = _ass_escape(display_text)
        lines.append(
            f"Dialogue: 0,{ass_time(event.start)},{ass_time(event.end)},Default,,0,0,0,,"
            f"{{\\an2\\q2{size_override}\\pos({x},{y})}}{safe_text}\n"
        )

    ass_file.write_text("".join(lines), encoding="utf-8")
    return ass_file


def write_srt(events: list[SubtitleEvent], output_file: Path) -> Path:
    from app.services.subtitles.timing import srt_time

    with output_file.open("w", encoding="utf-8") as handle:
        for index, event in enumerate(events, start=1):
            handle.write(f"{index}\n")
            handle.write(f"{srt_time(event.start)} --> {srt_time(event.end)}\n")
            handle.write(f"{event.text.strip()}\n\n")
    return output_file


def _wrap_subtitle_text(
    text: str, max_chars: float, *, measure: Callable[[str], float] = len
) -> str:
    normalized = text.replace(r"\N", " ")
    normalized = " ".join(normalized.split())
    if measure(normalized) <= max_chars:
        return normalized

    words = normalized.split()
    if len(words) < 2:
        return normalized

    best_split = 1
    best_score: tuple[float, float] | None = None
    for index in range(1, len(words)):
        first = " ".join(words[:index])
        second = " ".join(words[index:])
        first_width, second_width = measure(first), measure(second)
        overflow = max(0, first_width - max_chars) + max(0, second_width - max_chars)
        balance = abs(first_width - second_width) / max(max_chars, 1)
        # Prefer a clause boundary over equal character counts. Keep articles,
        # auxiliaries and common two-word expressions attached to their phrase.
        previous, following = words[index - 1].upper(), words[index].upper()
        boundary_cost = 0.0
        if previous[-1:] in ",;:.!?":
            boundary_cost -= 0.45
        if following in {"NHƯNG", "KHIẾN", "VÌ", "NÊN", "ĐỂ", "KHI", "MÀ", "VÀ"}:
            boundary_cost -= 0.3
        if previous in {"MỘT", "NHỮNG", "CÁC", "ĐANG", "SẼ", "ĐÃ", "CỦA", "VỚI", "VÀ", "LÀ"}:
            boundary_cost += 0.8
        if f"{previous} {following}" in {"BÌNH MINH", "KHÁM PHÁ", "ỐC VÍT", "XE TỰ", "TỰ HÀNH", "SÔNG NGÒI", "MIỆNG HỐ", "HỆ THỐNG", "TÀN TÍCH"}:
            boundary_cost += 0.8
        if min(index, len(words) - index) < 3:
            boundary_cost += 0.7
        score = (overflow, balance + boundary_cost)
        if best_score is None or score < best_score:
            best_score = score
            best_split = index

    return f"{' '.join(words[:best_split])}\\N{' '.join(words[best_split:])}"


def _ass_escape(text: str) -> str:
    return text.replace("{", r"\{").replace("}", r"\}")
