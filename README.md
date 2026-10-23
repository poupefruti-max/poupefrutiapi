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
| `establishments`        | Estabelecimento, com `lead_id`, `slug`, `status`, `phone`, `bairro`, `description`, `opening_hours`, `is_published`. O `cnpj` é **anulável** (o dono informa depois) |
| `establishment_members` | Vínculo usuário↔estabelecimento com `role` (padrão `owner`)     |
| `baskets`               | Cestas da sobra do dia (frutas/legumes) com desconto e estoque           |
| `invitations`           | Convites de acesso. Guarda **apenas o SHA-256** do token         |
| `admin_users`           | Administradores do painel, **sem relação com `users`**          |

`bairro` e `cesta_tipo` são colunas próprias (filtráveis e indexáveis). Os demais
campos variáveis ficam em `leads.extra` (JSONB).

`establishments.bairro` (VARCHAR 80, anulável, indexado) é o bairro do **mercado**
— diferente de `leads.bairro`, que é o bairro de quem se cadastrou. Sem ele o
mercado não pode ser publicado e não entra em nenhuma contagem por bairro.

### Bairros de Campinas — lista única

A lista de bairros do projeto está em **`app/validators.py`, `VALORES_BAIRRO`**,
e é a única fonte:

- os dois `<select>` do formulário da landing mostram exatamente esses valores,
  na mesma ordem;
- o `<select>` de bairro do painel monta as opções a partir do que o
  `GET /profile` devolve no campo `bairros` — o HTML não guarda cópia nenhuma;
- o perfil do mercado e a rota `/public/stats` validam contra a lista.

Antes havia duas listas divergentes dentro de `index.html`: a do formulário de
cliente tinha `Jardim(ns) Guanabara`, `Jardim(ns) Paulistano` e `Jardim(ns)
Chapada`, e a de mercado não. Isso fazia o mesmo bairro virar dois valores
diferentes no banco.

O último item é `Bairro não listado`: a porta de escape para quem mora num
bairro que não está na lista. Ele conta como valor válido (aparece no `<select>`,
passa na validação, dá para publicar e é filtrável em `/public/stats`). Fica por
último de propósito: é a opção para quem não se reconhece na lista, não uma
recomendação.

> **Não renomeie bairros já gravados.** `Jardim(ns) Guanabara` continua escrito
> assim, mesmo com a grafia feia: mudar o texto quebraria a contagem por bairro
> de quem já está no banco. Para corrigir um bairro, acrescente o valor novo ao
> lado do antigo e migre os dados em um script à parte.

`POST /api/leads/cliente` e `/mercado` **não** recusam bairro fora da lista (o
banco já tem valores assim, e recusar faria a pessoa escolher um bairro errado
só para passar na validação). O que elas fazem é gravar o texto canônico quando
o bairro é um valor da lista: `"centro"` vira `"Centro"`.

Papéis e status ficam em `app/rbac.py` (fonte única): `owner`, `manager`,
`staff`; e `pending`, `active`, `suspended`. Os enums de cesta também:
`frutas`/`legumes` e `active`/`paused`/`closed`.

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

Prefixo `/api/v1/auth`

| Método | Rota        | Auth | Sucesso | Erros                    |
| ------ | ----------- | ---- | ------- | ------------------------ |
| POST   | `/register` | não  | 201     | 400, 409                 |
| POST   | `/login`    | não  | 200     | 400, 401, **429**        |
| GET    | `/me`       | sim  | 200     | 401, 403, 404            |

O login tem **dois** contadores independentes: um por IP (protege a API) e um
por e-mail (protege uma conta específica de ataque dirigido). O e-mail vem
normalizado do corpo, então `Alvo@Test.com ` e `alvo@test.com` caem no mesmo
contador. Ambos respondem **429** com o JSON da API, nunca HTML.

O `/me` recusa token de administrador com **403**. Para usuário, devolve
`{ "user": { ... } }` com `memberships` — cada entrada traz `establishment_id`,
`name`, `slug`, `role` e `establishment_status`, para o front montar a navegação:

```json
{
  "user": {
    "id": 1, "email": "maria@exemplo.com", "name": "Maria",
    "memberships": [
      {
        "establishment_id": 1,
        "name": "Mercado São José",
        "slug": "mercado-sao-jose",
        "role": "owner",
        "establishment_status": "active"
      }
    ]
  }
}
```

### Convites — prefixo `/api/v1/invitations` (público, com rate limit)

| Método | Rota        | Auth  | Sucesso | Erros                          |
| ------ | ----------- | ----- | ------- | ------------------------------ |
| POST   | `/validate` | não   | 200     | 400 (`invalid_invitation`), 429 |
| POST   | `/accept`   | não   | 201     | 400, 409, 429                  |

`POST /validate` com `{ "token": "..." }` → `{ "valid": true, "establishment_name": "...", "role": "owner", "expires_at": "..." }`

`POST /accept` com `{ "token", "name", "email", "password" }` cria o `User`, o
`EstablishmentMember` com o papel do convite e devolve `access_token` **no mesmo
formato do login de usuário** (`{ "access_token", "user", ... }`).

Token inválido, expirado, já usado e revogado devolvem **a mesma resposta**
(`invalid_invitation`), para não revelar se o token existiu.

O aceite é **atômico**: um `UPDATE ... WHERE accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()`
com verificação de `rowcount`. Só a transação que marca a linha cria a conta,
então dois aceites simultâneos geram **uma única conta**.

### Onboarding de mercados

Fluxo: admin aprova → copia o link → manda por WhatsApp → o responsável cria a
conta e vira `owner`.

