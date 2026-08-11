from models import db, TaskTemplate, CompletionTask


def generate_completion_tasks(coach):
    """
    Synchronize CompletionTask records for a coach from the active
    TaskTemplate records belonging to that coach type.

    Rules:
    - Only active templates for the coach's type are considered.
    - Existing tasks are never duplicated.
    - Existing task progress/status is never reset.
    - Missing tasks are created.
    - Existing historical tasks are never deleted.
    - The caller controls the database transaction.
    """

    templates = (
        TaskTemplate.query
        .filter(
            TaskTemplate.coach_type.ilike(coach.coach_type.strip()),
            TaskTemplate.is_active.is_(True)
        )
        .order_by(
            TaskTemplate.sort_order.asc(),
            TaskTemplate.id.asc()
        )
        .all()
    )

    existing_tasks = (
        CompletionTask.query
        .filter_by(coach_id=coach.id)
        .all()
    )

    # Build identity set for existing tasks.
    existing_keys = {
        (
            task.phase.strip().lower(),
            task.section.strip().lower(),
            task.task.strip().lower(),
        )
        for task in existing_tasks
    }

    created = 0
    existing = 0

    for template in templates:

        key = (
            template.phase.strip().lower(),
            template.section.strip().lower(),
            template.task.strip().lower(),
        )

        if key in existing_keys:
            existing += 1
            continue

        task = CompletionTask(
            coach_id=coach.id,
            coach_no=coach.coach_number,
            coach_type=coach.coach_type,
            phase=template.phase,
            section=template.section,
            task=template.task,
            hours=float(template.hours or 0.0),

            completed=False,
            completed_date=None,

            status="Pending",
            percent_complete=0,
            assigned_to=None,
            workshop_station_id=None,
            started_at=None,
            completed_at=None,
            remarks=None,
        )

        db.session.add(task)

        created += 1
        existing_keys.add(key)

    return {
        "created": created,
        "existing": existing,
        "template_count": len(templates),
        "message": (
            f"{created} new tasks created; "
            f"{existing} existing tasks retained."
        ),
    }


def get_selected_production_tasks(coach):
    """
    Return only CompletionTask records that have been checked/selected
    for this coach.

    CompletionTask.completed represents the user's selection of work
    applicable to this particular coach.

    No database records are created, modified, or deleted.
    """

    return (
        CompletionTask.query
        .filter_by(
            coach_id=coach.id,
            completed=True
        )
        .order_by(
            CompletionTask.phase.asc(),
            CompletionTask.section.asc(),
            CompletionTask.task.asc()
        )
        .all()
    )


