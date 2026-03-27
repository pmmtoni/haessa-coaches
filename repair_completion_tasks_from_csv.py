# -*- coding: utf-8 -*-
"""
Created on Sun Mar 22 20:08:50 2026

@author: pmmto
"""

import csv
from pathlib import Path
from app import app, db
from models import Coach, CompletionTask

def load_completion_task_templates(coach_type):
    template_path = Path(app.root_path) / "completion_task_templates.csv"
    tasks = []

    if not template_path.exists():
        print(f"Template file not found: {template_path}")
        return tasks

    with open(template_path, newline="", encoding="utf-8-sig") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            row_coach_type = (row.get("coach_type") or "").strip()
            if row_coach_type.lower() == coach_type.strip().lower():
                tasks.append({
                    "phase": (row.get("phase") or "Completion").strip(),
                    "section": (row.get("section") or "").strip(),
                    "task": (row.get("task") or "").strip(),
                    "hours": float(row.get("hours") or 0),
                })

    return tasks

with app.app_context():
    coaches = Coach.query.all()
    total_added = 0

    for coach in coaches:
        existing_count = CompletionTask.query.filter_by(coach_id=coach.id).count()
        if existing_count > 0:
            continue

        template_tasks = load_completion_task_templates(coach.coach_type)

        for item in template_tasks:
            task = CompletionTask(
                coach_id=coach.id,
                coach_no=coach.coach_number,
                coach_type=coach.coach_type,
                phase=item["phase"],
                section=item["section"],
                task=item["task"],
                hours=item["hours"],
                completed=False,
                completed_date=None
            )
            db.session.add(task)
            total_added += 1

    db.session.commit()
    print(f"✅ Added {total_added} completion tasks")