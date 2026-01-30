import unittest
from pathlib import Path

class DashboardStructureTest(unittest.TestCase):
    DASHBOARD_PATH = Path('app/ui/dashboard.py')

    def test_review_tab_exists(self):
        text = self.DASHBOARD_PATH.read_text(encoding='utf-8')
        self.assertIn('Entity Review Queue', text)

    def test_entity_filter_in_search(self):
        text = self.DASHBOARD_PATH.read_text(encoding='utf-8')
        self.assertIn('REFERS_TO', text)
        self.assertIn('Filtrar por entidade', text)

if __name__ == '__main__':
    unittest.main()
