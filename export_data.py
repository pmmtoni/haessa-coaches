import json
from app import app
from models import Coach, User, CompletionTask

def serialize_date(value):
    return value.isoformat() if value else None

def serialize_user(u):
    return {
        "username": u.username,
        "password": u.password,   # hashed password string
        "role": u.role,
    }

def serialize_completion_task(t):
    return {
        "coach_no": t.coach_no,
        "coach_type": t.coach_type,
        "section": t.section,
        "phase": t.phase,
        "task": t.task,
        "hours": t.hours,
        "completed": t.completed,
        "completed_date": serialize_date(t.completed_date),
    }

def serialize_coach(c):
    return {
        "coach_number": c.coach_number,
        "coach_type": c.coach_type,

        "stripping": c.stripping,
        "stripping_date": serialize_date(c.stripping_date),

        "complete": c.complete,
        "completion_date": serialize_date(c.completion_date),

        "serviceworthy": c.serviceworthy,
        "serviceworthy_date": serialize_date(c.serviceworthy_date),

        "retention": c.retention,
        "retention_date": serialize_date(c.retention_date),

        "due_date": serialize_date(c.due_date),

        "stripping_task_bogie": c.stripping_task_bogie,
        "stripping_task_underframe": c.stripping_task_underframe,
        "stripping_task_plumbing_piping": c.stripping_task_plumbing_piping,
        "stripping_task_interior": c.stripping_task_interior,
        "stripping_task_exterior": c.stripping_task_exterior,
        "stripping_task_roof": c.stripping_task_roof,
        "stripping_task_components": c.stripping_task_components,
        "stripping_task_wiring": c.stripping_task_wiring,
        "stripping_task_sole_bar": c.stripping_task_sole_bar,
        "stripping_task_bogie_frame": c.stripping_task_bogie_frame,
        "stripping_task_loose_components": c.stripping_task_loose_components,
        "stripping_task_inspection": c.stripping_task_inspection,
        "stripping_task_cleaning": c.stripping_task_cleaning,
        "stripping_task_documentation": c.stripping_task_documentation,
        "stripping_task_approval": c.stripping_task_approval,

        "serviceworthy_task_maintenance": c.serviceworthy_task_maintenance,
        "serviceworthy_task_safety_check": c.serviceworthy_task_safety_check,

        "retention_task_storage_prep": c.retention_task_storage_prep,
        "retention_task_final_audit": c.retention_task_final_audit,

        "serviceworthy_ncr_gc": c.serviceworthy_ncr_gc,
        "serviceworthy_certificate": c.serviceworthy_certificate,

        "retention_ncr_gc": c.retention_ncr_gc,
        "retention_certificate": c.retention_certificate,

        "invoice_stripping": c.invoice_stripping,
        "invoice_completion": c.invoice_completion,
        "invoice_serviceworthy": c.invoice_serviceworthy,
        "invoice_retention": c.invoice_retention,
        "invoice_current_escalation": c.invoice_current_escalation,

        "notes": c.notes,

        "completion_tasks": [serialize_completion_task(t) for t in c.completion_tasks],
    }

with app.app_context():
    data = {
        "users": [serialize_user(u) for u in User.query.all()],
        "coaches": [serialize_coach(c) for c in Coach.query.all()],
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"✅ Exported {len(data['users'])} users and {len(data['coaches'])} coaches to data.json")