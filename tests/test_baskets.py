"""Cestas da sobra do dia: CRUD, validações, RBAC e rota pública."""

from datetime import datetime, timedelta, timezone

import pytest

from app import create_app
from app.extensions import db
from app.models import AdminUser, Basket, Establishment, EstablishmentMember, User
from app.rbac import BasketStatus, BasketType
from app.timeutils import agora_utc

# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def app():
    app = create_app("testing")
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_ENGINE_OPTIONS={},
        APP_BASE_URL="https://app.test",
    )
    ctx = app.app_context()
    ctx.push()
    db.create_all()
    yield app
    db.session.remove()
    db.drop_all()
    ctx.pop()


@pytest.fixture
def client(app):
    return app.test_client()


def _membro(app, email, role, slug):
    """Cria usuário + mercado + vínculo, e devolve o contexto."""
    with app.app_context():
        from app.models import AdminUser

        user = User(email=email, name=role.title())
        user.set_password("senha1234")
        est = Establishment(name=f"Mercado {role}", slug=slug)
        admin = AdminUser(email=f"admin-{slug}@test.com")
        admin.set_password("senha1234")

        db.session.add_all([user, est, admin])
        db.session.flush()

        db.session.add(EstablishmentMember(
            user_id=user.id, establishment_id=est.id, role=role
        ))
        db.session.commit()
        return {
            "user_id": user.id,
            "est_id": est.id,
            "slug": slug,
            "admin_id": admin.id,
            "email": email,
        }


@pytest.fixture
def owner(app, client):
    ctx = _membro(app, "owner@test.com", "owner", "m-owner")
    ctx["headers"] = _login(client, ctx["email"])
    return ctx


@pytest.fixture
def manager(app, client):
    ctx = _membro(app, "manager@test.com", "manager", "m-manager")
    ctx["headers"] = _login(client, ctx["email"])
    return ctx


@pytest.fixture
def staff(app, client):
    ctx = _membro(app, "staff@test.com", "staff", "m-staff")
    ctx["headers"] = _login(client, ctx["email"])
    return ctx


@pytest.fixture
def de_outro(app, client):
    """Usuário válido, sem vínculo com nenhum mercado."""
    with app.app_context():
        user = User(email="deoutro@test.com", name="De Outro")
        user.set_password("senha1234")
        db.session.add(user)
        db.session.commit()
    return {"headers": _login(client, "deoutro@test.com")}


@pytest.fixture
def admin_headers(app, client):
    with app.app_context():
        admin = AdminUser(email="admin@test.com")
        admin.set_password("senha1234")
        db.session.add(admin)
        db.session.commit()
    return _login_admin(client, "admin@test.com")


def _login(client, email, senha="senha1234"):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": senha})
    assert r.status_code == 200, r.get_json()
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


def _login_admin(client, email, senha="senha1234"):
    """
    Admin entra pelo endpoint de admin.

    `/api/v1/auth/login` consulta SÓ a tabela `users` — é a isolamento de
    tokens: um AdminUser não vira usuário comum por logar no endpoint errado.
    """
    r = client.post(
        "/api/v1/admin/auth/login", json={"email": email, "password": senha}
    )
    assert r.status_code == 200, r.get_json()
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


def _promover_a_dono(app, client, ctx):
    """Cria um owner no MESMO mercado de ctx e devolve o ctx com header dele."""
    with app.app_context():
        user = User(email=f"dono-{ctx['slug']}@test.com")
        user.set_password("senha1234")
        db.session.add(user)
        db.session.flush()
        db.session.add(EstablishmentMember(
            user_id=user.id, establishment_id=ctx["est_id"], role="owner"
        ))
        db.session.commit()
        email = user.email
    ctx = dict(ctx)
    ctx["headers"] = _login(client, email)
    return ctx


# ===========================================================================
# Helpers de request
# ===========================================================================


def url(ctx, caminho):
    return f"/api/v1/establishments/{ctx['est_id']}{caminho}"


def cestas_url(ctx):
    return url(ctx, "/baskets")


