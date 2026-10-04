"""Testes do onboarding de mercados: aprovação, convite e aceite."""

import hashlib

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    AdminUser,
    Establishment,
    EstablishmentMember,
    Invitation,
    Lead,
    LeadStatus,
    LeadType,
    User,
)
from services import invitations as svc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    app = create_app("testing")
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_ENGINE_OPTIONS={},
        APP_BASE_URL="https://app.test",
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


@pytest.fixture
def admin(app):
    with app.app_context():
        a = AdminUser(email="admin@test.com", name="Admin")
        a.set_password("senha123")
        db.session.add(a)
        db.session.commit()
        return a.id


@pytest.fixture
def admin_headers(client, admin):
    r = client.post(
        "/api/v1/admin/auth/login",
        json={"email": "admin@test.com", "password": "senha123"},
    )
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


@pytest.fixture
def user_headers(client, app):
    with app.app_context():
        u = User(email="comum@test.com", name="Comum")
        u.set_password("senha123")
        db.session.add(u)
        db.session.commit()
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "comum@test.com", "password": "senha123"},
    )
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


def criar_lead_mercado(app, nome="Mercado São José", telefone="(19) 98888-0000"):
    with app.app_context():
        lead = Lead(
            type=LeadType.establishment,
            name=nome,
            phone=telefone,
            email="responsavel@test.com",
            source="landing",
            status=LeadStatus.qualified,
            bairro="Centro",
            extra={"responsavel": "Maria Souza"},
        )
        db.session.add(lead)
        db.session.commit()
        return lead.id


def aprovar(app, client, headers, nome="Mercado São José"):
    lead_id = criar_lead_mercado(app, nome)
    resp = client.post(
        f"/api/v1/admin/leads/{lead_id}/approve-market", headers=headers
    )
    return resp


# ---------------------------------------------------------------------------
# Slug
# ---------------------------------------------------------------------------

class TestSlug:
    def test_slugify_removes_acentos_e_pontua(self):
        assert svc.slugify("Mercado São José") == "mercado-sao-jose"
        assert svc.slugify("Padaria  do  Zé!") == "padaria-do-ze"
        assert svc.slugify("Hortifrúti & Cia") == "hortifruti-cia"

    def test_slugify_trata_nome_vazio(self):
        assert svc.slugify("") == ""
        assert svc.slugify("!!!") == ""

    def test_slug_unico_resolve_colisao(self, app):
        """Cada slug precisa ser único no banco, e não só calculado diferente."""
        with app.app_context():
            db.session.add(Establishment(name="Mercado Central", slug="mercado-central"))
            db.session.commit()

            # Como na aprovação real, cada slug gerado é persistido antes do
            # próximo cálculo — senão os três devolveriam o mesmo candidato.
            slugs = []
            for _ in range(3):
                slug = svc.gerar_slug_unico("Mercado Central", sess=db.session)
                db.session.add(Establishment(name="Outro", slug=slug))
                db.session.commit()
                slugs.append(slug)

        assert slugs == ["mercado-central-2", "mercado-central-3", "mercado-central-4"]

    def test_slug_ignora_o_proprio_estabelecimento(self, app):
        """Regerar o slug do mesmo mercado não deve colidir consigo mesmo."""
        with app.app_context():
            est = Establishment(name="Mercado Central", slug="mercado-central")
            db.session.add(est)
            db.session.commit()
            est_id = est.id

            mesmo = svc.gerar_slug_unico("Mercado Central", sess=db.session, ignorar_id=est_id)
            db.session.commit()

            outro = svc.gerar_slug_unico("Mercado Central", sess=db.session)

        assert mesmo == "mercado-central"
        assert outro == "mercado-central-2"


# ---------------------------------------------------------------------------
# Aprovação
# ---------------------------------------------------------------------------

