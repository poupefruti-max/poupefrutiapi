"""Datas com fuso: o bug de `offset-naive vs offset-aware`.

Regressão real de produção: `Invitation.esta_pendente()` comparava
`expires_at` (TIMESTAMPTZ, vem **com** fuso do PostgreSQL) com
`datetime.utcnow()` (**sem** fuso), levantando

    TypeError: can't compare offset-naive and offset-aware datetimes

O SQLite (banco dos testes) **descarta o fuso ao gravar**, então a suíte nunca
pegou. Por isso o formato "aware" é testado com objeto em memória — que é
exatamente o estado em que o objeto se encontra logo apos vir do PostgreSQL —
e o serviço é exercitado com um objeto aware injetado.

Sem conexão com nenhum banco: tudo em SQLite em memória.
"""

import pathlib
import tokenize
from datetime import datetime, timedelta, timezone

import pytest

from app import create_app
from app.extensions import db
from app.models import Establishment, Invitation, User
from app.timeutils import UTC, agora_utc, como_utc
from services import invitations as svc

RAIZ = pathlib.Path(__file__).resolve().parent.parent

FUTURO = timedelta(days=1)
PASSADO = timedelta(days=1)


def expira_aware(delta):
    return agora_utc() + delta


def expira_naive(delta):
    return (agora_utc() + delta).replace(tzinfo=None)


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


@pytest.fixture
def base(app):
    """Mercado + admin: o mínimo para criar convite."""
    with app.app_context():
        from app.models import AdminUser

        est = Establishment(name="Mercado", slug="mercado")
        admin = AdminUser(email="admin@test.com")
        admin.set_password("senha1234")
        db.session.add(est)
        db.session.add(admin)
        db.session.commit()
        return {"est_id": est.id, "admin_id": admin.id}


def criar_convite_persistido(app, base, delta=FUTURO):
    """Convite real, gravado e confirmado no banco.

    `delta` permite criar um convite já vencido: o serviço usa
    DIAS_VALIDADE dias, então para simular o passado adjustamos a coluna.
    """
    with app.app_context():
        resultado = svc.criar_convite(
            establishment_id=base["est_id"],
            role="owner",
            criado_por_admin_id=base["admin_id"],
            app_base_url="https://app.test",
            sess=db.session,
        )
        convite = resultado["invitation"]
        if delta != FUTURO:
            convite.expires_at = agora_utc() + delta
        db.session.commit()
        return {"id": convite.id, "token": resultado["token"]}


# ===========================================================================
# O helper
# ===========================================================================

class TestTimeutils:
    def test_agora_utc_e_aware(self):
        agora = agora_utc()
        assert agora.tzinfo is not None
        assert agora.utcoffset() == timedelta(0)

    def test_agora_utc_usa_timezone_utc(self):
        assert agora_utc().tzinfo is timezone.utc

    def test_como_utc_none_continua_none(self):
        assert como_utc(None) is None

    def test_como_utc_anexa_fuso_a_naive(self):
        naive = datetime(2030, 1, 1, 12, 0, 0)
        convertido = como_utc(naive)
        assert convertido.tzinfo is not None
        assert convertido == datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_como_utc_converte_aware_de_outro_fuso(self):
        sp = timezone(timedelta(hours=-3))          # 12:00 em SP = 15:00 UTC
        convertido = como_utc(datetime(2030, 1, 1, 12, 0, 0, tzinfo=sp))
        assert convertido.utcoffset() == timedelta(0)
        assert convertido.hour == 15

    def test_como_utc_idempotente(self):
        uma_vez = como_utc(agora_utc())
        assert como_utc(uma_vez) == uma_vez


# ===========================================================================
# esta_pendente — o bug original, testado sem banco
# ===========================================================================

def convite_em_memoria(expires_at, aceito=None, revogado=None, nome="Mercado"):
    """Objeto Invitation não persistido: reflete o estado que o Postgres entrega."""
    from app.rbac import MemberRole

    convite = Invitation(
        establishment_id=1,
        # o model entrega o Enum, não a string crua
        role=MemberRole.owner,
        token_hash="x" * 64,
        expires_at=expires_at,
        accepted_at=aceito,
        revoked_at=revogado,
    )
    # validar_convite() lê convite.establishment.name
    convite.establishment = Establishment(id=1, name=nome, slug="mercado")
    return convite


