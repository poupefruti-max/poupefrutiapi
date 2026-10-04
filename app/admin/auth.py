"""Blueprint de autenticação do admin: /api/v1/admin/auth."""

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import create_access_token, get_jwt_identity

from ..extensions import db, limiter
from ..models import AdminUser
from ..validators import normalizar_email
from .decorators import admin_required

admin_auth_bp = Blueprint("admin_auth", __name__, url_prefix="/api/v1/admin/auth")


def erro(code: str, message: str, status: int):
    """Resposta de erro no formato padrão da API."""
    return jsonify({"error": {"code": code, "message": message}}), status


def _payload() -> dict:
    """Lê o corpo JSON aceitando apenas objetos."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# POST /api/v1/admin/auth/login
# ---------------------------------------------------------------------------
@admin_auth_bp.post("/login")
@limiter.limit("5 per minute")
def login():
    data = _payload()

    email = normalizar_email(data.get("email"))
    password = data.get("password") or ""

    if not email or not password:
        return erro("missing_credentials", "Informe e-mail e senha.", 400)

    admin = AdminUser.query.filter_by(email=email).first()

    # Mensagem genérica: não revela se o e-mail existe.
    if admin is None or not admin.check_password(password):
        current_app.logger.warning("Tentativa de login admin falhou para email=%s", email)
        return erro("invalid_credentials", "E-mail ou senha inválidos.", 401)

    # Token com claim de role admin
    token = create_access_token(
        identity=str(admin.id),
        additional_claims={"role": "admin"}
    )

    return jsonify({"access_token": token, "admin": admin.to_dict()}), 200


# ---------------------------------------------------------------------------
# GET /api/v1/admin/auth/me
# ---------------------------------------------------------------------------
@admin_auth_bp.get("/me")
@admin_required
def me():
    admin_id = get_jwt_identity()
    admin = db.session.get(AdminUser, int(admin_id))
    if admin is None:
        return erro("admin_not_found", "Administrador não encontrado.", 404)

    return jsonify({"admin": admin.to_dict()}), 200
