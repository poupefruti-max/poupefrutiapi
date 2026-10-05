"""`GET /api/v1/public/stats`: a contagem de cestas hoje, por bairro e no total.

O que esta rota promete
-----------------------
Um número, e só o número. Nada de nome de mercado, id, endereço, telefone ou
slug: quem pergunta "quantas cestas têm no meu bairro?" não precisa saber de
quem são as cestas — e um mercado suspenso ou despublicado não pode aparecer
nesta contagem.

O que conta como "cesta hoje": mercado publicado **e** ativo, cesta ativa, com
estoque (`quantity > 0`) e com a retirada ainda não encerrada. Tudo comparado
no banco (`func.now()`), nunca em Python — foi comparar datetime com fuso do
PostgreSQL contra datetime sem fuso do SQLite que quebrou em produção.

Estes testes são OFFLINE: SQLite em memória, nenhuma conexão externa.
"""

from datetime import datetime, timedelta

import pytest

from app import create_app
from app.extensions import db
from app.models import Basket, Establishment
from app.public import cache as cache_mod
from app.public.cache import limpar_cache
from app.rbac import EstablishmentStatus
from app.timeutils import agora_utc
from app.validators import (
    BAIRRO_NAO_LISTADO,
    VALORES_BAIRRO,
    bairro_canonico,
    bairro_valido,
)

ROTA = "/api/v1/public/stats"


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def app():
    app = create_app("testing")
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_ENGINE_OPTIONS={},
        # Sem limite global: cada teste deste arquivo pode bater na rota sem
        # brigar com o contador de outro teste do mesmo processo.
        RATELIMIT_DEFAULT="",
    )
    ctx = app.app_context()
    ctx.push()
    db.create_all()
    yield app
    db.session.remove()
    db.drop_all()
    ctx.pop()


@pytest.fixture(autouse=True)
def cache_limpo(app):
    """
    O cache vive em `app.extensions`, então um app novo já nasce limpo — mas
    explicitamos para o teste não depender desse detalhe.
    """
    limpar_cache(app)


@pytest.fixture
def client(app):
    return app.test_client()


def entradas_cache(app):
    """O dicionário de entradas do cache, para contar quantas existem."""
    return app.extensions.get("poupefruti_cache", {})


def criar_mercado(app, bairro="Centro", publicado=True, status="active", nome="Mercado"):
    """Mercado direto no banco, com a situação pedida."""
    with app.app_context():
        est = Establishment(
            name=nome,
            bairro=bairro,
            is_published=publicado,
            status=EstablishmentStatus(status),
        )
        db.session.add(est)
        db.session.commit()
        return est.id


def criar_cesta(app, est_id, quantidade=3, status="active", tipo="frutas",
                fim=None, inicio=None):
    """
    Cesta direto no banco, para montar os casos de filtro.

    `fim`/`inicio` aceitam datetime com fuso (aware) ou sem (naive): os dois
    precisam dar o mesmo resultado, e o teste cobre os dois.
    """
    agora = agora_utc()
    with app.app_context():
        cesta = Basket(
            establishment_id=est_id,
            type=tipo,
            name="Cesta",
            weight_grams=1000,
            price_cents=1500,
            quantity=quantidade,
            pickup_starts_at=inicio if inicio is not None else agora + timedelta(hours=1),
            pickup_ends_at=fim if fim is not None else agora + timedelta(hours=5),
            status=status,
        )
        db.session.add(cesta)
        db.session.commit()
        return cesta.id


def stats(client, bairro=None):
    url = ROTA if bairro is None else f"{ROTA}?bairro={bairro}"
    resp = client.get(url)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


# ===========================================================================
# O que conta
# ===========================================================================