```
POST /api/v1/admin/leads/<id>/approve-market     (admin) -> 201
POST /api/v1/admin/establishments/<id>/invitations  (admin) -> 201
GET  /api/v1/admin/establishments                 (admin) -> 200
GET  /api/v1/admin/establishments/<id>            (admin) -> 200
```

`approve-market` só aceita lead `establishment` ainda não convertido (senão
**409**). Numa única transação:

1. cria o `Establishment` com `slug` gerado do nome, `status=pending` e `lead_id`
2. cria o convite `owner`
3. marca o lead como `converted`

Resposta:

```json
{
  "establishment": { "id": 1, "slug": "mercado-sao-jose", "status": "pending", "cnpj": null },
  "invitation": { "id": 1, "role": "owner", "status": "pending", "expires_at": "..." },
  "invite_url": "https://poupefruti.vercel.app/convite#token=...",
  "expires_at": "...",
  "whatsapp_message": "Olá, Maria! ...",
  "aviso": "Guarde este link agora: ele não será exibido novamente."
}
```

**O link em claro aparece só nessa resposta.** Para mandar outro (link perdido
ou expirado), use `POST /establishments/<id>/invitations`, que **revoga** os
pendentes do mesmo papel.

Detalhes de segurança:

- token gerado com `secrets.token_urlsafe(32)`; no banco fica só o SHA-256
- o link usa **fragmento** (`#token=`), que não vai para o servidor nem para o `Referer`
- validade padrão de 7 dias (`services/invitations.py::DIAS_VALIDADE`)
- `services/invitations.py` não envia e-mail — a troca para o Resend depois é
  trocar uma função, sem tocar nas rotas
- `convert_lead` continua existindo, mas **só para lead do tipo `person`**; para
  mercado ele recusa e aponta para o `approve-market`

### Endpoints admin

| Método   | Rota                          | Descrição                          |
| -------- | ----------------------------- | ---------------------------------- |
| POST     | `/api/v1/admin/auth/login`    | Login do admin (limite de 5/min por IP) |
| GET      | `/api/v1/admin/auth/me`       | Administrador do token             |
| GET      | `/api/v1/admin/leads`         | Listagem com filtros e paginação    |
| GET      | `/api/v1/admin/leads/<id>`    | Detalhe do lead                    |
| PATCH    | `/api/v1/admin/leads/<id>/status` | Alteração de status              |
| POST     | `/api/v1/admin/leads/<id>/convert` | Conversão em usuário (só `person`) |
| POST     | `/api/v1/admin/leads/<id>/approve-market` | Aprova mercado e gera convite |
| POST     | `/api/v1/admin/establishments/<id>/invitations` | Gera convite de `owner` |
| GET      | `/api/v1/admin/establishments` | Lista mercados com status do convite|
| GET      | `/api/v1/admin/establishments/<id>` | Detalhe, membros e convites     |
| PATCH    | `/api/v1/admin/establishments/<id>/status` | Suspende ou reativa     |
| GET      | `/api/v1/admin/stats`         | Estatísticas                       |
| GET      | `/api/v1/admin/export`        | Exportação CSV                     |

**Parâmetros de** `GET /api/v1/admin/leads` (e de `/export`, que aceita os mesmos
filtros): `status`, `type`, `bairro`, `search` (nome/e-mail/telefone), `page`,
`per_page` (padrão 20), `sort` (padrão `-created_at`).

Exemplo: `/api/v1/admin/leads?status=new&type=person&search=ana&page=1&per_page=20`

### Aprovação de lead, mapeada a partir do enum `status`

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

## Painel do mercado (RBAC)

Quem entra no painel do mercado é `User` com vínculo em `establishment_members`.
A permissão vive num dicionário único em `app/rbac.py` e é **consultada no banco
a cada requisição** — nada de papel dentro do token. Assim remover ou rebaixar
alguém vale na hora, com o token que a pessoa já tinha.

| Ação                                | owner | manager | staff |
| ----------------------------------- | :---: | :-----: | :---: |
| Ver perfil e equipe                 |  sim  |   sim   |  sim  |
| Ver cestas                          |  sim  |   sim   |  sim  |
| Editar perfil                       |  sim  |   sim   |  não  |
| Publicar / despublicar página       |  sim  |   sim   |  não  |
| Gerenciar cestas (criar/editar)     |  sim  |   sim   |  não  |
| Ajustar estoque de cesta            |  sim  |   sim   |  sim  |
| Gerar link para `staff`             |  sim  |   sim   |  não  |
| Gerar link para `manager`           |  sim  |   não   |  não  |
| Gerar link para `owner`             |  não  |   não   |  não  |
| Alterar papel                       |  sim  | só equipe|  não  |
| Remover membro                      |  sim  | só equipe|  não  |

Staff ajusta estoque (o que faz no balcão) mas **nunca cria, edita campos nem
encerra** uma cesta: quem monta a oferta é gerência.

Regras adicionais:

- **Ninguém concede papel maior que o próprio**, e a regra vem da própria matriz:
  um papel é concedível exatamente quando o ator pode *convidar* alguém com ele
  (`pode_conceder_papel`). Isso garante que o caminho do convite e o da
  alteração de papel (`PATCH /members/<user_id>`) nunca divirjam.

  | quem atribui | → `owner` | → `manager` | → `staff` |
  | ------------ | :-------: | :---------: | :-------: |
  | owner        |    não    |     sim     |    sim    |
  | manager      |    não    |    **não**  |    sim    |
  | staff        |    não    |     não     |    não    |

  Ou seja: **manager não promove ninguém a manager.** Só o owner cria manager, e
  isso vale tanto por convite quanto por alteração de papel.
