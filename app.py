import os
import io
import csv
import math
import calendar

from pathlib import Path
from datetime import datetime, timedelta
from functools import wraps

import plotly.graph_objects as go
from collections import defaultdict

from io import StringIO
from flask import Response


from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import (
    LoginManager,
    login_user,
    login_required,
    logout_user,
    current_user,
)


#from models import db, User, Coach, CompletionTask, CoachAudit, CoachLocationHistory, ProductionLocationRule, TaskTemplate

from models import (
    db,
    User,
    Coach,
    CompletionTask,
    CoachAudit,
    CoachLocationHistory,
    ProductionLocationRule,
    WorkshopStation,
    TaskTemplate,
    CoachComponentInstallation,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or "coaches_secret_key_change_me_in_prod"

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    print("⚠️ DATABASE_URL missing — falling back to SQLite")
    database_url = "sqlite:///coaches.db"

database_url = database_url.strip()

if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

if os.environ.get("RENDER"):
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["REMEMBER_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


print("RAW DATABASE_URL repr:", repr(database_url))
print("Using database:", app.config["SQLALCHEMY_DATABASE_URI"])

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

with app.app_context():
    db.create_all()

    if not User.query.filter_by(username="admin").first():
        admin = User(
            username="admin",
            role="admin",
            is_active_user=True,
            created_at=datetime.utcnow(),
            created_by="system",
            updated_at=datetime.utcnow(),
            updated_by="system",
        )
        admin.set_password("Admin@123")
        db.session.add(admin)
        db.session.commit()
        print("✅ Admin user created")


def role_required(*roles):
    def wrapper(fn):
        @wraps(fn)
        @login_required
        def decorated(*args, **kwargs):
            user_role = (current_user.role or "").lower()
            allowed_roles = [r.lower() for r in roles]

            if user_role not in allowed_roles:
                flash("You don't have permission to access this page.", "danger")
                return redirect(url_for("coaches_list"))

            return fn(*args, **kwargs)
        return decorated
    return wrapper


def log_coach_audit(coach, action, changed_by=None, details=None):
    audit = CoachAudit(
        coach_id=coach.id,
        coach_number=coach.coach_number,
        action=action,
        changed_by=changed_by,
        details=details,
        created_at=datetime.utcnow(),
    )
    db.session.add(audit)


def log_system_audit(action, changed_by=None, details=None):
    audit = CoachAudit(
        coach_id=None,
        coach_number="TASK TEMPLATE",
        action=action,
        changed_by=changed_by,
        details=details,
        created_at=datetime.utcnow(),
    )
    db.session.add(audit)
    
    

@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))


def parse_date(value):
    if not value or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None

def save_component_installations(coach):
    """
    Replaces the coach's component/supplier/installer rows
    from the submitted edit form.
    """
    components = request.form.getlist("component[]")
    suppliers = request.form.getlist("supplier[]")
    installers = request.form.getlist("installer[]")

    # Clear existing rows for this coach
    CoachComponentInstallation.query.filter_by(coach_id=coach.id).delete()

    for component, supplier, installer in zip(components, suppliers, installers):
        component = (component or "").strip()
        supplier = (supplier or "").strip()
        installer = (installer or "").strip()

        # Skip completely blank rows
        if not component and not supplier and not installer:
            continue

        # Component is required if supplier/installer is entered
        if not component:
            continue

        db.session.add(
            CoachComponentInstallation(
                coach_id=coach.id,
                component=component,
                supplier=supplier or None,
                installer=installer or None,
            )
        )

def get_component_installation_snapshot(coach):
    rows = []

    for item in coach.component_installations:
        rows.append(
            {
                "component": item.component or "",
                "supplier": item.supplier or "",
                "installer": item.installer or "",
            }
        )

    return rows


def describe_component_installation_changes(old_rows, new_rows):
    old_set = {
        (row["component"], row["supplier"], row["installer"])
        for row in old_rows
    }

    new_set = {
        (row["component"], row["supplier"], row["installer"])
        for row in new_rows
    }

    added = sorted(new_set - old_set)
    removed = sorted(old_set - new_set)

    changes = []

    for component, supplier, installer in added:
        changes.append(
            f"Component added: {component} | {supplier or '—'} | {installer or '—'}"
        )

    for component, supplier, installer in removed:
        changes.append(
            f"Component removed: {component} | {supplier or '—'} | {installer or '—'}"
        )

    return changes




def get_inspection_date(coach):
    """
    Operational inspection date:
    coach must be ready 8 days before the contractual due date.
    """
    if not coach.due_date:
        return None
    return coach.due_date - timedelta(days=8)


def get_schedule_flags(coach, today):
    """
    Returns derived schedule flags based on inspection date, not contractual due date.
    """
    inspection_date = get_inspection_date(coach)

    is_complete = bool(coach.complete)
    is_not_started = bool(not coach.stripping and not coach.complete)

    if not inspection_date or is_complete:
        return {
            "inspection_date": inspection_date,
            "days_to_inspection": None if not inspection_date else (inspection_date - today).days,
            "is_complete": is_complete,
            "is_overdue": False,
            "is_due_soon": False,
            "is_approaching": False,
            "is_not_started": is_not_started,
            "is_in_progress": not is_complete and not is_not_started,
        }

    days_to_inspection = (inspection_date - today).days
    is_overdue = days_to_inspection < 0
    is_due_soon = 0 <= days_to_inspection <= 7
    is_approaching = 8 <= days_to_inspection <= 14
    is_in_progress = not is_complete and not is_overdue and not is_due_soon and not is_not_started

    return {
        "inspection_date": inspection_date,
        "days_to_inspection": days_to_inspection,
        "is_complete": is_complete,
        "is_overdue": is_overdue,
        "is_due_soon": is_due_soon,
        "is_approaching": is_approaching,
        "is_not_started": is_not_started,
        "is_in_progress": is_in_progress,
    }


def format_display_date(value):
    if not value:
        return "—"
    return value.strftime("%d %b %Y")


def load_task_templates(coach_type):
    """
    Load task templates from database.
    CSV is now used only for import/export, not as the live source of truth.
    """
    templates = (
        TaskTemplate.query
        .filter(
            TaskTemplate.coach_type.ilike(coach_type.strip()),
            TaskTemplate.is_active.is_(True)
        )
        .order_by(
            TaskTemplate.sort_order.asc(),
            TaskTemplate.phase.asc(),
            TaskTemplate.section.asc(),
            TaskTemplate.task.asc(),
        )
        .all()
    )

    tasks = []
    for row in templates:
        tasks.append(
            {
                "coach_type": row.coach_type,
                "phase": row.phase,
                "section": row.section,
                "task": row.task,
                "hours": float(row.hours or 0.0),
            }
        )

    print(f"Loaded {len(tasks)} DB task templates for coach type '{coach_type}'")
    return tasks

def import_task_templates_from_csv(csv_path=None, replace_existing=False):
    """
    Fast CSV import into TaskTemplate table.
    Avoids per-row database queries.
    """
    template_path = Path(csv_path) if csv_path else Path(app.root_path) / "coach_tasks.csv"

    if not template_path.exists():
        raise FileNotFoundError(f"Template file not found: {template_path}")

    allowed_phases = {
        "Stripping",
        "Completion",
        "Serviceworthy",
        "Retention",
    }

    imported_count = 0
    skipped_count = 0

    if replace_existing:
        TaskTemplate.query.delete()
        db.session.flush()

    existing_templates = {}

    if not replace_existing:
        existing_rows = TaskTemplate.query.all()

        for row in existing_rows:
            key = (
                row.coach_type,
                row.phase,
                row.section,
                row.task,
            )
            existing_templates[key] = row

    new_rows = []

    with open(template_path, mode="r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        for idx, row in enumerate(reader, start=1):
            coach_type = (row.get("coach_type") or "").strip()
            phase = (row.get("phase") or "").strip().title()
            section = (row.get("section") or "").strip()
            task = (row.get("task") or "").strip()
            row_hours = row.get("hours") or 0

            if not coach_type or not phase or not section or not task:
                skipped_count += 1
                continue

            if phase not in allowed_phases:
                print(f"Skipped CSV row {idx}: invalid phase '{phase}'")
                skipped_count += 1
                continue

            try:
                hours_value = float(row_hours)
            except (TypeError, ValueError):
                hours_value = 0.0

            key = (coach_type, phase, section, task)

            if not replace_existing and key in existing_templates:
                existing = existing_templates[key]
                existing.hours = hours_value
                existing.sort_order = idx
                existing.is_active = True
            else:
                new_rows.append(
                    TaskTemplate(
                        coach_type=coach_type,
                        phase=phase,
                        section=section,
                        task=task,
                        hours=hours_value,
                        is_active=True,
                        sort_order=idx,
                    )
                )

            imported_count += 1

    if new_rows:
        db.session.bulk_save_objects(new_rows)

    db.session.commit()

    print(f"CSV import complete: imported={imported_count}, skipped={skipped_count}")
    return imported_count




@app.route("/")
def index():
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            if not getattr(user, "is_active_user", True):
                flash("Your account is deactivated.", "danger")
                return render_template("login.html")

            login_user(user)
            flash("Logged in successfully", "success")
            return redirect(url_for("coaches_list"))

        flash("Invalid credentials", "danger")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out successfully", "info")
    return redirect(url_for("login"))

@app.route("/manage-users")
@login_required
@role_required("admin")
def manage_users():
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "username").strip()
    per_page = request.args.get("per_page", 10, type=int)
    page = request.args.get("page", 1, type=int)

    query = User.query

    if q:
        query = query.filter(User.username.ilike(f"%{q}%"))

    if sort == "role":
        query = query.order_by(User.role.asc(), User.username.asc())
    elif sort == "updated_at":
        query = query.order_by(User.updated_at.desc(), User.username.asc())
    else:
        query = query.order_by(User.username.asc())

    users = query.paginate(page=page, per_page=per_page, error_out=False)

    return render_template(
        "manage_users.html",
        users=users,
        q=q,
        sort=sort,
        per_page=per_page,
    )


@app.route("/users/add", methods=["GET", "POST"])
@login_required
@role_required("admin")
def add_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        role = request.form.get("role", "viewer").strip().lower()

        allowed_roles = ["admin", "editor", "viewer"]

        if not username:
            flash("Username is required.", "danger")
            return render_template("add_user.html")

        if not password:
            flash("Password is required.", "danger")
            return render_template("add_user.html")

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("add_user.html")

        if role not in allowed_roles:
            flash("Invalid role selected.", "danger")
            return render_template("add_user.html")

        if User.query.filter_by(username=username).first():
            flash("Username already exists.", "danger")
            return render_template("add_user.html")

        new_user = User(
            username=username,
            role=role,
            is_active_user=True,
            created_at=datetime.utcnow(),
            created_by=current_user.username,
            updated_at=datetime.utcnow(),
            updated_by=current_user.username,
        )
        new_user.set_password(password)

        db.session.add(new_user)
        db.session.commit()

        flash("User added successfully.", "success")
        return redirect(url_for("manage_users"))

    return render_template("add_user.html")