class TestContagem:
    def test_total_de_campinas(self, app, client):
        est = criar_mercado(app, bairro="Centro")
        criar_cesta(app, est, quantidade=3)
        criar_cesta(app, est, quantidade=2, tipo="legumes")

        dados = stats(client)
        assert dados["baskets"] == 2
        assert dados["markets"] == 1
        assert dados["units"] == 5
        assert dados["by_type"] == {"frutas": 1, "legumes": 1}
        assert dados["bairro"] is None

    def test_mercado_nao_publicado_nao_conta(self, app, client):
        est = criar_mercado(app, publicado=False)
        criar_cesta(app, est)

        dados = stats(client)
        assert dados["baskets"] == 0
        assert dados["markets"] == 0
        assert dados["units"] == 0

    def test_mercado_suspenso_nao_conta(self, app, client):
        est = criar_mercado(app, status="suspended")
        criar_cesta(app, est)

        assert stats(client)["baskets"] == 0

    def test_mercado_pendente_nao_conta(self, app, client):
        """Só `active` é público: pendente ainda não passou pelo onboarding."""
        est = criar_mercado(app, status="pending")
        criar_cesta(app, est)

        assert stats(client)["baskets"] == 0

    def test_cesta_pausada_nao_conta(self, app, client):
        est = criar_mercado(app)
        criar_cesta(app, est, status="paused")

        assert stats(client)["baskets"] == 0

    def test_cesta_encerrada_nao_conta(self, app, client):
        est = criar_mercado(app)
        criar_cesta(app, est, status="closed")

        assert stats(client)["baskets"] == 0

    def test_cesta_expirada_nao_conta(self, app, client):
        agora = agora_utc()
        est = criar_mercado(app)
        criar_cesta(app, est,
                    inicio=agora - timedelta(hours=3),
                    fim=agora - timedelta(hours=1))

        assert stats(client)["baskets"] == 0

    def test_cesta_sem_estoque_nao_conta(self, app, client):
        est = criar_mercado(app)
        criar_cesta(app, est, quantidade=0)

        dados = stats(client)
        assert dados["baskets"] == 0
        assert dados["units"] == 0

    def test_mix_de_casos(self, app, client):
        """Uma bagunça de cenário, e o número tem que sair exato."""
        agora = agora_utc()

        # conta: 2 cestas, 5 unidades, em 1 mercado
        ok = criar_mercado(app, bairro="Centro", nome="Conta")
        criar_cesta(app, ok, quantidade=3)
        criar_cesta(app, ok, quantidade=2, tipo="legumes")

        # não conta: despublicado
        despublicado = criar_mercado(app, bairro="Centro", publicado=False, nome="Despublicado")
        criar_cesta(app, despublicado)

        # não conta: suspenso
        suspenso = criar_mercado(app, bairro="Centro", status="suspended", nome="Suspenso")
        criar_cesta(app, suspenso)

        # não conta: cesta pausada, encerrada, sem estoque e expirada
        fora = criar_mercado(app, bairro="Centro", nome="Fora")
        criar_cesta(app, fora, status="paused")
        criar_cesta(app, fora, status="closed")
        criar_cesta(app, fora, quantidade=0)
        criar_cesta(app, fora, inicio=agora - timedelta(hours=3),
                    fim=agora - timedelta(hours=1))

        dados = stats(client)
        assert dados["markets"] == 1
        assert dados["baskets"] == 2
        assert dados["units"] == 5
        assert dados["by_type"] == {"frutas": 1, "legumes": 1}

    def test_unidades_soma_o_estoque(self, app, client):
        est = criar_mercado(app)
        criar_cesta(app, est, quantidade=4)
        criar_cesta(app, est, quantidade=6, tipo="legumes")

        dados = stats(client)
        assert dados["baskets"] == 2
        assert dados["units"] == 10

    def test_mais_de_um_mercado_no_total(self, app, client):
        a = criar_mercado(app, bairro="Centro", nome="A")
        b = criar_mercado(app, bairro="Bonfim", nome="B")
        criar_cesta(app, a)
        criar_cesta(app, b)

        dados = stats(client)
        assert dados["markets"] == 2
        assert dados["baskets"] == 2


# ===========================================================================
# Filtro por bairro
# ===========================================================================