- **O último owner não pode ser removido nem rebaixado** (409 `last_owner`). A
  contagem é feita com `FOR UPDATE` sobre as linhas de owner, para que duas
  requisições simultâneas não leiam "1 owner" ao mesmo tempo. O SQLite ignora
  esse lock, então a garantia só vale no PostgreSQL.
- **`owner` não é concedível por membro nenhum.** Só a administração da plataforma
  cria owner, pelo fluxo de aprovação do convite.
- **Quem não é membro recebe 404**, não 403 — não confirmamos a existência de um
  recurso para quem não tem relação com ele.
- **Mercado suspenso** some da rota pública e bloqueia as escritas do painel
  dele; a leitura continua. O admin suspende e reativa.

### Endpoints do painel do mercado

Prefixo `/api/v1/establishments/<id>`, com JWT de **usuário**.

| Método   | Rota                            | Permissão        | Sucesso | Erros                    |
| -------- | ------------------------------- | ---------------- | ------- | ------------------------ |
| GET      | `/profile`                      | `ver_perfil`     | 200     | 401, 403, 404            |
| PATCH    | `/profile`                      | `editar_perfil`  | 200     | 400, 401, 403, 404, 409  |
| POST     | `/publish`                      | `publicar`       | 200     | 400, 403                 |
| POST     | `/unpublish`                    | `publicar`       | 200     | 403                      |
| GET      | `/members`                      | `ver_equipe`     | 200     | 401, 403, 404            |
| POST     | `/invitations`                  | `convidar_staff` | 201     | 400, 401, 403, 404       |
| DELETE   | `/invitations/<invitation_id>`   | `convidar_staff` | 200     | 401, 403, 404, 409       |
| PATCH    | `/members/<user_id>`            | `alterar_papel`  | 200     | 400, 401, 403, 404, 409  |
| DELETE   | `/members/<user_id>`            | `remover_membro` | 200     | 401, 403, 404, 409       |

O `POST /invitations` recebe `{ "role": "manager" }` ou `{ "role": "staff" }` e
devolve o `invite_url` **uma única vez**. `GET /members` lista membros e convites
com status (`pending`, `accepted`, `expired`, `revoked`) — **nunca** o
`token_hash`. Remover membro apaga o vínculo; o `User` continua existindo.

Publicar exige **nome, endereço, telefone e bairro** preenchidos, e é o momento
em que o mercado sai de `pending` para `active`. O bairro entra na lista de
obrigatórios porque é ele que permite a pessoa ver as cestas do mercado no filtro
por bairro da landing: publicado sem bairro, o mercado não contaria em lugar
nenhum.

### `bairro` no perfil

`GET /profile` devolve o `bairro` do mercado e, no mesmo corpo, a lista
canônica no campo `bairros` (o painel monta o `<select>` a partir dela, sem
guardar cópia no HTML).

`PATCH /profile` aceita `bairro`:

| Valor enviado        | Resultado                                    |
| -------------------- | -------------------------------------------- |
| `"Centro"`, `"centro"`, `"  centro  "` | grava `"Centro"` (texto canônico) |
| valor fora da lista  | 400 `invalid_bairro`                         |
| `""`                 | limpa o campo (volta a ser nulo)              |

### Rota pública

| Método | Rota                                        | Auth | Sucesso | Erros        |
| ------ | ------------------------------------------- | ---- | ------- | ------------ |
| GET    | `/api/v1/public/establishments/<slug>`      | não  | 200     | 404, 429     |
| GET    | `/api/v1/public/stats`                      | não  | 200     | 400, 429     |

Só responde para mercado **publicado e ativo**, e devolve `name`, `address`,
`phone`, `opening_hours` e a lista `baskets`. Nunca CNPJ, `lead`, membros, `slug`
nem ids internos. Mercado inexistente, não publicado ou suspenso devolvem o
mesmo 404 — o campo `establishment` tem exatamente 5 chaves, e há teste
garantindo isso.

### `/api/v1/public/stats` — cestas hoje em Campinas

Conta quantas cestas existem **agora**, no total da cidade ou em um bairro.
Sem geolocalização e sem serviço externo: a pessoa escolhe o bairro, a API conta.

```bash
curl "$API/api/v1/public/stats"
curl "$API/api/v1/public/stats?bairro=Centro"
```

```json
{
  "markets": 2,
  "baskets": 5,
  "units": 11,
  "by_type": { "frutas": 3, "legumes": 2 },
  "bairro": "Centro",
  "updated_at": "2026-10-05T12:00:00+00:00"
}
```

| Campo        | Significado                                                      |
| ------------ | ---------------------------------------------------------------- |
| `markets`    | mercados distintos com pelo menos uma cesta contável              |
| `baskets`    | ofertas (linhas de cesta) contáveis                               |
| `units`      | soma de `quantity`: cestas que a pessoa conseguiria retirar       |
| `by_type`    | `baskets` separado em `frutas` e `legumes` (as duas chaves sempre) |
| `bairro`     | o bairro filtrado, ou `null` quando é o total da cidade            |
| `updated_at` | momento da contagem, em UTC, com fuso                             |

O que **entra** na contagem: mercado publicado **e** ativo, cesta `active`,
`quantity > 0` e `pickup_ends_at` ainda no futuro. Mercado despublicado,
suspenso ou `pending`, cesta pausada, encerrada, expirada ou sem estoque ficam
de fora. A comparação de tempo é feita pelo banco (`func.now()`), como no resto
do projeto — comparar em Python quebraria com o datetime sem fuso do SQLite.

