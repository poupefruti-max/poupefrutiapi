"""Validações compartilhadas entre os blueprints."""

import re

# Validação de e-mail deliberadamente simples.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")

VALORES_CESTA = {"frutas", "legumes", "ambas"}

VALORES_TIPO_ESTABELECIMENTO = {"mercado", "padaria", "hortifruti", "sacolao"}


def normalizar_email(email: str | None) -> str:
    """Minúsculas e sem espaços nas pontas."""
    return (email or "").strip().lower()


def normalizar_texto(valor: str | None) -> str:
    """Remove espaços nas pontas. Devolve string vazia para None."""
    return (valor or "").strip()


def apenas_digitos(valor: str | None) -> str:
    return re.sub(r"\D", "", valor or "")


def whatsapp_valido(valor: str | None) -> bool:
    """
    Telefone brasileiro com DDD.

    Aceita 10 dígitos (fixo) ou 11 dígitos (celular, com o 9 na frente).
    O DDD precisa estar entre 11 e 99.
    """
    digitos = apenas_digitos(valor)
    if len(digitos) not in (10, 11):
        return False
    ddd = int(digitos[:2])
    if not 11 <= ddd <= 99:
        return False
    if len(digitos) == 11 and digitos[2] != "9":
        return False
    return True


def email_valido(valor: str | None) -> bool:
    return bool(EMAIL_RE.match(normalizar_email(valor)))
