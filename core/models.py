"""
core/models.py
Database models for the AVI -> FortiADC Migration Tool.
"""
from __future__ import annotations
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

class User(db.Model, UserMixin):
    """
    Local user account for tool access control.
    Roles: 'admin', 'user'
    """
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role                  = db.Column(db.String(100), default='user')
    created_at            = db.Column(db.DateTime, default=db.func.now())
    must_change_password  = db.Column(db.Boolean, default=False)
    password_expires_at   = db.Column(db.DateTime, nullable=True)

    def set_password(self, password: str, must_change: bool = False, expires_in_days: int | None = None):
        self.password_hash = generate_password_hash(password)
        self.must_change_password = must_change
        if expires_in_days:
            from datetime import datetime, timezone, timedelta
            self.password_expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        else:
            self.password_expires_at = None

    def is_default_password_expired(self) -> bool:
        """Returns True if the temporary default password has passed its grace period."""
        if not self.must_change_password or not self.password_expires_at:
            return False
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        expires = self.password_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return now > expires

    def days_until_password_expiry(self) -> int:
        """Returns remaining days before the temporary password expires."""
        if not self.password_expires_at:
            return 999
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        expires = self.password_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        diff = (expires - now).total_seconds()
        return max(0, int(diff // 86400))

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def get_roles(self) -> list[str]:
        """Return roles as a list of trimmed strings."""
        if not self.role: return []
        return [r.strip().lower() for r in self.role.split(',') if r.strip()]

    def has_role(self, role_name: str) -> bool:
        """Check if the user has a specific role tag."""
        return role_name.lower() in self.get_roles()

    def is_admin(self) -> bool:
        """Legacy helper for single-admin check logic."""
        return self.has_role('admin')

    def __repr__(self):
        return f'<User {self.username} ({self.role})>'