O que **nunca sai**: nome do mercado, id, slug, endereço, telefone, CNPJ ou
qualquer nome de bairro além do que a pessoa pediu. Um bairro que não existe na
lista devolve 400 `invalid_bairro` — devolver zero faria a landing anunciar "0
cestas no seu bairro" quando o problema é o filtro.

**Cache em memória de 60 segundos**, por chave de requisição (caminho + query
inteira), em `app/public/cache.py`. Sem Redis e sem dependência nova; só 200 entra
no cache, erro passa direto. O cache vive em `app.extensions`, é por processo e
não sobrevive a restart — ver os comentários do arquivo para o que isso significa
com mais de um worker. **Rate limit:** 30/min por IP. CORS é o mesmo de
`/api/*`.

### Cestas da sobra do dia

O mercado cadastra as cestas de frutas ou legumes que está oferecendo, com
desconto. Ainda **não há pedidos nem pagamento** — a cesta é a oferta e o
estoque. As cestas públicas são só as que dá para retirar agora: `status`
`active`, `quantity > 0` e `pickup_ends_at` no futuro.

| Método | Rota                                        | Permissão          | Descrição                       |
| ------ | ------------------------------------------- | ------------------ | ------------------------------- |
| GET    | `/api/v1/establishments/<id>/baskets`       | `ver_cestas`       | Lista, por retirada mais próxima |
| POST   | `/api/v1/establishments/<id>/baskets`       | `gerenciar_cestas` | Cria                            |
| GET    | `/api/v1/establishments/<id>/baskets/<id>`  | `ver_cestas`       | Detalhe                         |
| PATCH  | `/api/v1/establishments/<id>/baskets/<id>`  | `gerenciar_cestas` | Edita qualquer campo, inclusive o status |
| PATCH  | `/api/v1/establishments/<id>/baskets/<id>/quantity` | `ajustar_estoque` | `{"quantity": N}` ou `{"delta": ±N}` |

`GET` aceita `?status=active|paused|closed`.

**Não existe DELETE.** Encerrar é `PATCH` com `{"status": "closed"}`, e uma cesta
encerrada não volta a ativa (409 `basket_closed`) — é o que preservará o
histórico quando os pedidos passarem a referenciar a cesta.

O ajuste de estoque é **atômico**: um `UPDATE ... WHERE quantity + delta >= 0`.
Dois balcões vendendo a última cesta ao mesmo tempo não conseguem zerar duas
vezes; ler-e-gravar deixaria passar.

### Validação das cestas

| Campo                 | Regra                                                    |
| --------------------- | -------------------------------------------------------- |
| `type`                | `frutas` ou `legumes`                                     |
| `price_cents`         | 100 a 50000 (R$ 1,00 a R$ 500,00)                        |
| `weight_grams`        | 500 a 30000                                              |
| `quantity`            | 0 a 999                                                  |
| `retail_value_cents`  | opcional; se enviado, **maior** que `price_cents`          |
| `pickup_starts_at`    | ISO 8601, com ou sem fuso                                 |
| `pickup_ends_at`      | depois do início, no futuro, janela de **até 7 dias**      |
| `name`                | até 80 caracteres, aparado, sem HTML                       |
| `notes`               | até 280 caracteres, aparado, sem HTML                      |

Um mercado pode ter no máximo **50 cestas ativas ou pausadas** (409
`too_many_baskets`). Cestas `closed` não contam.

Erros saem no formato `{"error": {"code", "message"}}`, com código por campo:
`invalid_type`, `invalid_price_cents`, `invalid_quantity`,
`invalid_weight_grams`, `invalid_retail_value_cents`, `invalid_pickup_window`,
`pickup_window_too_long`, `pickup_in_past`, `invalid_status`, `too_many_baskets`,
`insufficient_stock`, `basket_closed`.

As respostas incluem `discount_percent` (só quando há `retail_value_cents`).

### `description` do perfil está obsoleto

O campo `description` do perfil **saiu do fluxo**: não é mais aceito no
`PATCH /profile`, não aparece no `GET /profile` e sumiu da rota pública — a área
de cestas o substituiu.

**A coluna continua no banco.** Nada é apagado aqui; a remoção física fica para
uma etapa posterior, com backup.

### Administração do mercado

| Método | Rota                                             | Auth  | Descrição                  |
| ------ | ------------------------------------------------ | ----- | -------------------------- |
| GET    | `/api/v1/admin/establishments`                   | admin | Lista com status do convite |
| GET    | `/api/v1/admin/establishments/<id>`               | admin | Detalhe, membros e convites |
| POST   | `/api/v1/admin/establishments/<id>/invitations`   | admin | Gera convite de `owner`     |
| PATCH  | `/api/v1/admin/establishments/<id>/status`        | admin | Suspende ou reativa         |

`PATCH /api/v1/admin/establishments/<id>/status` com `{"status": "..."}` no
**corpo JSON** (query string não é lida):

```bash
curl -X PATCH "$API/api/v1/admin/establishments/1/status" \
  -H "Authorization: Bearer $ADMIN" \
  -H 'Content-Type: application/json' \
  -d '{"status":"suspended"}'
```

| Valor       | Efeito                                                                 |
| ----------- | ---------------------------------------------------------------------- |
| `suspended` | some da rota pública e bloqueia as escritas do painel (leitura continua) |
| `active`    | reativa; **mantém** o `is_published` que já estava                       |
| `pending`   | volta ao estado inicial, útil para devolver um recém-aprovado sem apagar dados |

