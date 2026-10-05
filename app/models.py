"""Modelos do banco de dados."""

import bcrypt
import enum
import json
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    String,
    Text,
    TypeDecorator,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB

from .extensions import db
from .rbac import BasketStatus, BasketType, EstablishmentStatus, MemberRole
from .timeutils import como_utc, agora_utc


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
    memberships = db.relationship(
        "EstablishmentMember",
        back_populates="user",
        foreign_keys="EstablishmentMember.user_id",
    )

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

    def to_dict_com_estabelecimentos(self) -> dict:
        """Usuário + os estabelecimentos onde tem vínculo, com o papel de cada um."""
        dados = self.to_dict()
        dados["establishments"] = [
            {
                "establishment_id": vinculo.establishment_id,
                "role": vinculo.role.value if vinculo.role else None,
            }
            for vinculo in self.memberships
        ]
        return dados

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class AdminUser(db.Model):
    __tablename__ = "admin_users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(CITEXTType(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(120), nullable=True)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())

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
            "email": self.email,
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<AdminUser {self.email}>"


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
            # Preenchido quando o lead vira pessoa ou mercado.
            "user_id": self.user.id if self.user else None,
            "establishment_id": self.establishment.id if self.establishment else None,
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
    # O CNPJ passa a ser opcional: o dono do mercado informa depois do onboarding.
    cnpj = db.Column(db.String(18), unique=True, nullable=True, index=True)
    address = db.Column(db.Text, nullable=True)
    # Gerado na aprovação, a partir do nome. Único quando presente.
    slug = db.Column(db.String(160), unique=True, nullable=True, index=True)
    status = db.Column(
        Enum(EstablishmentStatus, native_enum=False),
        nullable=False,
        default=EstablishmentStatus.pending,
        server_default=text("'pending'"),
        index=True,
    )
    phone = db.Column(db.String(20), nullable=True)
    # `description` saiu do perfil (virou área de CESTAS, tabela `baskets`).
    # A coluna continua no banco de propósito: nada é apagado aqui, e a
    # remoção física fica para uma etapa posterior com backup.
    description = db.Column(db.Text, nullable=True)
    opening_hours = db.Column(db.Text, nullable=True)
    is_published = db.Column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    lead = db.relationship("Lead", back_populates="establishment", foreign_keys="Establishment.lead_id")
    members = db.relationship("EstablishmentMember", back_populates="establishment", cascade="all, delete-orphan")
    baskets = db.relationship(
        "Basket", back_populates="establishment", cascade="all, delete-orphan"
    )
    invitations = db.relationship(
        "Invitation",
        back_populates="establishment",
        cascade="all, delete-orphan",
        foreign_keys="Invitation.establishment_id",
    )

    def to_dict(self) -> dict:
        """Perfil visto pelo painel do mercado (cnpj incluso: é do dono)."""
        return {
            "id": self.id,
            "lead_id": self.lead_id,
            "name": self.name,
            "cnpj": self.cnpj,
            "address": self.address,
            "slug": self.slug,
            "status": self.status.value if self.status else None,
            "phone": self.phone,
            "opening_hours": self.opening_hours,
            "is_published": self.is_published,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<Establishment {self.name} {self.slug}>"


class Invitation(db.Model):
    """Convite de acesso a um estabelecimento.

    Guarda apenas o SHA-256 do token: o valor em claro existe somente no
    retorno da criação, nunca no banco.
    """

    __tablename__ = "invitations"

    id = db.Column(db.Integer, primary_key=True)
    establishment_id = db.Column(
        db.Integer,
        db.ForeignKey("establishments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = db.Column(
        Enum(MemberRole, native_enum=False),
        nullable=False,
        default=MemberRole.owner,
        server_default=text("'owner'"),
    )
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)

    # Exatamente um dos dois é preenchido: ou um admin gerou, ou um membro.
    created_by_admin_id = db.Column(
        db.Integer, db.ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    expires_at = db.Column(DateTime(timezone=True), nullable=False, index=True)
    accepted_at = db.Column(DateTime(timezone=True), nullable=True)
    accepted_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at = db.Column(DateTime(timezone=True), nullable=True)
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    establishment = db.relationship(
        "Establishment", back_populates="invitations", foreign_keys=[establishment_id]
    )
    created_by_admin = db.relationship("AdminUser", foreign_keys=[created_by_admin_id])
    accepted_user = db.relationship("User", foreign_keys=[accepted_user_id])

    __table_args__ = (
        # Exatamente um criador: forma portátil (num_nonnulls() é só do PostgreSQL).
        CheckConstraint(
            "(created_by_admin_id IS NOT NULL AND created_by_user_id IS NULL) "
            "OR (created_by_admin_id IS NULL AND created_by_user_id IS NOT NULL)",
            name="ck_invitations_one_creator",
        ),
    )

    def esta_pendente(self, agora=None) -> bool:
        """
        True quando o convite ainda pode ser usado.

        Normaliza os dois lados: `expires_at` vem com fuso do PostgreSQL e sem
        fuso do SQLite, e `agora` precisa ser comparável aos dois.
        """
        if self.accepted_at is not None or self.revoked_at is not None:
            return False
        return como_utc(self.expires_at) > como_utc(agora or agora_utc())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "establishment_id": self.establishment_id,
            "role": self.role.value if self.role else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "accepted_at": self.accepted_at.isoformat() if self.accepted_at else None,
            "accepted_user_id": self.accepted_user_id,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<Invitation est={self.establishment_id} role={self.role}>"


class EstablishmentMember(db.Model):
    __tablename__ = "establishment_members"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    establishment_id = db.Column(db.Integer, db.ForeignKey("establishments.id", ondelete="CASCADE"), primary_key=True)
    role = db.Column(
        Enum(MemberRole, native_enum=False),
        nullable=False,
        default=MemberRole.owner,
        server_default=text("'owner'"),
    )
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user = db.relationship("User", back_populates="memberships", foreign_keys=[user_id])
    establishment = db.relationship("Establishment", back_populates="members", foreign_keys=[establishment_id])

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "establishment_id": self.establishment_id,
            "role": self.role.value if self.role else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<EstablishmentMember user={self.user_id} est={self.establishment_id} role={self.role}>"


class Basket(db.Model):
    """
    Cesta da sobra do dia (frutas ou legumes) com desconto, para retirada.

    Ainda não há pedidos nem pagamento: a cesta é só a oferta e o estoque.
    Por isso não existe DELETE — encerrar é `status = closed`, que é o que
    preservará o histórico quando os pedidos passarem a referenciar a cesta.
    """

    __tablename__ = "baskets"

    id = db.Column(db.Integer, primary_key=True)
    establishment_id = db.Column(
        db.Integer,
        db.ForeignKey("establishments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type = db.Column(Enum(BasketType, native_enum=False), nullable=False)
    name = db.Column(String(80), nullable=True)
    weight_grams = db.Column(db.Integer, nullable=False)
    price_cents = db.Column(db.Integer, nullable=False)
    retail_value_cents = db.Column(db.Integer, nullable=True)
    quantity = db.Column(db.Integer, nullable=False, server_default=text("0"))
    pickup_starts_at = db.Column(DateTime(timezone=True), nullable=False)
    pickup_ends_at = db.Column(DateTime(timezone=True), nullable=False, index=True)
    notes = db.Column(String(280), nullable=True)
    status = db.Column(
        Enum(BasketStatus, native_enum=False),
        nullable=False,
        default=BasketStatus.active,
        server_default=text("'active'"),
    )
    created_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = db.Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = db.Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    establishment = db.relationship("Establishment", back_populates="baskets")
    created_by_user = db.relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        CheckConstraint("price_cents > 0", name="ck_baskets_price_positive"),
        CheckConstraint("quantity >= 0", name="ck_baskets_quantity_non_negative"),
        CheckConstraint("weight_grams > 0", name="ck_baskets_weight_positive"),
        CheckConstraint(
            "pickup_ends_at > pickup_starts_at",
            name="ck_baskets_pickup_window",
        ),
        # Leitura do painel: mercado + status.
        Index("ix_baskets_establishment_status", "establishment_id", "status"),
    )

    @property
    def discount_percent(self):
        """
        Desconto em % sobre o preço de vitrine.

        Devolve None quando não há valor de referência, ou quando ele não é
        maior que o preço — a validação impede gravar assim, mas a property
        não deve explodir ao ler uma linha.
        """
        if not self.retail_value_cents or self.retail_value_cents <= 0:
            return None
        if self.retail_value_cents <= self.price_cents:
            return None
        return round(
            (self.retail_value_cents - self.price_cents) * 100.0
            / self.retail_value_cents,
            1,
        )

    def to_dict(self) -> dict:
        """Cesta como o painel do mercado vê (inclui ids internos)."""
        return {
            "id": self.id,
            "establishment_id": self.establishment_id,
            "type": self.type.value if self.type else None,
            "name": self.name,
            "weight_grams": self.weight_grams,
            "price_cents": self.price_cents,
            "retail_value_cents": self.retail_value_cents,
            "discount_percent": self.discount_percent,
            "quantity": self.quantity,
            "pickup_starts_at": como_utc(self.pickup_starts_at).isoformat()
            if self.pickup_starts_at
            else None,
            "pickup_ends_at": como_utc(self.pickup_ends_at).isoformat()
            if self.pickup_ends_at
            else None,
            "notes": self.notes,
            "status": self.status.value if self.status else None,
            "created_by_user_id": self.created_by_user_id,
            "created_at": como_utc(self.created_at).isoformat()
            if self.created_at
            else None,
            "updated_at": como_utc(self.updated_at).isoformat()
            if self.updated_at
            else None,
        }

    def to_dict_publico(self) -> dict:
        """
        Cesta como o visitante vê na página pública.

        NÃO inclui: id, establishment_id, created_by_user_id, status interno.
        Só o que alguém que quer retirar precisa saber.
        """
        return {
            "type": self.type.value if self.type else None,
            "name": self.name,
            "weight_grams": self.weight_grams,
            "price_cents": self.price_cents,
            "retail_value_cents": self.retail_value_cents,
            "discount_percent": self.discount_percent,
            "quantity": self.quantity,
            "pickup_starts_at": como_utc(self.pickup_starts_at).isoformat()
            if self.pickup_starts_at
            else None,
            "pickup_ends_at": como_utc(self.pickup_ends_at).isoformat()
            if self.pickup_ends_at
            else None,
            "notes": self.notes,
        }

    def __repr__(self) -> str:
        return f"<Basket est={self.establishment_id} type={self.type} qty={self.quantity}>"
