import unittest
from unittest.mock import patch
import cv2
import numpy as np
from app.services.subtitles import frame_processing as frames


class NativeFrameTests(unittest.TestCase):
    def test_cpp_masks_exactly_match_reference_at_thresholds_and_edges(self):
        lib = frames.native_library()
        self.assertIsNotNone(lib, 'Build native frame kernels before running this test')
        rng = np.random.default_rng(2026)
        for shape in ((1, 1, 3), (2, 7, 3), (15, 37, 3), (96, 1920, 3)):
            image = rng.integers(0, 256, shape, dtype=np.uint8)
            for value in (134, 135, 189, 190, 255):
                image[0, 0] = value
                gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                with patch.object(frames, 'native_library', return_value=None):
                    reference = frames.caption_masks(image, gray)
                actual = frames.caption_masks(image, gray)
                for expected, result in zip(reference, actual):
                    np.testing.assert_array_equal(result, expected)

    def test_cpp_comparison_matches_strided_and_reversed_reference(self):
        self.assertIsNotNone(frames.native_library())
        rng = np.random.default_rng(8)
        a = rng.integers(0, 2, (17, 40), dtype=np.uint8)
        b = rng.integers(0, 2, (17, 40), dtype=np.uint8)
        for left, right in ((a, b), (a[:, 3:19], b[:, 3:19]), (a[::-1], b[::-1]), (a[:, ::2], b[:, ::2])):
            with patch.object(frames, 'native_library', return_value=None):
                expected = frames.compare_masks(left, right)
            self.assertEqual(frames.compare_masks(left, right), expected)

    def test_cache_preserves_frame_identity_after_hits_and_seeks(self):
        class Capture:
            position = 0
            def get(self, prop): return self.position
            def set(self, prop, index): self.position = index
            def read(self):
                frame = np.full((4, 4, 3), self.position, dtype=np.uint8)
                self.position += 1
                return True, frame
        cache = frames.FrameCache(Capture(), budget=96)
        for index in (5, 6, 5, 7, 6, 9, 10, 9):
            ok, frame = cache.read_at(index)
            self.assertTrue(ok)
            self.assertTrue(np.all(frame == index))
            self.assertLessEqual(cache.bytes, 96)
        self.assertGreater(cache.hits, 0)