Suspender é decisão da administração e, ao contrário da publicação, **não é
revertido pelo owner do mercado** — o `POST /publish` recusa mercado suspenso.

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
| `insufficient_permissions` | 403  | membro do mercado sem permissão para a ação   |
| `establishment_suspended` | 403   | escrita bloqueada em mercado suspenso         |
| `cannot_grant_higher_role` | 403  | tentando conceder papel acima do próprio       |
| `last_owner`             | 409    | remover ou rebaixar o único dono              |
| `cnpj_already_used`      | 409    | CNPJ já cadastrado em outro estabelecimento   |
| `invalid_cnpj`           | 400    | CNPJ com dígito verificador errado            |
| `invalid_bairro`         | 400    | bairro fora de `VALORES_BAIRRO` (perfil e `/public/stats`) |
| `incomplete_profile`     | 400    | faltam campos obrigatórios para publicar (nome, endereço, telefone, bairro) |
| `invalid_role`           | 400    | papel fora de owner/manager/staff             |
| `invalid_name`           | 400    | nome vazio                                   |
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

## Páginas na Vercel

O front é estático e vive em `ui/`, servido pela Vercel. Cada página tem sua
entrada em `ui/vercel.json`:

| Rota       | Arquivo             | Para quê                                        |
| ---------- | ------------------- | ----------------------------------------------- |
| `/`        | `ui/index.html`     | landing page pública                            |
| `/admin`   | `ui/admin.html`     | painel de leads (exige login de admin)          |
| `/convite` | `ui/convite.html`   | criação de conta pelo convite (pública)         |
| `/painel`  | `ui/painel.html`    | painel do mercado (exige login de usuário)      |

`admin.html` e `convite.html` são `noindex, nofollow`. A URL da API fica na
constante `API_URL`, no topo do `<script>` de cada página.

### Acessando o painel admin

```
https://poupefruti.vercel.app/admin
```

1. Rode `create-admin` no ambiente da API (uma vez).
2. Acesse `/admin` e faça login com o e-mail/senha cadastrados.
3. O token fica em `sessionStorage` (nunca em `localStorage`); em 401/403 o
   painel volta para a tela de login.

A origem da Vercel precisa estar em `CORS_ORIGINS` (já vem
`https://poupefruti.vercel.app` como padrão).

## Fluxo de onboarding de um mercado

Do clique no admin até a conta criada:

```
1. ADMIN: aprova o lead
   POST /api/v1/admin/leads/<id>/approve-market
   → devolve invite_url (uma única vez), expires_at e a mensagem sugerida

2. ADMIN: copia o link e envia pelo WhatsApp
   A mensagem sugerida já vem pronta em whatsapp_message.
   O link é  APP_BASE_URL + "/convite#token=<token>"
   O painel também tem o botão "Enviar por WhatsApp", que abre o wa.me com a
   mensagem e o link preenchidos.

3. RESPONSÁVEL: abre o link em /convite
   A página lê o token de location.hash, apaga o hash da barra de endereço
   (history.replaceState) e chama POST /api/v1/invitations/validate.
   - válido   → mostra "Você foi convidado para <mercado> como <papel>"
   - inválido → mensagem genérica, sem dizer o motivo

4. RESPONSÁVEL: preenche nome, e-mail e senha
   Validação no cliente (e-mail, tamanho mínimo, confirmação) e de novo no
   backend. Em caso de sucesso, POST /api/v1/invitations/accept devolve o
   access_token, guardado em sessionStorage.

5. Tela de sucesso: "Conta criada! Em breve você poderá acessar o painel do
   seu mercado".
```

Detalhes que importam:

- o link usa **fragmento** (`#token=`), que não vai para o servidor nem para o
  `Referer`; a página também declara `<meta name="referrer" content="no-referrer">`
- o token em claro **não é recuperável** depois: só existe na resposta da
  aprovação. Se o responsável perder o link, o admin gera outro em
  `POST /api/v1/admin/establishments/<id>/invitations`
- token inválido, expirado, usado e revogado devolvem sempre a **mesma** mensagem
- `APP_BASE_URL` precisa apontar para o domínio certo, senão o convite leva a
  uma página inexistente

### O que ainda não existe

- Envio do convite por e-mail (Resend). `services/invitations.py` já devolve o
  link separado da rota, então a integração é uma função nova.

## Painel do mercado (`/painel`)

Onde o dono, gerente e equipe do mercado trabalham. É uma página estática, sem
framework, que fala com `/api/v1/establishments/*` usando o token guardado em
`sessionStorage` pelo aceite do convite.

```
https://poupefruti.vercel.app/painel
```

Como entra: **tela de login** com e-mail e senha (`POST /api/v1/auth/login`). O
`access_token` fica em `sessionStorage` e nunca em `localStorage`. Quem chegou
pelo `/convite` cai no painel direto, porque o aceite do convite já deixou o
token guardado.

Quem entra sem token válido cai no login. Um **401 em qualquer chamada** (token
expirado) limpa o `sessionStorage` e volta para o login com o aviso "Sua sessão
expirada. Entre novamente." O botão **Sair**, no cabeçalho, faz o mesmo de forma
explícita. Se a conta não estiver ligada a nenhum mercado, a tela explica que é
preciso falar com o PoupeFruti — quem convida é o admin, pelo fluxo de aprovação.

### O que a tela faz

