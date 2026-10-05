"""Cestas da sobra do dia: /api/v1/establishments/<id>/baskets.

Três permissões distintas, vindas da matriz em `app/rbac.py`:

* `ver_cestas`      — todos os papéis
* `gerenciar_cestas` — owner e manager (criar, editar, encerrar)
* `ajustar_estoque`  — todos os papéis (o staff mexe só no estoque)

Staff nunca cria, edita campo nem encerra: quem monta a oferta é gerência.
"""

from datetime import datetime, timedelta

from flask import Blueprint, g, jsonify, request
from flask_jwt_extended import get_jwt_identity
from sqlalchemy import func, update

from ..extensions import db
from ..models import Basket
from ..rbac import (
    BasketStatus,
    normalizar_basket_status,
    normalizar_basket_type,
)
from ..timeutils import agora_utc, como_utc
from .auth import establishment_role_required

baskets_bp = Blueprint("baskets", __name__, url_prefix="/api/v1/establishments")

# ---------------------------------------------------------------------------
# Limites de validação
# ---------------------------------------------------------------------------

LIMITES = {
    "price_cents": (100, 50000),        # R$ 1,00 a R$ 500,00
    "quantity": (0, 999),
    "weight_grams": (500, 30000),        # 0,5 kg a 30 kg
    "retail_value_cents": (100, 1000000),
    "name": 80,
    "notes": 280,
}

#: Janela máxima de retirada.
MAX_JANELA = timedelta(days=7)

#: Máximo de cestas ativas ou pausadas por mercado.
MAX_CESTAS_ABERTAS = 50

# Campos que o cliente pode enviar.
CAMPOS_TEXTUAIS = ("name", "notes")

_MARCAS = ("<", ">", "&", '"', "'")


def _erro(code: str, message: str, status: int):
    """Resposta de erro no formato padrão da API."""
    return jsonify({"error": {"code": code, "message": message}}), status


def _payload() -> dict:
    dados = request.get_json(silent=True)
    return dados if isinstance(dados, dict) else {}


def _texto(valor, campo: str):
    """Apara e limita. `None` quando vazio."""
    limpo = (valor or "").strip()
    if not limpo:
        return None
    return limpo[: LIMITES[campo]]


def _sem_html(valor):
    """Remove tags HTML e caracteres de entidade.

    O texto vai para a página pública, que pode ser renderizada como HTML por
    algum consumidor. Tiramos a marcação em vez de escapar, porque aqui o
    resultado é sempre texto puro.
    """
    import re

    texto = re.sub(r"<[^>]*>", "", str(valor))
    for marca in _MARCAS:
        texto = texto.replace(marca, "")
    return texto


def _inteiro(dados: dict, campo: str):
    """Lê um inteiro. Devolve (valor, erro_ou_None)."""
    if campo not in dados:
        return None, None

    bruto = dados.get(campo)
    if bruto is None:
        return None, None

    # bool é subclasse de int; "quantity": true não é 1.
    if isinstance(bruto, bool):
        return None, _erro(
            f"invalid_{campo}", f"{campo} deve ser um número inteiro.", 400
        )

    try:
        return int(bruto), None
    except (TypeError, ValueError):
        return None, _erro(
            f"invalid_{campo}", f"{campo} deve ser um número inteiro.", 400
        )


def _validar_inteiro(campo: str, valor: int):
    """Checa o intervalo. Devolve o erro ou None."""
    minimo, maximo = LIMITES[campo]
    if valor < minimo or valor > maximo:
        return _erro(
            f"invalid_{campo}",
            f"{campo} deve estar entre {minimo} e {maximo}.",
            400,
        )
    return None


def _parse_data(valor, campo: str):
    """Aceita ISO 8601. Normaliza para UTC aware."""
    if valor is None or valor == "":
        return None, _erro(f"invalid_{campo}", f"Informe {campo} em ISO 8601.", 400)
    if isinstance(valor, datetime):
        return como_utc(valor), None

    texto = str(valor).strip()
    # Python < 3.11 não entende o "Z" do RFC 3339.
    if texto.endswith("Z"):
        texto = texto[:-1] + "+00:00"
    try:
        return como_utc(datetime.fromisoformat(texto)), None
    except ValueError:
        return None, _erro(
            f"invalid_{campo}", f"{campo} inválido. Use ISO 8601.", 400
        )