@app.route("/users/edit/<int:id>", methods=["GET", "POST"])
@login_required
@role_required("admin")
def edit_user(id):
    user = User.query.get_or_404(id)

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        role = request.form.get("role", "viewer").strip().lower()
        password = request.form.get("password", "").strip()
        is_active_user = "is_active_user" in request.form

        allowed_roles = ["admin", "editor", "viewer"]

        if not username:
            flash("Username is required.", "danger")
            return render_template("edit_user.html", user=user)

        if role not in allowed_roles:
            flash("Invalid role selected.", "danger")
            return render_template("edit_user.html", user=user)

        existing_user = User.query.filter(User.username == username, User.id != user.id).first()
        if existing_user:
            flash("Another user already uses that username.", "danger")
            return render_template("edit_user.html", user=user)

        if user.id == current_user.id and not is_active_user:
            flash("You cannot deactivate your own account.", "danger")
            return render_template("edit_user.html", user=user)

        if user.id == current_user.id and role != "admin":
            flash("You cannot remove your own admin role.", "danger")
            return render_template("edit_user.html", user=user)

        user.username = username
        user.role = role
        user.is_active_user = is_active_user

        if password:
            user.set_password(password)

        user.updated_at = datetime.utcnow()
        user.updated_by = current_user.username

        db.session.commit()

        flash("User updated successfully.", "success")
        return redirect(url_for("manage_users"))

    return render_template("edit_user.html", user=user)


@app.route("/users/deactivate/<int:id>", methods=["POST"])
@login_required
@role_required("admin")
def deactivate_user(id):
    user = User.query.get_or_404(id)

    if user.username == "admin":
        flash("Cannot deactivate the admin account.", "danger")
        return redirect(url_for("manage_users"))

    if user.id == current_user.id:
        flash("You cannot deactivate your own account while logged in.", "danger")
        return redirect(url_for("manage_users"))

    if not user.is_active_user:
        flash("User is already inactive.", "warning")
        return redirect(url_for("manage_users"))

    user.is_active_user = False
    user.updated_at = datetime.utcnow()
    user.updated_by = current_user.username

    db.session.commit()

    flash("User deactivated successfully.", "info")
    return redirect(url_for("manage_users"))


@app.route("/users/reactivate/<int:id>", methods=["POST"])
@login_required
@role_required("admin")
def reactivate_user(id):
    user = User.query.get_or_404(id)

    if user.is_active_user:
        flash("User is already active.", "warning")
        return redirect(url_for("manage_users"))

    user.is_active_user = True
    user.updated_at = datetime.utcnow()
    user.updated_by = current_user.username

    db.session.commit()

    flash("User reactivated successfully.", "success")
    return redirect(url_for("manage_users"))

@app.route("/task-templates")
@login_required
@role_required("admin", "editor")
def task_templates_list():
    q = request.args.get("q", "").strip()
    coach_type_filter = request.args.get("coach_type", "").strip()
    active_filter = request.args.get("active", "active").strip().lower()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    
    if per_page not in [20, 50, 100, 200]:
        per_page = 20    

    query = TaskTemplate.query

    if q:
        query = query.filter(
            db.or_(
                TaskTemplate.coach_type.ilike(f"%{q}%"),
                TaskTemplate.phase.ilike(f"%{q}%"),
                TaskTemplate.section.ilike(f"%{q}%"),
                TaskTemplate.task.ilike(f"%{q}%"),
            )
        )

    if coach_type_filter:
        query = query.filter(TaskTemplate.coach_type == coach_type_filter)

    if active_filter == "active":
        query = query.filter(TaskTemplate.is_active.is_(True))
    elif active_filter == "inactive":
        query = query.filter(TaskTemplate.is_active.is_(False))

    templates = query.order_by(
        TaskTemplate.coach_type.asc(),
        TaskTemplate.sort_order.asc(),
        TaskTemplate.phase.asc(),
        TaskTemplate.section.asc(),
        TaskTemplate.task.asc(),
    ).paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )
        
    coach_types = sorted(
        {
            row.coach_type
            for row in TaskTemplate.query.with_entities(TaskTemplate.coach_type).all()
            if row.coach_type
        }
    )

    return render_template(
        "task_templates_list.html",
        templates=templates,
        q=q,
        coach_type_filter=coach_type_filter,
        active_filter=active_filter,
        coach_types=coach_types,
        page=page,
        per_page=per_page,
    )


@app.route("/task-templates/add", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def task_templates_add():
    if request.method == "POST":
        coach_type = request.form.get("coach_type", "").strip()
        phase = request.form.get("phase", "").strip()
        section = request.form.get("section", "").strip()
        task = request.form.get("task", "").strip()
        hours_raw = request.form.get("hours", "0").strip()
        sort_order_raw = request.form.get("sort_order", "0").strip()
        is_active = "is_active" in request.form

        if not coach_type or not phase or not section or not task:
            flash("Coach type, phase, section, and task are required.", "danger")
            return render_template("task_templates_add.html")

        try:
            hours = float(hours_raw or 0)
        except ValueError:
            flash("Hours must be a valid number.", "danger")
            return render_template("task_templates_add.html")

        try:
            sort_order = int(sort_order_raw or 0)
        except ValueError:
            sort_order = 0

        exists = TaskTemplate.query.filter_by(
            coach_type=coach_type,
            phase=phase,
            section=section,
            task=task,
        ).first()

        if exists:
            flash("That task template already exists.", "warning")
            return render_template("task_templates_add.html")

        db.session.add(
            TaskTemplate(
                coach_type=coach_type,
                phase=phase,
                section=section,
                task=task,
                hours=hours,
                sort_order=sort_order,
                is_active=is_active,
            )
        )
        db.session.commit()

        flash("Task template added successfully.", "success")
        return redirect(url_for("task_templates_list"))

    return render_template("task_templates_add.html")


@app.route("/task-templates/edit/<int:id>", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def task_templates_edit(id):
    template = TaskTemplate.query.get_or_404(id)

    if request.method == "POST":
        old_values = {
            "coach_type": template.coach_type,
            "phase": template.phase,
            "section": template.section,
            "task": template.task,
            "hours": template.hours,
            "sort_order": template.sort_order,
            "is_active": template.is_active,
        }

        coach_type = request.form.get("coach_type", "").strip()
        phase = request.form.get("phase", "").strip()
        section = request.form.get("section", "").strip()
        task = request.form.get("task", "").strip()
        hours_raw = request.form.get("hours", "0").strip()
        sort_order_raw = request.form.get("sort_order", "0").strip()
        is_active = "is_active" in request.form

        if not coach_type or not phase or not section or not task:
            flash("Coach type, phase, section, and task are required.", "danger")
            return render_template("task_templates_edit.html", template=template)

        try:
            hours = float(hours_raw or 0)
        except ValueError:
            flash("Hours must be a valid number.", "danger")
            return render_template("task_templates_edit.html", template=template)

        try:
            sort_order = int(sort_order_raw or 0)
        except ValueError:
            sort_order = 0

        duplicate = TaskTemplate.query.filter(
            TaskTemplate.id != template.id,
            TaskTemplate.coach_type == coach_type,
            TaskTemplate.phase == phase,
            TaskTemplate.section == section,
            TaskTemplate.task == task,
        ).first()

        if duplicate:
            flash("Another task template already uses the same coach type/phase/section/task.", "danger")
            return render_template("task_templates_edit.html", template=template)

        template.coach_type = coach_type
        template.phase = phase
        template.section = section
        template.task = task
        template.hours = hours
        template.sort_order = sort_order
        template.is_active = is_active

        new_values = {
            "coach_type": template.coach_type,
            "phase": template.phase,
            "section": template.section,
            "task": template.task,
            "hours": template.hours,
            "sort_order": template.sort_order,
            "is_active": template.is_active,
        }

        changes = []
        for field, old_value in old_values.items():
            new_value = new_values[field]
            if old_value != new_value:
                changes.append(f"{field}: {old_value} -> {new_value}")

        log_system_audit(
            action="task_template_updated",
            changed_by=current_user.username,
            details=(
                f"Template ID {template.id}; "
                + (" | ".join(changes) if changes else "No material changes")
            ),
        )

        db.session.commit()
        flash("Task template updated successfully.", "success")
        return redirect(url_for("task_templates_list"))

    return render_template("task_templates_edit.html", template=template)





@app.route("/task-templates/delete/<int:id>", methods=["POST"])
@login_required
@role_required("admin")
def task_templates_delete(id):
    template = TaskTemplate.query.get_or_404(id)
    db.session.delete(template)
    db.session.commit()
    flash("Task template deleted successfully.", "info")
    return redirect(url_for("task_templates_list"))


@app.route("/task-templates/import-csv", methods=["POST"])
@login_required
@role_required("admin", "editor")
def task_templates_import_csv():
    replace_existing = "replace_existing" in request.form

    try:
        count = import_task_templates_from_csv(replace_existing=replace_existing)
        
        log_system_audit(
            action="task_templates_imported",
            changed_by=current_user.username,
            details=f"Imported {count} row(s). replace_existing={replace_existing}"
        )
        db.session.commit()
                
        flash(f"Imported {count} task template row(s) from CSV.", "success")
    except Exception as exc:
        flash(f"CSV import failed: {exc}", "danger")

    return redirect(url_for("task_templates_list"))

@app.route("/task-templates/export-csv")
@login_required
@role_required("admin", "editor")
def task_templates_export_csv():
    templates = (
        TaskTemplate.query
        .order_by(
            TaskTemplate.coach_type.asc(),
            TaskTemplate.sort_order.asc(),
            TaskTemplate.phase.asc(),
            TaskTemplate.section.asc(),
            TaskTemplate.task.asc(),
        )
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["coach_type", "phase", "section", "task", "hours"])

    for row in templates:
        writer.writerow([
            row.coach_type,
            row.phase,
            row.section,
            row.task,
            row.hours,
        ])

    log_system_audit(
        action="task_templates_exported",
        changed_by=current_user.username,
        details=f"Exported {len(templates)} task template row(s)"
    )
    db.session.commit()

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=coach_tasks_export.csv"},
    )






def build_smart_alerts(coaches, today):
    alerts = []

    overdue = []
    due_soon = []
    ncr_gc_blocked = []
    retention_overdue = []
    serviceworthy_pending_retention = []

    for coach in coaches:
        flags = get_schedule_flags(coach, today)

        if flags.get("is_overdue"):
            overdue.append(coach)

        if flags.get("is_due_soon"):
            due_soon.append(coach)

        if getattr(coach, "ncr", False) or getattr(coach, "gc", False):
            ncr_gc_blocked.append(coach)

        is_trailer = (coach.coach_type or "").strip().lower() == "trailer"

        if not is_trailer and coach.serviceworthy_date and not coach.retention:
            retention_due = coach.serviceworthy_date + timedelta(days=14)

            if retention_due < today:
                retention_overdue.append(coach)
            else:
                serviceworthy_pending_retention.append(coach)

    if overdue:
        alerts.append({
            "level": "danger",
            "title": "Immediate Risk",
            "text": f"{len(overdue)} coach(es) are overdue against inspection date.",
            "items": overdue[:5],
        })

    if due_soon:
        alerts.append({
            "level": "warning",
            "title": "Upcoming Risk",
            "text": f"{len(due_soon)} coach(es) are due for inspection within 7 days.",
            "items": due_soon[:5],
        })

    if ncr_gc_blocked:
        alerts.append({
            "level": "danger",
            "title": "NCR / GC Blocked",
            "text": f"{len(ncr_gc_blocked)} coach(es) have open NCR or GC flags.",
            "items": ncr_gc_blocked[:5],
        })

    if retention_overdue:
        alerts.append({
            "level": "danger",
            "title": "Retention Overdue",
            "text": f"{len(retention_overdue)} coach(es) are past retention due date.",
            "items": retention_overdue[:5],
        })

    if serviceworthy_pending_retention:
        alerts.append({
            "level": "info",
            "title": "Retention Pending",
            "text": f"{len(serviceworthy_pending_retention)} coach(es) are serviceworthy and awaiting retention.",
            "items": serviceworthy_pending_retention[:5],
        })

    if not alerts:
        alerts.append({
            "level": "success",
            "title": "Healthy",
            "text": "No urgent inspection, NCR/GC, or retention risks detected.",
            "items": [],
        })

    return alerts

def get_current_phase_info(coach, today):
    """
    Determine the coach's current operational phase and how long it has been there.
    No database changes required.
    """

    is_trailer = (coach.coach_type or "").strip().lower() == "trailer"

    if coach.retention:
        return {
            "phase": "Delivered",
            "start_date": coach.retention_date or coach.serviceworthy_date or coach.completion_date,
            "days": 0,
            "risk": "success",
            "risk_label": "Complete",
        }

    if is_trailer and coach.serviceworthy:
        return {
            "phase": "Delivered",
            "start_date": coach.serviceworthy_date or coach.completion_date,
            "days": 0,
            "risk": "success",
            "risk_label": "Complete",
        }

    if coach.serviceworthy and not coach.retention and not is_trailer:
        start_date = coach.serviceworthy_date
        phase = "Retention"

    elif coach.complete and not coach.serviceworthy:
        start_date = coach.completion_date
        phase = "Serviceworthy"

    elif coach.stripping and not coach.complete:
        start_date = coach.stripping_date
        phase = "Completion"

    else:
        start_date = coach.due_date
        phase = "Stripping"

    if start_date:
        days = max((today - start_date).days, 0)
    else:
        days = None

    if days is None:
        risk = "secondary"
        risk_label = "Date Missing"
    elif days <= 7:
        risk = "success"
        risk_label = "Healthy"
    elif days <= 21:
        risk = "warning"
        risk_label = "Slow"
    else:
        risk = "danger"
        risk_label = "Critical"

    return {
        "phase": phase,
        "start_date": start_date,
        "days": days,
        "risk": risk,
        "risk_label": risk_label,
    }


def get_delay_risk_score(coach, today):
    score = 0
    reasons = []

    flags = get_schedule_flags(coach, today)
    phase_info = get_current_phase_info(coach, today)

    if coach.complete:
        return {
            "score": 0,
            "level": "success",
            "label": "Complete",
            "reasons": ["Coach is completed."],
        }

    if flags.get("is_overdue"):
        score += 35
        reasons.append("Inspection date overdue")

    elif flags.get("is_due_soon"):
        score += 20
        reasons.append("Inspection due within 7 days")

    days_in_phase = phase_info.get("days")

    if days_in_phase is None:
        score += 10
        reasons.append("Current phase start date missing")
    elif days_in_phase > 21:
        score += 25
        reasons.append(f"Current phase ageing: {days_in_phase} days")
    elif days_in_phase > 7:
        score += 12
        reasons.append(f"Current phase slowing: {days_in_phase} days")

    if getattr(coach, "ncr", False):
        score += 20
        reasons.append("NCR open")

    if getattr(coach, "gc", False):
        score += 20
        reasons.append("GC open")

    try:
        progress = coach.calculate_progress()
        overall_percent = float(progress.get("overall_percent", 0))
    except Exception:
        overall_percent = 0

    if overall_percent < 25:
        score += 15
        reasons.append("Overall progress below 25%")
    elif overall_percent < 50:
        score += 10
        reasons.append("Overall progress below 50%")

    score = min(score, 100)

    if score >= 70:
        level = "danger"
        label = "Critical Risk"
    elif score >= 40:
        level = "warning"
        label = "High Risk"
    else:
        level = "success"
        label = "Low Risk"

    if not reasons:
        reasons.append("No major delay indicators detected")

    return {
        "score": score,
        "level": level,
        "label": label,
        "reasons": reasons,
    }




@app.route("/coaches", methods=["GET"])
@login_required
def coaches_list():
    from datetime import date

    search_query = request.args.get("search", "").strip()
    coach_type_filter = request.args.get("coach_type", "").strip()
    status_filter = request.args.get("status", "").strip().lower()
    show_archived = request.args.get("show_archived", "false").lower() == "true"

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)
    
    if per_page not in [10, 20, 50, 100]:
        per_page = 10
    

    today = date.today()

    query = Coach.query

    if not show_archived:
        query = query.filter(Coach.archived.is_(False))

    if search_query:
        query = query.filter(
            db.or_(
                Coach.coach_number.ilike(f"%{search_query}%"),
                Coach.coach_type.ilike(f"%{search_query}%"),
            )
        )

    if coach_type_filter:
        query = query.filter(Coach.coach_type == coach_type_filter)

    all_matching_coaches = query.order_by(Coach.coach_number).all()

    status_counts = {
        "all": len(all_matching_coaches),
        "overdue": 0,
        "due_soon": 0,
        "complete": 0,
        "in_progress": 0,
        "not_started": 0,
    }

    for coach in all_matching_coaches:
        flags = get_schedule_flags(coach, today)

        if flags["is_complete"]:
            status_counts["complete"] += 1
        elif flags["is_overdue"]:
            status_counts["overdue"] += 1
        elif flags["is_due_soon"]:
            status_counts["due_soon"] += 1
        elif flags["is_not_started"]:
            status_counts["not_started"] += 1
        elif flags["is_in_progress"]:
            status_counts["in_progress"] += 1

    filtered_coaches = []
    
    for coach in all_matching_coaches:
        flags = get_schedule_flags(coach, today)
    
        include = (
            status_filter in ["", "all"]
            or (status_filter == "complete" and flags["is_complete"])
            or (status_filter == "overdue" and flags["is_overdue"])
            or (status_filter == "due_soon" and flags["is_due_soon"])
            or (status_filter == "not_started" and flags["is_not_started"])
            or (status_filter == "in_progress" and flags["is_in_progress"])
        )
    
        if include:
            filtered_coaches.append(coach)
    
    total_filtered = len(filtered_coaches)
    total_pages = max(1, math.ceil(total_filtered / per_page))
    
    if page < 1:
        page = 1
    
    if page > total_pages:
        page = total_pages
    
    start = (page - 1) * per_page
    end = start + per_page
    paged_coaches = filtered_coaches[start:end]  

