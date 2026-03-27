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

from models import db, User, Coach, CompletionTask


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or "coaches_secret_key_change_me_in_prod"

#database_url = os.environ.get("DATABASE_URL")
#if not database_url:
#    raise ValueError("DATABASE_URL is not set")

database_url = os.environ.get("DATABASE_URL")

if not database_url:
    database_url = "postgresql://neondb_owner:npg_DAtphFl8X9zI@ep-muddy-wave-anl937xk-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"


if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg2://", 1)
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg2://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

print("Using database:", app.config["SQLALCHEMY_DATABASE_URI"])

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"

with app.app_context():
    db.create_all()

    if not User.query.filter_by(username="admin").first():
        admin = User(username="admin", role="admin")
        admin.set_password("Admin@123")
        db.session.add(admin)
        db.session.commit()
        print("✅ Admin user created")

def role_required(*roles):
    def wrapper(fn):
        @wraps(fn)
        @login_required
        def decorated(*args, **kwargs):
            if current_user.role not in roles:
                flash("You don't have permission to access this page.", "danger")
                return redirect(url_for("coaches_list"))
            return fn(*args, **kwargs)
        return decorated
    return wrapper


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

            tasks.append({
                "coach_type": row_coach_type,
                "phase": row_phase,
                "section": row_section,
                "task": row_task,
                "hours": hours_value,
            })

    print(f"Loaded {len(tasks)} template tasks for coach type '{coach_type}'")
    return tasks


@app.route("/")
def index():
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(username=request.form["username"]).first()
        if user and user.check_password(request.form["password"]):
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


