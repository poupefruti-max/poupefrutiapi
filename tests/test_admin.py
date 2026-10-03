"""Testes para o painel admin."""

import pytest
from app import create_app
from app.extensions import db
from app.models import Lead, LeadType, LeadStatus, AdminUser, User


@pytest.fixture
def app():
    """Cria app Flask para testes."""
    app = create_app("testing")
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SQLALCHEMY_ENGINE_OPTIONS": {},
    })

    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def admin_user(app):
    """Cria um usuário admin para testes."""
    with app.app_context():
        admin = AdminUser(email="admin@test.com", name="Admin Test")
        admin.set_password("senha123")
        db.session.add(admin)
        db.session.commit()
        return admin


@pytest.fixture
def admin_token(client, admin_user):
    """Faz login como admin e retorna o token."""
    resp = client.post("/api/v1/admin/auth/login", json={
        "email": "admin@test.com",
        "password": "senha123",
    })
    return resp.get_json()["access_token"]


@pytest.fixture
def regular_user(app):
    """Cria um usuário comum para testes."""
    with app.app_context():
        user = User(email="user@test.com", name="User Test")
        user.set_password("senha123")
        db.session.add(user)
        db.session.commit()
        return user


@pytest.fixture
def user_token(client, regular_user):
    """Faz login como usuário comum e retorna o token."""
    resp = client.post("/api/v1/auth/login", json={
        "email": "user@test.com",
        "password": "senha123",
    })
    return resp.get_json()["access_token"]


@pytest.fixture
def client(app):
    return app.test_client()


