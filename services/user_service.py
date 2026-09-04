"""
services/user_service.py
Service layer for managing local users and authentication.
"""
from __future__ import annotations
from pathlib import Path
from core.models import db, User

def bootstrap_admin(app):
    """Ensure at least one admin user exists."""
    with app.app_context():
        # Ensure DB is created
        db.create_all()
        
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            print("[AUTH] Bootstrapping default admin account (admin/admin)")
            admin = User(username='admin', role='admin,approver')
            admin.set_password('admin')
            db.session.add(admin)
            db.session.commit()

def create_user(username, password, roles='user'):
    """Create a new local user account with one or more roles."""
    if User.query.filter_by(username=username).first():
        return False, "User already exists"
    
    # Normalize roles string (ensure it's comma separated)
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
    
    if user.username == 'admin':
        return False, "Cannot delete protected system account"
        
    db.session.delete(user)
    db.session.commit()
    return True, "User deleted"

def reset_password(user_id, new_password):
    """Reset a user's password."""
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
        
        # Ensure 'admin' user always keeps the admin role
        if user.username == 'admin' and 'admin' not in roles:
            roles = f"admin,{roles}" if roles else "admin"
            
        user.role = roles
        
    if password:
        user.set_password(password)
        
    db.session.commit()
    return True, "User updated successfully"

def get_all_users():
    """Return all local users."""
    return User.query.all()