# Debug                      
    print("==== COACHES PAGINATION DEBUG ====")
    print("ACTIVE APP FILE:", __file__)
    print("PAGE:", page)
    print("PER PAGE:", per_page)
    print("TOTAL FILTERED:", total_filtered)
    print("TOTAL PAGES:", total_pages)
    print("START:", start)
    print("END:", end)
    print("PAGED COACHES:", [c.coach_number for c in paged_coaches])                            
                    
                

    kpis = {
        "active_total": 0,
        "archived_total": 0,
        "overdue_total": 0,
        "due_soon_total": 0,
        "complete_total": 0,
        "in_progress_total": 0,
        "avg_progress": 0.0,
    }

    progress_values = []

    for coach in Coach.query.order_by(Coach.coach_number).all():
        flags = get_schedule_flags(coach, today)
        is_archived = bool(getattr(coach, "archived", False))

        if is_archived:
            kpis["archived_total"] += 1
        else:
            kpis["active_total"] += 1

        if flags["is_complete"]:
            kpis["complete_total"] += 1
        if flags["is_overdue"]:
            kpis["overdue_total"] += 1
        if flags["is_due_soon"]:
            kpis["due_soon_total"] += 1
        if flags["is_in_progress"]:
            kpis["in_progress_total"] += 1

        try:
            progress = coach.calculate_progress()
            progress_values.append(float(progress.get("overall_percent", 0)))
        except Exception:
            pass

    if progress_values:
        kpis["avg_progress"] = round(sum(progress_values) / len(progress_values), 1)

    coach_progress_data = []
    for coach in paged_coaches:
        progress = coach.calculate_progress()
        flags = get_schedule_flags(coach, today)


        phase_info = get_current_phase_info(coach, today)
        risk_info = get_delay_risk_score(coach, today)
        
        coach_progress_data.append(
            {
                "coach_id": coach.id,
                "coach_number": coach.coach_number,
                "coach_type": coach.coach_type,
                "progress": progress,
                "stripping_date": coach.stripping_date,
                "completion_date": coach.completion_date,
                "serviceworthy_date": coach.serviceworthy_date,
                "retention_date": coach.retention_date,
                "inspection_date": flags["inspection_date"],
                "days_to_inspection": flags["days_to_inspection"],
                "current_phase": phase_info["phase"],
                "days_in_phase": phase_info["days"],
                "phase_risk": phase_info["risk"],
                "phase_risk_label": phase_info["risk_label"],
                "risk_score": risk_info["score"],
                "risk_level": risk_info["level"],
                "risk_label": risk_info["label"],
                "risk_reasons": risk_info["reasons"],
                
            
            }
        )
        



    all_types = sorted(
        {
            c.coach_type
            for c in Coach.query.order_by(Coach.coach_type).all()
            if c.coach_type
        }
    )

    alerts = build_smart_alerts(
        Coach.query.filter(Coach.archived.is_(False)).all(),
        today
    )
    

    return render_template(
        "coaches_list.html",
        coaches=paged_coaches,
        coach_progress_data=coach_progress_data,
        total=total_filtered,
        page=page,
        current_page=page,
        per_page=per_page,
        total_pages=total_pages,
        search_query=search_query,
        coach_type_filter=coach_type_filter,
        status_filter=status_filter,
        all_types=all_types,
        show_archived=show_archived,
        today=today,
        status_counts=status_counts,
        kpis=kpis,
        alerts=alerts,
    )