def _contar_cestas_abertas(establishment_id: int) -> int:
    """Ativas + pausadas (closed não conta)."""
    return (
        db.session.query(func.count(Basket.id))
        .filter(
            Basket.establishment_id == establishment_id,
            Basket.status.in_([BasketStatus.active.value, BasketStatus.paused.value]),
        )
        .scalar()
        or 0
    )


# ---------------------------------------------------------------------------
# GET /api/v1/establishments/<id>/baskets
# ---------------------------------------------------------------------------
@baskets_bp.get("/<int:establishment_id>/baskets")
@establishment_role_required("ver_cestas")
def listar_cestas(establishment_id: int):
    """Lista as cestas do mercado, da retirada mais próxima para a mais distante."""
    consulta = Basket.query.filter(Basket.establishment_id == g.establishment.id)

    status = request.args.get("status")
    if status:
        canonico = normalizar_basket_status(status)
        if canonico is None:
            return _erro(
                "invalid_status",
                "Status inválido. Use active, paused ou closed.",
                400,
            )
        consulta = consulta.filter(Basket.status == canonico)

    cestas = consulta.order_by(Basket.pickup_starts_at.desc()).all()
    return jsonify({"baskets": [c.to_dict() for c in cestas]}), 200


# ---------------------------------------------------------------------------
# POST /api/v1/establishments/<id>/baskets
# ---------------------------------------------------------------------------
@baskets_bp.post("/<int:establishment_id>/baskets")
@establishment_role_required("gerenciar_cestas")
def criar_cesta(establishment_id: int):
    """Cria uma cesta."""
    dados = _payload()
    est_id = g.establishment.id

    # --- limite de cestas abertas ---
    status = normalizar_basket_status(dados.get("status") or "active")
    if status is None:
        return _erro(
            "invalid_status", "Status inválido. Use active, paused ou closed.", 400
        )
    if status != BasketStatus.closed.value:
        if _contar_cestas_abertas(est_id) >= MAX_CESTAS_ABERTAS:
            return _erro(
                "too_many_baskets",
                f"Este mercado já tem {MAX_CESTAS_ABERTAS} cestas ativas ou "
                "pausadas. Encerre alguma antes de criar outra.",
                409,
            )

    # --- type ---
    tipo = normalizar_basket_type(dados.get("type"))
    if tipo is None:
        return _erro(
            "invalid_type", "Informe type: frutas ou legumes.", 400
        )

    # --- textos ---
    nome = _texto(dados.get("name"), "name")
    if nome:
        nome = _sem_html(nome)
    notas = _texto(dados.get("notes"), "notes")
    if notas:
        notas = _sem_html(notas)

    # --- inteiros ---
    peso, erro = _inteiro(dados, "weight_grams")
    if erro:
        return erro
    if peso is None:
        return _erro("invalid_weight_grams", "Informe weight_grams.", 400)
    if erro := _validar_inteiro("weight_grams", peso):
        return erro

    preco, erro = _inteiro(dados, "price_cents")
    if erro:
        return erro
    if preco is None:
        return _erro("invalid_price_cents", "Informe price_cents.", 400)
    if erro := _validar_inteiro("price_cents", preco):
        return erro

    quantidade, erro = _inteiro(dados, "quantity")
    if erro:
        return erro
    if quantidade is None:
        quantidade = 0
    if erro := _validar_inteiro("quantity", quantidade):
        return erro

    varejo = None
    if dados.get("retail_value_cents") is not None:
        varejo, erro = _inteiro(dados, "retail_value_cents")
        if erro:
            return erro
        if erro := _validar_inteiro("retail_value_cents", varejo):
            return erro
        if varejo <= preco:
            return _erro(
                "invalid_retail_value_cents",
                "retail_value_cents deve ser maior que price_cents, "
                "senão não há desconto.",
                400,
            )

    # --- janela de retirada ---
    inicio, erro = _parse_data(dados.get("pickup_starts_at"), "pickup_starts_at")
    if erro:
        return erro
    fim, erro = _parse_data(dados.get("pickup_ends_at"), "pickup_ends_at")
    if erro:
        return erro

    if fim <= inicio:
        return _erro(
            "invalid_pickup_window",
            "pickup_ends_at precisa ser depois de pickup_starts_at.",
            400,
        )
    if (fim - inicio) > MAX_JANELA:
        return _erro(
            "pickup_window_too_long",
            "A janela de retirada não pode passar de 7 dias.",
            400,
        )
    if fim <= agora_utc():
        return _erro(
            "pickup_in_past",
            "pickup_ends_at precisa estar no futuro.",
            400,
        )

    cesta = Basket(
        establishment_id=est_id,
        type=tipo,
        name=nome,
        weight_grams=peso,
        price_cents=preco,
        retail_value_cents=varejo,
        quantity=quantidade,
        pickup_starts_at=inicio,
        pickup_ends_at=fim,
        notes=notas,
        status=status,
        created_by_user_id=int(get_jwt_identity()),
    )
    db.session.add(cesta)
    db.session.commit()
    return jsonify({"basket": cesta.to_dict()}), 201


