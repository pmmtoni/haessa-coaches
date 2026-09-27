"""Isolated integration tests: real models/routes/templates, temporary SQLite only.

Extract selected app functions to avoid the legacy app's import-time database
creation/admin seeding and dependencies on files not provided with this change.
Run from repository root: python -m unittest discover -s tests -p test_bom_dependencies.py -v
"""
import ast
import hmac
import secrets
import unittest
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from flask_login import LoginManager, current_user, login_required
from jinja2 import ChoiceLoader, DictLoader
from sqlalchemy.exc import IntegrityError

from models import db, User, Coach, CoachBOMItem, CompletionTask, CoachAudit, TaskBOMDependency

ROOT = Path(__file__).resolve().parents[1]
app = Flask(__name__, template_folder=str(ROOT / 'templates'))
app.config.update(TESTING=True, SECRET_KEY='test-only', SQLALCHEMY_DATABASE_URI='sqlite://')
db.init_app(app)
login_manager = LoginManager(app)
login_manager.user_loader(lambda user_id: db.session.get(User, int(user_id)))
app.jinja_loader = ChoiceLoader([app.jinja_loader, DictLoader({'base.html': '{% block content %}{% endblock %}'})])

names = {'role_required', 'enforce_record_permissions', 'log_coach_audit',
         'get_task_timing', 'build_dependency_gantt', 'update_task_bom_dependencies', 'production_tasks'}
tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8'))
functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
assert len(functions) == len(names)
exec(compile(ast.Module(body=functions, type_ignores=[]), str(ROOT / 'app.py'), 'exec'), globals())
for endpoint in ['coach_bom', 'coach_pack', 'coach_task_workflow', 'coaches_edit', 'coaches_list']:
    app.add_url_rule('/stub/' + endpoint, endpoint, lambda: '')


