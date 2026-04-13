import os
import csv
from pathlib import Path
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import (
    LoginManager,
    login_user,
    login_required,
    logout_user,
    current_user,
)

from models import db, User, Coach, CompletionTask, CoachAudit



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





@login_manager.user_loader
def load_user(uid):
    return User.query.get(int(uid))


def parse_date(value):
    if not value or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def load_task_templates(coach_type):
    template_path = Path(app.root_path) / "coach_tasks.csv"
    tasks = []

    if not template_path.exists():
        print(f"Template file not found: {template_path}")
        return tasks

    with open(template_path, mode="r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        for row in reader:
            row_coach_type = (row.get("coach_type") or "").strip()
            row_phase = (row.get("phase") or "").strip()
            row_section = (row.get("section") or "").strip()
            row_task = (row.get("task") or "").strip()
            row_hours = row.get("hours") or 0

            if row_coach_type.lower() != coach_type.strip().lower():
                continue

            try:
                hours_value = float(row_hours)
            except (TypeError, ValueError):
                hours_value = 0.0

            tasks.append(
                {
                    "coach_type": row_coach_type,
                    "phase": row_phase,
                    "section": row_section,
                    "task": row_task,
                    "hours": hours_value,
                }
            )

    print(f"Loaded {len(tasks)} template tasks for coach type '{coach_type}'")
    return tasks


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

@app.route("/manage-users", strict_slashes=False)
@login_required
@role_required("admin")
def manage_users():
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "username").strip().lower()
    status = request.args.get("status", "all").strip().lower()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)

    if per_page not in [10, 25, 50]:
        per_page = 10

    if status not in ["all", "active", "inactive"]:
        status = "all"

    query = User.query

    if q:
        query = query.filter(User.username.ilike(f"%{q}%"))

    if status == "active":
        query = query.filter(User.is_active_user.is_(True))
    elif status == "inactive":
        query = query.filter(User.is_active_user.is_(False))

    if sort == "role":
        query = query.order_by(User.role.asc(), User.username.asc())
    elif sort == "updated_at":
        query = query.order_by(User.updated_at.desc(), User.username.asc())
    else:
        sort = "username"
        query = query.order_by(User.username.asc())

    users = query.paginate(page=page, per_page=per_page, error_out=False)

    return render_template(
        "manage_users.html",
        users=users,
        q=q,
        sort=sort,
        status=status,
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


# @app.route("/users/delete/<int:id>", methods=["POST"])
# @login_required
# @role_required("admin")
# def delete_user(id):
#     user = User.query.get_or_404(id)

#     if user.username == "admin":
#         flash("Cannot delete the admin account.", "danger")
#         return redirect(url_for("manage_users"))

#     if user.id == current_user.id:
#         flash("You cannot delete your own account while logged in.", "danger")
#         return redirect(url_for("manage_users"))

#     db.session.delete(user)
#     db.session.commit()

#     flash("User deleted successfully.", "info")
#     return redirect(url_for("manage_users"))

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


@app.route("/coaches", methods=["GET"])
@login_required
def coaches_list():
    from datetime import date

    search_query = request.args.get("search", "").strip()
    coach_type_filter = request.args.get("coach_type", "").strip()
    status_filter = request.args.get("status", "").strip().lower()
    show_archived = request.args.get("show_archived", "false").lower() == "true"

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

    # Full result set before status filtering
    all_matching_coaches = query.order_by(Coach.coach_number).all()

    # Global counts for quick status cards
    status_counts = {
        "all": len(all_matching_coaches),
        "overdue": 0,
        "due_soon": 0,
        "complete": 0,
        "in_progress": 0,
        "not_started": 0,
    }

    for coach in all_matching_coaches:
        is_complete = bool(coach.complete)
        is_overdue = bool(coach.due_date and not coach.complete and coach.due_date < today)
        is_due_soon = bool(
            coach.due_date
            and not coach.complete
            and 0 <= (coach.due_date - today).days <= 10
        )
        is_not_started = bool(not coach.stripping and not coach.complete)
        is_in_progress = bool(
            not is_complete and not is_overdue and not is_due_soon and not is_not_started
        )

        if is_complete:
            status_counts["complete"] += 1
        elif is_overdue:
            status_counts["overdue"] += 1
        elif is_due_soon:
            status_counts["due_soon"] += 1
        elif is_not_started:
            status_counts["not_started"] += 1
        elif is_in_progress:
            status_counts["in_progress"] += 1

    # Apply status filter for displayed rows
    filtered_coaches = []
    for coach in all_matching_coaches:
        is_complete = bool(coach.complete)
        is_overdue = bool(coach.due_date and not coach.complete and coach.due_date < today)
        is_due_soon = bool(
            coach.due_date
            and not coach.complete
            and 0 <= (coach.due_date - today).days <= 10
        )
        is_not_started = bool(not coach.stripping and not coach.complete)
        is_in_progress = bool(
            not is_complete and not is_overdue and not is_due_soon and not is_not_started
        )

        include = (
            status_filter in ["", "all"]
            or (status_filter == "complete" and is_complete)
            or (status_filter == "overdue" and is_overdue)
            or (status_filter == "due_soon" and is_due_soon)
            or (status_filter == "not_started" and is_not_started)
            or (status_filter == "in_progress" and is_in_progress)
        )

        if include:
            filtered_coaches.append(coach)

    # KPI cards
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
        is_archived = bool(getattr(coach, "archived", False))
        is_complete = bool(coach.complete)
        is_overdue = bool(coach.due_date and not coach.complete and coach.due_date < today)
        is_due_soon = bool(
            coach.due_date
            and not coach.complete
            and 0 <= (coach.due_date - today).days <= 10
        )
        is_not_started = bool(not coach.stripping and not coach.complete)
        is_in_progress = bool(
            not is_complete and not is_overdue and not is_due_soon and not is_not_started
        )

        if is_archived:
            kpis["archived_total"] += 1
        else:
            kpis["active_total"] += 1

        if is_complete:
            kpis["complete_total"] += 1
        if is_overdue:
            kpis["overdue_total"] += 1
        if is_due_soon:
            kpis["due_soon_total"] += 1
        if is_in_progress:
            kpis["in_progress_total"] += 1

        try:
            progress = coach.calculate_progress()
            progress_values.append(float(progress.get("overall_percent", 0)))
        except Exception:
            pass

    if progress_values:
        kpis["avg_progress"] = round(sum(progress_values) / len(progress_values), 1)

    coach_progress_data = []
    for coach in filtered_coaches:
        progress = coach.calculate_progress()
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
            }
        )

    all_types = sorted(
        {
            c.coach_type
            for c in Coach.query.order_by(Coach.coach_type).all()
            if c.coach_type
        }
    )

    return render_template(
        "coaches_list.html",
        coaches=filtered_coaches,
        coach_progress_data=coach_progress_data,
        total=len(filtered_coaches),
        search_query=search_query,
        coach_type_filter=coach_type_filter,
        status_filter=status_filter,
        all_types=all_types,
        show_archived=show_archived,
        today=today,
        status_counts=status_counts,
        kpis=kpis,
    )





