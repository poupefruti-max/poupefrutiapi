-- ===========================================================================
-- 004_manual.sql — aplicação manual da migração 004
-- ===========================================================================
--
-- Para bancos criados à mão por SQL, sem Alembic (sem tabela alembic_version).
-- Aplica apenas o que a migração 004 e o `app/models.py` esperam e que ainda
-- não existe:
--
--   * establishments: slug, status, phone, description, opening_hours,
--     is_published + índices (slug único, status)
--   * invitations: tabela completa + índices + CHECK de um único criador
--
-- NÃO toca em `users` nem em `leads`, e NÃO mexe em `alembic_version`.
-- `admin_users` é assumida completa (as FKs de invitations a referenciam).
--
-- IDEMPOTENTE: pode rodar quantas vezes quiser. Toda alteração é IF NOT EXISTS
-- ou um UPDATE/ALTER normalizado, então a segunda execução é um no-op.
--
-- Para aplicar (psql ou console do Neon):
--     psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 004_manual.sql
-- Se der erro, tudo é desfeito: o arquivo está inteiro em uma transação.
-- ===========================================================================


BEGIN;

-- ===========================================================================
-- 1) establishments — colunas que faltam
-- ===========================================================================
--
-- Nota sobre `status`: a migração 004 usava VARCHAR(7), que NÃO comporta
-- 'suspended' (9 letras) e quebraria ao suspender um mercado. Aqui usamos
-- VARCHAR(9), que é o tamanho que o enum EstablishmentStatus realmente exige
-- (pending=7, active=6, suspended=9) — e é o mesmo que o models.py gera.
-- A migração 004 já foi corrigida para String(9); este arquivo acompanha.
-- tests/test_enum_column_length.py trava essa largura.

-- cnpj: a migração 004 tornava anulável. Se já aceitar NULL, isto é no-op.
ALTER TABLE establishments ALTER COLUMN cnpj DROP NOT NULL;

ALTER TABLE establishments ADD COLUMN IF NOT EXISTS slug            VARCHAR(160);
ALTER TABLE establishments ADD COLUMN IF NOT EXISTS status          VARCHAR(9);
ALTER TABLE establishments ADD COLUMN IF NOT EXISTS phone           VARCHAR(20);
ALTER TABLE establishments ADD COLUMN IF NOT EXISTS description     TEXT;
ALTER TABLE establishments ADD COLUMN IF NOT EXISTS opening_hours   TEXT;
ALTER TABLE establishments ADD COLUMN IF NOT EXISTS is_published    BOOLEAN;

-- Linhas existentes recebem os defaults antes de virar NOT NULL.
-- (Se a coluna acabou de ser criada, PostgreSQL já a preencheu com o default
--  apenas se ele estiver declarado; por isso o UPDATE é explícito.)
UPDATE establishments SET status       = 'pending' WHERE status IS NULL;
UPDATE establishments SET is_published = false     WHERE is_published IS NULL;

ALTER TABLE establishments ALTER COLUMN status       SET DEFAULT 'pending';
ALTER TABLE establishments ALTER COLUMN status       SET NOT NULL;
ALTER TABLE establishments ALTER COLUMN is_published SET DEFAULT false;
ALTER TABLE establishments ALTER COLUMN is_published SET NOT NULL;

-- Índices: slug precisa ser único (é o que a rota pública resolve) e
-- status é filtrado no painel.
CREATE UNIQUE INDEX IF NOT EXISTS ix_establishments_slug   ON establishments (slug);
CREATE INDEX        IF NOT EXISTS ix_establishments_status ON establishments (status);


-- ===========================================================================
-- 2) invitations — tabela nova
-- ===========================================================================
--
-- Tipos e nomes de constraint/índice idênticos aos de `app/models.py`.
-- `token_hash` guarda apenas o SHA-256 do convite: o token em claro nunca é
-- gravado.

