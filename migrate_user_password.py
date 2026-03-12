# -*- coding: utf-8 -*-
"""
Created on Mon Feb 16 07:28:23 2026

@author: pmmto
"""

from app import app, db

with app.app_context():
    # Check if password_hash column exists
    columns = db.engine.execute("PRAGMA table_info(user)").fetchall()
    column_names = [col[1] for col in columns]

    if 'password_hash' not in column_names and 'password' in column_names:
        print("Migrating: Renaming password → password_hash")
        db.engine.execute("ALTER TABLE user RENAME COLUMN password TO password_hash")
        db.session.commit()
        print("Migration complete.")
    else:
        print("No migration needed or column already correct.")