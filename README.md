# PoupeFruti — API

API REST do PoupeFruti: captação de leads, autenticação de usuários e painel
administrativo. JSON apenas, sem templates e sem arquivos estáticos — o front
(landing e painel) é hospedado separadamente na Vercel.

## Stack

Flask (application factory) · Flask-SQLAlchemy · Flask-Migrate · psycopg 3 ·
Flask-CORS · Flask-JWT-Extended · Flask-Limiter · bcrypt

## Modelo de dados

O banco separa a **captação** do **cadastro real**. Um `lead` é dado provisório e
incompleto (não tem senha nem autenticação); `users` e `establishments` só passam
a existir depois que o lead é convertido.

| Tabela                  | Papel                                                          |
| ----------------------- | -------------------------------------------------------------- |
| `leads`                 | Captação. `type` (`person`/`establishment`), `status`, `extra` (JSONB) |
| `users`                 | Usuário final, com `lead_id` apontando para o lead de origem    |
| `establishments`        | Estabelecimento, com `lead_id`                                  |
| `establishment_members` | Vínculo usuário↔estabelecimento com `role` (padrão `owner`)     |
| `admin_users`           | Administradores do painel, **sem relação com `users`**          |

`bairro` e `cesta_tipo` são colunas próprias (filtráveis e indexáveis). Os demais
campos variáveis ficam em `leads.extra` (JSONB).

## Endpoints

### Leads — públicos

| Método | Rota                      | Auth | Sucesso | Erros            |
| ------ | ------------------------- | ---- | ------- | ---------------- |
| POST   | `/api/leads/cliente`      | não  | 201     | 400              |
| POST   | `/api/leads/mercado`      | não  | 201     | 400              |
| POST   | `/api/leads/<id>/convert` | sim (admin) | 201 | 400, 401, 403, 409 |

O contrato de entrada é o mesmo desde o começo: a landing continua enviando
`tipo`, `nome`, `whatsapp`, `email`, `bairro`, `origem`, `enviado_em`, `cesta`,
`responsavel`, `estabelecimento` e `tipo_estabelecimento`.

- `tipo` `"cliente"` → `person`; `"mercado"` → `establishment`
- `bairro` → coluna `leads.bairro`; `cesta` → coluna `leads.cesta_tipo`
- os demais campos variables → `leads.extra`
- e-mail repetido (mesmo tipo) faz **upsert**: atualiza `phone`/`bairro`/
  `cesta_tipo`/`extra` do lead existente e devolve **201** com o lead, sem
  criar linha nova

### Usuários — prefixo `/api/v1/auth`

| Método | Rota        | Auth | Sucesso | Erros        |
| ------ | ----------- | ---- | ------- | ------------ |
| POST   | `/register` | não  | 201     | 400, 409     |
| POST   | `/login`    | não  | 200     | 400, 401     |
| GET    | `/me`       | sim  | 200     | 401, 403, 404 |

`/me` recusa token de administrador com **403**.

### Admin — prefixo `/api/v1/admin`

| Método | Rota                          | Auth | Descrição                                  |
| ------ | ----------------------------- | ---- | ------------------------------------------ |
| POST   | `/api/v1/admin/auth/login`    | não  | Login do admin (limite de 5/min por IP)     |
| GET    | `/api/v1/admin/auth/me`       | sim  | Administrador do token                     |
| GET    | `/api/v1/admin/leads`         | sim  | Listagem com filtros e paginação           |
| GET    | `/api/v1/admin/leads/<id>`    | sim  | Detalhe do lead                            |
| PATCH  | `/api/v1/admin/leads/<id>/status` | sim | Alteração de status                     |
| POST   | `/api/v1/admin/leads/<id>/convert` | sim | Conversão em usuário/estabelecimento |
| GET    | `/api/v1/admin/stats`         | sim  | Estatísticas                               |
| GET    | `/api/v1/admin/export`        | sim  | Exportação CSV                             |

**Parâmetros de** `GET /api/v1/admin/leads` (e de `/export`, que aceita os mesmos
filtros): `status`, `type`, `bairro`, `search` (nome/e-mail/telefone), `page`,
`per_page` (padrão 20), `sort` (padrão `-created_at`).

Exemplo: `/api/v1/admin/leads?status=new&type=person&search=ana&page=1&per_page=20`

### Aprovação, mapped a partir do enum `status`

O enum não ganhou valores novos; o painel mapeia ações para os existentes:

| Ação no painel | Status      |
| -------------- | ----------- |
| Aprovar        | `qualified` |
| Contatado      | `contacted` |
| Rejeitar       | `lost`      |
| Reativar       | `new` (só a partir de `lost`) |