@app.route("/coaches/add", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def coaches_add():
    if request.method == "POST":
        coach_number = request.form.get("coach_number", "").strip()

        existing = Coach.query.filter_by(coach_number=coach_number).first()

        if existing:
            flash(f"Coach number {coach_number} already exists.", "danger")
            return render_template("coaches_add.html")

        coach = Coach(
            coach_number=coach_number,
            coach_type=request.form.get("coach_type", "").strip(),

            # Old single-value fields kept empty for backward compatibility.
            component_service_supplier=None,
            service_provider=None,

            notes=request.form.get("notes") or None,
            stripping=False,
            stripping_certificate_issued="stripping_certificate_issued" in request.form,
            stripping_date=parse_date(request.form.get("stripping_date")),
            complete=False,
            completion_certificate_issued="completion_certificate_issued" in request.form,
            completion_date=parse_date(request.form.get("completion_date")),
            serviceworthy=False,
            serviceworthy_date=parse_date(request.form.get("serviceworthy_date")),
            retention=False,
            retention_date=None,
            due_date=parse_date(request.form.get("due_date")),
            invoice_stripping="invoice_stripping" in request.form,
            invoice_completion="invoice_completion" in request.form,
            invoice_serviceworthy="invoice_serviceworthy" in request.form,
            invoice_retention="invoice_retention" in request.form,
            invoice_current_escalation="invoice_current_escalation" in request.form,
        )

        db.session.add(coach)
        db.session.flush()

        # Save initial Component / Supplier / Installer rows.
        save_component_installations(coach)

        component_count = CoachComponentInstallation.query.filter_by(
            coach_id=coach.id
        ).count()

        template_tasks = load_task_templates(coach.coach_type)

        if not template_tasks:
            flash(
                f"No task template found for coach type '{coach.coach_type}'.",
                "warning"
            )
        else:
            for item in template_tasks:
                db.session.add(
                    CompletionTask(
                        coach_id=coach.id,
                        coach_no=coach.coach_number,
                        coach_type=coach.coach_type,
                        phase=item["phase"],
                        section=item["section"],
                        task=item["task"],
                        hours=item["hours"],
                        completed=False,
                        completed_date=None,
                    )
                )

        coach.sync_all_status()

        log_coach_audit(
            coach=coach,
            action="coach_created",
            changed_by=current_user.username,
            details=(
                f"Coach created. "
                f"Type={coach.coach_type}, "
                f"due_date={coach.due_date}, "
                f"tasks_loaded={len(template_tasks)}, "
                f"component_rows={component_count}"
            )
        )

        db.session.commit()

        flash("Coach added successfully", "success")
        return redirect(url_for("coaches_list"))

    return render_template("coaches_add.html")

@app.route("/coaches/archive/<int:id>", methods=["POST"])
@login_required
@role_required("admin")
def coaches_archive(id):
    coach = Coach.query.get_or_404(id)

    if coach.archived:
        flash("Coach is already archived.", "warning")
        return redirect(url_for("coaches_list"))

    coach.archived = True
    coach.archived_at = datetime.utcnow()
    coach.archived_by = current_user.username

    log_coach_audit(
        coach=coach,
        action="coach_archived",
        changed_by=current_user.username,
        details=f"Coach {coach.coach_number} archived"
    )

    db.session.commit()
    flash("Coach archived successfully.", "info")
    return redirect(url_for("coaches_list", updated=coach.coach_number))


@app.route("/coaches/unarchive/<int:id>", methods=["POST"])
@login_required
@role_required("admin")
def coaches_unarchive(id):
    coach = Coach.query.get_or_404(id)

    if not coach.archived:
        flash("Coach is not archived.", "warning")
        return redirect(url_for("coaches_list"))

    coach.archived = False
    coach.archived_at = None
    coach.archived_by = None

    log_coach_audit(
        coach=coach,
        action="coach_unarchived",
        changed_by=current_user.username,
        details=f"Coach {coach.coach_number} restored from archive"
    )

    db.session.commit()
    flash("Coach restored successfully.", "success")
    return redirect(url_for("coaches_list", updated=coach.coach_number))


@app.route("/coaches/delete/<int:id>", methods=["POST"])
@login_required
@role_required("admin")
def coaches_delete(id):
    coach = Coach.query.get_or_404(id)
    coach_number = coach.coach_number

    if not coach.archived:
        flash("Only archived coaches can be permanently deleted.", "danger")
        return redirect(url_for("coaches_list"))

    log_coach_audit(
        coach=coach,
        action="coach_deleted",
        changed_by=current_user.username,
        details=f"Coach {coach.coach_number} permanently deleted"
    )

    db.session.flush()
    db.session.delete(coach)
    db.session.commit()

    flash("Coach permanently deleted.", "danger")
    return redirect(url_for("coaches_list", updated=coach_number))


@app.route("/coaches/edit/<int:id>", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def coaches_edit(id):
    coach = Coach.query.get_or_404(id)

    if request.method == "POST":
        old_values = {
            "coach_number": coach.coach_number,
            "coach_type": coach.coach_type,
            "due_date": coach.due_date.isoformat() if coach.due_date else None,
            "notes": coach.notes,
            "stripping_certificate_issued": coach.stripping_certificate_issued,
            "stripping_date": coach.stripping_date.isoformat() if coach.stripping_date else None,
            "completion_certificate_issued": coach.completion_certificate_issued,
            "completion_date": coach.completion_date.isoformat() if coach.completion_date else None,
            "serviceworthy_date": coach.serviceworthy_date.isoformat() if coach.serviceworthy_date else None,
            "ncr": coach.ncr,
            "gc": coach.gc,
            "ncr_gc_cleared_date": coach.ncr_gc_cleared_date.isoformat() if coach.ncr_gc_cleared_date else None,
            "invoice_stripping": coach.invoice_stripping,
            "invoice_completion": coach.invoice_completion,
            "invoice_serviceworthy": coach.invoice_serviceworthy,
            "invoice_retention": coach.invoice_retention,
            "invoice_current_escalation": coach.invoice_current_escalation,
        }

        old_task_states = {
            task.id: task.completed
            for task in coach.completion_tasks
        }
        
        old_component_rows = get_component_installation_snapshot(coach)
        coach.coach_number = (request.form.get("coach_number") or "").strip()
        coach.coach_type = (request.form.get("coach_type") or "").strip()
        coach.due_date = parse_date(request.form.get("due_date"))
        coach.notes = (request.form.get("notes") or "").strip() or None

        save_component_installations(coach)
        
        db.session.flush()
        
        new_component_rows = []
        
        for item in CoachComponentInstallation.query.filter_by(
            coach_id=coach.id
        ).all():
            new_component_rows.append(
                {
                    "component": item.component or "",
                    "supplier": item.supplier or "",
                    "installer": item.installer or "",
                }
            )

        for task in coach.completion_tasks:
            field_name = f"task_{task.id}"
            checked = field_name in request.form
            task.completed = checked
            task.completed_date = datetime.now().date() if checked else None

        coach.stripping_certificate_issued = "stripping_certificate_issued" in request.form
        coach.stripping_date = parse_date(request.form.get("stripping_date"))

        coach.completion_certificate_issued = "completion_certificate_issued" in request.form
        coach.completion_date = parse_date(request.form.get("completion_date"))

        coach.serviceworthy_date = parse_date(request.form.get("serviceworthy_date"))

        coach.ncr = "ncr" in request.form
        coach.gc = "gc" in request.form
        coach.ncr_gc_cleared_date = parse_date(request.form.get("ncr_gc_cleared_date"))

        coach.invoice_stripping = "invoice_stripping" in request.form
        coach.invoice_completion = "invoice_completion" in request.form
        coach.invoice_serviceworthy = "invoice_serviceworthy" in request.form
        coach.invoice_retention = "invoice_retention" in request.form
        coach.invoice_current_escalation = "invoice_current_escalation" in request.form

        coach.sync_all_status()

        changes = []

        new_values = {
            "coach_number": coach.coach_number,
            "coach_type": coach.coach_type,
            "due_date": coach.due_date.isoformat() if coach.due_date else None,

            "notes": coach.notes,
            "stripping_certificate_issued": coach.stripping_certificate_issued,
            "stripping_date": coach.stripping_date.isoformat() if coach.stripping_date else None,
            "completion_certificate_issued": coach.completion_certificate_issued,
            "completion_date": coach.completion_date.isoformat() if coach.completion_date else None,
            "serviceworthy_date": coach.serviceworthy_date.isoformat() if coach.serviceworthy_date else None,
            "ncr": coach.ncr,
            "gc": coach.gc,
            "ncr_gc_cleared_date": coach.ncr_gc_cleared_date.isoformat() if coach.ncr_gc_cleared_date else None,
            "invoice_stripping": coach.invoice_stripping,
            "invoice_completion": coach.invoice_completion,
            "invoice_serviceworthy": coach.invoice_serviceworthy,
            "invoice_retention": coach.invoice_retention,
            "invoice_current_escalation": coach.invoice_current_escalation,
        }

        for field, old_val in old_values.items():
            new_val = new_values[field]
            if old_val != new_val:
                changes.append(f"{field}: {old_val} -> {new_val}")
                

        component_changes = describe_component_installation_changes(
            old_component_rows,
            new_component_rows
        )

        task_changes = []
        for task in coach.completion_tasks:
            old_completed = old_task_states.get(task.id)
            if old_completed != task.completed:
                task_changes.append(
                    f"Task '{task.task}' ({task.phase}/{task.section}): {old_completed} -> {task.completed}"
                )

        details_parts = []
        if changes:
            details_parts.append("Field changes: " + " | ".join(changes))
        if task_changes:
            details_parts.append("Task changes: " + " | ".join(task_changes))

        if component_changes:
            details_parts.append(
                "Component/Supplier/Installer changes: "
                + " | ".join(component_changes)
            )            
            
        if not details_parts:
            details_parts.append("No material changes recorded")

        log_coach_audit(
            coach=coach,
            action="coach_updated",
            changed_by=current_user.username,
            details=" || ".join(details_parts)
        )

        db.session.commit()
        flash("Coach updated successfully", "success")
        return redirect(url_for("coaches_list", updated=coach.coach_number))

    progress = coach.calculate_progress()
    return render_template("coaches_edit.html", coach=coach, progress=progress)

@app.route("/coaches/<int:coach_id>/tasks/add", methods=["POST"])
@login_required
@role_required("admin", "editor")
def coach_task_add(coach_id):
    coach = Coach.query.get_or_404(coach_id)

    phase = request.form.get("phase", "").strip()
    section = request.form.get("section", "").strip()
    task_text = request.form.get("task", "").strip()
    hours_raw = request.form.get("hours", "0").strip()

    if not phase or not section or not task_text:
        flash("Phase, section, and task are required.", "danger")
        return redirect(url_for("coaches_edit", id=coach.id))

    try:
        hours = float(hours_raw or 0)
    except ValueError:
        hours = 0.0

    new_task = CompletionTask(
        coach_id=coach.id,
        coach_no=coach.coach_number,
        coach_type=coach.coach_type,
        phase=phase,
        section=section,
        task=task_text,
        hours=hours,
        completed=False,
        completed_date=None,
    )

    db.session.add(new_task)
    coach.sync_all_status()

    log_coach_audit(
        coach=coach,
        action="coach_task_added",
        changed_by=current_user.username,
        details=f"Task added: {phase}/{section} - {task_text} ({hours} hrs)"
    )

    db.session.commit()
    flash("Task added successfully.", "success")
    return redirect(url_for("coaches_edit", id=coach.id))


@app.route("/coaches/<int:coach_id>/tasks/<int:task_id>/edit", methods=["POST"])
@login_required
@role_required("admin", "editor")
def coach_task_edit(coach_id, task_id):
    coach = Coach.query.get_or_404(coach_id)

    task = CompletionTask.query.filter_by(
        id=task_id,
        coach_id=coach.id
    ).first_or_404()

    old_details = f"{task.phase}/{task.section} - {task.task} ({task.hours} hrs)"

    phase = request.form.get("phase", "").strip()
    section = request.form.get("section", "").strip()
    task_text = request.form.get("task", "").strip()
    hours_raw = request.form.get("hours", "0").strip()
    completed = "completed" in request.form

    if not phase or not section or not task_text:
        flash("Phase, section, and task are required.", "danger")
        return redirect(url_for("coaches_edit", id=coach.id))

    try:
        hours = float(hours_raw or 0)
    except ValueError:
        hours = 0.0

    task.phase = phase
    task.section = section
    task.task = task_text
    task.hours = hours
    task.completed = completed
    task.completed_date = datetime.now().date() if completed else None

    coach.sync_all_status()

    new_details = f"{task.phase}/{task.section} - {task.task} ({task.hours} hrs)"

    log_coach_audit(
        coach=coach,
        action="coach_task_updated",
        changed_by=current_user.username,
        details=f"Task updated: {old_details} -> {new_details}; completed={completed}"
    )

    db.session.commit()
    flash("Task updated successfully.", "success")
    return redirect(url_for("coaches_edit", id=coach.id))


@app.route("/coaches/<int:coach_id>/tasks/<int:task_id>/delete", methods=["POST"])
@login_required
@role_required("admin", "editor")
def coach_task_delete(coach_id, task_id):
    coach = Coach.query.get_or_404(coach_id)

    task = CompletionTask.query.filter_by(
        id=task_id,
        coach_id=coach.id
    ).first_or_404()

    old_details = f"{task.phase}/{task.section} - {task.task} ({task.hours} hrs)"

    db.session.delete(task)
    coach.sync_all_status()

    log_coach_audit(
        coach=coach,
        action="coach_task_deleted",
        changed_by=current_user.username,
        details=f"Task deleted: {old_details}"
    )

    db.session.commit()
    flash("Task deleted successfully.", "warning")
    return redirect(url_for("coaches_edit", id=coach.id))

@app.route("/coaches-map", methods=["GET", "POST"])
@login_required
def coaches_map():
    from datetime import date

    if request.method == "POST":
        if current_user.role not in ["admin", "editor"]:
            flash("You do not have permission to update coach locations.", "danger")
            return redirect(url_for("coaches_map"))

        coach_id = request.form.get("coach_id", type=int)
        latitude = request.form.get("latitude", type=float)
        longitude = request.form.get("longitude", type=float)
        activity = request.form.get("activity", "").strip()
        production_location = request.form.get("production_location", "").strip()
        position_date = parse_date(request.form.get("position_date"))
        expected_stationary_days = request.form.get("expected_stationary_days", type=int)

        coach = Coach.query.get_or_404(coach_id)

        old_activity = coach.map_activity or ""
        old_location = coach.production_location or ""
        
        new_activity = activity or None
        new_location = production_location or None
        
        activity_changed = old_activity != (new_activity or "")
        location_changed = old_location != (new_location or "")
        
        coach.latitude = latitude
        coach.longitude = longitude
        coach.map_activity = new_activity
        coach.production_location = new_location
        coach.map_position_date = position_date or date.today()
        
        # Reset production analytics only when the production activity/location changes.
        # Updating coordinates alone must not reset the production clock.
        if activity_changed or location_changed or not coach.stationary_start_date:
            coach.stationary_start_date = coach.map_position_date
            coach.expected_stationary_days = expected_stationary_days
        
            if coach.stationary_start_date and expected_stationary_days is not None:
                coach.expected_move_date = (
                    coach.stationary_start_date
                    + timedelta(days=expected_stationary_days)
                )
            else:
                coach.expected_move_date = None
        else:
            coach.expected_stationary_days = expected_stationary_days
        
            if coach.stationary_start_date and expected_stationary_days is not None:
                coach.expected_move_date = (
                    coach.stationary_start_date
                    + timedelta(days=expected_stationary_days)
                )






        today = date.today()

        actual_days_stationary = None
        stationary_status = "No Target"

        if coach.stationary_start_date:
            actual_days_stationary = max((today - coach.stationary_start_date).days, 0)

        if coach.expected_move_date:
            days_remaining = (coach.expected_move_date - today).days

            if days_remaining < 0:
                stationary_status = "Overdue"
            elif days_remaining <= 1:
                stationary_status = "Due Soon"
            else:
                stationary_status = "Healthy"

        log_coach_audit(
            coach=coach,
            action="coach_map_position_updated",
            changed_by=current_user.username,
            details=(
                f"Map position updated: "
                f"lat={latitude}, lng={longitude}, "
                f"activity={activity}, "
                f"location={production_location}, "
                f"date={coach.map_position_date}, "
                f"expected_stationary_days={expected_stationary_days}, "
                f"expected_move_date={coach.expected_move_date}, "
                
                f"stationary_status={stationary_status}, "
                f"activity_changed={activity_changed}, "
                f"location_changed={location_changed}"
            ),
        )

        db.session.add(
            CoachLocationHistory(
                coach_id=coach.id,
                coach_number=coach.coach_number,
                latitude=latitude,
                longitude=longitude,
                activity=activity or None,
                production_location=production_location or None,
                stationary_start_date=coach.stationary_start_date,
                expected_stationary_days=coach.expected_stationary_days,
                expected_move_date=coach.expected_move_date,
                actual_days_stationary=actual_days_stationary,
                stationary_status=stationary_status,
                moved_by=current_user.username,
            )
        )

        db.session.commit()
        flash("Coach map position updated successfully.", "success")
        return redirect(url_for("coaches_map"))

    coaches = (
        Coach.query
        .filter(Coach.archived.is_(False))
        .order_by(Coach.coach_number.asc())
        .all()
    )

    today = date.today()

    stationary_summary = {
        "Healthy": 0,
        "Due Soon": 0,
        "Overdue": 0,
        "No Target": 0,
    }

    mapped_coaches = []
    overdue_coaches = []

    for coach in coaches:
        if coach.latitude is None or coach.longitude is None:
            continue

        days_stationary = None
        if coach.stationary_start_date:
            days_stationary = max((today - coach.stationary_start_date).days, 0)

        if coach.expected_move_date:
            days_remaining = (coach.expected_move_date - today).days

            if days_remaining < 0:
                stationary_status = "Overdue"
            elif days_remaining <= 1:
                stationary_status = "Due Soon"
            else:
                stationary_status = "Healthy"
        else:
            stationary_status = "No Target"

        stationary_summary[stationary_status] = stationary_summary.get(stationary_status, 0) + 1

        overdue_days = 0
        if stationary_status == "Overdue" and coach.expected_move_date:
            overdue_days = max((today - coach.expected_move_date).days, 0)

            overdue_coaches.append(
                {
                    "coach_number": coach.coach_number,
                    "activity": coach.map_activity or "No activity recorded",
                    "production_location": coach.production_location or "No location recorded",
                    "days_stationary": days_stationary,
                    "overdue_days": overdue_days,
                }
            )

        mapped_coaches.append(
            {
                "id": coach.id,
                "coach_number": coach.coach_number,
                "coach_type": coach.coach_type,
                "latitude": coach.latitude,
                "longitude": coach.longitude,
                "activity": coach.map_activity or "No activity recorded",
                "production_location": coach.production_location or "No location recorded",
                "position_date": coach.map_position_date.strftime("%d %b %Y") if coach.map_position_date else "No date recorded",
                "stationary_start_date": coach.stationary_start_date.strftime("%d %b %Y") if coach.stationary_start_date else "No start date",
                "expected_stationary_days": coach.expected_stationary_days,
                "expected_move_date": coach.expected_move_date.strftime("%d %b %Y") if coach.expected_move_date else "No expected move date",
                "days_stationary": days_stationary,
                "stationary_status": stationary_status,
            }
        )

    overdue_coaches = sorted(
        overdue_coaches,
        key=lambda row: row["overdue_days"],
        reverse=True
    )[:10]

    location_rules = (
        ProductionLocationRule.query
        .filter(ProductionLocationRule.is_active.is_(True))
        .order_by(ProductionLocationRule.location.asc())
        .all()
    )
    
    location_days_map = {
        rule.location: rule.default_days
        for rule in location_rules
    }


    workshop_stations = (
        WorkshopStation.query
        .filter_by(active=True)
        .order_by(
            WorkshopStation.sequence,
            WorkshopStation.station
        )
        .all()
    )





    return render_template(
        "coaches_map.html",
        coaches=coaches,
        mapped_coaches=mapped_coaches,
        workshop_stations=workshop_stations,
        stationary_summary=stationary_summary,
        overdue_coaches=overdue_coaches,
        location_rules=location_rules,
        location_days_map=location_days_map,
        google_maps_api_key=os.environ.get("GOOGLE_MAPS_API_KEY", ""),
        default_lat=os.environ.get("CTE_MAP_CENTER_LAT", "-29.634247"),
        default_lng=os.environ.get("CTE_MAP_CENTER_LNG", "30.351547"),
    )

@app.route("/production-bottlenecks")
@login_required
def production_bottlenecks():
    risk_filter = request.args.get("risk", "").strip()
    location_filter = request.args.get("location", "").strip()
    min_moves = request.args.get("min_moves", 0, type=int)

    rows = (
        CoachLocationHistory.query
        .filter(CoachLocationHistory.production_location.isnot(None))
        .all()
    )

    summary = {}

    for row in rows:
        location = row.production_location or "No location recorded"

        if location not in summary:
            summary[location] = {
                "location": location,
                "moves": 0,
                "expected_total": 0,
                "actual_total": 0,
                "overdue_count": 0,
            }

        expected = row.expected_stationary_days or 0
        actual = row.actual_days_stationary or 0

        summary[location]["moves"] += 1
        summary[location]["expected_total"] += expected
        summary[location]["actual_total"] += actual

        if row.stationary_status == "Overdue":
            summary[location]["overdue_count"] += 1

    bottlenecks = []

    for item in summary.values():
        moves = item["moves"] or 1
        avg_expected = round(item["expected_total"] / moves, 1)
        avg_actual = round(item["actual_total"] / moves, 1)
        variance = round(avg_actual - avg_expected, 1)

        if variance > 3 or item["overdue_count"] >= 3:
            risk = "High"
        elif variance > 1 or item["overdue_count"] >= 1:
            risk = "Medium"
        else:
            risk = "Low"

        row = {
            "location": item["location"],
            "moves": item["moves"],
            "avg_expected": avg_expected,
            "avg_actual": avg_actual,
            "variance": variance,
            "overdue_count": item["overdue_count"],
            "risk": risk,
        }

        if risk_filter and row["risk"] != risk_filter:
            continue

        if location_filter and row["location"] != location_filter:
            continue

        if min_moves and row["moves"] < min_moves:
            continue

        bottlenecks.append(row)

    bottlenecks = sorted(
        bottlenecks,
        key=lambda row: (row["variance"], row["overdue_count"]),
        reverse=True
    )

    all_locations = sorted(summary.keys())

    return render_template(
        "production_bottlenecks.html",
        bottlenecks=bottlenecks,
        all_locations=all_locations,
        risk_filter=risk_filter,
        location_filter=location_filter,
        min_moves=min_moves,
    )

@app.route("/production-bottlenecks/export")
@login_required
def production_bottlenecks_export():
    risk_filter = request.args.get("risk", "").strip()
    location_filter = request.args.get("location", "").strip()
    min_moves = request.args.get("min_moves", 0, type=int)

    rows = (
        CoachLocationHistory.query
        .filter(CoachLocationHistory.production_location.isnot(None))
        .all()
    )

    summary = {}

    for row in rows:
        location = row.production_location or "No location recorded"

        if location not in summary:
            summary[location] = {
                "location": location,
                "moves": 0,
                "expected_total": 0,
                "actual_total": 0,
                "overdue_count": 0,
            }

        expected = row.expected_stationary_days or 0
        actual = row.actual_days_stationary or 0

        summary[location]["moves"] += 1
        summary[location]["expected_total"] += expected
        summary[location]["actual_total"] += actual

        if row.stationary_status == "Overdue":
            summary[location]["overdue_count"] += 1

    bottlenecks = []

    for item in summary.values():
        moves = item["moves"] or 1
        avg_expected = round(item["expected_total"] / moves, 1)
        avg_actual = round(item["actual_total"] / moves, 1)
        variance = round(avg_actual - avg_expected, 1)

        if variance > 3 or item["overdue_count"] >= 3:
            risk = "High"
        elif variance > 1 or item["overdue_count"] >= 1:
            risk = "Medium"
        else:
            risk = "Low"

        row = {
            "location": item["location"],
            "moves": item["moves"],
            "avg_expected": avg_expected,
            "avg_actual": avg_actual,
            "variance": variance,
            "overdue_count": item["overdue_count"],
            "risk": risk,
        }

        if risk_filter and row["risk"] != risk_filter:
            continue

        if location_filter and row["location"] != location_filter:
            continue

        if min_moves and row["moves"] < min_moves:
            continue

        bottlenecks.append(row)

    bottlenecks = sorted(
        bottlenecks,
        key=lambda row: (row["variance"], row["overdue_count"]),
        reverse=True
    )

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Production Location",
        "Movement Records",
        "Average Expected Days",
        "Average Actual Days",
        "Variance",
        "Overdue Count",
        "Risk",
    ])

    for row in bottlenecks:
        writer.writerow([
            row["location"],
            row["moves"],
            row["avg_expected"],
            row["avg_actual"],
            row["variance"],
            row["overdue_count"],
            row["risk"],
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=production_bottlenecks.csv"
        },
    )



