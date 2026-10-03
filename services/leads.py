"""Serviços para gerenciamento de leads."""

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Lead, LeadType, LeadStatus, User, Establishment, EstablishmentMember
from app.extensions import db

logger = logging.getLogger(__name__)


class LeadError(Exception):
    """Erro base para operações de lead."""
    pass


class LeadAlreadyConvertedError(LeadError):
    """Lead já foi convertido."""
    def __init__(self, lead_id: int, existing_user_id: Optional[int] = None, existing_establishment_id: Optional[int] = None):
        self.lead_id = lead_id
        self.existing_user_id = existing_user_id
        self.existing_establishment_id = existing_establishment_id
        super().__init__(f"Lead {lead_id} já convertido")


class DuplicateLeadError(LeadError):
    """Lead duplicado (email único)."""
    pass


def normalize_email(email: Optional[str]) -> Optional[str]:
    """Normaliza email para minúsculas e sem espaços."""
    if email is None:
        return None
    return email.strip().lower()


def normalize_phone(phone: str) -> str:
    """Normaliza telefone removendo formatação."""
    import re
    return re.sub(r"[\s\(\)-]", "", phone or "")


def create_lead(
    type: LeadType,
    name: str,
    phone: str,
    source: str = "landing-poupefruti",
    email: Optional[str] = None,
    extra: Optional[dict] = None,
    session: Optional[Session] = None,
) -> Lead:
    """
    Cria um novo lead.

    Se já existir um lead com o mesmo email (não nulo) e tipo, atualiza
    phone/extra/source e retorna o existente (upsert amigável).
    """
    sess = session or db.session
    email_norm = normalize_email(email)
    phone_norm = normalize_phone(phone)

    # Verifica duplicidade por email + tipo (se email fornecido)
    if email_norm:
        existing = sess.query(Lead).filter(
            Lead.email == email_norm,
            Lead.type == type
        ).first()
        if existing:
            # Upsert: atualiza campos mutáveis do lead existente
            existing.phone = phone_norm
            existing.source = source
            if extra:
                # Merge extra: novo dict para disparar flag_modified
                merged = {**existing.extra, **extra}
                existing.extra = merged
            logger.info("Upsert lead email=%s type=%s id=%s", email_norm, type.value, existing.id)
            return existing

    lead = Lead(
        type=type,
        name=name.strip(),
        email=email_norm,
        phone=phone_norm,
        source=source,
        status=LeadStatus.new,
        extra=extra or {},
    )

    sess.add(lead)
    try:
        sess.flush()
    except IntegrityError as e:
        sess.rollback()
        # Race condition: outro processo criou o mesmo email entre check e insert
        if email_norm and "ix_leads_email_unique" in str(e):
            existing = sess.query(Lead).filter(
                Lead.email == email_norm,
                Lead.type == type
            ).first()
            if existing:
                existing.phone = phone_norm
                existing.source = source
                if extra:
                    merged = {**existing.extra, **extra}
                    existing.extra = merged
                logger.info("Race condition upsert email=%s type=%s id=%s", email_norm, type.value, existing.id)
                return existing
        raise DuplicateLeadError(f"Erro ao criar lead: {e}") from e

    return lead


def update_lead_status(
    lead_id: int,
    status: LeadStatus,
    session: Optional[Session] = None,
) -> Lead:
    """Atualiza o status do lead."""
    sess = session or db.session
    lead = sess.get(Lead, lead_id)
    if not lead:
        raise LeadError(f"Lead {lead_id} não encontrado")

    # Valida transições permitidas
    valid_transitions = {
        LeadStatus.new: [LeadStatus.contacted, LeadStatus.qualified, LeadStatus.lost, LeadStatus.converted],
        LeadStatus.contacted: [LeadStatus.qualified, LeadStatus.lost, LeadStatus.converted],
        LeadStatus.qualified: [LeadStatus.converted, LeadStatus.lost],
        LeadStatus.converted: [],  # Não permite sair de converted
        LeadStatus.lost: [LeadStatus.new],  # Permite reativar
    }

    if status not in valid_transitions.get(lead.status, []):
        raise LeadError(f"Transição inválida de {lead.status.value} para {status.value}")

    lead.status = status
    if status == LeadStatus.converted:
        lead.converted_at = datetime.utcnow()

    sess.flush()
    return lead


