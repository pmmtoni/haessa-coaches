# -*- coding: utf-8 -*-
"""
Created on Thu Feb 26 19:31:59 2026

@author: pmmto
"""

from app import app, db, Coach

with app.app_context():
    coaches = Coach.query.all()
    updated = 0

    for coach in coaches:
        stripping_tasks = [
            coach.stripping_task_bogie,
            coach.stripping_task_underframe,
            coach.stripping_task_plumbing_piping,
            coach.stripping_task_interior,
            coach.stripping_task_exterior,
            coach.stripping_task_roof,
            coach.stripping_task_components,
            coach.stripping_task_wiring,
            coach.stripping_task_sole_bar,
            coach.stripping_task_bogie_frame,
            coach.stripping_task_loose_components,
            coach.stripping_task_inspection,
            coach.stripping_task_cleaning,
            coach.stripping_task_documentation,
            coach.stripping_task_approval
        ]

        new_value = all(t for t in stripping_tasks if t is not None)

        if coach.stripping != new_value:
            coach.stripping = new_value
            updated += 1

    db.session.commit()
    print(f"Updated {updated} coaches.")
    print(f"New Stripping count: {Coach.query.filter_by(stripping=True).count()}")