Converter em usuário/estabelecimento é uma ação **separada** (modal próprio), que
chama `convert_lead` e move o lead para `converted`.

Transições aceitas (as demais devolvem **400** `invalid_transition`):

```
new       → contacted | qualified | lost | converted
contacted → qualified | lost | converted
qualified → converted | lost
converted → (nenhuma)
lost      → new
```

### Isolamento de tokens

O token de admin carrega o claim `role: "admin"` (`additional_claims`).

- `admin_required` exige JWT válido **e** `role == "admin"` **e** o `AdminUser`
  ainda existente no banco — sem isso, 403 ou 401
- token de admin em rota de usuário → **403**
- token de usuário comum em rota de admin → **403**

## Formato de erro

```json
{ "error": { "code": "invalid_credentials", "message": "E-mail ou senha inválidos." } }
```

| code                     | Status | Quando                                        |
| ------------------------ | ------ | --------------------------------------------- |
| `invalid_email`          | 400    | e-mail malformado                             |
| `weak_password`          | 400    | senha abaixo do mínimo                        |
| `missing_credentials`    | 400    | login sem e-mail ou senha                     |
| `missing_status`         | 400    | PATCH de status sem `status`                  |
| `invalid_status`         | 400    | status fora do enum                           |
| `invalid_type`           | 400    | tipo fora do enum                             |
| `invalid_transition`     | 400    | transição de status não permitida             |
| `conversion_error`       | 400    | conversão sem senha/CNPJ/e-mail               |
| `status_error`           | 400    | erro na mudança de status                     |
| `email_already_exists`   | 409    | e-mail já cadastrado                          |
| `duplicate_lead`         | 409    | conflito ao gravar o lead                     |
| `invalid_credentials`    | 401    | e-mail ou senha incorretos                    |
| `authorization_required` | 401    | requisição sem token                          |
| `invalid_token`          | 401    | token malformado                              |
| `token_expired`          | 401    | token expirado                                |
| `admin_not_found`        | 401    | token válido de admin removido                |
| `user_not_found`         | 404    | token válido de usuário removido              |
| `not_found`              | 404    | registro ou rota inexistente                  |
| `forbidden`              | 403    | token válido sem permissão                    |
| `method_not_allowed`     | 405    | método não permitido                          |
| `rate_limit_exceeded`    | 429    | limite de tentativas                          |
| `internal_error`         | 500    | falha inesperada                              |

## Criando o primeiro administrador

Não existe admin por padrão. O comando pede e-mail e senha no terminal (nada é
hardcoded):

```bash
cd api
flask --app wsgi create-admin
```

```
E-mail do admin: voce@poupefruti.com.br
Senha: ********
Nome (opcional): Dani
Admin criado com sucesso! ID: 1
```

Para criar outro, repita o comando. E-mail repetido é recusado.

## Acessando o painel

O painel é uma página estática na Vercel, em `/admin`:

```
https://poupefruti.vercel.app/admin
```

1. Rode `create-admin` no ambiente da API (uma vez).
2. Acesse `/admin` e faça login com o e-mail/senha cadastrados.
3. O token fica em `sessionStorage` (nunca em `localStorage`); em 401/403 o
   painel volta para a tela de login.

O arquivo é `ui/admin.html`, marcado como `noindex, nofollow`. A origem da Vercel
precisa estar em `CORS_ORIGINS` (já vem `https://poupefruti.vercel.app` como
padrão).

A URL da API fica na constante `API_URL`, no topo do `<script>` de
`ui/admin.html`. Em produção use a URL com `-pooler`.

## Configuração

Copie `.env.example` para `.env` e preencha.

| Variável                   | Obrigatória | Descrição                                      |
| -------------------------- | ----------- | ---------------------------------------------- |
| `DATABASE_URL`             | sim         | `postgresql+psycopg://user:senha@host:5432/db` |
| `SECRET_KEY`               | produção    | Segredo da aplicação                           |
| `JWT_SECRET_KEY`           | produção    | Segredo de assinatura do JWT                   |
| `CORS_ORIGINS`             | sim         | Origens liberadas, separadas por vírgula      |
| `JWT_ACCESS_TOKEN_EXPIRES` | não         | Expiração do token em segundos (padrão 3600)  |
| `MIN_PASSWORD_LENGTH`      | não         | Tamanho mínimo da senha (padrão 8)             |
| `FLASK_ENV`                | não         | `development` (padrão) ou `production`         |

