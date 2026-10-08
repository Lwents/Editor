import unittest
from app.models.job import DubbingRequest, VoiceGender
from app.services.ai.voice import get_voice_engine
from app.services.ai.voice_catalog import voice_catalog
from app.services.ai.zerotts_voice import ZeroTtsVoiceEngine


class ZeroTtsSelectionTests(unittest.TestCase):
    def test_catalog_and_per_job_voice_selection(self):
        catalog = next(e for e in voice_catalog()["engines"] if e["id"] == "zerotts")
        self.assertEqual(len(catalog["voices"]), 8)
        voice = get_voice_engine("zerotts", "giahuy")
        self.assertIsInstance(voice, ZeroTtsVoiceEngine)
        self.assertEqual(voice.get_voice(VoiceGender.female), "giahuy")
        self.assertEqual(get_voice_engine("zerotts", "maichi").get_voice(VoiceGender.male), "maichi")
        request = DubbingRequest(local_file_path="test.mp4", voice_engine="zerotts", voice_name="giahuy")
        self.assertEqual(request.voice_name, "giahuy")

    def test_rejects_mismatched_voices(self):
        for engine, name in [("zerotts", "Mai Anh"), ("edge", "maichi")]:
            with self.assertRaises(ValueError):
                DubbingRequest(local_file_path="test.mp4", voice_engine=engine, voice_name=name)
