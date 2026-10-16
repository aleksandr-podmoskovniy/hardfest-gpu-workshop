"""Keep the participant examples concise and consistent across the three guides."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class KnowledgeExamples(unittest.TestCase):
    def test_every_guide_uses_the_same_cache_and_rag_story(self):
        for name in ('README.md', 'RTX5060.md', 'RTXPRO6000.md'):
            text = (ROOT / name).read_text()
            self.assertIn('examples/pizza-incident.md', text, name)
            self.assertIn('docs/CHAT_AND_ACCESS.md#что-спросить-у-базы-знаний', text, name)
            for old in ('регламент «Липа»', 'КЕДР-7318', 'Синий компас', 'Липовый Бор'):
                self.assertNotIn(old, text, name)

    def test_stories_are_short_and_explicitly_fictional(self):
        pizza = (ROOT / 'examples/pizza-incident.md').read_text()
        cases = (ROOT / 'examples/knowledge-cases.md').read_text()
        self.assertIn('вымышлен', pizza.lower())
        self.assertIn('вымышлен', cases.lower())
        self.assertEqual(len(re.findall(r'^## ', pizza, re.M)), 5)
        self.assertEqual(len(re.findall(r'^## ', cases, re.M)), 3)
        self.assertEqual(len(re.findall(r'^### ', cases, re.M)), 12)
        self.assertLess(len(pizza.split()), 650)
        self.assertLess(len(cases.split()), 900)
        for text in (pizza, cases):
            for block in re.split(r'\n\s*\n', text):
                if not block.startswith(('#', '|', '```')):
                    self.assertLessEqual(len(block.split()), 75, block)

    def test_all_choices_are_discoverable(self):
        guide = (ROOT / 'docs/CHAT_AND_ACCESS.md').read_text()
        for source in ('knowledge-kubernetes.md', 'knowledge-llm.md', 'pizza-incident.md', 'knowledge-cases.md'):
            self.assertIn('../examples/' + source, guide)
        for title in re.findall(r'^## (.+)$', (ROOT / 'examples/knowledge-cases.md').read_text(), re.M):
            self.assertIn(title, guide)
        self.assertIn('снова выберите базу пиццерии через `#`', guide)


if __name__ == '__main__':
    unittest.main()
