-- ===========================================================================
-- 006_manual.sql — bairro do mercado (establishments.bairro)
-- ===========================================================================
--
-- Para bancos criados à mão por SQL, sem Alembic (sem tabela alembic_version).
-- Espelha a migração 006 e o `app/models.py`.
--
-- NÃO toca em nenhuma outra tabela nem em `alembic_version`.
--
-- IDEMPOTENTE: pode rodar quantas vezes quiser.
--
--     psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 006_manual.sql
--
-- Se der erro, tudo é desfeito: o arquivo está inteiro em uma transação.
-- ===========================================================================


BEGIN;

-- ===========================================================================
-- establishments.bairro — bairro do mercado em Campinas
-- ===========================================================================
--
-- VARCHAR(80), anulável e indexado. Nulo enquanto o dono não informar: é
-- exatamente por isso que `POST /publish` passou a exigir o campo.
--
-- Sem default e sem backfill de propósito. Preencher com um bairro inventado
-- seria pior que deixar a lacuna visível — e a lacuna aparece sozinha na rota
-- pública: mercado sem bairro não entra na contagem por bairro.
--
-- NÃO se cria um CHECK com a lista de bairros aqui. O PostgreSQL aceitaria,
-- mas a lista de app/validators.py é a fonte da verdade e vai crescer; um
-- CHECK congelado passaria a rejeitar bairros novos. A validação fica na API.

ALTER TABLE establishments
    ADD COLUMN IF NOT EXISTS bairro VARCHAR(80);

CREATE INDEX IF NOT EXISTS ix_establishments_bairro ON establishments (bairro);


COMMIT;


-- ===========================================================================
-- CONSULTAS DE VERIFICAÇÃO
-- Rode depois de aplicar. Nenhuma delas altera dados.
-- ===========================================================================

-- ---- 1) Coluna nova: esperado exatamente uma linha ------------------------
--   bairro | character varying | 80 | YES | (sem default)
SELECT ordinal_position AS pos,
       column_name,
       data_type,
       character_maximum_length AS len,
       is_nullable,
       column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name   = 'establishments'
  AND column_name  = 'bairro';

-- ---- 2) Índice: esperado exatamente ix_establishments_bairro --------------
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename  = 'establishments'
  AND indexname  = 'ix_establishments_bairro';

-- ---- 3) Quantos mercados já informaram bairro -----------------------------
-- Valores fora de app/validators.py VALORES_BAIRRO são legados da landing,
-- que tinha duas listas divergentes. NÃO são erro: não renomeie nada, o
-- endpoint de estatísticas só conta o que está na lista.
SELECT bairro, count(*)
FROM establishments
WHERE bairro IS NOT NULL
GROUP BY bairro
ORDER BY count(*) DESC, bairro;

-- ---- 4) Publicados sem bairro — é o que ainda impede a publicação ---------
SELECT id, name, slug, status, is_published
FROM establishments
WHERE is_published IS TRUE
  AND (bairro IS NULL OR bairro = '')
ORDER BY id;

-- ---- 5) Conferência final: uma linha por mercado, como a rota /stats conta -
SELECT count(*) FILTER (WHERE is_published AND status = 'active') AS publicados_ativos,
       count(*) FILTER (WHERE bairro IS NOT NULL)                  AS com_bairro
FROM establishments;