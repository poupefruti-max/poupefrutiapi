"""new data model: leads, users, establishments, establishment_members

Revision ID: 001
Revises: 
Create Date: 2026-10-03 14:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, ENUM

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


lead_type_enum = ENUM('person', 'establishment', name='lead_type', create_type=True)
lead_status_enum = ENUM('new', 'contacted', 'qualified', 'converted', 'lost', name='lead_status', create_type=True)


def upgrade() -> None:
    # Enable citext extension
    op.execute('CREATE EXTENSION IF NOT EXISTS citext')

    # Create enums
    lead_type_enum.create(op.get_bind(), checkfirst=True)
    lead_status_enum.create(op.get_bind(), checkfirst=True)

    # Create establishments table
    op.create_table(
        'establishments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('lead_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(160), nullable=False),
        sa.Column('cnpj', sa.String(18), nullable=False),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cnpj'),
    )
    op.create_index('ix_establishments_lead_id', 'establishments', ['lead_id'], unique=True)
    op.create_index('ix_establishments_cnpj', 'establishments', ['cnpj'], unique=True)

    # Create establishment_members table
    op.create_table(
        'establishment_members',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('establishment_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(30), server_default=sa.text("'owner'"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['establishment_id'], ['establishments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'establishment_id'),
    )

    # Add new columns to users table
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('lead_id', sa.Integer(), nullable=True))
        batch_op.alter_column('email',
               existing_type=sa.String(255),
               type_=CITEXT(),
               existing_nullable=False)
        batch_op.create_index('ix_users_lead_id', ['lead_id'], unique=True)

    # Add foreign key from users to leads (after leads table exists)
    # This will be done after leads table is modified

    # Modify leads table - rename tipo to type, add new columns
    with op.batch_alter_table('leads', schema=None) as batch_op:
        # Add new columns
        batch_op.add_column(sa.Column('type', lead_type_enum, nullable=True))
        batch_op.add_column(sa.Column('name', sa.String(160), nullable=True))
        batch_op.add_column(sa.Column('phone', sa.String(20), nullable=True))
        batch_op.add_column(sa.Column('source', sa.String(60), nullable=True))
        batch_op.add_column(sa.Column('status', lead_status_enum, server_default=sa.text("'new'"), nullable=True))
        batch_op.add_column(sa.Column('extra', JSONB(), server_default=sa.text("'{}'"), nullable=True))
        batch_op.add_column(sa.Column('converted_at', sa.DateTime(timezone=True), nullable=True))
        
        # Alter email to CITEXT
        batch_op.alter_column('email',
               existing_type=sa.String(255),
               type_=CITEXT(),
               existing_nullable=True)

    # Migrate data from old columns to new columns
    op.execute("""
        UPDATE leads SET
            type = CASE 
                WHEN tipo = 'cliente' THEN 'person'::lead_type
                WHEN tipo = 'mercado' THEN 'establishment'::lead_type
                ELSE 'person'::lead_type
            END,
            name = COALESCE(nome, responsavel, estabelecimento, ''),
            phone = whatsapp,
            source = COALESCE(origem, 'landing-poupefruti'),
            status = 'new'::lead_status,
            extra = jsonb_build_object(
                'cesta', cesta,
                'responsavel', responsavel,
                'estabelecimento', estabelecimento,
                'tipo_estabelecimento', tipo_estabelecimento,
                'enviado_em', enviado_em,
                'bairro', bairro
            ) - '{null}'::text[]
        WHERE tipo IS NOT NULL
    """)

    # Make new columns non-nullable after data migration
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.alter_column('type', nullable=False)
        batch_op.alter_column('name', nullable=False)
        batch_op.alter_column('phone', nullable=False)
        batch_op.alter_column('status', nullable=False)
        batch_op.alter_column('extra', nullable=False)
        # Drop old columns
        batch_op.drop_column('tipo')
        batch_op.drop_column('nome')
        batch_op.drop_column('whatsapp')
        batch_op.drop_column('bairro')
        batch_op.drop_column('origem')
        batch_op.drop_column('enviado_em')
        batch_op.drop_column('cesta')
        batch_op.drop_column('responsavel')
        batch_op.drop_column('estabelecimento')
        batch_op.drop_column('tipo_estabelecimento')

    # Create unique partial index on leads.email
    op.create_index(
        'ix_leads_email_unique',
        'leads',
        ['email'],
        unique=True,
        postgresql_where=sa.text('email IS NOT NULL')
    )

    # Add foreign key from users.lead_id to leads.id
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_foreign_key('fk_users_lead_id', 'leads', ['lead_id'], ['id'], ondelete='SET NULL')

    # Add foreign key from establishments.lead_id to leads.id
    with op.batch_alter_table('establishments', schema=None) as batch_op:
        batch_op.create_foreign_key('fk_establishments_lead_id', 'leads', ['lead_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    # Drop foreign keys
    with op.batch_alter_table('establishments', schema=None) as batch_op:
        batch_op.drop_constraint('fk_establishments_lead_id', type_='foreignkey')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('fk_users_lead_id', type_='foreignkey')

    # Drop unique partial index
    op.drop_index('ix_leads_email_unique', table_name='leads')

    # Restore old columns in leads
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tipo', sa.String(20), nullable=True))
        batch_op.add_column(sa.Column('nome', sa.String(160), nullable=True))
        batch_op.add_column(sa.Column('whatsapp', sa.String(20), nullable=True))
        batch_op.add_column(sa.Column('bairro', sa.String(120), nullable=True))
        batch_op.add_column(sa.Column('origem', sa.String(60), nullable=True))
        batch_op.add_column(sa.Column('enviado_em', sa.String(40), nullable=True))
        batch_op.add_column(sa.Column('cesta', sa.String(20), nullable=True))
        batch_op.add_column(sa.Column('responsavel', sa.String(160), nullable=True))
        batch_op.add_column(sa.Column('estabelecimento', sa.String(160), nullable=True))
        batch_op.add_column(sa.Column('tipo_estabelecimento', sa.String(30), nullable=True))

    # Migrate data back from new columns to old columns
    op.execute("""
        UPDATE leads SET
            tipo = CASE 
                WHEN type = 'person' THEN 'cliente'
                WHEN type = 'establishment' THEN 'mercado'
                ELSE 'cliente'
            END,
            nome = name,
            whatsapp = phone,
            bairro = extra->>'bairro',
            origem = source,
            enviado_em = extra->>'enviado_em',
            cesta = extra->>'cesta',
            responsavel = extra->>'responsavel',
            estabelecimento = extra->>'estabelecimento',
            tipo_estabelecimento = extra->>'tipo_estabelecimento'
    """)

    # Make old columns non-nullable where needed
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.alter_column('tipo', nullable=False)
        batch_op.alter_column('nome', nullable=False)
        batch_op.alter_column('whatsapp', nullable=False)
        batch_op.alter_column('bairro', nullable=False)
        # Drop new columns
        batch_op.drop_column('type')
        batch_op.drop_column('name')
        batch_op.drop_column('phone')
        batch_op.drop_column('source')
        batch_op.drop_column('status')
        batch_op.drop_column('extra')
        batch_op.drop_column('converted_at')
        batch_op.alter_column('email',
               existing_type=CITEXT(),
               type_=sa.String(255),
               existing_nullable=True)

    # Drop foreign key and columns from users
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index('ix_users_lead_id')
        batch_op.drop_column('lead_id')
        batch_op.alter_column('email',
               existing_type=CITEXT(),
               type_=sa.String(255),
               existing_nullable=False)

    # Drop tables
    op.drop_table('establishment_members')
    op.drop_table('establishments')

    # Drop enums
    lead_status_enum.drop(op.get_bind(), checkfirst=True)
    lead_type_enum.drop(op.get_bind(), checkfirst=True)

    # Drop citext extension (optional, might be used by other tables)
    # op.execute('DROP EXTENSION IF EXISTS citext')