# ---------------------------------------------------------------------------
# GET /api/v1/establishments/<id>/baskets/<basket_id>
# ---------------------------------------------------------------------------
@baskets_bp.get("/<int:establishment_id>/baskets/<int:basket_id>")
@establishment_role_required("ver_cestas")
def ver_cesta(establishment_id: int, basket_id: int):
    cesta = _buscar(g.establishment.id, basket_id)
    if cesta is None:
        return _erro("not_found", "Cesta não encontrada.", 404)
    return jsonify({"basket": cesta.to_dict()}), 200


def _buscar(establishment_id: int, basket_id: int):
    """Cesta do mercado. `None` se não existir (não vaza existência de outro)."""
    return Basket.query.filter_by(
        id=basket_id, establishment_id=establishment_id
    ).first()


# ---------------------------------------------------------------------------
# PATCH /api/v1/establishments/<id>/baskets/<basket_id>
# ---------------------------------------------------------------------------
@baskets_bp.patch("/<int:establishment_id>/baskets/<int:basket_id>")
@establishment_role_required("gerenciar_cestas")
def editar_cesta(establishment_id: int, basket_id: int):
    """Edita qualquer campo da cesta, inclusive o status (encerrar = closed)."""
    dados = _payload()
    cesta = _buscar(g.establishment.id, basket_id)
    if cesta is None:
        return _erro("not_found", "Cesta não encontrada.", 404)

    # --- status (inclui encerrar) ---
    if "status" in dados:
        status = normalizar_basket_status(dados.get("status"))
        if status is None:
            return _erro(
                "invalid_status", "Status inválido. Use active, paused ou closed.", 400
            )
        # Reabrir uma cesta encerrada reabriria o passado; não permitimos.
        if cesta.status == BasketStatus.closed and status != BasketStatus.closed:
            return _erro(
                "basket_closed",
                "Uma cesta encerrada não pode voltar a ativa.",
                409,
            )
        if status != BasketStatus.closed and cesta.status == BasketStatus.closed:
            return _erro(
                "basket_closed",
                "Uma cesta encerrada não pode voltar a ativa.",
                409,
            )
        cesta.status = status

    # --- type ---
    if "type" in dados:
        tipo = normalizar_basket_type(dados.get("type"))
        if tipo is None:
            return _erro("invalid_type", "Informe type: frutas ou legumes.", 400)
        cesta.type = tipo

    # --- textos ---
    for campo in CAMPOS_TEXTUAIS:
        if campo in dados:
            valor = _texto(dados.get(campo), campo)
            setattr(cesta, campo, _sem_html(valor) if valor else None)

    # --- inteiros ---
    if "weight_grams" in dados:
        peso, erro = _inteiro(dados, "weight_grams")
        if erro:
            return erro
        if peso is None:
            return _erro("invalid_weight_grams", "Informe weight_grams.", 400)
        if erro := _validar_inteiro("weight_grams", peso):
            return erro
        cesta.weight_grams = peso

    if "price_cents" in dados:
        preco, erro = _inteiro(dados, "price_cents")
        if erro:
            return erro
        if preco is None:
            return _erro("invalid_price_cents", "Informe price_cents.", 400)
        if erro := _validar_inteiro("price_cents", preco):
            return erro
        cesta.price_cents = preco

    if "quantity" in dados:
        quantidade, erro = _inteiro(dados, "quantity")
        if erro:
            return erro
        if quantidade is None:
            return _erro("invalid_quantity", "Informe quantity.", 400)
        if erro := _validar_inteiro("quantity", quantidade):
            return erro
        cesta.quantity = quantidade

    if "retail_value_cents" in dados:
        if dados.get("retail_value_cents") is None:
            cesta.retail_value_cents = None
        else:
            varejo, erro = _inteiro(dados, "retail_value_cents")
            if erro:
                return erro
            if erro := _validar_inteiro("retail_value_cents", varejo):
                return erro
            if varejo <= cesta.price_cents:
                return _erro(
                    "invalid_retail_value_cents",
                    "retail_value_cents deve ser maior que price_cents, "
                    "senão não há desconto.",
                    400,
                )
            cesta.retail_value_cents = varejo

    # --- janela ---
    # Normaliza os valores que já estavam na linha: o PostgreSQL devolve com
    # fuso e o SQLite sem, e comparar um deles com `agora_utc()` (sempre com
    # fuso) levanta "offset-naive vs offset-aware".
    inicio = como_utc(cesta.pickup_starts_at)
    fim = como_utc(cesta.pickup_ends_at)
    if "pickup_starts_at" in dados:
        inicio, erro = _parse_data(dados.get("pickup_starts_at"), "pickup_starts_at")
        if erro:
            return erro
    if "pickup_ends_at" in dados:
        fim, erro = _parse_data(dados.get("pickup_ends_at"), "pickup_ends_at")
        if erro:
            return erro

    if fim <= inicio:
        return _erro(
            "invalid_pickup_window",
            "pickup_ends_at precisa ser depois de pickup_starts_at.",
            400,
        )
    if (fim - inicio) > MAX_JANELA:
        return _erro(
            "pickup_window_too_long",
            "A janela de retirada não pode passar de 7 dias.",
            400,
        )
    if fim <= agora_utc():
        return _erro(
            "pickup_in_past",
            "pickup_ends_at precisa estar no futuro.",
            400,
        )
    cesta.pickup_starts_at = inicio
    cesta.pickup_ends_at = fim

    db.session.commit()
    return jsonify({"basket": cesta.to_dict()}), 200


