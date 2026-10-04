"""CORS nas respostas de erro da API.

Erro sem `Access-Control-Allow-Origin` chega no navegador como "falha de CORS"
— o front nunca enxerga a mensagem de verdade. Estes testes cobrem 404, 405, 429,
500 por exceção, 500 por erro de SQLAlchemy, e o caso em que o próprio handler
de 500 quebra ao tentar desfazer a sessão.
"""

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.orm.scoping import scoped_session

from app import create_app
from app.extensions import db

ORIGEM = "https://poupefruti.vercel.app"
CABECALHO = "Access-Control-Allow-Origin"


@pytest.fixture
def app():
    """
    App com rotas /api/* que quebram de propósito.

    TESTING fica False de propósito: com True, o Flask PROPAGA a exceção em vez
    de chamar o handler de erro, e o 500 nunca chegaria ao cliente.
    """
    app = create_app("testing")
    app.config.update(
        TESTING=False,
        PROPAGATE_EXCEPTIONS=False,
        DEBUG=False,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_ENGINE_OPTIONS={},
        CORS_ORIGINS=[ORIGEM],
        RATELIMIT_DEFAULT="",
        LOGIN_RATE_LIMIT="2 per minute",
        LOGIN_EMAIL_RATE_LIMIT="1000 per minute",
    )

    @app.route("/api/_test/quebra")
    def quebra():
        raise RuntimeError("falha proposital")

    @app.route("/api/_test/erro-de-banco")
    def erro_de_banco():
        # simula erro de SQLAlchemy: tabela inexistente
        db.session.execute(db.text("SELECT * FROM tabela_que_nao_existe"))
        return "chegou aqui", 200

    @app.route("/api/_test/so-get")
    def so_get():
        return "ok", 200

    with app.app_context():
        db.create_all()

    yield app

    # Sem db.session.remove() aqui: o Flask-SQLAlchemy já descarta a sessão no
    # teardown, e chamar de novo (com monkeypatch possivelmente ativo) estouraria.
    with app.app_context():
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def assert_cors(resp, nome, status_esperado):
    assert resp.status_code == status_esperado, (
        f"{nome}: status {resp.status_code}, esperado {status_esperado}"
    )
    assert resp.headers.get(CABECALHO) == ORIGEM, (
        f"{nome}: {CABECALHO} ausente na resposta de erro "
        f"(content_type={resp.content_type})"
    )


# ---------------------------------------------------------------------------
# Erros com CORS
# ---------------------------------------------------------------------------

class TestCorsEmErros:
    def test_404_tem_cors(self, client):
        r = client.get("/api/nao-existe", headers={"Origin": ORIGEM})
        assert_cors(r, "404", 404)
        assert r.get_json()["error"]["code"] == "not_found"

    def test_405_tem_cors(self, client):
        # PATCH numa rota que só aceita GET
        r = client.patch("/api/_test/so-get", headers={"Origin": ORIGEM})
        assert_cors(r, "405", 405)
        assert r.get_json()["error"]["code"] == "method_not_allowed"

    def test_429_tem_cors(self, client):
        respostas = [
            client.post(
                "/api/v1/auth/login",
                json={"email": "alvo@test.com", "password": "errada"},
                headers={"Origin": ORIGEM},
            )
            for _ in range(3)
        ]
        r = respostas[-1]
        assert r.status_code == 429
        assert r.headers.get(CABECALHO) == ORIGEM, (
            f"429 sem CORS: o Flask-Limiter devolve HTML cru "
            f"(content_type={r.content_type})"
        )
        assert r.get_json()["error"]["code"] == "rate_limit_exceeded"

    def test_500_por_excecao_tem_cors(self, client):
        r = client.get("/api/_test/quebra", headers={"Origin": ORIGEM})
        assert_cors(r, "500 por exceção", 500)
        assert r.get_json()["error"]["code"] == "internal_error"

    def test_500_por_erro_de_sqlalchemy_tem_cors(self, client):
        r = client.get("/api/_test/erro-de-banco", headers={"Origin": ORIGEM})
        assert_cors(r, "500 por SQLAlchemy", 500)
        assert r.get_json()["error"]["code"] == "internal_error"

    def test_erros_sempre_json(self, client):
        """Com CORS presente, o front consegue ler a mensagem de verdade."""
        for rota, metodo, status in [
            ("/api/nao-existe", "get", 404),
            ("/api/_test/so-get", "patch", 405),
            ("/api/_test/quebra", "get", 500),
        ]:
            resp = getattr(client, metodo)(rota, headers={"Origin": ORIGEM})
            assert_cors(resp, rota, status)
            assert resp.is_json, f"{rota}: resposta de erro nao e JSON"
            assert "error" in resp.get_json()


