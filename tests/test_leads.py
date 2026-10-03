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

    def test_convert_establishment_creates_establishment_user_member(self, session):
        """Conversão de establishment cria Establishment + User + Member."""
        lead = create_lead(
            type=LeadType.establishment,
            name="Mercado Central",
            phone="(19) 98888-0000",
            email="contato@mercado.com",
        )

        result = convert_lead(
            lead.id,
            password="senha123",
            establishment_data={"cnpj": "12.345.678/0001-90", "name": "Mercado Central", "address": "Rua A, 123"},
        )

        assert "user" in result
        assert "establishment" in result

        user = result["user"]
        est = result["establishment"]

        assert user.email == "contato@mercado.com"
        assert user.lead_id == lead.id
        assert user.check_password("senha123")

        assert est.name == "Mercado Central"
        assert est.cnpj == "12.345.678/0001-90"
        assert est.address == "Rua A, 123"
        assert est.lead_id == lead.id

        # Verifica member
        member = session.query(EstablishmentMember).filter_by(
            user_id=user.id, establishment_id=est.id
        ).first()
        assert member is not None
        assert member.role == "owner"

        assert result["lead"].status == LeadStatus.converted

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

    def test_convert_idempotent_establishment(self, session):
        """Segunda conversão do mesmo lead establishment retorna registros existentes."""
        lead = create_lead(
            type=LeadType.establishment,
            name="Mercado",
            phone="(19) 98888-0000",
            email="contato@mercado.com",
        )

        result1 = convert_lead(
            lead.id,
            password="senha123",
            establishment_data={"cnpj": "12.345.678/0001-90"},
        )
        result2 = convert_lead(
            lead.id,
            password="outrasenha",
            establishment_data={"cnpj": "12.345.678/0001-90", "name": "Outro Nome"},
        )

        assert result1["user"].id == result2["user"].id
        assert result1["establishment"].id == result2["establishment"].id
        assert result2["establishment"].name == "Mercado"  # Nome original mantido

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

    def test_convert_establishment_without_cnpj_raises(self, session):
        """Conversão de establishment sem CNPJ falha."""
        lead = create_lead(
            type=LeadType.establishment,
            name="Mercado",
            phone="(19) 98888-0000",
            email="contato@mercado.com",
        )
        with pytest.raises(LeadError, match="cnpj.*obrigatório"):
            convert_lead(lead.id, password="senha123", establishment_data={})

    def test_convert_duplicate_cnpj_reuses_existing(self, session):
        """CNPJ duplicado na conversão de establishment reusa o estabelecimento existente."""
        # Cria primeiro establishment
        lead1 = create_lead(
            type=LeadType.establishment,
            name="Mercado 1",
            phone="(19) 98888-0000",
            email="contato1@mercado.com",
        )
        result1 = convert_lead(lead1.id, password="senha123", establishment_data={"cnpj": "12.345.678/0001-90"})

        # Tenta converter segundo lead com mesmo CNPJ
        lead2 = create_lead(
            type=LeadType.establishment,
            name="Mercado 2",
            phone="(19) 97777-0000",
            email="contato2@mercado.com",
        )
        result2 = convert_lead(lead2.id, password="senha123", establishment_data={"cnpj": "12.345.678/0001-90"})

        # Deve reusar o mesmo establishment
        assert result1["establishment"].id == result2["establishment"].id
        assert result1["establishment"].cnpj == "12.345.678/0001-90"
        # Leads diferentes mas mesmo establishment
        assert result1["lead"].id != result2["lead"].id
        # Usuários diferentes
        assert result1["user"].id != result2["user"].id
        # Lead2 deve estar convertido
        assert result2["lead"].status == LeadStatus.converted

    def test_convert_nonexistent_lead_raises(self, session):
        """Conversão de lead inexistente falha."""
        with pytest.raises(LeadError, match="não encontrado"):
            convert_lead(99999, password="senha123")


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
        assert data["lead"]["extra"]["responsavel"] == "Maria Silva"

    def test_convert_lead_route(self, app):
        """POST /api/leads/<id>/convert converte lead."""
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

        # Converte
        resp = client.post(f"/api/leads/{lead_id}/convert", json={
            "password": "senha123",
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["lead"]["status"] == "converted"
        assert "user" in data
        assert data["user"]["email"] == "joao@test.com"