Em `production`, a aplicação recusa subir com segredos que ainda comecem com `dev-`.

No Neon, use a connection string **com `-pooler`** e mantenha
`SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 300}`, já
configurado em `app/config.py`.

## Instalação

```bash
cd api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Crie o banco (uma vez):

```bash
createdb -U postgres poupfruti
```

## Rodando

```bash
# 1) migrações
flask --app wsgi db upgrade

# 2) criar o primeiro admin
flask --app wsgi create-admin

# 3) subir a API em http://localhost:5000
flask --app wsgi run --debug --port 5000
```

Produção:

```bash
gunicorn "wsgi:app" --bind 0.0.0.0:5000 --workers 4
```

## Migrações

```bash
flask --app wsgi db init            # só na primeira vez (já feito neste repo)
flask --app wsgi db migrate -m "msg"
flask --app wsgi db upgrade
flask --app wsgi db downgrade       # desfaz a última
flask --app wsgi db current         # revisão aplicada
```

Presentes em `migrations/versions/`:

| Revisão | Arquivo                              | O que faz                                              |
| ------- | ------------------------------------ | ------------------------------------------------------ |
| 001     | `001_new_data_model.py`              | enums, `citext`, tabelas `users`/`establishments`/`establishment_members`, migração dos leads do formato antigo |
| 002     | `002_add_bairro_cesta_tipo.py`       | colunas `bairro` e `cesta_tipo` + índices (idempotente) |
| 003     | `003_create_admin_users.py`          | tabela `admin_users`                                   |

## Testes

```bash
cd api
python -m pytest tests/ -q
```

- `tests/test_leads.py` — criação de lead, upsert de e-mail, status, conversão
  (pessoa e estabelecimento), idempotência e persistência real (confere com
  sessão nova, depois de `db.session.remove()`)
- `tests/test_admin.py` — login, rate limit, isolamento de tokens, filtros,
  paginação, transições, stats, CSV e exigência de admin no `/convert`

## Exemplos com curl

```bash
# Captação (201)
curl -i -X POST http://localhost:5000/api/leads/cliente \
  -H 'Content-Type: application/json' \
  -d '{"tipo":"cliente","nome":"Teste","whatsapp":"(11) 99999-9999",
       "email":"teste@exemplo.com","bairro":"Centro",
       "origem":"landing-poupefruti","cesta":"frutas"}'

# Login do admin (200) — captura o token
ADMIN=$(curl -s -X POST http://localhost:5000/api/v1/admin/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"voce@poupefruti.com.br","password":"suaSenha123"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# Listagem filtrada (200)
curl -i "http://localhost:5000/api/v1/admin/leads?status=new&per_page=20" \
  -H "Authorization: Bearer $ADMIN"

# Aprovar um lead (200) — vira qualified
curl -i -X PATCH http://localhost:5000/api/v1/admin/leads/1/status \
  -H "Authorization: Bearer $ADMIN" \
  -H 'Content-Type: application/json' \
  -d '{"status":"qualified"}'

# Converter em usuário (201)
curl -i -X POST http://localhost:5000/api/v1/admin/leads/1/convert \
  -H "Authorization: Bearer $ADMIN" \
  -H 'Content-Type: application/json' \
  -d '{"password":"senha1234"}'

# Estatísticas (200)
curl -i http://localhost:5000/api/v1/admin/stats \
  -H "Authorization: Bearer $ADMIN"

# Exportar CSV (200) — precisa do header, então não use um link simples
curl -s "http://localhost:5000/api/v1/admin/export" \
  -H "Authorization: Bearer $ADMIN" -o leads.csv

# Token de admin é recusado em rota de usuário (403)
curl -i http://localhost:5000/api/v1/auth/me \
  -H "Authorization: Bearer $ADMIN"
```

## Notas de segurança

- Senhas nunca são gravadas em texto puro: os models só armazenam `password_hash`
  (bcrypt) e `to_dict()` não inclui hash nem senha.
- `AdminUser` é uma tabela separada de `User`, sem `is_admin` e sem relação
  entre elas — um token de usuário não vira admin por Promote.
- E-mail tem índice único no banco; 409 é tratado tanto pelo `IntegrityError`
  quanto esperado por corrida entre requisições.
- O login do admin tem limite de 5 tentativas por minuto por IP, e a resposta de
  erro é genérica (não revela se o e-mail existe).
- A exportação CSV protege contra CSV injection: células que começam com
  `=`, `+`, `-` ou `@` são prefixadas com `'`.
- Erros de JWT, de HTTP e do limitador passam pelo mesmo serializador
  `{"error": {...}}`.
