# -*- coding: utf-8 -*-
"""
Created on Sun Mar  8 18:22:41 2026

@author: pmmto
"""


import csv
from datetime import datetime
from app import app, db
from models import Coach, CompletionTask

def import_tasks_from_csv(csv_file_path):
    with app.app_context():
        added = 0
        skipped = 0
        errors = []
        debug_rows = []  # collect first 5 rows for debugging

        with open(csv_file_path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            row_num = 1
            for row in reader:
                row_num += 1

                # Debug: save first few rows
                if row_num <= 5:
                    debug_rows.append(row)

                coach_number = row.get('coach_number', '').strip()
                if not coach_number:
                    errors.append(f"Row {row_num}: Missing coach_number")
                    continue

                coach = Coach.query.filter_by(coach_number=coach_number).first()
                if not coach:
                    errors.append(f"Row {row_num}: Coach not found - {coach_number}")
                    continue

                task_name = row.get('task', '').strip()
                if not task_name:
                    errors.append(f"Row {row_num}: Missing task name for {coach_number}")
                    continue

                # TEMPORARILY DISABLE duplicate check for testing
                # existing = CompletionTask.query.filter_by(coach_id=coach.id, task=task_name).first()
                # if existing:
                #     skipped += 1
                #     continue

                # Read fields
                phase = row.get('phase', 'Completion').strip()
                section = row.get('section', 'Unknown').strip()  # fallback if blank
                coach_type = row.get('coach_type', coach.coach_type).strip()

                # Debug: log section value
                if row_num <= 5:
                    print(f"Row {row_num} - section: '{section}'")

                completed_str = row.get('completed', 'FALSE').strip().upper()
                completed = completed_str in ['TRUE', 'YES', '1', 'T']

                completed_date = None
                date_str = row.get('completed_date', '').strip()
                if date_str:
                    try:
                        completed_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    except ValueError:
                        errors.append(f"Row {row_num}: Invalid date '{date_str}'")

                hours_str = row.get('hours', '0').strip()
                try:
                    hours = float(hours_str)
                except ValueError:
                    hours = 0.0
                    errors.append(f"Row {row_num}: Invalid hours '{hours_str}'")

                new_task = CompletionTask(
                    coach_id=coach.id,
                    coach_no=coach.coach_number,
                    coach_type=coach_type,
                    phase=phase,
                    section=section,
                    task=task_name,
                    hours=hours,
                    completed=completed,
                    completed_date=completed_date
                )

                db.session.add(new_task)
                added += 1
                print(f"Added task: '{task_name}' (Phase: {phase}, Section: {section}) for {coach_number}")

        db.session.commit()

        print(f"\n=== Import finished ===")
        print(f"Added tasks     : {added}")
        print(f"Skipped (duplicates): {skipped}")
        if errors:
            print("\nErrors:")
            for e in errors:
                print(e)
        else:
            print("No errors!")

        # Debug: show first 5 rows read from CSV
        print("\nFirst 5 rows from CSV (for debug):")
        for row in debug_rows:
            print(row)

if __name__ == "__main__":
    csv_file = "completion_tasks_10M50083M_new.csv"  # your file name
    import_tasks_from_csv(csv_file)