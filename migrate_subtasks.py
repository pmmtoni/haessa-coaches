# -*- coding: utf-8 -*-
"""
Created on Sun Feb  1 22:52:39 2026

@author: pmmto
"""

# migrate_subtasks.py
from app import app, db

def migrate_columns():
    with app.app_context():
        conn = db.engine.connect()
        columns_to_add = [
            ("stripping_task_inspection",     "BOOLEAN DEFAULT FALSE"),
            ("stripping_task_cleaning",       "BOOLEAN DEFAULT FALSE"),
            ("stripping_task_documentation",  "BOOLEAN DEFAULT FALSE"),
            ("stripping_task_approval",       "BOOLEAN DEFAULT FALSE"),
            ("complete_task_testing",         "BOOLEAN DEFAULT FALSE"),
            ("complete_task_certification",   "BOOLEAN DEFAULT FALSE"),
            ("serviceworthy_task_maintenance", "BOOLEAN DEFAULT FALSE"),
            ("serviceworthy_task_safety_check", "BOOLEAN DEFAULT FALSE"),
            ("retention_task_storage_prep",   "BOOLEAN DEFAULT FALSE"),
            ("retention_task_final_audit",    "BOOLEAN DEFAULT FALSE"),
        ]

        for col_name, col_type in columns_to_add:
            try:
                conn.execute(db.text(f"ALTER TABLE coach ADD COLUMN {col_name} {col_type}"))
                print(f"Added column: {col_name}")
            except Exception as e:
                if "duplicate column name" in str(e):
                    print(f"Column already exists: {col_name}")
                else:
                    print(f"Error adding {col_name}: {e}")

        conn.commit()
        print("Migration complete.")

if __name__ == "__main__":
    migrate_columns()