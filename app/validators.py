"""Validações compartilhadas entre os blueprints."""

import re
import unicodedata

# Validação de e-mail deliberadamente simples.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")

VALORES_CESTA = {"frutas", "legumes", "ambas"}

VALORES_TIPO_ESTABELECIMENTO = {"mercado", "padaria", "hortifruti", "sacolao"}

# ---------------------------------------------------------------------------
# Bairros de Campinas — lista única do projeto
# ---------------------------------------------------------------------------
#
# A lista vivia duplicada dentro de `ui/index.html`: a do formulário de CLIENTE
# tinha três itens que a do formulário de MERCADO não tinha ("Jardim(ns)
# Guanabara", "Jardim(ns) Paulistano", "Jardim(ns) Chapada"). Duas listas
# divergentes fazem o mesmo bairro virar dois valores diferentes no banco.
#
# Aqui é a fonte da verdade. Os dois <select> do HTML mostram exatamente estes
# valores, nesta ordem, e a API valida contra eles.
#
# REGRA: NÃO renomeie nem reescreva um valor que já foi gravado. "Jardim(ns)
# Guanabara" continua "Jardim(ns) Guanabara" mesmo com a grafia feia: mudar o
# texto quebraria a contagem por bairro de quem já está no banco. Para corrigir
# um bairro já gravado, acrescente o valor novo AO LADO do antigo e migre os
# dados num script à parte.
#
# O último item é a porta de escape: quem mora num bairro que não está na lista
# não é obrigado a escolher um bairro errado — e assim a resposta a "quantas
# cestas tem no meu bairro?" nunca mente sobre onde a pessoa está.

#: Bairros aceitos, na ordem em que aparecem nos <select>. O último é a porta
#: de escape e sempre fica por último.
VALORES_BAIRRO: tuple[str, ...] = (
    "Barão Geraldo",
    "Bonfim",
    "Bosque",
    "Cambuí",
    "Centro",
    "Castelo",
    "Santa Bárbara",
    "Taquaral",
    "Jardim Bandeirantes",
    "Jardim Eudes",
    "Jardim São Marcos",
    "Sousas",
    "Joaquim Egídio",
    "Padre Martins",
    "Vila Bueno",
    "Vila São Carlos",
    "Jardim(ns) Guanabara",
    "Jardim(ns) Paulistano",
    "Jardim(ns) Chapada",
    "Parque Industrial",
    "Bairro não listado",
)

#: A porta de escape, para o código que precisar do nome em vez da lista.
BAIRRO_NAO_LISTADO = VALORES_BAIRRO[-1]


def normalizar_chave_bairro(valor: str | None) -> str:
    """
    Chave de comparação de um bairro: minúsculas, sem acento e sem espaços nas
    pontas.

    Só para COMPARAR (filtro da rota pública, select do painel). O valor gravado
    é sempre o canônico de VALORES_BAIRRO.
    """
    texto = unicodedata.normalize("NFD", (valor or "").strip().casefold())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


#: Índice de busca por chave normalizada.
_BAIRRO_POR_CHAVE: dict[str, str] = {
    normalizar_chave_bairro(v): v for v in VALORES_BAIRRO
}


def bairro_canonico(valor: str | None) -> str | None:
    """
    Devolve o valor canônico do bairro, ou None se não for um bairro da lista.

    Aceita variação de caixa, acento e espaço nas pontas ("centro", " CENTRO ")
    porque a query string e o select do painel não garantem a grafia exata.
    Nomes já gravados não são normalizados por aqui: quem grava passa antes por
    esta função.
    """
    chave = normalizar_chave_bairro(valor)
    if not chave:
        return None
    return _BAIRRO_POR_CHAVE.get(chave)


def bairro_valido(valor: str | None) -> bool:
    """True quando o bairro é um valor da lista (porta de escape incluída)."""
    return bairro_canonico(valor) is not None


def normalizar_email(email: str | None) -> str:
    """Minúsculas e sem espaços nas pontas."""
    return (email or "").strip().lower()


def normalizar_texto(valor: str | None) -> str:
    """Remove espaços nas pontas. Devolve string vazia para None."""
    return (valor or "").strip()


def apenas_digitos(valor: str | None) -> str:
    return re.sub(r"\D", "", valor or "")


def whatsapp_valido(valor: str | None) -> bool:
    """
    Telefone brasileiro com DDD.

    Aceita 10 dígitos (fixo) ou 11 dígitos (celular, com o 9 na frente).
    O DDD precisa estar entre 11 e 99.
    """
    digitos = apenas_digitos(valor)
    if len(digitos) not in (10, 11):
        return False
    ddd = int(digitos[:2])
    if not 11 <= ddd <= 99:
        return False
    if len(digitos) == 11 and digitos[2] != "9":
        return False
    return True


def email_valido(valor: str | None) -> bool:
    return bool(EMAIL_RE.match(normalizar_email(valor)))
