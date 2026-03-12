# -*- coding: utf-8 -*-
"""
HAESSA Coaches Dashboard
Created on Sat Jan 31 15:01:33 2026
@author: pmmto
"""

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from functools import wraps
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "coaches_secret_key_change_me_in_prod")

# Database configuration
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///coaches.db")
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

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

# Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default="viewer")

    def set_password(self, raw):
        self.password = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password, raw)

class Coach(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    coach_number = db.Column(db.String(50), unique=True, nullable=False)
    coach_type = db.Column(db.String(100), nullable=False)
    
    stripping = db.Column(db.Boolean, default=False)
    stripping_date = db.Column(db.Date, nullable=True)
    
    complete = db.Column(db.Boolean, default=False)
    completion_date = db.Column(db.Date, nullable=True)
    
    serviceworthy = db.Column(db.Boolean, default=False)
    serviceworthy_date = db.Column(db.Date, nullable=True)
    
    retention = db.Column(db.Boolean, default=False)
    retention_date = db.Column(db.Date, nullable=True)
    
    notes = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f"<Coach {self.coach_number}>"

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

@login_required
@app.route("/coaches")
def coaches_list():
    coaches = Coach.query.order_by(Coach.coach_number).all()
    return render_template("coaches_list.html", coaches=coaches)

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
            notes=request.form.get("notes") or None
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
        coach.coach_type = request.form.get("coach_type", "").strip()
        coach.stripping = "stripping" in request.form
        coach.stripping_date = parse_date(request.form.get("stripping_date"))
        coach.complete = "complete" in request.form
        coach.completion_date = parse_date(request.form.get("completion_date"))
        coach.serviceworthy = "serviceworthy" in request.form
        coach.serviceworthy_date = parse_date(request.form.get("serviceworthy_date"))
        coach.retention = "retention" in request.form
        coach.retention_date = parse_date(request.form.get("retention_date"))
        coach.notes = request.form.get("notes") or None

        db.session.commit()
        flash("Coach updated successfully", "success")
        return redirect(url_for("coaches_list"))

    return render_template("coaches_edit.html", coach=coach)

@login_required
@role_required("admin")
@app.route("/coaches/delete/<int:id>")
def coaches_delete(id):
    coach = Coach.query.get_or_404(id)
    db.session.delete(coach)
    db.session.commit()
    flash("Coach deleted successfully", "info")
    return redirect(url_for("coaches_list"))

# ---------------------------------------------------------------
# RUN
# ---------------------------------------------------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        # Create default admin if not exists
        if not User.query.filter_by(username="admin").first():
            admin = User(username="admin", role="admin")
            admin.set_password("Admin@123")
            db.session.add(admin)
            db.session.commit()
            print("Default admin created: username = admin, password = Admin@123")
    
    app.run(debug=True, host="0.0.0.0", port=8088)