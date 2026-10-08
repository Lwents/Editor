import unittest
from unittest.mock import patch

from app.core import settings
from app.services.media.renderer import _available_video_output_args


class EncoderSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_driver_uses_software_encoder(self):
        with patch.object(settings, "video_encoder", "h264_nvenc"), patch("app.services.media.renderer.Path.stat"), patch("app.services.media.renderer._encoder_works", return_value=False):
            args = await _available_video_output_args("ffmpeg")
        self.assertEqual(args[args.index("-c:v") + 1], "libx264")
        self.assertNotIn("-cq", args)
        self.assertEqual(args[args.index("-preset") + 1], "veryfast")

    async def test_working_driver_keeps_hardware_encoder(self):
        with patch.object(settings, "video_encoder", "h264_nvenc"), patch("app.services.media.renderer.Path.stat"), patch("app.services.media.renderer._encoder_works", return_value=True):
            args = await _available_video_output_args("ffmpeg")
        self.assertEqual(args[args.index("-c:v") + 1], "h264_nvenc")

    async def test_software_encoder_does_not_probe_hardware(self):
        with patch.object(settings, "video_encoder", "libx264"), patch("app.services.media.renderer._encoder_works") as probe:
            await _available_video_output_args("ffmpeg")
        probe.assert_not_called()
