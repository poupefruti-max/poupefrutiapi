"""Modelos do banco de dados."""

import bcrypt
import enum
import json
from datetime import datetime
from sqlalchemy import DateTime, Enum, Index, func, text, String, TypeDecorator, Text
from sqlalchemy.dialects.postgresql import CITEXT, JSONB

from .extensions import db


class CITEXTType(TypeDecorator):
    """Tipo que usa CITEXT no PostgreSQL e String (case-insensitive via collation) no SQLite."""
    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(CITEXT())
        return dialect.type_descriptor(String(255))


class JSONBType(TypeDecorator):
    """Tipo que usa JSONB no PostgreSQL e Text no SQLite."""
    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is not None:
            return json.dumps(value)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return json.loads(value)
        return value


class LeadType(enum.Enum):
    person = "person"
    establishment = "establishment"


class LeadStatus(enum.Enum):
    new = "new"
    contacted = "contacted"
    qualified = "qualified"
    converted = "converted"
    lost = "lost"


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, unique=True, index=True)
    email = db.Column(CITEXTType(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(120), nullable=True)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    lead = db.relationship("Lead", back_populates="user", foreign_keys=[lead_id])

    def set_password(self, raw_password: str) -> None:
        self.password_hash = bcrypt.hashpw(
            raw_password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")

    def check_password(self, raw_password: str) -> bool:
        if not self.password_hash:
            return False
        return bcrypt.checkpw(
            raw_password.encode("utf-8"), self.password_hash.encode("utf-8")
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "lead_id": self.lead_id,
            "email": self.email,
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class Lead(db.Model):
    __tablename__ = "leads"

    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(Enum(LeadType, native_enum=False), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    email = db.Column(CITEXTType(255), nullable=True)
    phone = db.Column(db.String(20), nullable=False, index=True)
    source = db.Column(db.String(60), nullable=True)
    status = db.Column(Enum(LeadStatus, native_enum=False), nullable=False, default=LeadStatus.new, server_default=text("'new'"), index=True)
    extra = db.Column(JSONBType, nullable=False, default=dict, server_default=text("'{}'"))
    converted_at = db.Column(DateTime(timezone=True), nullable=True)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    bairro = db.Column(db.Text, nullable=True)
    cesta_tipo = db.Column(db.Text, nullable=True)

    user = db.relationship("User", back_populates="lead", foreign_keys="User.lead_id", uselist=False)
    establishment = db.relationship("Establishment", back_populates="lead", foreign_keys="Establishment.lead_id", uselist=False)

    def to_dict(self) -> dict:
        # Compatibilidade: extra continua com bairro/cesta se existirem (dados antigos)
        extra_compat = dict(self.extra)
        if self.bairro is not None:
            extra_compat["bairro"] = self.bairro
        if self.cesta_tipo is not None:
            extra_compat["cesta"] = self.cesta_tipo

        return {
            "id": self.id,
            "type": self.type.value if self.type else None,
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "source": self.source,
            "status": self.status.value if self.status else None,
            "extra": extra_compat,
            "bairro": self.bairro,
            "cesta_tipo": self.cesta_tipo,
            "converted_at": self.converted_at.isoformat() if self.converted_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<Lead {self.type} {self.phone}>"


class Establishment(db.Model):
    __tablename__ = "establishments"

    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, unique=True, index=True)
    name = db.Column(db.String(160), nullable=False)
    cnpj = db.Column(db.String(18), unique=True, nullable=False, index=True)
    address = db.Column(db.Text, nullable=True)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    lead = db.relationship("Lead", back_populates="establishment", foreign_keys="Establishment.lead_id")
    members = db.relationship("EstablishmentMember", back_populates="establishment", cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "lead_id": self.lead_id,
            "name": self.name,
            "cnpj": self.cnpj,
            "address": self.address,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<Establishment {self.name} {self.cnpj}>"


class EstablishmentMember(db.Model):
    __tablename__ = "establishment_members"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    establishment_id = db.Column(db.Integer, db.ForeignKey("establishments.id", ondelete="CASCADE"), primary_key=True)
    role = db.Column(db.String(30), nullable=False, default="owner", server_default=text("'owner'"))
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = db.relationship("User", foreign_keys=[user_id])
    establishment = db.relationship("Establishment", back_populates="members", foreign_keys=[establishment_id])

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "establishment_id": self.establishment_id,
            "role": self.role,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<EstablishmentMember user={self.user_id} est={self.establishment_id} role={self.role}>"