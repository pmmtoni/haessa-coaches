# -*- coding: utf-8 -*-
"""
Created on Wed Mar 25 10:15:56 2026

@author: pmmto
"""

from app import app, db, load_task_templates
from models import Coach, CompletionTask

with app.app_context():
    coaches = Coach.query.all()

    for coach in coaches:
        template_tasks = load_task_templates(coach.coach_type)

        existing_keys = {
            (
                (t.phase or "").strip(),
                (t.section or "").strip(),
                (t.task or "").strip()
            )
            for t in coach.completion_tasks
        }

        added = 0
        for item in template_tasks:
            key = (
                (item["phase"] or "").strip(),
                (item["section"] or "").strip(),
                (item["task"] or "").strip()
            )

            if key in existing_keys:
                continue

            db.session.add(CompletionTask(
                coach_id=coach.id,
                coach_no=coach.coach_number,
                coach_type=coach.coach_type,
                phase=item["phase"],
                section=item["section"],
                task=item["task"],
                hours=item["hours"],
                completed=False,
                completed_date=None,
            ))
            added += 1

        print(f"{coach.coach_number}: added {added} missing tasks")

    db.session.commit()
    print("Backfill complete.")