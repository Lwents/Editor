import json
from pathlib import Path

from app.core import settings


def voice_catalog() -> dict:
    voices = json.loads((Path(__file__).resolve().parents[2] / "resources" / "vieneu_voices.json").read_text(encoding="utf-8"))
    return {
        "default_engine": settings.voice_engine if settings.voice_engine in {"vieneu", "zerotts", "edge"} else "edge",
        "engines": [
            {"id": "zerotts", "label": "ZeroTTS · CPU trên máy", "default_voice": "maichi", "voices": json.loads((Path(__file__).resolve().parents[2] / "resources" / "zerotts_voices.json").read_text(encoding="utf-8"))},
            {"id": "vieneu", "label": "VieNeu · chạy trên máy", "default_voice": settings.vieneu_voice_female, "voices": voices},
            {"id": "edge", "label": "Edge TTS · cần Internet", "default_voice": "vi-VN-HoaiMyNeural", "voices": [
                {"id": "vi-VN-HoaiMyNeural", "label": "Hoài My — nữ, tiếng Việt", "gender": "female"},
                {"id": "vi-VN-NamMinhNeural", "label": "Nam Minh — nam, tiếng Việt", "gender": "male"},
            ]},
        ],
    }


def validate_voice_selection(engine: str, voice_name: str | None) -> None:
    if voice_name is None:
        return
    entry = next((e for e in voice_catalog()["engines"] if e["id"] == engine), None)
    if not entry or not any(v["id"] == voice_name for v in entry["voices"]):
        raise ValueError("Giọng đọc không thuộc dịch vụ đã chọn.")
