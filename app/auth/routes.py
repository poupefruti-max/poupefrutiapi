"""Blueprint de autenticação: /api/v1/auth."""

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import (
    create_access_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
)
from sqlalchemy.exc import IntegrityError

from ..extensions import db, limiter
from ..models import User
from services.tokens import revogar_token
from ..validators import email_valido, normalizar_email

auth_bp = Blueprint("auth", __name__, url_prefix="/api/v1/auth")


def _erro(code: str, message: str, status: int):
    """Resposta de erro no formato padrão da API."""
    return jsonify({"error": {"code": code, "message": message}}), status


def _payload() -> dict:
    """Lê o corpo JSON aceitando apenas objetos."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _chave_por_email() -> str:
    """Limite por e-mail, para travar ataque dirigido a uma conta.

    Complementa o limite por IP: uma miríade de IPs mirando a mesma conta não
    passa, porque o e-mail vira a chave. O e-mail vem normalizado do corpo,
    então `Alvo@Test.com ` e `alvo@test.com` caem no mesmo bucket.
    """
    dados = _payload()
    email = normalizar_email(dados.get("email"))
    return f"auth-email:{email}" if email else "auth-email:__vazio__"


def _chave_por_ip() -> str:
    """Limite por IP.

    Depende do ProxyFix estar configurado (PROXY_FIX_X_FOR) — atrás do proxy do
    Render, sem isso o IP é o do proxy e todos compartilham o mesmo bucket.
    """
    return f"auth-ip:{request.remote_addr}"


# ---------------------------------------------------------------------------
# POST /api/v1/auth/register
# ---------------------------------------------------------------------------
@auth_bp.post("/register")
def register():
    data = _payload()

    email_value = data.get("email")
    email = normalizar_email(email_value)
    password = data.get("password")
    name_value = data.get("name")
    name = name_value.strip() if isinstance(name_value, str) else ""
    min_len = current_app.config["MIN_PASSWORD_LENGTH"]

    if not email_valido(email) or len(email) > 255:
        return _erro("invalid_email", "Informe um e-mail válido.", 400)

    if not name or len(name) > 120:
        return _erro("invalid_name", "Informe seu nome (até 120 caracteres).", 400)

    if (
        not isinstance(password, str)
        or len(password) < min_len
        or len(password.encode("utf-8")) > 72
    ):
        return _erro(
            "weak_password",
            f"A senha deve ter ao menos {min_len} caracteres e no máximo 72 bytes.",
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
        return _erro("email_already_exists", "Este e-mail já está cadastrado.", 409)

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
@limiter.limit(
    lambda: current_app.config["LOGIN_RATE_LIMIT"],
    key_func=_chave_por_ip,
    # 429 precisa sair no mesmo formato de erro da API.
    # O Flask-Limiter passa o limite como argumento; o handler 429 registered
    # em create_app cobre os demais casos.
    on_breach=lambda _limite: _erro(
        "rate_limit_exceeded",
        "Muitas tentativas de login. Aguarde um pouco e tente de novo.",
        429,
    ),
)
@limiter.limit(
    lambda: current_app.config["LOGIN_EMAIL_RATE_LIMIT"],
    key_func=_chave_por_email,
)
def login():
    data = _payload()

    email = normalizar_email(data.get("email"))
    password = data.get("password")

    if not email or not isinstance(password, str) or not password:
        return _erro("missing_credentials", "Informe e-mail e senha.", 400)

    if len(password.encode("utf-8")) > 72:
        return _erro("invalid_credentials", "E-mail ou senha inválidos.", 401)

    user = User.query.filter_by(email=email).first()

    # Mensagem genérica: não revela se o e-mail existe.
    if user is None or not user.check_password(password):
        return _erro("invalid_credentials", "E-mail ou senha inválidos.", 401)

    token = create_access_token(identity=str(user.id))

    return jsonify({"access_token": token, "user": user.to_dict()}), 200


# ---------------------------------------------------------------------------
# POST /api/v1/auth/logout
# ---------------------------------------------------------------------------
@auth_bp.post("/logout")
@jwt_required()
def logout():
    try:
        revogar_token(get_jwt())
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Erro ao encerrar sessão de usuário")
        return _erro("logout_failed", "Não foi possível encerrar a sessão.", 500)
    return jsonify({"message": "Sessão encerrada."}), 200


# ---------------------------------------------------------------------------
# GET /api/v1/auth/me
# ---------------------------------------------------------------------------
@auth_bp.get("/me")
@jwt_required()
def me():
    claims = get_jwt()
    if claims.get("role") == "admin":
        return _erro("forbidden", "Use o endpoint de admin para administradores.", 403)

    user = db.session.get(User, int(get_jwt_identity()))
    if user is None:
        return _erro("user_not_found", "Usuário não encontrado.", 404)

    # Inclui os mercados do usuário, para o front montar a navegação.
    dados = user.to_dict_com_estabelecimentos()
    memberships = []
    for vinculo in user.memberships:
        est = vinculo.establishment
        memberships.append(
            {
                "establishment_id": vinculo.establishment_id,
                "name": est.name if est else None,
                "slug": est.slug if est else None,
                "role": vinculo.role.value if vinculo.role else None,
                "establishment_status": est.status.value if est and est.status else None,
            }
        )
    dados["memberships"] = memberships

    return jsonify({"user": dados}), 200
