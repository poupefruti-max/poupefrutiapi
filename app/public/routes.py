"""Rota pública do estabelecimento: /api/v1/public/establishments/<slug>.

Só responde para mercado publicado **e** ativo. Qualquer outro caso (não existe,
não publicado, suspenso) devolve o mesmo 404, para não revelar o que existe.

Não expõe CNPJ, lead, membros, ids internos nem nada de vínculo. A única área
pública além do perfil é a lista de CESTAS ativas e ainda retiráveis.
"""

from flask import Blueprint, jsonify
from sqlalchemy import func

from ..extensions import db, limiter
from ..models import Basket, Establishment
from ..rbac import BasketStatus, EstablishmentStatus

public_bp = Blueprint("public", __name__, url_prefix="/api/v1/public")


def _erro(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


@public_bp.get("/establishments/<slug>")
@limiter.limit("60 per minute")
def ver_estabelecimento(slug: str):
    """Dados públicos do mercado."""
    est = (
        db.session.query(Establishment)
        .filter(
            Establishment.slug == slug,
            Establishment.is_published.is_(True),
            Establishment.status == EstablishmentStatus.active,
        )
        .first()
    )

    if est is None:
        # Genérico de propósito: não diz se o slug existe, se está suspenso
        # ou só ainda não foi publicado.
        return _erro("not_found", "Estabelecimento não encontrado.", 404)

    # Só as cestas que dá para retirar agora: ativas, com estoque e dentro da
    # janela. A comparação de tempo é feita pelo BANCO (func.now()) para não
    # misturar datetime com fuso (PostgreSQL devolve com fuso, SQLite sem) —
    # foi exatamente esse o bug que quebrou em produção.
    cestas = (
        db.session.query(Basket)
        .filter(
            Basket.establishment_id == est.id,
            Basket.status == BasketStatus.active,
            Basket.quantity > 0,
            Basket.pickup_ends_at > func.now(),
        )
        .order_by(Basket.pickup_starts_at.desc())
        .all()
    )

    return (
        jsonify(
            {
                "establishment": {
                    "name": est.name,
                    "address": est.address,
                    "phone": est.phone,
                    "opening_hours": est.opening_hours,
                    "baskets": [c.to_dict_publico() for c in cestas],
                }
            }
        ),
        200,
    )