class TestEstaPendente:
    @pytest.mark.parametrize("rotulo,expira,esperado", [
        ("aware futuro", expira_aware(FUTURO), True),
        ("aware passado", expira_aware(-PASSADO), False),
        ("naive futuro", expira_naive(FUTURO), True),
        ("naive passado", expira_naive(-PASSADO), False),
    ])
    def test_esta_pendente_nos_dois_formatos(self, rotulo, expira, esperado):
        convite = convite_em_memoria(expira)
        assert convite.esta_pendente() is esperado, rotulo

    def test_premissa_formato_aware(self):
        """Confirma que o caso 'aware' realmente é aware (o do PostgreSQL)."""
        convite = convite_em_memoria(expira_aware(FUTURO))
        assert convite.expires_at.tzinfo is not None

    def test_premissa_formato_naive(self):
        """Confirma que o caso 'naive' realmente é naive (o do SQLite)."""
        convite = convite_em_memoria(expira_naive(FUTURO))
        assert convite.expires_at.tzinfo is None

    def test_aceito_nao_e_pendente(self):
        for expira in (expira_aware(FUTURO), expira_naive(FUTURO)):
            convite = convite_em_memoria(expira, aceito=agora_utc())
            assert convite.esta_pendente() is False

    def test_revogado_nao_e_pendente(self):
        for expira in (expira_aware(FUTURO), expira_naive(FUTURO)):
            convite = convite_em_memoria(expira, revogado=agora_utc())
            assert convite.esta_pendente() is False

    def test_agora_explicito_e_normalizado(self):
        """Passar `agora` naive também funciona (vem de teste ou de código antigo)."""
        convite = convite_em_memoria(expira_naive(FUTURO))
        assert convite.esta_pendente(agora=datetime(2020, 1, 1)) is True
        assert convite.esta_pendente(agora=datetime(2030, 1, 1)) is False

    def test_agora_explicito_aware(self):
        convite = convite_em_memoria(expira_aware(FUTURO))
        assert convite.esta_pendente(agora=agora_utc()) is True


class TestCausaRaizDocumentada:
    def test_comparar_aware_com_utcnow_levanta(self):
        """
        Este é o bug original. Se `esta_pendente()` voltar a usar datetime
        naive, os testes acima quebram — e este continua passando, documentando
        a causa.
        """
        aware = agora_utc() + FUTURO
        with pytest.raises(TypeError, match="offset-naive and offset-aware"):
            _ = aware > datetime.utcnow()

    def test_comparar_aware_com_utcnow_corretido_nao_levanta(self):
        aware = agora_utc() + FUTURO
        assert (como_utc(aware) > como_utc(agora_utc())) is True


# ===========================================================================
# Status do convite (admin e equipe devem concordar)
# ===========================================================================

class TestStatusDoConvite:
    def _status(self, expira, aceito=None, revogado=None):
        from app.admin.marketplaces import _status_convite as admin_status
        from app.establishments.team import _status_convite as equipe_status

        convite = convite_em_memoria(expira, aceito=aceito, revogado=revogado)
        return admin_status(convite), equipe_status(convite)

    @pytest.mark.parametrize("rotulo,expira,esperado", [
        ("aware futuro", expira_aware(FUTURO), "pending"),
        ("naive futuro", expira_naive(FUTURO), "pending"),
        ("aware passado", expira_aware(-PASSADO), "expired"),
        ("naive passado", expira_naive(-PASSADO), "expired"),
    ])
    def test_status_nos_dois_formatos(self, rotulo, expira, esperado):
        admin, equipe = self._status(expira)
        assert admin == esperado, rotulo
        assert equipe == esperado, rotulo

    def test_aceito(self):
        for expira in (expira_aware(FUTURO), expira_naive(FUTURO)):
            admin, equipe = self._status(expira, aceito=agora_utc())
            assert admin == equipe == "accepted"

    def test_revogado(self):
        for expira in (expira_aware(FUTURO), expira_naive(FUTURO)):
            admin, equipe = self._status(expira, revogado=agora_utc())
            assert admin == equipe == "revoked"

    def test_as_duas_funcoes_nunca_divergem(self):
        casos = [
            (expira_aware(FUTURO), None, None),
            (expira_naive(FUTURO), None, None),
            (expira_aware(-PASSADO), None, None),
            (expira_naive(-PASSADO), None, None),
            (expira_aware(FUTURO), agora_utc(), None),
            (expira_naive(FUTURO), None, agora_utc()),
        ]
        for expira, aceito, revogado in casos:
            admin, equipe = self._status(expira, aceito, revogado)
            assert admin == equipe, f"divergiram com expira={expira}"


# ===========================================================================
# validar_convite e aceitar_convite com objeto aware injetado
# ===========================================================================

