"""Modelos do banco de dados."""

import bcrypt
from sqlalchemy import DateTime, func

from .extensions import db


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # -- Senha ------------------------------------------------------------
    def set_password(self, raw_password: str) -> None:
        """Gera o hash bcrypt a partir da senha em texto puro."""
        self.password_hash = bcrypt.hashpw(
            raw_password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")

    def check_password(self, raw_password: str) -> bool:
        """Compara a senha informada com o hash armazenado."""
        if not self.password_hash:
            return False
        return bcrypt.checkpw(
            raw_password.encode("utf-8"), self.password_hash.encode("utf-8")
        )

    # -- Serialização -----------------------------------------------------
    def to_dict(self) -> dict:
        """Representação pública. Nunca inclui a senha nem o hash."""
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.email}>"


class Lead(db.Model):
    """
    Cadastro da lista de espera do PoupeFruti.

    Recebe exatamente o payload enviado pela landing page:
      comum a todos : tipo, nome, whatsapp, email, bairro, origem
      cliente      : cesta
      mercado      : responsavel, estabelecimento, tipoEstabelecimento
    """

    __tablename__ = "leads"

    id = db.Column(db.Integer, primary_key=True)

    # Campos comuns
    tipo = db.Column(db.String(20), nullable=False, index=True)
    nome = db.Column(db.String(160), nullable=False)
    whatsapp = db.Column(db.String(20), nullable=False, index=True)
    email = db.Column(db.String(255), nullable=True)
    bairro = db.Column(db.String(120), nullable=False, index=True)
    origem = db.Column(db.String(60), nullable=True)
    # Timestamp enviado pela UI (ISO 8601), apenas para rastreio.
    enviado_em = db.Column(db.String(40), nullable=True)

    # Exclusivos de cliente
    cesta = db.Column(db.String(20), nullable=True)

    # Exclusivos de mercado
    responsavel = db.Column(db.String(160), nullable=True)
    estabelecimento = db.Column(db.String(160), nullable=True)
    tipo_estabelecimento = db.Column(db.String(30), nullable=True)

    created_at = db.Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tipo": self.tipo,
            "nome": self.nome,
            "whatsapp": self.whatsapp,
            "email": self.email,
            "bairro": self.bairro,
            "origem": self.origem,
            "enviado_em": self.enviado_em,
            "cesta": self.cesta,
            "responsavel": self.responsavel,
            "estabelecimento": self.estabelecimento,
            "tipoEstabelecimento": self.tipo_estabelecimento,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Lead {self.tipo} {self.whatsapp}>"