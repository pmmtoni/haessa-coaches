# -*- coding: utf-8 -*-
"""
Created on Mon Mar  9 22:38:10 2026

@author: pmmto
"""

from app import app, db
from models import CompletionTask, Coach

coach_number = "10M50083M"

with app.app_context():
    coach = Coach.query.filter_by(coach_number=coach_number).first()
    if not coach:
        print(f"Coach {coach_number} not found")
    else:
        tasks = CompletionTask.query.filter_by(coach_id=coach.id).all()
        count = len(tasks)
        if count == 0:
            print("No Completion tasks to delete")
        else:
            for task in tasks:
                db.session.delete(task)
            db.session.commit()
            print(f"Deleted {count} Completion tasks for {coach_number}")