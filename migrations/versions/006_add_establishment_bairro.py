"""add establishments.bairro

Acrescenta em `establishments` a coluna `bairro`: o bairro do mercado em
Campinas, usado pela rota pública de estatísticas
(`GET /api/v1/public/stats?bairro=`).

- VARCHAR(80), anulável: enquanto o mercado não informar, fica NULL — e é por
  isso que `publicar` passou a exigir o campo.
- Indexada: o filtro por bairro é a consulta da landing, e ela roda a cada
  visita à página.

Reversível: o `downgrade` só remove o índice e a coluna que esta revisão criou.
Nenhum dado é apagado do resto, e nenhum bairro já gravado é renomeado.

Revisão filha da 005, para não criar mais de uma head.
"""
from alembic import op
import sqlalchemy as sa

revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None


def _coluna_existe(tabela: str, coluna: str) -> bool:
    """True se a coluna já existe no banco (permite rodar em bases divergentes)."""
    bind = op.get_bind()
    return bool(
        bind.exec_driver_sql(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c",
            {"t": tabela, "c": coluna},
        ).scalar()
    )


def _indice_existe(nome: str) -> bool:
    """True se o índice já existe (bases aplicadas à mão podem já tê-lo)."""
    bind = op.get_bind()
    return bool(
        bind.exec_driver_sql(
            "SELECT 1 FROM pg_indexes WHERE indexname = :n", {"n": nome}
        ).scalar()
    )


def upgrade() -> None:
    # Sem default e sem backfill: `NULL` significa "o mercado ainda não
    # informou o bairro". Preencher com um bairro inventado seria pior que
    # deixar a lacuna visível.
    if not _coluna_existe("establishments", "bairro"):
        op.add_column(
            "establishments",
            sa.Column("bairro", sa.String(80), nullable=True),
        )

    if not _indice_existe("ix_establishments_bairro"):
        op.create_index("ix_establishments_bairro", "establishments", ["bairro"])


def downgrade() -> None:
    if _indice_existe("ix_establishments_bairro"):
        op.drop_index("ix_establishments_bairro", table_name="establishments")
    if _coluna_existe("establishments", "bairro"):
        op.drop_column("establishments", "bairro")