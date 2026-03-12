# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 10:08:21 2026

@author: Paul Mmtoni Barasa
"""

# add_stripping_subtasks.py
from app import app, db

def add_columns():
    with app.app_context():
        conn = db.engine.connect()

        columns = [
            "stripping_task_bogie",
            "stripping_task_underframe",
            "stripping_task_plumbing_piping",
            "stripping_task_interior",
            "stripping_task_exterior",
            "stripping_task_roof",
            "stripping_task_components",
            "stripping_task_wiring",
            "stripping_task_sole_bar",
            "stripping_task_bogie_frame",
            "stripping_task_loose_components",
        ]

        for col in columns:
            try:
                conn.execute(db.text(f"ALTER TABLE coach ADD COLUMN {col} BOOLEAN DEFAULT FALSE"))
                print(f"Added column: {col}")
            except Exception as e:
                if "duplicate column name" in str(e):
                    print(f"Already exists: {col}")
                else:
                    print(f"Error on {col}: {e}")

        conn.commit()
        print("All columns processed.")

if __name__ == "__main__":
    add_columns()