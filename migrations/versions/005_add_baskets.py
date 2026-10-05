"""add baskets table (cestas da sobra do dia)

Cria a tabela `baskets`: as cestas de frutas ou legumes que o mercado oferece
com desconto. Ainda não há pedidos nem pagamento.

Revisão filha da 004, para não criar mais de uma head.

"""
from alembic import op

revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IMPORTANTE: o tamanho das colunas de enum tem que caber o maior valor.
    #   baskets.type   -> 'legumes' (7)  => VARCHAR(7)
    #   baskets.status -> 'active'  (6)  => VARCHAR(6)
    # VARCHAR menor que isso quebraria ao gravar o valor mais longo.
    op.execute("""
        CREATE TABLE IF NOT EXISTS baskets (
            id                  SERIAL       PRIMARY KEY,
            establishment_id   INTEGER      NOT NULL
                                REFERENCES establishments (id) ON DELETE CASCADE,
            type                VARCHAR(7)   NOT NULL,
            name                VARCHAR(80),
            weight_grams        INTEGER      NOT NULL,
            price_cents         INTEGER      NOT NULL,
            retail_value_cents  INTEGER,
            quantity            INTEGER      NOT NULL DEFAULT 0,
            pickup_starts_at    TIMESTAMPTZ  NOT NULL,
            pickup_ends_at      TIMESTAMPTZ  NOT NULL,
            notes               VARCHAR(280),
            status              VARCHAR(6)   NOT NULL DEFAULT 'active',
            created_by_user_id  INTEGER
                                REFERENCES users (id) ON DELETE SET NULL,
            created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
            CONSTRAINT ck_baskets_price_positive
                CHECK (price_cents > 0),
            CONSTRAINT ck_baskets_quantity_non_negative
                CHECK (quantity >= 0),
            CONSTRAINT ck_baskets_weight_positive
                CHECK (weight_grams > 0),
            CONSTRAINT ck_baskets_pickup_window
                CHECK (pickup_ends_at > pickup_starts_at)
        )
    """)

    # `updated_at` NÃO ganha trigger de propósito: o `Basket.updated_at` do
    # models.py tem `onupdate=func.now()`, e o SQLAlchemy já emite
    # `updated_at = now()` em todo UPDATE. Uma trigger aqui duplicaria a
    # responsabilidade e divergiria do model.
    op.create_index("ix_baskets_establishment_id", "baskets", ["establishment_id"])
    op.create_index(
        "ix_baskets_establishment_status", "baskets", ["establishment_id", "status"]
    )
    op.create_index("ix_baskets_pickup_ends_at", "baskets", ["pickup_ends_at"])


def downgrade() -> None:
    op.drop_index("ix_baskets_pickup_ends_at", table_name="baskets")
    op.drop_index("ix_baskets_establishment_status", table_name="baskets")
    op.drop_index("ix_baskets_establishment_id", table_name="baskets")
    op.drop_table("baskets")
