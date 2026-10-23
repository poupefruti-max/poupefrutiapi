"""Catálogo público de ofertas de cesta."""

from datetime import timedelta

import pytest

from app import create_app
from app.extensions import db
from app.models import Basket, Establishment
from app.rbac import BasketStatus, EstablishmentStatus
from app.timeutils import agora_utc


@pytest.fixture
def app():
    app = create_app("testing")
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_ENGINE_OPTIONS={},
        RATELIMIT_DEFAULT="",
    )
    ctx = app.app_context()
    ctx.push()
    db.create_all()
    yield app
    db.session.remove()
    db.drop_all()
    ctx.pop()


@pytest.fixture
def client(app):
    return app.test_client()


def criar_oferta(app, *, bairro="Centro", publicada=True, mercado_status="active",
                 cesta_status="active", quantidade=2, fim=None, slug="mercado"):
    with app.app_context():
        mercado = Establishment(
            name="Mercado de Teste",
            slug=slug,
            bairro=bairro,
            is_published=publicada,
            status=EstablishmentStatus(mercado_status),
            opening_hours="9h às 18h",
        )
        db.session.add(mercado)
        db.session.flush()
        cesta = Basket(
            establishment_id=mercado.id,
            type="frutas",
            name="Caixa surpresa",
            weight_grams=1500,
            price_cents=1800,
            retail_value_cents=3000,
            quantity=quantidade,
            pickup_starts_at=agora_utc() - timedelta(minutes=5),
            pickup_ends_at=fim or agora_utc() + timedelta(hours=3),
            status=BasketStatus(cesta_status),
        )
        db.session.add(cesta)
        db.session.commit()


def test_catalogo_retorna_somente_ofertas_publicas_disponiveis(app, client):
    criar_oferta(app)
    criar_oferta(app, slug="mercado-esgotado", quantidade=0)
    criar_oferta(app, slug="mercado-pausado", cesta_status="paused")
    criar_oferta(app, slug="mercado-nao-publicado", publicada=False)
    criar_oferta(
        app, slug="mercado-expirado", fim=agora_utc() - timedelta(minutes=1)
    )

    resposta = client.get("/api/v1/public/baskets")

    assert resposta.status_code == 200
    dados = resposta.get_json()
    assert dados["total"] == 1
    assert dados["baskets"][0]["name"] == "Caixa surpresa"
    assert dados["baskets"][0]["establishment"] == {
        "name": "Mercado de Teste",
        "slug": "mercado",
        "bairro": "Centro",
        "opening_hours": "9h às 18h",
    }
    assert "establishment_id" not in dados["baskets"][0]
    assert "created_by_user_id" not in dados["baskets"][0]


def test_catalogo_filtra_por_bairro_canonico(app, client):
    criar_oferta(app, slug="centro")
    criar_oferta(app, bairro="Bonfim", slug="bonfim")

    resposta = client.get("/api/v1/public/baskets?bairro=centro")

    assert resposta.status_code == 200
    assert resposta.get_json()["total"] == 1
    assert resposta.get_json()["baskets"][0]["establishment"]["bairro"] == "Centro"


def test_catalogo_rejeita_bairro_desconhecido_e_paginacao_invalida(client):
    assert client.get("/api/v1/public/baskets?bairro=Atlantis").status_code == 400
    assert client.get("/api/v1/public/baskets?page=abc").status_code == 400


def test_catalogo_aplica_paginacao(app, client):
    criar_oferta(app, slug="mercado-a")
    criar_oferta(app, slug="mercado-b")

    resposta = client.get("/api/v1/public/baskets?page=2&limit=1")

    assert resposta.status_code == 200
    assert resposta.get_json()["page"] == 2
    assert resposta.get_json()["pages"] == 2
    assert len(resposta.get_json()["baskets"]) == 1


def test_catalogo_filtra_por_texto_e_preco_maximo(app, client):
    criar_oferta(app, slug="frutas-baratas")
    texto = client.get("/api/v1/public/baskets?q=caixa")
    assert texto.status_code == 200
    assert texto.get_json()["total"] == 1
    with app.app_context():
        mercado = Establishment.query.filter_by(slug="frutas-baratas").one()
        cesta = Basket.query.filter_by(establishment_id=mercado.id).one()
        cesta.price_cents = 2600
        db.session.commit()

    resposta = client.get(
        "/api/v1/public/baskets?q=caixa&max_price_cents=2500"
    )

    assert resposta.status_code == 200
    assert resposta.get_json()["total"] == 0
