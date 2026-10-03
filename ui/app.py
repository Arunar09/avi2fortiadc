"""
ui/app.py
Flask application factory for the AVI -> FortiADC Migration Operator Console.

Design constraints:
- Air-gapped: no CDN, no external assets, no remote fonts
- Thin routes: all data comes from the service layer
- No execution logic in routes or app factory
- Templates rendered server-side via Jinja2
- LLM features are clearly marked optional/advisory
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import Flask


def create_app(config: dict | None = None) -> Flask:
    """
    Create and configure the Flask application.

    Production security requires an explicit Flask secret via
    FLASK_SECRET_KEY or the supplied config. A deterministic fallback is
    never used.
    """
    tool_root = Path(__file__).resolve().parent.parent

    app = Flask(
        __name__,
        template_folder=str(tool_root / "templates"),
        static_folder=str(tool_root / "static"),
    )

    app.config["TOOL_ROOT"] = str(tool_root)
    app.config["TOOL_CONFIG_PATH"] = str(tool_root / "config.yaml")
    app.config["STATE_DIR"] = str(tool_root / "state")
    app.config["REPORTS_DIR"] = str(tool_root / "reports")
    app.config["DISCOVERY_DIR"] = str(tool_root / "discovery")
    app.config["LOGS_DIR"] = str(tool_root / "logs")
    app.config["FORTIADC_DIR"] = str(tool_root / "fortiadc")
    app.config["DEBUG"] = False
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_SESSION_COOKIE_SECURE", "false").lower() == "true"
    app.config["PERMANENT_SESSION_LIFETIME"] = 3600

    # Identity DB
    identity_db = Path(app.config["STATE_DIR"]) / "identity.db"
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{identity_db}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    if config:
        app.config.update(config)

    secret_key = app.config.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY")
    if not secret_key:
        if app.config.get("TESTING"):
            secret_key = "test-only-secret-not-for-production"
        else:
            raise RuntimeError(
                "FLASK_SECRET_KEY is required for the operator console. "
                "Generate a high-entropy secret and provide it through the environment."
            )
    if isinstance(secret_key, str) and len(secret_key) < 32 and not app.config.get("TESTING"):
        raise RuntimeError("FLASK_SECRET_KEY must be at least 32 characters.")
    app.config["SECRET_KEY"] = secret_key

    from core.models import db, User
    from flask_login import LoginManager, AnonymousUserMixin
    from flask_wtf.csrf import CSRFProtect

    db.init_app(app)
    CSRFProtect(app)

    class AnonymousUser(AnonymousUserMixin):
        def has_role(self, role):
            return False

        def get_roles(self):
            return []

        def is_admin(self):
            return False

    login_manager = LoginManager()
    login_manager.login_view = "main.login"
    login_manager.anonymous_user = AnonymousUser
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from services.user_service import bootstrap_admin
    bootstrap_admin(app)

    from ui.routes import bp
    app.register_blueprint(bp)
    return app
