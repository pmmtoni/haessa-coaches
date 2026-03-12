# -*- coding: utf-8 -*-
"""
Created on Mon Mar  9 22:10:40 2026

@author: pmmto
"""

from app import app, db
from models import CompletionTask
from sqlalchemy import func

with app.app_context():
    # Find duplicates (same coach_id + task)
    duplicates = db.session.query(
        CompletionTask.coach_id,
        CompletionTask.task,
        func.count(CompletionTask.id).label('count')
    ).group_by(
        CompletionTask.coach_id,
        CompletionTask.task
    ).having(
        func.count(CompletionTask.id) > 1
    ).all()

    deleted = 0
    for dup in duplicates:
        # Get all matching tasks, sorted by ID (oldest first)
        tasks = CompletionTask.query.filter_by(
            coach_id=dup.coach_id,
            task=dup.task
        ).order_by(CompletionTask.id).all()

        # Keep the first (oldest), delete the rest
        for task in tasks[1:]:
            db.session.delete(task)
            deleted += 1

    db.session.commit()
    print(f"Cleanup complete: deleted {deleted} duplicate tasks")