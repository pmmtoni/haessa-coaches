"""Real routes/models on SQLite; never import the app's startup database code."""
import ast
import calendar
import csv
import io
import importlib.util
import unittest
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from flask import Flask, Response, abort, flash, g, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user, login_required
from jinja2 import ChoiceLoader, DictLoader
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.exc import IntegrityError

from models import db, User, Coach, CoachBOMItem, CoachAudit, CompletionTask
from bom_stock import IN_STOCK, OUT_OF_STOCK
from bom_csv import build_plan, section_catalog
from download_names import download_filename

ROOT = Path(__file__).resolve().parents[1]
app = Flask(__name__, template_folder=str(ROOT / 'templates'))
app.config.update(TESTING=True, SECRET_KEY='test-only', SQLALCHEMY_DATABASE_URI='sqlite://')
db.init_app(app)
login_manager = LoginManager(app)
login_manager.user_loader(lambda uid: db.session.get(User, int(uid)))
app.jinja_loader = ChoiceLoader([DictLoader({'base.html': '{% block content %}{% endblock %}'}), app.jinja_loader])
names = {'coach_bom', 'coach_bom_rows', 'bom_section_catalog', 'get_bom_item_flags',
         'bom_summary_for_coach', 'audit_snapshot', 'audit_value', 'audit_changes',
         'audit_label', 'audit_record', 'log_coach_audit', 'build_dependency_gantt',
         'coach_bom_analytics', '_save_bom_plan', 'bom_csv_text', 'coach_bom_export_csv'}
tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8'))
functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
assert len(functions) == len(names)
exec(compile(ast.Module(body=functions, type_ignores=[]), str(ROOT / 'app.py'), 'exec'), globals())
for endpoint in ['production_tasks', 'production_dependency_gantt', 'coaches_edit', 'coaches_list', 'coach_bom_import']:
    app.add_url_rule('/stub/' + endpoint, endpoint, lambda: '')


