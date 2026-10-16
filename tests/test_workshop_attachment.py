from pathlib import Path
import unittest
from xml.etree import ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]


class WorkshopAttachment(unittest.TestCase):
    def test_docx_is_small_synthetic_and_has_no_external_relationships(self):
        file = ROOT / 'examples/pizza-order-update.docx'
        self.assertLess(file.stat().st_size, 10_000)
        with zipfile.ZipFile(file) as archive:
            self.assertEqual(set(archive.namelist()),
                             {'[Content_Types].xml', '_rels/.rels', 'word/document.xml'})
            document = ET.fromstring(archive.read('word/document.xml'))
            paragraphs = [''.join(p.itertext()) for p in document.findall(
                './/{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p')]
            self.assertEqual(len(paragraphs), 5)
            text = '\n'.join(paragraphs)
            for expected in ('Учебный вымышленный пример', 'P-402',
                             'Повторная оплата не требуется', '18:24',
                             'Имя курьера и время доставки в этом документе не указаны'):
                self.assertIn(expected, text)
            relationships = ET.fromstring(archive.read('_rels/.rels'))
            self.assertEqual(len(relationships), 1)
            self.assertEqual(relationships[0].attrib['Target'], 'word/document.xml')
            self.assertNotIn('TargetMode', relationships[0].attrib)
        self.assertIn('../examples/pizza-order-update.docx',
                      (ROOT / 'docs/CHAT_AND_ACCESS.md').read_text())


if __name__ == '__main__':
    unittest.main()
