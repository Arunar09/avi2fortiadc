import os

import pytest
from flask import Flask

from core.events import sanitize
from core.models import db, User
from services.user_service import bootstrap_admin


def test_sanitize_redacts_common_secret_forms():
    text = """
    password=SuperSecret123
    "api_key": "abc123-secret"
    Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456
    Cookie: session=very-secret-cookie
    https://user:passw0rd@example.internal.local/api?token=topsecret&x=1
    access_token=secret-token
    -----BEGIN PRIVATE KEY-----
    private-key-material
    -----END PRIVATE KEY-----
    IPv6=2001:db8:1234::10/64
    IPv4=10.20.30.40
    """

    result = sanitize(text)

    for secret in [
        "SuperSecret123",
        "abc123-secret",
        "abcdefghijklmnopqrstuvwxyz123456",
        "very-secret-cookie",
        "passw0rd",
        "topsecret",
        "secret-token",
        "private-key-material",
    ]:
        assert secret not in result

    assert "CREDENTIAL_REDACTED" in result
    assert "KEY_MATERIAL_REDACTED" in result
    assert "IP6_" in result
    assert "IP_" in result


def test_sanitize_is_idempotent_for_redacted_values():
    source = "password=secret-value; 10.20.30.40; 2001:db8::1"
    once = sanitize(source)
    twice = sanitize(once)
    assert twice == once


def test_first_run_requires_explicit_bootstrap_password(monkeypatch, tmp_path):
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'identity.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)

    monkeypatch.delenv("MIGRATION_BOOTSTRAP_ADMIN_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="MIGRATION_BOOTSTRAP_ADMIN_PASSWORD"):
        bootstrap_admin(app)


def test_first_run_bootstraps_only_with_explicit_strong_password(monkeypatch, tmp_path):
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'identity.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)

    monkeypatch.setenv("MIGRATION_BOOTSTRAP_ADMIN_USERNAME", "bootstrap-admin")
    monkeypatch.setenv("MIGRATION_BOOTSTRAP_ADMIN_PASSWORD", "A-strong-bootstrap-password-123")

    bootstrap_admin(app)

    with app.app_context():
        user = User.query.filter_by(username="bootstrap-admin").one()
        assert user.has_role("admin")
        assert user.has_role("approver")
        assert user.check_password("A-strong-bootstrap-password-123")
        assert not user.check_password("admin")


def test_fortiadc_exists_fails_closed_on_non_404(monkeypatch):
    import requests
    from core.fortiadc_client import FortiADCClient

    client = FortiADCClient(
        "https://example.invalid",
        "user",
        "password",
        dry_run=True,
    )
    response = requests.Response()
    response.status_code = 403
    error = requests.HTTPError("forbidden", response=response)

    def denied(*args, **kwargs):
        raise error

    monkeypatch.setattr(client, "get", denied)
    with pytest.raises(requests.HTTPError):
        client.exists("objects", "name")


def test_fortiadc_exists_returns_false_only_for_404(monkeypatch):
    import requests
    from core.fortiadc_client import FortiADCClient

    client = FortiADCClient(
        "https://example.invalid",
        "user",
        "password",
        dry_run=True,
    )
    response = requests.Response()
    response.status_code = 404
    error = requests.HTTPError("not found", response=response)

    def missing(*args, **kwargs):
        raise error

    monkeypatch.setattr(client, "get", missing)
    assert client.exists("objects", "name") is False
