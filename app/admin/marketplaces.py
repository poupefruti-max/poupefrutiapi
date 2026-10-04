"""Onboarding de mercados: /api/v1/admin (rotas de estabelecimento)."""

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity
from sqlalchemy import desc

from ..extensions import db
from ..models import (
    Establishment,
    Invitation,
    Lead,
    LeadStatus,
    LeadType,
)
from ..rbac import EstablishmentStatus, MemberRole, normalizar_role
from ..timeutils import agora_utc, como_utc
from ..validators import normalizar_texto
from .decorators import admin_required
from services import invitations as svc_convites

admin_market_bp = Blueprint("admin_market", __name__, url_prefix="/api/v1/admin")


def _erro(code: str, message: str, status: int):
    """Resposta de erro no formato padrão da API."""
    return jsonify({"error": {"code": code, "message": message}}), status


def _admin_id() -> int:
    return int(get_jwt_identity())


def _status_convite(convite: Invitation) -> str:
    """Traduz o estado do convite para um rótulo estável na API.

    `expires_at` é normalizado antes da comparação: o PostgreSQL devolve a
    data com fuso e o SQLite sem, e comparar as duas com `agora_utc()`
    levantaria TypeError.
    """
    if convite.accepted_at is not None:
        return "accepted"
    if convite.revoked_at is not None:
        return "revoked"
    if como_utc(convite.expires_at) is not None and como_utc(convite.expires_at) <= agora_utc():
        return "expired"
    return "pending"


def _convite_dict(convite: Invitation) -> dict:
    dados = convite.to_dict()
    dados["status"] = _status_convite(convite)
    return dados


