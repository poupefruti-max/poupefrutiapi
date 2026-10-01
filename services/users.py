"""Serviços de banco de dados para usuários."""

import re
import logging
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from psycopg.errors import UniqueViolation

from db import get_cursor

ph = PasswordHasher()

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
PHONE_RE = re.compile(r"^\+?\d{10,15}$")


class ValidationError(ValueError):
    """Erro de validação de campos."""
    pass


def normalize_phone(phone: str | None) -> str:
    """Remove espaços, parênteses, traços e normaliza o telefone."""
    if not phone:
        return ""
    return re.sub(r"[\s\(\)-]", "", phone)


def validate_user_data(username: str, email: str, phone: str, password: str, neighborhood: str) -> None:
    """Valida os dados do usuário antes de inserir."""
    if not username or not USERNAME_RE.match(username):
        raise ValidationError("username inválido: 3-30 caracteres, apenas letras, números, _ e .")
    if not email or not EMAIL_RE.match(email):
        raise ValidationError("email inválido")
    if not password or len(password) < 8:
        raise ValidationError("senha deve ter no mínimo 8 caracteres")
    if not neighborhood or not neighborhood.strip():
        raise ValidationError("bairro é obrigatório")
    phone_normalized = normalize_phone(phone)
    if not phone_normalized or not PHONE_RE.match(phone_normalized):
        raise ValidationError("phone inválido: apenas dígitos, 10-15 caracteres, opcional +")


def create_user(username: str, email: str, phone: str, password: str, neighborhood: str) -> dict:
    """Cria um novo usuário no banco."""
    validate_user_data(username, email, phone, password, neighborhood)

    password_hash = ph.hash(password)
    phone_normalized = normalize_phone(phone)
    neighborhood_clean = neighborhood.strip()

    with get_cursor() as cur:
        try:
            cur.execute(
                """
                INSERT INTO users (username, email, phone, password_hash, neighborhood)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, username, email, phone, neighborhood, created_at
                """,
                (username, email.lower(), phone_normalized, password_hash, neighborhood_clean),
            )
            row = cur.fetchone()
            return {
                "id": row[0],
                "username": row[1],
                "email": row[2],
                "phone": row[3],
                "neighborhood": row[4],
                "created_at": row[5].isoformat() if row[5] else None,
            }
        except UniqueViolation as e:
            constraint = str(e).split("Key (")[-1].split(")")[0] if "Key (" in str(e) else ""
            field_map = {
                "username": "username",
                "email": "email",
                "phone": "phone",
            }
            field = field_map.get(constraint, "campo")
            logging.warning("Violação de UNIQUE: %s", constraint)
            raise ValidationError(f"{field} já cadastrado")


def get_user_by_id(user_id: int) -> dict | None:
    """Busca usuário por ID (dados públicos)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, username, email, phone, neighborhood, created_at
            FROM users WHERE id = %s
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "username": row[1],
            "email": row[2],
            "phone": row[3],
            "neighborhood": row[4],
            "created_at": row[5].isoformat() if row[5] else None,
        }


def get_user_by_email(email: str) -> dict | None:
    """Busca usuário por email (inclui password_hash para login)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, username, email, phone, neighborhood, password_hash, created_at
            FROM users WHERE email = %s
            """,
            (email.lower(),),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "username": row[1],
            "email": row[2],
            "phone": row[3],
            "neighborhood": row[4],
            "password_hash": row[5],
            "created_at": row[6].isoformat() if row[6] else None,
        }


def verify_password(password_hash: str, password: str) -> bool:
    """Verifica a senha usando argon2."""
    try:
        ph.verify(password_hash, password)
        return True
    except VerifyMismatchError:
        return False
    except Exception as e:
        logging.exception("Erro ao verificar senha: %s", e)
        return False