# -*- coding: utf-8 -*-
"""
Created on Sat Feb 14 11:33:37 2026

@author: pmmto
"""

# test_model.py
from app import app, Coach, CompletionTask

with app.app_context():
    coach = Coach.query.first()
    if coach:
        print("Coach found:", coach.coach_number)
        print("Has completion_tasks attribute?", hasattr(coach, 'completion_tasks'))
        if hasattr(coach, 'completion_tasks'):
            print("Number of tasks:", coach.completion_tasks.count())
    else:
        print("No coaches in DB")