@app.route("/coach-movement-timeline")
@login_required
def coach_movement_timeline():
    from datetime import date

    q = request.args.get("q", "").strip()
    coach_filter = request.args.get("coach", "").strip()

    query = CoachLocationHistory.query

    if q:
        query = query.filter(
            db.or_(
                CoachLocationHistory.coach_number.ilike(f"%{q}%"),
                CoachLocationHistory.activity.ilike(f"%{q}%"),
                CoachLocationHistory.production_location.ilike(f"%{q}%"),
                CoachLocationHistory.moved_by.ilike(f"%{q}%"),
            )
        )

    if coach_filter:
        query = query.filter(CoachLocationHistory.coach_number == coach_filter)

    history_rows = query.order_by(
        CoachLocationHistory.coach_number.asc(),
        CoachLocationHistory.moved_at.desc()
    ).all()

    timeline = {}
    today = date.today()

    for row in history_rows:
        if row.stationary_start_date:
            row.display_actual_days_stationary = max(
                (today - row.stationary_start_date).days,
                0
            )
        else:
            row.display_actual_days_stationary = None

        timeline.setdefault(row.coach_number, []).append(row)

    coach_numbers = sorted({
        row.coach_number
        for row in CoachLocationHistory.query.with_entities(CoachLocationHistory.coach_number).all()
        if row.coach_number
    })

    return render_template(
        "coach_movement_timeline.html",
        timeline=timeline,
        coach_numbers=coach_numbers,
        q=q,
        coach_filter=coach_filter,
    )

