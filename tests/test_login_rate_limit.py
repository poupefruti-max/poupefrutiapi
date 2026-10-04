"""Testes de rate limit no login de usuário e de ProxyFix."""

import pytest

from app import create_app
from app.extensions import db
from app.models import User


@pytest.fixture
def app():
    app = create_app("testing")
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_ENGINE_OPTIONS={},
        # IP folgado, e-mail apertado: assim dá para testar cada contador
        # isoladamente, sem um mascarar o outro.
        LOGIN_RATE_LIMIT="100 per minute",
        LOGIN_EMAIL_RATE_LIMIT="3 per minute",
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


def _criar_user(app, email="alvo@test.com", senha="senha1234"):
    with app.app_context():
        u = User(email=email, name="Alvo")
        u.set_password(senha)
        db.session.add(u)
        db.session.commit()


def _login(client, email, senha="errada123"):
    return client.post(
        "/api/v1/auth/login", json={"email": email, "password": senha}
    )


# ---------------------------------------------------------------------------
# Rate limit por IP
# ---------------------------------------------------------------------------

class TestRateLimitPorIP:
    def test_429_apos_estourar(self, app, client):
        _criar_user(app)
        respostas = [_login(client, "alvo@test.com") for _ in range(3)]

        # As três primeiras são 401 (senha errada), a quarta estoura.
        assert [r.status_code for r in respostas[:3]] == [401, 401, 401]

        r4 = _login(client, "alvo@test.com")
        assert r4.status_code == 429

    def test_429_vem_em_json_da_api(self, app, client):
        _criar_user(app)
        for _ in range(3):
            _login(client, "alvo@test.com")

        r = _login(client, "alvo@test.com")
        assert r.status_code == 429
        assert r.is_json
        corpo = r.get_json()
        assert corpo["error"]["code"] == "rate_limit_exceeded"
        assert "Muitas tentativas" in corpo["error"]["message"]

    def test_login_valido_antes_do_limite_passa(self, app, client):
        _criar_user(app, senha="senha1234")
        r = _login(client, "alvo@test.com", "senha1234")
        assert r.status_code == 200
        assert "access_token" in r.get_json()

    def test_limite_por_ip_nao_vaza_para_outro_e_mail(self, app, client):
        """O e-mail sozinho não esgota o bucket de IP de outra pessoa."""
        _criar_user(app, email="a@test.com", senha="senha1234")
        _criar_user(app, email="b@test.com", senha="senha1234")

        for _ in range(4):
            _login(client, "a@test.com", "errada")

        # a@test.com já está barrado no próprio contador de e-mail
        barrado = _login(client, "a@test.com", "senha1234")
        assert barrado.status_code == 429

        # b@test.com é outro contador: passa
        ok = _login(client, "b@test.com", "senha1234")
        assert ok.status_code == 200


# ---------------------------------------------------------------------------
# Rate limit por e-mail (ataque dirigido a uma conta)
# ---------------------------------------------------------------------------