def convert_lead(
    lead_id: int,
    password: Optional[str] = None,
    establishment_data: Optional[dict] = None,
    session: Optional[Session] = None,
) -> dict:
    """
    Converte um lead em usuário e/ou estabelecimento.
    
    Operação atômica em uma única transação:
    - Se lead.type == person: cria User, vincula lead_id, atualiza lead para converted
    - Se lead.type == establishment: cria Establishment, cria User (owner), cria EstablishmentMember, vincula FKs, atualiza lead
    
    Idempotente: se lead já convertido, retorna registros existentes sem duplicar.
    """
    sess = session or db.session
    
    lead = sess.get(Lead, lead_id)
    if not lead:
        raise LeadError(f"Lead {lead_id} não encontrado")

    # Idempotência: se já convertido, retorna existentes
    if lead.status == LeadStatus.converted:
        result = {"lead": lead}
        if lead.user:
            result["user"] = lead.user
        if lead.establishment:
            result["establishment"] = lead.establishment
        logger.info("Lead %s já convertido, retornando existentes", lead_id)
        return result

    if lead.type == LeadType.person:
        return _convert_person(lead, password, sess)
    elif lead.type == LeadType.establishment:
        return _convert_establishment(lead, password, establishment_data, sess)
    else:
        raise LeadError(f"Tipo de lead desconhecido: {lead.type}")


def _convert_person(lead: Lead, password: Optional[str], sess: Session) -> dict:
    """Converte lead do tipo person em User."""
    if not lead.email:
        raise LeadError("Lead person precisa de email para conversão")
    if not password:
        raise LeadError("Senha é obrigatória para criar usuário")

    # Verifica se já existe user com esse email (race condition)
    user = sess.query(User).filter(User.email == lead.email).first()
    if user:
        # Vincula o lead ao user existente se não tiver lead_id
        if user.lead_id is None:
            user.lead_id = lead.id
        lead.status = LeadStatus.converted
        lead.converted_at = datetime.utcnow()
        sess.flush()
        return {"lead": lead, "user": user}

    user = User(
        email=lead.email,
        name=lead.name,
        lead_id=lead.id,
    )
    user.set_password(password)

    sess.add(user)
    sess.flush()

    lead.status = LeadStatus.converted
    lead.converted_at = datetime.utcnow()

    return {"lead": lead, "user": user}


def _convert_establishment(
    lead: Lead,
    password: Optional[str],
    establishment_data: Optional[dict],
    sess: Session,
) -> dict:
    """Converte lead do tipo establishment em Establishment + User + Member."""
    if not lead.email:
        raise LeadError("Lead establishment precisa de email para conversão")
    if not password:
        raise LeadError("Senha é obrigatória para criar usuário owner")
    if not establishment_data or not establishment_data.get("cnpj"):
        raise LeadError("Dados do estabelecimento (cnpj) são obrigatórios")

    cnpj = establishment_data["cnpj"].strip()
    est_name = establishment_data.get("name") or lead.name
    address = establishment_data.get("address")

    # Verifica se establishment com esse CNPJ já existe
    establishment = sess.query(Establishment).filter(Establishment.cnpj == cnpj).first()
    if establishment:
        # Se já existe, verifica se já tem lead vinculado
        if establishment.lead_id is None:
            establishment.lead_id = lead.id
        
        # Busca ou cria o user owner
        user = sess.query(User).filter(User.email == lead.email).first()
        if not user:
            user = User(email=lead.email, name=lead.name, lead_id=lead.id)
            user.set_password(password)
            sess.add(user)
            sess.flush()
        elif user.lead_id is None:
            user.lead_id = lead.id
        
        # Verifica se já é membro
        member = sess.query(EstablishmentMember).filter(
            EstablishmentMember.user_id == user.id,
            EstablishmentMember.establishment_id == establishment.id
        ).first()
        if not member:
            member = EstablishmentMember(user_id=user.id, establishment_id=establishment.id, role="owner")
            sess.add(member)

        lead.status = LeadStatus.converted
        lead.converted_at = datetime.utcnow()
        sess.flush()
        return {"lead": lead, "user": user, "establishment": establishment}

    # Cria establishment
    establishment = Establishment(
        name=est_name,
        cnpj=cnpj,
        address=address,
        lead_id=lead.id,
    )
    sess.add(establishment)
    sess.flush()

    # Cria user owner
    user = sess.query(User).filter(User.email == lead.email).first()
    if not user:
        user = User(email=lead.email, name=lead.name, lead_id=lead.id)
        user.set_password(password)
        sess.add(user)
        sess.flush()
    elif user.lead_id is None:
        user.lead_id = lead.id

    # Cria vínculo member
    member = EstablishmentMember(user_id=user.id, establishment_id=establishment.id, role="owner")
    sess.add(member)

    lead.status = LeadStatus.converted
    lead.converted_at = datetime.utcnow()

    return {"lead": lead, "user": user, "establishment": establishment}