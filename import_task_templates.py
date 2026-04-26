# -*- coding: utf-8 -*-
"""
Created on Thu Apr 23 17:39:20 2026

@author: pmmto
"""

import csv
from pathlib import Path

from app import app, db
from models import TaskTemplate


def import_csv():
    csv_path = Path(app.root_path) / "coach_tasks.csv"

    if not csv_path.exists():
        print(f"CSV not found: {csv_path}")
        return

    with app.app_context():
        count = 0

        with open(csv_path, mode="r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)

            for i, row in enumerate(reader, start=1):
                coach_type = (row.get("coach_type") or "").strip()
                phase = (row.get("phase") or "").strip()
                section = (row.get("section") or "").strip()
                task = (row.get("task") or "").strip()
                hours_raw = row.get("hours") or 0

                if not coach_type or not phase or not section or not task:
                    continue

                try:
                    hours = float(hours_raw)
                except (TypeError, ValueError):
                    hours = 0.0

                existing = TaskTemplate.query.filter_by(
                    coach_type=coach_type,
                    phase=phase,
                    section=section,
                    task=task
                ).first()

                if existing:
                    existing.hours = hours
                    existing.is_active = True
                    existing.sort_order = i
                else:
                    db.session.add(
                        TaskTemplate(
                            coach_type=coach_type,
                            phase=phase,
                            section=section,
                            task=task,
                            hours=hours,
                            is_active=True,
                            sort_order=i,
                        )
                    )

                count += 1

            db.session.commit()
            print(f"Imported/updated {count} task templates from {csv_path}")


if __name__ == "__main__":
    import_csv()