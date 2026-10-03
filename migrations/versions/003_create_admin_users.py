"""create admin_users table

Revision ID: 003
Revises: 002
Create Date: 2026-10-03 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import CITEXT

# revision identifiers, used by Alembic.
revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable citext extension if not exists
    op.execute('CREATE EXTENSION IF NOT EXISTS citext')

    op.create_table(
        'admin_users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', CITEXT(), nullable=False),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('name', sa.String(120), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
    )
    op.create_index('ix_admin_users_email', 'admin_users', ['email'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_admin_users_email', table_name='admin_users')
    op.drop_table('admin_users')