@app.route("/coach-journey")
@login_required
def coach_journey():

    coach_id = request.args.get("coach_id", type=int)

    coaches = (
        Coach.query
        .filter(Coach.archived.is_(False))
        .order_by(Coach.coach_number)
        .all()
    )

    selected_coach = None
    history = []

    if coach_id:

        selected_coach = Coach.query.get_or_404(coach_id)

        history = (
            CoachLocationHistory.query
            .filter_by(coach_id=coach_id)
            .order_by(CoachLocationHistory.moved_at.asc())
            .all()
        )
    
    # =====================================================
    # Production Journey
    # =====================================================
    
    production_flow = [
        "Stripping",
        "Structural",
        "Paint - Grit Blast",
        "Paint - Body",
        "Assembly - Coach Build",
        "Assembly - Electrical",
        "Assembly - BUP",
        "Testing & Commissioning",
    ]
    
    completed_stages = []
    current_stage = None
    
    if history:
    
        # History should be oldest → newest
        history = sorted(history, key=lambda h: h.moved_at)
    
        completed_stages = [
            h.activity
            for h in history[:-1]
            if h.activity
        ]
    
        current_stage = history[-1].activity
    




    
    return render_template(
        "coach_journey.html",
        coaches=coaches,
        selected_coach=selected_coach,
        history=history,
        production_flow=production_flow,
        completed_stages=completed_stages,
        current_stage=current_stage,
    )


@app.route("/coach-location-history")
@login_required
def coach_location_history():
    q = request.args.get("q", "").strip()
    location_filter = request.args.get("location", "").strip()
    activity_filter = request.args.get("activity", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)

    if per_page not in [20, 50, 100]:
        per_page = 20

    query = CoachLocationHistory.query

    if q:
        query = query.filter(
            db.or_(
                CoachLocationHistory.coach_number.ilike(f"%{q}%"),
                CoachLocationHistory.moved_by.ilike(f"%{q}%"),
                CoachLocationHistory.activity.ilike(f"%{q}%"),
                CoachLocationHistory.production_location.ilike(f"%{q}%"),
            )
        )

    if location_filter:
        query = query.filter(CoachLocationHistory.production_location == location_filter)

    if activity_filter:
        query = query.filter(CoachLocationHistory.activity == activity_filter)

    history = query.order_by(
        CoachLocationHistory.moved_at.desc()
    ).paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )

    locations = sorted({
        row.production_location
        for row in CoachLocationHistory.query.with_entities(CoachLocationHistory.production_location).all()
        if row.production_location
    })

    activities = sorted({
        row.activity
        for row in CoachLocationHistory.query.with_entities(CoachLocationHistory.activity).all()
        if row.activity
    })

    return render_template(
        "coach_location_history.html",
        history=history,
        q=q,
        location_filter=location_filter,
        activity_filter=activity_filter,
        locations=locations,
        activities=activities,
        per_page=per_page,
    )






@app.route("/coaches/<int:id>/pack")
@login_required
def coach_pack(id):
    coach = Coach.query.get_or_404(id)
    progress = coach.calculate_progress()

    audits = (
        CoachAudit.query
        .filter(
            db.or_(
                CoachAudit.coach_id == coach.id,
                CoachAudit.coach_number == coach.coach_number,
            )
        )
        .order_by(CoachAudit.created_at.desc())
        .limit(20)
        .all()
    )

    return render_template(
        "coach_pack.html",
        coach=coach,
        progress=progress,
        audits=audits,
        generated_at=datetime.utcnow().strftime("%d %b %Y %H:%M"),
    )






@app.route("/delivery-schedule")
@login_required
def delivery_schedule():
    today = datetime.now().date()
    status_filter = request.args.get("status", "all").strip().lower()

    coaches = (
        Coach.query
        .filter(Coach.archived.is_(False))
        .order_by(Coach.due_date.asc())
        .all()
    )

    on_schedule = []
    completed_late = []
    work_in_progress = []
    approaching = []
    urgent = []

    route_version = "v6-inspection-date-minus-8"

    for coach in coaches:
        progress = coach.calculate_progress()
        due_date = coach.due_date
        completion_date = coach.completion_date
        inspection_date = get_inspection_date(coach)

        if completion_date:
            days_left = None
        else:
            days_left = (inspection_date - today).days if inspection_date else None

        if coach.coach_type and coach.coach_type.lower() == "trailer":
            retention_due_date = "Not applicable (Trailer)"
            retention_countdown = "Not applicable (Trailer)"
        elif coach.serviceworthy_date:
            retention_due = coach.serviceworthy_date + timedelta(days=14)
            retention_due_date = retention_due.strftime("%d %b %Y")

            retention_days = (retention_due - today).days
            if retention_days > 0:
                retention_countdown = f"{retention_days} days left"
            elif retention_days == 0:
                retention_countdown = "Due today"
            else:
                retention_countdown = f"{abs(retention_days)} days overdue"
        else:
            retention_due_date = "Not set"
            retention_countdown = "Serviceworthy not set yet"

        if coach.retention:
            status = "Commissioned / Handed Over to Client"
        elif coach.serviceworthy:
            status = "Serviceworthy"
        elif completion_date:
            status = "Completed"
        else:
            status = "In Progress"

        item = {
            "coach": coach,
            "days_left": days_left,
            "due_date": format_display_date(due_date),
            "inspection_date": format_display_date(inspection_date),
            "retention_due_date": retention_due_date,
            "retention_countdown": retention_countdown,
            "status": status,
            "progress_data": {
                "percentage": progress.get("overall_percent", progress.get("percentage", 0)),
                "phases": progress.get("phases", []),
            },
            "classification_reason": "",
        }

        if completion_date:
            if inspection_date:
                if completion_date <= inspection_date:
                    item["classification_reason"] = "Completed on or before inspection date"
                    on_schedule.append(item)
                else:
                    item["classification_reason"] = "Completed after inspection date"
                    completed_late.append(item)
            else:
                item["classification_reason"] = "Completed (no due date / inspection date)"
                on_schedule.append(item)
        else:
            if inspection_date is None:
                item["classification_reason"] = "No due date set"
                work_in_progress.append(item)
            elif days_left < 0:
                item["classification_reason"] = "Inspection date overdue and coach not completed"
                urgent.append(item)
            elif 0 <= days_left <= 7:
                item["classification_reason"] = "Inspection due within 7 days"
                urgent.append(item)
            elif 8 <= days_left <= 14:
                item["classification_reason"] = "Inspection due within 8–14 days"
                approaching.append(item)
            else:
                item["classification_reason"] = "More than 14 days remaining to inspection"
                work_in_progress.append(item)

    on_schedule_count = len(on_schedule)
    completed_late_count = len(completed_late)
    work_in_progress_count = len(work_in_progress)
    approaching_count = len(approaching)
    urgent_count = len(urgent)

    if status_filter == "on_schedule":
        on_schedule_view = on_schedule
        completed_late_view = []
        work_in_progress_view = []
        approaching_view = []
        urgent_view = []
    elif status_filter == "completed_late":
        on_schedule_view = []
        completed_late_view = completed_late
        work_in_progress_view = []
        approaching_view = []
        urgent_view = []
    elif status_filter == "work_in_progress":
        on_schedule_view = []
        completed_late_view = []
        work_in_progress_view = work_in_progress
        approaching_view = []
        urgent_view = []
    elif status_filter == "approaching":
        on_schedule_view = []
        completed_late_view = []
        work_in_progress_view = []
        approaching_view = approaching
        urgent_view = []
    elif status_filter == "urgent":
        on_schedule_view = []
        completed_late_view = []
        work_in_progress_view = []
        approaching_view = []
        urgent_view = urgent
    else:
        status_filter = "all"
        on_schedule_view = on_schedule
        completed_late_view = completed_late
        work_in_progress_view = work_in_progress
        approaching_view = approaching
        urgent_view = urgent

    alerts = []
    if urgent_count > 0:
        alerts.append({
            "level": "danger",
            "text": f"{urgent_count} coach(es) are urgent / overdue against the inspection date."
        })
    if approaching_count > 0:
        alerts.append({
            "level": "warning",
            "text": f"{approaching_count} coach(es) are approaching inspection within 8–14 days."
        })
    if not alerts:
        alerts.append({
            "level": "success",
            "text": "No urgent or approaching inspection risks at the moment."
        })

    return render_template(
        "delivery_schedule.html",
        today=today.strftime("%d %b %Y"),
        route_version=route_version,
        status_filter=status_filter,
        on_schedule=on_schedule_view,
        completed_late=completed_late_view,
        work_in_progress=work_in_progress_view,
        approaching=approaching_view,
        urgent=urgent_view,
        on_schedule_count=on_schedule_count,
        completed_late_count=completed_late_count,
        work_in_progress_count=work_in_progress_count,
        approaching_count=approaching_count,
        urgent_count=urgent_count,
        alerts=alerts,
    )



@app.route("/coach-audits", strict_slashes=False)
@login_required
@role_required("admin", "editor")
def coach_audits():
    q = request.args.get("q", "").strip()
    action = request.args.get("action", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)

    if per_page not in [20, 50, 100]:
        per_page = 20

    query = CoachAudit.query

    if q:
        query = query.filter(
            db.or_(
                CoachAudit.coach_number.ilike(f"%{q}%"),
                CoachAudit.changed_by.ilike(f"%{q}%"),
                CoachAudit.details.ilike(f"%{q}%"),
            )
        )

    if action:
        query = query.filter(CoachAudit.action == action)

    audits = query.order_by(CoachAudit.created_at.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )

    actions = [
        "coach_created",
        "coach_updated",
        "coach_deleted",
        "coach_archived",
        "coach_unarchived",
    ]

    return render_template(
        "coach_audits.html",
        audits=audits,
        q=q,
        action=action,
        per_page=per_page,
        actions=actions,
    )

@app.route("/production-locations")
@login_required
@role_required("admin", "editor")
def production_locations_list():
    q = request.args.get("q", "").strip()
    active_filter = request.args.get("active", "active").strip().lower()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)

    if per_page not in [20, 50, 100]:
        per_page = 20

    query = ProductionLocationRule.query

    if q:
        query = query.filter(ProductionLocationRule.location.ilike(f"%{q}%"))

    if active_filter == "active":
        query = query.filter(ProductionLocationRule.is_active.is_(True))
    elif active_filter == "inactive":
        query = query.filter(ProductionLocationRule.is_active.is_(False))

    locations = query.order_by(
        ProductionLocationRule.location.asc()
    ).paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )

    return render_template(
        "production_locations_list.html",
        locations=locations,
        q=q,
        active_filter=active_filter,
        per_page=per_page,
    )