class TestAprovarMercado:
    def test_cria_estabelecimento_convite_e_marca_lead(self, app, client, admin_headers):
        resp = aprovar(app, client, admin_headers)

        assert resp.status_code == 201
        dados = resp.get_json()

        est = dados["establishment"]
        assert est["name"] == "Mercado São José"
        assert est["slug"] == "mercado-sao-jose"
        assert est["status"] == "pending"
        assert est["cnpj"] is None  # dono informa depois
        assert est["is_published"] is False

        assert dados["invitation"]["role"] == "owner"
        assert dados["invitation"]["status"] == "pending"
        assert dados["expires_at"]
        assert "#token=" in dados["invite_url"]
        assert dados["invite_url"].startswith("https://app.test/convite")
        assert "whatsapp_message" in dados
        assert dados["lead"]["status"] == "converted"
        assert dados["lead"]["converted_at"]

    def test_token_em_claro_nao_e_persistido(self, app, client, admin_headers):
        resp = aprovar(app, client, admin_headers)
        token = resp.get_json()["invite_url"].split("#token=")[1]

        with app.app_context():
            convites = db.session.query(Invitation).all()
            assert len(convites) == 1
            convite = convites[0]

            assert convite.token_hash != token
            assert len(convite.token_hash) == 64
            assert convite.token_hash == hashlib.sha256(token.encode()).hexdigest()
            # O token não aparece em nenhum campo persistido.
            persisted = " ".join(
                f"{c.token_hash}{c.role}{c.establishment_id}" for c in convites
            )
            assert token not in persisted

    def test_slug_ganha_sufixo_em_colisao(self, app, client, admin_headers):
        aprovar(app, client, admin_headers, nome="Mercado Central")
        resp2 = aprovar(app, client, admin_headers, nome="Mercado Central")

        assert resp2.status_code == 201
        assert resp2.get_json()["establishment"]["slug"] == "mercado-central-2"

    def test_recusa_lead_ja_convertido(self, app, client, admin_headers):
        from datetime import datetime

        lead_id = criar_lead_mercado(app, "Outro Mercado")

        # Marca como convertido para simular um lead já aprovado.
        with app.app_context():
            lead = db.session.get(Lead, lead_id)
            lead.status = LeadStatus.converted
            lead.converted_at = datetime.utcnow()
            db.session.commit()

        resp = client.post(
            f"/api/v1/admin/leads/{lead_id}/approve-market", headers=admin_headers
        )
        assert resp.status_code == 409
        assert resp.get_json()["error"]["code"] == "lead_already_converted"

    def test_recusa_lead_de_pessoa(self, app, client, admin_headers):
        with app.app_context():
            lead = Lead(
                type=LeadType.person,
                name="João",
                phone="19999990000",
                status=LeadStatus.qualified,
            )
            db.session.add(lead)
            db.session.commit()
            lead_id = lead.id

        resp = client.post(
            f"/api/v1/admin/leads/{lead_id}/approve-market", headers=admin_headers
        )
        assert resp.status_code == 409
        assert resp.get_json()["error"]["code"] == "invalid_lead_type"

    def test_lead_inexistente(self, app, client, admin_headers):
        resp = client.post("/api/v1/admin/leads/99999/approve-market", headers=admin_headers)
        assert resp.status_code == 404

    def test_convite_registra_admin_como_criador(self, app, client, admin_headers, admin):
        aprovar(app, client, admin_headers)

        with app.app_context():
            convite = db.session.query(Invitation).one()
            assert convite.created_by_admin_id == admin
            assert convite.created_by_user_id is None


# ---------------------------------------------------------------------------
# Convites
# ---------------------------------------------------------------------------

