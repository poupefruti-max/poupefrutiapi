"""Gestão da equipe: membros e convites de /api/v1/establishments/<id>.

Fica em módulo próprio porque é a parte com mais regras: ninguém concede papel
maior que o seu, o último owner não pode ser rebaixado nem removido, e manager
só mexe em staff.
"""

from flask import Blueprint, current_app, g, jsonify, request
from flask_jwt_extended import jwt_required

from ..extensions import db
from ..models import EstablishmentMember, Invitation, User
from ..rbac import (
    MemberRole,
    eh_convite_permitido,
    normalizar_role,
    pode_agir_sobre,
    pode_conceder_papel,
)
from ..timeutils import agora_utc, como_utc
from .auth import carregar_membership, establishment_role_required

team_bp = Blueprint("establishments_team", __name__, url_prefix="/api/v1/establishments")

ROTULOS_PAPEL = {
    "owner": "dono",
    "manager": "gerente",
    "staff": "equipe",
}


def _erro(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


def _payload() -> dict:
    dados = request.get_json(silent=True)
    return dados if isinstance(dados, dict) else {}


def _status_convite(convite: Invitation) -> str:
    """
    Estado do convite para a API. Normaliza `expires_at` antes de comparar:
    o PostgreSQL devolve a data com fuso e o SQLite sem; comparar as duas com
    `agora_utc()` levantaria TypeError.
    """
    if convite.accepted_at is not None:
        return "accepted"
    if convite.revoked_at is not None:
        return "revoked"
    if como_utc(convite.expires_at) is not None and como_utc(convite.expires_at) <= agora_utc():
        return "expired"
    return "pending"


def _convite_dict(convite: Invitation) -> dict:
    """Nunca inclui token_hash — nem para membros."""
    return {
        "id": convite.id,
        "role": convite.role.value if convite.role else None,
        "status": _status_convite(convite),
        "expires_at": convite.expires_at.isoformat() if convite.expires_at else None,
        "accepted_at": convite.accepted_at.isoformat() if convite.accepted_at else None,
        "revoked_at": convite.revoked_at.isoformat() if convite.revoked_at else None,
        "created_at": convite.created_at.isoformat() if convite.created_at else None,
    }


def _contar_owners(establishment_id: int) -> int:
    """Quantos owners o mercado tem, com as linhas travadas.

    Trava com `FOR UPDATE` para que duas requisições simultâneas não leiam
    "1 owner" ao mesmo tempo e ambas removam, deixando o mercado sem dono.
    O SQLite ignora o `FOR UPDATE` (não tem esse lock), então o teste local não
    exercita essa parte — a garantia real é no PostgreSQL, em produção.

    Também NÃO dá para travar um `count(*)`: o PostgreSQL recusa `FOR UPDATE`
    com função de agregação. Por isso buscamos as linhas e contamos em Python.
    """
    linhas = (
        db.session.query(EstablishmentMember.user_id)
        .filter(
            EstablishmentMember.establishment_id == establishment_id,
            EstablishmentMember.role == MemberRole.owner,
        )
        .with_for_update()
        .all()
    )
    return len(linhas)


# ---------------------------------------------------------------------------
# GET /api/v1/establishments/<id>/members
# ---------------------------------------------------------------------------
@team_bp.get("/<int:establishment_id>/members")
@jwt_required()
@establishment_role_required("ver_equipe")
def listar_membros(establishment_id: int):
    """Membros do mercado e convites pendentes (sem hash de token)."""
    est = g.establishment

    membros = (
        db.session.query(EstablishmentMember, User)
        .join(User, User.id == EstablishmentMember.user_id)
        .filter(EstablishmentMember.establishment_id == est.id)
        .order_by(EstablishmentMember.created_at)
        .all()
    )

    convites = (
        db.session.query(Invitation)
        .filter(Invitation.establishment_id == est.id)
        .order_by(Invitation.id.desc())
        .all()
    )

    return (
        jsonify(
            {
                "members": [
                    {
                        "user_id": vinculo.user_id,
                        "name": user.name,
                        "email": user.email,
                        "role": vinculo.role.value if vinculo.role else None,
                        "role_label": ROTULOS_PAPEL.get(
                            vinculo.role.value if vinculo.role else "", ""
                        ),
                        "is_me": vinculo.user_id == g.membership.user_id,
                        "created_at": vinculo.created_at.isoformat()
                        if vinculo.created_at
                        else None,
                    }
                    for vinculo, user in membros
                ],
                "invitations": [_convite_dict(c) for c in convites],
            }
        ),
        200,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/establishments/<id>/invitations
# ---------------------------------------------------------------------------
@team_bp.post("/<int:establishment_id>/invitations")
@jwt_required()
@establishment_role_required("convidar_staff")
def gerar_convite(establishment_id: int):
    """Gera convite para manager ou staff (owner só pela administração)."""
    from services import invitations as svc

    dados = _payload()
    pedido = normalizar_role(dados.get("role"))
    if pedido is None:
        return _erro(
            "invalid_role",
            "Informe o papel: manager ou staff.",
            400,
        )

    # A matriz decide: staff não convida ninguém; manager não convida manager;
    # owner não é concedível por membro nenhum.
    if not eh_convite_permitido(g.meu_papel, pedido):
        return _erro(
            "insufficient_permissions",
            f"Você não pode convidar alguém como {ROTULOS_PAPEL[pedido]}.",
            403,
        )

    try:
        resultado = svc.criar_convite(
            establishment_id=establishment_id,
            role=pedido,
            criado_por_user_id=g.membership.user_id,
            app_base_url=current_app.config["APP_BASE_URL"],
            sess=db.session,
        )
        db.session.commit()
    except svc.ErroConvite as erro:
        db.session.rollback()
        return _erro(erro.codigo, erro.mensagem, erro.status)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Erro ao gerar convite para est. %s", establishment_id)
        return _erro("internal_error", "Erro interno no servidor.", 500)

    convite = resultado["invitation"]
    return (
        jsonify(
            {
                "message": "Convite criado. Compartilhe o link com a pessoa convidada.",
                "invitation": {
                    "id": convite.id,
                    "role": resultado["role"],
                    "status": "pending",
                    "expires_at": resultado["expires_at"].isoformat(),
                },
                # Único momento em que o link em claro aparece.
                "invite_url": resultado["invite_url"],
                "expires_at": resultado["expires_at"].isoformat(),
                "aviso": "Guarde este link agora: ele não será exibido novamente.",
            }
        ),
        201,
    )


# ---------------------------------------------------------------------------
# DELETE /api/v1/establishments/<id>/invitations/<invitation_id>
# ---------------------------------------------------------------------------
@team_bp.delete("/<int:establishment_id>/invitations/<int:invitation_id>")
@jwt_required()
@establishment_role_required("convidar_staff")
def revogar_convite(establishment_id: int, invitation_id: int):
    """Revoga um convite pendente do próprio mercado."""
    convite = db.session.get(Invitation, invitation_id)
    if convite is None or convite.establishment_id != g.establishment.id:
        return _erro("not_found", "Convite não encontrado.", 404)

    if convite.accepted_at is not None:
        return _erro(
            "invitation_already_accepted",
            "Este convite já foi aceito.",
            409,
        )
    if convite.revoked_at is not None:
        return _erro(
            "invitation_already_revoked",
            "Este convite já está revogado.",
            409,
        )

    convite.revoked_at = agora_utc()
    db.session.commit()
    return jsonify({"invitation": _convite_dict(convite)}), 200


# ---------------------------------------------------------------------------
# PATCH /api/v1/establishments/<id>/members/<user_id>
# ---------------------------------------------------------------------------
@team_bp.patch("/<int:establishment_id>/members/<int:user_id>")
@jwt_required()
@establishment_role_required("alterar_papel")
def alterar_papel(establishment_id: int, user_id: int):
    """Muda o papel de um membro."""
    dados = _payload()
    novo = normalizar_role(dados.get("role"))
    if novo is None:
        return _erro("invalid_role", "Papel inválido.", 400)

    alvo = carregar_membership(user_id, g.establishment.id)
    if alvo is None:
        return _erro("not_found", "Membro não encontrado.", 404)

    papel_atual = alvo.role.value if alvo.role else None
    if papel_atual == novo:
        return jsonify({"message": "O membro já tem esse papel."}), 200

    # Manager só mexe em staff.
    if not pode_agir_sobre(g.meu_papel, papel_atual):
        return _erro(
            "insufficient_permissions",
            f"Você não pode alterar o papel de um {ROTULOS_PAPEL.get(papel_atual, papel_atual)}.",
            403,
        )

    # Só se concede o que a matriz permite. Derivado de PERMISSOES, então o
    # caminho de alteração de papel e o de convite usam a mesma regra: manager
    # NÃO promove ninguém a manager, porque `convidar_manager` é só do owner.
    if not pode_conceder_papel(g.meu_papel, novo):
        return _erro(
            "cannot_grant_higher_role",
            f"Você não pode atribuir o papel {ROTULOS_PAPEL.get(novo, novo)}.",
            403,
        )

    # O último owner não pode ser rebaixado.
    if papel_atual == "owner" and novo != "owner" and _contar_owners(g.establishment.id) <= 1:
        return _erro(
            "last_owner",
            "Não é possível rebaixar o único dono do estabelecimento.",
            409,
        )

    alvo.role = novo
    db.session.commit()
    return jsonify({"member": {"user_id": alvo.user_id, "role": alvo.role.value}}), 200


# ---------------------------------------------------------------------------
# DELETE /api/v1/establishments/<id>/members/<user_id>
# ---------------------------------------------------------------------------
@team_bp.delete("/<int:establishment_id>/members/<int:user_id>")
@jwt_required()
@establishment_role_required("remover_membro")
def remover_membro(establishment_id: int, user_id: int):
    """Remove o vínculo. O User continua existindo."""
    alvo = carregar_membership(user_id, g.establishment.id)
    if alvo is None:
        return _erro("not_found", "Membro não encontrado.", 404)

    papel_atual = alvo.role.value if alvo.role else None

    if not pode_agir_sobre(g.meu_papel, papel_atual):
        return _erro(
            "insufficient_permissions",
            f"Você não pode remover um {ROTULOS_PAPEL.get(papel_atual, papel_atual)}.",
            403,
        )

    if papel_atual == "owner" and _contar_owners(g.establishment.id) <= 1:
        return _erro(
            "last_owner",
            "Não é possível remover o único dono do estabelecimento.",
            409,
        )

    db.session.delete(alvo)
    db.session.commit()
    return jsonify({"message": "Membro removido.", "user_id": user_id}), 200
