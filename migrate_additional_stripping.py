# -*- coding: utf-8 -*-
"""
Created on Wed Feb 25 22:39:32 2026

@author: pmmto
"""

from app import app, db

with app.app_context():
    db.create_all()
    print("Additional Stripping sub-task columns added to Coach table.")