import unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone
from flask import Flask, render_template
from download_names import download_filename


class DownloadNameTests(unittest.TestCase):
    def test_print_template_with_and_without_coach_escapes_username(self):
        app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'templates'))
        with app.test_request_context('/'):
            user = SimpleNamespace(is_authenticated=True, username='</script>')
            html = render_template('_download_names.html', current_user=user)
            self.assertIn('const coachNumber = null;', html)
            self.assertIn(r'\u003c/script\u003e', html)
            html = render_template('_download_names.html', current_user=user,
                                   coach=SimpleNamespace(coach_number='000 999'))
            self.assertIn('const coachNumber = "000 999";', html)

    def test_coach_user_and_sast_date_rollover(self):
        now = datetime(2026, 10, 10, 23, 4, 5, tzinfo=timezone.utc)
        self.assertEqual(download_filename('BOM', '000 999', 'pmmto', 'csv', now),
                         'BOM_000_999_20261011_010405_SAST_pmmto.csv')

    def test_all_coaches_and_unsafe_characters(self):
        now = datetime(2026, 10, 10, tzinfo=timezone.utc)
        self.assertEqual(download_filename('backup', None, '../A "B"\r\n', 'csv', now),
                         'backup_all-coaches_20261010_020000_SAST_A_B.csv')
        self.assertEqual(download_filename('BOM', '../', '...', 'csv', now),
                         'BOM_all-coaches_20261010_020000_SAST_user.csv')
