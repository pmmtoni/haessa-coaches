from app import app, db
from models import TaskTemplate
import csv

with app.app_context():
    rows = TaskTemplate.query.order_by(
        TaskTemplate.coach_type,
        TaskTemplate.phase,
        TaskTemplate.section,
        TaskTemplate.sort_order
    ).all()

    with open("task_template_backup.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)

        writer.writerow([
            "coach_type",
            "phase",
            "section",
            "task",
            "hours",
            "sort_order",
            "is_active"
        ])

        for r in rows:
            writer.writerow([
                r.coach_type,
                r.phase,
                r.section,
                r.task,
                r.hours,
                r.sort_order,
                r.is_active
            ])

print("Backup complete.")