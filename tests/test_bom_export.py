"""Real BOM routes/models/templates against isolated in-memory SQLite.

Extract routes as in test_bom_dependencies to avoid app import-time DB writes.
Run: python -m unittest discover -s tests -v
"""
import ast
import csv
import io
import unittest
from datetime import date, datetime
from pathlib import Path

from flask import Flask, Response, abort, g, render_template, request
from flask_login import LoginManager, current_user, login_required
from jinja2 import ChoiceLoader, DictLoader

from models import db, User, Coach, CoachBOMItem
from download_names import download_filename

ROOT = Path(__file__).resolve().parents[1]
app = Flask(__name__, template_folder=str(ROOT / 'templates'))
app.config.update(TESTING=True, SECRET_KEY='test-only', SQLALCHEMY_DATABASE_URI='sqlite://')
db.init_app(app)
login_manager = LoginManager(app)
login_manager.user_loader(lambda user_id: db.session.get(User, int(user_id)))
app.jinja_loader = ChoiceLoader([
    DictLoader({'base.html': '{% block content %}{% endblock %}'}), app.jinja_loader,
])
names = {'enforce_record_permissions', 'get_bom_item_flags', 'bom_summary_for_coach',
         'coach_bom_rows', 'bom_csv_text', 'coach_bom_export_csv', 'coach_bom',
         'bom_section_catalog'}
tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8'))
functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
assert len(functions) == len(names)
exec(compile(ast.Module(body=functions, type_ignores=[]), str(ROOT / 'app.py'), 'exec'), globals())
for endpoint in ['production_tasks', 'production_dependency_gantt', 'coaches_edit', 'coaches_list', 'coach_bom_import']:
    app.add_url_rule('/stub/' + endpoint, endpoint, lambda: '')


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add_all([
            User(id=1, username='viewer', password='unused', role='viewer'),
            User(id=2, username='editor', password='unused', role='editor'),
            User(id=3, username='admin', password='unused', role='admin'),
            Coach(id=1, coach_number='C1', coach_type='Test'),
            Coach(id=2, coach_number='C2', coach_type='Test'),
            Coach(id=3, coach_number='Empty', coach_type='Test'),
        ])
        db.session.flush()
        db.session.add_all([
            CoachBOMItem(id=1, coach_id=1, component='Café, "seat"', section='Electrical',
                         quantity=2, uom='EA', notes='First line\nSecond line', delivered=True,
                         expected_delivery_date=date(2026, 9, 1), supplier_date=date(2026, 9, 2),
                         actual_delivery_date=date(2026, 9, 3)),
            CoachBOMItem(id=2, coach_id=1, component='Windows', section='Paint',
                         quantity=1, uom='m²', delivered=False),
            CoachBOMItem(id=3, coach_id=2, component='Other coach secret'),
        ])
        db.session.commit()
        self.client = app.test_client()
        self.login(1)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login(self, user_id):
        g.pop('_login_user', None)
        with self.client.session_transaction() as sess:
            sess.clear()
            if user_id:
                sess['_user_id'] = str(user_id)
                sess['_fresh'] = True

    def export(self, coach_id=1, **params):
        return self.client.get(f'/coaches/{coach_id}/bom/export.csv', query_string=params)

    def rows(self, response):
        return list(csv.DictReader(io.StringIO(response.data.decode('utf-8-sig'))))

    def test_content_headers_encoding_quoting_and_coach_isolation(self):
        response = self.export(coach_id=1, item_id=3)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(response.content_type, 'text/csv; charset=utf-8')
        self.assertRegex(response.headers['Content-Disposition'], r'attachment; filename=BOM_C1_\d{8}_\d{6}_SAST_viewer\.csv')
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        rows = self.rows(response)
        self.assertEqual(len(rows), 2)
        self.assertEqual(list(rows[0]), ['component', 'section', 'quantity', 'uom',
            'expected_delivery_date', 'supplier_date', 'actual_delivery_date', 'countdown', 'delivered', 'notes'])
        self.assertEqual(rows[0]['component'], 'Café, "seat"')
        self.assertEqual(rows[0]['notes'], 'First line\nSecond line')
        self.assertEqual(rows[0]['quantity'], '2')
        self.assertEqual(rows[0]['supplier_date'], '2026-09-02')
        self.assertEqual(rows[0]['actual_delivery_date'], '2026-09-03')
        self.assertEqual(rows[0]['countdown'], 'Delivered')
        self.assertEqual(rows[0]['delivered'], '1')
        self.assertEqual(rows[1]['expected_delivery_date'], '')
        self.assertEqual(rows[1]['countdown'], 'No expected date')
        self.assertEqual(rows[1]['delivered'], '0')
        self.assertNotIn(b'Other coach', response.data)

    def test_filter_matches_page_search_fields_and_is_literal(self):
        for query, component in [(' CAFÉ ', 'Café, "seat"'), ('electrical', 'Café, "seat"'),
                                 ('second line', 'Café, "seat"'), ('M²', 'Windows')]:
            with self.subTest(query=query):
                self.assertEqual([row['component'] for row in self.rows(self.export(q=query))], [component])
        for query in ['absent', '%', "' OR 1=1 --", 'Other coach']:
            self.assertEqual(self.rows(self.export(q=query)), [])
        self.assertEqual(len(self.rows(self.export(q='   '))), 2)

    def test_empty_and_missing_coach(self):
        self.assertEqual(self.rows(self.export(3)), [])
        self.assertEqual(self.export(999).status_code, 404)

    def test_authentication_and_read_roles(self):
        self.login(None)
        self.assertEqual(self.export().status_code, 401)
        for user_id in [1, 2, 3]:
            self.login(user_id)
            self.assertEqual(self.export().status_code, 200)
        self.login(1)
        self.assertEqual(self.client.post('/coaches/1/bom/export.csv').status_code, 403)
        self.assertEqual(CoachBOMItem.query.count(), 3)

    def test_formula_injection_and_safe_filename(self):
        item = db.session.get(CoachBOMItem, 1)
        item.component = '=HYPERLINK("https://example.test")'
        item.section = '  +1+1'
        item.uom = '@SUM(1)'
        item.notes = '\t=1+1'
        db.session.get(Coach, 1).coach_number = '../../Bad\r\nX-Evil: yes'
        db.session.commit()
        response = self.export()
        row = next(r for r in self.rows(response) if r['quantity'] == '2')
        for field in ['component', 'section', 'uom', 'notes']:
            self.assertTrue(row[field].startswith("'"))
        filename = response.headers['Content-Disposition']
        for unsafe in ['\r', '\n', '/', '\\']:
            self.assertNotIn(unsafe, filename)
        for value in ['=1', '+1', '-1', '@SUM(1)', ' \t=1', '\nvalue', '\rvalue']:
            self.assertEqual(bom_csv_text(value), "'" + value)
        self.assertEqual(bom_csv_text('ordinary text'), 'ordinary text')
        self.assertEqual(item.notes, '\t=1+1')

    def test_page_buttons_and_shared_search_for_every_role(self):
        for user_id in [1, 2, 3]:
            self.login(user_id)
            response = self.client.get('/coaches/1/bom')
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn('id="bomCsvBtn"', html)
            self.assertIn('/coaches/1/bom/export.csv', html)
            self.assertIn('id="bomPdfBtn"', html)
            self.assertIn('window.print()', html)
            self.assertIn('data-search="Windows Paint  m²"', html)
            self.assertEqual('Preview CSV' in html, user_id != 1)

    def test_importer_accepts_export_columns(self):
        from bom_csv import build_plan, section_catalog
        plan = build_plan(self.export().data.decode('utf-8-sig'), section_catalog(), coach_id=1)
        self.assertEqual(plan['ready'], 2)
        self.assertEqual(plan['invalid'], 0)


if __name__ == '__main__':
    unittest.main()
