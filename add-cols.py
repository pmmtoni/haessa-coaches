# -*- coding: utf-8 -*-
"""
Created on Sun Feb  1 13:21:20 2026

@author: pmmto
"""

from app import app, db

with app.app_context():
    with db.engine.connect() as conn:
        # Add each new column one by one
        # If a column already exists → SQLite will raise an error, but you can just continue
        try:
            conn.execute(db.text("ALTER TABLE coach ADD COLUMN stripping_task_inspection BOOLEAN DEFAULT FALSE"))
            print("Added stripping_task_inspection")
        except Exception as e:
            print("stripping_task_inspection already exists or error:", e)

        try:
            conn.execute(db.text("ALTER TABLE coach ADD COLUMN stripping_task_cleaning BOOLEAN DEFAULT FALSE"))
            print("Added stripping_task_cleaning")
        except Exception as e:
            print("stripping_task_cleaning already exists or error:", e)

        # Repeat for all other columns...
        try:
            conn.execute(db.text("ALTER TABLE coach ADD COLUMN stripping_task_documentation BOOLEAN DEFAULT FALSE"))
            print("Added stripping_task_documentation")
        except Exception as e:
            print("stripping_task_documentation already exists or error:", e)

        try:
            conn.execute(db.text("ALTER TABLE coach ADD COLUMN stripping_task_approval BOOLEAN DEFAULT FALSE"))
            print("Added stripping_task_approval")
        except Exception as e:
            print("stripping_task_approval already exists or error:", e)

        try:
            conn.execute(db.text("ALTER TABLE coach ADD COLUMN complete_task_testing BOOLEAN DEFAULT FALSE"))
            print("Added complete_task_testing")
        except Exception as e:
            print("complete_task_testing already exists or error:", e)

        try:
            conn.execute(db.text("ALTER TABLE coach ADD COLUMN complete_task_certification BOOLEAN DEFAULT FALSE"))
            print("Added complete_task_certification")
        except Exception as e:
            print("complete_task_certification already exists or error:", e)

        try:
            conn.execute(db.text("ALTER TABLE coach ADD COLUMN serviceworthy_task_maintenance BOOLEAN DEFAULT FALSE"))
            print("Added serviceworthy_task_maintenance")
        except Exception as e:
            print("serviceworthy_task_maintenance already exists or error:", e)

        try:
            conn.execute(db.text("ALTER TABLE coach ADD COLUMN serviceworthy_task_safety_check BOOLEAN DEFAULT FALSE"))
            print("Added serviceworthy_task_safety_check")
        except Exception as e:
            print("serviceworthy_task_safety_check already exists or error:", e)

        try:
            conn.execute(db.text("ALTER TABLE coach ADD COLUMN retention_task_storage_prep BOOLEAN DEFAULT FALSE"))
            print("Added retention_task_storage_prep")
        except Exception as e:
            print("retention_task_storage_prep already exists or error:", e)

        try:
            conn.execute(db.text("ALTER TABLE coach ADD COLUMN retention_task_final_audit BOOLEAN DEFAULT FALSE"))
            print("Added retention_task_final_audit")
        except Exception as e:
            print("retention_task_final_audit already exists or error:", e)

        conn.commit()  # Important: commit the changes