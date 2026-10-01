"""Blueprint de autenticação: /api/v1/auth."""

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import (
    create_access_token,
    get_jwt_identity,
    jwt_required,
)
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import User
from ..validators import email_valido, normalizar_email

auth_bp = Blueprint("auth", __name__, url_prefix="/api/v1/auth")


def erro(code: str, message: str, status: int):
    """Resposta de erro no formato padrão da API."""
    return jsonify({"error": {"code": code, "message": message}}), status


def _payload() -> dict:
    """Lê o corpo JSON aceitando apenas objetos."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# POST /api/v1/auth/register
# ---------------------------------------------------------------------------
@auth_bp.post("/register")
def register():
    data = _payload()

    email = normalizar_email(data.get("email"))
    password = data.get("password") or ""
    name = (data.get("name") or "").strip() or None
    min_len = current_app.config["MIN_PASSWORD_LENGTH"]

    if not email_valido(email):
        return erro("invalid_email", "Informe um e-mail válido.", 400)

    if not isinstance(password, str) or len(password) < min_len:
        return erro(
            "weak_password",
            f"A senha deve ter ao menos {min_len} caracteres.",
            400,
        )

    user = User(email=email, name=name)
    user.set_password(password)

    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        # Corrida entre duas requisições com o mesmo e-mail.
        db.session.rollback()
        return erro("email_already_exists", "Este e-mail já está cadastrado.", 409)

    return (
        jsonify(
            {
                "message": "Conta criada com sucesso.",
                "user": user.to_dict(),
            }
        ),
        201,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/auth/login
# ---------------------------------------------------------------------------
@auth_bp.post("/login")
def login():
    data = _payload()

    email = normalizar_email(data.get("email"))
    password = data.get("password") or ""

    if not email or not password:
        return erro("missing_credentials", "Informe e-mail e senha.", 400)

    user = User.query.filter_by(email=email).first()

    # Mensagem genérica: não revela se o e-mail existe.
    if user is None or not user.check_password(password):
        return erro("invalid_credentials", "E-mail ou senha inválidos.", 401)

    token = create_access_token(identity=str(user.id))

    return jsonify({"access_token": token, "user": user.to_dict()}), 200


# ---------------------------------------------------------------------------
# GET /api/v1/auth/me
# ---------------------------------------------------------------------------
@auth_bp.get("/me")
@jwt_required()
def me():
    user = db.session.get(User, int(get_jwt_identity()))
    if user is None:
        return erro("user_not_found", "Usuário não encontrado.", 404)

    return jsonify({"user": user.to_dict()}), 200