class TestFiltroPorBairro:
    def _cenario(self, app):
        centro = criar_mercado(app, bairro="Centro", nome="Centro")
        criar_cesta(app, centro, quantidade=3)

        bonfim = criar_mercado(app, bairro="Bonfim", nome="Bonfim")
        criar_cesta(app, bonfim, quantidade=1)
        criar_cesta(app, bonfim, quantidade=1, tipo="legumes")

        # mercado sem bairro: não entra em nenhum filtro, mas SOMA na cidade
        sem = criar_mercado(app, bairro=None, nome="Sem Bairro")
        criar_cesta(app, sem, quantidade=7)
        return centro

    def test_filtra_por_bairro(self, app, client):
        self._cenario(app)

        dados = stats(client, "Centro")
        assert dados["bairro"] == "Centro"
        assert dados["baskets"] == 1
        assert dados["markets"] == 1
        assert dados["units"] == 3

    def test_outro_bairro(self, app, client):
        self._cenario(app)

        dados = stats(client, "Bonfim")
        assert dados["baskets"] == 2
        assert dados["markets"] == 1
        assert dados["units"] == 2
        assert dados["by_type"] == {"frutas": 1, "legumes": 1}

    def test_total_da_cidade_soma_todos(self, app, client):
        """O total inclui o mercado sem bairro; o filtro por bairro não."""
        self._cenario(app)

        dados = stats(client)
        assert dados["baskets"] == 4
        assert dados["markets"] == 3
        assert dados["units"] == 12

    def test_bairro_sem_cesta_responde_zero(self, app, client):
        self._cenario(app)

        dados = stats(client, "Sousas")
        assert dados["baskets"] == 0
        assert dados["units"] == 0
        assert dados["by_type"] == {"frutas": 0, "legumes": 0}
        assert dados["markets"] == 0
        assert dados["bairro"] == "Sousas"

    def test_grafia_diferente_cai_no_mesmo_bairro(self, app, client):
        """'centro' e 'Centro' são o mesmo bairro, e o filtro não é case-sensitive."""
        self._cenario(app)

        assert stats(client, "centro")["baskets"] == 1
        assert stats(client, "  CENTRO  ")["baskets"] == 1

    def test_resposta_devolve_o_bairro_canonico(self, app, client):
        self._cenario(app)

        assert stats(client, "centro")["bairro"] == "Centro"

    def test_bairro_invalido_400(self, client):
        resp = client.get(f"{ROTA}?bairro=Bairro%20que%20nao%20existe")
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_bairro"
        # Formato padrão de erro da API.
        assert set(resp.get_json()["error"]) == {"code", "message"}

    def test_bairro_vazio_e_igual_a_sem_filtro(self, app, client):
        self._cenario(app)

        assert stats(client, "")["baskets"] == stats(client)["baskets"]

    def test_porta_de_escape_e_aceita(self, app, client):
        """'Bairro não listado' é um valor válido, mesmo sem nenhuma utilidade."""
        est = criar_mercado(app, bairro=BAIRRO_NAO_LISTADO)
        criar_cesta(app, est)

        dados = stats(client, BAIRRO_NAO_LISTADO)
        assert dados["baskets"] == 1
        assert dados["bairro"] == BAIRRO_NAO_LISTADO


# ===========================================================================
# O que NÃO pode vazar
# ===========================================================================


class TestSemVazamento:
    def test_campos_do_payload(self, app, client):
        est = criar_mercado(app, bairro="Centro", nome="Mercado Santa Cruz")
        criar_cesta(app, est)

        dados = stats(client)
        assert set(dados) == {
            "markets", "baskets", "units", "by_type", "bairro", "updated_at"
        }, set(dados)
        assert set(dados["by_type"]) == {"frutas", "legumes"}

    def test_sem_nome_id_endereco_telefone(self, app, client):
        # Ids descartáveis primeiro: o mercado que importa precisa ter um id
        # que não coincida com nenhum número da contagem (markets=1, baskets=1),
        # senão o teste passaria/falaria por acaso.
        for i in range(6):
            criar_mercado(app, bairro="Centro", publicado=False, nome=f"Falso {i}")

        est = criar_mercado(app, bairro="Centro", nome="Mercado Santa Cruz")
        with app.app_context():
            db.session.add(Basket(
                establishment_id=est, type="frutas", weight_grams=1000,
                price_cents=1500, quantity=3,
                pickup_starts_at=agora_utc() + timedelta(hours=1),
                pickup_ends_at=agora_utc() + timedelta(hours=5),
                status="active",
            ))
            # Telefone/endereço reais, para a busca por texto ter o que achar.
            e = db.session.get(Establishment, est)
            e.phone = "19988887777"
            e.address = "Rua Das Flores, 123"
            db.session.commit()
            id_mercado = e.id

        dados = stats(client)
        texto = str(dados)

        assert "Mercado Santa Cruz" not in texto
        assert "19988887777" not in texto
        assert "Rua Das Flores" not in texto
        assert "establishment_id" not in texto
        assert "slug" not in texto

        # Nenhum valor do payload pode ser o id interno do mercado. Um
        # substring solto não serviria aqui: id 1 aparece dentro de qualquer
        # número maior.
        def achou_id(valor):
            if isinstance(valor, dict):
                return any(achou_id(v) for v in valor.values())
            if isinstance(valor, list):
                return any(achou_id(v) for v in valor)
            return valor == id_mercado

        assert not achou_id(dados), dados