| Área            | Comportamento                                                        |
| --------------- | -------------------------------------------------------------------- |
| Seleção         | com mais de um vínculo, lista os mercados; com um só, abre direto     |
| Abas            | **Perfil**, **Cestas** e Equipe; setas do teclado trocam de aba       |
| Perfil          | nome, CNPJ, endereço, telefone, **bairro** e horário                |
| Publicação      | publicar/despublicar; publicar promove `pending` → `active`          |
| Cestas          | lista em cartões, filtro por status, ajuste rápido de quantidade      |
| Equipe          | lista membros com papel e ações por linha                             |
| Convites        | gera link (uma vez só) e revoga pendentes                            |

### Cestas na tela

Cada cartão mostra o tipo (🍎 frutas / 🥕 legumes), o nome — ou "Cesta de
frutas"/"Cesta de legumes" quando vazio —, "cerca de X kg", o preço em R$, o
varejo riscado com selo de desconto, a quantidade, a janela de retirada em
horário de Brasília ("Hoje, 17h às 19h" ou "12/10, 17h às 19h") e a observação.

O badge distingue **expirada**: uma cesta `active` cuja janela já passou aparece
como expirada, porque não dá mais para retirar.

**Ajuste rápido de quantidade**: os botões − e + em cada cartão chamam
`PATCH /baskets/<id>/quantity` com `{"delta": ±1}`. A atualização é otimista — o
número muda na hora — e **volta ao valor anterior** se a API recusar, mostrando o
motivo (por exemplo, estoque insuficiente). Depois de qualquer ajuste, o número
exibido é o que o servidor devolveu.

**Formulário** (só owner e manager): tipo em cartões de opção, nome opcional,
peso em kg (convertido para gramas), preço e varejo em R$ aceitando vírgula,
quantidade, janela de retirada e observação com contador de caracteres. O
desconto aparece ao vivo, e os placeholders sugerem os valores da landing
(frutas: ~4 kg, R$ 35, varejo R$ 70; legumes: ~4 kg, R$ 25, varejo R$ 50).

O dinheiro é convertido **por texto**, nunca por `parseFloat`: em ponto flutuante
`0.1 + 0.2` daria 3003 centavos em vez de 3000. O `datetime-local` é
interpretado como Brasília e enviado em ISO 8601 com o offset `-03:00`.

### Quem vê o quê na tela

| Papel   | Aba Cestas                                                 |
| ------- | ---------------------------------------------------------- |
| owner    | lista, cria, edita, pausa, encerra e ajusta estoque         |
| manager  | lista, cria, edita, pausa, encerra e ajusta estoque         |
| staff    | **só a lista e o ajuste de quantidade** — sem criar/editar/encerrar |

Essas regras escondem botões; o backend continua sendo a autoridade. Se a tela
mostrar algo que a API recusar, o erro aparece na tela.

### Como a tela reage ao backend

A interface **espelha** o backend para esconder botões inúteis, mas quem decide
é a API — se a tela mostrar algo que a API recusa, o erro aparece.

- **404 em rota do mercado** significa "não existe **ou** você não tem acesso".
  O painel não tenta adivinhar qual dos dois é: volta para a seleção de
  mercados e recarrega os vínculos.
- **Mercado suspenso** (`status: suspended`): mostra um aviso fixo no topo
  ("Este mercado está suspenso. Fale com o PoupeFruti."), desabilita os campos
  e esconde os botões de edição, convite e criação de cesta. **A leitura
  continua** — perfil, equipe e a lista de cestas continuam visíveis, com o
  ajuste de estoque desabilitado.
- **Ações por papel** (mesma tabela da seção de RBAC):

  | quem         | convida gerente | convida equipe | age sobre |
  | ------------ | :-------------: | :------------: | --------- |
  | owner        |      sim       |      sim       | todos     |
  | manager      |      não       |      sim       | só equipe |
  | staff        |      não       |      não       | ninguém   |

- **Ninguém atribui `owner` pelo painel.** Só a administração da plataforma cria
  owner, pelo fluxo de aprovação.
- **Remover o último owner** devolve 409 `last_owner`; a tela mostra "Não é
  possível remover ou rebaixar o único dono do estabelecimento."
- **`is_me: true`** impede oferecer "alterar papel" e "remover" sobre si mesmo —
  um autopromoção é sempre engano, e o backend só recusaria quando é o último
  dono. Se o backend recusar mesmo assim, o erro aparece.
- **Publicar com perfil incompleto** devolve 400 `incomplete_profile` com a lista
  do que falta; a tela repete a mensagem e marca os campos correspondentes. O
  bairro entra nessa lista: `CAMPOS_PUBLISH` no painel espelha a constante de
  mesmo nome em `app/establishments/routes.py`.
- **O `<select>` de bairro não guarda lista no HTML.** As opções são montadas a
  partir do campo `bairros` do `GET /profile`. Se a lista não vier (backend
  velho, erro de rede), a tela mantém pelo menos a opção já salva — perder um
  bairro que a pessoa preencheu seria pior do que um `<select>` curto.
- Papéis aparecem traduzidos: `owner` = dono, `manager` = gerente,
  `staff` = equipe.

## Configuração

Copie `.env.example` para `.env` e preencha.