@app.route("/production-locations/add", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def production_locations_add():
    if request.method == "POST":
        location = request.form.get("location", "").strip()
        default_days = request.form.get("default_days", type=int)
        is_active = "is_active" in request.form

        if not location:
            flash("Location is required.", "danger")
            return render_template("production_locations_add.html")

        if default_days is None or default_days < 0:
            flash("Default days must be zero or greater.", "danger")
            return render_template("production_locations_add.html")

        exists = ProductionLocationRule.query.filter_by(location=location).first()

        if exists:
            flash("That production location already exists.", "warning")
            return render_template("production_locations_add.html")

        rule = ProductionLocationRule(
            location=location,
            default_days=default_days,
            is_active=is_active,
        )

        db.session.add(rule)

        log_system_audit(
            action="production_location_created",
            changed_by=current_user.username,
            details=f"Location created: {location}; default_days={default_days}; active={is_active}"
        )

        db.session.commit()
        flash("Production location added successfully.", "success")
        return redirect(url_for("production_locations_list"))

    return render_template("production_locations_add.html")


@app.route("/production-locations/edit/<int:id>", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def production_locations_edit(id):
    rule = ProductionLocationRule.query.get_or_404(id)

    if request.method == "POST":
        old_location = rule.location
        old_default_days = rule.default_days
        old_is_active = rule.is_active

        location = request.form.get("location", "").strip()
        default_days = request.form.get("default_days", type=int)
        is_active = "is_active" in request.form

        if not location:
            flash("Location is required.", "danger")
            return render_template("production_locations_edit.html", rule=rule)

        if default_days is None or default_days < 0:
            flash("Default days must be zero or greater.", "danger")
            return render_template("production_locations_edit.html", rule=rule)

        duplicate = ProductionLocationRule.query.filter(
            ProductionLocationRule.id != rule.id,
            ProductionLocationRule.location == location,
        ).first()

        if duplicate:
            flash("Another production location already uses that name.", "danger")
            return render_template("production_locations_edit.html", rule=rule)

        rule.location = location
        rule.default_days = default_days
        rule.is_active = is_active

        changes = []
        if old_location != rule.location:
            changes.append(f"location: {old_location} -> {rule.location}")
        if old_default_days != rule.default_days:
            changes.append(f"default_days: {old_default_days} -> {rule.default_days}")
        if old_is_active != rule.is_active:
            changes.append(f"is_active: {old_is_active} -> {rule.is_active}")

        log_system_audit(
            action="production_location_updated",
            changed_by=current_user.username,
            details=f"Location ID {rule.id}; " + (" | ".join(changes) if changes else "No material changes")
        )

        db.session.commit()
        flash("Production location updated successfully.", "success")
        return redirect(url_for("production_locations_list"))

    return render_template("production_locations_edit.html", rule=rule)


@app.route("/production-locations/delete/<int:id>", methods=["POST"])
@login_required
@role_required("admin")
def production_locations_delete(id):
    rule = ProductionLocationRule.query.get_or_404(id)

    log_system_audit(
        action="production_location_deleted",
        changed_by=current_user.username,
        details=f"Location deleted: {rule.location}; default_days={rule.default_days}"
    )

    db.session.delete(rule)
    db.session.commit()

    flash("Production location deleted successfully.", "warning")
    return redirect(url_for("production_locations_list"))


@app.route("/task-templates/export")
@login_required
@role_required("admin", "editor")
def export_task_templates_csv():
    templates = TaskTemplate.query.filter(
        TaskTemplate.is_active.is_(True)
    ).order_by(
        TaskTemplate.coach_type.asc(),
        TaskTemplate.sort_order.asc(),
        TaskTemplate.phase.asc(),
        TaskTemplate.section.asc(),
        TaskTemplate.task.asc(),
    ).all()

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow(["coach_type", "phase", "section", "task", "hours"])

    for t in templates:
        writer.writerow([
            t.coach_type,
            t.phase,
            t.section,
            t.task,
            t.hours or 0,
        ])

    csv_data = output.getvalue()

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=coach_tasks_export.csv"
        },
    )



