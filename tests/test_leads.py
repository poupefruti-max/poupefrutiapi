"""Testes para o serviço de leads."""

import pytest
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.models import Lead, LeadType, LeadStatus, User, Establishment, EstablishmentMember
from services.leads import (
    create_lead,
    update_lead_status,
    convert_lead,
    LeadError,
    LeadAlreadyConvertedError,
    DuplicateLeadError,
)


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
def session(app):
    """Provê sessão do banco para testes."""
    with app.app_context():
        yield db.session


class TestCreateLead:
    """Testes para create_lead."""

    def test_create_lead_person(self, session):
        """Cria lead do tipo person."""
        lead = create_lead(
            type=LeadType.person,
            name="João Silva",
            phone="(19) 99999-0000",
            source="test",
            email="joao@test.com",
        )
        assert lead.id is not None
        assert lead.type == LeadType.person
        assert lead.name == "João Silva"
        assert lead.email == "joao@test.com"
        assert lead.phone == "19999990000"
        assert lead.status == LeadStatus.new
        assert lead.extra == {}

    def test_create_lead_establishment(self, session):
        """Cria lead do tipo establishment."""
        lead = create_lead(
            type=LeadType.establishment,
            name="Mercado Central",
            phone="(19) 98888-0000",
            source="test",
            email="contato@mercado.com",
            extra={"cnpj": "12.345.678/0001-90"},
        )
        assert lead.id is not None
        assert lead.type == LeadType.establishment
        assert lead.name == "Mercado Central"
        assert lead.extra == {"cnpj": "12.345.678/0001-90"}

    def test_create_lead_normalizes_email(self, session):
        """Email deve ser normalizado para minúsculas."""
        lead = create_lead(
            type=LeadType.person,
            name="Test",
            phone="(19) 99999-0000",
            email="  JOAO@TEST.COM  ",
        )
        assert lead.email == "joao@test.com"

    def test_create_lead_normalizes_phone(self, session):
        """Telefone deve ser normalizado removendo formatação."""
        lead = create_lead(
            type=LeadType.person,
            name="Test",
            phone="(19) 99999-0000",
        )
        assert lead.phone == "19999990000"

    def test_duplicate_email_same_type_returns_existing(self, session):
        """Email duplicado com mesmo tipo retorna lead existente (upsert)."""
        lead1 = create_lead(
            type=LeadType.person,
            name="João",
            phone="(19) 99999-0000",
            email="joao@test.com",
        )
        lead2 = create_lead(
            type=LeadType.person,
            name="João Diferente",
            phone="(19) 88888-0000",
            email="joao@test.com",
        )
        assert lead1.id == lead2.id
        assert lead2.name == "João"  # Mantém o original

    def test_same_email_different_type_allowed(self, session):
        """Mesmo email com tipos diferentes é permitido."""
        lead1 = create_lead(
            type=LeadType.person,
            name="João",
            phone="(19) 99999-0000",
            email="joao@test.com",
        )
        lead2 = create_lead(
            type=LeadType.establishment,
            name="Mercado do João",
            phone="(19) 88888-0000",
            email="joao@test.com",
        )
        assert lead1.id != lead2.id

    def test_lead_without_email_allowed(self, session):
        """Lead sem email é permitido."""
        lead = create_lead(
            type=LeadType.person,
            name="Sem Email",
            phone="(19) 99999-0000",
            email=None,
        )
        assert lead.id is not None
        assert lead.email is None