# ---------------------------------------------------------------------------
# PATCH /api/v1/establishments/<id>/baskets/<basket_id>/quantity
# ---------------------------------------------------------------------------
@baskets_bp.patch("/<int:establishment_id>/baskets/<int:basket_id>/quantity")
@establishment_role_required("ajustar_estoque")
def ajustar_estoque(establishment_id: int, basket_id: int):
    """
    Define a quantidade ("quantity") ou mexe por delta ("delta": -2).

    O ajuste é um UPDATE atômico com `quantity + delta >= 0` no WHERE: dois
    balcões vendendo a última cesta ao mesmo tempo não conseguem zerar duas
    vezes. Ler-e-gravar deixaria passar.
    """
    dados = _payload()
    cesta = _buscar(g.establishment.id, basket_id)
    if cesta is None:
        return _erro("not_found", "Cesta não encontrada.", 404)

    tem_quantity = "quantity" in dados
    tem_delta = "delta" in dados

    if tem_quantity and tem_delta:
        return _erro(
            "invalid_quantity",
            "Informe quantity ou delta, não os dois.",
            400,
        )
    if not tem_quantity and not tem_delta:
        return _erro(
            "missing_quantity",
            "Informe quantity (valor final) ou delta (variação).",
            400,
        )

    if tem_quantity:
        alvo, erro = _inteiro(dados, "quantity")
        if erro:
            return erro
        if alvo is None:
            return _erro("invalid_quantity", "Informe quantity.", 400)
        if erro := _validar_inteiro("quantity", alvo):
            return erro
        cesta.quantity = alvo
        db.session.commit()
        return jsonify({"basket": cesta.to_dict()}), 200

    delta, erro = _inteiro(dados, "delta")
    if erro:
        return erro
    if delta is None or delta == 0:
        return _erro(
            "invalid_delta", "Informe delta com um valor diferente de zero.", 400
        )

    # Guarda atômica: só passa se o resultado ficar dentro de 0..999.
    resultado = db.session.execute(
        update(Basket)
        .where(
            Basket.id == cesta.id,
            Basket.establishment_id == g.establishment.id,
            Basket.quantity + delta >= 0,
            Basket.quantity + delta <= LIMITES["quantity"][1],
        )
        .values(quantity=Basket.quantity + delta)
    )

    if (resultado.rowcount or 0) != 1:
        db.session.rollback()
        db.session.refresh(cesta)
        nova = cesta.quantity + delta
        if nova < 0:
            return _erro(
                "insufficient_stock",
                f"Estoque insuficiente: há {cesta.quantity} e o ajuste pediria "
                f"{delta}.",
                409,
            )
        return _erro(
            "invalid_quantity",
            f"O ajuste deixaria o estoque em {nova}, fora do limite de "
            f"0 a {LIMITES['quantity'][1]}.",
            400,
        )

    db.session.commit()
    db.session.refresh(cesta)
    return jsonify({"basket": cesta.to_dict()}), 200
