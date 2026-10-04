"""Serviço de convites de acesso a estabelecimentos.

Decisões importantes deste módulo:

* O token em claro é gerado com `secrets.token_urlsafe(32)` e devolvido **apenas**
  no retorno da criação. No banco fica só o SHA-256.
* O link usa **fragmento** (`#token=...`): a parte depois do `#` não vai para o
  servidor nem para o cabefer Referer.
* O aceite é atômico: um único `UPDATE ... WHERE accepted_at IS NULL ...` com
  verificação de `rowcount`. Só quem marcar a linha cria a conta, o que faz dois
  aceites simultâneos resultarem em uma única conta.

O envio de e-mail fica fora de propósito: este módulo só devolve o link, para que
integrar o Resend depois seja uma troca de uma função.
"""

import hashlib
import logging
import secrets
import unicodedata
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.extensions import db
from app.models import Establishment, EstablishmentMember, Invitation, MemberRole, User
from app.rbac import normalizar_role

logger = logging.getLogger(__name__)

#: Validade padrão de um convite.
DIAS_VALIDADE = 7

#: Caminho da página pública de convite na aplicação web.
CAMINHO_CONVITE = "/convite"


class ErroConvite(Exception):
    """Erro de negócio do fluxo de convites."""

    def __init__(self, codigo: str, mensagem: str, status: int = 400):
        self.codigo = codigo
        self.mensagem = mensagem
        self.status = status
        super().__init__(mensagem)


# ---------------------------------------------------------------------------
# Slug
# ---------------------------------------------------------------------------

def _sem_acento(valor: str) -> str:
    """Remove acentos: 'Padaria São José' -> 'Padaria Sao Jose'."""
    normalizado = unicodedata.normalize("NFKD", valor)
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def slugify(nome: str) -> str:
    """Converte um nome em slug: minúsculas, sem acentos, hífens.

    >>> slugify("Mercado São José")
    'mercado-sao-jose'
    """
    base = _sem_acento(nome or "").lower()
    allowed = [c if (c.isalnum()) else "-" for c in base]
    slug = "".join(allowed)
    slug = "-".join(part for part in slug.split("-") if part)
    return slug[:160].strip("-")


def gerar_slug_unico(nome: str, sess: Optional[Session] = None, ignorar_id: Optional[int] = None) -> str:
    """Slug a partir do nome, com sufixo numérico em caso de colisão.

    "Mercado Central" -> "mercado-central"; se já existir, tenta
    "mercado-central-2", "mercado-central-3", ...
    """
    sess = sess or db.session

    base = slugify(nome) or "estabelecimento"

    def existe(candidato: str) -> bool:
        consulta = sess.query(Establishment.id).filter(Establishment.slug == candidato)
        if ignorar_id is not None:
            consulta = consulta.filter(Establishment.id != ignorar_id)
        return consulta.first() is not None

    if not existe(base):
        return base

    sufixo = 2
    while True:
        candidato = f"{base}-{sufixo}"
        if len(candidato) > 160:
            candidato = candidato[:157]
        if not existe(candidato):
            return candidato
        sufixo += 1


# ---------------------------------------------------------------------------
# Token e link
# ---------------------------------------------------------------------------

def gerar_token() -> tuple[str, str]:
    """Devolve (token_em_claro, sha256_do_token)."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    """SHA-256 hexadecimal do token. É o que vai para o banco."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def montar_link(app_base_url: str, token: str) -> str:
    """Link de convite, com o token no fragmento (não vai para o servidor)."""
    base = (app_base_url or "").rstrip("/")
    return f"{base}{CAMINHO_CONVITE}#token={token}"


def _agora() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# Criação
# ---------------------------------------------------------------------------