class TestUpdateLeadStatus:
    """Testes para update_lead_status."""

    def test_update_status_new_to_contacted(self, session):
        """Transição new -> contacted."""
        lead = create_lead(
            type=LeadType.person,
            name="Test",
            phone="(19) 99999-0000",
        )
        updated = update_lead_status(lead.id, LeadStatus.contacted)
        assert updated.status == LeadStatus.contacted
        assert updated.converted_at is None

    def test_update_status_to_converted_sets_timestamp(self, session):
        """Transição para converted define converted_at."""
        lead = create_lead(
            type=LeadType.person,
            name="Test",
            phone="(19) 99999-0000",
        )
        updated = update_lead_status(lead.id, LeadStatus.converted)
        assert updated.status == LeadStatus.converted
        assert updated.converted_at is not None

    def test_invalid_transition_raises(self, session):
        """Transição inválida levanta erro."""
        lead = create_lead(
            type=LeadType.person,
            name="Test",
            phone="(19) 99999-0000",
        )
        update_lead_status(lead.id, LeadStatus.converted)
        with pytest.raises(LeadError, match="Transição inválida"):
            update_lead_status(lead.id, LeadStatus.contacted)

    def test_reactivate_from_lost(self, session):
        """Pode reativar lead lost para new."""
        lead = create_lead(
            type=LeadType.person,
            name="Test",
            phone="(19) 99999-0000",
        )
        update_lead_status(lead.id, LeadStatus.lost)
        updated = update_lead_status(lead.id, LeadStatus.new)
        assert updated.status == LeadStatus.new


class TestConvertLead:
    """Testes para convert_lead."""

    def test_convert_person_creates_user(self, session):
        """Conversão de person cria User vinculado."""
        lead = create_lead(
            type=LeadType.person,
            name="João Silva",
            phone="(19) 99999-0000",
            email="joao@test.com",
        )

        result = convert_lead(lead.id, password="senha123")

        assert "user" in result
        user = result["user"]
        assert user.email == "joao@test.com"
        assert user.name == "João Silva"
        assert user.lead_id == lead.id
        assert user.check_password("senha123")
        assert result["lead"].status == LeadStatus.converted
        assert result["lead"].converted_at is not None

    def test_convert_establishment_lead_is_rejected(self, session):
        """Lead de mercado não passa por convert_lead: usa approve-market."""
        lead = create_lead(
            type=LeadType.establishment,
            name="Mercado Central",
            phone="(19) 98888-0000",
            email="contato@mercado.com",
        )

        with pytest.raises(LeadError, match="approve-market"):
            convert_lead(lead.id, password="senha123",
                         establishment_data={"cnpj": "12.345.678/0001-90"})

    def test_convert_idempotent_person(self, session):
        """Segunda conversão do mesmo lead person retorna user existente."""
        lead = create_lead(
            type=LeadType.person,
            name="João",
            phone="(19) 99999-0000",
            email="joao@test.com",
        )

        result1 = convert_lead(lead.id, password="senha123")
        result2 = convert_lead(lead.id, password="outrasenha")

        assert result1["user"].id == result2["user"].id
        assert result2["user"].check_password("senha123")  # Senha original mantida

    def test_convert_person_without_email_raises(self, session):
        """Conversão de person sem email falha."""
        lead = create_lead(
            type=LeadType.person,
            name="Sem Email",
            phone="(19) 99999-0000",
            email=None,
        )
        with pytest.raises(LeadError, match="precisa de email"):
            convert_lead(lead.id, password="senha123")

    def test_convert_without_password_raises(self, session):
        """Conversão sem senha falha."""
        lead = create_lead(
            type=LeadType.person,
            name="Test",
            phone="(19) 99999-0000",
            email="test@test.com",
        )
        with pytest.raises(LeadError, match="Senha é obrigatória"):
            convert_lead(lead.id, password=None)

    def test_convert_nonexistent_lead_raises(self, session):
        """Conversão de lead inexistente falha."""
        with pytest.raises(LeadError, match="não encontrado"):
            convert_lead(99999, password="senha123")


