"""O maior valor de cada enum tem que caber na coluna.

Regressão real: a migração 004 declarava `establishments.status` como
`sa.String(7)`, mas 'suspended' tem 9 letras. O `models.py` (que dimensiona
pelo maior valor do enum) gerava VARCHAR(9), então a migração divergia do model
— e suspender um mercado quebraria com "value too long for type character
varying(7)".

Estes testes são OFFLINE: compilam os tipos com o dialect do PostgreSQL e não
abrem conexão nenhuma.
"""

import ast
import pathlib
import re

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app import create_app
from app.extensions import db
from app.models import Establishment, Invitation, Lead, LeadStatus
from app.rbac import EstablishmentStatus, MemberRole

DIALECT = postgresql.dialect()

RAIZ = pathlib.Path(__file__).resolve().parent.parent

# (rótulo, coluna, enum correspondente)
COLUNAS_DE_ENUM = [
    ("establishments.status", Establishment.__table__.c.status, EstablishmentStatus),
    ("invitations.role", Invitation.__table__.c.role, MemberRole),
    ("leads.status", Lead.__table__.c.status, LeadStatus),
]


def ddl_da_tabela(tabela) -> str:
    """Aceita uma Table do SQLAlchemy."""
    return str(CreateTable(tabela).compile(dialect=DIALECT))


class TestTamanhoDaColunaDeEnum:
    @pytest.mark.parametrize("rotulo,coluna,enum_cls", COLUNAS_DE_ENUM,
                             ids=[c[0] for c in COLUNAS_DE_ENUM])
    def test_maior_valor_cabe_na_coluna(self, rotulo, coluna, enum_cls):
        maior = max(enum_cls, key=lambda e: len(e.value))
        tamanho = getattr(coluna.type, "length", None)

        assert tamanho is not None, f"{rotulo}: coluna sem tamanho definido"
        assert tamanho >= len(maior.value), (
            f"{rotulo}: coluna é VARCHAR({tamanho}) e não comporta "
            f"'{maior.value}' ({len(maior.value)} caracteres)"
        )

    @pytest.mark.parametrize("rotulo,coluna,enum_cls", COLUNAS_DE_ENUM,
                             ids=[c[0] for c in COLUNAS_DE_ENUM])
    def test_maior_valor_cabe_no_varchar_emitido(self, rotulo, coluna, enum_cls):
        """O DDL emitido precisa de VARCHAR >= maior valor."""
        maior = max(enum_cls, key=lambda e: len(e.value))
        ddl = ddl_da_tabela(coluna.table)

        achado = re.search(
            rf"{re.escape(coluna.name)}\s+VARCHAR\((\d+)\)", ddl, re.IGNORECASE
        )
        assert achado, f"{rotulo}: não achei VARCHAR no DDL:\n{ddl}"

        emitido = int(achado.group(1))
        assert emitido >= len(maior.value), (
            f"{rotulo}: DDL emite VARCHAR({emitido}) e não comporta "
            f"'{maior.value}' ({len(maior.value)})"
        )

    def test_suspended_cabe_em_establishments(self):
        """Regressão explícita do bug: 'suspended' tem 9 letras."""
        assert EstablishmentStatus.suspended.value == "suspended"
        assert len(EstablishmentStatus.suspended.value) == 9
        assert Establishment.__table__.c.status.type.length >= 9

    def test_leads_status_tem_nove_caracteres(self):
        """'contacted' tem 9 — o status do lead também é VARCHAR(9)."""
        assert len(LeadStatus.contacted.value) == 9
        assert Lead.__table__.c.status.type.length >= 9

    def test_enums_nao_usan_tipos_nativos(self):
        """native_enum=False: vira VARCHAR, não tipo ENUM do PostgreSQL."""
        for rotulo, coluna, _ in COLUNAS_DE_ENUM:
            tipo = coluna.type
            assert tipo.native_enum is False, (
                f"{rotulo}: native_enum=True criaria um tipo ENUM no banco, "
                "e a migração 001/004 assume VARCHAR"
            )


class TestMigracao004NaoDivergirDoModel:
    """
    A migração 004 é o que roda em banco novo; se divergir do model, nasce
    schema errado. Aqui comparamos a largura declarada lá com a do model.
    """

    def _arquivo(self):
        return RAIZ / "migrations" / "versions" / "004_invitations_and_establishment_onboarding.py"

    def _larguras_de(self, arquivo, variavel):
        """
        Extrai {coluna: tamanho} das tuplas ("nome", sa.String(N)) do arquivo.
        """
        fonte = arquivo.read_text(encoding="utf-8")
        # Procura a lista `novas = [ ... ]`
        bloco = re.search(r"novas\s*=\s*\[(.*?)\]", fonte, re.DOTALL)
        assert bloco, "não achei a lista 'novas' na migração"
        pares = re.findall(
            r'\(\s*"([^"]+)"\s*,\s*sa\.String\((\d+)\)\s*\)', bloco.group(1)
        )
        return {nome: int(n) for nome, n in pares}

    def test_status_da_migracao_comporta_suspended(self):
        arquivo = self._arquivo()
        larguras = self._larguras_de(arquivo, "novas")

        assert "status" in larguras, f"status não encontrado: {larguras}"
        maior = max(EstablishmentStatus, key=lambda e: len(e.value))
        assert larguras["status"] >= len(maior.value), (
            f"migração 004 declara status como String({larguras['status']}) "
            f"e não comporta '{maior.value}' ({len(maior.value)})"
        )

    def test_larguras_da_migracao_batem_com_o_model(self):
        arquivo = self._arquivo()
        larguras = self._larguras_de(arquivo, "novas")

        # slug e phone também são String(N) no model
        esperado = {
            "slug": 160,
            "status": 9,
            "phone": 20,
        }
        for coluna, tamanho in esperado.items():
            assert larguras.get(coluna) == tamanho, (
                f"migração 004 diz {coluna}=String({larguras.get(coluna)}), "
                f"model diz String({tamanho})"
            )

    def test_migracao_declara_set_string_9_no_alter_column(self):
        """O `existing_type` do ALTER COLUMN também tem de acompanhar."""
        arquivo = self._arquivo()
        fonte = arquivo.read_text(encoding="utf-8")

        # Procura alter_column de establishments.status e confere o existing_type
        bloco = re.search(
            r'alter_column\(\s*"establishments",\s*"status".*?\)',
            fonte,
            re.DOTALL,
        )
        assert bloco, "não achei alter_column de establishments.status"

        achado = re.search(r"existing_type=sa\.String\((\d+)\)", bloco.group(0))
        assert achado, "alter_column de status sem existing_type=sa.String(N)"
        assert int(achado.group(1)) >= 9, (
            f"existing_type=String({achado.group(1)}) não comporta 'suspended'"
        )
