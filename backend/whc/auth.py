from functools import wraps
import secrets

from flask import Blueprint, current_app, g, jsonify, request, session
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from .models import db, User
from .validation import ApiError

bp = Blueprint("auth", __name__)


def login_required(function):
    @wraps(function)
    def decorated(*args, **kwargs):
        if not getattr(g, "user", None):
            raise ApiError("Sign in to continue", 401, "authentication_required")
        return function(*args, **kwargs)
    return decorated


def admin_required(function):
    @wraps(function)
    @login_required
    def decorated(*args, **kwargs):
        if g.user.role != "admin":
            raise ApiError("Administrator access is required", 403, "forbidden")
        return function(*args, **kwargs)
    return decorated


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def password_valid(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ApiError("Use a password between 12 and 256 characters")


def user_credentials(data):
    if not isinstance(data, dict):
        raise ApiError("Request body must be a JSON object")
    username = data.get("username", "")
    if not isinstance(username, str) or not 2 <= len(username.strip()) <= 80:
        raise ApiError("Username must contain 2 to 80 characters")
    password_valid(data.get("password"))
    return username.strip(), data["password"]


@bp.get("/auth/session")
def auth_session():
    return jsonify(user=g.user.to_dict() if g.user else None, csrf_token=csrf_token(),
                   setup_required=db.session.scalar(select(User.id).limit(1)) is None)


@bp.post("/auth/setup")
def setup():
    if db.session.scalar(select(User.id).limit(1)):
        raise ApiError("The household has already been set up", 409, "setup_complete")
    username, password = user_credentials(request.get_json(silent=True) or {})
    user = User(username=username, password_hash=generate_password_hash(password), role="admin")
    db.session.add(user)
    db.session.commit()
    session.clear()
    session["user_id"] = user.id
    return jsonify(user=user.to_dict(), csrf_token=csrf_token()), 201


@bp.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        raise ApiError("Request body must be a JSON object")
    username, password = data.get("username", ""), data.get("password", "")
    if not isinstance(username, str) or not isinstance(password, str) or len(password) > 256:
        raise ApiError("Incorrect username or password", 401, "invalid_credentials")
    user = db.session.scalar(select(User).where(User.username == username.strip()))
    # Perform a hash calculation even for an unknown username.
    valid = check_password_hash(user.password_hash, password) if user else check_password_hash(current_app.config["DUMMY_PASSWORD_HASH"], password)
    if not user or not user.active or not valid:
        raise ApiError("Incorrect username or password", 401, "invalid_credentials")
    session.clear()
    session["user_id"] = user.id
    return jsonify(user=user.to_dict(), csrf_token=csrf_token())


@bp.post("/auth/logout")
@login_required
def logout():
    session.clear()
    return jsonify(message="Signed out", csrf_token=csrf_token())
