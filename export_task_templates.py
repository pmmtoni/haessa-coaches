# -*- coding: utf-8 -*-
"""
Created on Sun Apr 26 12:02:05 2026

@author: pmmto
"""

import csv
from pathlib import Path

from app import app
from models import TaskTemplate


def export_task_templates():
    output_path = Path(app.root_path) / "coach_tasks.csv"

    with app.app_context():
        templates = TaskTemplate.query.filter(
            TaskTemplate.is_active.is_(True)
        ).order_by(
            TaskTemplate.coach_type.asc(),
            TaskTemplate.sort_order.asc(),
            TaskTemplate.phase.asc(),
            TaskTemplate.section.asc(),
            TaskTemplate.task.asc(),
        ).all()

        with open(output_path, mode="w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["coach_type", "phase", "section", "task", "hours"])

            for t in templates:
                writer.writerow([
                    t.coach_type,
                    t.phase,
                    t.section,
                    t.task,
                    t.hours or 0,
                ])

        print(f"Exported {len(templates)} active task templates to:")
        print(output_path)


if __name__ == "__main__":
    export_task_templates()