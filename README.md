# PoupeFruti — API

API REST de autenticação (JSON apenas, sem templates e sem arquivos estáticos).
Escopo atual: cadastro, login e consulta do usuário autenticado.

## Stack

Flask (application factory) · Flask-SQLAlchemy · Flask-Migrate · psycopg 3 · Flask-CORS · Flask-JWT-Extended · bcrypt

## Endpoints

Prefixo: `/api/v1/auth`

| Método | Rota           | Auth | Sucesso | Erros                     |
| ------ | -------------- | ---- | ------- | ------------------------- |
| POST   | `/register`    | não  | 201     | 400, 409                  |
| POST   | `/login`       | não  | 200     | 400, 401                  |
| GET    | `/me`          | sim  | 200     | 401, 404                  |

### `POST /register`

```json
{ "email": "ana@exemplo.com", "password": "senha1234", "name": "Ana" }
```

`name` é opcional. O e-mail é normalizado em minúsculas e não pode repetir.

### `POST /login`

```json
{ "email": "ana@exemplo.com", "password": "senha1234" }
```

Resposta: `{ "access_token": "...", "user": { ... } }`. O login inválido sempre
devolve 401 com mensagem genérica, sem revelar se o e-mail existe.

### `GET /me`

Exige `Authorization: Bearer <access_token>`. Retorna `{ "user": { ... } }`.

## Formato de erro

```json
{ "error": { "code": "invalid_credentials", "message": "E-mail ou senha inválidos." } }
```

| code                    | Status | Quando                                     |
| ----------------------- | ------ | ------------------------------------------ |
| `invalid_email`         | 400    | e-mail malformado                          |
| `weak_password`         | 400    | senha abaixo do mínimo                     |
| `missing_credentials`   | 400    | login sem e-mail ou senha                  |
| `email_already_exists`  | 409    | e-mail já cadastrado                       |
| `invalid_credentials`   | 401    | e-mail ou senha incorretos                 |
| `authorization_required`| 401    | requisição sem token                       |
| `invalid_token`         | 401    | token malformado                           |
| `token_expired`         | 401    | token expirado                             |
| `user_not_found`        | 404    | token válido de usuário removido           |
| `not_found`             | 404    | rota inexistente                           |
| `method_not_allowed`    | 405    | método não permitido                       |
| `internal_error`        | 500    | falha inesperada                           |

## Configuração

Copie `.env.example` para `.env` e preencha.

| Variável                   | Obrigatória | Descrição                                        |
| -------------------------- | ----------- | ------------------------------------------------ |
| `DATABASE_URL`             | sim         | `postgresql+psycopg://user:senha@host:5432/db`   |
| `SECRET_KEY`               | produção    | Segredo da aplicação                             |
| `JWT_SECRET_KEY`           | produção    | Segredo de assinatura do JWT                     |
| `CORS_ORIGINS`             | sim         | Origens liberadas, separadas por vírgula        |
| `JWT_ACCESS_TOKEN_EXPIRES` | não         | Expiração do token em segundos (padrão 3600)    |
| `MIN_PASSWORD_LENGTH`      | não         | Tamanho mínimo da senha (padrão 8)               |
| `FLASK_ENV`                | não         | `development` (padrão) ou `production`           |

Em `production`, a aplicação recusa subir com segredos que ainda comecem com `dev-`.

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

# 2) subir a API em http://localhost:5000
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
```

A migração inicial está em `migrations/versions/766f3be75627_create_users_table.py`.

## Exemplos com curl

```bash
# Cadastro (201)
curl -i -X POST http://localhost:5000/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"ana@exemplo.com","password":"senha1234","name":"Ana"}'

# E-mail já cadastrado (409)
curl -i -X POST http://localhost:5000/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"ana@exemplo.com","password":"outra1234"}'

# Login (200) — captura o token
TOKEN=$(curl -s -X POST http://localhost:5000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"ana@exemplo.com","password":"senha1234"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# Usuário autenticado (200)
curl -i http://localhost:5000/api/v1/auth/me \
  -H "Authorization: Bearer $TOKEN"

# Login inválido (401)
curl -i -X POST http://localhost:5000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"ana@exemplo.com","password":"errada"}'
```

## Notas de segurança

- Senhas nunca são gravadas em texto puro: o model só armazena `password_hash` (bcrypt, custo 12) e `to_dict()` não inclui nem o hash nem a senha.
- O e-mail tem índice único no banco; o 409 é tratado tanto pelo `IntegrityError` quanto é esperado por qualquer corrida entre requisições.
- Erros de JWT e de HTTP passam pelo mesmo serializador `{"error": {...}}`.