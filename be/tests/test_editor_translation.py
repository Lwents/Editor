"""Editor translation must finish without synthesizing or burning subtitles."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.models.job import DubbingRequest, JobStatus
from app.pipeline import dubbing


class EditorTranslationTests(unittest.TestCase):
    def test_editor_job_returns_clean_video_and_exact_subtitle_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            subtitles = root / "translated.srt"
            subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chào\n", encoding="utf-8")
            request = DubbingRequest(local_file_path=str(source), editor_subtitles_only=True)
            store = Mock()
            store.get.return_value = SimpleNamespace(request=request)
            with patch.object(dubbing, "settings", SimpleNamespace(storage_dir=str(root))), \
                 patch.object(dubbing, "job_store", store), \
                 patch.object(dubbing, "find_ffmpeg", return_value="ffmpeg"), \
                 patch.object(dubbing, "prepare_source_video", AsyncMock(return_value=source)), \
                 patch.object(dubbing, "extract_audio", AsyncMock()), \
                 patch.object(dubbing, "probe_video_duration", AsyncMock(return_value=1.0)), \
                 patch.object(dubbing, "get_or_create_subtitles", AsyncMock(return_value=subtitles)), \
                 patch.object(dubbing, "get_voice_engine") as voice, \
                 patch.object(dubbing, "render_video") as render:
                asyncio.run(dubbing.process_dubbing_job("editor-test"))
                voice.assert_not_called()
                render.assert_not_called()
            completed = store.update.call_args.kwargs
            self.assertEqual(completed["status"], JobStatus.completed)
            self.assertEqual(completed["output_file_path"], str(source))
            self.assertEqual((root / "jobs/editor-test/subtitles.editor.srt").read_text(encoding="utf-8"), subtitles.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
