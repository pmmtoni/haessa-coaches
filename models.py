from collections import defaultdict
from datetime import datetime, timedelta

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(db.Model, UserMixin):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="viewer")

    def set_password(self, raw_password):
        self.password = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password, raw_password)

    def __repr__(self):
        return f"<User {self.username}>"


class Coach(db.Model):
    __tablename__ = "coach"

    id = db.Column(db.Integer, primary_key=True)
    coach_number = db.Column(db.String(50), unique=True, nullable=False)
    coach_type = db.Column(db.String(100), nullable=False)

    # Milestone flags
    stripping = db.Column(db.Boolean, default=False)
    complete = db.Column(db.Boolean, default=False)
    serviceworthy = db.Column(db.Boolean, default=False)
    retention = db.Column(db.Boolean, default=False)

    # Manual milestone/certificate fields
    stripping_certificate_issued = db.Column(db.Boolean, default=False)
    stripping_date = db.Column(db.Date, nullable=True)

    completion_certificate_issued = db.Column(db.Boolean, default=False)
    completion_date = db.Column(db.Date, nullable=True)

    serviceworthy_date = db.Column(db.Date, nullable=True)
    retention_date = db.Column(db.Date, nullable=True)

    due_date = db.Column(db.Date, nullable=True)

    # NCR / GC
    ncr = db.Column(db.Boolean, default=False)
    gc = db.Column(db.Boolean, default=False)
    ncr_gc_cleared_date = db.Column(db.Date, nullable=True)

    # Legacy fields kept for DB compatibility
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

    serviceworthy_task_maintenance = db.Column(db.Boolean, default=False)
    serviceworthy_task_safety_check = db.Column(db.Boolean, default=False)

    retention_task_storage_prep = db.Column(db.Boolean, default=False)
    retention_task_final_audit = db.Column(db.Boolean, default=False)

    serviceworthy_ncr_gc = db.Column(db.Boolean, default=False)
    serviceworthy_certificate = db.Column(db.Boolean, default=False)

    retention_ncr_gc = db.Column(db.Boolean, default=False)
    retention_certificate = db.Column(db.Boolean, default=False)

    # Invoicing
    invoice_stripping = db.Column(db.Boolean, default=False)
    invoice_completion = db.Column(db.Boolean, default=False)
    invoice_serviceworthy = db.Column(db.Boolean, default=False)
    invoice_retention = db.Column(db.Boolean, default=False)
    invoice_current_escalation = db.Column(db.Boolean, default=False)

    notes = db.Column(db.Text, nullable=True)

    completion_tasks = db.relationship(
        "CompletionTask",
        backref="coach",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="CompletionTask.phase, CompletionTask.section, CompletionTask.id",
    )

    def __repr__(self):
        return f"<Coach {self.coach_number}>"

    def _tasks_for_phase(self, phase_name):
        phase_key = (phase_name or "").strip().lower()
        return [
            task
            for task in self.completion_tasks
            if (task.phase or "").strip().lower() == phase_key
        ]

    def _is_trailer(self):
        return (self.coach_type or "").strip().lower() == "trailer"

    def sync_active_phase_status(self):
        """
        Active phases are based on task completion, but manual certificate/date
        milestones can also preserve milestone status.
        """
        today = datetime.now().date()

        stripping_tasks = self._tasks_for_phase("Stripping")
        completion_tasks = self._tasks_for_phase("Completion")

        if stripping_tasks:
            stripping_complete = all(task.completed for task in stripping_tasks)
            self.stripping = stripping_complete or bool(self.stripping_certificate_issued)
        else:
            self.stripping = bool(self.stripping_certificate_issued)

        if self.stripping and not self.stripping_date:
            self.stripping_date = today

        if completion_tasks:
            completion_complete = all(task.completed for task in completion_tasks)
            self.complete = completion_complete or bool(self.completion_certificate_issued)
        else:
            self.complete = bool(self.completion_certificate_issued)

        if self.complete and not self.completion_date:
            self.completion_date = today

    def sync_passive_status(self):
        """
        Serviceworthy is manual and independent from completion.
        Retention is triggered by serviceworthy_date + 14 days, except for trailers.
        If NCR/GC is open, retention waits for ncr_gc_cleared_date.
        """
        today = datetime.now().date()

        self.serviceworthy = bool(self.serviceworthy_date)

        if self._is_trailer():
            self.retention = False
            self.retention_date = None
            return

        if not self.serviceworthy_date:
            self.retention = False
            self.retention_date = None
            return

        base_retention_date = self.serviceworthy_date + timedelta(days=14)

        if self.ncr or self.gc:
            if self.ncr_gc_cleared_date:
                self.retention_date = max(base_retention_date, self.ncr_gc_cleared_date)
                self.retention = self.retention_date <= today
            else:
                self.retention = False
                self.retention_date = None
        else:
            self.retention_date = base_retention_date
            self.retention = self.retention_date <= today

    def sync_all_status(self):
        self.sync_active_phase_status()
        self.sync_passive_status()

    def calculate_progress(self):
        self.sync_all_status()
        tasks = self.completion_tasks or []

        if not tasks:
            return {
                "overall_percent": 0.0,
                "percentage": 0.0,
                "total_completed": 0,
                "total_tasks": 0,
                "phases": [],
                "serviceworthy": self.serviceworthy,
                "serviceworthy_date": self.serviceworthy_date,
                "retention": self.retention,
                "retention_date": self.retention_date,
                "completion_certificate_issued": self.completion_certificate_issued,
                "completion_date": self.completion_date,
                "ncr": self.ncr,
                "gc": self.gc,
                "ncr_gc_cleared_date": self.ncr_gc_cleared_date,
                "final_stage": "Serviceworthy" if self._is_trailer() else "Retention",
            }

        phase_map = defaultdict(list)
        for task in tasks:
            phase_name = (task.phase or "Unassigned").strip() or "Unassigned"
            phase_map[phase_name].append(task)

        preferred_phase_order = ["Stripping", "Completion", "Serviceworthy", "Retention"]

        sorted_phase_names = sorted(
            phase_map.keys(),
            key=lambda phase: (
                preferred_phase_order.index(phase)
                if phase in preferred_phase_order
                else len(preferred_phase_order),
                phase,
            ),
        )

        phases_output = []
        total_completed = 0
        total_tasks = 0

        for phase_name in sorted_phase_names:
            phase_tasks = phase_map[phase_name]

            section_map = defaultdict(list)
            for task in phase_tasks:
                section_name = (task.section or "General").strip() or "General"
                section_map[section_name].append(task)

            phase_total = len(phase_tasks)
            phase_completed = sum(1 for task in phase_tasks if task.completed)
            phase_progress = round((phase_completed / phase_total) * 100, 1) if phase_total else 0.0
            phase_hours_total = round(sum((task.hours or 0.0) for task in phase_tasks), 1)

            section_rows = []
            for section_name in sorted(section_map.keys()):
                section_tasks = section_map[section_name]
                section_total = len(section_tasks)
                section_completed = sum(1 for task in section_tasks if task.completed)
                section_progress = round((section_completed / section_total) * 100, 1) if section_total else 0.0
                section_hours_total = round(sum((task.hours or 0.0) for task in section_tasks), 1)

                section_rows.append(
                    {
                        "name": section_name,
                        "completed": section_completed,
                        "total": section_total,
                        "progress": section_progress,
                        "hours_total": section_hours_total,
                    }
                )

            phases_output.append(
                {
                    "name": phase_name,
                    "completed": phase_completed,
                    "total": phase_total,
                    "progress": phase_progress,
                    "hours_total": phase_hours_total,
                    "sections": section_rows,
                }
            )

            total_completed += phase_completed
            total_tasks += phase_total

        overall_percent = round((total_completed / total_tasks) * 100, 1) if total_tasks else 0.0

        return {
            "overall_percent": overall_percent,
            "percentage": overall_percent,
            "total_completed": total_completed,
            "total_tasks": total_tasks,
            "phases": phases_output,
            "serviceworthy": self.serviceworthy,
            "serviceworthy_date": self.serviceworthy_date,
            "retention": self.retention,
            "retention_date": self.retention_date,
            "completion_certificate_issued": self.completion_certificate_issued,
            "completion_date": self.completion_date,
            "ncr": self.ncr,
            "gc": self.gc,
            "ncr_gc_cleared_date": self.ncr_gc_cleared_date,
            "final_stage": "Serviceworthy" if self._is_trailer() else "Retention",
        }


class CompletionTask(db.Model):
    __tablename__ = "completion_task"

    id = db.Column(db.Integer, primary_key=True)
    coach_id = db.Column(db.Integer, db.ForeignKey("coach.id"), nullable=False)
    coach_no = db.Column(db.String(50), nullable=False)
    coach_type = db.Column(db.String(100), nullable=False)
    section = db.Column(db.String(100), nullable=False)
    phase = db.Column(db.String(100), nullable=False, default="Completion")
    task = db.Column(db.String(500), nullable=False)
    hours = db.Column(db.Float, default=0.0)
    completed = db.Column(db.Boolean, default=False)
    completed_date = db.Column(db.Date, nullable=True)

    def __repr__(self):
        return f"<CompletionTask {self.coach_no} - {self.phase} - {self.section} - {self.task}>"