# ---------------------------------------------------------------------------
# POST /api/v1/admin/leads/<id>/approve-market
# ---------------------------------------------------------------------------
@admin_market_bp.post("/leads/<int:lead_id>/approve-market")
@admin_required
def aprovar_mercado(lead_id: int):
    """Aprova um lead de mercado: cria o estabelecimento e o convite de owner.

    É o único momento em que o link em claro aparece.
    """
    lead = db.session.get(Lead, lead_id)
    if lead is None:
        return _erro("not_found", "Lead não encontrado.", 404)

    if lead.type != LeadType.establishment:
        return _erro(
            "invalid_lead_type",
            "Este fluxo é só para leads de estabelecimento.",
            409,
        )

    if lead.status == LeadStatus.converted or lead.establishment is not None:
        return _erro(
            "lead_already_converted",
            "Este lead já foi convertido. Gere um novo link pela tela de estabelecimentos.",
            409,
        )

    app_base = current_app.config["APP_BASE_URL"]

    try:
        slug = svc_convites.gerar_slug_unico(lead.name, sess=db.session)

        estabelecimento = Establishment(
            name=lead.name,
            lead_id=lead.id,
            slug=slug,
            status=EstablishmentStatus.pending,
            phone=lead.phone,
        )
        db.session.add(estabelecimento)
        db.session.flush()

        convite = svc_convites.criar_convite(
            establishment_id=estabelecimento.id,
            role=MemberRole.owner.value,
            criado_por_admin_id=_admin_id(),
            app_base_url=app_base,
            sess=db.session,
        )

        lead.status = LeadStatus.converted
        lead.converted_at = agora_utc()

        db.session.commit()
    except svc_convites.ErroConvite as erro:
        db.session.rollback()
        return _erro(erro.codigo, erro.mensagem, erro.status)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Erro ao aprovar mercado do lead %s", lead_id)
        return _erro("internal_error", "Erro interno no servidor.", 500)

    contato = (lead.extra or {}).get("responsavel") or ""
    texto = svc_convites.texto_sugerido_whatsapp(
        estabelecimento_nome=estabelecimento.name,
        link_convite=convite["invite_url"],
        nome_contato=contato,
    )

    return (
        jsonify(
            {
                "message": "Mercado aprovado e convite gerado.",
                "lead": lead.to_dict(),
                "establishment": estabelecimento.to_dict(),
                "invitation": {
                    "id": convite["invitation"].id,
                    "role": convite["role"],
                    "status": "pending",
                    "expires_at": convite["expires_at"].isoformat(),
                },
                # Único retorno com o link em claro.
                "invite_url": convite["invite_url"],
                "expires_at": convite["expires_at"].isoformat(),
                "whatsapp_message": texto,
                "aviso": "Guarde este link agora: ele não será exibido novamente.",
            }
        ),
        201,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/admin/establishments/<id>/invitations
# ---------------------------------------------------------------------------
@admin_market_bp.post("/establishments/<int:establishment_id>/invitations")
@admin_required
def gerar_novo_convite(establishment_id: int):
    """Gera um novo convite, revogando os pendentes do mesmo papel."""
    dados = request.get_json(silent=True)
    dados = dados if isinstance(dados, dict) else {}

    papel = normalizar_role(dados.get("role") or MemberRole.owner.value)
    if papel is None:
        return _erro(
            "invalid_role",
            "Papel inválido. Use owner, manager ou staff.",
            400,
        )

    try:
        convite = svc_convites.criar_convite(
            establishment_id=establishment_id,
            role=papel,
            criado_por_admin_id=_admin_id(),
            app_base_url=current_app.config["APP_BASE_URL"],
            sess=db.session,
        )
        db.session.commit()
    except svc_convites.ErroConvite as erro:
        db.session.rollback()
        return _erro(erro.codigo, erro.mensagem, erro.status)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Erro ao gerar convite para est. %s", establishment_id)
        return _erro("internal_error", "Erro interno no servidor.", 500)

    return (
        jsonify(
            {
                "message": "Novo convite gerado. Os pendentes anteriores foram revogados.",
                "establishment_id": establishment_id,
                "invitation": {
                    "id": convite["invitation"].id,
                    "role": convite["role"],
                    "status": "pending",
                    "expires_at": convite["expires_at"].isoformat(),
                },
                "invite_url": convite["invite_url"],
                "expires_at": convite["expires_at"].isoformat(),
                "aviso": "Guarde este link agora: ele não será exibido novamente.",
            }
        ),
        201,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/admin/establishments
# ---------------------------------------------------------------------------
@admin_market_bp.get("/establishments")
@admin_required
def listar_estabelecimentos():
    status = request.args.get("status")
    busca = (request.args.get("search") or "").strip()

    consulta = Establishment.query
    if status:
        try:
            consulta = consulta.filter(
                Establishment.status == EstablishmentStatus(status)
            )
        except ValueError:
            return _erro("invalid_status", f"Status inválido: {status}", 400)
    if busca:
        consulta = consulta.filter(Establishment.name.ilike(f"%{busca}%"))

    pagina = consulta.order_by(desc(Establishment.created_at)).all()

    itens = []
    for est in pagina:
        dados = est.to_dict()
        convites = sorted(est.invitations, key=lambda c: c.id, reverse=True)
        dados["invitations"] = [_convite_dict(c) for c in convites]
        dados["latest_invitation_status"] = (
            _status_convite(convites[0]) if convites else None
        )
        dados["members_count"] = len(est.members)
        itens.append(dados)

    return jsonify({"establishments": itens, "total": len(itens)}), 200


# ---------------------------------------------------------------------------
# GET /api/v1/admin/establishments/<id>
# ---------------------------------------------------------------------------
@admin_market_bp.get("/establishments/<int:establishment_id>")
@admin_required
def detalhar_estabelecimento(establishment_id: int):
    est = db.session.get(Establishment, establishment_id)
    if est is None:
        return _erro("not_found", "Estabelecimento não encontrado.", 404)

    dados = est.to_dict()

    dados["members"] = [
        {
            "user_id": m.user_id,
            "role": m.role.value if m.role else None,
            "name": m.user.name if m.user else None,
            "email": m.user.email if m.user else None,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in est.members
    ]

    convites = sorted(est.invitations, key=lambda c: c.id, reverse=True)
    dados["invitations"] = [_convite_dict(c) for c in convites]
    dados["latest_invitation_status"] = _status_convite(convites[0]) if convites else None

    return jsonify({"establishment": dados}), 200


# ---------------------------------------------------------------------------
# PATCH /api/v1/admin/establishments/<id>/status
# ---------------------------------------------------------------------------
@admin_market_bp.patch("/establishments/<int:establishment_id>/status")
@admin_required
def mudar_status(establishment_id: int):
    """Suspende ou reativa um mercado.

    O status vem no corpo JSON: `{"status": "suspended"}`.

    Valores aceitos:
      * `suspended` — some da rota pública e bloqueia as escritas do painel
        dele; a leitura continua.
      * `active`    — reativa. Mantém o `is_published` que já estava, para não
        despublicar a página por efeito colateral.
      * `pending`   — aceito por simetria com o enum, e útil para devolver um
        mercado recém-aprovado ao estado inicial sem precisar apagar dados.
        Não muda nem a publicação nem a rota pública.

    Suspender é decisão da administração e, ao contrário da publicação, não é
    revertido pelo owner do mercado.
    """
    est = db.session.get(Establishment, establishment_id)
    if est is None:
        return _erro("not_found", "Estabelecimento não encontrado.", 404)

    dados = request.get_json(silent=True)
    pedido = normalizar_texto((dados or {}).get("status") or "")

    if pedido not in ("active", "suspended", "pending"):
        return _erro(
            "invalid_status",
            "Status inválido. Use active, suspended ou pending.",
            400,
        )

    anterior = est.status
    est.status = EstablishmentStatus(pedido)

    db.session.commit()
    current_app.logger.info(
        "Estabelecimento %s: %s -> %s (por admin %s)",
        est.id,
        anterior.value if anterior else None,
        pedido,
        _admin_id(),
    )

    return (
        jsonify(
            {
                "message": f"Mercado {pedido}.",
                "establishment_id": est.id,
                "status": est.status.value,
                "previous_status": anterior.value if anterior else None,
                "is_published": est.is_published,
            }
        ),
        200,
    )