class TestConvites:
    def test_novo_convite_revoga_o_anterior(self, app, client, admin_headers):
        primeira = aprovar(app, client, admin_headers)
        est_id = primeira.get_json()["establishment"]["id"]

        resp = client.post(
            f"/api/v1/admin/establishments/{est_id}/invitations", headers=admin_headers
        )
        assert resp.status_code == 201

        with app.app_context():
            convites = (
                db.session.query(Invitation)
                .order_by(Invitation.id)
                .all()
            )
            assert len(convites) == 2
            antigo, novo = convites
            assert antigo.revoked_at is not None
            assert antigo.accepted_at is None
            assert novo.revoked_at is None

    def test_convite_com_papel_invalido(self, app, client, admin_headers):
        est_id = aprovar(app, client, admin_headers).get_json()["establishment"]["id"]

        resp = client.post(
            f"/api/v1/admin/establishments/{est_id}/invitations",
            json={"role": "superusuario"},
            headers=admin_headers,
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_role"

    def test_convite_pra_mercado_inexistente(self, app, client, admin_headers):
        resp = client.post(
            "/api/v1/admin/establishments/99999/invitations", headers=admin_headers
        )
        assert resp.status_code == 404

    def test_listagem_traz_status_do_convite(self, app, client, admin_headers):
        aprovar(app, client, admin_headers)

        resp = client.get("/api/v1/admin/establishments", headers=admin_headers)
        assert resp.status_code == 200
        itens = resp.get_json()["establishments"]
        assert len(itens) == 1
        assert itens[0]["latest_invitation_status"] == "pending"
        assert len(itens[0]["invitations"]) == 1

    def test_detalhe_traz_membros_e_convites(self, app, client, admin_headers):
        est_id = aprovar(app, client, admin_headers).get_json()["establishment"]["id"]

        resp = client.get(f"/api/v1/admin/establishments/{est_id}", headers=admin_headers)
        assert resp.status_code == 200
        dados = resp.get_json()["establishment"]
        assert dados["members"] == []
        assert len(dados["invitations"]) == 1
        assert dados["invitations"][0]["status"] == "pending"

    def test_convite_aceito_aparece_como_accepted(self, app, client, admin_headers):
        resp = aprovar(app, client, admin_headers)
        token = resp.get_json()["invite_url"].split("#token=")[1]
        est_id = resp.get_json()["establishment"]["id"]

        client.post(
            "/api/v1/invitations/accept",
            json={
                "token": token,
                "name": "Maria",
                "email": "maria@test.com",
                "password": "senha1234",
            },
        )

        detalhe = client.get(f"/api/v1/admin/establishments/{est_id}", headers=admin_headers)
        dados = detalhe.get_json()["establishment"]
        assert dados["latest_invitation_status"] == "accepted"
        assert len(dados["members"]) == 1
        assert dados["members"][0]["role"] == "owner"
        assert dados["members"][0]["email"] == "maria@test.com"


# ---------------------------------------------------------------------------
# Aceite
# ---------------------------------------------------------------------------

class TestAceite:
    def _aprovar_e_token(self, app, client, headers):
        resp = aprovar(app, client, headers)
        dados = resp.get_json()
        return dados["invite_url"].split("#token=")[1], dados["establishment"]["id"]

    def test_validate_devolve_nome_e_papel(self, app, client, admin_headers):
        token, _ = self._aprovar_e_token(app, client, admin_headers)

        resp = client.post("/api/v1/invitations/validate", json={"token": token})
        assert resp.status_code == 200
        dados = resp.get_json()
        assert dados["valid"] is True
        assert dados["establishment_name"] == "Mercado São José"
        assert dados["role"] == "owner"

    def test_aceite_cria_usuario_owner(self, app, client, admin_headers):
        token, est_id = self._aprovar_e_token(app, client, admin_headers)

        resp = client.post(
            "/api/v1/invitations/accept",
            json={
                "token": token,
                "name": "Maria Souza",
                "email": "maria@test.com",
                "password": "senha1234",
            },
        )
        assert resp.status_code == 201
        dados = resp.get_json()

        assert dados["access_token"]
        assert dados["user"]["email"] == "maria@test.com"
        assert dados["user"]["name"] == "Maria Souza"
        assert dados["role"] == "owner"
        assert dados["user"]["establishments"] == [
            {"establishment_id": est_id, "role": "owner"}
        ]

        with app.app_context():
            user = db.session.query(User).filter_by(email="maria@test.com").one()
            assert user.check_password("senha1234")

            membro = db.session.query(EstablishmentMember).one()
            assert membro.user_id == user.id
            assert membro.establishment_id == est_id
            assert membro.role.value == "owner"

            convite = db.session.query(Invitation).one()
            assert convite.accepted_at is not None
            assert convite.accepted_user_id == user.id

    def test_token_aceito_aciona_o_login_de_usuario(self, app, client, admin_headers):
        token, _ = self._aprovar_e_token(app, client, admin_headers)
        client.post(
            "/api/v1/invitations/accept",
            json={
                "token": token,
                "name": "Maria",
                "email": "maria@test.com",
                "password": "senha1234",
            },
        )

        login = client.post(
            "/api/v1/auth/login",
            json={"email": "maria@test.com", "password": "senha1234"},
        )
        assert login.status_code == 200
        assert login.get_json()["user"]["email"] == "maria@test.com"

    def test_segundo_aceite_falha(self, app, client, admin_headers):
        token, _ = self._aprovar_e_token(app, client, admin_headers)
        primeiro = {
            "token": token,
            "name": "Maria",
            "email": "maria@test.com",
            "password": "senha1234",
        }
        assert client.post("/api/v1/invitations/accept", json=primeiro).status_code == 201

        segundo = dict(primeiro, email="outro@test.com")
        resp = client.post("/api/v1/invitations/accept", json=segundo)

        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_invitation"

        with app.app_context():
            assert db.session.query(User).count() == 1

    def test_token_invalido_falha(self, client):
        resp = client.post("/api/v1/invitations/accept", json={
            "token": "token-que-nao-existe",
            "name": "X",
            "email": "x@test.com",
            "password": "senha1234",
        })
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_invitation"

    def test_token_expirado_falha(self, app, client, admin_headers):
        from datetime import datetime, timedelta

        token, _ = self._aprovar_e_token(app, client, admin_headers)
        with app.app_context():
            convite = db.session.query(Invitation).one()
            convite.expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.session.commit()

        resp = client.post("/api/v1/invitations/accept", json={
            "token": token,
            "name": "X",
            "email": "x@test.com",
            "password": "senha1234",
        })
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_invitation"

    def test_token_revogado_falha(self, app, client, admin_headers):
        resp1 = aprovar(app, client, admin_headers)
        token = resp1.get_json()["invite_url"].split("#token=")[1]
        est_id = resp1.get_json()["establishment"]["id"]

        client.post(f"/api/v1/admin/establishments/{est_id}/invitations", headers=admin_headers)

        resp = client.post("/api/v1/invitations/accept", json={
            "token": token,
            "name": "X",
            "email": "x@test.com",
            "password": "senha1234",
        })
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_invitation"

    def test_respostas_genericas_sao_identicas(self, app, client, admin_headers):
        """Inválido, expirado e revogado devolvem exatamente o mesmo corpo."""
        respostas = []

        # Token inválido
        respostas.append(client.post("/api/v1/invitations/accept", json={
            "token": "nao-existe",
            "name": "X",
            "email": "x@test.com",
            "password": "senha1234",
        }).get_json())

        # Expirado
        from datetime import datetime, timedelta

        token_exp, _ = self._aprovar_e_token(app, client, admin_headers)
        with app.app_context():
            convite = db.session.query(Invitation).order_by(Invitation.id.desc()).first()
            convite.expires_at = datetime.utcnow() - timedelta(seconds=1)
            db.session.commit()
        respostas.append(client.post("/api/v1/invitations/accept", json={
            "token": token_exp,
            "name": "X",
            "email": "x@test.com",
            "password": "senha1234",
        }).get_json())

        # Revogado
        resp1 = aprovar(app, client, admin_headers, nome="Revogado Aqui")
        token_rev = resp1.get_json()["invite_url"].split("#token=")[1]
        est_rev = resp1.get_json()["establishment"]["id"]
        client.post(f"/api/v1/admin/establishments/{est_rev}/invitations", headers=admin_headers)
        respostas.append(client.post("/api/v1/invitations/accept", json={
            "token": token_rev,
            "name": "X",
            "email": "x@test.com",
            "password": "senha1234",
        }).get_json())

        assert respostas[0] == respostas[1] == respostas[2]

    def test_email_ja_cadastrado_recusa_com_mensagem_clara(
        self, app, client, admin_headers, user_headers
    ):
        """A fixture user_headers já cria comum@test.com como User."""
        token, _ = self._aprovar_e_token(app, client, admin_headers)

        resp = client.post("/api/v1/invitations/accept", json={
            "token": token,
            "name": "Maria",
            "email": "comum@test.com",
            "password": "senha1234",
        })
        assert resp.status_code == 409
        assert resp.get_json()["error"]["code"] == "email_already_exists"
        assert "já está cadastrado" in resp.get_json()["error"]["message"]

        # O convite continua pendente (não foi consumido).
        with app.app_context():
            convite = db.session.query(Invitation).one()
            assert convite.accepted_at is None

    def test_senha_curta_recusada(self, app, client, admin_headers):
        token, _ = self._aprovar_e_token(app, client, admin_headers)
        resp = client.post("/api/v1/invitations/accept", json={
            "token": token,
            "name": "Maria",
            "email": "maria@test.com",
            "password": "123",
        })
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "weak_password"

    def test_email_invalido_recusado(self, app, client, admin_headers):
        token, _ = self._aprovar_e_token(app, client, admin_headers)
        resp = client.post("/api/v1/invitations/accept", json={
            "token": token,
            "name": "Maria",
            "email": "nao-e-email",
            "password": "senha1234",
        })
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_email"


# ---------------------------------------------------------------------------
# Atomicidade do aceite
# ---------------------------------------------------------------------------

class TestAtomicidade:
    """
    O aceite é protegido por um UPDATE condicional, não apenas por um SELECT.

    Uma corrida real de duas conexões exigiria PostgreSQL: com SQLite em memória
    cada thread recebe um banco diferente, então o teste seria vazio. Aqui
    verificamos o mecanismo diretamente: o UPDATE devolve rowcount 0 para um
    convite já marcado, e o serviço aborta antes de criar a segunda conta.
    """

    def test_update_guardado_nao_marca_duas_vezes(self, app, admin):
        from datetime import datetime
        from sqlalchemy import update

        with app.app_context():
            est = Establishment(name="M", slug="m")
            db.session.add(est)
            db.session.commit()
            resultado = svc.criar_convite(
                establishment_id=est.id,
                criado_por_admin_id=admin,
                app_base_url="https://app.test",
                sess=db.session,
            )
            convite_id = resultado["invitation"].id
            db.session.commit()

            agora = datetime.utcnow()

            # Primeira marcação: vence.
            primeiro = db.session.execute(
                update(Invitation)
                .where(
                    Invitation.id == convite_id,
                    Invitation.accepted_at.is_(None),
                    Invitation.revoked_at.is_(None),
                    Invitation.expires_at > agora,
                )
                .values(accepted_at=agora)
            )
            db.session.commit()
            assert primeiro.rowcount == 1

            # Segunda tentativa com o mesmo estado: ninguém pode marcar.
            segundo = db.session.execute(
                update(Invitation)
                .where(
                    Invitation.id == convite_id,
                    Invitation.accepted_at.is_(None),
                    Invitation.revoked_at.is_(None),
                    Invitation.expires_at > agora,
                )
                .values(accepted_at=agora)
            )
            db.session.commit()
            assert segundo.rowcount == 0

    def test_segundo_aceite_na_cria_conta(self, app, client, admin_headers):
        """Aceitar duas vezes cria uma conta só (e a segunda devolve erro genérico)."""
        resp = aprovar(app, client, admin_headers)
        token = resp.get_json()["invite_url"].split("#token=")[1]

        base = {"token": token, "name": "Maria", "password": "senha1234"}

        primeiro = client.post(
            "/api/v1/invitations/accept", json=dict(base, email="primeira@test.com")
        )
        segundo = client.post(
            "/api/v1/invitations/accept", json=dict(base, email="segunda@test.com")
        )

        assert primeiro.status_code == 201
        assert segundo.status_code == 400
        assert segundo.get_json()["error"]["code"] == "invalid_invitation"

        with app.app_context():
            users = db.session.query(User).all()
            assert len(users) == 1
            assert users[0].email == "primeira@test.com"
            assert db.session.query(EstablishmentMember).count() == 1

    def test_aceite_nao_deixa_conta_parcial_quando_falha(self, app, client, admin_headers):
        """Se o e-mail já existe, nada é gravado: nem vínculo, nem consumo do convite."""
        resp = aprovar(app, client, admin_headers)
        token = resp.get_json()["invite_url"].split("#token=")[1]

        with app.app_context():
            user_existente = User(email="ocupado@test.com", name="Ja Existe")
            user_existente.set_password("senha1234")
            db.session.add(user_existente)
            db.session.commit()

        falha = client.post("/api/v1/invitations/accept", json={
            "token": token,
            "name": "Maria",
            "email": "ocupado@test.com",
            "password": "senha1234",
        })
        assert falha.status_code == 409

        with app.app_context():
            assert db.session.query(User).count() == 1
            assert db.session.query(EstablishmentMember).count() == 0
            convite = db.session.query(Invitation).one()
            assert convite.accepted_at is None
            assert convite.accepted_user_id is None


# ---------------------------------------------------------------------------
# Autorização
# ---------------------------------------------------------------------------

class TestAutorizacao:
    @pytest.mark.parametrize("metodo,rota", [
        ("post", "/api/v1/admin/leads/1/approve-market"),
        ("post", "/api/v1/admin/establishments/1/invitations"),
        ("get", "/api/v1/admin/establishments"),
        ("get", "/api/v1/admin/establishments/1"),
    ])
    def test_anonimo_recebe_401(self, client, metodo, rota):
        resp = getattr(client, metodo)(rota)
        assert resp.status_code == 401

    @pytest.mark.parametrize("metodo,rota", [
        ("post", "/api/v1/admin/leads/1/approve-market"),
        ("post", "/api/v1/admin/establishments/1/invitations"),
        ("get", "/api/v1/admin/establishments"),
        ("get", "/api/v1/admin/establishments/1"),
    ])
    def test_usuario_comum_recebe_403(self, client, user_headers, metodo, rota):
        resp = getattr(client, metodo)(rota, headers=user_headers)
        assert resp.status_code == 403

    def test_token_admin_nao_passa_em_rota_de_usuario(self, client, admin_headers):
        resp = client.get("/api/v1/auth/me", headers=admin_headers)
        assert resp.status_code == 403

    def test_aceite_nao_exige_autenticacao(self, client):
        resp = client.post("/api/v1/invitations/validate", json={"token": "x"})
        # 400 (token inválido), não 401: a rota é pública.
        assert resp.status_code == 400

    def test_convert_continua_exigindo_admin(self, client):
        resp = client.post("/api/leads/1/convert", json={"password": "senha1234"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------

class TestRateLimit:
    def test_validate_limita_tentativas(self, client):
        responses = [
            client.post("/api/v1/invitations/validate", json={"token": f"t{i}"})
            for i in range(35)
        ]
        assert any(r.status_code == 429 for r in responses)

    def test_accept_limita_tentativas(self, client):
        responses = [
            client.post("/api/v1/invitations/accept", json={
                "token": f"t{i}",
                "name": "X",
                "email": "x@test.com",
                "password": "senha1234",
            })
            for i in range(15)
        ]
        assert any(r.status_code == 429 for r in responses)