# ===========================================================================
# Cache
# ===========================================================================


class TestCache:
    def test_segunda_chamada_usa_o_cache(self, app, client, monkeypatch):
        relogio = [1000.0]
        monkeypatch.setattr(cache_mod, "_relogio", lambda: relogio[0])

        est = criar_mercado(app)
        criar_cesta(app, est)

        primeira = stats(client)
        # Uma segunda cesta aparece depois da primeira resposta.
        criar_cesta(app, est, quantidade=1, tipo="legumes")

        segunda = stats(client)
        assert segunda == primeira, "o cache deveria ter servido a resposta velha"

    def test_cache_expira_em_60_segundos(self, app, client, monkeypatch):
        relogio = [1000.0]
        monkeypatch.setattr(cache_mod, "_relogio", lambda: relogio[0])

        est = criar_mercado(app)
        criar_cesta(app, est)

        antes = stats(client)
        criar_cesta(app, est, quantidade=1, tipo="legumes")

        relogio[0] += 59
        assert stats(client) == antes, "aos 59 s ainda é a mesma resposta"

        relogio[0] += 2
        depois = stats(client)
        assert depois["baskets"] == 2
        assert depois["by_type"] == {"frutas": 1, "legumes": 1}

    def test_cache_separa_por_bairro(self, app, client, monkeypatch):
        relogio = [1000.0]
        monkeypatch.setattr(cache_mod, "_relogio", lambda: relogio[0])

        centro = criar_mercado(app, bairro="Centro", nome="Centro")
        criar_cesta(app, centro, quantidade=3)
        bonfim = criar_mercado(app, bairro="Bonfim", nome="Bonfim")
        criar_cesta(app, bonfim, quantidade=1, tipo="legumes")

        assert stats(client, "Centro")["baskets"] == 1
        assert stats(client, "Bonfim")["baskets"] == 1
        assert stats(client)["baskets"] == 2

        # Trocar de bairro não pode devolver o número do bairro anterior.
        assert stats(client, "Centro")["units"] == 3
        assert stats(client, "Bonfim")["units"] == 1

    def test_grafia_diferente_usa_a_mesma_entrada(self, app, client, monkeypatch):
        relogio = [1000.0]
        monkeypatch.setattr(cache_mod, "_relogio", lambda: relogio[0])

        est = criar_mercado(app, bairro="Centro")
        criar_cesta(app, est)

        canônico = stats(client, "Centro")
        assert stats(client, "centro")["units"] == canônico["units"]

        # Uma entrada só: a chave do cache é o bairro canônico, então a segunda
        # grafia não cria uma segunda cópia do mesmo número.
        chaves = list(entradas_cache(app))
        assert chaves == [f"{ROTA}|bairro=Centro"]

    def test_erro_nao_fica_no_cache(self, app, client):
        resp = client.get(f"{ROTA}?bairro=Nada%20a%20ver")
        assert resp.status_code == 400

        est = criar_mercado(app, bairro="Centro")
        criar_cesta(app, est)

        # Se o 400 tivesse sido guardado, isto continuaria falhando.
        assert stats(client, "Centro")["baskets"] == 1


# ===========================================================================
# Datas com fuso e sem fuso
# ===========================================================================