@app.route("/delivery-graph", methods=["GET", "POST"])
@login_required
def delivery_graph():
    from datetime import date
    import calendar
    import math
    from collections import defaultdict

    today_date = date.today()
    report_date = today_date.strftime("%d %b %Y")

    selected_type = request.values.get("coach_type", "all")
    desired_rate = request.values.get("desired_rate", type=float)

    query = Coach.query.filter(Coach.archived.is_(False))

    if selected_type != "all":
        query = query.filter(Coach.coach_type == selected_type)

    coaches = query.all()
    all_coach_types = sorted({c.coach_type for c in Coach.query.filter(Coach.archived.is_(False)).all() if c.coach_type})

    total_coaches = len(coaches)
    completed_coaches = [c for c in coaches if c.completion_date]
    incomplete_coaches = [c for c in coaches if not c.completion_date]

    completed_count = len(completed_coaches)
    incomplete_count = len(incomplete_coaches)

    max_due_date = max([c.due_date for c in coaches if c.due_date], default=today_date)

    if desired_rate is None:
        desired_rate = math.ceil(total_coaches / 12) if total_coaches else 0

    def month_start(d):
        return date(d.year, d.month, 1)

    start_month = month_start(today_date)
    end_month = month_start(max_due_date)

    months = []
    cursor = start_month

    while cursor <= end_month:
        months.append(cursor)
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)

    remaining_months = max(len(months), 1)
    required_rate = math.ceil(incomplete_count / remaining_months) if incomplete_count else 0

    completions_by_month = defaultdict(int)
    due_by_month = defaultdict(int)

    for coach in completed_coaches:
        completions_by_month[month_start(coach.completion_date)] += 1

    for coach in coaches:
        if coach.due_date:
            due_by_month[month_start(coach.due_date)] += 1

    labels = []
    actual_completed_line = []
    desired_completed_line = []
    required_completed_line = []
    remaining_line = []
    due_load_line = []
    monthly_completion_line = []
    monthly_completion_avg_line = []
    capacity_heat_colors = []
    capacity_heat_labels = []
    cumulative_actual = completed_count

    for index, m in enumerate(months):
        if index > 0:
            cumulative_actual += completions_by_month[m]

        desired_completed = min(total_coaches, completed_count + int(round(desired_rate * (index + 1))))
        required_completed = min(total_coaches, completed_count + int(round(required_rate * (index + 1))))
        remaining = max(total_coaches - cumulative_actual, 0)

        labels.append(f"{calendar.month_abbr[m.month]} {m.year}")
        actual_completed_line.append(cumulative_actual)
        desired_completed_line.append(desired_completed)
        required_completed_line.append(required_completed)
        remaining_line.append(remaining)
        due_load_line.append(due_by_month[m])
        monthly_completion_line.append(completions_by_month[m])
        recent_values = monthly_completion_line[-3:]
        monthly_avg = round(sum(recent_values) / len(recent_values), 1) if recent_values else 0
        monthly_completion_avg_line.append(monthly_avg)
        month_due_count = due_by_month[m]
        
        if required_rate == 0:
            heat_label = "No Load"
            heat_color = "#6c757d"
        elif month_due_count >= required_rate * 1.5:
            heat_label = "High Pressure"
            heat_color = "#dc3545"
        elif month_due_count >= required_rate:
            heat_label = "Moderate Pressure"
            heat_color = "#fd7e14"
        else:
            heat_label = "Manageable"
            heat_color = "#198754"
        
        capacity_heat_labels.append(heat_label)
        capacity_heat_colors.append(heat_color)        

    
    # INSERT STEP 1 HERE
    if len(monthly_completion_avg_line) >= 2:
        previous_avg = monthly_completion_avg_line[-2]
        latest_avg = monthly_completion_avg_line[-1]
    
        if latest_avg > previous_avg:
            productivity_trend = "Improving"
            productivity_trend_color = "success"
            productivity_trend_icon = "🟢"
    
        elif latest_avg < previous_avg:
            productivity_trend = "Declining"
            productivity_trend_color = "danger"
            productivity_trend_icon = "🔴"
    
        else:
            productivity_trend = "Stable"
            productivity_trend_color = "warning"
            productivity_trend_icon = "🟠"
    
    else:
        productivity_trend = "Insufficient Data"
        productivity_trend_color = "secondary"
        productivity_trend_icon = "⚪"


    if desired_rate and desired_rate > 0 and incomplete_count:
        months_to_finish = math.ceil(incomplete_count / desired_rate)
        projected_index = max(months_to_finish - 1, 0)

        if projected_index < len(months):
            projected_completion_month = labels[projected_index]
        else:
            extra_months = projected_index - len(months) + 1
            projected_completion_month = f"{extra_months} month(s) after final due date"
    elif incomplete_count == 0:
        projected_completion_month = "Already complete"
    else:
        projected_completion_month = "Not achievable at 0/month"

    bottlenecks = {
        "Stripping": 0,
        "Completion": 0,
        "Serviceworthy": 0,
        "Retention": 0,
    }

    for coach in incomplete_coaches:
        if not coach.stripping:
            bottlenecks["Stripping"] += 1
        elif not coach.complete:
            bottlenecks["Completion"] += 1
        elif not coach.serviceworthy:
            bottlenecks["Serviceworthy"] += 1
        elif coach.coach_type and coach.coach_type.lower() != "trailer" and not coach.retention:
            bottlenecks["Retention"] += 1

    bottleneck_labels = list(bottlenecks.keys())
    bottleneck_values = list(bottlenecks.values())
    
    section_totals = defaultdict(int)
    section_incomplete = defaultdict(int)
    
    for coach in incomplete_coaches:
        for task in coach.completion_tasks:
    
            section_name = task.section or "Unspecified"
    
            section_totals[section_name] += 1
    
            if not task.completed:
                section_incomplete[section_name] += 1
    
    section_percentages = []
    
    for section_name in section_totals:
    
        total = section_totals[section_name]
        incomplete = section_incomplete[section_name]
    
        if total > 0:
            incomplete_percent = round((incomplete / total) * 100, 1)
        else:
            incomplete_percent = 0
    
        section_percentages.append(
            (section_name, incomplete_percent)
        )
    
    section_percentages_sorted = sorted(
        section_percentages,
        key=lambda item: item[1],
        reverse=True
    )
    
    section_bottleneck_labels = [
        item[0]
        for item in section_percentages_sorted[:12]
    ]
    
    section_bottleneck_values = [
        item[1]
        for item in section_percentages_sorted[:12]
    ]

    section_risk_matrix = []
    
    for section_name, percent in section_percentages_sorted[:12]:
    
        if percent >= 75:
            risk_level = "danger"
            risk_label = "Critical"
            risk_icon = "🔴"
    
        elif percent >= 50:
            risk_level = "warning"
            risk_label = "High"
            risk_icon = "🟠"
    
        elif percent >= 25:
            risk_level = "primary"
            risk_label = "Moderate"
            risk_icon = "🔵"
    
        else:
            risk_level = "success"
            risk_label = "Stable"
            risk_icon = "🟢"
    
        section_risk_matrix.append({
            "section": section_name,
            "percent": percent,
            "risk_level": risk_level,
            "risk_label": risk_label,
            "risk_icon": risk_icon,
        })    

    gap = desired_rate - required_rate
    
    if incomplete_count == 0:
        status_color = "success"
        confidence = "HIGH CONFIDENCE"
        confidence_icon = "🟢"
    
        status = (
            "All coaches are complete. "
            "No outstanding delivery risk exists."
        )
    
    elif desired_rate >= required_rate * 1.25:
        status_color = "success"
        confidence = "HIGH CONFIDENCE"
        confidence_icon = "🟢"
    
        status = (
            f"Production capacity exceeds required throughput. "
            f"Desired rate ({desired_rate:g}/month) is comfortably above "
            f"required rate ({required_rate:g}/month)."
        )
    
    elif desired_rate >= required_rate:
        status_color = "warning"
        confidence = "MODERATE RISK"
        confidence_icon = "🟠"
    
        status = (
            f"Production targets are technically achievable, but with low margin. "
            f"Desired rate ({desired_rate:g}/month) is only slightly above "
            f"required rate ({required_rate:g}/month)."
        )
    
    else:
        status_color = "danger"
        confidence = "CRITICAL RISK"
        confidence_icon = "🔴"
    
        shortfall = required_rate - desired_rate
    
        status = (
            f"Current throughput is insufficient to achieve planned delivery dates. "
            f"Production is short by approximately {shortfall:g} coach(es)/month."
        )
  
    actual_rate = round(completed_count / 12, 1) if completed_count else 0

    if incomplete_count == 0:
        delivery_confidence = 100
    
    else:
        confidence_ratio = desired_rate / required_rate if required_rate else 1
    
        if confidence_ratio >= 1.2:
            delivery_confidence = 95
        elif confidence_ratio >= 1.0:
            delivery_confidence = 80
        elif confidence_ratio >= 0.8:
            delivery_confidence = 60
        elif confidence_ratio >= 0.6:
            delivery_confidence = 40
        else:
            delivery_confidence = 20
    
        overdue_count = len([
            c for c in coaches
            if get_schedule_flags(c, today_date).get("is_overdue")
        ])
    
        delivery_confidence -= overdue_count * 3
    
        critical_risk_count = len([
            c for c in coaches
            if get_delay_risk_score(c, today_date)["score"] >= 70
        ])
    
        delivery_confidence -= critical_risk_count * 2
    
        delivery_confidence = max(min(delivery_confidence, 100), 0)
    
    if delivery_confidence >= 85:
        confidence_grade = "Excellent"
        confidence_color = "success"
        confidence_icon = "🟢"
    
    elif delivery_confidence >= 70:
        confidence_grade = "Good"
        confidence_color = "primary"
        confidence_icon = "🔵"
    
    elif delivery_confidence >= 50:
        confidence_grade = "Moderate"
        confidence_color = "warning"
        confidence_icon = "🟠"
    
    else:
        confidence_grade = "High Risk"
        confidence_color = "danger"
        confidence_icon = "🔴"    

    top_risk_coaches = []
    
    for coach in coaches:
        risk = get_delay_risk_score(coach, today_date)
    
        if risk["score"] > 0:
            top_risk_coaches.append({
                "coach_id": coach.id,
                "coach_number": coach.coach_number,
                "coach_type": coach.coach_type,
                "risk_score": risk["score"],
                "risk_level": risk["level"],
                "risk_label": risk["label"],
                "risk_reasons": risk["reasons"],
            })
    
    top_risk_coaches = sorted(
        top_risk_coaches,
        key=lambda item: item["risk_score"],
        reverse=True
    )[:5]
    
    gantt_items = []
    
    for coach in coaches:
        start_date = coach.stripping_date or coach.due_date or today_date
        end_date = coach.completion_date or coach.due_date or today_date
    
        if end_date < start_date:
            end_date = start_date
    
        flags = get_schedule_flags(coach, today_date)
    
        if coach.complete:
            bar_color = "success"
            status_label = "Complete"
        elif flags.get("is_overdue"):
            bar_color = "danger"
            status_label = "Overdue"
        elif flags.get("is_due_soon"):
            bar_color = "warning"
            status_label = "Due Soon"
        else:
            bar_color = "primary"
            status_label = "In Progress"
    
        gantt_items.append({
            "coach_id": coach.id,
            "coach_number": coach.coach_number,
            "coach_type": coach.coach_type,
            "start_date": start_date,
            "end_date": end_date,
            "start_label": start_date.strftime("%d %b %Y"),
            "end_label": end_date.strftime("%d %b %Y"),
            "bar_color": bar_color,
            "status_label": status_label,
        })    
        
        ageing_items = []
        
        # Phase ageing from current operational phase
        phase_ageing = defaultdict(list)
        
        for coach in incomplete_coaches:
            phase_info = get_current_phase_info(coach, today_date)
        
            phase_name = phase_info.get("phase") or "Unknown"
            days = phase_info.get("days")
        
            if days is not None:
                phase_ageing[phase_name].append(days)
        
        for phase_name, values in phase_ageing.items():
            avg_days = round(sum(values) / len(values), 1) if values else 0
        
            ageing_items.append({
                "category": "Phase",
                "name": phase_name,
                "avg_days": avg_days,
                "count": len(values),
            })
        
        
        # Section ageing pressure from incomplete tasks on incomplete coaches
        section_ageing = defaultdict(list)
        
        for coach in incomplete_coaches:
            phase_info = get_current_phase_info(coach, today_date)
            days = phase_info.get("days")
        
            if days is None:
                continue
        
            for task in coach.completion_tasks:
                if not task.completed:
                    section_name = task.section or "Unspecified"
                    section_ageing[section_name].append(days)
        
        for section_name, values in section_ageing.items():
            avg_days = round(sum(values) / len(values), 1) if values else 0
        
            ageing_items.append({
                "category": "Section",
                "name": section_name,
                "avg_days": avg_days,
                "count": len(values),
            })
        
        
        ageing_items = sorted(
            ageing_items,
            key=lambda item: item["avg_days"],
            reverse=True
        )[:15]
        
        ageing_labels = [
            f"{item['category']}: {item['name']}"
            for item in ageing_items
        ]
        
        ageing_values = [
            item["avg_days"]
            for item in ageing_items
        ]
    
    executive_actions = []
    
    if top_risk_coaches:
        coach = top_risk_coaches[0]
        executive_actions.append({
            "level": coach["risk_level"],
            "title": f"Prioritise coach {coach['coach_number']}",
            "text": (
                f"Risk score is {coach['risk_score']}/100. "
                f"Main indicators: {', '.join(coach['risk_reasons'][:2])}."
            ),
        })
    
    if section_risk_matrix:
        section = section_risk_matrix[0]
        executive_actions.append({
            "level": section["risk_level"],
            "title": f"Review {section['section']} capacity",
            "text": (
                f"{section['percent']}% of work is incomplete. "
                f"Operational risk is {section['risk_label']}."
            ),
        })
    
    if ageing_items:
        ageing = ageing_items[0]
        action_level = "danger" if ageing["avg_days"] > 21 else "warning" if ageing["avg_days"] > 7 else "success"
    
        executive_actions.append({
            "level": action_level,
            "title": f"Investigate ageing in {ageing['name']}",
            "text": (
                f"{ageing['category']} ageing average is {ageing['avg_days']} day(s), "
                f"based on {ageing['count']} item(s)."
            ),
        })
    
    executive_actions.append({
        "level": confidence_color,
        "title": f"Delivery confidence is {confidence_grade}",
        "text": (
            f"Overall delivery confidence is {delivery_confidence}%. "
            f"Current forecast status: {confidence}."
        ),
    })
    
    executive_actions.append({
        "level": productivity_trend_color,
        "title": f"Productivity trend is {productivity_trend}",
        "text": "Review monthly completion velocity if the trend is declining or stable.",
    })
    
    



    return render_template(
        "delivery_graph.html",
        report_date=report_date,
        selected_type=selected_type,
        all_coach_types=all_coach_types,
        total_coaches=total_coaches,
        completed_count=completed_count,
        incomplete_count=incomplete_count,
        desired_rate=desired_rate,
        actual_rate=actual_rate,
        required_rate=required_rate,
        final_deadline=max_due_date.strftime("%d %b %Y"),
        remaining_months=remaining_months,
        projected_completion_month=projected_completion_month,
        status=status,
        status_color=status_color,
        confidence=confidence,
        confidence_icon=confidence_icon,
        labels=labels,
        actual_completed_line=actual_completed_line,
        desired_completed_line=desired_completed_line,
        required_completed_line=required_completed_line,
        remaining_line=remaining_line,
        due_load_line=due_load_line,
        monthly_completion_line=monthly_completion_line,
        bottleneck_labels=bottleneck_labels,
        bottleneck_values=bottleneck_values,
        capacity_heat_labels=capacity_heat_labels,
        capacity_heat_colors=capacity_heat_colors,
        section_bottleneck_labels=section_bottleneck_labels,
        section_bottleneck_values=section_bottleneck_values,
        monthly_completion_avg_line=monthly_completion_avg_line,
        productivity_trend=productivity_trend,
        productivity_trend_color=productivity_trend_color,
        productivity_trend_icon=productivity_trend_icon,
        delivery_confidence=delivery_confidence,
        confidence_grade=confidence_grade,
        confidence_color=confidence_color,
        #confidence_icon=confidence_icon,
        top_risk_coaches=top_risk_coaches,
        section_risk_matrix=section_risk_matrix,
        gantt_items=gantt_items,
        ageing_items=ageing_items,
        ageing_labels=ageing_labels,
        ageing_values=ageing_values,
        executive_actions=executive_actions,
        
    )

@app.route("/admin/export-database-csv")
@login_required
@role_required("admin")
def export_database_csv():
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["TABLE: coach"])
    writer.writerow([
        "id", "coach_number", "coach_type", "due_date",
        "stripping_date", "completion_date", "serviceworthy_date",
        "retention_date",
        "stripping", "complete", "serviceworthy", "retention",
        "archived"
    ])

    for c in Coach.query.order_by(Coach.coach_number.asc()).all():
        writer.writerow([
            c.id,
            c.coach_number,
            c.coach_type,
            c.due_date,
            c.stripping_date,
            c.completion_date,
            c.serviceworthy_date,
            c.retention_date,
            c.stripping,
            c.complete,
            c.serviceworthy,
            c.retention,
            getattr(c, "archived", False),
        ])

    writer.writerow([])
    writer.writerow(["TABLE: completion_task"])
    writer.writerow([
        "id", "coach_id", "coach_no", "coach_type",
        "phase", "section", "task", "hours",
        "completed", "completed_date"
    ])

    for t in CompletionTask.query.order_by(CompletionTask.coach_no.asc(), CompletionTask.phase.asc(), CompletionTask.section.asc()).all():
        writer.writerow([
            t.id,
            t.coach_id,
            t.coach_no,
            t.coach_type,
            t.phase,
            t.section,
            t.task,
            t.hours,
            t.completed,
            t.completed_date,
        ])

    writer.writerow([])
    writer.writerow(["TABLE: task_template"])
    writer.writerow([
        "id", "coach_type", "phase", "section", "task",
        "hours", "is_active", "sort_order"
    ])

    for tt in TaskTemplate.query.order_by(TaskTemplate.coach_type.asc(), TaskTemplate.sort_order.asc()).all():
        writer.writerow([
            tt.id,
            tt.coach_type,
            tt.phase,
            tt.section,
            tt.task,
            tt.hours,
            tt.is_active,
            tt.sort_order,
        ])

    writer.writerow([])
    writer.writerow(["TABLE: coach_audit"])
    writer.writerow([
        "id", "coach_id", "coach_number", "action",
        "changed_by", "details", "created_at"
    ])

    for a in CoachAudit.query.order_by(CoachAudit.created_at.desc()).all():
        writer.writerow([
            a.id,
            a.coach_id,
            a.coach_number,
            a.action,
            a.changed_by,
            a.details,
            a.created_at,
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=cte_durban_coaches_backup.csv"
        },
    )


@app.route("/debug-env")
def debug_env():
    return {
        "DATABASE_URL in os.environ": "DATABASE_URL" in os.environ,
        "DATABASE_URL value": os.environ.get("DATABASE_URL", "[missing]"),
        "All env keys (first 20)": list(os.environ.keys())[:20],
    }


if __name__ == "__main__":
    print("ACTIVE APP FILE:", __file__)
    print("ACTIVE PROCESS STARTED")

    port = int(os.environ.get("PORT", 8088))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )