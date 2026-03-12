# === Imports (keep at top) ===
import os
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from functools import wraps

# Import models AFTER app creation
from models import db, User, Coach, CompletionTask

# === Create Flask app FIRST ===
app = Flask(__name__)

# Secret key – always from env in production
app.secret_key = os.environ.get("SECRET_KEY") or "coaches_secret_key_change_me_in_prod"

# Database config – Render-safe
database_url = os.environ.get("DATABASE_URL")
if database_url and database_url.startswith("postgres://"):
    database_url = "postgresql://" + database_url[11:]

app.config["SQLALCHEMY_DATABASE_URI"] = (
    database_url
    or os.environ.get("LOCAL_DATABASE_URL")
    or "postgresql://postgres:your_local_password@localhost:5432/coaches_db"  # CHANGE or remove
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Force modern psycopg3 compatibility (if using psycopg[binary])
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"future": True}

# === Initialize extensions ONLY ONCE, here ===
db.init_app(app)  # ← only this line – no duplicate db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# Role required decorator
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

# User loader
@login_manager.user_loader
def load_user(uid):
    return User.query.get(int(uid))


# Routes
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
    search_query = request.args.get('search', '').strip()
    coach_type_filter = request.args.get('coach_type', '')
    min_progress = request.args.get('min_progress', type=float)

    # Expire everything to avoid any cache
    db.session.expire_all()

    query = Coach.query
    if search_query:
        query = query.filter(
            db.or_(
                Coach.coach_number.ilike(f"%{search_query}%"),
                Coach.coach_type.ilike(f"%{search_query}%")
            )
        )
    if coach_type_filter:
        query = query.filter(Coach.coach_type == coach_type_filter)

    # Get fresh list
    coaches = query.all()

    print("List view - Sample stripping flags:", [c.stripping for c in coaches[:5]])
    print("List view - Stripping count from query:", sum(1 for c in coaches if c.stripping))


    # Apply min_progress filter on fresh objects
    if min_progress is not None:
        coaches = [c for c in coaches if c.calculate_progress()['percentage'] >= min_progress]

    coaches.sort(key=lambda c: c.coach_number)
    
    # Prepare data for stacked bar chart
    coach_progress_data = []
    for coach in coaches:
        progress = coach.calculate_progress()
        coach_progress_data.append({
            'coach_number': coach.coach_number,
            'phases': [
                {'name': 'Stripping', 'progress': progress['phases'][0]['progress']},
                {'name': 'Completion', 'progress': progress['phases'][1]['progress']},
                {'name': 'Serviceworthy', 'progress': progress['phases'][2]['progress']},
                {'name': 'Retention', 'progress': progress['phases'][3]['progress'] if len(progress['phases']) > 3 else 0}
            ]
        })    
        
        
    

    # Counts from fresh objects
    stripping_count = sum(1 for c in coaches if c.stripping)
    complete_count = sum(1 for c in coaches if c.complete)
    serviceworthy_count = sum(1 for c in coaches if c.serviceworthy)
    retention_count = sum(1 for c in coaches if c.retention)

    total = len(coaches)
    all_types = sorted(set(c.coach_type for c in coaches))

    # Debug print
    print("List view - Stripping count:", stripping_count)
    print("List view - Sample flags:", [c.stripping for c in coaches[:3]])

    return render_template(
        "coaches_list.html",
        coaches=coaches,
        total=total,
        stripping_count=stripping_count,
        complete_count=complete_count,
        serviceworthy_count=serviceworthy_count,
        retention_count=retention_count,
        search_query=search_query,
        coach_type_filter=coach_type_filter,
        min_progress=min_progress,
        all_types=all_types,
        coach_progress_data=coach_progress_data  # NEW
    )


@login_required
@role_required("admin", "editor")
@app.route("/coaches/add", methods=["GET", "POST"])
def coaches_add():
    if request.method == "POST":
        def parse_date(value):
            if not value or not value.strip():
                return None
            try:
                return datetime.strptime(value.strip(), "%Y-%m-%d").date()
            except ValueError:
                flash(f"Invalid date format: '{value}'. Use YYYY-MM-DD.", "danger")
                return None

        coach = Coach(
            coach_number=request.form.get("coach_number", "").strip(),
            coach_type=request.form.get("coach_type", "").strip(),
            stripping="stripping" in request.form,
            stripping_date=parse_date(request.form.get("stripping_date")),
            complete="complete" in request.form,
            completion_date=parse_date(request.form.get("completion_date")),
            serviceworthy="serviceworthy" in request.form,
            serviceworthy_date=parse_date(request.form.get("serviceworthy_date")),
            retention="retention" in request.form,
            retention_date=parse_date(request.form.get("retention_date")),
            notes=request.form.get("notes") or None,
            stripping_task_bogie = "stripping_task_bogie" in request.form,
            stripping_task_underframe = "stripping_task_underframe" in request.form,
            stripping_task_plumbing_piping = "stripping_task_plumbing_piping" in request.form,
            stripping_task_interior = "stripping_task_interior" in request.form,
            stripping_task_exterior = "stripping_task_exterior" in request.form,
            stripping_task_roof = "stripping_task_roof" in request.form,
            stripping_task_components = "stripping_task_components" in request.form,
            stripping_task_wiring = "stripping_task_wiring" in request.form,
            stripping_task_sole_bar = "stripping_task_sole_bar" in request.form,
            stripping_task_bogie_frame = "stripping_task_bogie_frame" in request.form,
            stripping_task_loose_components = "stripping_task_loose_components" in request.form,
            stripping_task_inspection = "stripping_task_inspection" in request.form,
            stripping_task_cleaning = "stripping_task_cleaning" in request.form,
            stripping_task_documentation = "stripping_task_documentation" in request.form,
            stripping_task_approval = "stripping_task_approval" in request.form
        )
        db.session.add(coach)
        db.session.commit()
        flash("Coach added successfully", "success")
        return redirect(url_for("coaches_list"))
    return render_template("coaches_add.html")

@login_required
@role_required("admin", "editor")
@app.route("/coaches/edit/<int:id>", methods=["GET", "POST"])
def coaches_edit(id):
    coach = Coach.query.get_or_404(id)

    if request.method == "POST":
            def parse_date(value):
                if not value or not value.strip():
                    return None
                try:
                    return datetime.strptime(value.strip(), "%Y-%m-%d").date()
                except ValueError:
                    flash(f"Invalid date format: '{value}'. Use YYYY-MM-DD.", "danger")
                    return None

            coach.coach_number = request.form.get("coach_number", "").strip()
            coach.coach_type   = request.form.get("coach_type", "").strip()

            # Main milestones (user direct input)
            coach.stripping           = "stripping"           in request.form
            coach.stripping_date      = parse_date(request.form.get("stripping_date"))
            coach.complete            = "complete"            in request.form
            coach.completion_date     = parse_date(request.form.get("completion_date"))
            coach.serviceworthy_date  = parse_date(request.form.get("serviceworthy_date"))
            coach.retention_date      = parse_date(request.form.get("retention_date"))
            coach.due_date            = parse_date(request.form.get("due_date"))

            # Stripping sub-tasks (all 15)
            coach.stripping_task_bogie               = "stripping_task_bogie"               in request.form
            coach.stripping_task_underframe          = "stripping_task_underframe"          in request.form
            coach.stripping_task_plumbing_piping     = "stripping_task_plumbing_piping"     in request.form
            coach.stripping_task_interior            = "stripping_task_interior"            in request.form
            coach.stripping_task_exterior            = "stripping_task_exterior"            in request.form
            coach.stripping_task_roof                = "stripping_task_roof"                in request.form
            coach.stripping_task_components          = "stripping_task_components"          in request.form
            coach.stripping_task_wiring              = "stripping_task_wiring"              in request.form
            coach.stripping_task_sole_bar            = "stripping_task_sole_bar"            in request.form
            coach.stripping_task_bogie_frame         = "stripping_task_bogie_frame"         in request.form
            coach.stripping_task_loose_components    = "stripping_task_loose_components"    in request.form
            coach.stripping_task_inspection          = "stripping_task_inspection"          in request.form
            coach.stripping_task_cleaning            = "stripping_task_cleaning"            in request.form
            coach.stripping_task_documentation       = "stripping_task_documentation"       in request.form
            coach.stripping_task_approval            = "stripping_task_approval"            in request.form

            # Completion tasks (reset + save)
            completion_updated = False
            for task in coach.completion_tasks:
                if task.completed:
                    task.completed = False
                    task.completed_date = None
                    completion_updated = True
            for key, value in request.form.items():
                if key.startswith('completion_task_'):
                    try:
                        task_id = int(key.split('_')[2])
                        task = CompletionTask.query.get(task_id)
                        if task and task.coach_id == coach.id:
                            task.completed = True
                            if not task.completed_date:
                                task.completed_date = datetime.now().date()
                            completion_updated = True
                    except (ValueError, IndexError):
                        continue

            # New simplified Serviceworthy & Retention questions
            coach.serviceworthy_ncr_gc        = "serviceworthy_ncr_gc"        in request.form
            coach.serviceworthy_certificate   = "serviceworthy_certificate"   in request.form
            coach.retention_ncr_gc            = "retention_ncr_gc"            in request.form
            coach.retention_certificate       = "retention_certificate"       in request.form

            # Auto-sync main flags (strict logic: NO NCR/GC AND Certificate issued)
            coach.serviceworthy = (
                not coach.serviceworthy_ncr_gc      # No active issues (unchecked)
                and coach.serviceworthy_certificate # Certificate issued (checked)
            )

            coach.retention = (
                not coach.retention_ncr_gc          # No active issues (unchecked)
                and coach.retention_certificate     # Certificate issued (checked)
            )

            # Make Complete fully responsive to sub-tasks
            completion_tasks = coach.completion_tasks
            total = len(completion_tasks)
            if total > 0:
                completed = sum(1 for t in completion_tasks if t.completed)
                coach.complete = (completed == total)
                if coach.complete and not coach.completion_date:
                    coach.completion_date = datetime.now().date()

            # Invoicing record-keeping
            coach.invoice_stripping            = "invoice_stripping"            in request.form
            coach.invoice_completion           = "invoice_completion"           in request.form
            coach.invoice_serviceworthy        = "invoice_serviceworthy"        in request.form
            coach.invoice_retention            = "invoice_retention"            in request.form
            coach.invoice_current_escalation   = "invoice_current_escalation"   in request.form

            coach.notes = request.form.get("notes") or None

            # Force sync Stripping flag (safety)
            coach.stripping = all([
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
            ])

            coach.calculate_progress()

            print("Before commit - Stripping flag:", coach.stripping)

            db.session.commit()
            db.session.expire(coach)

            flash("Coach updated successfully", "success")
            return redirect(url_for("coaches_list", updated=coach.coach_number))

    # GET: force fresh data
    coach = Coach.query.get_or_404(id)
    db.session.expire(coach)
    return render_template("coaches_edit.html", coach=coach)



@login_required
@role_required("admin")
@app.route("/coaches/delete/<int:id>")
def coaches_delete(id):
    coach = Coach.query.get_or_404(id)
    db.session.delete(coach)
    db.session.commit()
    flash("Coach deleted successfully", "info")
    return redirect(url_for("coaches_list", updated=coach.coach_number))




@app.route("/delivery-schedule")
@login_required
def delivery_schedule():
    today = datetime.now().date()
    coaches = Coach.query.filter(Coach.due_date.isnot(None)).all()

    on_schedule = []
    completed_late = []
    approaching = []
    urgent = []

    for coach in coaches:
        days_left = (coach.due_date - today).days if coach.due_date else None

        # Retention countdown: only for non-Trailer coaches
        retention_countdown = "Serviceworthy not set yet"
        retention_due_date = "Not set"
        if coach.coach_type.lower() == 'trailer':
            retention_countdown = "Not applicable (Trailer)"
            retention_due_date = "Not applicable (Trailer)"
        elif coach.serviceworthy_date:
            retention_due = coach.serviceworthy_date + timedelta(days=14)
            retention_due_date = retention_due.strftime('%d %b %Y')
            days_to_retention = (retention_due - today).days
            if days_to_retention >= 0:
                retention_countdown = f"{days_to_retention} days remaining"
            else:
                retention_countdown = f"Overdue by {-days_to_retention} days"

        # Commissioned / Handed Over status – type-dependent
        status = "In Progress"
        if coach.coach_type.lower() == 'trailer':
            if coach.serviceworthy_certificate:
                status = "Commissioned / Handed Over to Client"
        else:
            if coach.retention_certificate:
                status = "Commissioned / Handed Over to Client"

        # For trailers: days left/overdue = 0 after commissioned
        days_display = "0 (Commissioned)" if status == "Commissioned to client" else (
            f"{days_left} days" if days_left is not None and days_left >= 0 else f"Overdue by {-days_left} days" if days_left is not None else "N/A"
        )

        item = {
            'coach': coach,
            'coach_number': coach.coach_number,
            'coach_type': coach.coach_type,
            'completion_date': coach.completion_date.strftime('%d %b %Y') if coach.completion_date else 'Not completed',
            'due_date': coach.due_date.strftime('%d %b %Y') if coach.due_date else 'No due date',
            'days_left': days_left,
            'days_display': days_display,
            'retention_countdown': retention_countdown,
            'retention_due_date': retention_due_date,
            'status': status
        }
        
        # Get progress breakdown for stacked bar
        progress = coach.calculate_progress()
        item['progress_data'] = {
            'percentage': progress['percentage'],
            'phases': progress['phases']  # list of dicts: name, progress, completed, total
        }
         
        
        
        if coach.complete:
            if coach.completion_date and coach.due_date and coach.completion_date <= coach.due_date:
                on_schedule.append(item)
            else:
                completed_late.append(item)
        elif days_left is not None:
            if 8 <= days_left <= 21:
                approaching.append(item)
            else:
                urgent.append(item)

    # Sort lists correctly using actual date objects
    on_schedule.sort(key=lambda x: x['coach'].completion_date or datetime.max.date())
    completed_late.sort(key=lambda x: x['coach'].completion_date or datetime.max.date())
    approaching.sort(key=lambda x: x['days_left'] if x['days_left'] is not None else 999)
    urgent.sort(key=lambda x: x['days_left'] if x['days_left'] is not None else 999)

    return render_template(
        "delivery_schedule.html",
        on_schedule=on_schedule,
        completed_late=completed_late,
        approaching=approaching,
        urgent=urgent,
        today=today.strftime('%d %b %Y')
    )




if __name__ == "__main__":
    # Local development only
    # Create tables & default admin (safe inside context)
    with app.app_context():
        db.create_all()  # creates tables if they don't exist
        # Create default admin if not exists
        if not User.query.filter_by(username="admin").first():
            admin = User(username="admin", role="admin")
            admin.set_password("Admin@123")  # CHANGE THIS in production!
            db.session.add(admin)
            db.session.commit()
            print("Default admin created: username = admin, password = Admin@123")

    # Run Flask dev server (local only)
# === Local run block – at the VERY BOTTOM ===
# === Local run block – at the VERY BOTTOM ===
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username="admin").first():
            admin = User(username="admin", role="admin")
            admin.set_password("Admin@123")  # CHANGE THIS in production!
            db.session.add(admin)
            db.session.commit()
            print("Default admin created: username = admin, password = Admin@123")

    port = int(os.environ.get("PORT", 8088))
    app.run(debug=True, host="0.0.0.0", port=port)