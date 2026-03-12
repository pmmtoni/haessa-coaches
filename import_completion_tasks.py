# -*- coding: utf-8 -*-
"""
Created on Thu Feb 12 23:03:29 2026

@author: pmmto
"""

# import_completion_tasks.py
import pandas as pd
from app import app, db, CompletionTask, Coach

# Correct path (paste exactly as shown)
excel_file = r"G:\My Drive\CTE Durban-coaches\completion_tasks.xlsx"

with app.app_context():
    df = pd.read_excel(excel_file)

    # Normalize column names
    df.columns = [col.strip().lower().replace(' ', '_') for col in df.columns]

    required = ['coach_no', 'coach_type', 'section', 'task', 'hours']
    missing = [col for col in required if col not in df.columns]
    if missing:
        print(f"Missing columns: {missing}")
        exit()

    added = 0
    skipped = 0

    for _, row in df.iterrows():
        coach_no = str(row['coach_no']).strip()
        task_name = row['task'].strip()

        coach = Coach.query.filter_by(coach_number=coach_no).first()
        if not coach:
            print(f"Coach {coach_no} not found – skipping '{task_name}'")
            skipped += 1
            continue

        existing = CompletionTask.query.filter_by(
            coach_id=coach.id,
            task=task_name
        ).first()
        if existing:
            skipped += 1
            continue

        new_task = CompletionTask(
            coach_id=coach.id,
            coach_no=coach_no,
            coach_type=row['coach_type'].strip(),
            section=row['section'].strip(),
            task=task_name,
            hours=float(row['hours']) if pd.notna(row['hours']) else 0.0
        )
        db.session.add(new_task)
        added += 1

    db.session.commit()
    print(f"Imported {added} new completion tasks. Skipped {skipped} (already exist or coach missing).")