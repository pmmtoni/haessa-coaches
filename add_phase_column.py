# -*- coding: utf-8 -*-
"""
Created on Mon Mar  9 19:46:01 2026

@author: pmmto
"""

from app import app, db
from sqlalchemy import text

with app.app_context():
    with db.engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE completion_task ADD COLUMN phase VARCHAR(100) DEFAULT 'Completion'"))
            conn.commit()
            print("Column 'phase' added successfully to completion_task table")
        except Exception as e:
            error_str = str(e)
            if "already exists" in error_str or "duplicate column name" in error_str:
                print("Column 'phase' already exists — skipping")
            else:
                print(f"Error adding phase column: {error_str}")