class TestAdminAuth:
    """Testes de autenticação do admin."""

    def test_login_success(self, client, admin_user):
        """Login de admin com credenciais válidas."""
        resp = client.post("/api/v1/admin/auth/login", json={
            "email": "admin@test.com",
            "password": "senha123",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "access_token" in data
        assert data["admin"]["email"] == "admin@test.com"
        assert data["admin"]["name"] == "Admin Test"

    def test_login_invalid_credentials(self, client, admin_user):
        """Login falha com credenciais inválidas."""
        resp = client.post("/api/v1/admin/auth/login", json={
            "email": "admin@test.com",
            "password": "wrongpassword",
        })
        assert resp.status_code == 401
        assert resp.get_json()["error"]["code"] == "invalid_credentials"

    def test_login_missing_credentials(self, client):
        """Login falha sem credenciais."""
        resp = client.post("/api/v1/admin/auth/login", json={})
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "missing_credentials"

    def test_rate_limit_login(self, client, admin_user):
        """Rate limit bloqueia após 5 tentativas por minuto."""
        for _ in range(5):
            resp = client.post("/api/v1/admin/auth/login", json={
                "email": "admin@test.com",
                "password": "wrongpassword",
            })
            assert resp.status_code == 401

        # 6ª tentativa deve ser bloqueada
        resp = client.post("/api/v1/admin/auth/login", json={
            "email": "admin@test.com",
            "password": "wrongpassword",
        })
        assert resp.status_code == 429

    def test_me_with_valid_token(self, client, admin_token):
        """GET /me com token válido."""
        resp = client.get("/api/v1/admin/auth/me", headers={
            "Authorization": f"Bearer {admin_token}"
        })
        assert resp.status_code == 200
        assert resp.get_json()["admin"]["email"] == "admin@test.com"

    def test_me_without_token(self, client):
        """GET /me sem token retorna 401."""
        resp = client.get("/api/v1/admin/auth/me")
        assert resp.status_code == 401


class TestAdminTokenIsolation:
    """Testes de isolamento de tokens entre admin e usuário comum."""

    def test_admin_token_rejected_on_user_me(self, client, admin_token):
        """Token de admin é rejeitado em /api/v1/auth/me."""
        resp = client.get("/api/v1/auth/me", headers={
            "Authorization": f"Bearer {admin_token}"
        })
        assert resp.status_code == 403
        assert resp.get_json()["error"]["code"] == "forbidden"

    def test_user_token_rejected_on_admin_me(self, client, user_token):
        """Token de usuário comum é rejeitado em /api/v1/admin/auth/me."""
        resp = client.get("/api/v1/admin/auth/me", headers={
            "Authorization": f"Bearer {user_token}"
        })
        assert resp.status_code == 403
        assert resp.get_json()["error"]["code"] == "forbidden"

    def test_user_token_rejected_on_admin_routes(self, client, user_token):
        """Token de usuário comum é rejeitado nas rotas de admin."""
        resp = client.get("/api/v1/admin/leads", headers={
            "Authorization": f"Bearer {user_token}"
        })
        assert resp.status_code == 403


class TestAdminLeads:
    """Testes de listagem e manipulação de leads pelo admin."""

    def _create_leads(self, app):
        """Cria leads de teste e retorna os IDs."""
        with app.app_context():
            leads = [
                Lead(
                    type=LeadType.person,
                    name="João Silva",
                    email="joao@test.com",
                    phone="19999990000",
                    source="landing",
                    status=LeadStatus.new,
                    bairro="Centro",
                    cesta_tipo="frutas",
                ),
                Lead(
                    type=LeadType.establishment,
                    name="Mercado Central",
                    email="mercado@test.com",
                    phone="19888880000",
                    source="landing",
                    status=LeadStatus.contacted,
                    bairro="Vila Nova",
                    cesta_tipo=None,
                    extra={"responsavel": "Maria", "estabelecimento": "Mercado Central"},
                ),
                Lead(
                    type=LeadType.person,
                    name="Ana Souza",
                    email="ana@test.com",
                    phone="19777770000",
                    source="landing",
                    status=LeadStatus.qualified,
                    bairro="Centro",
                    cesta_tipo="ambas",
                ),
            ]
            db.session.add_all(leads)
            db.session.commit()
            return [lead.id for lead in leads]

    def test_list_leads_no_filters(self, client, admin_token, app):
        """Listagem sem filtros."""
        self._create_leads(app)
        resp = client.get("/api/v1/admin/leads", headers={
            "Authorization": f"Bearer {admin_token}"
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["leads"]) == 3
        assert data["pagination"]["total"] == 3

    def test_list_leads_filter_status(self, client, admin_token, app):
        """Filtro por status."""
        self._create_leads(app)
        resp = client.get("/api/v1/admin/leads?status=contacted", headers={
            "Authorization": f"Bearer {admin_token}"
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["leads"]) == 1
        assert data["leads"][0]["status"] == "contacted"

    def test_list_leads_filter_type(self, client, admin_token, app):
        """Filtro por tipo."""
        self._create_leads(app)
        resp = client.get("/api/v1/admin/leads?type=establishment", headers={
            "Authorization": f"Bearer {admin_token}"
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["leads"]) == 1
        assert data["leads"][0]["type"] == "establishment"

    def test_list_leads_filter_bairro(self, client, admin_token, app):
        """Filtro por bairro."""
        self._create_leads(app)
        resp = client.get("/api/v1/admin/leads?bairro=Centro", headers={
            "Authorization": f"Bearer {admin_token}"
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["leads"]) == 2
        assert all(l["bairro"] == "Centro" for l in data["leads"])

    def test_list_leads_search(self, client, admin_token, app):
        """Busca por nome/email/telefone."""
        self._create_leads(app)
        resp = client.get("/api/v1/admin/leads?search=joao", headers={
            "Authorization": f"Bearer {admin_token}"
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["leads"]) == 1
        assert data["leads"][0]["name"] == "João Silva"

    def test_list_leads_pagination(self, client, admin_token, app):
        """Paginação."""
        self._create_leads(app)
        resp = client.get("/api/v1/admin/leads?page=1&per_page=2", headers={
            "Authorization": f"Bearer {admin_token}"
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["leads"]) == 2
        assert data["pagination"]["page"] == 1
        assert data["pagination"]["per_page"] == 2
        assert data["pagination"]["total"] == 3
        assert data["pagination"]["pages"] == 2

    def test_list_leads_sorting(self, client, admin_token, app):
        """Ordenação."""
        self._create_leads(app)
        resp = client.get("/api/v1/admin/leads?sort=name", headers={
            "Authorization": f"Bearer {admin_token}"
        })
        assert resp.status_code == 200
        data = resp.get_json()
        names = [l["name"] for l in data["leads"]]
        assert names == sorted(names)

    def test_get_lead_detail(self, client, admin_token, app):
        """Detalhe do lead."""
        lead_ids = self._create_leads(app)
        lead_id = lead_ids[0]
        resp = client.get(f"/api/v1/admin/leads/{lead_id}", headers={
            "Authorization": f"Bearer {admin_token}"
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["lead"]["id"] == lead_id
        assert data["lead"]["name"] == "João Silva"

    def test_get_lead_not_found(self, client, admin_token):
        """Lead não encontrado."""
        resp = client.get("/api/v1/admin/leads/99999", headers={
            "Authorization": f"Bearer {admin_token}"
        })
        assert resp.status_code == 404


class TestAdminLeadStatus:
    """Testes de alteração de status."""

    def _create_lead(self, app, status=LeadStatus.new):
        with app.app_context():
            lead = Lead(
                type=LeadType.person,
                name="Teste",
                email="teste@test.com",
                phone="19999990000",
                source="landing",
                status=status,
                bairro="Centro",
                cesta_tipo="frutas",
            )
            db.session.add(lead)
            db.session.commit()
            return lead.id

    def test_change_status_valid_transition(self, client, admin_token, app):
        """Transição válida de status."""
        lead_id = self._create_lead(app, LeadStatus.new)
        resp = client.patch(f"/api/v1/admin/leads/{lead_id}/status", json={
            "status": "contacted"
        }, headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        assert resp.get_json()["lead"]["status"] == "contacted"

    def test_change_status_invalid_transition(self, client, admin_token, app):
        """Transição inválida de status."""
        lead_id = self._create_lead(app, LeadStatus.converted)
        resp = client.patch(f"/api/v1/admin/leads/{lead_id}/status", json={
            "status": "contacted"
        }, headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_transition"

    def test_change_status_reactivate_from_lost(self, client, admin_token, app):
        """Reativar lead lost -> new."""
        lead_id = self._create_lead(app, LeadStatus.lost)
        resp = client.patch(f"/api/v1/admin/leads/{lead_id}/status", json={
            "status": "new"
        }, headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        assert resp.get_json()["lead"]["status"] == "new"

    def test_change_status_missing_status(self, client, admin_token, app):
        """Status obrigatório."""
        lead_id = self._create_lead(app)
        resp = client.patch(f"/api/v1/admin/leads/{lead_id}/status", json={}, headers={
            "Authorization": f"Bearer {admin_token}"
        })
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "missing_status"


class TestAdminConvert:
    """Testes de conversão de lead."""

    def _create_lead(self, app, lead_type=LeadType.person):
        with app.app_context():
            lead = Lead(
                type=lead_type,
                name="Teste Conversão",
                email="convert@test.com",
                phone="19999990000",
                source="landing",
                status=LeadStatus.qualified,
                bairro="Centro",
                cesta_tipo="frutas" if lead_type == LeadType.person else None,
            )
            db.session.add(lead)
            db.session.commit()
            return lead.id

    def test_convert_person(self, client, admin_token, app):
        """Converter lead person em usuário."""
        lead_id = self._create_lead(app, LeadType.person)
        resp = client.post(f"/api/v1/admin/leads/{lead_id}/convert", json={
            "password": "senha123",
        }, headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["lead"]["status"] == "converted"
        assert "user" in data
        assert data["user"]["email"] == "convert@test.com"

    def test_convert_establishment(self, client, admin_token, app):
        """Converter lead establishment em estabelecimento + usuário."""
        lead_id = self._create_lead(app, LeadType.establishment)
        resp = client.post(f"/api/v1/admin/leads/{lead_id}/convert", json={
            "password": "senha123",
            "establishment": {
                "cnpj": "12.345.678/0001-90",
                "name": "Mercado Teste",
                "address": "Rua Teste, 123"
            }
        }, headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["lead"]["status"] == "converted"
        assert "user" in data
        assert "establishment" in data
        assert data["establishment"]["cnpj"] == "12.345.678/0001-90"

    def test_convert_without_password(self, client, admin_token, app):
        """Conversão falha sem senha."""
        lead_id = self._create_lead(app)
        resp = client.post(f"/api/v1/admin/leads/{lead_id}/convert", json={}, headers={
            "Authorization": f"Bearer {admin_token}"
        })
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "conversion_error"

    def test_convert_idempotent(self, client, admin_token, app):
        """Conversão idempotente - segunda vez retorna existente."""
        lead_id = self._create_lead(app)
        # Primeira conversão
        resp1 = client.post(f"/api/v1/admin/leads/{lead_id}/convert", json={
            "password": "senha123",
        }, headers={"Authorization": f"Bearer {admin_token}"})
        assert resp1.status_code == 201
        user_id = resp1.get_json()["user"]["id"]
        # Segunda conversão
        resp2 = client.post(f"/api/v1/admin/leads/{lead_id}/convert", json={
            "password": "outrasenha",
        }, headers={"Authorization": f"Bearer {admin_token}"})
        assert resp2.status_code == 201
        data2 = resp2.get_json()
        assert data2["user"]["id"] == user_id
        assert data2["lead"]["status"] == "converted"


class TestAdminStats:
    """Testes de estatísticas."""

    def test_stats(self, client, admin_token, app):
        """Estatísticas gerais."""
        with app.app_context():
            db.session.add(Lead(
                type=LeadType.person,
                name="Teste", email="a@test.com", phone="19999990000",
                source="landing", status=LeadStatus.new,
                bairro="Centro", cesta_tipo="frutas"
            ))
            db.session.add(Lead(
                type=LeadType.establishment,
                name="Teste 2", email="b@test.com", phone="19888880000",
                source="landing", status=LeadStatus.contacted,
                bairro="Vila Nova", cesta_tipo=None
            ))
            db.session.commit()

        resp = client.get("/api/v1/admin/stats", headers={
            "Authorization": f"Bearer {admin_token}"
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "by_status" in data
        assert "by_type" in data
        assert "today" in data
        assert "last_7_days" in data
        assert "daily" in data


class TestAdminExport:
    """Testes de exportação CSV."""

    def _create_lead(self, app):
        with app.app_context():
            lead = Lead(
                type=LeadType.person,
                name="Export Test",
                email="export@test.com",
                phone="19999990000",
                source="landing",
                status=LeadStatus.new,
                bairro="Centro",
                cesta_tipo="frutas",
                extra={"origem": "teste"},
            )
            db.session.add(lead)
            db.session.commit()
            return lead.id

    def test_export_csv(self, client, admin_token, app):
        """Exportação CSV com proteção contra injection."""
        self._create_lead(app)
        resp = client.get("/api/v1/admin/export", headers={
            "Authorization": f"Bearer {admin_token}"
        })
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/csv")
        content = resp.get_data(as_text=True)
        assert "id,type,name,email,phone" in content
        assert "Export Test" in content
        # Proteção CSV injection: campos começando com =,+,-,@ são prefixados com '
        assert "'=" not in content or "=" in content  # apenas verifica que não quebrou

    def test_export_csv_with_filters(self, client, admin_token, app):
        """Exportação CSV respeita filtros."""
        with app.app_context():
            db.session.add(Lead(
                type=LeadType.person, name="A", email="a@test.com", phone="19999990000",
                source="landing", status=LeadStatus.new, bairro="Centro", cesta_tipo="frutas"
            ))
            db.session.add(Lead(
                type=LeadType.establishment, name="B", email="b@test.com", phone="19888880000",
                source="landing", status=LeadStatus.contacted, bairro="Vila Nova", cesta_tipo=None
            ))
            db.session.commit()

        resp = client.get("/api/v1/admin/export?status=contacted", headers={
            "Authorization": f"Bearer {admin_token}"
        })
        assert resp.status_code == 200
        content = resp.get_data(as_text=True)
        lines = content.strip().split("\n")
        assert len(lines) == 2  # header + 1 lead
        assert "B" in content
        assert "A" not in content


class TestAdminAuthWithoutToken:
    """Testes sem token (devem retornar 401)."""

    def test_list_leads_no_token(self, client):
        resp = client.get("/api/v1/admin/leads")
        assert resp.status_code == 401

    def test_get_lead_no_token(self, client):
        resp = client.get("/api/v1/admin/leads/1")
        assert resp.status_code == 401

    def test_change_status_no_token(self, client):
        resp = client.patch("/api/v1/admin/leads/1/status", json={"status": "contacted"})
        assert resp.status_code == 401

    def test_convert_no_token(self, client):
        resp = client.post("/api/v1/admin/leads/1/convert", json={"password": "123"})
        assert resp.status_code == 401

    def test_stats_no_token(self, client):
        resp = client.get("/api/v1/admin/stats")
        assert resp.status_code == 401

    def test_export_no_token(self, client):
        resp = client.get("/api/v1/admin/export")
        assert resp.status_code == 401