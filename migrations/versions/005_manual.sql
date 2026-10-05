-- ===========================================================================
-- 005_manual.sql — cestas da sobra do dia (tabela `baskets`)
-- ===========================================================================
--
-- Para bancos criados à mão por SQL, sem Alembic (sem tabela alembic_version).
-- Espelha a migração 005 e o `app/models.py`.
--
-- NÃO toca em `users`, `leads`, `establishments` nem `alembic_version`.
-- `establishments` e `users` são apenas referenciadas pelas FKs.
--
-- IDEMPOTENTE: pode rodar quantas vezes quiser.
--
--     psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 005_manual.sql
--
-- Se der erro, tudo é desfeito: o arquivo está inteiro em uma transação.
-- ===========================================================================


BEGIN;

-- ===========================================================================
-- baskets — cestas de frutas ou legumes com desconto, para retirada
-- ===========================================================================
--
-- Tamanho das colunas de enum: tem que caber o maior valor.
--   type   -> 'legumes' (7)  => VARCHAR(7)
--   status -> 'active'  (6)  => VARCHAR(6)
-- VARCHAR menor que isso quebraria ao gravar o valor mais longo — foi o que
-- aconteceu com establishments.status (VARCHAR(7) para 'suspended').

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

    CONSTRAINT ck_baskets_price_positive          CHECK (price_cents > 0),
    CONSTRAINT ck_baskets_quantity_non_negative   CHECK (quantity >= 0),
    CONSTRAINT ck_baskets_weight_positive         CHECK (weight_grams > 0),
    CONSTRAINT ck_baskets_pickup_window           CHECK (pickup_ends_at > pickup_starts_at)
);

-- ---------------------------------------------------------------------------
-- Índices (mesmos nomes do models.py)
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_baskets_establishment_id       ON baskets (establishment_id);
CREATE INDEX IF NOT EXISTS ix_baskets_establishment_status  ON baskets (establishment_id, status);
CREATE INDEX IF NOT EXISTS ix_baskets_pickup_ends_at        ON baskets (pickup_ends_at);


COMMIT;


-- ===========================================================================
-- CONSULTAS DE VERIFICAÇÃO
-- Rode depois de aplicar. Nenhuma delas altera dados.
-- ===========================================================================

-- ---- 1) Colunas de baskets: esperado exatamente estas 15 -------------------
--
--   id, establishment_id, type, name, weight_grams, price_cents,
--   retail_value_cents, quantity, pickup_starts_at, pickup_ends_at, notes,
--   status, created_by_user_id, created_at, updated_at
SELECT ordinal_position AS pos,
       column_name,
       data_type,
       character_maximum_length AS len,
       is_nullable,
       column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name   = 'baskets'
ORDER BY ordinal_position;

-- ---- 2) Defaults que devem existir ---------------------------------------
--   quantity -> 0
--   status   -> 'active'::character varying
--   created_at / updated_at -> now()
SELECT column_name, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name   = 'baskets'
  AND column_name IN ('quantity', 'status', 'created_at', 'updated_at')
ORDER BY column_name;

-- ---- 3) CHECK constraints: esperado exatamente estes 4 --------------------
SELECT conname,
       pg_get_constraintdef(oid) AS definicao
FROM pg_constraint
WHERE conrelid = 'public.baskets'::regclass
  AND contype  = 'c'
ORDER BY conname;

-- ---- 4) Índices: esperado exatamente estes 3 -----------------------------
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename  = 'baskets'
ORDER BY indexname;

-- ---- 5) FKs (2) ----------------------------------------------------------
--   establishment_id  -> establishments(id) ON DELETE CASCADE
--   created_by_user_id -> users(id)         ON DELETE SET NULL
SELECT kcu.column_name,
       ccu.table_name AS references_table,
       rc.delete_rule  AS on_delete
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage ccu
  ON tc.constraint_name = ccu.constraint_name
JOIN information_schema.referential_constraints rc
  ON tc.constraint_name = rc.constraint_name
WHERE tc.table_schema = 'public'
  AND tc.table_name   = 'baskets'
  AND tc.constraint_type = 'FOREIGN KEY'
ORDER BY kcu.column_name;

-- ---- 6) Sanidade dos dados ----------------------------------------------
--   type   deve ser frutas|legumes
--   status deve ser active|paused|closed
--   nenhuma cesta pode violar a janela (o CHECK impede, mas confira)
SELECT 'type invalido' AS problema, type AS valor, count(*)
FROM baskets
WHERE type NOT IN ('frutas', 'legumes')
GROUP BY type

UNION ALL

SELECT 'status invalido', status, count(*)
FROM baskets
WHERE status NOT IN ('active', 'paused', 'closed')
GROUP BY status

UNION ALL

SELECT 'janela invertida', 'pickup_ends_at <= pickup_starts_at', count(*)
FROM baskets
WHERE pickup_ends_at <= pickup_starts_at

UNION ALL

SELECT 'estoque negativo', 'quantity < 0', count(*)
FROM baskets
WHERE quantity < 0;

-- ---- 7) Quantas cestas cada mercado tem -----------------------------------
SELECT e.id,
       e.name,
       e.slug,
       count(b.id) FILTER (WHERE b.status IN ('active', 'paused')) AS abertas,
       count(b.id) FILTER (WHERE b.status = 'closed')          AS encerradas
FROM establishments e
LEFT JOIN baskets b ON b.establishment_id = e.id
GROUP BY e.id, e.name, e.slug
ORDER BY e.id;

-- ===========================================================================
-- NOTA SOBRE `updated_at`
-- ===========================================================================
-- Não há trigger de propósito. O `Basket.updated_at` do models.py usa
-- `onupdate=func.now()`, e o SQLAlchemy já emite `updated_at = now()` em todo
-- UPDATE feito pela aplicação. Um UPDATE executado direto no psql não move o
-- campo — igual ao comportamento de qualquer outra coluna `updated_at` do
-- projeto.
-- ===========================================================================
