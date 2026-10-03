"""
services/user_service.py
Service layer for managing local users and authentication.
"""
from __future__ import annotations

import os

from core.models import db, User


def bootstrap_admin(app):
    """Create the first admin only from explicit bootstrap credentials.

    There is intentionally no universal/default password. Existing installations
    are left untouched. New installations must provide
    MIGRATION_BOOTSTRAP_ADMIN_PASSWORD and may optionally provide
    MIGRATION_BOOTSTRAP_ADMIN_USERNAME.
    """
    with app.app_context():
        db.create_all()

        # Migrate existing SQLite databases if columns are missing
        try:
            with db.engine.connect() as conn:
                cols = {row[1] for row in conn.execute(db.text("PRAGMA table_info(user)")).fetchall()}
                if "must_change_password" not in cols:
                    conn.execute(db.text("ALTER TABLE user ADD COLUMN must_change_password BOOLEAN DEFAULT 0"))
                if "password_expires_at" not in cols:
                    conn.execute(db.text("ALTER TABLE user ADD COLUMN password_expires_at DATETIME"))
                conn.commit()
        except Exception:
            pass

        if User.query.first():
            return

        username = os.environ.get("MIGRATION_BOOTSTRAP_ADMIN_USERNAME", "admin").strip()
        password = os.environ.get("MIGRATION_BOOTSTRAP_ADMIN_PASSWORD")

        if not password:
            if app.config.get("ALLOW_DEFAULT_BOOTSTRAP", False):
                default_pass = "Admin@Migration123!"
                admin = User(username=username, role="admin,approver")
                admin.set_password(default_pass, must_change=True, expires_in_days=7)
                db.session.add(admin)
                db.session.commit()
                import logging
                logging.getLogger(__name__).info(
                    "[*] First-run setup: Default administrator created (Username: %s | Password: %s). "
                    "Temporary login valid for 7 days. Please change upon first login.",
                    username, default_pass
                )
                return

            raise RuntimeError(
                "No local users exist. Set MIGRATION_BOOTSTRAP_ADMIN_PASSWORD "
                "before starting the operator console for first-run provisioning."
            )
        if len(password) < 12:
            raise RuntimeError(
                "MIGRATION_BOOTSTRAP_ADMIN_PASSWORD must contain at least 12 characters."
            )
        if username.lower() == "admin" and password.lower() == "admin":
            raise RuntimeError("The insecure admin/admin bootstrap credential is not allowed.")

        admin = User(username=username, role="admin,approver")
        admin.set_password(password, must_change=False)
        db.session.add(admin)
        db.session.commit()


def change_password(user_id, current_password, new_password):
    """Change a user's password verifying the current password."""
    if not isinstance(new_password, str) or len(new_password) < 12:
        return False, "New password must contain at least 12 characters."
    user = User.query.get(user_id)
    if not user:
        return False, "User not found"
    if not user.check_password(current_password):
        return False, "Current password is incorrect."
    user.set_password(new_password, must_change=False)
    db.session.commit()
    return True, "Password updated successfully"


def create_user(username, password, roles="user"):
    """Create a new local user account with one or more roles."""
    if not isinstance(password, str) or len(password) < 12:
        return False, "Password must contain at least 12 characters."
    if User.query.filter_by(username=username).first():
        return False, "User already exists"

    if isinstance(roles, list):
        roles = ",".join(roles)

    new_user = User(username=username, role=roles)
    new_user.set_password(password)
    db.session.add(new_user)
    db.session.commit()
    return True, "User created successfully"


def delete_user(user_id):
    """Delete a local user account. Admin cannot delete themselves."""
    user = User.query.get(user_id)
    if not user:
        return False, "User not found"

    if user.username == "admin":
        return False, "Cannot delete protected system account"

    db.session.delete(user)
    db.session.commit()
    return True, "User deleted"


def reset_password(user_id, new_password):
    """Reset a user's password."""
    if not isinstance(new_password, str) or len(new_password) < 12:
        return False, "Password must contain at least 12 characters."
    user = User.query.get(user_id)
    if not user:
        return False, "User not found"

    user.set_password(new_password)
    db.session.commit()
    return True, "Password updated"


def update_user(user_id, roles=None, password=None):
    """Update user details."""
    user = User.query.get(user_id)
    if not user:
        return False, "User not found"

    if roles is not None:
        if isinstance(roles, list):
            roles = ",".join(roles)

        if user.username == "admin" and "admin" not in roles:
            roles = f"admin,{roles}" if roles else "admin"

        user.role = roles

    if password:
        if not isinstance(password, str) or len(password) < 12:
            return False, "Password must contain at least 12 characters."
        user.set_password(password)

    db.session.commit()
    return True, "User updated successfully"


def get_all_users():
    """Return all local users."""
    return User.query.all()