class TestRateLimitPorEmail:
    def test_mesmo_e_mail_agota_o_limite_proprio(self, app, client):
        _criar_user(app, email="vitima@test.com")

        respostas = [_login(client, "vitima@test.com") for _ in range(3)]
        assert [r.status_code for r in respostas] == [401, 401, 401]

        barrado = _login(client, "vitima@test.com")
        assert barrado.status_code == 429

    def test_e_mail_normalizado_agrupa_variacoes(self, app, client):
        """Maiúsculas e espaços não escapam do limite."""
        _criar_user(app, email="vitima@test.com")

        variacoes = ["vitima@test.com", "  VITIMA@TEST.COM ", "Vitima@Test.Com"]
        for i in range(4):
            _login(client, variacoes[i % len(variacoes)])

        r = _login(client, "VITIMA@test.com")
        assert r.status_code == 429

    def test_corpo_invalido_nao_quebra(self, app, client):
        """JSON malformado não pode estourar o limite nem dar 500."""
        r = client.post(
            "/api/v1/auth/login", data="nao é json",
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_outro_e_mail_tem_contador_independente(self, app, client):
        _criar_user(app, email="a@test.com", senha="senha1234")
        _criar_user(app, email="b@test.com", senha="senha1234")

        for _ in range(4):
            _login(client, "a@test.com", "errada")

        assert _login(client, "a@test.com", "senha1234").status_code == 429
        assert _login(client, "b@test.com", "senha1234").status_code == 200


# ---------------------------------------------------------------------------
# ProxyFix
# ---------------------------------------------------------------------------

class TestProxyFix:
    def test_desligado_por_padrao(self):
        """Sem configuração, não confiamos em cabeçalho de cliente."""
        from app.config import Config

        assert Config.PROXY_FIX_X_FOR == 0

    def test_trava_impede_teste_de_usar_banco_real(self):
        """
        O teste que derrubou produção usava create_app('development').
        A trava precisa barrar isso ANTES de qualquer conexão.
        """
        import os

        os.environ["PYTEST_CURRENT_TEST"] = "teste::x (call)"
        try:
            with pytest.raises(RuntimeError, match="Trava de seguranca|Trava de segurança"):
                create_app("development")
        finally:
            os.environ.pop("PYTEST_CURRENT_TEST", None)

    def test_producao_recusa_mais_de_dois_saltos(self):
        """
        As classes de Config leem o ambiente na importação, então
        monkeypatch de os.environ não muda a classe. Ajustamos os atributos.
        """
        from app.config import ProductionConfig

        # ProductionConfig.__init__ valida os segredos antes; aqui só queremos
        # chegar na validação do PROXY_FIX_X_FOR.
        ProductionConfig.SECRET_KEY = "s" * 40
        ProductionConfig.JWT_SECRET_KEY = "j" * 40
        ProductionConfig.PROXY_FIX_X_FOR = 3
        try:
            with pytest.raises(RuntimeError, match="PROXY_FIX_X_FOR"):
                ProductionConfig()
        finally:
            ProductionConfig.PROXY_FIX_X_FOR = 1

    def test_producao_aceita_um_salto(self):
        from app.config import ProductionConfig

        ProductionConfig.SECRET_KEY = "s" * 40
        ProductionConfig.JWT_SECRET_KEY = "j" * 40
        ProductionConfig.PROXY_FIX_X_FOR = 1
        try:
            inst = ProductionConfig()   # não deve levantar
            assert inst.PROXY_FIX_X_FOR == 1
        finally:
            ProductionConfig.PROXY_FIX_X_FOR = 0

    def test_proxyfix_faz_ip_separar_por_x_forwarded_for(self):
        """
        Com ProxyFix ligado, três X-Forwarded-For distintos viram IPs
        distintos. Sem ele, os três cairiam no mesmo bucket e o 3º daria 429.
        """
        app = create_app("testing")
        app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_ENGINE_OPTIONS={},
            RATELIMIT_DEFAULT="",
            # e-mail sem limite: aqui queremos isolar o comportamento do IP
            LOGIN_RATE_LIMIT="2 per minute",
            LOGIN_EMAIL_RATE_LIMIT="1000 per minute",
        )
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

        ctx = app.app_context()
        ctx.push()
        db.create_all()
        try:
            with app.app_context():
                u = User(email="alvo@test.com", name="Alvo")
                u.set_password("senha1234")
                db.session.add(u)
                db.session.commit()

            c = app.test_client()

            def tentar(forwarded):
                return c.post(
                    "/api/v1/auth/login",
                    json={"email": "alvo@test.com", "password": "errada"},
                    headers={"X-Forwarded-For": forwarded},
                )

            assert tentar("1.1.1.1").status_code == 401
            assert tentar("2.2.2.2").status_code == 401
            r3 = tentar("3.3.3.3")

            assert r3.status_code == 401, (
                f"ProxyFix não separou os IPs: terceiro recebeu {r3.status_code}"
            )

            # Repetir o MESMO IP esgota o bucket dele.
            assert tentar("1.1.1.1").status_code == 401
            assert tentar("1.1.1.1").status_code == 429
        finally:
            db.session.remove()
            db.drop_all()
            ctx.pop()
