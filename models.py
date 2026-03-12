# -*- coding: utf-8 -*-
"""
Created on Sun Mar  8 18:34:36 2026

@author: pmmto
"""
# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin   # ← ADD THIS LINE
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default="viewer")

    def set_password(self, raw):
        self.password = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password, raw)

class Coach(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    coach_number = db.Column(db.String(50), unique=True, nullable=False)
    coach_type = db.Column(db.String(100), nullable=False)

    stripping = db.Column(db.Boolean, default=False)
    stripping_date = db.Column(db.Date, nullable=True)

    complete = db.Column(db.Boolean, default=False)
    completion_date = db.Column(db.Date, nullable=True)

    serviceworthy = db.Column(db.Boolean, default=False)
    serviceworthy_date = db.Column(db.Date, nullable=True)

    retention = db.Column(db.Boolean, default=False)
    retention_date = db.Column(db.Date, nullable=True)

    due_date = db.Column(db.Date, nullable=True)

    # Stripping sub-tasks
    stripping_task_bogie = db.Column(db.Boolean, default=False)
    stripping_task_underframe = db.Column(db.Boolean, default=False)
    stripping_task_plumbing_piping = db.Column(db.Boolean, default=False)
    stripping_task_interior = db.Column(db.Boolean, default=False)
    stripping_task_exterior = db.Column(db.Boolean, default=False)
    stripping_task_roof = db.Column(db.Boolean, default=False)
    stripping_task_components = db.Column(db.Boolean, default=False)
    stripping_task_wiring = db.Column(db.Boolean, default=False)
    stripping_task_sole_bar = db.Column(db.Boolean, default=False)
    stripping_task_bogie_frame = db.Column(db.Boolean, default=False)
    stripping_task_loose_components = db.Column(db.Boolean, default=False)
    stripping_task_inspection = db.Column(db.Boolean, default=False)
    stripping_task_cleaning = db.Column(db.Boolean, default=False)
    stripping_task_documentation = db.Column(db.Boolean, default=False)
    stripping_task_approval = db.Column(db.Boolean, default=False)

    # Serviceworthy sub-tasks
    serviceworthy_task_maintenance = db.Column(db.Boolean, default=False)
    serviceworthy_task_safety_check = db.Column(db.Boolean, default=False)

    # Retention sub-tasks
    retention_task_storage_prep = db.Column(db.Boolean, default=False)
    retention_task_final_audit = db.Column(db.Boolean, default=False)

    # New simplified Serviceworthy questions
    serviceworthy_ncr_gc = db.Column(db.Boolean, default=False)
    serviceworthy_certificate = db.Column(db.Boolean, default=False)

    # New simplified Retention questions
    retention_ncr_gc = db.Column(db.Boolean, default=False)
    retention_certificate = db.Column(db.Boolean, default=False)

    # Invoicing confirmation (record keeping only)
    invoice_stripping = db.Column(db.Boolean, default=False)
    invoice_completion = db.Column(db.Boolean, default=False)
    invoice_serviceworthy = db.Column(db.Boolean, default=False)
    invoice_retention = db.Column(db.Boolean, default=False)
    invoice_current_escalation = db.Column(db.Boolean, default=False)


    notes = db.Column(db.Text, nullable=True)

    completion_tasks = db.relationship('CompletionTask', backref='coach', lazy=True)

    def __repr__(self):
        return f"<Coach {self.coach_number}>"

    def calculate_progress(self):
        is_trailer = self.coach_type.lower() == 'trailer'
        stripping_tasks = [
            self.stripping_task_bogie, self.stripping_task_underframe, self.stripping_task_plumbing_piping,
            self.stripping_task_interior, self.stripping_task_exterior, self.stripping_task_roof,
            self.stripping_task_components, self.stripping_task_wiring, self.stripping_task_sole_bar,
            self.stripping_task_bogie_frame, self.stripping_task_loose_components,
            self.stripping_task_inspection, self.stripping_task_cleaning,
            self.stripping_task_documentation, self.stripping_task_approval
        ]
        stripping_completed = sum(1 for t in stripping_tasks if t)
        stripping_progress = round((stripping_completed / 15) * 100, 1) if stripping_tasks else 0

        completion_tasks = self.completion_tasks
        completion_total = len(completion_tasks)
        completion_completed = sum(1 for t in completion_tasks if t.completed)
        completion_progress = round((completion_completed / completion_total * 100), 1) if completion_total > 0 else 0

        serviceworthy_progress = 100 if self.serviceworthy else 0
        retention_progress = 100 if self.retention else 0 if not is_trailer else 0

        phases = [
            {'name': 'Stripping', 'progress': stripping_progress, 'completed': stripping_completed, 'total': 15},
            {'name': 'Completion', 'progress': completion_progress, 'completed': completion_completed, 'total': completion_total},
            {'name': 'Serviceworthy', 'progress': serviceworthy_progress, 'completed': 1 if self.serviceworthy else 0, 'total': 1},
        ]

        if not is_trailer:
            phases.append({'name': 'Retention', 'progress': retention_progress, 'completed': 1 if self.retention else 0, 'total': 1})

        total_progress = sum(p['progress'] for p in phases) / len(phases) if phases else 0

        if completion_total > 0 and completion_completed == completion_total and not self.complete:
            self.complete = True
            self.completion_date = datetime.now().date()

        return {
            'percentage': round(total_progress, 1),
            'phases': phases,
            'is_trailer': is_trailer,
            'final_stage': 'Serviceworthy' if is_trailer else 'Retention'
        }

class CompletionTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    coach_id = db.Column(db.Integer, db.ForeignKey('coach.id'), nullable=False)
    coach_no = db.Column(db.String(50), nullable=False)
    coach_type = db.Column(db.String(100), nullable=False)
    section = db.Column(db.String(100), nullable=False)
    phase = db.Column(db.String(100), nullable=False, default='Completion')  # default for existing rows
    task = db.Column(db.String(500), nullable=False)
    hours = db.Column(db.Float, default=0.0)
    completed = db.Column(db.Boolean, default=False)
    completed_date = db.Column(db.Date, nullable=True)
    phase = db.Column(db.String(100), nullable=False, default='Completion')    

    def __repr__(self):
        return f"<CompletionTask {self.coach_no} - {self.section} - {self.task}>"