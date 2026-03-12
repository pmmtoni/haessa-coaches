# -*- coding: utf-8 -*-
"""
Created on Sat Feb 14 15:04:37 2026

@author: pmmto
"""

# check_tasks_summary.py
from app import app, db, CompletionTask

with app.app_context():
    print("Summary of tasks by coach_no:")
    from sqlalchemy import func
    results = db.session.query(
        CompletionTask.coach_no,
        func.count(CompletionTask.id).label('task_count')
    ).group_by(CompletionTask.coach_no).all()

    for row in results:
        print(f"Coach {row.coach_no}: {row.task_count} tasks")