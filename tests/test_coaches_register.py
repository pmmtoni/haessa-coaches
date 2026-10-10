"""Exercise the real register route/template with an isolated SQLite database."""
import ast
import math
import re
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, render_template, request
from flask_login import LoginManager, current_user, login_required
from jinja2 import ChoiceLoader, DictLoader
from models import db, User, Coach, CompletionTask

ROOT = Path(__file__).resolve().parents[1]
app = Flask(__name__, template_folder=str(ROOT / 'templates'))
app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_DATABASE_URI='sqlite://')
db.init_app(app)
login_manager = LoginManager(app)
login_manager.user_loader(lambda uid: db.session.get(User, int(uid)))
app.jinja_loader = ChoiceLoader([
    DictLoader({'base.html': '{% block content %}{% endblock %}'}), app.jinja_loader,
])
names = {'coaches_list', 'get_inspection_date', 'get_schedule_flags',
         'get_current_phase_info', 'get_delay_risk_score', 'build_smart_alerts'}
tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8'))
functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
assert len(functions) == len(names)
exec(compile(ast.Module(body=functions, type_ignores=[]), str(ROOT / 'app.py'), 'exec'), globals())
for endpoint in ['coaches_add', 'coaches_edit', 'coaches_archive', 'coaches_unarchive', 'coaches_delete']:
    app.add_url_rule('/stub/' + endpoint, endpoint, lambda: '')


class RegisterTests(unittest.TestCase):
    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add(User(id=1, username='admin', password='unused', role='admin'))
        db.session.add_all([Coach(id=1, coach_number='C01', coach_type='Motor'),
                            Coach(id=2, coach_number='C02', coach_type='Trailer', archived=True),
                            Coach(id=3, coach_number='Empty', coach_type='Motor')])
        db.session.flush()
        for phase, section, states in [('Stripping', 'Electrical', [True]),
                                       ('Completion', 'Electrical', [True, False, False]),
                                       ('Completion', 'Paint', [False]),
                                       ('Completion', ' ', [True]),
                                       ('Completion', '<script>bad</script>', [False])]:
            for state in states:
                db.session.add(CompletionTask(coach_id=1, coach_no='C01', coach_type='Motor',
                    phase=phase, section=section, task='Task', completed=state))
        db.session.commit()
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session['_user_id'] = '1'
            session['_fresh'] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def page(self, **params):
        response = self.client.get('/coaches', query_string=params)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def test_subsections_grouped_by_phase_with_counts_and_rounding(self):
        html = self.page(search='C01')
        groups = re.findall(r'<ul[^>]*aria-label="([^"]+)"[^>]*>(.*?)</ul>', html, re.S)
        groups = dict(groups)
        self.assertIn('Electrical: 1/1 (100.0%)', groups['Stripping subsection completion'])
        completion = groups['Completion subsection completion']
        self.assertIn('Electrical: 1/3 (33.3%)', completion)
        self.assertIn('Paint: 0/1 (0.0%)', completion)
        self.assertIn('General: 1/1 (100.0%)', completion)
        self.assertIn('&lt;script&gt;bad&lt;/script&gt;', completion)
        self.assertIn('Completion: 2/6 (33.3%)', html)
        self.assertIn('42.9%', html)

    def test_empty_tasks_filters_archives_and_actions(self):
        html = self.page(search='Empty')
        self.assertIn('0.0%', html)
        self.assertNotIn('aria-label="Completion subsection completion"', html)
        self.assertIn('No production tasks recorded.', html)
        self.assertIn('/stub/coaches_edit?id=3', html)
        self.assertNotIn('C02', self.page())
        self.assertIn('C02', self.page(show_archived='true', coach_type='Trailer'))
        self.assertIn('No coaches found', self.page(search='missing'))

    def test_pagination_and_filter_state(self):
        db.session.add_all([Coach(coach_number=f'M{i:02}', coach_type='Motor') for i in range(12)])
        db.session.commit()
        html = self.page(coach_type='Motor', page=2, per_page=10)
        self.assertIn('Page 2 of 2', html)
        self.assertNotIn('C01', html)
        self.assertIn('coach_type=Motor', html)

    def test_print_report_keeps_all_fields_and_full_notes(self):
        coach = db.session.get(Coach, 1)
        coach.notes = 'Long notes ' * 70 + '<script>unsafe</script> END OF NOTES'
        coach.due_date = date(2026, 12, 31)
        db.session.commit()
        html = self.page(search='C01')
        report = html.split('<section class="register-print"', 1)[1]
        for label in ['Stripping', 'Completion', 'Serviceworthy', 'Retention',
                      'Current Phase', 'Days in Phase', 'Delay Risk', 'Due Date', 'Notes']:
            self.assertIn(label, report)
        self.assertIn('31 Dec 2026', report)
        self.assertIn('END OF NOTES', report)
        self.assertIn('&lt;script&gt;unsafe&lt;/script&gt;', report)
        self.assertNotIn('<script>unsafe</script>', report)
        self.assertIn('Electrical: 1/3 (33.3%)', report)
        self.assertIn('Printed by admin', report)
        self.assertNotIn('text-truncate', report)
        self.assertNotIn('<form', report)


if __name__ == '__main__':
    unittest.main()
