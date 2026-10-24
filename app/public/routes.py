"""Rotas públicas do PoupeFruti, sob /api/v1/public.

Duas áreas:

* `/establishments/<slug>` — o perfil de UM mercado, com as cestas ativas e
  ainda retiráveis.
* `/stats` — quantas cestas existem hoje em Campinas, no total e por bairro, sem
  geolocalização: a pessoa escolhe o bairro e a API conta.

As duas só respondem sobre mercado publicado **e** ativo. Qualquer outro caso
(não existe, não publicado, suspenso) fica de fora.

Nada de nome, id, endereço, telefone, CNPJ, lead ou membro sai daqui — em
`/stats` não sai nem o nome do mercado que tem a cesta. A pergunta "quantas
cestas no meu bairro?" se responde com contagens, e é só isso que a rota
devolve.
"""

from flask import Blueprint, jsonify, request
from sqlalchemy import case, func, or_

from ..extensions import db, limiter
from ..models import Basket, Establishment
from ..rbac import BasketStatus, BasketType, EstablishmentStatus
from ..timeutils import agora_utc
from ..validators import bairro_canonico
from .cache import cache_60s

public_bp = Blueprint("public", __name__, url_prefix="/api/v1/public")

#: Filtro que define "mercado visível ao público", escrito uma vez só. As duas
#: rotas abaixo usam exatamente este par — se uma delas ganhasse um critério a
#: mais, a contagem de /stats e a página do mercado contariam coisas diferentes.
MERCADO_PUBLICO = (
    Establishment.is_published.is_(True),
    Establishment.status == EstablishmentStatus.active,
)


