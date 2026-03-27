# -*- coding: utf-8 -*-
"""
Created on Sun Mar 22 21:33:10 2026

@author: pmmto
"""

#import os
#import csv
from app import app, db, load_completion_task_templates
from models import Coach, CompletionTask

with app.app_context():
    added = 0
    skipped = 0

    for coach in Coach.query.all():
        existing = CompletionTask.query.filter_by(coach_id=coach.id).count()

        if existing > 0:
            skipped += 1
            print(f"Skipping {coach.coach_number}: already has {existing} tasks")
            continue

        template_tasks = load_completion_task_templates(coach.coach_type)

        if not template_tasks:
            print(f"⚠ No template found for {coach.coach_number} ({coach.coach_type})")
            continue

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
                completed_date=None,
            )
            db.session.add(task)
            added += 1

        print(f"Added {len(template_tasks)} tasks for {coach.coach_number}")

    db.session.commit()
    print(f"\n✅ Total tasks added: {added}")
    print(f"✅ Coaches skipped (already had tasks): {skipped}")