class CalculationTests(unittest.TestCase):
    def task(self, **changes):
        values = dict(id=1, coach_id=1, assigned_date=date(2026, 9, 1), started_date=None,
                      expected_days=3, due_date=date(2026, 9, 3), completed=False, completed_date=None)
        values.update(changes)
        return SimpleNamespace(**values)

    def material(self, **changes):
        values = dict(id=1, coach_id=1, component='Windows', expected_delivery_date=date(2026, 9, 1),
                      actual_delivery_date=date(2026, 9, 5))
        values.update(changes)
        return SimpleNamespace(**values)

    def row(self, task=None, materials=None, selected=None):
        return build_dependency_gantt([task or self.task()], materials or [self.material()],
                                      selected if selected is not None else {1: [1]}, date(2026, 9, 10))['rows'][0]

    def test_late_material_shifts_duration_without_mutating(self):
        task = self.task()
        row = self.row(task)
        self.assertEqual((row['delay'], row['shifted_start'], row['shifted_due']),
                         (4, date(2026, 9, 5), date(2026, 9, 7)))
        self.assertEqual(task.due_date, date(2026, 9, 3))

    def test_delays_are_max_not_sum(self):
        row = self.row(materials=[self.material(), self.material(id=2, actual_delivery_date=date(2026, 9, 8))], selected={1: [1, 2]})
        self.assertEqual(row['delay'], 7)

    def test_early_delivery_never_advances_task(self):
        self.assertEqual(self.row(materials=[self.material(actual_delivery_date=date(2026, 8, 30))])['delay'], 0)

    def test_pending_is_provisional_and_updates_by_day(self):
        row = self.row(materials=[self.material(actual_delivery_date=None)])
        self.assertTrue(row['pending'])
        self.assertEqual(row['delay'], 9)

    def test_missing_expected_is_unknown(self):
        self.assertTrue(self.row(materials=[self.material(expected_delivery_date=None)])['unknown'])

    def test_no_mapping_never_infers_section_relationship(self):
        self.assertEqual(self.row(selected={})['materials'], [])
        self.assertEqual(self.row(selected={})['delay'], 0)

    def test_other_coach_ignored_defensively(self):
        self.assertEqual(self.row(materials=[self.material(coach_id=2)])['materials'], [])

    def test_started_due_not_shifted_twice(self):
        row = self.row(self.task(started_date=date(2026, 9, 5), due_date=date(2026, 9, 7)))
        self.assertEqual(row['due'], date(2026, 9, 3))
        self.assertEqual(row['shifted_due'], date(2026, 9, 7))

    def test_completed_actual_kept(self):
        task = self.task(started_date=date(2026, 9, 2), completed=True, completed_date=date(2026, 9, 4))
        actual = [bar for bar in self.row(task)['bars'] if bar['kind'] == 'complete'][0]
        self.assertEqual(actual['end'], date(2026, 9, 4))

    def test_unscheduled_and_one_day(self):
        self.assertIsNone(self.row(self.task(assigned_date=None, expected_days=None, due_date=None))['shifted_due'])
        row = self.row(self.task(expected_days=1))
        self.assertEqual(row['shifted_start'], row['shifted_due'])

    def test_due_date_duration_fallback_and_leap_day(self):
        row = self.row(self.task(expected_days=None))
        self.assertEqual(row['duration'], 3)
        row = self.row(self.task(assigned_date=date(2024, 2, 28), expected_days=3))
        self.assertEqual(row['due'], date(2024, 3, 1))

    def test_empty_chart(self):
        self.assertEqual(build_dependency_gantt([], [], {})['rows'], [])


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        with db.engine.connect() as conn:
            conn.exec_driver_sql('PRAGMA foreign_keys=ON')
        db.create_all()
        db.session.add_all([User(id=1, username='editor', password='unused', role='editor'),
                            User(id=2, username='viewer', password='unused', role='viewer'),
                            Coach(id=1, coach_number='C1', coach_type='Test'),
                            Coach(id=2, coach_number='C2', coach_type='Test')])
        db.session.flush()
        db.session.add_all([CompletionTask(id=1, coach_id=1, coach_no='C1', coach_type='Test', section='Interior', task='Install windows'),
                            CoachBOMItem(id=1, coach_id=1, component='<script>alert(1)</script>', section='Interior'),
                            CoachBOMItem(id=2, coach_id=2, component='Other coach')])
        db.session.commit()
        self.client = app.test_client()
        self.login(1)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login(self, user_id):
        # The test holds one application context across requests; clear Login's
        # g cache when changing users, unlike production's per-request context.
        g.pop('_login_user', None)
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
            sess['bom_dependency_csrf'] = 'test-token'

    def post(self, ids, **extra):
        return self.client.post('/coach/1/tasks/1/bom-dependencies', data=dict(dependency_csrf='test-token', bom_item_ids=ids, **extra))

    def test_add_deduplicate_clear_and_audit(self):
        self.assertEqual(self.post(['1', '1']).status_code, 302)
        self.assertEqual(TaskBOMDependency.query.count(), 1)
        self.assertEqual(CoachAudit.query.count(), 1)
        self.post(['1'])
        self.assertEqual(CoachAudit.query.count(), 1)
        self.post([])
        self.assertEqual(TaskBOMDependency.query.count(), 0)
        self.assertEqual(CoachAudit.query.count(), 2)

    def test_cross_coach_and_invalid_rejected(self):
        for ids in [['2'], ['1', '2'], ['999'], ['bad']]:
            self.assertEqual(self.post(ids).status_code, 400)
        self.assertEqual(TaskBOMDependency.query.count(), 0)
        self.assertEqual(self.client.post('/coach/2/tasks/1/bom-dependencies', data={'dependency_csrf': 'test-token'}).status_code, 404)

    def test_read_only_and_anonymous_rejected(self):
        self.login(2)
        self.assertEqual(self.post(['1']).status_code, 403)
        with self.client.session_transaction() as sess:
            sess.clear()
        g.pop('_login_user', None)
        self.assertEqual(self.post(['1']).status_code, 401)

    def test_csrf_rejected(self):
        self.assertEqual(self.client.post('/coach/1/tasks/1/bom-dependencies', data={'bom_item_ids': '1'}).status_code, 400)

    def test_production_template_and_escape(self):
        self.post(['1'])
        response = self.client.get('/coach/1/production-tasks')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Production Activities', response.data)
        self.assertIn(b'BOM', response.data)
        self.assertIn(b'&lt;script&gt;', response.data)
        self.assertNotIn(b'<script>alert(1)</script>', response.data)
        self.login(2)
        self.assertNotIn(b'Save dependencies', self.client.get('/coach/1/production-tasks').data)

    def test_parent_delete_cascades_only_dependency(self):
        self.post(['1'])
        db.session.delete(db.session.get(CoachBOMItem, 1))
        db.session.commit()
        self.assertEqual(TaskBOMDependency.query.count(), 0)
        self.assertIsNotNone(db.session.get(CompletionTask, 1))

    def test_duplicate_database_constraint(self):
        self.post(['1'])
        db.session.add(TaskBOMDependency(task_id=1, bom_item_id=1))
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()


if __name__ == '__main__':
    unittest.main()
