# -*- coding: utf-8 -*-
"""
Created on Thu Mar  5 20:29:51 2026

@author: pmmto
"""

from app import app, db

with app.app_context():
    with db.engine.connect() as connection:
        try:
            connection.execute(db.text("ALTER TABLE coach ADD COLUMN serviceworthy_task_maintenance BOOLEAN DEFAULT FALSE"))
            connection.execute(db.text("ALTER TABLE coach ADD COLUMN serviceworthy_task_safety_check BOOLEAN DEFAULT FALSE"))
            connection.execute(db.text("ALTER TABLE coach ADD COLUMN retention_task_storage_prep BOOLEAN DEFAULT FALSE"))
            connection.execute(db.text("ALTER TABLE coach ADD COLUMN retention_task_final_audit BOOLEAN DEFAULT FALSE"))
            connection.commit()
            print("4 sub-task columns added to coach table successfully.")
        except Exception as e:
            if "column" in str(e).lower() and "already exists" in str(e).lower():
                print("Columns already exist — no change needed.")
            else:
                print("Error adding columns:", str(e))