class StockTests(unittest.TestCase):
    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add_all([Coach(id=1, coach_number='C1', coach_type='Test'),
                            Coach(id=2, coach_number='C2', coach_type='Test'),
                            User(id=1, username='editor', password='x', role='editor'),
                            User(id=2, username='viewer', password='x', role='viewer')])
        db.session.commit()
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session['_user_id'] = '1'
            session['_fresh'] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def post(self, **values):
        data = dict(action='add', component='Seats', section='Coach Build', stock_status=OUT_OF_STOCK)
        data.update(values)
        return self.client.post('/coaches/1/bom', data=data)

    def item(self):
        db.session.expire_all()
        return CoachBOMItem.query.one()

    def test_stock_without_receipt_available_and_audited(self):
        self.assertEqual(self.post(stock_status=IN_STOCK).status_code, 302)
        item = self.item()
        self.assertTrue(item.available_for_production)
        self.assertIsNone(item.actual_delivery_date)
        self.assertFalse(item.delivered)
        self.assertIn('Stock Status: — -> In Stock', CoachAudit.query.one().details)
        summary = bom_summary_for_coach(db.session.get(Coach, 1))
        self.assertEqual(summary['counts']['available'], 1)
        self.assertEqual(summary['counts']['delivered'], 0)
        self.assertEqual(summary['counts']['outstanding'], 0)
        self.assertEqual(get_bom_item_flags(item)['countdown_label'], IN_STOCK)
        page = self.client.get('/coaches/1/bom')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'name="stock_status"', page.data)
        self.assertIn(b'disabled', page.data)

    def test_actual_overrides_unchecked_delivered_and_out_of_stock(self):
        self.post(actual_delivery_date='2026-10-01', expected_delivery_date='2026-09-29', supplier_date='2026-09-30')
        item = self.item()
        self.assertEqual(item.stock_status, IN_STOCK)
        self.assertTrue(item.delivered)
        self.assertEqual(item.actual_delivery_date, date(2026, 10, 1))
        self.post(action='update', item_id=item.id, stock_status=IN_STOCK, notes='Saved note')
        item = self.item()
        self.assertEqual(item.expected_delivery_date, date(2026, 9, 29))
        self.assertEqual(item.supplier_date, date(2026, 9, 30))
        self.assertEqual(item.actual_delivery_date, date(2026, 10, 1))
        self.assertTrue(item.delivered)
        self.assertEqual(CoachAudit.query.count(), 2)

    def test_update_delivery_and_stock_audit(self):
        self.post()
        self.post(action='update', item_id=self.item().id, actual_delivery_date='2026-10-02')
        audit = CoachAudit.query.order_by(CoachAudit.id.desc()).first()
        self.assertIn('Stock Status: Out of Stock -> In Stock', audit.details)
        self.assertIn('Delivered: No -> Yes', audit.details)
        self.assertIn('Actual Delivery Date: — -> 2026-10-02', audit.details)

    def test_correction_can_clear_receipt_explicitly(self):
        self.post(actual_delivery_date='2026-10-02')
        self.post(action='update', item_id=self.item().id, actual_delivery_date='')
        self.assertIsNone(self.item().actual_delivery_date)
        self.assertFalse(self.item().delivered)
        self.assertEqual(self.item().stock_status, OUT_OF_STOCK)

    def test_invalid_input_rejected_before_any_mutation(self):
        self.post(notes='original')
        for invalid in [dict(stock_status='unknown'), dict(stock_status=''), dict(actual_delivery_date='2026-02-30'),
                        dict(expected_delivery_date='oops'), dict(supplier_date='2026-1-1'),
                        dict(delivered='1')]:
            self.post(action='update', item_id=self.item().id, notes='changed', **invalid)
            self.assertEqual(self.item().notes, 'original')
            self.assertEqual(CoachAudit.query.count(), 1)

    def test_read_only_and_cross_coach_updates(self):
        self.post()
        self.assertEqual(self.client.post('/coaches/2/bom', data=dict(
            action='update', item_id=self.item().id, stock_status=IN_STOCK)).status_code, 404)
        g.pop('_login_user', None)
        with self.client.session_transaction() as session:
            session['_user_id'] = '2'
        self.post(action='update', item_id=self.item().id, stock_status=IN_STOCK)
        self.assertEqual(self.item().stock_status, OUT_OF_STOCK)

    def test_stock_dependency_has_no_pending_unknown_or_delay(self):
        self.post(stock_status=IN_STOCK)
        task = CompletionTask(id=1, coach_id=1, assigned_date=date(2026, 9, 1), expected_days=3)
        for expected in (None, date(2026, 8, 1)):
            item = self.item()
            item.expected_delivery_date = expected
            row = build_dependency_gantt([task], [item], {1: [item.id]})['rows'][0]
            self.assertEqual(row['delay'], 0)
            self.assertFalse(row['pending'])
            self.assertFalse(row['unknown'])
            self.assertEqual(row['shifted_start'], date(2026, 9, 1))
            self.assertFalse(any('awaiting' in bar['label'] for bar in row['bars']))

    def test_analytics_counts_stock_and_preserves_actual_monthly_history(self):
        self.post(stock_status=IN_STOCK, expected_delivery_date='2026-01-01', supplier_date='2026-02-01')
        with patch.dict(globals(), render_template=lambda name, **context: context):
            with app.test_request_context('/coach-bom-analytics'):
                context = coach_bom_analytics.__wrapped__()
        self.assertEqual(context['kpis']['in_stock'], 1)
        self.assertEqual(context['kpis']['delivered'], 0)
        self.assertEqual(context['kpis']['available'], 1)
        self.assertEqual(context['kpis']['overdue'], 0)
        self.assertEqual(context['production_risk_count'], 0)
        self.assertEqual(sum(context['supplier_actual']['actual']), 0)
        self.assertEqual(self.client.get('/coach-bom-analytics').status_code, 200)

    def test_analytics_all_components_reconcile_and_filters_are_disjoint(self):
        self.post(component='Stock', stock_status=IN_STOCK, quantity='20')
        self.post(component='Received', actual_delivery_date='2026-01-01')
        self.post(component='Missing dates')
        self.post(component='Late', expected_delivery_date='2000-01-01')
        db.session.add(CoachBOMItem(coach_id=2, component='Other', section='Paint', stock_status=IN_STOCK))
        db.session.commit()
        def context(query=''):
            with patch.dict(globals(), render_template=lambda name, **values: values):
                with app.test_request_context('/coach-bom-analytics' + query):
                    return coach_bom_analytics.__wrapped__()
        result = context('?coach_id=1')
        kpis = result['kpis']
        self.assertEqual(kpis['total'], 4)  # BOM records, not incompatible quantities.
        self.assertEqual(kpis['available'], 2)
        self.assertEqual(kpis['available_percent'], 50)
        for key in ('in_stock', 'delivered', 'outstanding', 'overdue'):
            self.assertEqual(kpis[key], 1)
            self.assertEqual(kpis[key + '_percent'], 25)
            filtered = context('?coach_id=1&status=' + key)['kpis']
            self.assertEqual(filtered['total'], 1)
            self.assertEqual(filtered[key], 1)
        self.assertEqual(context('?coach_id=1&status=available')['kpis']['total'], 2)
        for row in result['coach_performance'] + result['section_rows']:
            self.assertEqual(sum(row[key] for key in ('in_stock', 'delivered', 'outstanding', 'overdue')), row['total'])
        self.assertEqual(sum(result['supplier_actual']['actual']), 1)
        self.assertEqual(context('?section=Paint')['kpis']['in_stock'], 1)
        empty = context('?coach_id=999')['kpis']
        self.assertEqual(empty['in_stock_percent'], 0)
        self.assertEqual(empty['total'], 0)
        db.session.get(Coach, 2).archived = True
        db.session.commit()
        self.assertEqual(context()['kpis']['total'], 4)
        page = self.client.get('/coach-bom-analytics').data
        self.assertIn(b'In Stock (no delivery recorded)', page)
        self.assertIn(b'coachInStock', page)

    def test_stock_does_not_hide_another_pending_dependency(self):
        self.post(stock_status=IN_STOCK)
        stocked = self.item()
        pending = CoachBOMItem(id=99, coach_id=1, component='Glass', stock_status=OUT_OF_STOCK,
                               expected_delivery_date=date(2026, 10, 1))
        task = CompletionTask(id=1, coach_id=1, assigned_date=date(2026, 10, 1), expected_days=3)
        row = build_dependency_gantt([task], [stocked, pending], {1: [stocked.id, 99]},
                                     date(2026, 10, 5))['rows'][0]
        self.assertTrue(row['pending'])
        self.assertFalse(row['unknown'])
        self.assertEqual(row['delay'], 4)

    def test_csv_import_export_preserves_stock_without_fabricated_date(self):
        plan = build_plan('component,stock_status,delivered,actual_delivery_date\nSeats,In Stock,no,\nWindows,Out of Stock,no,2026-10-01\nOld stock,,yes,\n', section_catalog(), coach_id=1)
        self.assertEqual(plan['ready'], 3)
        with patch.dict(globals(), current_user=db.session.get(User, 1)):
            self.assertEqual(_save_bom_plan(plan), 3)
        items = CoachBOMItem.query.order_by(CoachBOMItem.id).all()
        self.assertTrue(all(i.stock_status == IN_STOCK for i in items))
        self.assertIsNone(items[0].actual_delivery_date)
        self.assertIsNone(items[2].actual_delivery_date)
        self.assertTrue(items[1].delivered)
        self.assertEqual(CoachAudit.query.count(), 3)
        response = self.client.get('/coaches/1/bom/export.csv')
        exported = list(csv.DictReader(io.StringIO(response.data.decode('utf-8-sig'))))
        self.assertTrue(all(i['stock_status'] == IN_STOCK for i in exported))
        preview = self.client.get('/coaches/1/bom')
        self.assertEqual(preview.status_code, 200)

    def test_bad_csv_stock_and_dates_cannot_be_saved(self):
        for raw in ['component,stock_status\nSeats,invalid\n',
                    'component,stock_status,actual_delivery_date\nSeats,In Stock,2026-02-30\n',
                    'component,stock_status,delivered\nSeats,Out of Stock,yes\n']:
            plan = build_plan(raw, section_catalog(), coach_id=1)
            self.assertEqual(plan['invalid'], 1)
            with self.assertRaises(ValueError):
                _save_bom_plan(plan)
        self.assertEqual(CoachBOMItem.query.count(), 0)

    def test_orm_delivery_invariant_and_status_constraint(self):
        item = CoachBOMItem(coach_id=1, component='Seat', actual_delivery_date=date(2026, 1, 1))
        db.session.add(item)
        db.session.commit()
        self.assertEqual(item.stock_status, IN_STOCK)
        self.assertTrue(item.delivered)
        with self.assertRaises(IntegrityError):
            db.session.execute(text("UPDATE coach_bom_item SET stock_status='bad'"))
        db.session.rollback()


