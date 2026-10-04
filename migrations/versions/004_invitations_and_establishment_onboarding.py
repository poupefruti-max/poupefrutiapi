"""add invitations table and establishment onboarding fields

Cria a tabela `invitations` e acrescenta em `establishments` os campos do
onboarding: slug, status, phone, description, opening_hours e is_published.
Também torna `cnpj` anulável (o dono do mercado informa depois do onboarding).

Reversível. Não remove nem renomeia nada além do que foi acrescentado.

"""
from alembic import op
import sqlalchemy as sa

revision = '004'
down_revision = '003'
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


def upgrade() -> None:
    # ---- establishments: campos novos -------------------------------------
    # cnpj passa a aceitar NULL (dono informa depois do onboarding).
    if _coluna_existe("establishments", "cnpj"):
        op.alter_column(
            "establishments",
            "cnpj",
            existing_type=sa.String(18),
            nullable=True,
        )

    novas = [
        ("slug", sa.String(160)),
        # 9, não 7: 'suspended' tem 9 letras e precisa caber. Um VARCHAR(7)
        # quebraria ao suspender um mercado.
        ("status", sa.String(9)),
        ("phone", sa.String(20)),
        ("description", sa.Text()),
        ("opening_hours", sa.Text()),
        ("is_published", sa.Boolean()),
    ]
    for nome, tipo in novas:
        if not _coluna_existe("establishments", nome):
            op.add_column("establishments", sa.Column(nome, tipo, nullable=True))

    # status e is_published passam a ser NOT NULL com default.
    op.execute("UPDATE establishments SET status = 'pending' WHERE status IS NULL")
    op.alter_column(
        "establishments",
        "status",
        existing_type=sa.String(9),
        nullable=False,
        server_default=sa.text("'pending'"),
    )

    op.execute("UPDATE establishments SET is_published = false WHERE is_published IS NULL")
    op.alter_column(
        "establishments",
        "is_published",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.text("false"),
    )

    op.create_index("ix_establishments_slug", "establishments", ["slug"], unique=True)
    op.create_index("ix_establishments_status", "establishments", ["status"])

    # ---- invitations -------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS invitations (
            id SERIAL PRIMARY KEY,
            establishment_id INTEGER NOT NULL
                REFERENCES establishments (id) ON DELETE CASCADE,
            role VARCHAR(7) NOT NULL DEFAULT 'owner',
            token_hash VARCHAR(64) NOT NULL,
            created_by_admin_id INTEGER
                REFERENCES admin_users (id) ON DELETE SET NULL,
            created_by_user_id INTEGER
                REFERENCES users (id) ON DELETE SET NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            accepted_at TIMESTAMPTZ,
            accepted_user_id INTEGER
                REFERENCES users (id) ON DELETE SET NULL,
            revoked_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_invitations_one_creator CHECK (
                (created_by_admin_id IS NOT NULL AND created_by_user_id IS NULL)
                OR (created_by_admin_id IS NULL AND created_by_user_id IS NOT NULL)
            )
        )
    """)

    op.create_index("ix_invitations_establishment_id", "invitations", ["establishment_id"])
    op.create_index("ix_invitations_token_hash", "invitations", ["token_hash"], unique=True)
    op.create_index("ix_invitations_expires_at", "invitations", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_invitations_expires_at", table_name="invitations")
    op.drop_index("ix_invitations_token_hash", table_name="invitations")
    op.drop_index("ix_invitations_establishment_id", table_name="invitations")
    op.drop_table("invitations")

    op.drop_index("ix_establishments_status", table_name="establishments")
    op.drop_index("ix_establishments_slug", table_name="establishments")

    for nome in ("is_published", "opening_hours", "description", "phone", "status", "slug"):
        op.drop_column("establishments", nome)

    op.alter_column(
        "establishments",
        "cnpj",
        existing_type=sa.String(18),
        nullable=False,
    )
