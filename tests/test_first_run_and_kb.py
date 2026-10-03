import os
from datetime import datetime, timezone, timedelta
from flask import Flask
from core.models import db, User
from services.user_service import bootstrap_admin, change_password
from ui.app import create_app


def test_default_bootstrap_admin_created_with_7_day_grace(tmp_path):
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'identity.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        ALLOW_DEFAULT_BOOTSTRAP=True,
    )
    db.init_app(app)

    # First run without environment variable
    bootstrap_admin(app)

    with app.app_context():
        admin = User.query.filter_by(username="admin").first()
        assert admin is not None
        assert admin.check_password("Admin@Migration123!")
        assert admin.must_change_password is True
        assert admin.password_expires_at is not None
        assert admin.is_default_password_expired() is False
        assert 6 <= admin.days_until_password_expiry() <= 7


def test_change_password_workflow(tmp_path):
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'identity.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        ALLOW_DEFAULT_BOOTSTRAP=True,
    )
    db.init_app(app)
    bootstrap_admin(app)

    with app.app_context():
        admin = User.query.filter_by(username="admin").first()
        # Invalid current password
        ok, msg = change_password(admin.id, "WrongPassword!", "NewStrongPassword123!")
        assert not ok
        assert "incorrect" in msg.lower()

        # New password too short
        ok, msg = change_password(admin.id, "Admin@Migration123!", "short")
        assert not ok
        assert "12 characters" in msg

        # Valid password change
        ok, msg = change_password(admin.id, "Admin@Migration123!", "NewStrongPassword123!")
        assert ok
        admin = User.query.filter_by(username="admin").first()
        assert admin.check_password("NewStrongPassword123!")
        assert admin.must_change_password is False
        assert admin.password_expires_at is None


def test_kb_doc_viewer_renders_markdown(tmp_path):
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'identity.db'}",
        "ALLOW_DEFAULT_BOOTSTRAP": True,
    })
    client = app.test_client()

    # Log in as default admin
    r_login = client.post("/login", data={"username": "admin", "password": "Admin@Migration123!"}, follow_redirects=True)
    assert r_login.status_code == 200

    # View Knowledge Base list
    r_kb = client.get("/kb")
    assert r_kb.status_code == 200
    assert b"/kb/doc/" in r_kb.data

    # View specific doc (e.g. USER-GUIDE.md)
    r_doc = client.get("/kb/doc/USER-GUIDE.md")
    assert r_doc.status_code == 200
    assert b"User Guide" in r_doc.data
    assert b"docs/USER-GUIDE.md" in r_doc.data

    # Path traversal protection
    r_bad = client.get("/kb/doc/../../etc/passwd")
    assert r_bad.status_code in (400, 404)
