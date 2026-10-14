import unittest

from app.services.ai.lexical_guidance import lexical_guidance


class LexicalGuidanceTests(unittest.TestCase):
    def test_traditional_glyphs_and_colloquial_idioms_get_matching_senses(self):
        notes = lexical_guidance(['穩固皇權的垫腳石', '喂你个锤锤'], 'vi')
        self.assertTrue(any(row['source'] == '垫腳石' and row['vi'] == 'bàn đạp' for row in notes))
        self.assertTrue(any('con khỉ' in row['vi'] for row in notes))

    def test_distinguishes_similar_chinese_idioms(self):
        notes = lexical_guidance(['他不以为然，却不以为意。'], 'vi')
        by_source = {row['source']: row for row in notes}
        self.assertIn('không tán thành', by_source['不以为然']['vi'])
        self.assertEqual(by_source['不以为意']['vi'], 'không để tâm')

    def test_english_boundaries_case_and_polysemy(self):
        self.assertEqual(lexical_guidance(['unsanctioned actuality'], 'vi'), [])
        notes = lexical_guidance(['They SANCTION the decision.'], 'vi')
        self.assertIn('hoặc', notes[0]['vi'])
        self.assertIn('có thể là nghĩa đen', lexical_guidance(['Break a leg!'], 'vi')[0]['usage'])

    def test_limits_and_irrelevant_languages(self):
        source = ['他得寸进尺，又心甘情愿。']
        self.assertEqual(len(lexical_guidance(source, 'vi', max_entries=1)), 1)
        self.assertEqual(lexical_guidance(source, 'vi', max_chars=1), [])
        self.assertEqual(lexical_guidance(source, 'en'), [])
        self.assertEqual(lexical_guidance(['take', 'for granted'], 'vi'), [])


if __name__ == '__main__':
    unittest.main()
