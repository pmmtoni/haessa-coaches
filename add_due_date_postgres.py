# -*- coding: utf-8 -*-
"""
Created on Thu Mar  5 23:51:19 2026

@author: pmmto
"""

from app import app, db
from sqlalchemy import text

with app.app_context():
    with db.engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE coach ADD COLUMN due_date DATE"))
            conn.commit()
            print("Column 'due_date' added to 'coach' table successfully.")
        except Exception as e:
            if "column" in str(e).lower() and "already exists" in str(e).lower():
                print("Column 'due_date' already exists — no change needed.")
            else:
                print("Error adding column:", str(e))

        # Optional verification
        result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'coach' AND column_name = 'due_date'"))
        if result.fetchone():
            print("Verification: due_date column exists.")
        else:
            print("Verification failed — column not found.")