class TestServicoComExpiresAware:
    """O serviço recebe do banco um invite com fuso (PostgreSQL)."""

    def _injetar_aware(self, monkeypatch, expira):
        """Faz buscar_por_token devolver um convite com expires_at aware."""
        fake = convite_em_memoria(expira)

        def buscar(token, sess=None):
            return fake if token == "token-ok" else None

        monkeypatch.setattr(svc, "buscar_por_token", buscar)
        return fake

    def test_validar_aceita_aware(self, app, monkeypatch):
        self._injetar_aware(monkeypatch, expira_aware(FUTURO))
        with app.app_context():
            info = svc.validar_convite("token-ok")
            assert info["role"] == "owner"

    def test_validar_recusa_aware_expirado(self, app, monkeypatch):
        self._injetar_aware(monkeypatch, expira_aware(-PASSADO))
        with app.app_context():
            with pytest.raises(svc.ErroConvite) as erro:
                svc.validar_convite("token-ok")
            assert erro.value.codigo == "invalid_invitation"

    def test_validar_recusa_naive_expirado(self, app, monkeypatch):
        self._injetar_aware(monkeypatch, expira_naive(-PASSADO))
        with app.app_context():
            with pytest.raises(svc.ErroConvite) as erro:
                svc.validar_convite("token-ok")
            assert erro.value.codigo == "invalid_invitation"

    def test_erro_generico_e_o_mesmo(self, app, monkeypatch):
        """Awake e expirado dão a mesma mensagem, como o frontend espera."""
        mensagens = set()
        for expira in (expira_aware(-PASSADO), expira_naive(-PASSADO)):
            self._injetar_aware(monkeypatch, expira)
            with app.app_context():
                with pytest.raises(svc.ErroConvite) as erro:
                    svc.validar_convite("token-ok")
                mensagens.add(erro.value.codigo + "|" + erro.value.mensagem)
        assert len(mensagens) == 1


class TestValidarEAceitarNoBanco:
    """Caminho completo, com o que o SQLite de fato grava (naive)."""

    def test_valida_convite_pendente(self, app, client, base):
        dados = criar_convite_persistido(app, base)
        r = client.post("/api/v1/invitations/validate", json={"token": dados["token"]})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["establishment_name"] == "Mercado"

    def test_valida_convite_expirado(self, app, client, base):
        dados = criar_convite_persistido(app, base, delta=-PASSADO)
        r = client.post("/api/v1/invitations/validate", json={"token": dados["token"]})
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "invalid_invitation"

    def test_aceita_convite_pendente(self, app, client, base):
        dados = criar_convite_persistido(app, base)
        r = client.post("/api/v1/invitations/accept", json={
            "token": dados["token"], "name": "Dono",
            "email": "dono@test.com", "password": "senha1234",
        })
        assert r.status_code == 201, r.get_json()
        assert r.get_json()["role"] == "owner"
        with app.app_context():
            assert db.session.query(User).count() == 1

    def test_aceita_convite_expirado_nao_cria_conta(self, app, client, base):
        dados = criar_convite_persistido(app, base, delta=-PASSADO)
        r = client.post("/api/v1/invitations/accept", json={
            "token": dados["token"], "name": "Dono",
            "email": "dono@test.com", "password": "senha1234",
        })
        assert r.status_code == 400
        with app.app_context():
            assert db.session.query(User).count() == 0

    def test_convite_aceito_nao_e_reusavel(self, app, client, base):
        dados = criar_convite_persistido(app, base)
        primeiro = client.post("/api/v1/invitations/accept", json={
            "token": dados["token"], "name": "Dono",
            "email": "dono@test.com", "password": "senha1234",
        })
        assert primeiro.status_code == 201
        segundo = client.post("/api/v1/invitations/accept", json={
            "token": dados["token"], "name": "Outro",
            "email": "outro@test.com", "password": "senha1234",
        })
        assert segundo.status_code == 400
        assert segundo.get_json()["error"]["code"] == "invalid_invitation"

    def test_revogado_barra_o_aceite(self, app, client, base):
        dados = criar_convite_persistido(app, base)
        with app.app_context():
            convite = db.session.get(Invitation, dados["id"])
            convite.revoked_at = agora_utc()
            db.session.commit()

        r = client.post("/api/v1/invitations/accept", json={
            "token": dados["token"], "name": "Dono",
            "email": "dono@test.com", "password": "senha1234",
        })
        assert r.status_code == 400
        with app.app_context():
            assert db.session.query(User).count() == 0