class MigrationTests(unittest.TestCase):
    def test_failed_audit_rolls_back_schema_and_data(self):
        spec = importlib.util.spec_from_file_location('stock_migration', ROOT/'migrations/20261013_bom_stock.py')
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine('sqlite://')
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE coach (id INTEGER, coach_number TEXT)'))
            connection.execute(text("INSERT INTO coach VALUES (1, 'C1')"))
            connection.execute(text('''CREATE TABLE coach_bom_item (id INTEGER, coach_id INTEGER,
                delivered BOOLEAN, actual_delivery_date DATE)'''))
            connection.execute(text("INSERT INTO coach_bom_item VALUES (1,1,0,'2026-09-01')"))
        from sqlalchemy.exc import OperationalError
        with self.assertRaises(OperationalError):
            migration.migrate(engine)  # Missing audit table deliberately forces a rollback.
        with engine.connect() as connection:
            self.assertNotIn('stock_status', {c['name'] for c in inspect(connection).get_columns('coach_bom_item')})
            self.assertEqual(connection.scalar(text('SELECT delivered FROM coach_bom_item')), 0)
        engine.dispose()

    def test_preserves_dates_audits_and_is_repeatable(self):
        spec = importlib.util.spec_from_file_location('stock_migration', ROOT/'migrations/20261013_bom_stock.py')
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine('sqlite://')
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE coach (id INTEGER, coach_number TEXT)'))
            connection.execute(text('INSERT INTO coach VALUES (1, \'C1\')'))
            connection.execute(text('''CREATE TABLE coach_bom_item (id INTEGER, coach_id INTEGER,
                delivered BOOLEAN, actual_delivery_date DATE, expected_delivery_date DATE, notes TEXT)'''))
            connection.execute(text('''INSERT INTO coach_bom_item VALUES
                (1,1,0,NULL,'2026-09-01','pending'), (2,1,1,NULL,NULL,'legacy'),
                (3,1,0,'2026-09-03','2026-09-01','received')'''))
            connection.execute(text('''CREATE TABLE coach_audit (coach_id INTEGER, coach_number TEXT,
                action TEXT, changed_by TEXT, details TEXT, created_at DATETIME)'''))
        self.assertEqual(migration.migrate(engine), 3)
        self.assertEqual(migration.migrate(engine), 0)
        with engine.connect() as connection:
            rows = connection.execute(text('SELECT * FROM coach_bom_item ORDER BY id')).mappings().all()
            self.assertEqual([r['stock_status'] for r in rows], [OUT_OF_STOCK, IN_STOCK, IN_STOCK])
            self.assertIsNone(rows[1]['actual_delivery_date'])
            self.assertEqual(rows[2]['actual_delivery_date'], '2026-09-03')
            self.assertEqual(rows[2]['expected_delivery_date'], '2026-09-01')
            self.assertEqual(rows[2]['notes'], 'received')
            self.assertEqual(rows[2]['delivered'], 1)
            self.assertEqual(connection.scalar(text('SELECT COUNT(*) FROM coach_audit')), 3)
        engine.dispose()


if __name__ == '__main__':
    unittest.main()
