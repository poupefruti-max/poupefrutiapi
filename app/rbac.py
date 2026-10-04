"""Papéis e permissões — fonte única de verdade.

Qualquer módulo que precise dos papéis importa daqui, para não repetir
listas de strings em models, services e rotas.
"""

import enum


class MemberRole(enum.Enum):
    """Papéis possíveis dentro de um estabelecimento."""

    owner = "owner"
    manager = "manager"
    staff = "staff"


#: Conjunto de papéis aceitos, útil para validação rápida.
VALID_ROLES = {r.value for r in MemberRole}


def normalizar_role(valor) -> str | None:
    """Aceita string ou MemberRole e devolve o valor canônico, ou None."""
    if isinstance(valor, MemberRole):
        return valor.value
    if isinstance(valor, str):
        candidato = valor.strip().lower()
        if candidato in VALID_ROLES:
            return candidato
    return None


def role_valida(valor) -> bool:
    """True se o valor (string ou MemberRole) for um papel conhecido."""
    return normalizar_role(valor) is not None


class EstablishmentStatus(enum.Enum):
    """Situação do estabelecimento no sistema."""

    pending = "pending"
    active = "active"
    suspended = "suspended"


VALID_ESTABLISHMENT_STATUS = {s.value for s in EstablishmentStatus}


# ===========================================================================
# Matriz de permissões
# ===========================================================================
#
# Fonte única de verdade. Qualquer rota que precise saber "esse papel pode fazer
# isso?" consulta PERMISSOES — não há lista de roles espalhada pelo código.
#
# Um papel ausente de um conjunto significa "não pode".

PERMISSOES: dict[str, set[str]] = {
    # --- leitura (todos) ---
    "ver_perfil":        {"owner", "manager", "staff"},
    "ver_equipe":        {"owner", "manager", "staff"},

    # --- escrita no próprio estabelecimento ---
    "editar_perfil":     {"owner", "manager"},
    "publicar":          {"owner", "manager"},

    # --- convites ---
    "convidar_staff":    {"owner", "manager"},
    "convidar_manager":  {"owner"},
    # Papel owner não é concedível por membros: só a administração da plataforma
    # cria owner, via fluxo de aprovação.
    "convidar_owner":    set(),

    # --- gestão da equipe ---
    "alterar_papel":     {"owner", "manager"},
    "remover_membro":    {"owner", "manager"},
}

#: Quem pode agir sobre quem. Chave = papel do alvo, valor = quem pode agir.
#: Um manager só mexe em staff; owner mexe em qualquer um.
ALVOS_POR_PAPEL: dict[str, set[str]] = {
    "owner":   {"owner"},
    "manager": {"owner"},
    "staff":   {"owner", "manager"},
}


def pode_acao(acao: str, role) -> bool:
    """True se o papel tem permissão para a ação. Papel desconhecido => False."""
    papel = normalizar_role(role)
    if papel is None or acao not in PERMISSOES:
        return False
    return papel in PERMISSOES[acao]


def pode_agir_sobre(ator_role, alvo_role) -> bool:
    """True se o ator pode alterar/remover alguém com aquele papel."""
    ator = normalizar_role(ator_role)
    alvo = normalizar_role(alvo_role)
    if ator is None or alvo is None:
        return False
    return ator in ALVOS_POR_PAPEL.get(alvo, set())


def pode_conceder_papel(ator_role, papel_concedido) -> bool:
    """True se o ator pode atribuir este papel a alguém.

    Derivado de propósito de PERMISSOES: um papel é concedível exatamente
    quando o ator pode *convidar* alguém com ele. Assim o caminho do convite e
    o caminho da alteração de papel usam a MESMA regra e não podem divergir.

    Efeito: só owner concede manager (porque `convidar_manager` é só de owner), e
    manager concede apenas staff. Owner não concede owner — quem cria owner é a
    administração da plataforma, pelo fluxo de aprovação.
    """
    alvo = normalizar_role(papel_concedido)
    if alvo is None:
        return False
    return pode_acao(f"convidar_{alvo}", ator_role)


def papeis_convidaveis(ator_role) -> set[str]:
    """Quais papéis o ator pode conceder, conforme a matriz."""
    ator = normalizar_role(ator_role)
    if ator is None:
        return set()
    return {
        papel for papel in VALID_ROLES
        if pode_acao(f"convidar_{papel}", ator)
    }


def eh_convite_permitido(ator_role, papel_convidado) -> bool:
    """True se o ator pode gerar um convite com este papel."""
    return pode_conceder_papel(ator_role, papel_convidado)


# ===========================================================================
# CNPJ
# ===========================================================================

def _digitos_cnpj(cnpj: str) -> str:
    return "".join(c for c in (cnpj or "") if c.isdigit())


#: Pesos do módulo 11 para os dois dígitos verificadores do CNPJ.
#: O ciclo é 2..9, mas a posição inicial muda conforme o tamanho da base.
_PESOS_CNPJ: dict[int, list[int]] = {
    12: [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2],
    13: [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2],
}


def _digito_verificador_cnpj(base: list[int]) -> int:
    """Calcula um dígito verificador de CNPJ (módulo 11)."""
    pesos = _PESOS_CNPJ.get(len(base))
    if pesos is None:
        raise ValueError(f"base de tamanho inválido para CNPJ: {len(base)}")
    soma = sum(d * p for d, p in zip(base, pesos))
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto


def cnpj_valido(cnpj) -> bool:
    """True se o CNPJ tem 14 dígitos com os dois verificadores corretos."""
    digitos = _digitos_cnpj(cnpj)
    if len(digitos) != 14:
        return False

    # Rejeita sequências repetidas (00000000000000, 11111111111111, ...).
    if len(set(digitos)) == 1:
        return False

    numeros = [int(c) for c in digitos]

    primeiro = _digito_verificador_cnpj(numeros[:12])
    if primeiro != numeros[12]:
        return False

    segundo = _digito_verificador_cnpj(numeros[:12] + [primeiro])
    return segundo == numeros[13]


def normalizar_cnpj(cnpj) -> str:
    """Só os dígitos: '12.345.678/0001-95' -> '12345678000195'."""
    return _digitos_cnpj(cnpj)


def formatar_cnpj(cnpj) -> str:
    """Formata em 00.000.000/0000-00 (ou devolve vazio)."""
    digitos = _digitos_cnpj(cnpj)
    if len(digitos) != 14:
        return digitos
    return f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}"