| Variável                   | Obrigatória | Descrição                                      |
| -------------------------- | ----------- | ---------------------------------------------- |
| `DATABASE_URL`             | sim         | `postgresql+psycopg://user:senha@host:5432/db` |
| `SECRET_KEY`               | produção    | Segredo da aplicação                           |
| `JWT_SECRET_KEY`           | produção    | Segredo de assinatura do JWT                   |
| `CORS_ORIGINS`             | sim         | Origens liberadas, separadas por vírgula      |
| `APP_BASE_URL`             | produção    | Base pública da SPA, usada nos links de convite |
| `PROXY_FIX_X_FOR`          | produção    | Saltos de proxy à frente do app (Render = `1`) |
| `LOGIN_RATE_LIMIT`         | não         | Limite de login por IP (padrão `10 per minute;50 per hour`) |
| `LOGIN_EMAIL_RATE_LIMIT`   | não         | Limite de login por e-mail (padrão `6 per minute;20 per hour`) |
| `JWT_ACCESS_TOKEN_EXPIRES` | não         | Expiração do token em segundos (padrão 3600)  |
| `MIN_PASSWORD_LENGTH`      | não         | Tamanho mínimo da senha (padrão 8)             |
| `FLASK_ENV`                | não         | `development` (padrão) ou `production`         |

### ProxyFix e o rate limit

O limite de login usa o IP como chave. **Atrás de um proxy, `request.remote_addr`
é o IP do proxy** — sem configurar `ProxyFix`, todas as pessoas cairiam no mesmo
contador, e uma só pessoa batendo no limite travaria o login de todo mundo.

Por isso o ProxyFix **não vem ligado**: confiar cegamente em `X-Forwarded-For`
deixaria um cliente falsificar o IP e escapar do limite (ou jogá-lo contra outra
pessoa). Ligue explicitamente, com a contagem de saltos exata:

| Onde roda                 | `PROXY_FIX_X_FOR` |
| ------------------------- | ----------------- |
| Render → app              | `1`               |
| Render + CDN/proxy extra  | `2`               |
| Local (sem proxy)         | deixe em `0`      |

`ProductionConfig` **recusa subir** com valor acima de 2, porque a partir dai o app
passaria a confiar em cabeçalho que o cliente pode forjar.

### Trava de segurança nos testes

`create_app()` sob pytest recusa qualquer banco que não seja SQLite. Existe
porque um teste já chegou a usar `create_app("development")` — que lê o
`DATABASE_URL` do `.env` — e o `db.drop_all()` do teardown **apagou as tabelas
do banco de produção**. A falha agora acontece na criação do app, com mensagem
clara, antes de qualquer conexão.

`APP_BASE_URL` entra nos links como `APP_BASE_URL + "/convite#token=..."`. Em
produção aponte para o domínio do painel (ex.: `https://poupefruti.vercel.app`).
Se ficar errado, o convite aponta para uma página inexistente.

Em `production`, a aplicação recusa subir com segredos que ainda comecem com `dev-`.

No Neon, use a connection string **com `-pooler`** e mantenha
`SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 300}`, já
configurado em `app/config.py`.

Em `production`, o `DATABASE_URL` é normalizado automaticamente: o driver `psycopg`
é adicionado (`postgresql+psycopg://`) e `sslmode=require` é forçado na query string.
URLs fora desse formato — ou sem `psycopg`, ou sem `sslmode` — são corrigidas em tempo
de inicialização, antes de qualquer conexão.

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
| 004     | `004_invitations_and_establishment_onboarding.py` | tabela `invitations`; em `establishments`: `slug`, `status`, `phone`, `description`, `opening_hours`, `is_published`; `cnpj` passa a anulável |
| 005     | `005_add_baskets.py`         | tabela `baskets` (oferta da sobra do dia)                  |
| 006     | `006_add_establishment_bairro.py` | em `establishments`: `bairro VARCHAR(80)` anulável + índice `ix_establishments_bairro` |

### Banco sem Alembic (SQL manual)

