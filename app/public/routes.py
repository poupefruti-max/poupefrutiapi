"""Rota pública do estabelecimento: /api/v1/public/establishments/<slug>.

Só responde para mercado publicado **e** ativo. Qualquer outro caso (não existe,
não publicado, suspenso) devolve o mesmo 404, para não revelar o que existe.

Não expõe CNPJ, lead, membros, ids internos nem nada de vínculo.
"""

from flask import Blueprint, jsonify

from ..extensions import db, limiter
from ..models import Establishment
from ..rbac import EstablishmentStatus

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

    return (
        jsonify(
            {
                "establishment": {
                    "name": est.name,
                    "description": est.description,
                    "address": est.address,
                    "phone": est.phone,
                    "opening_hours": est.opening_hours,
                }
            }
        ),
        200,
    )
