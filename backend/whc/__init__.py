"""Application factory. Importing this package does not open or create a database."""
from contextlib import nullcontext
from datetime import timedelta
from decimal import InvalidOperation
import importlib
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, request, session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.security import generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

from .models import db, User, Record
from .validation import ApiError

SCHEMA_REVISION = "0001_initial"


def create_app(config=None):
    app = Flask(__name__)
    # Production API has one private nginx peer; development has the Next proxy.
    # Both preserve the public host so same-origin CSRF checks include its port.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    data_dir = Path(os.environ.get("DATA_DIR", "./data")).resolve()
    app.config.from_mapping(
        DATA_DIR=str(data_dir), UPLOAD_DIR=os.environ.get("UPLOAD_DIR", str(data_dir / "uploads")),
        BACKUP_DIR=os.environ.get("BACKUP_DIR", str(data_dir / "backups")),
        DATABASE_URL=os.environ.get("DATABASE_URL", "sqlite:///" + str(data_dir / "hoardcounter.db").replace("\\", "/")),
        SECRET_KEY=os.environ.get("SECRET_KEY"), COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
        HOUSEHOLD_TIMEZONE=os.environ.get("HOUSEHOLD_TIMEZONE", "America/New_York"),
        MAX_CONTENT_LENGTH=100 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    if config:
        app.config.update(config)
    if not app.config["SECRET_KEY"]:
        if app.config.get("TESTING"):
            app.config["SECRET_KEY"] = "test-secret-not-for-production"
        else:
            raise RuntimeError("SECRET_KEY is required; generate a random secret before starting Hoard Counter")
    app.config["SQLALCHEMY_DATABASE_URI"] = app.config.get("SQLALCHEMY_DATABASE_URI", app.config["DATABASE_URL"])
    app.config["SESSION_COOKIE_SECURE"] = app.config["COOKIE_SECURE"]
    app.config["DUMMY_PASSWORD_HASH"] = generate_password_hash(secrets.token_urlsafe(32))
    for path in (app.config["DATA_DIR"], app.config["UPLOAD_DIR"], app.config["BACKUP_DIR"]):
        Path(path).mkdir(parents=True, exist_ok=True)
    db.init_app(app)
    from .auth import bp as auth_bp
    from .resources import bp as resources_bp
    app.register_blueprint(auth_bp, url_prefix="/api/v1")
    app.register_blueprint(resources_bp, url_prefix="/api/v1")
    for name in ("analytics", "exchange", "reports", "files", "admin"):
        try:
            module = importlib.import_module("whc." + name)
        except ModuleNotFoundError as error:
            if error.name != "whc." + name:
                raise
            continue
        app.register_blueprint(module.bp, url_prefix="/api/v1")

    @app.before_request
    def request_security():
        g.user = None
        g.mutation_guard = None
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            try:
                from .backup import mutation_lock
            except ImportError:
                mutation_lock = None
            guard = mutation_lock(app.config["DATA_DIR"], exclusive=False) if mutation_lock else nullcontext()
            guard.__enter__()
            g.mutation_guard = guard
            if (Path(app.config["DATA_DIR"]) / ".maintenance").exists():
                raise ApiError("Maintenance is in progress. Try again shortly.", 503, "maintenance")
            origin = request.headers.get("Origin")
            if origin:
                origin_parts = urlsplit(origin)
                if origin_parts.netloc != request.host or origin_parts.scheme not in ("http", "https"):
                    raise ApiError("Cross-origin mutations are not allowed", 403, "origin_rejected")
            if request.endpoint not in ("auth.login", "auth.setup"):
                token = request.headers.get("X-CSRF-Token", "")
                if not token or not secrets.compare_digest(token, session.get("csrf_token", "")):
                    raise ApiError("Your session token has expired. Reload and try again.", 403, "csrf_failed")
            if db.engine.dialect.name == "sqlite":
                # Serializes compare-and-swap revisions and subscription occurrence claims.
                db.session.execute(text("BEGIN IMMEDIATE"))
        uid = session.get("user_id")
        if uid:
            user = db.session.get(User, uid)
            if user and user.active:
                g.user = user
            else:
                session.clear()
        if g.user:
            settings = db.session.scalars(db.select(Record).where(Record.kind == "setting", Record.deleted.is_(False))).first()
            if settings and settings.data.get("timezone"):
                app.config["HOUSEHOLD_TIMEZONE"] = settings.data["timezone"]

    @app.teardown_request
    def release_mutation_guard(error):
        db.session.rollback()
        guard = getattr(g, "mutation_guard", None)
        if guard is not None:
            guard.__exit__(None, None, None)

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(ApiError)
    def api_error(error):
        db.session.rollback()
        return jsonify(error={"code": error.code, "message": error.message, "details": error.details}, message=error.message), error.status

    @app.errorhandler(IntegrityError)
    def integrity_error(error):
        db.session.rollback()
        return jsonify(error={"code": "conflict", "message": "This record conflicts with existing data"}), 409

    @app.errorhandler(ValueError)
    @app.errorhandler(KeyError)
    @app.errorhandler(InvalidOperation)
    def value_error(error):
        db.session.rollback()
        return jsonify(error={"code": "validation_error", "message": str(error)}, message=str(error)), 400

    @app.errorhandler(OperationalError)
    def unavailable(error):
        db.session.rollback()
        app.logger.exception("Database operation failed")
        return jsonify(error={"code": "database_unavailable", "message": "The database is temporarily unavailable"}), 503

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(error={"code": "too_large", "message": "Upload exceeds the 100 MB request limit"}), 413

    @app.get("/api/health")
    def health():
        try:
            db.session.execute(text("SELECT 1 FROM records LIMIT 1"))
            if not app.config.get("TESTING"):
                revision = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
                if revision != SCHEMA_REVISION:
                    return jsonify(status="unhealthy", reason="migration_required"), 503
            return jsonify(status="ok", schema=SCHEMA_REVISION)
        except Exception:
            db.session.rollback()
            return jsonify(status="unhealthy", reason="database_unavailable"), 503

    if app.config.get("TESTING"):
        with app.app_context():
            db.create_all()
            from .ledger import seed_system_accounts
            seed_system_accounts()
            db.session.commit()
    return app