class TestValoresGravaosComFuso:
    """O que o serviço grava precisa sair com fuso."""

    def test_expires_at_aware(self, app, base):
        criar_convite_persistido(app, base)
        with app.app_context():
            convite = db.session.query(Invitation).first()
            # o SQLite zera o fuso ao gravar; o que importa é que o valor
            # gravado era aware antes disso
            assert como_utc(convite.expires_at) is not None

    def test_revoked_at_aware(self, app, base):
        dados = criar_convite_persistido(app, base)
        with app.app_context():
            antes = agora_utc()
            svc.revogar_pendentes(db.session, base["est_id"], "owner", agora=antes)
            db.session.commit()
            convite = db.session.get(Invitation, dados["id"])
            assert como_utc(convite.revoked_at) is not None

    def test_converted_at_aware_no_lead(self, app):
        from app.models import Lead, LeadStatus
        from services.leads import update_lead_status

        with app.app_context():
            lead = Lead(
                type=__import__("app.models", fromlist=["LeadType"]).LeadType.person,
                name="Pessoa", phone="19999990000", status=LeadStatus.new,
            )
            db.session.add(lead)
            db.session.commit()

            update_lead_status(lead.id, LeadStatus.converted)
            db.session.commit()
            db.session.refresh(lead)

            assert como_utc(lead.converted_at) is not None


# ===========================================================================
# Varredura: datetime.utcnow não pode voltar
# ===========================================================================

def _tokens_de_codigo(caminho: pathlib.Path):
    """Lê o arquivo ignorando comentários e strings literais."""
    with caminho.open("rb") as arquivo:
        try:
            for token in tokenize.tokenize(arquivo.readline):
                if token.type in (tokenize.COMMENT, tokenize.STRING):
                    continue
                yield token.string
        except (tokenize.TokenError, IndentationError, UnicodeDecodeError):
            return


ARQUIVOS_VARRIDOS = sorted(
    list((RAIZ / "app").rglob("*.py")) + list((RAIZ / "services").rglob("*.py"))
)


class TestVarredura:
    def test_existe_arquivo_para_varrer(self):
        assert len(ARQUIVOS_VARRIDOS) > 5, "a varredura não achou arquivos"

    def test_nenhum_utcnow_em_codigo(self):
        """
        Varre app/ e services/ procurando `utcnow`.

        Comentários e docstrings são ignorados de propósito: o timeutils.py
        precisa mencionar o nome proibido para explicar a regra.
        """
        achados = []
        for caminho in ARQUIVOS_VARRIDOS:
            for token in _tokens_de_codigo(caminho):
                if "utcnow" in token:
                    achados.append(f"{caminho.relative_to(RAIZ)}: {token}")

        assert not achados, (
            "datetime.utcnow() voltou (devolve naive e quebra a comparação com "
            "TIMESTAMPTZ):\n  " + "\n  ".join(achados)
        )

    def test_nenhum_datetime_now_sem_timezone(self):
        """
        `datetime.now()` sem fuso tem o mesmo problema.

        Exceção: o próprio app/timeutils.py, que é o lugar sancionado para
        construir a data de agora — e lá ela vem com `timezone.utc`.
        """
        achados = []
        for caminho in ARQUIVOS_VARRIDOS:
            if caminho.name == "timeutils.py":
                continue
            tokens = list(_tokens_de_codigo(caminho))
            for i in range(len(tokens) - 2):
                if tokens[i] == "datetime" and tokens[i + 1] == "." and tokens[i + 2] == "now":
                    achados.append(f"{caminho.relative_to(RAIZ)}: datetime.now(...)")
        assert not achados, (
            "datetime.now() sem timezone=UTC devolve naive:\n  " + "\n  ".join(achados)
        )

    def test_timeutils_constroi_a_data_com_fuso(self):
        """O helper sancionado usa timezone.utc de verdade."""
        texto = (RAIZ / "app" / "timeutils.py").read_text(encoding="utf-8")
        assert "datetime.now(UTC)" in texto
        assert "timezone.utc" in texto

    def test_nenhum_arquivo_usa_o_timeutils_como_alias_da_data(self):
        """
        Quem grava instante de agora tem que chamar `agora_utc()`.

        Verifica o padrão real do código, não uma string solta: procura
        `<algo>atribui<algo> = <algo>.utcnow()` / `.now()` sem fuso.
        """
        padroes = []
        for caminho in ARQUIVOS_VARRIDOS:
            if caminho.name == "timeutils.py":
                continue
            texto = caminho.read_text(encoding="utf-8")
            for linha in texto.splitlines():
                alvo = linha.strip()
                # ignora comentário
                if alvo.startswith("#"):
                    continue
                if "utcnow()" in alvo or "datetime.now()" in alvo:
                    padroes.append(f"{caminho.relative_to(RAIZ)}: {alvo}")

        assert not padroes, (
            "data construída na mão; use app.timeutils.agora_utc():\n  "
            + "\n  ".join(padroes)
        )