CREATE TABLE IF NOT EXISTS invitations (
    id                  SERIAL       PRIMARY KEY,
    establishment_id   INTEGER      NOT NULL
                        REFERENCES establishments (id) ON DELETE CASCADE,
    role                VARCHAR(7)   NOT NULL DEFAULT 'owner',
    token_hash          VARCHAR(64)  NOT NULL,
    created_by_admin_id INTEGER
                        REFERENCES admin_users (id) ON DELETE SET NULL,
    created_by_user_id  INTEGER
                        REFERENCES users (id)        ON DELETE SET NULL,
    expires_at          TIMESTAMPTZ  NOT NULL,
    accepted_at         TIMESTAMPTZ,
    accepted_user_id    INTEGER
                        REFERENCES users (id)        ON DELETE SET NULL,
    revoked_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_invitations_one_creator CHECK (
        (created_by_admin_id IS NOT NULL AND created_by_user_id IS NULL)
        OR (created_by_admin_id IS NULL AND created_by_user_id IS NOT NULL)
    )
);

-- Se a tabela já existisse sem o CHECK, adiciona agora (idempotente).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_invitations_one_creator'
    ) THEN
        ALTER TABLE invitations ADD CONSTRAINT ck_invitations_one_creator CHECK (
            (created_by_admin_id IS NOT NULL AND created_by_user_id IS NULL)
            OR (created_by_admin_id IS NULL AND created_by_user_id IS NOT NULL)
        );
    END IF;
END $$;

CREATE INDEX        IF NOT EXISTS ix_invitations_establishment_id ON invitations (establishment_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_invitations_token_hash       ON invitations (token_hash);
CREATE INDEX        IF NOT EXISTS ix_invitations_expires_at       ON invitations (expires_at);


COMMIT;


-- ===========================================================================
-- CONSULTAS DE VERIFICAÇÃO
-- Rode depois de aplicar. Nenhuma delas altera dados.
-- ===========================================================================

-- ---- 1) Colunas de establishments: esperado exatamente estas 12 ----------
--
--   id, lead_id, name, cnpj, address, created_at,
--   slug, status, phone, description, opening_hours, is_published
--
-- data_type esperado:
--   slug          varchar(160)
--   status        varchar(9)      <- 9, não 7 (senão 'suspended' não cabe)
--   phone         varchar(20)
--   description   text
--   opening_hours text
--   is_published  boolean
--
-- is_nullable esperado: 'NO' para name, status, is_published, created_at;
--                        'YES' para as demais.
SELECT ordinal_position AS pos,
       column_name,
       data_type,
       character_maximum_length AS len,
       is_nullable,
       column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name   = 'establishments'
ORDER BY ordinal_position;

-- ---- 2) Colunas de invitations: esperado exatamente estas 12 ------------
--
--   id, establishment_id, role, token_hash,
--   created_by_admin_id, created_by_user_id, expires_at,
--   accepted_at, accepted_user_id, revoked_at, created_at
--
-- data_type esperado:
--   id/id INTEGERS; establishment_id integer; role varchar(7);
--   token_hash varchar(64); os *_id integer;
--   expires_at / accepted_at / accepted_user_id / revoked_at / created_at = timestamp with time zone
SELECT ordinal_position AS pos,
       column_name,
       data_type,
       character_maximum_length AS len,
       is_nullable,
       column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name   = 'invitations'
ORDER BY ordinal_position;

-- ---- 3) Defaults que devem existir ---------------------------------------
--   establishments.status       -> 'pending'::character varying
--   establishments.is_published -> false
--   invitations.role            -> 'owner'::character varying
--   invitations.created_at      -> now()
SELECT table_name, column_name, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND (   (table_name = 'establishments' AND column_name IN ('status', 'is_published'))
       OR (table_name = 'invitations'   AND column_name IN ('role', 'created_at')) )
ORDER BY table_name, column_name;

-- ---- 4) Índices: esperado exatamente estes 5 ------------------------------
--
--   ix_establishments_slug      UNIQUE (slug)
--   ix_establishments_status    (status)
--   ix_invitations_establishment_id (establishment_id)
--   ix_invitations_token_hash   UNIQUE (token_hash)
--   ix_invitations_expires_at   (expires_at)
SELECT tablename,
       indexname,
       indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN ('establishments', 'invitations')
ORDER BY tablename, indexname;