def revogar_pendentes(
    sess: Session,
    establishment_id: int,
    role: str,
    agora: Optional[datetime] = None,
) -> int:
    """Revoga os convites pendentes do mesmo mercado e papel. Devolve quantos."""
    agora = agora or _agora()
    resultado = sess.execute(
        update(Invitation)
        .where(
            Invitation.establishment_id == establishment_id,
            Invitation.role == role,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .values(revoked_at=agora)
    )
    return resultado.rowcount or 0


def criar_convite(
    establishment_id: int,
    role: str = MemberRole.owner.value,
    criado_por_admin_id: Optional[int] = None,
    criado_por_user_id: Optional[int] = None,
    dias_validade: int = DIAS_VALIDADE,
    app_base_url: str = "",
    revogar_anteriores: bool = True,
    sess: Optional[Session] = None,
) -> dict:
    """Cria um convite e devolve o link.

    Faz `flush()`, não `commit`: quem chama controla a transação.
    """
    sess = sess or db.session

    papel = normalizar_role(role)
    if papel is None:
        raise ErroConvite("invalid_role", f"Papel inválido: {role}", 400)

    if criado_por_admin_id is None and criado_por_user_id is None:
        raise ErroConvite(
            "missing_creator", "Informe quem criou o convite (admin ou usuário).", 400
        )
    if criado_por_admin_id is not None and criado_por_user_id is not None:
        raise ErroConvite(
            "invalid_creator", "Informe apenas um criador para o convite.", 400
        )

    if sess.get(Establishment, establishment_id) is None:
        raise ErroConvite("establishment_not_found", "Estabelecimento não encontrado.", 404)

    if revogar_anteriores:
        revogados = revogar_pendentes(sess, establishment_id, papel)
        if revogados:
            logger.info(
                "Convites revogados: est=%s role=%s total=%s", establishment_id, papel, revogados
            )

    token, token_hash = gerar_token()
    expira = _agora() + timedelta(days=dias_validade)

    convite = Invitation(
        establishment_id=establishment_id,
        role=papel,
        token_hash=token_hash,
        created_by_admin_id=criado_por_admin_id,
        created_by_user_id=criado_por_user_id,
        expires_at=expira,
    )
    sess.add(convite)

    try:
        sess.flush()
    except IntegrityError as erro:
        sess.rollback()
        raise ErroConvite("invite_creation_failed", "Não foi possível criar o convite.", 409) from erro

    return {
        "invitation": convite,
        "token": token,
        "invite_url": montar_link(app_base_url, token),
        "expires_at": expira,
        "role": papel,
    }


# ---------------------------------------------------------------------------
# Consulta e aceite
# ---------------------------------------------------------------------------

def buscar_por_token(token: str, sess: Optional[Session] = None) -> Optional[Invitation]:
    """Convite pelo token em claro. Não diz se está válido — só encontra."""
    sess = sess or db.session
    if not token or not isinstance(token, str):
        return None
    return sess.query(Invitation).filter(Invitation.token_hash == hash_token(token)).first()


def validar_convite(token: str, sess: Optional[Session] = None) -> dict:
    """Dados públicos do convite, ou erro genérico.

    Token inválido, expirado, usado e revogado devolvem **o mesmo** erro, para
    não revelar se um token existiu.
    """
    sess = sess or db.session

    generico = ErroConvite(
        "invalid_invitation",
        "Convite inválido ou expirado. Peça um novo link ao responsável.",
        400,
    )

    convite = buscar_por_token(token, sess)
    if convite is None or not convite.esta_pendente():
        raise generico

    return {
        "invitation_id": convite.id,
        "establishment_id": convite.establishment_id,
        "establishment_name": convite.establishment.name,
        "role": convite.role.value if convite.role else None,
        "expires_at": convite.expires_at.isoformat() if convite.expires_at else None,
    }


def aceitar_convite(
    token: str,
    nome: str,
    email: str,
    senha: str,
    sess: Optional[Session] = None,
) -> dict:
    """Aceita o convite: marca o convite e cria User + vínculo.

    O passo decisivo é o UPDATE condicional. Só a transação que conseguir
    `rowcount == 1` segue criando a conta, o que garante uma conta única mesmo
    com dois aceites simultâneos.
    """
    sess = sess or db.session

    generico = ErroConvite(
        "invalid_invitation",
        "Convite inválido ou expirado. Peça um novo link ao responsável.",
        400,
    )

    convite = buscar_por_token(token, sess)
    if convite is None or not convite.esta_pendente():
        raise generico

    papel = convite.role.value if convite.role else MemberRole.owner.value

    # Passo 1: reserva o convite de forma atômica.
    resultado = sess.execute(
        update(Invitation)
        .where(
            Invitation.id == convite.id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > _agora(),
        )
        .values(accepted_at=_agora())
    )

    if (resultado.rowcount or 0) != 1:
        sess.rollback()
        raise generico

    # Passo 2: quem reservei o convite cria a conta.
    nome = (nome or "").strip()
    email = (email or "").strip().lower()

    existente = sess.query(User).filter(User.email == email).first()
    if existente is not None:
        sess.rollback()
        raise ErroConvite(
            "email_already_exists",
            "Este e-mail já está cadastrado. Entre com a conta existente ou use outro e-mail.",
            409,
        )

    usuario = User(email=email, name=nome or None)
    usuario.set_password(senha)
    sess.add(usuario)
    sess.flush()

    sess.execute(
        update(Invitation)
        .where(Invitation.id == convite.id)
        .values(accepted_user_id=usuario.id)
    )

    vinculo = EstablishmentMember(
        user_id=usuario.id,
        establishment_id=convite.establishment_id,
        role=papel,
    )
    sess.add(vinculo)

    sess.flush()

    return {
        "user": usuario,
        "membership": vinculo,
        "establishment": sess.get(Establishment, convite.establishment_id),
        "role": papel,
    }


def texto_sugerido_whatsapp(
    estabelecimento_nome: str,
    link_convite: str,
    nome_contato: str = "",
) -> str:
    """Mensagem pronta para o admin copiar e mandar pelo WhatsApp."""
    saudacao = f", {nome_contato}" if nome_contato else ""
    return (
        f"Olá{saudacao}! 👋\n\n"
        f"Seu {estabelecimento_nome} foi aprovado no PoupeFruti.\n\n"
        f"Use este link para criar sua conta e começar a usar o painel:\n"
        f"{link_convite}\n\n"
        f"O link é pessoal e válido por {DIAS_VALIDADE} dias."
    )
