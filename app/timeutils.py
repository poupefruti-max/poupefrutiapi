"""Datas e horários: sempre em UTC e sempre com fuso (timezone-aware).

Por que este módulo existe
-------------------------
O PostgreSQL devolve `TIMESTAMPTZ` **com** fuso. O SQLite devolve as datas
**sem** fuso (o driver do SQLite simplesmente descarta o tzinfo ao gravar).
Comparar os dois tipos levanta:

    TypeError: can't compare offset-naive and offset-aware datetimes

O SQLite é o banco dos testes, então a suíte não pegava nada — e o bug só
aparecia em produção, no `Invitation.esta_pendente()`.

A regra do projeto:

* Para **gravar** o instante de agora, use `agora_utc()`.
* Para **comparar** com uma coluna `DateTime(timezone=True)`, normalize antes
  com `como_utc()`. Isso funciona nos dois bancos: no PostgreSQL a data já vem
  com fuso e é apenas convertida; no SQLite vem sem fuso e ganha `timezone.utc`.

Nunca use `datetime.utcnow()`: devolve um datetime *naive*, que é a origem do
TypeError acima.
"""

from datetime import datetime, timezone

#: Fuso usado em todo o projeto.
UTC = timezone.utc

__all__ = ["UTC", "agora_utc", "como_utc"]


def agora_utc() -> datetime:
    """Instante atual em UTC, com fuso. Substitui `datetime.utcnow()`."""
    return datetime.now(UTC)


def como_utc(valor):
    """Normaliza um datetime para UTC com fuso. `None` continua `None`.

    * `None` -> `None`
    * sem fuso (naive) -> assume que os valores são UTC e anexa `timezone.utc`
      (é o que o SQLite devolve)
    * com fuso (aware) -> converte para UTC (é o que o PostgreSQL devolve)
    """
    if valor is None:
        return None
    if valor.tzinfo is None or valor.tzinfo.utcoffset(valor) is None:
        return valor.replace(tzinfo=UTC)
    return valor.astimezone(UTC)