@app.route("/coaches/add", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def coaches_add():
    if request.method == "POST":
        coach = Coach(
            coach_number=request.form.get("coach_number", "").strip(),
            coach_type=request.form.get("coach_type", "").strip(),
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

        template_tasks = load_task_templates(coach.coach_type)

        if not template_tasks:
            flash(f"No task template found for coach type '{coach.coach_type}'.", "warning")
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
            details=f"Coach created. Type={coach.coach_type}, due_date={coach.due_date}, tasks_loaded={len(template_tasks)}"
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



# @app.route("/coaches/delete/<int:id>")
# @login_required
# @role_required("admin")
# def coaches_delete(id):
#     coach = Coach.query.get_or_404(id)
#     coach_number = coach.coach_number
#     db.session.delete(coach)
#     db.session.commit()
#     flash("Coach deleted successfully", "info")
#     return redirect(url_for("coaches_list", updated=coach_number))


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

        coach.coach_number = (request.form.get("coach_number") or "").strip()
        coach.coach_type = (request.form.get("coach_type") or "").strip()
        coach.due_date = parse_date(request.form.get("due_date"))
        coach.notes = (request.form.get("notes") or "").strip() or None

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

    route_version = "v4-quick-filter-global-counts"

    for coach in coaches:
        progress = coach.calculate_progress()

        due_date = coach.due_date
        completion_date = coach.completion_date

        if completion_date:
            days_left = None
        else:
            days_left = (due_date - today).days if due_date else None

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
            if due_date:
                if completion_date <= due_date:
                    item["classification_reason"] = "Completed on or before due date"
                    on_schedule.append(item)
                else:
                    item["classification_reason"] = "Completed after due date"
                    completed_late.append(item)
            else:
                item["classification_reason"] = "Completed (no due date)"
                on_schedule.append(item)
        else:
            if due_date is None:
                item["classification_reason"] = "No due date set"
                work_in_progress.append(item)
            elif days_left < 0:
                item["classification_reason"] = "Overdue and not completed"
                urgent.append(item)
            elif 0 <= days_left <= 10:
                item["classification_reason"] = "Due within 7 days and not completed"
                urgent.append(item)
            elif 11 <= days_left <= 21:
                item["classification_reason"] = "Due within 8–21 days and not completed"
                approaching.append(item)
            else:
                item["classification_reason"] = "More than 21 days remaining"
                work_in_progress.append(item)

    # Global counts before applying view filter
    on_schedule_count = len(on_schedule)
    completed_late_count = len(completed_late)
    work_in_progress_count = len(work_in_progress)
    approaching_count = len(approaching)
    urgent_count = len(urgent)

    # Apply display filter
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
    ]

    return render_template(
        "coach_audits.html",
        audits=audits,
        q=q,
        action=action,
        per_page=per_page,
        actions=actions,
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