@app.route("/coaches", methods=["GET"])
@login_required
def coaches_list():
    search_query = request.args.get("search", "").strip()
    coach_type_filter = request.args.get("coach_type", "")

    query = Coach.query

    if search_query:
        query = query.filter(
            db.or_(
                Coach.coach_number.ilike(f"%{search_query}%"),
                Coach.coach_type.ilike(f"%{search_query}%"),
            )
        )

    if coach_type_filter:
        query = query.filter(Coach.coach_type == coach_type_filter)

    coaches = query.order_by(Coach.coach_number).all()

    coach_progress_data = []
    for coach in coaches:
        progress = coach.calculate_progress()
        coach_progress_data.append({
            "coach_id": coach.id,
            "coach_number": coach.coach_number,
            "coach_type": coach.coach_type,
            "progress": progress,
            "stripping_date": coach.stripping_date,
            "completion_date": coach.completion_date,
            "serviceworthy_date": coach.serviceworthy_date,
            "retention_date": coach.retention_date,
        })

    all_types = sorted({c.coach_type for c in coaches})

    return render_template(
        "coaches_list.html",
        coaches=coaches,
        coach_progress_data=coach_progress_data,
        total=len(coaches),
        search_query=search_query,
        coach_type_filter=coach_type_filter,
        all_types=all_types,
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
            stripping_date=parse_date(request.form.get("stripping_date")),
            complete=False,
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
                db.session.add(CompletionTask(
                    coach_id=coach.id,
                    coach_no=coach.coach_number,
                    coach_type=coach.coach_type,
                    phase=item["phase"],
                    section=item["section"],
                    task=item["task"],
                    hours=item["hours"],
                    completed=False,
                    completed_date=None,
                ))

        coach.sync_active_phase_status()
        coach.sync_passive_status()

        db.session.commit()
        flash("Coach added successfully", "success")
        return redirect(url_for("coaches_list"))

    return render_template("coaches_add.html")



@app.route("/coaches/delete/<int:id>")
@login_required
@role_required("admin")
def coaches_delete(id):
    coach = Coach.query.get_or_404(id)
    db.session.delete(coach)
    db.session.commit()
    flash("Coach deleted successfully", "info")
    return redirect(url_for("coaches_list", updated=coach.coach_number))


@app.route("/coaches/edit/<int:id>", methods=["GET", "POST"])
@login_required
@role_required("admin", "editor")
def coaches_edit(id):
    coach = Coach.query.get_or_404(id)

    if request.method == "POST":
        coach.coach_number = (request.form.get("coach_number") or "").strip()
        coach.coach_type = (request.form.get("coach_type") or "").strip()
        coach.due_date = parse_date(request.form.get("due_date"))
        coach.notes = (request.form.get("notes") or "").strip() or None

        for task in coach.completion_tasks:
            field_name = f"task_{task.id}"
            checked = field_name in request.form
            task.completed = checked
            task.completed_date = datetime.now().date() if checked else None

        coach.completion_certificate_issued = "completion_certificate_issued" in request.form
        coach.completion_date = parse_date(request.form.get("completion_date"))
        coach.ncr = "ncr" in request.form
        coach.gc = "gc" in request.form
        coach.ncr_gc_cleared_date = parse_date(request.form.get("ncr_gc_cleared_date"))

        coach.invoice_stripping = "invoice_stripping" in request.form
        coach.invoice_completion = "invoice_completion" in request.form
        coach.invoice_serviceworthy = "invoice_serviceworthy" in request.form
        coach.invoice_retention = "invoice_retention" in request.form
        coach.invoice_current_escalation = "invoice_current_escalation" in request.form

        coach.sync_active_phase_status()
        coach.sync_passive_status()

        db.session.commit()
        flash("Coach updated successfully", "success")
        return redirect(url_for("coaches_list", updated=coach.coach_number))

    progress = coach.calculate_progress()
    return render_template("coaches_edit.html", coach=coach, progress=progress)

@app.route("/delivery-schedule")
@login_required
def delivery_schedule():
    today = datetime.now().date()

    coaches = Coach.query.order_by(Coach.due_date.asc()).all()

    on_schedule = []
    completed_late = []
    work_in_progress = []
    approaching = []
    urgent = []

    route_version = "v2-clean-classification"

    for coach in coaches:
        progress = coach.calculate_progress()

        due_date = coach.due_date
        completion_date = coach.completion_date

        days_left = (due_date - today).days if due_date else None

        # -----------------------------
        # Retention countdown
        # -----------------------------
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

        # -----------------------------
        # Status text
        # -----------------------------
        if coach.retention:
            status = "Commissioned / Handed Over to Client"
        elif coach.serviceworthy:
            status = "Serviceworthy"
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
                "phases": progress.get("phases", [])
            },
            "classification_reason": ""
        }

        # =========================================
        # ✅ CORRECT CLASSIFICATION LOGIC
        # =========================================

        # 1. Completed coaches ONLY
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

        # 2. NOT completed
        else:
            if due_date is None:
                item["classification_reason"] = "No due date set"
                work_in_progress.append(item)

            elif days_left < 0:
                item["classification_reason"] = "Overdue and not completed"
                urgent.append(item)

            elif 0 <= days_left <= 7:
                item["classification_reason"] = "Due within 7 days and not completed"
                urgent.append(item)

            elif 8 <= days_left <= 21:
                item["classification_reason"] = "Due within 8–21 days and not completed"
                approaching.append(item)

            else:
                item["classification_reason"] = "More than 21 days remaining"
                work_in_progress.append(item)

    return render_template(
        "delivery_schedule.html",
        today=today.strftime("%d %b %Y"),
        route_version=route_version,
        on_schedule=on_schedule,
        completed_late=completed_late,
        work_in_progress=work_in_progress,
        approaching=approaching,
        urgent=urgent
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

    with app.app_context():
        db.create_all()

        if not User.query.filter_by(username="admin").first():
            admin = User(username="admin", role="admin")
            admin.set_password("Admin@123")
            db.session.add(admin)
            db.session.commit()
            print("Default admin created: username = admin, password = Admin@123")

    port = int(os.environ.get("PORT", 8088))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )