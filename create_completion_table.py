# -*- coding: utf-8 -*-
"""
Created on Fri Feb 13 06:17:00 2026

@author: pmmto
"""

# create_completion_table.py
from app import app, db

with app.app_context():
    db.create_all()
    print("CompletionTask table created (if not already present).")