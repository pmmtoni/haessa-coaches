# -*- coding: utf-8 -*-
"""
Created on Mon Feb 16 07:30:56 2026

@author: pmmto
"""

# check_columns.py
from app import app, db

with app.app_context():
    with db.engine.connect() as connection:
        result = connection.execute(db.text("PRAGMA table_info(coach)"))
        columns = result.fetchall()
        column_names = [col[1] for col in columns]  # column name is the 2nd item (index 1)
        
        print("Columns in 'coach' table:")
        for name in sorted(column_names):
            print(f" - {name}")