class TestLeadPersistence:
    """Testes de persistência real (verifica com sessão nova após commit)."""

    def test_create_cliente_persists_after_commit(self, app):
        """POST /api/leads/cliente persiste no banco após commit."""
        client = app.test_client()
        response = client.post("/api/leads/cliente", json={
            "tipo": "cliente",
            "nome": "João Silva",
            "whatsapp": "(19) 99999-0000",
            "email": "joao@test.com",
            "bairro": "Centro",
            "cesta": "frutas",
        })
        assert response.status_code == 201
        lead_id = response.get_json()["lead"]["id"]

        # Nova sessão para verificar persistência real
        with app.app_context():
            db.session.remove()
            from app.models import Lead
            lead = db.session.get(Lead, lead_id)
            assert lead is not None
            assert lead.type.value == "person"
            assert lead.name == "João Silva"
            assert lead.email == "joao@test.com"
            assert lead.phone == "19999990000"
            assert lead.source == "landing-poupefruti"
            # Novas colunas
            assert lead.bairro == "Centro"
            assert lead.cesta_tipo == "frutas"
            # extra TEM bairro/cesta no to_dict() (compatibilidade Opção A)
            lead_dict = lead.to_dict()
            assert lead_dict["extra"]["bairro"] == "Centro"
            assert lead_dict["extra"]["cesta"] == "frutas"
            assert lead_dict["extra"].get("enviado_em") is None

    def test_create_mercado_persists_after_commit(self, app):
        """POST /api/leads/mercado persiste no banco após commit."""
        client = app.test_client()
        response = client.post("/api/leads/mercado", json={
            "tipo": "mercado",
            "responsavel": "Maria Silva",
            "whatsapp": "(19) 98888-0000",
            "email": "maria@mercado.com",
            "bairro": "Centro",
            "estabelecimento": "Mercado da Maria",
            "tipoEstabelecimento": "mercado",
        })
        assert response.status_code == 201
        lead_id = response.get_json()["lead"]["id"]

        # Nova sessão para verificar persistência real
        with app.app_context():
            db.session.remove()
            from app.models import Lead
            lead = db.session.get(Lead, lead_id)
            assert lead is not None
            assert lead.type.value == "establishment"
            assert lead.name == "Mercado da Maria"
            # Novas colunas
            assert lead.bairro == "Centro"
            assert lead.cesta_tipo is None
            # extra TEM bairro no to_dict() (compatibilidade Opção A)
            lead_dict = lead.to_dict()
            assert lead_dict["extra"]["bairro"] == "Centro"
            assert lead_dict["extra"]["responsavel"] == "Maria Silva"
            assert lead_dict["extra"]["estabelecimento"] == "Mercado da Maria"
            assert lead_dict["extra"]["tipo_estabelecimento"] == "mercado"

    def test_duplicate_email_upsert_persists(self, app):
        """Email duplicado faz upsert e persiste alterações (phone/extra/bairro/cesta_tipo)."""
        client = app.test_client()
        # Primeiro POST
        resp1 = client.post("/api/leads/cliente", json={
            "tipo": "cliente",
            "nome": "João Original",
            "whatsapp": "(19) 99999-0000",
            "email": "dup@test.com",
            "bairro": "Centro",
            "cesta": "frutas",
        })
        assert resp1.status_code == 201
        lead_id_1 = resp1.get_json()["lead"]["id"]

        # Segundo POST com mesmo email (upsert) - telefone válido com 9 dígitos
        resp2 = client.post("/api/leads/cliente", json={
            "tipo": "cliente",
            "nome": "João Atualizado",
            "whatsapp": "(19) 98888-0000",  # telefone diferente (válido)
            "email": "dup@test.com",
            "bairro": "Vila Nova",  # bairro diferente
            "cesta": "legumes",  # cesta diferente
        })
        assert resp2.status_code == 201
        lead_id_2 = resp2.get_json()["lead"]["id"]

        # Deve ser o mesmo lead (upsert)
        assert lead_id_1 == lead_id_2

        # Nova sessão para verificar persistência real
        with app.app_context():
            db.session.remove()
            from app.models import Lead
            lead = db.session.get(Lead, lead_id_1)
            assert lead is not None
            # Nome NÃO deve mudar (mantém o original no upsert)
            assert lead.name == "João Original"
            # Phone, bairro e cesta_tipo DEVEM ser atualizados
            assert lead.phone == "19988880000"
            assert lead.bairro == "Vila Nova"
            assert lead.cesta_tipo == "legumes"
            # extra TEM bairro/cesta atualizados no to_dict() (compatibilidade Opção A)
            lead_dict = lead.to_dict()
            assert lead_dict["extra"]["bairro"] == "Vila Nova"
            assert lead_dict["extra"]["cesta"] == "legumes"

    def test_invalid_payload_no_persist(self, app):
        """Payload inválido não grava nada."""
        client = app.test_client()
        response = client.post("/api/leads/cliente", json={
            "tipo": "cliente",
            # sem nome
            "whatsapp": "(19) 99999-0000",
            "email": "invalido@test.com",
            "bairro": "Centro",
            "cesta": "frutas",
        })
        assert response.status_code == 400

        with app.app_context():
            db.session.remove()
            from app.models import Lead
            count = db.session.query(Lead).count()
            assert count == 0

    def test_invalid_tipo_no_persist(self, app):
        """Tipo desconhecido não grava nada."""
        client = app.test_client()
        response = client.post("/api/leads/cliente", json={
            "tipo": "xyz",  # inválido
            "nome": "Teste",
            "whatsapp": "(19) 99999-0000",
            "email": "tipo@test.com",
            "bairro": "Centro",
            "cesta": "frutas",
        })
        assert response.status_code == 400

        with app.app_context():
            db.session.remove()
            from app.models import Lead
            count = db.session.query(Lead).count()
            assert count == 0

    def test_empty_bairro_cesta_becomes_null(self, app):
        """bairro/cesta vazios viram NULL nas colunas."""
        client = app.test_client()
        response = client.post("/api/leads/cliente", json={
            "tipo": "cliente",
            "nome": "Teste Vazio",
            "whatsapp": "(19) 99999-0000",
            "email": "vazio@test.com",
            "bairro": "",
            "cesta": "",
        })
        assert response.status_code == 201
        lead_id = response.get_json()["lead"]["id"]

        with app.app_context():
            db.session.remove()
            from app.models import Lead
            lead = db.session.get(Lead, lead_id)
            assert lead is not None
            assert lead.bairro is None
            assert lead.cesta_tipo is None

    def test_bairro_da_lista_grava_o_texto_canonico(self, app):
        """
        Bairro que existe na lista vira o texto canônico.

        "centro" e "Centro" viram a mesma coisa: é o que impede a contagem por
        bairro de dividir o mesmo bairro em duas grafias.
        """
        client = app.test_client()
        response = client.post("/api/leads/cliente", json={
            "tipo": "cliente",
            "nome": "Joana",
            "whatsapp": "(19) 99999-0001",
            "email": "joana@test.com",
            "bairro": " centro ",
            "cesta": "frutas",
        })
        assert response.status_code == 201
        assert response.get_json()["lead"]["bairro"] == "Centro"

    def test_bairro_fora_da_lista_e_aceito(self, app):
        """
        Lead com bairro fora da lista NÃO é recusado.

        A lista é nova e o banco já tem valores que não estão nela (a landing
        tinha duas listas divergentes). Recusar aqui faria a pessoa escolher um
        bairro errado só para passar pela validação.
        """
        client = app.test_client()
        response = client.post("/api/leads/cliente", json={
            "tipo": "cliente",
            "nome": "Vila Nova",
            "whatsapp": "(19) 99999-0002",
            "email": "villanova@test.com",
            "bairro": "Vila Nova",
            "cesta": "frutas",
        })
        assert response.status_code == 201
        assert response.get_json()["lead"]["bairro"] == "Vila Nova"


