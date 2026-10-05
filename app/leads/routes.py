"""
Blueprint de cadastros da lista de espera: /api/leads.

Consome exatamente o payload produzido por enviarCadastro() na landing page
(ui/landing.html). As duas rotas existem separadas porque a UI monta a URL
assim: API_URL + "/api/leads/cliente" e API_URL + "/api/leads/mercado".
"""

from flask import Blueprint, jsonify, request, current_app
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Lead, LeadType
from ..validators import (
    VALORES_CESTA,
    VALORES_TIPO_ESTABELECIMENTO,
    bairro_canonico,
    email_valido,
    normalizar_email,
    normalizar_texto,
    whatsapp_valido,
)
from ..admin.decorators import admin_required
from services import create_lead, DuplicateLeadError

leads_bp = Blueprint("leads", __name__, url_prefix="/api/leads")

ORIGEM_PADRAO = "landing-poupefruti"


def erro(code: str, message: str, status: int):
    """Resposta de erro no formato padrão da API."""
    return jsonify({"error": {"code": code, "message": message}}), status


def _payload():
    """Lê o corpo JSON. Devolve (dados, erro) — erro quando não há objeto JSON."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, erro(
            "invalid_payload", "Envie um corpo JSON no formato de objeto.", 400
        )
    return data, None


def _salvar_lead(lead: Lead):
    """
    Commita a transação e devolve a resposta de sucesso.
    Trata IntegrityError (race condition no email único) com upsert.
    """
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # Race condition: dois POSTs simultâneos com mesmo email
        if lead.email:
            existing = db.session.query(Lead).filter(
                Lead.email == lead.email,
                Lead.type == lead.type
            ).first()
            if existing:
                current_app.logger.info(
                    "Race condition commit: lead email=%s type=%s id=%s",
                    lead.email, lead.type.value, existing.id
                )
                return jsonify({
                    "message": "Cadastro recebido com sucesso!",
                    "lead": existing.to_dict(),
                }), 201
        current_app.logger.exception("IntegrityError sem lead existente")
        return erro("internal_error", "Erro interno no servidor.", 500)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Erro ao salvar lead")
        return erro("internal_error", "Erro interno no servidor.", 500)

    return jsonify({
        "message": "Cadastro recebido com sucesso!",
        "lead": lead.to_dict(),
    }), 201


# ---------------------------------------------------------------------------
# Campos comuns (cliente e mercado)
# ---------------------------------------------------------------------------
def _validar_comuns(data: dict, exigir_nome: bool = True):
    """
    Valida o bloco comum do payload.

    Retorna (dados_normalizados, erro). Se houver erro, os dados são None.
    O `tipo` esperado é conferido contra o enviado pela UI para evitar que um
    cadastro de cliente chegue na rota de mercado.

    `exigir_nome` é False para o mercado: a UI não manda "nome" nesse
    formulário (o nome do negócio vem em "estabelecimento"), e sim a pessoa
    responsável em "responsavel".
    """
    nome = normalizar_texto(data.get("nome"))
    whatsapp = normalizar_texto(data.get("whatsapp"))
    bairro = normalizar_texto(data.get("bairro"))
    email = normalizar_email(data.get("email")) or None
    origem = normalizar_texto(data.get("origem")) or ORIGEM_PADRAO
    enviado_em = normalizar_texto(data.get("timestamp")) or None

    if exigir_nome and not nome:
        return None, erro("invalid_name", "Informe o nome.", 400)

    if not whatsapp:
        return None, erro("invalid_whatsapp", "Informe o WhatsApp.", 400)
    if not whatsapp_valido(whatsapp):
        return None, erro(
            "invalid_whatsapp",
            "Telefone inválido. Use DDD + número (ex.: (19) 99999-0000).",
            400,
        )

    # bairro pode ser vazio (vira NULL no banco)
    #
    # Aqui NÃO recusamos bairro fora da lista de app/validators.py, e é
    # deliberado: a lista é nova, o banco já tem valores fora dela (a landing
    # tinha duas listas divergentes até agora) e este cadastro é a única porta
    # de entrada de bairro que existe. Recusar faria a pessoa escolher um bairro
    # errado só para passar pela validação.
    #
    # O que fazemos é o contrário: quando o bairro É um valor da lista, grava o
    # texto canônico. "centro" vira "Centro", e a contagem por bairro da rota
    # pública não divide o mesmo bairro em duas grafias. Fora da lista, o texto
    # vai como veio — é dado histórico, e ninguém o renomeia.
    bairro = normalizar_texto(data.get("bairro")) or ""
    bairro = bairro_canonico(bairro) or bairro

    # Mercado não pede e-mail na UI (vem vazio). Só validamos quando preenchido.
    if email and not email_valido(email):
        return None, erro("invalid_email", "Informe um e-mail válido.", 400)

    tipo_enviado = normalizar_texto(data.get("tipo"))
    return {
        "nome": nome,
        "whatsapp": whatsapp,
        "bairro": bairro,
        "email": email,
        "origem": origem,
        "enviado_em": enviado_em,
        "tipo_enviado": tipo_enviado,
    }, None


# ---------------------------------------------------------------------------
# POST /api/leads/cliente
# ---------------------------------------------------------------------------
@leads_bp.post("/cliente")
def criar_cliente():
    data, falha = _payload()
    if falha:
        return falha

    dados, falha = _validar_comuns(data)
    if falha:
        return falha

    if dados["tipo_enviado"] and dados["tipo_enviado"] != "cliente":
        return erro("tipo_mismatch", "Tipo de cadastro inválido para esta rota.", 400)

    cesta = normalizar_texto(data.get("cesta"))
    if cesta and cesta not in VALORES_CESTA:
        return erro(
            "invalid_cesta",
            "Informe a cesta de interesse (frutas, legumes ou ambas).",
            400,
        )

    extra = {
        "enviado_em": dados["enviado_em"],
    }

    try:
        lead = create_lead(
            type=LeadType.person,
            name=dados["nome"],
            phone=dados["whatsapp"],
            source=dados["origem"],
            email=dados["email"],
            extra=extra,
            bairro=dados["bairro"],
            cesta_tipo=cesta,
        )
    except DuplicateLeadError:
        return erro("duplicate_lead", "Cadastro duplicado.", 409)

    return _salvar_lead(lead)


# ---------------------------------------------------------------------------
# POST /api/leads/mercado
# ---------------------------------------------------------------------------
@leads_bp.post("/mercado")
def criar_mercado():
    data, falha = _payload()
    if falha:
        return falha

    dados, falha = _validar_comuns(data, exigir_nome=False)
    if falha:
        return falha

    if dados["tipo_enviado"] and dados["tipo_enviado"] != "mercado":
        return erro("tipo_mismatch", "Tipo de cadastro inválido para esta rota.", 400)

    responsavel = normalizar_texto(data.get("responsavel"))
    estabelecimento = normalizar_texto(data.get("estabelecimento"))
    tipo_estabelecimento = normalizar_texto(data.get("tipoEstabelecimento"))

    if not responsavel:
        return erro("invalid_responsible", "Informe o nome do responsável.", 400)

    if not estabelecimento:
        return erro("invalid_establishment", "Informe o nome do estabelecimento.", 400)

    if tipo_estabelecimento not in VALORES_TIPO_ESTABELECIMENTO:
        return erro(
            "invalid_establishment_type",
            "Informe o tipo de comércio (mercado, padaria, hortifruti ou sacolao).",
            400,
        )

    extra = {
        "responsavel": responsavel,
        "estabelecimento": estabelecimento,
        "tipo_estabelecimento": tipo_estabelecimento,
        "enviado_em": dados["enviado_em"],
    }

    try:
        lead = create_lead(
            type=LeadType.establishment,
            name=estabelecimento,
            phone=dados["whatsapp"],
            source=dados["origem"],
            email=dados["email"],
            extra=extra,
            bairro=dados["bairro"],
        )
    except DuplicateLeadError:
        return erro("duplicate_lead", "Cadastro duplicado.", 409)

    return _salvar_lead(lead)


# ---------------------------------------------------------------------------
# POST /api/leads/<id>/convert
# ---------------------------------------------------------------------------
@leads_bp.post("/<int:lead_id>/convert")
@admin_required
def converter_lead(lead_id: int):
    """Converte um lead em usuário/estabelecimento. Requer autenticação de admin."""
    from services import convert_lead, LeadAlreadyConvertedError, LeadError

    data = request.get_json(silent=True) or {}
    password = data.get("password")
    establishment_data = data.get("establishment")

    try:
        result = convert_lead(lead_id, password=password, establishment_data=establishment_data)
    except LeadAlreadyConvertedError as e:
        return jsonify({
            "message": "Lead já convertido anteriormente.",
            "lead": e.args[0] if e.args else {},
            "user_id": e.existing_user_id,
            "establishment_id": e.existing_establishment_id,
        }), 200
    except LeadError as e:
        return erro("conversion_error", str(e), 400)
    except IntegrityError as e:
        db.session.rollback()
        return erro("conversion_error", f"Erro de integridade: {e}", 409)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Erro ao converter lead")
        return erro("internal_error", "Erro interno no servidor.", 500)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Erro ao commitar conversão")
        return erro("internal_error", "Erro interno no servidor.", 500)

    response = {"message": "Lead convertido com sucesso!", "lead": result["lead"].to_dict()}
    if "user" in result:
        response["user"] = result["user"].to_dict()
    if "establishment" in result:
        response["establishment"] = result["establishment"].to_dict()

    return jsonify(response), 201
