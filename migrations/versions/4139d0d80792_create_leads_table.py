"""create leads table

Revision ID: 4139d0d80792
Revises: 766f3be75627
Create Date: 2026-10-01 00:38:56.924361

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "4139d0d80792"
down_revision = "766f3be75627"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "leads",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("nome", sa.String(length=160), nullable=False),
        sa.Column("whatsapp", sa.String(length=20), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("bairro", sa.String(length=120), nullable=False),
        sa.Column("origem", sa.String(length=60), nullable=True),
        sa.Column("enviado_em", sa.String(length=40), nullable=True),
        sa.Column("cesta", sa.String(length=20), nullable=True),
        sa.Column("responsavel", sa.String(length=160), nullable=True),
        sa.Column("estabelecimento", sa.String(length=160), nullable=True),
        sa.Column("tipo_estabelecimento", sa.String(length=30), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leads")),
    )
    op.create_index(op.f("ix_leads_tipo"), "leads", ["tipo"], unique=False)
    op.create_index(op.f("ix_leads_whatsapp"), "leads", ["whatsapp"], unique=False)
    op.create_index(op.f("ix_leads_bairro"), "leads", ["bairro"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_leads_bairro"), table_name="leads")
    op.drop_index(op.f("ix_leads_whatsapp"), table_name="leads")
    op.drop_index(op.f("ix_leads_tipo"), table_name="leads")
    op.drop_table("leads")