def _erro(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


def _cestas_contaveis():
    """
    Filtro de "cesta que dá para retirar agora" — o mesmo da rota do perfil.

    São três condições da própria cesta; a quarta (mercado publicado e ativo)
    vem de MERCADO_PUBLICO, que as duas rotas aplicam na junção. A comparação de
    tempo é feita pelo BANCO (`func.now()`) e não pelo Python: o PostgreSQL
    devolve a data com fuso e o SQLite sem, e comparar um datetime com fuso
    vindo do banco contra um naive levanta TypeError (foi o bug que quebrou em
    produção).
    """
    return (
        Basket.status == BasketStatus.active,
        Basket.quantity > 0,
        Basket.pickup_ends_at > func.now(),
    )


@public_bp.get("/baskets")
@limiter.limit("60 per minute")
def listar_cestas():
    """Catálogo público de cestas disponíveis, opcionalmente por bairro.

    Os estabelecimentos ainda não armazenam coordenadas, portanto a resposta
    não fornece nem simula distância. A página pode filtrar por bairro enquanto
    a geolocalização de mercados não fizer parte do cadastro.
    """
    bairro_pedido = (request.args.get("bairro") or "").strip()
    bairro = None
    if bairro_pedido:
        bairro = bairro_canonico(bairro_pedido)
        if bairro is None:
            return _erro(
                "invalid_bairro",
                "Bairro inválido. Escolha um bairro cadastrado em Campinas.",
                400,
            )

    try:
        pagina = max(1, int(request.args.get("page", "1")))
        limite = min(60, max(1, int(request.args.get("limit", "24"))))
    except (TypeError, ValueError):
        return _erro("invalid_pagination", "Página ou limite inválido.", 400)

    consulta = (
        db.session.query(Basket, Establishment)
        .join(Establishment, Establishment.id == Basket.establishment_id)
        .filter(*MERCADO_PUBLICO, *_cestas_contaveis())
    )
    if bairro:
        consulta = consulta.filter(Establishment.bairro == bairro)

    texto = (request.args.get("q") or "").strip()[:80]
    if texto:
        termo = f"%{texto}%"
        consulta = consulta.filter(or_(
            Basket.name.ilike(termo),
            Establishment.name.ilike(termo),
            Establishment.bairro.ilike(termo),
        ))

    preco_maximo = request.args.get("max_price_cents")
    if preco_maximo not in (None, ""):
        try:
            preco_maximo = int(preco_maximo)
            if preco_maximo < 100:
                raise ValueError
        except (TypeError, ValueError):
            return _erro("invalid_price", "Informe um preço máximo válido em centavos.", 400)
        consulta = consulta.filter(Basket.price_cents <= preco_maximo)

    ordenacao = (request.args.get("sort") or "pickup").strip().lower()
    if ordenacao not in {"pickup", "price"}:
        return _erro("invalid_sort", "Ordenação inválida.", 400)

    total = consulta.count()
    ordenada = (
        consulta.order_by(Basket.price_cents.asc(), Basket.pickup_ends_at.asc())
        if ordenacao == "price"
        else consulta.order_by(Basket.pickup_ends_at.asc(), Basket.price_cents.asc())
    )
    linhas = (
        ordenada
        .offset((pagina - 1) * limite)
        .limit(limite)
        .all()
    )
    return jsonify({
        "baskets": [
            {
                **basket.to_dict_publico(),
                "establishment": {
                    "name": establishment.name,
                    "slug": establishment.slug,
                    "bairro": establishment.bairro,
                    "opening_hours": establishment.opening_hours,
                },
            }
            for basket, establishment in linhas
        ],
        "page": pagina,
        "limit": limite,
        "total": total,
        "pages": (total + limite - 1) // limite,
    }), 200


@public_bp.get("/establishments/<slug>")
@limiter.limit("60 per minute")
def ver_estabelecimento(slug: str):
    """Dados públicos do mercado."""
    est = (
        db.session.query(Establishment)
        .filter(Establishment.slug == slug, *MERCADO_PUBLICO)
        .first()
    )

    if est is None:
        # Genérico de propósito: não diz se o slug existe, se está suspenso
        # ou só ainda não foi publicado.
        return _erro("not_found", "Estabelecimento não encontrado.", 404)

    cestas = (
        db.session.query(Basket)
        .filter(Basket.establishment_id == est.id, *_cestas_contaveis())
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


# ---------------------------------------------------------------------------
# GET /api/v1/public/stats?bairro=<bairro>
# ---------------------------------------------------------------------------
@public_bp.get("/stats")
@limiter.limit("30 per minute")
@cache_60s()
def ver_stats():
    """
    Quantas cestas existem hoje, no total de Campinas ou em um bairro.

    Sem `bairro`, devolve o total da cidade. Com `bairro` (validado contra
    `app.validators.VALORES_BAIRRO`), devolve só o que existe naquele bairro.

    O cache é de 60 segundos por chave — mesma chave, mesma resposta. A
    pessoa pode trocar de bairro quantas vezes quiser: cada combinação tem sua
    própria entrada, e nada é misturado entre um bairro e outro.

    Formato de erro é o padrão da API ({"error": {"code", "message"}}).
    """
    pedido = (request.args.get("bairro") or "").strip()
    bairro = None
    if pedido:
        bairro = bairro_canonico(pedido)
        if bairro is None:
            # Lista fechada de propósito: devolver zero para um bairro
            # desconhecido faria a landing anunciar "0 cestas no seu bairro"
            # quando, na verdade, é o filtro que está errado.
            return _erro(
                "invalid_bairro",
                "Bairro inválido. Escolha um bairro da lista de Campinas.",
                400,
            )

    # Um mercado só conta se estiver publicado, ativo e — quando o filtro é por
    # bairro — tiver o bairro pedido. Sem isto, um mercado de outro bairro
    # inflaria o total da cidade.
    filtros = list(MERCADO_PUBLICO) + list(_cestas_contaveis())
    if bairro is not None:
        filtros.append(Establishment.bairro == bairro)

    # UMA consulta para os quatro números. A rota é lida a cada visita da
    # landing e o resultado fica 60 s no cache, mas ainda assim: quatro
    # queries para um número só é desperdício sem motivo.
    #
    # `baskets` conta as ofertas (linhas de cesta), `units` soma `quantity`:
    # quantas cestas a pessoa conseguiria retirar hoje somando todos os
    # mercados. É o número que responde "tem coisa perto de mim?".
    linha = (
        db.session.query(
            func.count(Basket.id),
            func.coalesce(func.sum(Basket.quantity), 0),
            func.count(func.distinct(Basket.establishment_id)),
            func.coalesce(
                func.sum(case((Basket.type == BasketType.frutas, 1), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((Basket.type == BasketType.legumes, 1), else_=0)), 0
            ),
        )
        .join(Establishment, Establishment.id == Basket.establishment_id)
        .filter(*filtros)
        .one()
    )
    cestas, unidades, mercados, frutas, legumes = linha

    return (
        jsonify(
            {
                "markets": int(mercados),
                "baskets": int(cestas),
                "units": int(unidades),
                # As duas chaves existem sempre, mesmo com zero: a UI não
                # deveria ter que adivinhar o que existe no payload.
                "by_type": {"frutas": int(frutas), "legumes": int(legumes)},
                "bairro": bairro,
                "updated_at": agora_utc().isoformat(),
            }
        ),
        200,
    )
