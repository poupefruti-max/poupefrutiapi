"""CORS restrito e respostas de /auth/me em cenários de sessão."""

import pytest
from datetime import timedelta
from flask_jwt_extended import create_access_token

from app import create_app
from app.extensions import db


ORIGIN = "https://poupefruti.vercel.app"
FOREIGN_ORIGIN = "https://site-malicioso.example"


@pytest.fixture
def cors_app():
    app = create_app("testing")
    app.config.update(
        TESTING=False,
        PROPAGATE_EXCEPTIONS=False,
        DEBUG=False,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_ENGINE_OPTIONS={},
        CORS_ORIGINS=[ORIGIN],
        RATELIMIT_DEFAULT="",
    )
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.drop_all()


@pytest.fixture
def client(cors_app):
    return cors_app.test_client()


def user_token(client):
    created = client.post("/api/v1/auth/register", json={
        "name": "Cliente CORS",
        "email": "cors-user@example.test",
        "password": "senha-segura-123",
    })
    assert created.status_code == 201
    login = client.post("/api/v1/auth/login", json={
        "email": "cors-user@example.test",
        "password": "senha-segura-123",
    })
    assert login.status_code == 200
    return login.get_json()["access_token"]


def assert_cors(response, expected_origin=ORIGIN):
    assert response.headers.get("Access-Control-Allow-Origin") == expected_origin


def test_auth_me_returns_cors_401_without_token(client):
    response = client.get("/api/v1/auth/me", headers={"Origin": ORIGIN})
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "authorization_required"
    assert response.get_json()["error"]["message"] == "Autenticação necessária."
    assert_cors(response)


def test_auth_me_returns_cors_401_with_invalid_token(client):
    response = client.get("/api/v1/auth/me", headers={
        "Origin": ORIGIN,
        "Authorization": "Bearer not-a-jwt",
    })
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "invalid_token"
    assert "not-a-jwt" not in response.get_data(as_text=True)
    assert_cors(response)


def test_auth_me_returns_cors_401_with_expired_token(cors_app, client):
    with cors_app.app_context():
        token = create_access_token(identity="1", expires_delta=timedelta(seconds=-1))
    response = client.get("/api/v1/auth/me", headers={
        "Origin": ORIGIN,
        "Authorization": f"Bearer {token}",
    })
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "token_expired"
    assert_cors(response)


def test_auth_me_returns_cors_200_with_valid_token(client):
    token = user_token(client)
    response = client.get("/api/v1/auth/me", headers={
        "Origin": ORIGIN,
        "Authorization": f"Bearer {token}",
    })
    assert response.status_code == 200
    assert response.get_json()["user"]["email"] == "cors-user@example.test"
    assert_cors(response)


def test_auth_me_returns_cors_401_after_token_revocation(client):
    token = user_token(client)
    headers = {"Origin": ORIGIN, "Authorization": f"Bearer {token}"}
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    response = client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "revoked_token"
    assert_cors(response)


def test_auth_me_rejects_cors_for_unlisted_origin(client):
    token = user_token(client)
    response = client.get("/api/v1/auth/me", headers={
        "Origin": FOREIGN_ORIGIN,
        "Authorization": f"Bearer {token}",
    })
    assert response.status_code == 200
    assert_cors(response, expected_origin=None)


def test_auth_me_preflight_allows_authorization_header(client):
    response = client.options("/api/v1/auth/me", headers={
        "Origin": ORIGIN,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    })
    assert response.status_code == 200
    assert_cors(response)
    assert "authorization" in response.headers.get("Access-Control-Allow-Headers", "").lower()