Cada migração com efeito estrutural tem um `NNN_manual.sql` ao lado, para banco
que foi montado à mão e **não tem tabela `alembic_version`**. São idempotentes
(rodar duas vezes não muda nada) e estão inteiros em `BEGIN`/`COMMIT`, então um
erro no meio não deixa metade aplicada:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/versions/006_manual.sql
```

| Arquivo         | O que faz                                             |
| --------------- | ----------------------------------------------------- |
| `004_manual.sql`| onboarding + `invitations`                            |
| `005_manual.sql`| tabela `baskets`                                      |
| `006_manual.sql`| `establishments.bairro` + `ix_establishments_bairro`   |

O `006_manual.sql` **não** cria CHECK com a lista de bairros: essa lista vai
crescer e um CHECK congelado passaria a rejeitar bairros novos. A validação fica
na API (`app/validators.py`). Cada arquivo termina com consultas de verificação
(`information_schema`, `pg_indexes`) que só leem — pode rodar depois de aplicar.

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
- `tests/test_marketplace.py` — aprovação, slug e colisão, token em claro não
  persistido, aceite atômico, revogação, respostas genéricas, rate limit e
  autorização
- `tests/test_rbac.py` — matriz de permissões, escalada de privilégio por papel
  (incluindo manager tentando promover a manager), isolamento entre mercados
  (404), efeito imediato de remoção/rebaixamento, regra do último owner com
  `FOR UPDATE`, suspensão e vazamento de dados na rota pública
- `tests/test_baskets.py` — CRUD de cestas, validação de cada limite, RBAC por
  papel (staff só ajusta estoque), ajuste atômico sem passar de zero, limite de
  50 cestas, e o que a rota pública mostra e não mostra
- `tests/test_timezones.py` — datas com e sem fuso: `esta_pendente`, status do
  convite, `validar_convite` e `aceitar_convite` nos dois formatos, mais uma
  varredura que falha se `datetime.utcnow` voltar
- `tests/test_cors_errors.py` — CORS (404/405/429/500) e o handler de 500 que não
  pode levantar ao desfazer a sessão
- `tests/test_login_rate_limit.py` — limites por IP e por e-mail, e o ProxyFix
- `tests/test_enum_column_length.py` — o maior valor de cada enum cabe na coluna,
  e a migração 004 bate com o model
- `tests/test_public_stats.py` — `/api/v1/public/stats`: o que conta e o que não
  conta (despublicado, suspenso, `pending`, cesta pausada/encerrada/expirada/sem
  estoque), filtro por bairro e bairro inválido (400), ausência de nome/id/
  endereço/telefone no payload, cache de 60 s separado por bairro e expirando,
  datas com e sem fuso, rate limit e a lista única de bairros

## Estilos na landing (`ui/index.html`)

Duas partes do front são geradas por JS a partir da API:

- **"Cestas hoje em Campinas"** (`GET /api/v1/public/stats`): carrega depois da
  página, em `requestIdleCallback` (ou `setTimeout`), com timeout de 4 s por
  `AbortController` e **sem retentativa**. Falhou, veio zerado ou veio menos de 1
  cesta → a seção fica escondida: "0 cestas" é pior que não mostrar nada, porque
  parece erro. O número entra por `textContent` (nunca `innerHTML`), o valor tem
  `aria-live="polite"`, o `<select>` tem `<label>`, e a animação de entrada
  só existe sob `prefers-reduced-motion: no-preference`.
- **`<select>` de bairro do painel** (`painel.html`): as opções vêm do campo
  `bairros` do `GET /profile`, e não de uma lista copiada no HTML.

Os dois `<select>` de bairro dos formulários da landing são **estáticos** e têm
de continuar idênticos entre si e com `VALORES_BAIRRO` — há verificação em
`tests/test_public_stats.py::TestListaDeBairros` para a lista do backend, e a
paridade dos dois `<select>` é mantida à mão.

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

### Onboarding de mercados

```bash
# Aprovar lead de mercado (201) — devolve o link UMA única vez
LINK=$(curl -s -X POST http://localhost:5000/api/v1/admin/leads/2/approve-market \
  -H "Authorization: Bearer $ADMIN" | python3 -c 'import sys,json;print(json.load(sys.stdin)["invite_url"])')

TOKEN=${LINK#*#token=}
echo "$LINK"

# Conferir o token (200)
curl -s -X POST http://localhost:5000/api/v1/invitations/validate \
  -H 'Content-Type: application/json' -d "{\"token\":\"$TOKEN\"}"

# Responsável cria a conta (201) e já recebe access_token
curl -i -X POST http://localhost:5000/api/v1/invitations/accept \
  -H 'Content-Type: application/json' \
  -d "{\"token\":\"$TOKEN\",\"name\":\"Maria Souza\",\"email\":\"maria@exemplo.com\",\"password\":\"senha1234\"}"

# Rejeitar (400 invalid_invitation) — o mesmo corpo para token usado/expirado/revogado
curl -i -X POST http://localhost:5000/api/v1/invitations/accept \
  -H 'Content-Type: application/json' \
  -d "{\"token\":\"$TOKEN\",\"name\":\"X\",\"email\":\"x@exemplo.com\",\"password\":\"senha1234\"}"

# Gerar novo link, revogando o pendente (201)
curl -i -X POST http://localhost:5000/api/v1/admin/establishments/1/invitations \
  -H "Authorization: Bearer $ADMIN" \
  -H 'Content-Type: application/json' -d '{"role":"owner"}'

# Ver convite e membros (200)
curl -i http://localhost:5000/api/v1/admin/establishments/1 \
  -H "Authorization: Bearer $ADMIN"
```

### Cestas hoje (público)

```bash
# Total de Campinas (200) — é o que a landing mostra no bloco "Cestas hoje"
curl -s http://localhost:5000/api/v1/public/stats

# Só de um bairro (200) — "centro" e "Centro" são o mesmo bairro
curl -s "http://localhost:5000/api/v1/public/stats?bairro=Centro"

# Bairro fora da lista (400 invalid_bairro)
curl -i "http://localhost:5000/api/v1/public/stats?bairro=Vila%20Nova"
```

Guarde o `bairro` no perfil antes de publicar — sem ele o mercado não aparece em
nenhum filtro:

```bash
curl -i -X PATCH http://localhost:5000/api/v1/establishments/1/profile \
  -H "Authorization: Bearer $DONO" -H 'Content-Type: application/json' \
  -d '{"name":"Mercado","address":"Rua A, 1","phone":"19988880000","bairro":"Centro"}'
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
- Convites: só o SHA-256 do token é guardado; o valor em claro existe apenas no
  retorno da criação. O link usa fragmento (`#token=`), que não vai para o
  servidor nem para o `Referer`. Novo convite revoga os pendentes do mesmo papel.
- O aceite do convite é atômico (UPDATE condicional + `rowcount`), então dois
  aceites simultâneos criam uma única conta.
- Taxas no público: `validate` 30/min, `accept` 10/min, login de admin 5/min.
- `GET /api/v1/public/stats` só devolve números agregados e o bairro pedido.
  Não sai nome, id, slug, endereço, telefone, CNPJ nem nome de mercado: quem
  pergunta "quantas cestas têm no meu bairro?" não precisa saber de quem são as
  cestas, e um mercado suspenso ou despublicado não pode entrar na contagem.
  Filtro de bairro é de lista fechada (400 no bairro desconhecido) para a landing
  não anunciar "0 cestas" por causa de um erro de digitação.