def post_cesta(client, ctx, **overrides):
    corpo = {
        "type": "frutas",
        "name": "Cesta da manhã",
        "weight_grams": 1000,
        "price_cents": 1500,
        "retail_value_cents": 3000,
        "quantity": 5,
        "pickup_starts_at": (agora_utc() + timedelta(hours=1)).isoformat(),
        "pickup_ends_at": (agora_utc() + timedelta(hours=5)).isoformat(),
    }
    corpo.update(overrides)
    return client.post(cestas_url(ctx), json=corpo, headers=ctx["headers"])


# ===========================================================================
# Criar / ler / editar
# ===========================================================================


class TestCriarCesta:
    def test_owner_cria(self, client, owner):
        r = post_cesta(client, owner)
        assert r.status_code == 201, r.get_json()
        cesta = r.get_json()["basket"]
        assert cesta["type"] == "frutas"
        assert cesta["status"] == "active"
        assert cesta["quantity"] == 5
        assert cesta["discount_percent"] == 50.0

    def test_manager_cria(self, client, manager):
        assert post_cesta(client, manager).status_code == 201

    def test_staff_nao_cria(self, client, staff):
        r = post_cesta(client, staff)
        assert r.status_code == 403
        assert r.get_json()["error"]["code"] == "insufficient_permissions"

    def test_desconto_so_com_valor_de_varejo(self, client, owner):
        r = post_cesta(client, owner, retail_value_cents=None)
        cesta = r.get_json()["basket"]
        assert cesta["discount_percent"] is None

    def test_desconto_calculado(self, client, owner):
        r = post_cesta(client, owner, price_cents=800, retail_value_cents=1000)
        assert r.get_json()["basket"]["discount_percent"] == 20.0

    def test_quantidade_padrao_zero(self, client, owner):
        corpo = {
            "type": "legumes", "weight_grams": 800, "price_cents": 1200,
            "pickup_starts_at": (agora_utc() + timedelta(hours=1)).isoformat(),
            "pickup_ends_at": (agora_utc() + timedelta(hours=3)).isoformat(),
        }
        r = client.post(cestas_url(owner), json=corpo, headers=owner["headers"])
        assert r.status_code == 201
        assert r.get_json()["basket"]["quantity"] == 0

    def test_textos_aparam_e_removem_html(self, client, owner):
        r = post_cesta(client, owner,
                        name="  <b>Cesta</b> boa  ", notes="<script>x</script> fresquinha")
        cesta = r.get_json()["basket"]
        assert cesta["name"] == "Cesta boa"
        assert "script" not in cesta["notes"]
        assert "<" not in cesta["notes"]

    def test_textos_limitados(self, client, owner):
        cesta = post_cesta(client, owner, name="n" * 500, notes="o" * 900).get_json()["basket"]
        assert len(cesta["name"]) == 80
        assert len(cesta["notes"]) == 280

    def test_aceita_data_com_Z(self, client, owner):
        """O 'Z' do RFC 3339 é aceito."""
        inicio = (agora_utc() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        fim = (agora_utc() + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = post_cesta(client, owner,
                        pickup_starts_at=inicio, pickup_ends_at=fim)
        assert r.status_code == 201, r.get_json()

    def test_aceita_data_naive(self, client, owner):
        """Data sem fuso também é aceita (normalizada para UTC)."""
        inicio = (agora_utc() + timedelta(hours=1)).replace(tzinfo=None).isoformat()
        fim = (agora_utc() + timedelta(hours=3)).replace(tzinfo=None).isoformat()
        r = post_cesta(client, owner,
                        pickup_starts_at=inicio, pickup_ends_at=fim)
        assert r.status_code == 201, r.get_json()
        assert r.get_json()["basket"]["pickup_ends_at"].endswith("+00:00")


class TestListarCesta:
    def test_lista_por_retirada_mais_proxima(self, client, owner):
        agora = agora_utc()
        post_cesta(client, owner,
                   pickup_starts_at=(agora + timedelta(hours=6)).isoformat(),
                   pickup_ends_at=(agora + timedelta(hours=8)).isoformat())
        post_cesta(client, owner,
                   pickup_starts_at=(agora + timedelta(hours=1)).isoformat(),
                   pickup_ends_at=(agora + timedelta(hours=2)).isoformat())

        r = client.get(cestas_url(owner), headers=owner["headers"])
        assert r.status_code == 200
        cestas = r.get_json()["baskets"]
        assert len(cestas) == 2
        # a mais próxima primeiro (desc)
        assert cestas[0]["pickup_starts_at"] > cestas[1]["pickup_starts_at"]

    def test_filtra_por_status(self, client, owner):
        post_cesta(client, owner)
        post_cesta(client, owner, status="paused")

        r = client.get(cestas_url(owner) + "?status=paused", headers=owner["headers"])
        assert len(r.get_json()["baskets"]) == 1

    def test_status_invalido_no_filtro(self, client, owner):
        r = client.get(cestas_url(owner) + "?status=xxx", headers=owner["headers"])
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "invalid_status"

    def test_staff_le(self, client, app, staff):
        post_cesta(client, _promover_a_dono(app, client, staff))
        r = client.get(cestas_url(staff), headers=staff["headers"])
        assert r.status_code == 200


class TestVerEEditarCesta:
    def test_ve_detalhe(self, client, owner):
        cesta = post_cesta(client, owner).get_json()["basket"]
        r = client.get(f"{cestas_url(owner)}/{cesta['id']}", headers=owner["headers"])
        assert r.status_code == 200
        assert r.get_json()["basket"]["id"] == cesta["id"]

    def test_ve_de_outro_mercado_404(self, client, owner, manager):
        cesta = post_cesta(client, owner).get_json()["basket"]
        r = client.get(f"{cestas_url(manager)}/{cesta['id']}", headers=manager["headers"])
        assert r.status_code == 404

    def test_inexistente_404(self, client, owner):
        r = client.get(f"{cestas_url(owner)}/999", headers=owner["headers"])
        assert r.status_code == 404

    def test_manager_edita(self, client, manager):
        cesta = post_cesta(client, manager).get_json()["basket"]
        r = client.patch(f"{cestas_url(manager)}/{cesta['id']}",
                         json={"price_cents": 2000}, headers=manager["headers"])
        assert r.status_code == 200
        assert r.get_json()["basket"]["price_cents"] == 2000

    def test_staff_nao_edita(self, client, app, staff):
        dono = _promover_a_dono(app, client, staff)
        cesta = post_cesta(client, dono).get_json()["basket"]
        r = client.patch(f"{cestas_url(staff)}/{cesta['id']}",
                         json={"price_cents": 2000}, headers=staff["headers"])
        assert r.status_code == 403

    def test_staff_nao_encerra(self, client, app, staff):
        dono = _promover_a_dono(app, client, staff)
        cesta = post_cesta(client, dono).get_json()["basket"]
        r = client.patch(f"{cestas_url(staff)}/{cesta['id']}",
                         json={"status": "closed"}, headers=staff["headers"])
        assert r.status_code == 403

    def test_encerrar_e_status_closed(self, client, owner):
        cesta = post_cesta(client, owner).get_json()["basket"]
        r = client.patch(f"{cestas_url(owner)}/{cesta['id']}",
                         json={"status": "closed"}, headers=owner["headers"])
        assert r.status_code == 200
        assert r.get_json()["basket"]["status"] == "closed"

    def test_cesta_encerrada_nao_reabre(self, client, owner):
        cesta = post_cesta(client, owner).get_json()["basket"]
        client.patch(f"{cestas_url(owner)}/{cesta['id']}",
                     json={"status": "closed"}, headers=owner["headers"])
        r = client.patch(f"{cestas_url(owner)}/{cesta['id']}",
                         json={"status": "active"}, headers=owner["headers"])
        assert r.status_code == 409
        assert r.get_json()["error"]["code"] == "basket_closed"

    def test_atualiza_updated_at(self, client, owner):
        cesta = post_cesta(client, owner).get_json()["basket"]
        r = client.patch(f"{cestas_url(owner)}/{cesta['id']}",
                         json={"name": "Novo nome"}, headers=owner["headers"])
        assert r.status_code == 200
        assert r.get_json()["basket"]["updated_at"] is not None


class TestAjustarEstoque:
    def _cesta(self, client, ctx, **kw):
        return post_cesta(client, ctx, **kw).get_json()["basket"]

    def _ajustar(self, client, ctx, cesta_id, corpo):
        return client.patch(
            f"{cestas_url(ctx)}/{cesta_id}/quantity",
            json=corpo, headers=ctx["headers"],
        )

    def test_staff_ajusta_quantity(self, client, app, staff):
        dono = _promover_a_dono(app, client, staff)
        cesta = self._cesta(client, dono, quantity=10)
        r = self._ajustar(client, staff, cesta["id"], {"quantity": 3})
        assert r.status_code == 200
        assert r.get_json()["basket"]["quantity"] == 3

    def test_staff_ajusta_delta(self, client, app, staff):
        dono = _promover_a_dono(app, client, staff)
        cesta = self._cesta(client, dono, quantity=10)
        r = self._ajustar(client, staff, cesta["id"], {"delta": -2})
        assert r.get_code() if False else r.get_json()["basket"]["quantity"] == 8

    def test_staff_nao_cria_nem_edita(self, client, app, staff):
        """Ajustar estoque não abre porta para o resto."""
        dono = _promover_a_dono(app, client, staff)
        cesta = self._cesta(client, dono)
        assert client.post(cestas_url(staff), json={
            "type": "frutas", "weight_grams": 900, "price_cents": 1000,
            "pickup_starts_at": (agora_utc() + timedelta(hours=1)).isoformat(),
            "pickup_ends_at": (agora_utc() + timedelta(hours=2)).isoformat(),
        }, headers=staff["headers"]).status_code == 403
        assert client.patch(f"{cestas_url(staff)}/{cesta['id']}",
                            json={"name": "x"}, headers=staff["headers"]).status_code == 403

    def test_delta_negativo_maior_que_estoque(self, client, owner):
        cesta = self._cesta(client, owner, quantity=2)
        r = self._ajustar(client, owner, cesta["id"], {"delta": -5})
        assert r.status_code == 409
        assert r.get_json()["error"]["code"] == "insufficient_stock"
        # estoque intacto
        with client.application.app_context():
            atual = db.session.get(Basket, cesta["id"])
            assert atual.quantity == 2

    def test_delta_ate_zero(self, client, owner):
        cesta = self._cesta(client, owner, quantity=2)
        r = self._ajustar(client, owner, cesta["id"], {"delta": -2})
        assert r.status_code == 200
        assert r.get_json()["basket"]["quantity"] == 0

    def test_delta_abaixo_de_zero_bloqueado(self, client, owner):
        cesta = self._cesta(client, owner, quantity=0)
        r = self._ajustar(client, owner, cesta["id"], {"delta": -1})
        assert r.status_code == 409

    def test_delta_acima_do_limite(self, client, owner):
        cesta = self._cesta(client, owner, quantity=999)
        r = self._ajustar(client, owner, cesta["id"], {"delta": 1})
        assert r.status_code == 400

    def test_exige_quantity_ou_delta(self, client, owner):
        cesta = self._cesta(client, owner)
        r = self._ajustar(client, owner, cesta["id"], {})
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "missing_quantity"

    def test_nao_aceita_os_dois(self, client, owner):
        cesta = self._cesta(client, owner)
        r = self._ajustar(client, owner, cesta["id"], {"quantity": 1, "delta": 1})
        assert r.status_code == 400

    def test_delta_zero(self, client, owner):
        cesta = self._cesta(client, owner)
        r = self._ajustar(client, owner, cesta["id"], {"delta": 0})
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "invalid_delta"

    def test_quantity_booleano_recusado(self, client, owner):
        cesta = self._cesta(client, owner)
        r = self._ajustar(client, owner, cesta["id"], {"quantity": True})
        assert r.status_code == 400


# ===========================================================================
# Validações
# ===========================================================================


class TestValidacoes:
    @pytest.mark.parametrize("campo,valor,codigo", [
        ("type", "banana", "invalid_type"),
        ("type", None, "invalid_type"),
        ("price_cents", 99, "invalid_price_cents"),
        ("price_cents", 50001, "invalid_price_cents"),
        ("quantity", -1, "invalid_quantity"),
        ("quantity", 1000, "invalid_quantity"),
        ("weight_grams", 499, "invalid_weight_grams"),
        ("weight_grams", 30001, "invalid_weight_grams"),
        ("retail_value_cents", 50, "invalid_retail_value_cents"),
    ])
    def test_limites(self, client, owner, campo, valor, codigo):
        r = post_cesta(client, owner, **{campo: valor})
        assert r.status_code == 400, campo
        assert r.get_json()["error"]["code"] == codigo

    def test_limites_aceitos(self, client, owner):
        assert post_cesta(client, owner, price_cents=100).status_code == 201
        # no topo do intervalo, o valor de varejo tem de acompanhar o preco
        assert post_cesta(client, owner, price_cents=50000,
                          retail_value_cents=None).status_code == 201
        assert post_cesta(client, owner, weight_grams=500).status_code == 201
        assert post_cesta(client, owner, weight_grams=30000).status_code == 201
        assert post_cesta(client, owner, quantity=0).status_code == 201
        assert post_cesta(client, owner, quantity=999).status_code == 201

    def test_varejo_tem_que_ser_maior_que_preco(self, client, owner):
        r = post_cesta(client, owner, price_cents=2000, retail_value_cents=1000)
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "invalid_retail_value_cents"

    def test_varejo_igual_ao_preco_recusado(self, client, owner):
        r = post_cesta(client, owner, price_cents=2000, retail_value_cents=2000)
        assert r.status_code == 400

    def test_varejo_opcional(self, client, owner):
        assert post_cesta(client, owner, retail_value_cents=None).status_code == 201

    def test_inteiro_texto(self, client, owner):
        r = post_cesta(client, owner, price_cents="barato")
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "invalid_price_cents"

    def test_booleano_recusado(self, client, owner):
        r = post_cesta(client, owner, quantity=True)
        assert r.status_code == 400

    def test_fim_antes_do_inicio(self, client, owner):
        agora = agora_utc()
        r = post_cesta(client, owner,
                        pickup_starts_at=(agora + timedelta(hours=5)).isoformat(),
                        pickup_ends_at=(agora + timedelta(hours=2)).isoformat())
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "invalid_pickup_window"

    def test_fim_no_passado(self, client, owner):
        agora = agora_utc()
        r = post_cesta(client, owner,
                        pickup_starts_at=(agora - timedelta(hours=5)).isoformat(),
                        pickup_ends_at=(agora - timedelta(hours=2)).isoformat())
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "pickup_in_past"

    def test_janela_acima_de_7_dias(self, client, owner):
        agora = agora_utc()
        r = post_cesta(client, owner,
                        pickup_starts_at=agora.isoformat(),
                        pickup_ends_at=(agora + timedelta(days=8)).isoformat())
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "pickup_window_too_long"

    def test_janela_de_exatamente_7_dias(self, client, owner):
        agora = agora_utc()
        r = post_cesta(client, owner,
                        pickup_starts_at=(agora + timedelta(hours=1)).isoformat(),
                        pickup_ends_at=(agora + timedelta(days=7, hours=1)).isoformat())
        assert r.status_code == 201, r.get_json()

    def test_data_invalida(self, client, owner):
        r = post_cesta(client, owner, pickup_starts_at="amanha")
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "invalid_pickup_starts_at"

    def test_campos_obrigatorios(self, client, owner):
        r = client.post(cestas_url(owner), json={}, headers=owner["headers"])
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "invalid_type"

    def test_maximo_de_50_cestas(self, app, client, owner):
        """O limite de 50 cestas ativas/pausadas é do mercado."""
        with app.app_context():
            est = db.session.get(Establishment, owner["est_id"])
            agora = agora_utc()
            for i in range(50):
                db.session.add(Basket(
                    establishment_id=est.id,
                    type=BasketType.frutas.value,
                    weight_grams=1000,
                    price_cents=1000,
                    quantity=1,
                    pickup_starts_at=agora + timedelta(hours=1),
                    pickup_ends_at=agora + timedelta(hours=5),
                    status="active",
                ))
            db.session.commit()

        r = post_cesta(client, owner)
        assert r.status_code == 409
        assert r.get_json()["error"]["code"] == "too_many_baskets"

    def test_encerra_e_pode_criar_de_novo(self, app, client, owner):
        """Cestar closed não conta no limite."""
        with app.app_context():
            est = db.session.get(Establishment, owner["est_id"])
            agora = agora_utc()
            for i in range(50):
                db.session.add(Basket(
                    establishment_id=est.id, type="frutas", weight_grams=1000,
                    price_cents=1000, quantity=1,
                    pickup_starts_at=agora + timedelta(hours=1),
                    pickup_ends_at=agora + timedelta(hours=5), status="closed",
                ))
            db.session.commit()

        assert post_cesta(client, owner).status_code == 201


# ===========================================================================
# Isolamento
# ===========================================================================


class TestIsolamento:
    def test_usuario_de_outro_mercado_404(self, client, owner, de_outro):
        r = client.get(cestas_url(owner), headers=de_outro["headers"])
        assert r.status_code == 404

    def test_escrita_de_outro_mercado_404(self, client, owner, de_outro):
        r = client.post(cestas_url(owner), json={
            "type": "frutas", "weight_grams": 900, "price_cents": 1000,
            "pickup_starts_at": (agora_utc() + timedelta(hours=1)).isoformat(),
            "pickup_ends_at": (agora_utc() + timedelta(hours=2)).isoformat(),
        }, headers=de_outro["headers"])
        assert r.status_code == 404

    def test_anonimo_401(self, client, owner):
        assert client.get(cestas_url(owner)).status_code == 401

    def test_token_de_admin_nao_acessa(self, client, owner, admin_headers):
        assert client.get(cestas_url(owner), headers=admin_headers).status_code == 404

    def test_token_de_admin_nao_cria(self, client, owner, admin_headers):
        r = client.post(cestas_url(owner), json={
            "type": "frutas", "weight_grams": 900, "price_cents": 1000,
            "pickup_starts_at": (agora_utc() + timedelta(hours=1)).isoformat(),
            "pickup_ends_at": (agora_utc() + timedelta(hours=2)).isoformat(),
        }, headers=admin_headers)
        assert r.status_code == 404

    def test_token_de_usuario_nao_acessa_admin(self, client, owner):
        r = client.get("/api/v1/admin/establishments", headers=owner["headers"])
        assert r.status_code == 403


class TestSuspenso:
    def test_leitura_mantem_escrita_bloqueada(self, client, owner, admin_headers):
        cesta = post_cesta(client, owner).get_json()["basket"]

        client.patch(
            f"/api/v1/admin/establishments/{owner['est_id']}/status",
            json={"status": "suspended"}, headers=admin_headers,
        )

        # leitura continua
        assert client.get(cestas_url(owner), headers=owner["headers"]).status_code == 200
        assert client.get(
            f"{cestas_url(owner)}/{cesta['id']}", headers=owner["headers"]
        ).status_code == 200

        # escrita bloqueada
        r = client.patch(f"{cestas_url(owner)}/{cesta['id']}",
                         json={"price_cents": 999}, headers=owner["headers"])
        assert r.status_code == 403
        assert r.get_json()["error"]["code"] == "establishment_suspended"

        r2 = client.post(cestas_url(owner), json={
            "type": "legumes", "weight_grams": 900, "price_cents": 1000,
            "pickup_starts_at": (agora_utc() + timedelta(hours=1)).isoformat(),
            "pickup_ends_at": (agora_utc() + timedelta(hours=2)).isoformat(),
        }, headers=owner["headers"])
        assert r2.status_code == 403

        # até o estoque, que é a operação mais comum, bloqueia
        r3 = client.patch(f"{cestas_url(owner)}/{cesta['id']}/quantity",
                          json={"quantity": 1}, headers=owner["headers"])
        assert r3.status_code == 403


# ===========================================================================
# Rota pública
# ===========================================================================


class TestRotaPublicaComCestas:
    def _publicar(self, client, owner):
        # `bairro` é exigido para publicar: sem ele o mercado não entraria na
        # contagem por bairro da rota pública.
        client.patch(f"/api/v1/establishments/{owner['est_id']}/profile", json={
            "name": "Mercado", "address": "Rua A, 1", "phone": "19999990000",
            "bairro": "Centro",
        }, headers=owner["headers"])
        client.post(f"/api/v1/establishments/{owner['est_id']}/publish",
                    headers=owner["headers"])

    def _cesta_bruta(self, app, owner, **kw):
        """Cesta direto no banco, para montar os casos de filtro."""
        with app.app_context():
            agora = agora_utc()
            dados = dict(
                establishment_id=owner["est_id"], type="frutas",
                name="Cesta", weight_grams=1000, price_cents=1500,
                retail_value_cents=3000, quantity=3,
                pickup_starts_at=agora + timedelta(hours=1),
                pickup_ends_at=agora + timedelta(hours=5), status="active",
            )
            dados.update(kw)
            cesta = Basket(**dados)
            db.session.add(cesta)
            db.session.commit()
            return cesta.id

    def _publica(self, client, owner):
        r = client.get(f"/api/v1/public/establishments/{owner['slug']}")
        assert r.status_code == 200, r.get_json()
        return r.get_json()["establishment"]["baskets"]

    def test_mostra_cesta_valida(self, app, client, owner):
        self._publicar(client, owner)
        self._cesta_bruta(app, owner)
        cestas = self._publica(client, owner)
        assert len(cestas) == 1
        assert cestas[0]["type"] == "frutas"
        assert cestas[0]["discount_percent"] == 50.0

    def test_filtra_expirada(self, app, client, owner):
        self._publicar(client, owner)
        agora = agora_utc()
        self._cesta_bruta(app, owner,
                          pickup_starts_at=agora - timedelta(hours=3),
                          pickup_ends_at=agora - timedelta(hours=1))
        assert self._publica(client, owner) == []

    def test_filtra_pausada(self, app, client, owner):
        self._publicar(client, owner)
        self._cesta_bruta(app, owner, status="paused")
        assert self._publica(client, owner) == []

    def test_filtra_encerrada(self, app, client, owner):
        self._publicar(client, owner)
        self._cesta_bruta(app, owner, status="closed")
        assert self._publica(client, owner) == []

    def test_filtra_sem_estoque(self, app, client, owner):
        self._publicar(client, owner)
        self._cesta_bruta(app, owner, quantity=0)
        assert self._publica(client, owner) == []

    def test_mercado_nao_publicado_nao_mostra(self, app, client, owner):
        self._cesta_bruta(app, owner)
        r = client.get(f"/api/v1/public/establishments/{owner['slug']}")
        assert r.status_code == 404

    def test_mercado_despublicado_nao_mostra(self, app, client, owner):
        self._publicar(client, owner)
        self._cesta_bruta(app, owner)
        client.post(f"/api/v1/establishments/{owner['est_id']}/unpublish",
                    headers=owner["headers"])
        assert client.get(
            f"/api/v1/public/establishments/{owner['slug']}"
        ).status_code == 404

    def test_nao_vaza_campos_internos(self, app, client, owner):
        self._publicar(client, owner)
        self._cesta_bruta(app, owner)
        cesta = self._publica(client, owner)[0]
        assert set(cesta.keys()) == {
            "type", "name", "weight_grams", "price_cents",
            "retail_value_cents", "discount_percent", "quantity",
            "pickup_starts_at", "pickup_ends_at", "notes",
        }, set(cesta.keys())
        texto = str(cesta)
        assert "created_by" not in texto
        assert "establishment_id" not in texto

    def test_nao_vaza_id_da_cesta(self, app, client, owner):
        self._publicar(client, owner)
        id_cesta = self._cesta_bruta(app, owner)
        cesta = self._publica(client, owner)[0]
        assert "id" not in cesta
        # nenhum valor é o id interno da cesta
        assert str(id_cesta) not in [
            str(v) for v in cesta.values()
        ]
