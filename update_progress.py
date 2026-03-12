# -*- coding: utf-8 -*-
"""
Created on Sat Feb 14 15:11:38 2026

@author: pmmto
"""

from app import app, db, Coach

with app.app_context():
    coaches = Coach.query.all()
    for coach in coaches:
        coach.calculate_progress()  # triggers auto-complete logic if needed
    db.session.commit()
    print(f"Progress recalculated for {len(coaches)} coaches")