class TestOrigemNaoPermitida:
    def test_origem_desconhecida_nao_recebe_cors(self, client):
        """Nao inventamos Allowed-Origin para origem de fora da lista."""
        r = client.get("/api/_test/quebra", headers={"Origin": "https://site-malicioso.com"})
        assert r.status_code == 500
        assert r.headers.get(CABECALHO) is None

    def test_origem_desconhecida_nao_recebe_cors_em_erro_4xx(self, client):
        """Vale para 404/405 também, não só para 500."""
        for metodo, rota in [("get", "/api/nao-existe"), ("patch", "/api/_test/so-get")]:
            r = getattr(client, metodo)(
                rota, headers={"Origin": "https://site-malicioso.com"}
            )
            assert r.headers.get(CABECALHO) is None, (
                f"{rota}: origem nao permitida recebeu "
                f"{r.headers.get(CABECALHO)!r}"
            )

    def test_so_a_origem_configurada_passa(self, client):
        """
        Nota: o flask-cors 4.x pode enviar ACAO mesmo sem header Origin na
        requisição. Isso é inofensivo — o navegador só faz checagem de CORS
        quando envia Origin — mas o que NÃO pode acontecer é responder a uma
        origem que não está na lista.
        """
        for origem, esperado in [
            (ORIGEM, ORIGEM),
            ("https://outra.com", None),
        ]:
            r = client.get("/api/nao-existe", headers={"Origin": origem})
            assert r.headers.get(CABECALHO) == esperado, (
                f"origem={origem}: ACAO={r.headers.get(CABECALHO)!r}, "
                f"esperado={esperado!r}"
            )


# ---------------------------------------------------------------------------
# O handler de 500 não pode levantar
# ---------------------------------------------------------------------------

class TestHandler500NaoLevanta:
    def test_500_sobrevive_a_rollback_quebrado(self, app, client, monkeypatch):
        """
        Se `db.session.rollback()` levantar dentro do handler de 500, a
        exceção escapa do request inteiro e o gunicorn devolve um 500 cru —
        sem CORS. Era exatamente esse o risco.
        """
        def rollback_quebrado(self, *args, **kwargs):
            raise RuntimeError("sessao quebrada: nao foi possivel fazer rollback")

        monkeypatch.setattr(Session, "rollback", rollback_quebrado)

        r = client.get("/api/_test/quebra", headers={"Origin": ORIGEM})

        assert r.status_code == 500
        assert r.headers.get(CABECALHO) == ORIGEM, (
            "sem CORS: o handler levantou e a resposta veio crua do WSGI"
        )
        assert r.is_json
        assert r.get_json()["error"]["code"] == "internal_error"

    def test_500_sobrevive_a_rollback_e_remove_quebrados(self, app, client, monkeypatch):
        """Nem rollback nem remove podem derrubar o handler."""
        def quebra(self, *args, **kwargs):
            raise RuntimeError("sessao inutilizavel")

        # context() desfaz o patch assim que a requisição termina; sem isso o
        # teardown do app context (que chama remove) também estouraria.
        with monkeypatch.context() as m:
            m.setattr(Session, "rollback", quebra)
            m.setattr(scoped_session, "remove", quebra)

            r = client.get("/api/_test/quebra", headers={"Origin": ORIGEM})

        assert r.status_code == 500
        assert r.headers.get(CABECALHO) == ORIGEM
        assert r.get_json()["error"]["code"] == "internal_error"

    def test_sessao_saudavel_nao_derruba_a_resposta(self, app, client, monkeypatch):
        """
        Caminho feliz não regrediu: com a sessão saudável, o handler descarta
        a sessão e a requisição seguinte continua respondendo igual.

        O que importa aqui é a propriedade visível — duas requisições seguidas
        devolvem 500 com CORS e JSON — e não quantas vezes o Flask-SQLAlchemy
        chamou remove() internamente.
        """
        primeira = client.get("/api/_test/quebra", headers={"Origin": ORIGEM})
        segunda = client.get("/api/_test/quebra", headers={"Origin": ORIGEM})

        for r, nome in [(primeira, "1a"), (segunda, "2a")]:
            assert r.status_code == 500, nome
            assert r.headers.get(CABECALHO) == ORIGEM, nome
            assert r.is_json, nome
            assert r.get_json()["error"]["code"] == "internal_error", nome


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

class TestPreflight:
    def test_options_tem_cors(self, client):
        r = client.options(
            "/api/v1/invitations/validate",
            headers={
                "Origin": ORIGEM,
                "Access-Control-Request-Method": "POST",
            },
        )
        assert r.status_code == 200
        assert r.headers.get(CABECALHO) == ORIGEM
        metodos = r.headers.get("Access-Control-Allow-Methods", "")
        assert "POST" in metodos

    def test_options_de_rota_inexistente_tem_cors(self, client):
        """Preflight de rota que não existe também precisa de CORS."""
        r = client.options(
            "/api/nao-existe",
            headers={
                "Origin": ORIGEM,
                "Access-Control-Request-Method": "POST",
            },
        )
        assert r.headers.get(CABECALHO) == ORIGEM, (
            f"preflight sem CORS: status={r.status_code}"
        )
