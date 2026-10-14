import unittest

from benchmarks.evaluate_reference_videos import summarize


class SubtitleBenchmarkTests(unittest.TestCase):
    def test_spurious_caption_reduces_score_and_repeated_samples_count_once(self):
        samples = [{'id': 1, 'time': 1, 'candidate': 'Đúng vậy'},
                   {'id': 2, 'time': 2, 'candidate': 'Đúng vậy'},
                   {'id': 3, 'time': 3, 'candidate': 'Lời bịa'}]
        items = [{'source_visible': '是', 'confidence': .99, 'semantic_ok': True,
                  'address_ok': True, 'natural': True} for _ in range(2)]
        items.append({'source_visible': '', 'confidence': .99, 'semantic_ok': True,
                      'address_ok': True, 'natural': True})
        result = summarize(samples, items)
        self.assertEqual(result['meaning_address_and_naturalness'],
                         {'passed': 1, 'total': 2, 'percent': 50.0})
        self.assertEqual(result['spurious_caption_frames'], 1)
        self.assertEqual(len(result['errors']), 1)


if __name__ == '__main__':
    unittest.main()
