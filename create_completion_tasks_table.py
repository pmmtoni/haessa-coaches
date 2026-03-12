# -*- coding: utf-8 -*-
"""
Created on Thu Feb 12 12:05:04 2026

@author: pmmto
"""

# create_completion_tasks_table.py
from app import app, db

with app.app_context():
    db.create_all()
    print("CompletionTask table created successfully (if it didn't exist already).")