class TestDatas:
    def test_aware_entrante_conta(self, app, client):
        est = criar_mercado(app)
        agora = agora_utc()
        criar_cesta(app, est,
                    inicio=agora + timedelta(hours=1),   # com fuso
                    fim=agora + timedelta(hours=4))

        assert stats(client)["baskets"] == 1

    def test_naive_entrante_nao_conta(self, app, client):
        """Sem fuso é o que o SQLite devolve; a comparação acontece no banco."""
        est = criar_mercado(app)
        agora = agora_utc()
        criar_cesta(app, est,
                    inicio=(agora - timedelta(hours=3)).replace(tzinfo=None),
                    fim=(agora - timedelta(hours=1)).replace(tzinfo=None))

        assert stats(client)["baskets"] == 0

    def test_naive_futuro_conta(self, app, client):
        est = criar_mercado(app)
        agora = agora_utc()
        criar_cesta(app, est,
                    inicio=(agora + timedelta(hours=1)).replace(tzinfo=None),
                    fim=(agora + timedelta(hours=4)).replace(tzinfo=None))

        assert stats(client)["baskets"] == 1

    def test_mistura_de_aware_e_naive(self, app, client):
        """Duas cestas gravadas de jeitos diferentes, o número tem que fechar."""
        agora = agora_utc()
        est = criar_mercado(app)
        criar_cesta(app, est, quantidade=2,
                    inicio=agora + timedelta(hours=1),
                    fim=agora + timedelta(hours=4))
        criar_cesta(app, est, quantidade=3, tipo="legumes",
                    inicio=(agora + timedelta(hours=1)).replace(tzinfo=None),
                    fim=(agora + timedelta(hours=4)).replace(tzinfo=None))

        dados = stats(client)
        assert dados["baskets"] == 2
        assert dados["units"] == 5

    def test_updated_at_e_aware(self, client):
        """O carimbo de tempo volta com fuso, como o resto da API."""
        dados = stats(client)
        momento = datetime.fromisoformat(dados["updated_at"])
        assert momento.tzinfo is not None, dados["updated_at"]


# ===========================================================================
# Rate limit
# ===========================================================================


class TestRateLimit:
    def test_estoura_e_devolve_json(self, app):
        """A rota tem limite próprio (30/min). Um 429 em HTML quebraria a UI."""
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)
        client = app.test_client()
        cabecalhos = {"X-Forwarded-For": "9.9.9.9"}

        respostas = [
            client.get(ROTA, headers=cabecalhos) for _ in range(31)
        ]

        assert any(r.status_code == 429 for r in respostas), [
            r.status_code for r in respostas
        ]
        ultima = respostas[-1]
        assert ultima.status_code == 429
        assert ultima.get_json()["error"]["code"] == "rate_limit_exceeded"


# ===========================================================================
# A lista de bairros é única
# ===========================================================================


class TestListaDeBairros:
    def test_porta_de_escape_e_a_ultima(self):
        assert VALORES_BAIRRO[-1] == BAIRRO_NAO_LISTADO
        assert VALORES_BAIRRO.count(BAIRRO_NAO_LISTADO) == 1

    def test_sem_valores_repetidos(self):
        assert len(set(VALORES_BAIRRO)) == len(VALORES_BAIRRO)

    def test_grafia_antiga_preservada(self):
        """'Jardim(ns) Guanabara' é feio, mas já está gravado: não se mexe."""
        assert "Jardim(ns) Guanabara" in VALORES_BAIRRO
        assert "Jardim(ns) Paulistano" in VALORES_BAIRRO
        assert "Jardim(ns) Chapada" in VALORES_BAIRRO

    def test_canonico_aceita_variacao_de_caixa_e_acento(self):
        assert bairro_canonico("barão geraldo") == "Barão Geraldo"
        assert bairro_canonico("BARAO GERALDO") == "Barão Geraldo"
        assert bairro_canonico("  centro  ") == "Centro"

    def test_canonico_recusa_o_que_nao_existe(self):
        assert bairro_canonico("Vila Nova") is None
        assert bairro_canonico("") is None
        assert bairro_canonico(None) is None

    def test_valido_aceita_porta_de_escape(self):
        assert bairro_valido(BAIRRO_NAO_LISTADO) is True
        assert bairro_valido("Centro") is True
        assert bairro_valido("Vila Nova") is False
        assert bairro_valido(None) is False