class TestLeadErrorPaths:
    """
    Caminhos de erro das rotas.

    Houve um bug em que `erro("internal_error", "...")` era chamado com 2
    argumentos numa função que exige 3, então o próprio tratamento de erro
    estourava TypeError e a resposta virava 500 sem o JSON da API. Estes testes
    travam esse comportamento.
    """

    def test_commit_quebrado_devolve_500_com_json(self, app, monkeypatch):
        """Falha inesperada no commit vira 500 no formato {"error": {...}}."""
        from sqlalchemy.orm import Session

        def commit_quebrado(self):
            raise RuntimeError("falha simulada no commit")

        monkeypatch.setattr(Session, "commit", commit_quebrado)

        client = app.test_client()
        response = client.post("/api/leads/cliente", json={
            "tipo": "cliente",
            "nome": "Teste",
            "whatsapp": "(11) 99999-9999",
            "email": "erro500@exemplo.com",
            "bairro": "Centro",
            "cesta": "frutas",
        })

        assert response.status_code == 500
        # O corpo tem que ser o JSON da API, não um erro de template/TypeError.
        assert response.is_json
        corpo = response.get_json()
        assert corpo["error"]["code"] == "internal_error"
        assert corpo["error"]["message"] == "Erro interno no servidor."

        # Nada foi gravado.
        with app.app_context():
            db.session.remove()
            from app.models import Lead
            assert db.session.query(Lead).count() == 0

    def test_commit_quebrado_no_mercado_devolve_500(self, app, monkeypatch):
        """Mesma garantia na rota de estabelecimento."""
        from sqlalchemy.orm import Session

        def commit_quebrado(self):
            raise RuntimeError("falha simulada no commit")

        monkeypatch.setattr(Session, "commit", commit_quebrado)

        client = app.test_client()
        response = client.post("/api/leads/mercado", json={
            "tipo": "mercado",
            "responsavel": "Maria",
            "whatsapp": "(11) 98888-8888",
            "bairro": "Centro",
            "estabelecimento": "Mercado da Maria",
            "tipoEstabelecimento": "mercado",
        })

        assert response.status_code == 500
        assert response.get_json()["error"]["code"] == "internal_error"


