"""Gestão do estabelecimento: /api/v1/establishments/<id>.

Só membros do mercado acessam. A permissão vem da matriz em `app/rbac.py`,
consultada no banco a cada requisição.
"""

from flask import Blueprint, current_app, g, jsonify, request
from flask_jwt_extended import jwt_required
from ..extensions import db
from ..models import Establishment
from ..rbac import (
    EstablishmentStatus,
    cnpj_valido,
    formatar_cnpj,
    normalizar_cnpj,
)
from ..validators import VALORES_BAIRRO, bairro_canonico
from .auth import establishment_role_required

establishments_bp = Blueprint("establishments", __name__, url_prefix="/api/v1/establishments")

# Limites de tamanho dos campos de texto livre.
LIMITES = {
    "name": 160,
    "cnpj": 18,
    "address": 300,
    "phone": 20,
    "opening_hours": 300,
    # Mesmo tamanho da coluna (VARCHAR(80)). A lista de app/validators.py é bem
    # mais curta, mas o limite existe para o caso de o banco receber algo
    # fora dela por baixo dos panos.
    "bairro": 80,
}

#: Campos obrigatórios para publicar.
#: `bairro` entra porque é o que dá sentido à pergunta "quantas cestas têm no
#: meu bairro?" — um mercado sem bairro não contaria em nenhum filtro.
CAMPOS_PUBLISH = ("name", "address", "phone", "bairro")

#: Tradução campo -> rótulo nas mensagens de erro (o que a pessoa lê).
ROTULOS_PUBLISH = {
    "name": "nome",
    "address": "endereço",
    "phone": "telefone",
    "bairro": "bairro",
}


def _erro(code: str, message: str, status: int):
    """Resposta de erro no formato padrão da API."""
    return jsonify({"error": {"code": code, "message": message}}), status


def _payload() -> dict:
    dados = request.get_json(silent=True)
    return dados if isinstance(dados, dict) else {}


def _texto(valor, campo: str) -> str:
    """Normaliza e corta no limite do campo."""
    limpo = (valor or "").strip()
    return limpo[: LIMITES[campo]]


def _perfil_publico(est: Establishment) -> dict:
    """Dados do perfil para quem é membro (inclui CNPJ, que é do dono)."""
    return {
        "id": est.id,
        "name": est.name,
        "cnpj": formatar_cnpj(est.cnpj) if est.cnpj else None,
        "address": est.address,
        "phone": est.phone,
        "bairro": est.bairro,
        "opening_hours": est.opening_hours,
        "slug": est.slug,
        "status": est.status.value if est.status else None,
        "is_published": est.is_published,
        "created_at": est.created_at.isoformat() if est.created_at else None,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/establishments/<id>/profile
# ---------------------------------------------------------------------------
@establishments_bp.get("/<int:establishment_id>/profile")
@jwt_required()
@establishment_role_required("ver_perfil")
def ver_perfil(establishment_id: int):
    dados = _perfil_publico(g.establishment)
    dados["meu_papel"] = g.meu_papel
    # A lista de bairros vai junto para o painel montar o <select> a partir
    # dela. Sem isto, o painel manteria uma cópia da lista no HTML — que é
    # exatamente o que divergiu entre os dois formulários da landing.
    return jsonify({"establishment": dados, "bairros": list(VALORES_BAIRRO)}), 200


# ---------------------------------------------------------------------------
# PATCH /api/v1/establishments/<id>/profile
# ---------------------------------------------------------------------------
@establishments_bp.patch("/<int:establishment_id>/profile")
@jwt_required()
@establishment_role_required("editar_perfil")
def editar_perfil(establishment_id: int):
    """Atualiza os dados do próprio estabelecimento."""
    dados = _payload()
    est = g.establishment

    # --- nome ---
    if "name" in dados:
        nome = _texto(dados.get("name"), "name")
        if not nome:
            return _erro("invalid_name", "Informe o nome do estabelecimento.", 400)
        est.name = nome

    # --- cnpj ---
    if "cnpj" in dados:
        bruto = (dados.get("cnpj") or "").strip()
        if not bruto:
            est.cnpj = None                      # permite limpar
        else:
            digitos = normalizar_cnpj(bruto)
            if not cnpj_valido(digitos):
                return _erro(
                    "invalid_cnpj",
                    "CNPJ inválido. Confira os 14 dígitos.",
                    400,
                )
            outro = Establishment.query.filter(
                Establishment.cnpj == digitos,
                Establishment.id != est.id,
            ).first()
            if outro is not None:
                return _erro(
                    "cnpj_already_used",
                    "Este CNPJ já está cadastrado em outro estabelecimento.",
                    409,
                )
            est.cnpj = digitos

    # --- campos de texto ---
    # `description` NÃO entra mais aqui: virou a área de CESTAS (/baskets).
    for campo in ("address", "phone", "opening_hours"):
        if campo in dados:
            setattr(est, campo, _texto(dados.get(campo), campo) or None)

    # --- bairro ---
    # Lista fechada, de propósito: é o que permite contar cestas por bairro sem
    # geolocalização e sem variantar "Centro" de três formas.
    if "bairro" in dados:
        bruto = _texto(dados.get("bairro"), "bairro")
        if not bruto:
            est.bairro = None                 # permite limpar
        else:
            canonico = bairro_canonico(bruto)
            if canonico is None:
                return _erro(
                    "invalid_bairro",
                    "Bairro inválido. Escolha um bairro da lista de Campinas.",
                    400,
                )
            est.bairro = canonico

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Erro ao atualizar perfil do est. %s", establishment_id)
        return _erro("internal_error", "Erro interno no servidor.", 500)

    return jsonify({"establishment": _perfil_publico(est)}), 200


# ---------------------------------------------------------------------------
# POST /api/v1/establishments/<id>/publish
# ---------------------------------------------------------------------------
@establishments_bp.post("/<int:establishment_id>/publish")
@jwt_required()
@establishment_role_required("publicar")
def publicar(establishment_id: int):
    """Publica a página do mercado.

    Exige nome, endereço, telefone e bairro preenchidos. Publicar um mercado
    ainda `pending` o promove para `active` — é o momento em que ele fica
    visível na rota pública.
    """
    est = g.establishment

    faltando = [c for c in CAMPOS_PUBLISH if not (getattr(est, c) or "").strip()]
    if faltando:
        return _erro(
            "incomplete_profile",
            "Complete antes de publicar: "
            + ", ".join(ROTULOS_PUBLISH[c] for c in faltando)
            + ".",
            400,
        )

    if est.status is not None and est.status.value == "suspended":
        return _erro(
            "establishment_suspended",
            "Este estabelecimento está suspenso.",
            403,
        )

    est.is_published = True
    if est.status == EstablishmentStatus.pending:
        # Publicar completa o onboarding: vira active.
        est.status = EstablishmentStatus.active

    db.session.commit()
    return jsonify({"establishment": _perfil_publico(est)}), 200


# ---------------------------------------------------------------------------
# POST /api/v1/establishments/<id>/unpublish
# ---------------------------------------------------------------------------
@establishments_bp.post("/<int:establishment_id>/unpublish")
@jwt_required()
@establishment_role_required("publicar")
def despublicar(establishment_id: int):
    """Tira o mercado da página pública, sem alterar o status."""
    g.establishment.is_published = False
    db.session.commit()
    return jsonify({"establishment": _perfil_publico(g.establishment)}), 200
