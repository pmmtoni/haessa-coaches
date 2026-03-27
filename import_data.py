import json
from datetime import datetime
from app import app, db
from models import Coach, User, CompletionTask

def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None

with app.app_context():
    with open("data.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    # Import users
    for u in data.get("users", []):
        existing_user = User.query.filter_by(username=u["username"]).first()
        if existing_user:
            continue

        user = User(
            username=u["username"],
            password=u["password"],   # already hashed from SQLite
            role=u.get("role", "viewer"),
        )
        db.session.add(user)

    db.session.commit()

    # Import coaches + completion tasks
    for c in data.get("coaches", []):
        existing_coach = Coach.query.filter_by(coach_number=c["coach_number"]).first()
        if existing_coach:
            continue

        coach = Coach(
            coach_number=c["coach_number"],
            coach_type=c["coach_type"],

            stripping=c.get("stripping", False),
            stripping_date=parse_date(c.get("stripping_date")),

            complete=c.get("complete", False),
            completion_date=parse_date(c.get("completion_date")),

            serviceworthy=c.get("serviceworthy", False),
            serviceworthy_date=parse_date(c.get("serviceworthy_date")),

            retention=c.get("retention", False),
            retention_date=parse_date(c.get("retention_date")),

            due_date=parse_date(c.get("due_date")),

            stripping_task_bogie=c.get("stripping_task_bogie", False),
            stripping_task_underframe=c.get("stripping_task_underframe", False),
            stripping_task_plumbing_piping=c.get("stripping_task_plumbing_piping", False),
            stripping_task_interior=c.get("stripping_task_interior", False),
            stripping_task_exterior=c.get("stripping_task_exterior", False),
            stripping_task_roof=c.get("stripping_task_roof", False),
            stripping_task_components=c.get("stripping_task_components", False),
            stripping_task_wiring=c.get("stripping_task_wiring", False),
            stripping_task_sole_bar=c.get("stripping_task_sole_bar", False),
            stripping_task_bogie_frame=c.get("stripping_task_bogie_frame", False),
            stripping_task_loose_components=c.get("stripping_task_loose_components", False),
            stripping_task_inspection=c.get("stripping_task_inspection", False),
            stripping_task_cleaning=c.get("stripping_task_cleaning", False),
            stripping_task_documentation=c.get("stripping_task_documentation", False),
            stripping_task_approval=c.get("stripping_task_approval", False),

            serviceworthy_task_maintenance=c.get("serviceworthy_task_maintenance", False),
            serviceworthy_task_safety_check=c.get("serviceworthy_task_safety_check", False),

            retention_task_storage_prep=c.get("retention_task_storage_prep", False),
            retention_task_final_audit=c.get("retention_task_final_audit", False),

            serviceworthy_ncr_gc=c.get("serviceworthy_ncr_gc", False),
            serviceworthy_certificate=c.get("serviceworthy_certificate", False),

            retention_ncr_gc=c.get("retention_ncr_gc", False),
            retention_certificate=c.get("retention_certificate", False),

            invoice_stripping=c.get("invoice_stripping", False),
            invoice_completion=c.get("invoice_completion", False),
            invoice_serviceworthy=c.get("invoice_serviceworthy", False),
            invoice_retention=c.get("invoice_retention", False),
            invoice_current_escalation=c.get("invoice_current_escalation", False),

            notes=c.get("notes"),
        )

        db.session.add(coach)
        db.session.flush()  # get coach.id before adding tasks

        for t in c.get("completion_tasks", []):
            task = CompletionTask(
                coach_id=coach.id,
                coach_no=t["coach_no"],
                coach_type=t["coach_type"],
                section=t["section"],
                phase=t.get("phase", "Completion"),
                task=t["task"],
                hours=t.get("hours", 0.0),
                completed=t.get("completed", False),
                completed_date=parse_date(t.get("completed_date")),
            )
            db.session.add(task)

    db.session.commit()
    print("✅ Data imported into PostgreSQL")