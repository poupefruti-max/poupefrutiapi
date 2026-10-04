"""add bairro and cesta_tipo columns to leads

Revision ID: 002
Revises: 001
Create Date: 2026-10-03 16:45:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add columns if not exists
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='leads' AND column_name='bairro') THEN
                ALTER TABLE leads ADD COLUMN bairro TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='leads' AND column_name='cesta_tipo') THEN
                ALTER TABLE leads ADD COLUMN cesta_tipo TEXT;
            END IF;
        END $$;
    """)

    # Create indexes if not exists
    op.execute("CREATE INDEX IF NOT EXISTS leads_bairro_idx ON leads (bairro)")
    op.execute("CREATE INDEX IF NOT EXISTS leads_cesta_tipo_idx ON leads (cesta_tipo)")


def downgrade() -> None:
    # Drop indexes
    op.drop_index('leads_cesta_tipo_idx', table_name='leads')
    op.drop_index('leads_bairro_idx', table_name='leads')

    # Drop columns
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.drop_column('cesta_tipo')
        batch_op.drop_column('bairro')