-- ---- 5) Chave estrangeira de invitations (4 FKs, com ondelete) -----------
--   establishment_id    -> establishments(id)  ON DELETE CASCADE
--   created_by_admin_id -> admin_users(id)     ON DELETE SET NULL
--   created_by_user_id  -> users(id)           ON DELETE SET NULL
--   accepted_user_id    -> users(id)           ON DELETE SET NULL
SELECT tc.constraint_name,
       kcu.column_name,
       ccu.table_name  AS references_table,
       ccu.column_name  AS references_column,
       rc.delete_rule   AS on_delete
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage  kcu ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name = ccu.constraint_name
JOIN information_schema.referential_constraints rc ON tc.constraint_name = rc.constraint_name
WHERE tc.table_schema = 'public'
  AND tc.table_name   = 'invitations'
  AND tc.constraint_type = 'FOREIGN KEY'
ORDER BY kcu.column_name;

-- ---- 6) CHECK constraint de invitations ----------------------------------
-- Deve existir exatamente 1, chamado ck_invitations_one_creator.
SELECT conname,
       pg_get_constraintdef(oid) AS definicao
FROM pg_constraint
WHERE conrelid = 'public.invitations'::regclass
  AND contype  = 'c';

-- ---- 7) Sanidade dos dados ----------------------------------------------
--   status: só pending, active ou suspended (e nenhum NULL/vazio)
--   role:   só owner, manager ou staff
--   linhas com mais de um criador (violariam o CHECK)
SELECT 'status invalido em establishments' AS problema, status AS valor, count(*)
FROM establishments
WHERE status NOT IN ('pending', 'active', 'suspended')
GROUP BY status

UNION ALL

SELECT 'role invalido em invitations', role, count(*)
FROM invitations
WHERE role NOT IN ('owner', 'manager', 'staff')
GROUP BY role

UNION ALL

SELECT 'estabelecimento sem slug (rota publica nao acha)', '(vazio)', count(*)
FROM establishments
WHERE slug IS NULL OR slug = '';

-- ---- 8) Contagens ---------------------------------------------------------
SELECT
    (SELECT count(*) FROM establishments) AS estabelecimentos,
    (SELECT count(*) FROM establishments WHERE slug IS NULL) AS sem_slug,
    (SELECT count(*) FROM invitations)      AS convites;

-- ===========================================================================
-- OPCIONAL (fora da transação acima) — leia antes de rodar
-- ===========================================================================
--
-- A) `models.py` declara unique=True em establishments.lead_id e
--    establishments.cnpj, mas a migração 004 NEMHUM dos dois. Se o banco foi
--    criado à mão, é possível que esses índices não existam — e o app confia
--    neles (a rota que edita o CNPJ checa duplicidade no banco).
--
--    NÃO aplique sem antes rodar a checagem de duplicatas abaixo: se houver
--    repetição, o índice falha e a transação inteira aborta.
--
--    Checagem de duplicatas (não altera nada):
--
--      SELECT lead_id, count(*) FROM establishments
--       WHERE lead_id IS NOT NULL GROUP BY lead_id HAVING count(*) > 1;
--      SELECT cnpj,   count(*) FROM establishments
--       WHERE cnpj   IS NOT NULL GROUP BY cnpj   HAVING count(*) > 1;
--
--    Se as duas voltarem vazias, aplique:
--
--      BEGIN;
--      CREATE UNIQUE INDEX IF NOT EXISTS ix_establishments_lead_id ON establishments (lead_id);
--      CREATE UNIQUE INDEX IF NOT EXISTS ix_establishments_cnpj   ON establishments (cnpj);
--      COMMIT;
--
-- B) `slug` das linhas que já existiam fica NULL depois desta migração — a
--    004 não faz backfill. A rota pública resolve por slug, então esses
--    mercados não aparecem até terem slug. O app gera o slug na aprovação; para
--    os que já existiam, gere um:
--
--      UPDATE establishments
--         SET slug = lower(regexp_replace(
--               trim(BOTH ' ' FROM unaccent(name)),
--               '[^a-z0-9]+', '-', 'g'))
--       WHERE slug IS NULL;
--
--    Se o banco não tem a extensão unaccent, crie antes:
--      CREATE EXTENSION IF NOT EXISTS unaccent;
--    Depois confira se não gerou duplicata antes de criar o índice único.
-- ===========================================================================
