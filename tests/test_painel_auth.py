"""Autenticação dos endpoints que o painel do mercado usa.

O painel é uma página estática no Vercel: o `sessionStorage` do navegador
guarda o token e cada clique manda um `Authorization: Bearer`. Se um desses
endpoints deixar de exigir token, o painel deixa de ter porta de entrada —
e a falha aparece como "a tela não abriu", longe do lugar do problema.

Esta lista é a transcrição das chamadas que `ui/painel.html` faz. Se a tela
mudar (um endpoint novo, um caminho novo), o teste precisa mudar junto: é
a forma de a lista não envelhecer em silêncio.

Sem token, a resposta tem de ser 401 — não 403, não 404 e não 200 com corpo
vazio. Qualquer outro status significa que o endpoint respondeu sem saber quem
é quem está perguntando.
"""

import pytest

# (método, caminho, corpo) — a ordem é a em que o painel chama.
ROTAS_DO_PAINEL = [
    ("GET", "/api/v1/auth/me", None),
    ("GET", "/api/v1/establishments/1/profile", None),
    ("PATCH", "/api/v1/establishments/1/profile", {"name": "Mercado X"}),
    ("POST", "/api/v1/establishments/1/publish", {}),
    ("POST", "/api/v1/establishments/1/unpublish", {}),
    ("GET", "/api/v1/establishments/1/members", None),
    ("PATCH", "/api/v1/establishments/1/members/2", {"role": "staff"}),
    ("DELETE", "/api/v1/establishments/1/members/2", None),
    ("POST", "/api/v1/establishments/1/invitations", {"email": "a@b.com", "role": "staff"}),
    ("DELETE", "/api/v1/establishments/1/invitations/3", None),
    # Fora do painel, mas no mesmo formato de tela (admin): mesma exigência.
    ("POST", "/api/leads/1/convert", {"password": "abcdefgh"}),
    ("POST", "/api/v1/admin/leads/1/convert", {"password": "abcdefgh"}),
]

# Rotas públicas: continuam abertas, senão o painel nem consegue entrar.
ROTAS_PUBLICAS = [
    ("POST", "/api/leads/cliente", {"name": "Ana", "bairro": "Centro"}),
    ("POST", "/api/leads/mercado", {"name": "Bruno", "bairro": "Centro"}),
]


def _chamar(client, metodo, caminho, corpo, cabecalhos=None):
    if corpo is None:
        return client.open(caminho, method=metodo, headers=cabecalhos or {})
    return client.open(caminho, method=metodo, json=corpo, headers=cabecalhos or {})


@pytest.mark.parametrize("metodo,caminho,corpo", ROTAS_DO_PAINEL)
def test_sem_token_responde_401(app, metodo, caminho, corpo):
    """Nenhum endpoint do painel responde a quem não se identifica."""
    client = app.test_client()
    resp = _chamar(client, metodo, caminho, corpo)

    assert resp.status_code == 401, (
        f"{metodo} {caminho} respondeu {resp.status_code} sem token"
    )


@pytest.mark.parametrize("metodo,caminho,corpo", ROTAS_DO_PAINEL)
def test_token_invalido_responde_401(app, metodo, caminho, corpo):
    """Token de mentira também é 401: o que vale é a assinatura, não o formato."""
    client = app.test_client()
    resp = _chamar(
        client, metodo, caminho, corpo,
        cabecalhos={"Authorization": "Bearer nao-e-um-jwt"},
    )

    assert resp.status_code == 401, (
        f"{metodo} {caminho} respondeu {resp.status_code} com token inválido"
    )


@pytest.mark.parametrize("metodo,caminho,corpo", ROTAS_PUBLICAS)
def test_cadastro_publico_continua_aberto(app, metodo, caminho, corpo):
    """O cadastro de lead é público por projeto; o teste trava essa decisão."""
    client = app.test_client()
    resp = _chamar(client, metodo, caminho, corpo)

    assert resp.status_code != 401, f"{metodo} {caminho} passou a exigir token"


def test_converter_lead_exige_token(app):
    """
    `POST /api/leads/<id>/convert` cria usuário e senha a partir de um lead.

    É a rota mais perigosa de bloquear errado: quem chega nela escolhe a senha
    de uma conta nova. Por isso o teste é explícito sobre ela, mesmo já estando
    na lista acima.
    """
    client = app.test_client()
    resp = client.post(
        "/api/leads/1/convert",
        json={"password": "abcdefgh", "establishment_data": {}},
    )

    assert resp.status_code == 401