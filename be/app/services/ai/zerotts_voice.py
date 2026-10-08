"""CPU ZeroTTS client using the shared WAV and subtitle timeline pipeline."""
from app.core import settings
from app.models.job import VoiceGender
from app.services.ai.vieneu_voice import VieNeuVoiceEngine


class ZeroTtsVoiceEngine(VieNeuVoiceEngine):
    label = "ZeroTTS"
    parts_name = "zerotts_parts"

    @property
    def api_url(self):
        return settings.zerotts_api_url

    @property
    def timeout_seconds(self):
        return settings.zerotts_timeout_seconds

    def get_voice(self, gender: VoiceGender) -> str:
        return self.voice_name or ("maichi" if gender == VoiceGender.female else "giahuy")
