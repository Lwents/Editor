import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from app.core import settings
from app.models.job import VoiceGender
from app.models.job import DubbingRequest
from app.services.ai.voice import VoiceError, get_voice_engine
from app.services.ai.vieneu_voice import VieNeuVoiceEngine


class VieNeuVoiceTests(unittest.IsolatedAsyncioTestCase):
    def test_selected_voices_are_independent_per_job(self):
        male = get_voice_engine("vieneu", "Hải Đăng")
        female = get_voice_engine("vieneu", "Mai Anh")
        self.assertEqual(male.get_voice(VoiceGender.female), "Hải Đăng")
        self.assertEqual(female.get_voice(VoiceGender.male), "Mai Anh")
        edge = get_voice_engine("edge", "vi-VN-NamMinhNeural")
        self.assertEqual(edge.get_voice_for_text("Xin chào", VoiceGender.female), "vi-VN-NamMinhNeural")

    def test_rejects_voice_from_another_engine(self):
        with self.assertRaises(ValueError):
            DubbingRequest(local_file_path="test.mp4", voice_engine="edge", voice_name="Mai Anh")
        with self.assertRaises(ValueError):
            DubbingRequest(local_file_path="test.mp4", voice_name="Mai Anh")

    def test_factory_selects_local_engine(self):
        with patch.object(settings, "voice_engine", "vieneu"):
            self.assertIsInstance(get_voice_engine(), VieNeuVoiceEngine)

    async def test_saves_wave_and_sends_selected_voice(self):
        wav = b"RIFF" + bytes(4) + b"WAVE" + bytes(100)
        response = Mock(content=wav)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "speech.wav"
            with patch("app.services.ai.vieneu_voice.requests.post", return_value=response) as post:
                await VieNeuVoiceEngine()._save_wav("Xin chào", output, VoiceGender.female)
            self.assertEqual(output.read_bytes(), wav)
        self.assertEqual(post.call_args.kwargs["json"]["voice"], settings.vieneu_voice_female)
        self.assertEqual(post.call_args.kwargs["json"]["input"], "Xin chào")

    async def test_rejects_invalid_audio(self):
        with patch("app.services.ai.vieneu_voice.requests.post", return_value=Mock(content=b"invalid")):
            with self.assertRaises(VoiceError):
                await VieNeuVoiceEngine()._save_wav("Xin chào", Path("unused.wav"), VoiceGender.male)

    async def test_connection_failure_has_useful_error(self):
        with patch("app.services.ai.vieneu_voice.requests.post", side_effect=requests.ConnectionError("offline")):
            with self.assertRaisesRegex(VoiceError, "VieNeu"):
                await VieNeuVoiceEngine()._save_wav("Xin chào", Path("unused.wav"), VoiceGender.female)
