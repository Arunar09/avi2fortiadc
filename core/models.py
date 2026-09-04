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
    role          = db.Column(db.String(100), default='user')
    created_at    = db.Column(db.DateTime, default=db.func.now())

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

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
