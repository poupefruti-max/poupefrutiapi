"""Testes de RBAC e gestão da equipe.

O foco é escalada de privilégio: cada papel tentando fazer o que não pode,
usuário de outro mercado, isolamento de tokens e efeito imediato de remoção.
"""

from datetime import datetime, timedelta

import pytest

from app import create_app
from app.extensions import db
from app.models import (
    AdminUser,
    Establishment,
    EstablishmentMember,
    Invitation,
    User,
)
from app.rbac import (
    ALVOS_POR_PAPEL,
    PERMISSOES,
    cnpj_valido,
    eh_convite_permitido,
    pode_acao,
    pode_agir_sobre,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    app = create_app("testing")
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_ENGINE_OPTIONS={},
        APP_BASE_URL="https://app.test",
        MIN_PASSWORD_LENGTH=8,
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


def _criar_user(app, email, nome="Teste"):
    with app.app_context():
        u = User(email=email, name=nome)
        u.set_password("senha1234")
        db.session.add(u)
        db.session.commit()
        return u.id


def _criar_estabelecimento(app, nome="Mercado Teste", slug=None):
    """slug único por mercado — a coluna tem índice único."""
    with app.app_context():
        if slug is None:
            slug = "mercado-" + str(
                (Establishment.query.count() or 0) + 1
            ) + "-" + str(abs(hash(nome)) % 10000)
        est = Establishment(name=nome, slug=slug)
        db.session.add(est)
        db.session.commit()
        est_id, est_slug = est.id, est.slug
    return est_id, est_slug


def _vincular(app, user_id, establishment_id, role):
    with app.app_context():
        m = EstablishmentMember(
            user_id=user_id, establishment_id=establishment_id, role=role
        )
        db.session.add(m)
        db.session.commit()
        return m.user_id


def _login(client, email, senha="senha1234"):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": senha})
    assert r.status_code == 200, r.get_json()
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


def _time(app, papel, email, slug):
    """Cria usuário + vínculo num mercado próprio, e devolve o contexto dele."""
    uid = _criar_user(app, email, papel.capitalize())
    est_id, est_slug = _criar_estabelecimento(app, f"Mercado {papel}", slug)
    _vincular(app, uid, est_id, papel)
    return {
        "user_id": uid,
        "est_id": est_id,
        "slug": est_slug,
        "headers": _login(app.test_client(), email),
    }


@pytest.fixture
def owner(app, client):
    return _time(app, "owner", "owner@test.com", "mercado-owner")


@pytest.fixture
def manager(app, client):
    return _time(app, "manager", "manager@test.com", "mercado-manager")


@pytest.fixture
def staff(app, client):
    return _time(app, "staff", "staff@test.com", "mercado-staff")


def _login_admin(client, email, senha="senha1234"):
    """Admin entra pelo endpoint de admin, não pelo de usuário."""
    r = client.post(
        "/api/v1/admin/auth/login", json={"email": email, "password": senha}
    )
    assert r.status_code == 200, r.get_json()
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


@pytest.fixture
def admin_headers(app, client):
    with app.app_context():
        a = AdminUser(email="admin@test.com")
        a.set_password("senha1234")
        db.session.add(a)
        db.session.commit()
    return _login_admin(client, "admin@test.com")


@pytest.fixture
def membro_de_outro(app, client):
    """Usuário com conta válida, mas sem vínculo com o mercado do owner."""
    uid = _criar_user(app, "deoutro@test.com", "De Outro")
    return {"user_id": uid, "headers": _login(client, "deoutro@test.com")}


# ---------------------------------------------------------------------------
# A matriz
# ---------------------------------------------------------------------------

class TestMatriz:
    def test_matriz_batem_com_a_especificacao(self):
        esperado = {
            "ver_perfil":       {"owner", "manager", "staff"},
            "ver_equipe":       {"owner", "manager", "staff"},
            "editar_perfil":    {"owner", "manager"},
            "publicar":         {"owner", "manager"},
            "convidar_staff":   {"owner", "manager"},
            "convidar_manager": {"owner"},
            "convidar_owner":   set(),
            "alterar_papel":    {"owner", "manager"},
            "remover_membro":   {"owner", "manager"},
        }
        assert PERMISSOES == esperado

    def test_ninguem_concede_owner(self):
        for papel in ("owner", "manager", "staff"):
            assert not eh_convite_permitido(papel, "owner")

    def test_alvos(self):
        assert ALVOS_POR_PAPEL["staff"] == {"owner", "manager"}
        assert ALVOS_POR_PAPEL["manager"] == {"owner"}
        assert ALVOS_POR_PAPEL["owner"] == {"owner"}

    def test_cnpj_valido(self):
        assert cnpj_valido("11222333000181")
        assert cnpj_valido("11.222.333/0001-81")
        assert not cnpj_valido("11222333000180")
        assert not cnpj_valido("00000000000000")


# ---------------------------------------------------------------------------
# Perfil: leitura e escrita
# ---------------------------------------------------------------------------

class TestPerfil:
    def test_todos_leem_perfil(self, owner, manager, staff):
        for p in (owner, manager, staff):
            resp = _get(p, "/profile")
            assert resp.status_code == 200, p
            assert resp.get_json()["establishment"]["name"].startswith("Mercado ")

    def test_staff_nao_edita_perfil(self, staff):
        resp = _patch(staff, "/profile", {"name": "Novo"})
        assert resp.status_code == 403
        assert resp.get_json()["error"]["code"] == "insufficient_permissions"

    def test_manager_edita_perfil(self, manager):
        resp = _patch(manager, "/profile", {
            "name": "Mercado Novo", "address": "Rua A, 1", "phone": "19988880000",
        })
        assert resp.status_code == 200
        assert resp.get_json()["establishment"]["name"] == "Mercado Novo"

    def test_edita_cnpj_valido(self, owner):
        resp = _patch(owner, "/profile", {"cnpj": "11.222.333/0001-81"})
        assert resp.status_code == 200
        assert resp.get_json()["establishment"]["cnpj"] == "11.222.333/0001-81"

    def test_edita_cnpj_invalido(self, owner):
        resp = _patch(owner, "/profile", {"cnpj": "11222333000180"})
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_cnpj"

    def test_cnpj_duplicado_da_erro_claro(self, owner, app):
        _criar_estabelecimento(app, "Outro", slug="cnpj-outro")
        with app.app_context():
            e = Establishment.query.filter_by(slug="cnpj-outro").one()
            e.cnpj = "11222333000181"
            db.session.commit()

        resp = _patch(owner, "/profile", {"cnpj": "11.222.333/0001-81"})
        assert resp.status_code == 409
        assert resp.get_json()["error"]["code"] == "cnpj_already_used"

    def test_texto_e_limitado(self, owner):
        resp = _patch(owner, "/profile", {"description": "x" * 5000})
        assert resp.status_code == 200
        assert len(resp.get_json()["establishment"]["description"]) == 2000

    def test_nome_vazio_recusado(self, owner):
        resp = _patch(owner, "/profile", {"name": "   "})
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_name"


# helpers para as rotas (o prefixo depende do mercado do fixture)
def _url(p, caminho):
    return f"/api/v1/establishments/{p['est_id']}{caminho}"


def _get(p, caminho):
    return _CLIENT.get(_url(p, caminho), headers=p["headers"])


def _GET_RAW(est_id, caminho, headers):
    return _CLIENT.get(f"/api/v1/establishments/{est_id}{caminho}", headers=headers)


def _patch(p, caminho, corpo):
    return _CLIENT.patch(_url(p, caminho), json=corpo, headers=p["headers"])


def _delete(p, caminho):
    return _CLIENT.delete(_url(p, caminho), headers=p["headers"])


def _post(p, caminho, corpo=None):
    return _CLIENT.post(_url(p, caminho), json=corpo or {}, headers=p["headers"])


# pytest injeta o client aqui
@pytest.fixture(autouse=True)
def _ligar_client(client):
    global _CLIENT
    _CLIENT = client


# ---------------------------------------------------------------------------
# Publicação
# ---------------------------------------------------------------------------

class TestPublicacao:
    def test_publicar_exige_perfil_minimo(self, owner):
        resp = _post(owner, "/publish")
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "incomplete_profile"

    def test_publicar_promove_para_active(self, owner):
        _patch(owner, "/profile", {
            "name": "Mercado", "address": "Rua A, 1", "phone": "19988880000",
        })
        resp = _post(owner, "/publish")
        assert resp.status_code == 200
        dados = resp.get_json()["establishment"]
        assert dados["is_published"] is True
        assert dados["status"] == "active"

    def test_despublicar(self, owner):
        _patch(owner, "/profile", {
            "name": "Mercado", "address": "Rua A, 1", "phone": "19988880000",
        })
        _post(owner, "/publish")
        resp = _post(owner, "/unpublish")
        assert resp.status_code == 200
        assert resp.get_json()["establishment"]["is_published"] is False

    def test_staff_nao_publica(self, staff):
        resp = _post(staff, "/publish")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Convites
# ---------------------------------------------------------------------------

class TestConvitesEquipe:
    def test_owner_convida_manager_e_staff(self, owner):
        for papel in ("manager", "staff"):
            resp = _post(owner, "/invitations", {"role": papel})
            assert resp.status_code == 201
            assert resp.get_json()["invitation"]["role"] == papel
            assert "#token=" in resp.get_json()["invite_url"]

    def test_manager_convida_staff(self, manager):
        resp = _post(manager, "/invitations", {"role": "staff"})
        assert resp.status_code == 201

    def test_manager_nao_convida_manager(self, manager):
        resp = _post(manager, "/invitations", {"role": "manager"})
        assert resp.status_code == 403
        assert resp.get_json()["error"]["code"] == "insufficient_permissions"

    def test_staff_nao_convida_nem_que_seja_staff(self, staff):
        resp = _post(staff, "/invitations", {"role": "staff"})
        assert resp.status_code == 403

    def test_ninguem_convida_owner(self, owner, manager):
        for p in (owner, manager):
            resp = _post(p, "/invitations", {"role": "owner"})
            assert resp.status_code == 403

    def test_convite_mostra_link_uma_vez(self, owner):
        resp = _post(owner, "/invitations", {"role": "staff"})
        assert "invite_url" in resp.get_json()
        # A listagem não devolve o link nem o hash.
        lista = _get(owner, "/members")
        assert lista.status_code == 200
        texto = str(lista.get_json())
        assert "token_hash" not in texto
        assert "invite_url" not in texto

    def test_revogar_convite_impede_aceite(self, owner):
        resp = _post(owner, "/invitations", {"role": "staff"})
        token = resp.get_json()["invite_url"].split("#token=")[1]
        conv_id = resp.get_json()["invitation"]["id"]

        rev = _delete(owner, f"/invitations/{conv_id}")
        assert rev.status_code == 200
        assert rev.get_json()["invitation"]["status"] == "revoked"

        # O aceite público passa a recusar.
        aceite = _CLIENT.post("/api/v1/invitations/accept", json={
            "token": token, "name": "Fulano",
            "email": "fulano@test.com", "password": "senha1234",
        })
        assert aceite.status_code == 400
        assert aceite.get_json()["error"]["code"] == "invalid_invitation"

    def test_revogar_convite_ja_aceito(self, owner, app):
        resp = _post(owner, "/invitations", {"role": "staff"})
        conv_id = resp.get_json()["invitation"]["id"]
        token = resp.get_json()["invite_url"].split("#token=")[1]

        _CLIENT.post("/api/v1/invitations/accept", json={
            "token": token, "name": "Fulano",
            "email": "fulano@test.com", "password": "senha1234",
        })

        rev = _delete(owner, f"/invitations/{conv_id}")
        assert rev.status_code == 409
        assert rev.get_json()["error"]["code"] == "invitation_already_accepted"

    def test_convite_de_outro_mercado_404(self, owner, manager):
        resp = _post(owner, "/invitations", {"role": "staff"})
        conv_id = resp.get_json()["invitation"]["id"]

        # manager pertence a outro mercado: não existe para ele
        resp2 = _delete(manager, f"/invitations/{conv_id}")
        assert resp2.status_code == 404


# ---------------------------------------------------------------------------
# Gestão de membros
# ---------------------------------------------------------------------------

class TestGestaoDeMembros:
    def test_listar_membros_tem_convites(self, owner):
        resp = _get(owner, "/members")
        assert resp.status_code == 200
        dados = resp.get_json()
        assert len(dados["members"]) == 1
        assert dados["members"][0]["role"] == "owner"
        assert dados["invitations"] == []

    def test_status_de_convite_aparece(self, owner, app):
        _post(owner, "/invitations", {"role": "staff"})
        with app.app_context():
            conv = db.session.query(Invitation).order_by(Invitation.id.desc()).first()
            conv.expires_at = datetime.utcnow() - timedelta(days=1)
            db.session.commit()

        resp = _get(owner, "/members")
        status = [c["status"] for c in resp.get_json()["invitations"]]
        assert "expired" in status

    def test_owner_promove_staff_a_manager(self, owner, app, client):
        sid = _criar_user(app, "promover@test.com")
        _vincular(app, sid, owner["est_id"], "staff")

        resp = _patch(owner, f"/members/{sid}", {"role": "manager"})
        assert resp.status_code == 200
        assert resp.get_json()["member"]["role"] == "manager"

    def test_owner_rebaixa_manager_para_staff(self, owner, app):
        mid = _criar_user(app, "rebaixar@test.com")
        _vincular(app, mid, owner["est_id"], "manager")

        resp = _patch(owner, f"/members/{mid}", {"role": "staff"})
        assert resp.status_code == 200
        assert resp.get_json()["member"]["role"] == "staff"

    def test_staff_nao_altera_papel(self, staff, owner):
        oid = owner["user_id"]
        resp = _patch(staff, f"/members/{oid}", {"role": "staff"})
        assert resp.status_code == 403

    # --- escalada: manager não pode criar manager ---
    #
    # A matriz diz que "Gerar link para manager" é só do owner. Se manager
    # pudesse promover staff a manager pelo PATCH /members, ele criaria
    # manager por um caminho diferente do convite — escalada de privilégio.
    # Este é o teste que trava essa regra.

    def test_manager_nao_promove_staff_a_manager(self, manager, app):
        sid = _criar_user(app, "promovido@test.com")
        _vincular(app, sid, manager["est_id"], "staff")

        resp = _patch(manager, f"/members/{sid}", {"role": "manager"})

        assert resp.status_code == 403
        assert resp.get_json()["error"]["code"] == "cannot_grant_higher_role"

        # O papel não mudou.
        with app.app_context():
            vinculo = EstablishmentMember.query.filter_by(user_id=sid).one()
            assert vinculo.role.value == "staff"

    def test_manager_rebaixa_staff_para_staff_nao_muda_nada(self, manager, app):
        sid = _criar_user(app, "mesmo@test.com")
        _vincular(app, sid, manager["est_id"], "staff")

        resp = _patch(manager, f"/members/{sid}", {"role": "staff"})
        assert resp.status_code == 200  # já é staff: no-op

    def test_ninguem_atribui_owner_pelo_patch(self, manager, owner, app):
        """Owner não é concedível por membro nenhum, nem pelo PATCH."""
        sid = _criar_user(app, "virouowner@test.com")
        _vincular(app, sid, manager["est_id"], "staff")

        resp = _patch(manager, f"/members/{sid}", {"role": "owner"})
        assert resp.status_code == 403

    def test_manager_nao_promove_outro_manager_nem_o_proprio_cenario(
        self, manager, app
    ):
        """Manager não consegue, por nenhum caminho, minti-lo de manager."""
        outro = _criar_user(app, "colega@test.com")
        _vincular(app, outro, manager["est_id"], "staff")
        staff_id = _criar_user(app, "funcionario2@test.com")
        _vincular(app, staff_id, manager["est_id"], "staff")

        # staff -> manager: bloqueado
        r1 = _patch(manager, f"/members/{outro}", {"role": "manager"})
        # manager -> staff: bloqueado (não pode rebaixar par)
        r2 = _patch(manager, f"/members/{staff_id}", {"role": "manager"})

        assert r1.status_code == 403
        assert r2.status_code == 403

        # Nenhum virou manager
        with app.app_context():
            gerentes = EstablishmentMember.query.filter_by(
                establishment_id=manager["est_id"], role="manager"
            ).count()
            assert gerentes == 1  # só o próprio manager do fixture

    def test_manager_nao_altera_manager_nem_owner(self, manager, app):
        """No mesmo mercado: owner e manager são intocáveis pelo manager."""
        # Um owner no mercado do manager (senão seria 404, não 403).
        owner_id = _criar_user(app, "dono@test.com")
        _vincular(app, owner_id, manager["est_id"], "owner")

        outro_manager = _criar_user(app, "gerente@test.com")
        _vincular(app, outro_manager, manager["est_id"], "manager")

        r1 = _patch(manager, f"/members/{owner_id}", {"role": "staff"})
        r2 = _patch(manager, f"/members/{outro_manager}", {"role": "staff"})

        assert r1.status_code == 403
        assert r2.status_code == 403
        assert r1.get_json()["error"]["code"] == "insufficient_permissions"

    def test_manager_remove_staff(self, manager, app):
        sid = _criar_user(app, "sai@test.com")
        _vincular(app, sid, manager["est_id"], "staff")

        resp = _delete(manager, f"/members/{sid}")
        assert resp.status_code == 200
        with app.app_context():
            assert EstablishmentMember.query.filter_by(user_id=sid).count() == 0
            # o User continua existindo
            assert User.query.filter_by(email="sai@test.com").count() == 1

    def test_manager_nao_remove_owner_nem_manager(self, manager, app):
        owner_id = _criar_user(app, "dono@test.com")
        _vincular(app, owner_id, manager["est_id"], "owner")

        outro = _criar_user(app, "g2@test.com")
        _vincular(app, outro, manager["est_id"], "manager")

        r1 = _delete(manager, f"/members/{owner_id}")
        r2 = _delete(manager, f"/members/{outro}")

        assert r1.status_code == 403
        assert r2.status_code == 403

    def test_membro_de_outro_mercado_404_e_nao_403(self, manager, owner):
        """Owner pertence a outro mercado: 404, não 403 — nem confirma que existe."""
        resp = _patch(manager, f"/members/{owner['user_id']}", {"role": "staff"})
        assert resp.status_code == 404

    def test_staff_nao_remove_ninguem(self, staff, owner):
        resp = _delete(staff, f"/members/{owner['user_id']}")
        assert resp.status_code == 403

    def test_nao_remove_ultimo_owner(self, owner):
        resp = _delete(owner, f"/members/{owner['user_id']}")
        assert resp.status_code == 409
        assert resp.get_json()["error"]["code"] == "last_owner"

    def test_nao_rebaixa_ultimo_owner(self, owner):
        resp = _patch(owner, f"/members/{owner['user_id']}", {"role": "manager"})
        assert resp.status_code == 409
        assert resp.get_json()["error"]["code"] == "last_owner"

    def test_owner_pode_remover_outro_owner(self, owner, app):
        outro = _criar_user(app, "socinho@test.com")
        _vincular(app, outro, owner["est_id"], "owner")

        resp = _delete(owner, f"/members/{outro}")
        assert resp.status_code == 200

    def test_remover_membro_vale_na_mesma_hora(self, owner, app, client):
        """O staff removido perde o acesso imediatamente, com o mesmo token."""
        sid = _criar_user(app, "expulso@test.com")
        _vincular(app, sid, owner["est_id"], "staff")
        token_staff = _login(client, "expulso@test.com")

        # antes: staff consegue ler o perfil
        assert _CLIENT.get(
            f"/api/v1/establishments/{owner['est_id']}/profile",
            headers=token_staff,
        ).status_code == 200

        _delete(owner, f"/members/{sid}")

        # depois: 404, sem novo login
        depois = _CLIENT.get(
            f"/api/v1/establishments/{owner['est_id']}/profile",
            headers=token_staff,
        )
        assert depois.status_code == 404

    def test_rebaixar_vale_na_mesma_hora(self, owner, app, client):
        """Depois de rebaixado para staff, o manager perde as escritas."""
        mid = _criar_user(app, "rebaixado@test.com")
        _vincular(app, mid, owner["est_id"], "manager")
        token_manager = _login(client, "rebaixado@test.com")

        _patch(owner, f"/members/{mid}", {"role": "staff"})

        resp = _CLIENT.patch(
            f"/api/v1/establishments/{owner['est_id']}/profile",
            json={"name": "X"}, headers=token_manager,
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Isolamento
# ---------------------------------------------------------------------------

class TestIsolamento:
    ROTAS_ESCRITA = [
        ("post", "/invitations", {"role": "staff"}),
        ("post", "/publish", None),
        ("post", "/unpublish", None),
        ("patch", "/profile", {"name": "X"}),
        ("patch", "/members/1", {"role": "staff"}),
        ("delete", "/members/1", None),
        ("delete", "/invitations/1", None),
    ]

    @pytest.mark.parametrize("metodo,caminho,corpo", ROTAS_ESCRITA)
    def test_anonimo_401(self, owner, metodo, caminho, corpo):
        resp = getattr(_CLIENT, metodo)(_url(owner, caminho), json=corpo)
        assert resp.status_code == 401

    @pytest.mark.parametrize("metodo,caminho,corpo", ROTAS_ESCRITA)
    def test_de_outro_mercado_404(self, owner, membro_de_outro, metodo, caminho, corpo):
        resp = getattr(_CLIENT, metodo)(
            _url(owner, caminho), json=corpo, headers=membro_de_outro["headers"]
        )
        assert resp.status_code == 404

    def test_token_admin_nao_serve(self, owner, admin_headers):
        resp = _CLIENT.get(_url(owner, "/profile"), headers=admin_headers)
        assert resp.status_code == 404

    def test_token_admin_nao_serve_nas_escritas(self, owner, admin_headers):
        resp = _CLIENT.patch(
            _url(owner, "/profile"), json={"name": "X"}, headers=admin_headers
        )
        assert resp.status_code == 404

    def test_token_usuario_nao_serve_no_admin(self, owner):
        resp = _CLIENT.get("/api/v1/admin/establishments", headers=owner["headers"])
        assert resp.status_code == 403

    def test_mercado_inexistente_404(self, owner):
        resp = _CLIENT.get(
            "/api/v1/establishments/99999/profile", headers=owner["headers"]
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Suspensão
# ---------------------------------------------------------------------------

class TestSuspensao:
    def test_admin_suspende_e_reativa(self, owner, admin_headers):
        est = owner["est_id"]

        # status no CORPO JSON, não na query string
        resp = _CLIENT.patch(
            f"/api/v1/admin/establishments/{est}/status",
            json={"status": "suspended"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        corpo = resp.get_json()
        assert corpo["status"] == "suspended"
        assert corpo["previous_status"] == "pending"

        # escrita bloqueada
        r2 = _CLIENT.patch(
            f"/api/v1/establishments/{est}/profile",
            json={"name": "X"}, headers=owner["headers"],
        )
        assert r2.status_code == 403
        assert r2.get_json()["error"]["code"] == "establishment_suspended"

        # leitura continua
        r3 = _CLIENT.get(
            f"/api/v1/establishments/{est}/profile", headers=owner["headers"]
        )
        assert r3.status_code == 200

        # reativar
        r4 = _CLIENT.patch(
            f"/api/v1/admin/establishments/{est}/status",
            json={"status": "active"},
            headers=admin_headers,
        )
        assert r4.status_code == 200
        assert r4.get_json()["status"] == "active"

        r5 = _CLIENT.patch(
            f"/api/v1/establishments/{est}/profile",
            json={"name": "X"}, headers=owner["headers"],
        )
        assert r5.status_code == 200

    def test_status_vem_do_corpo_e_nao_da_query(self, owner, admin_headers):
        """Query string não é mais lida: mandar só ?status= deve falhar."""
        est = owner["est_id"]
        resp = _CLIENT.patch(
            f"/api/v1/admin/establishments/{est}/status?status=suspended",
            headers=admin_headers,
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_status"

        # e o status não mudou
        det = _CLIENT.get(
            f"/api/v1/admin/establishments/{est}", headers=admin_headers
        )
        assert det.get_json()["establishment"]["status"] == "pending"

    def test_status_invalido(self, owner, admin_headers):
        resp = _CLIENT.patch(
            f"/api/v1/admin/establishments/{owner['est_id']}/status",
            json={"status": "banido"},
            headers=admin_headers,
        )
        assert resp.status_code == 400

    def test_corpo_vazio(self, owner, admin_headers):
        resp = _CLIENT.patch(
            f"/api/v1/admin/establishments/{owner['est_id']}/status",
            json={},
            headers=admin_headers,
        )
        assert resp.status_code == 400

    def test_pending_e_aceito(self, owner, admin_headers):
        """pending volta o mercado ao estado inicial sem apagar dados."""
        est = owner["est_id"]
        r = _CLIENT.patch(
            f"/api/v1/admin/establishments/{est}/status",
            json={"status": "pending"},
            headers=admin_headers,
        )
        assert r.status_code == 200
        assert r.get_json()["status"] == "pending"

    def test_suspenso_some_da_rota_publica(self, owner, admin_headers):
        est = owner["est_id"]
        _patch(owner, "/profile", {
            "name": "Mercado", "address": "Rua A, 1", "phone": "19988880000",
        })
        _post(owner, "/publish")

        publica = _CLIENT.get(f"/api/v1/public/establishments/{owner['slug']}")
        assert publica.status_code == 200

        _CLIENT.patch(
            f"/api/v1/admin/establishments/{est}/status",
            json={"status": "suspended"},
            headers=admin_headers,
        )
        depois = _CLIENT.get(f"/api/v1/public/establishments/{owner['slug']}")
        assert depois.status_code == 404

    def test_reativar_preserva_is_published(self, owner, admin_headers):
        """Reativar não despublica a página por efeito colateral."""
        est = owner["est_id"]
        _patch(owner, "/profile", {
            "name": "Mercado", "address": "Rua A, 1", "phone": "19988880000",
        })
        _post(owner, "/publish")

        _CLIENT.patch(
            f"/api/v1/admin/establishments/{est}/status",
            json={"status": "suspended"}, headers=admin_headers,
        )
        r = _CLIENT.patch(
            f"/api/v1/admin/establishments/{est}/status",
            json={"status": "active"}, headers=admin_headers,
        )
        assert r.status_code == 200
        assert r.get_json()["is_published"] is True

        # e continua na rota pública
        assert _CLIENT.get(
            f"/api/v1/public/establishments/{owner['slug']}"
        ).status_code == 200

    def test_leitura_do_mercado_suspenso_continua(self, owner, admin_headers):
        est = owner["est_id"]
        _CLIENT.patch(
            f"/api/v1/admin/establishments/{est}/status",
            json={"status": "suspended"}, headers=admin_headers,
        )
        for caminho in ("/profile", "/members"):
            r = _GET_RAW(est, caminho, owner["headers"])
            assert r.status_code == 200, caminho


# ---------------------------------------------------------------------------
# Rota pública
# ---------------------------------------------------------------------------

class TestRotaPublica:
    def _publicar(self, owner):
        _patch(owner, "/profile", {
            "name": "Mercado", "address": "Rua A, 1", "phone": "19988880000",
            "description": "Bom lugar", "opening_hours": "Seg a Sex, 8-18",
        })
        _post(owner, "/publish")

    def test_nao_publicado_404(self, owner):
        resp = _CLIENT.get(f"/api/v1/public/establishments/{owner['slug']}")
        assert resp.status_code == 404

    def test_publicado_devolve_campos_publicos(self, owner):
        self._publicar(owner)
        resp = _CLIENT.get(f"/api/v1/public/establishments/{owner['slug']}")
        assert resp.status_code == 200
        est = resp.get_json()["establishment"]
        assert est["name"] == "Mercado"
        assert est["description"] == "Bom lugar"
        assert est["address"] == "Rua A, 1"
        assert est["phone"] == "19988880000"
        assert est["opening_hours"] == "Seg a Sex, 8-18"

    def test_nao_vaza_cnpj(self, owner):
        _patch(owner, "/profile", {"cnpj": "11.222.333/0001-81"})
        self._publicar(owner)
        resp = _CLIENT.get(f"/api/v1/public/establishments/{owner['slug']}")
        texto = str(resp.get_json())
        assert "cnpj" not in texto
        assert "11222333000181" not in texto
        assert "11.222.333/0001-81" not in texto

    def test_nao_vaza_ids_nem_vinculo(self, owner):
        self._publicar(owner)
        resp = _CLIENT.get(f"/api/v1/public/establishments/{owner['slug']}")
        dados = resp.get_json()["establishment"]
        # Só os campos públicos, nada de id/lead/members/slug.
        assert set(dados.keys()) == {
            "name", "description", "address", "phone", "opening_hours"
        }

    def test_slug_inexistente_404(self, client):
        assert _CLIENT.get("/api/v1/public/establishments/nao-existe").status_code == 404

    def test_despublicado_some(self, owner):
        self._publicar(owner)
        _post(owner, "/unpublish")
        assert _CLIENT.get(f"/api/v1/public/establishments/{owner['slug']}").status_code == 404

    def test_pendente_com_perfil_nao_aparece(self, owner):
        """Só publicar promove para active; enquanto isso, não aparece."""
        _patch(owner, "/profile", {
            "name": "Mercado", "address": "Rua A, 1", "phone": "19988880000",
        })
        est = _CLIENT.get(f"/api/v1/public/establishments/{owner['slug']}")
        assert est.status_code == 404


# ---------------------------------------------------------------------------
# /me com memberships
# ---------------------------------------------------------------------------

class TestMeComMemberships:
    def test_me_lista_mercados(self, owner):
        resp = owner["headers"] and _CLIENT.get("/api/v1/auth/me", headers=owner["headers"])
        assert resp.status_code == 200
        ms = resp.get_json()["user"]["memberships"]
        assert len(ms) == 1
        assert ms[0]["establishment_id"] == owner["est_id"]
        assert ms[0]["name"] == "Mercado owner"
        assert ms[0]["slug"] == owner["slug"]
        assert ms[0]["role"] == "owner"

    def test_me_mostra_estabelecimento_suspenso(self, owner, admin_headers):
        _CLIENT.patch(
            f"/api/v1/admin/establishments/{owner['est_id']}/status",
            json={"status": "suspended"},
            headers=admin_headers,
        )
        resp = _CLIENT.get("/api/v1/auth/me", headers=owner["headers"])
        ms = resp.get_json()["user"]["memberships"]
        assert ms[0]["establishment_status"] == "suspended"
