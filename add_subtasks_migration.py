# -*- coding: utf-8 -*-
"""
Created on Thu Feb  5 20:24:51 2026

@author: pmmto
"""

# add_subtasks_migration.py
from app import app, db

def add_missing_columns():
    with app.app_context():
        conn = db.engine.connect()
        
        # List of sub-task columns to add (BOOLEAN DEFAULT FALSE)
        columns = [
            "stripping_task_inspection",
            "stripping_task_cleaning",
            "stripping_task_documentation",
            "stripping_task_approval",
            "complete_task_testing",
            "complete_task_certification",
            "serviceworthy_task_maintenance",
            "serviceworthy_task_safety_check",
            "retention_task_storage_prep",
            "retention_task_final_audit",
        ]

        for col in columns:
            try:
                conn.execute(db.text(f"ALTER TABLE coach ADD COLUMN {col} BOOLEAN DEFAULT FALSE"))
                print(f"Added column: {col}")
            except Exception as e:
                if "duplicate column name" in str(e):
                    print(f"Column already exists: {col} (skipping)")
                else:
                    print(f"Error adding {col}: {e}")

        conn.commit()
        print("Migration finished. All columns added or already present.")

if __name__ == "__main__":
    add_missing_columns()