import tempfile
import unittest
from pathlib import Path
from openpyxl import load_workbook
from ranging.demo import demo_result
import app


class AppTests(unittest.TestCase):
    def test_demo_is_labeled_and_uses_real_export(self):
        with tempfile.TemporaryDirectory() as td:
            status = app.main(['--demo', '--output', td])
            files = list(Path(td).glob('demo_*.xlsx'))
            self.assertEqual(status, 0)
            self.assertEqual(len(files), 1)
            wb = load_workbook(files[0])
            self.assertIn('ВЫМЫШЛЕННЫЕ', wb['Главная']['A1'].value)
            wb.close()
            self.assertFalse((Path(td)/'history.json').exists())

    def test_demo_has_unknown_and_priority_cases(self):
        result = demo_result()
        self.assertTrue(result['demo'])
        self.assertEqual(len(result['rows']), 4)
        self.assertTrue(any(row['group'].startswith('A') for row in result['rows']))
        self.assertTrue(any(row['score'] is None for row in result['rows']))

    def test_cli_bad_file_returns_error(self):
        self.assertEqual(app.main(['--input', '/no/such/file.csv']), 1)

if __name__ == '__main__':
    unittest.main()
