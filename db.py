"""Pool de conexões PostgreSQL com psycopg 3."""

import os
import logging
from contextlib import contextmanager

from dotenv import load_dotenv
from psycopg_pool import ConnectionPool
from psycopg import sql

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL não definida no ambiente. Configure no arquivo .env")

pool = ConnectionPool(
    conninfo=DATABASE_URL,
    min_size=1,
    max_size=10,
    kwargs={"autocommit": False},
)


def test_connection() -> bool:
    """Testa a conexão executando SELECT now()."""
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT now()")
                result = cur.fetchone()
                logging.info("Conexão OK: %s", result[0])
                return True
    except Exception as e:
        logging.exception("Falha no teste de conexão: %s", e)
        return False


@contextmanager
def get_cursor():
    """Context manager para obter cursor do pool."""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            try:
                yield cur
                conn.commit()
            except Exception:
                conn.rollback()
                raise


def close_pool() -> None:
    """Fecha o pool de conexões."""
    pool.close()