class TestLeadRoutes:
    """Testes de integração para rotas de leads."""

    def test_create_cliente_route(self, app):
        """POST /api/leads/cliente cria lead person."""
        client = app.test_client()
        response = client.post("/api/leads/cliente", json={
            "tipo": "cliente",
            "nome": "João Silva",
            "whatsapp": "(19) 99999-0000",
            "email": "joao@test.com",
            "bairro": "Centro",
            "cesta": "frutas",
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data["lead"]["type"] == "person"
        assert data["lead"]["name"] == "João Silva"
        # Novas colunas na resposta
        assert data["lead"]["bairro"] == "Centro"
        assert data["lead"]["cesta_tipo"] == "frutas"
        # extra TEM bairro/cesta (compatibilidade Opção A)
        assert data["lead"]["extra"]["bairro"] == "Centro"
        assert data["lead"]["extra"]["cesta"] == "frutas"

    def test_create_mercado_route(self, app):
        """POST /api/leads/mercado cria lead establishment."""
        client = app.test_client()
        response = client.post("/api/leads/mercado", json={
            "tipo": "mercado",
            "responsavel": "Maria Silva",
            "whatsapp": "(19) 98888-0000",
            "email": "maria@mercado.com",
            "bairro": "Centro",
            "estabelecimento": "Mercado da Maria",
            "tipoEstabelecimento": "mercado",
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data["lead"]["type"] == "establishment"
        assert data["lead"]["name"] == "Mercado da Maria"
        # Novas colunas na resposta
        assert data["lead"]["bairro"] == "Centro"
        assert data["lead"]["cesta_tipo"] is None
        # extra TEM bairro (compatibilidade Opção A)
        assert data["lead"]["extra"]["bairro"] == "Centro"
        assert data["lead"]["extra"]["responsavel"] == "Maria Silva"

    def test_convert_lead_route(self, app):
        """POST /api/v1/admin/leads/<id>/convert converte lead (requer admin)."""
        client = app.test_client()
        # Cria lead
        resp = client.post("/api/leads/cliente", json={
            "tipo": "cliente",
            "nome": "João",
            "whatsapp": "(19) 99999-0000",
            "email": "joao@test.com",
            "bairro": "Centro",
            "cesta": "frutas",
        })
        lead_id = resp.get_json()["lead"]["id"]

        # Cria admin e faz login
        with app.app_context():
            from app.models import AdminUser
            admin = AdminUser(email="admin@test.com", name="Admin")
            admin.set_password("senha123")
            db.session.add(admin)
            db.session.commit()
            admin_id = admin.id

        # Login admin
        resp = client.post("/api/v1/admin/auth/login", json={
            "email": "admin@test.com",
            "password": "senha123",
        })
        assert resp.status_code == 200
        token = resp.get_json()["access_token"]

        # Converte usando token admin
        resp = client.post(f"/api/v1/admin/leads/{lead_id}/convert", json={
            "password": "senha123",
        }, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["lead"]["status"] == "converted"
        assert "user" in data
        assert data["user"]["email"] == "joao@test.com"

        # Verifica persistência com nova sessão
        with app.app_context():
            db.session.remove()
            from app.models import Lead, User
            lead = db.session.get(Lead, lead_id)
            assert lead is not None
            assert lead.status.value == "converted"
            user = db.session.query(User).filter_by(email="joao@test.com").first()
            assert user is not None
            assert user.lead_id == lead_id

    def test_commit_exception_cliente_returns_standard_500(self, app, monkeypatch):
        """db.session.commit levantando Exception em POST /api/leads/cliente vira 500 padronizado."""
        from sqlalchemy.orm import Session

        def commit_quebrado(self):
            raise Exception("boom")

        monkeypatch.setattr(Session, "commit", commit_quebrado)

        client = app.test_client()
        response = client.post("/api/leads/cliente", json={
            "tipo": "cliente",
            "nome": "Teste",
            "whatsapp": "(11) 99999-9999",
            "email": "boom@exemplo.com",
            "bairro": "Centro",
            "cesta": "frutas",
        })

        assert response.status_code == 500
        assert response.get_json() == {
            "error": {"code": "internal_error", "message": "Erro interno no servidor."}
        }

    def test_integrity_error_without_existing_lead_returns_standard_500(self, app, monkeypatch):
        """IntegrityError no commit sem lead existente correspondente vira 500 padronizado."""
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.orm import Session

        def commit_integrity_error(self):
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))

        monkeypatch.setattr(Session, "commit", commit_integrity_error)

        client = app.test_client()
        response = client.post("/api/leads/cliente", json={
            "tipo": "cliente",
            "nome": "Teste",
            "whatsapp": "(11) 99999-9999",
            "email": "integrity@exemplo.com",
            "bairro": "Centro",
            "cesta": "frutas",
        })

        assert response.status_code == 500
        assert response.get_json() == {
            "error": {"code": "internal_error", "message": "Erro interno no servidor."}
        }

    def test_convert_commit_exception_returns_standard_500(self, app, monkeypatch):
        """db.session.commit quebrado em POST /api/leads/<id>/convert vira 500 padronizado."""
        from sqlalchemy.orm import Session

        client = app.test_client()

        # Cria um lead válido (commit não está mockado ainda)
        resp = client.post("/api/leads/cliente", json={
            "tipo": "cliente",
            "nome": "João",
            "whatsapp": "(19) 99999-0000",
            "email": "joao@exemplo.com",
            "bairro": "Centro",
            "cesta": "frutas",
        })
        assert resp.status_code == 201
        lead_id = resp.get_json()["lead"]["id"]

        # Cria admin e obtém token
        with app.app_context():
            from app.models import AdminUser
            admin = AdminUser(email="admin@exemplo.com", name="Admin")
            admin.set_password("senha123")
            db.session.add(admin)
            db.session.commit()

        resp = client.post("/api/v1/admin/auth/login", json={
            "email": "admin@exemplo.com",
            "password": "senha123",
        })
        assert resp.status_code == 200
        token = resp.get_json()["access_token"]

        # Mocka commit para levantar Exception
        def commit_quebrado(self):
            raise Exception("boom")

        monkeypatch.setattr(Session, "commit", commit_quebrado)

        resp = client.post(
            f"/api/leads/{lead_id}/convert",
            json={"password": "senha123"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 500
        assert resp.get_json() == {
            "error": {"code": "internal_error", "message": "Erro interno no servidor."}
        }
