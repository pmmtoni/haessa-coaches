# -*- coding: utf-8 -*-
"""
Created on Thu Feb 26 20:27:50 2026

@author: pmmto
"""

from app import app, db

with app.app_context():
    db.create_all()
    print("Due date column added to Coach table.")