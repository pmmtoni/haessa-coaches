# -*- coding: utf-8 -*-
"""
Created on Sun Mar 22 21:52:45 2026

@author: pmmto
"""

from app import app, db, load_completion_task_templates
from models import Coach, CompletionTask

COACH_NUMBER = "25 714"

with app.app_context():
    coach = Coach.query.filter_by(coach_number=COACH_NUMBER).first()

    if not coach:
        print(f"❌ Coach {COACH_NUMBER} not found")
    else:
        existing = CompletionTask.query.filter_by(coach_id=coach.id).count()
        print(f"Existing tasks for {coach.coach_number}: {existing}")

        if existing > 0:
            print("⚠ Coach already has tasks, skipping")
        else:
            template_tasks = load_completion_task_templates(coach.coach_type)

            if not template_tasks:
                print(f"❌ No template found for coach type '{coach.coach_type}'")
            else:
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

                db.session.commit()
                print(f"✅ Added {len(template_tasks)} tasks for {coach.coach_number}")  