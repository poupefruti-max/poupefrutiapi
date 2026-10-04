"""Convites públicos: /api/v1/invitations.

Não exigem autenticação — o token em claro é a credencial. Todas as respostas de
recusa são idênticas, para não revelar se um token existiu, já foi usado ou
expirou.
"""

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import create_access_token
from sqlalchemy.exc import IntegrityError

from ..extensions import db, limiter
from ..validators import email_valido, normalizar_email
from services import invitations as svc_convites

invitations_bp = Blueprint("invitations", __name__, url_prefix="/api/v1/invitations")


def _erro(code: str, message: str, status: int):
    """Resposta de erro no formato padrão da API."""
    return jsonify({"error": {"code": code, "message": message}}), status


def _payload() -> dict:
    """Lê o corpo JSON aceitando apenas objetos."""
    dados = request.get_json(silent=True)
    return dados if isinstance(dados, dict) else {}


# ---------------------------------------------------------------------------
# POST /api/v1/invitations/validate
# ---------------------------------------------------------------------------
@invitations_bp.post("/validate")
@limiter.limit("30 per minute")
def validar():
    """Confere o token e devolve o nome do estabelecimento e o papel."""
    dados = _payload()

    try:
        info = svc_convites.validar_convite(dados.get("token"), sess=db.session)
    except svc_convites.ErroConvite as erro:
        return _erro(erro.codigo, erro.mensagem, erro.status)

    return (
        jsonify(
            {
                "valid": True,
                "establishment_name": info["establishment_name"],
                "role": info["role"],
                "expires_at": info["expires_at"],
            }
        ),
        200,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/invitations/accept
# ---------------------------------------------------------------------------
@invitations_bp.post("/accept")
@limiter.limit("10 per minute")
def aceitar():
    """Aceita o convite: cria a conta e o vínculo com o papel do convite."""
    dados = _payload()

    email = normalizar_email(dados.get("email"))
    senha = dados.get("password") or ""
    nome = (dados.get("name") or "").strip() or None
    min_len = current_app.config["MIN_PASSWORD_LENGTH"]

    if not email_valido(email):
        return _erro("invalid_email", "Informe um e-mail válido.", 400)

    if not isinstance(senha, str) or len(senha) < min_len:
        return _erro(
            "weak_password",
            f"A senha deve ter ao menos {min_len} caracteres.",
            400,
        )

    try:
        resultado = svc_convites.aceitar_convite(
            token=dados.get("token"),
            nome=nome or "",
            email=email,
            senha=senha,
            sess=db.session,
        )
        db.session.commit()
    except svc_convites.ErroConvite as erro:
        db.session.rollback()
        return _erro(erro.codigo, erro.mensagem, erro.status)
    except IntegrityError:
        # Corrida no e-mail: outra requisição criou a conta no mesmo instante.
        db.session.rollback()
        return _erro(
            "email_already_exists",
            "Este e-mail já está cadastrado. Entre com a conta existente ou use outro e-mail.",
            409,
        )
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Erro ao aceitar convite")
        return _erro("internal_error", "Erro interno no servidor.", 500)

    usuario = resultado["user"]

    # Mesmo formato do login de usuário, para o front tratar igual.
    token = create_access_token(identity=str(usuario.id))

    return (
        jsonify(
            {
                "message": "Conta criada com sucesso.",
                "access_token": token,
                "user": usuario.to_dict_com_estabelecimentos(),
                "establishment": resultado["establishment"].to_dict(),
                "role": resultado["role"],
            }
        ),
        201,
    )
