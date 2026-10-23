"""Testes do ciclo de vida das contas e sessões de usuário."""

from datetime import timedelta
from uuid import uuid4

from flask_jwt_extended import create_access_token
import pytest

from app.extensions import db
from app.models import AdminUser, RevokedToken, User


@pytest.fixture(autouse=True)
def auth_test_rate_limits(app):
    app.config.update(LOGIN_RATE_LIMIT="1000 per minute", LOGIN_EMAIL_RATE_LIMIT="1000 per minute")


def email_unico():
    return f"{uuid4().hex}@example.test"


def test_register_normalizes_email_and_ignores_privileged_fields(client, app):
    email = email_unico()
    response = client.post("/api/v1/auth/register", json={
        "name": "  Ana Cliente ", "email": f"  {email.upper()}  ",
        "password": "senha-segura-123", "role": "admin", "is_admin": True,
    })
    assert response.status_code == 201
    body = response.get_json()
    assert body["user"]["email"] == email
    assert "access_token" not in body
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.name == "Ana Cliente"
        assert user.check_password("senha-segura-123")
        assert not hasattr(user, "role")
        assert not AdminUser.query.filter_by(email=email).first()


def test_register_rejects_bad_fields_and_duplicate(client):
    for payload in (
        {"name": "Ana", "email": "sem-email", "password": "senha-segura"},
        {"name": " ", "email": email_unico(), "password": "senha-segura"},
        {"name": "Ana", "email": email_unico(), "password": "curta"},
        {"name": "Ana", "email": email_unico(), "password": "á" * 40},
    ):
        response = client.post("/api/v1/auth/register", json=payload)
        assert response.status_code == 400
    email = email_unico()
    payload = {"name": "Ana", "email": email, "password": "senha-segura"}
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    payload["email"] = email.upper()
    assert client.post("/api/v1/auth/register", json=payload).status_code == 409


def test_login_me_and_generic_invalid_credentials(client, app):
    email = email_unico()
    assert client.post("/api/v1/auth/register", json={
        "name": "Ana", "email": email, "password": "senha-segura",
    }).status_code == 201
    login = client.post("/api/v1/auth/login", json={"email": email.upper(), "password": "senha-segura"})
    assert login.status_code == 200
    token = login.get_json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.get_json()["user"]["email"] == email
    wrong = client.post("/api/v1/auth/login", json={"email": email, "password": "senha-errada"})
    missing = client.post("/api/v1/auth/login", json={"email": email_unico(), "password": "senha-errada"})
    assert wrong.status_code == missing.status_code == 401
    assert wrong.get_json()["error"] == missing.get_json()["error"]
    assert client.get("/api/v1/auth/me").status_code == 401


def test_user_logout_revokes_only_current_jwt(client, app):
    email = email_unico()
    client.post("/api/v1/auth/register", json={"name": "Ana", "email": email, "password": "senha-segura"})
    token = client.post("/api/v1/auth/login", json={"email": email, "password": "senha-segura"}).get_json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    with app.app_context():
        assert db.session.query(RevokedToken).count() >= 1


def test_expired_token_and_role_boundaries(client, app):
    email = email_unico()
    admin_email = email_unico()
    client.post("/api/v1/auth/register", json={"name": "Ana", "email": email, "password": "senha-segura"})
    with app.app_context():
        admin = AdminUser(email=admin_email, name="Admin")
        admin.set_password("senha-segura")
        db.session.add(admin)
        db.session.commit()
        expired = create_access_token(identity="1", expires_delta=timedelta(seconds=-1))
    admin_login = client.post("/api/v1/admin/auth/login", json={"email": admin_email, "password": "senha-segura"})
    admin_token = admin_login.get_json()["access_token"]
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {admin_token}"}).status_code == 403
    user_login = client.post("/api/v1/auth/login", json={"email": email, "password": "senha-segura"})
    user_token = user_login.get_json()["access_token"]
    assert client.get("/api/v1/admin/auth/me", headers={"Authorization": f"Bearer {user_token}"}).status_code == 403
    assert client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    assert client.post("/api/v1/admin/auth/logout", headers={"Authorization": f"Bearer {admin_token}"}).status_code == 200
    assert client.get("/api/v1/admin/auth/me", headers={"Authorization": f"Bearer {admin_token}"}).status_code == 401
