"""Cache em memória, por processo, para as rotas públicas.

Por que existe
--------------
A landing chama `GET /api/v1/public/stats` a cada visita, e a resposta muda
no máximo uma vez por dia de mercado. Sem cache, cada visita seria uma consulta
com agregação em duas tabelas — para um número que não muda.

Por que à mão
-------------
Não há Redis nesta fase e não vale introduzir dependência nova (nem um servidor
novo) para guardar quatro números por 60 segundos. O flask-caching resolveria,
mas ele traz um backend junto e a manutenção disso não se justifica aqui.

O que este cache NÃO faz
------------------------
* Não sobrevive a restart do worker. Isso é intencional: é por isso que 60 s
  depois a pessoa vê o número de novo.
* Não é compartilhado entre processos. Com mais de um worker cada um tem o seu,
  e cada um recontaria uma vez por janela. O número exibido continua correto,
  só pode vir de consultas diferentes — para uma contagem que muda no dia, isso
  é irrelevante.
* Não guarda resposta de erro. Só 200 entra no cache: servir um 400 por um
  minuto inteiro transformaria um erro de digitação em algo persistente.
"""

import time

from flask import current_app, request

#: Tempo de validade do cache, em segundos.
TTL_PADRAO = 60

#: Isolado em uma função para os testes poderem controlar o relógio.
_relogio = time.monotonic


def _chave() -> str:
    """
    Chave da resposta em cache: caminho + query inteira, ordenada.

    Incluir TODOS os argumentos da query (não só o que interessa) evita
    cache poisoning por parâmetro esquecido: duas requisições diferentes nunca
    caem na mesma entrada.

    A única exceção é o `bairro`, que entra já canonicalizado. "?bairro=Centro"
    e "?bairro=centro" são a MESMA pergunta e produzem a MESMA resposta — se
    entrassem como chaves diferentes, quem digitasse uma grafia e outra
    receberia duas entradas iguais do mesmo número. A rota já valida e
    canonicaliza o bairro; aqui é só repetir o mesmo caminho para que a chave
    seja a da resposta.
    """
    from ..validators import bairro_canonico

    partes = [request.path]
    for chave in sorted(request.args.keys()):
        for valor in sorted(request.args.getlist(chave)):
            if chave == "bairro":
                valor = bairro_canonico(valor) or valor
            partes.append(f"{chave}={valor}")
    return "|".join(partes)


def _dicionario():
    """Cache do app. Guardado em `app.extensions`, não em global do módulo."""
    return current_app.extensions.setdefault("poupefruti_cache", {})


def limpar_cache(app=None) -> None:
    """Esvazia o cache. Usado pelos testes entre um cenário e outro."""
    if app is not None:
        app.extensions.pop("poupefruti_cache", None)
        return
    if current_app:
        current_app.extensions.pop("poupefruti_cache", None)


def _como_resposta(retorno):
    """
    Normaliza o retorno da view para (Response, status).

    As rotas deste projeto devolvem `(jsonify(...), 200)` — tupla, não Response.
    Sem esta normalização, o cache receberia uma tupla e quebraria em
    `resposta.status_code`.

    Response pronta (por exemplo, um dict cru): aí não há o que guardar, e o
    retorno original segue intacto para o Flask montar como sempre.
    """
    from flask import Response

    corpo, status = retorno, None
    if isinstance(retorno, tuple):
        if len(retorno) == 1:
            corpo = retorno[0]
        else:
            corpo, status = retorno[0], retorno[1]

    if not isinstance(corpo, Response):
        return None, 500

    if status is None:
        return corpo, corpo.status_code

    try:
        return corpo, int(str(status).split()[0])
    except (TypeError, ValueError):
        return None, 500


def cache_60s(ttl: int = TTL_PADRAO):
    """
    Memoriza a resposta de 200 por `ttl` segundos, por chave de requisição.

    Devolve uma `Response` nova, montada a partir do corpo guardado — não o
    objeto original, que já foi consumido pelo ciclo da requisição.
    """

    def deco(view):
        def envolvida(*args, **kwargs):
            chave = _chave()
            agora = _relogio()
            entrada = _dicionario().get(chave)

            if entrada is not None:
                expira_em, corpo, status = entrada
                if agora < expira_em:
                    return current_app.response_class(
                        corpo, status=status, mimetype="application/json"
                    )
                # Entrado vencido: removida agora para não crescer sem parar.
                _dicionario().pop(chave, None)

            retorno = view(*args, **kwargs)
            resposta, status = _como_resposta(retorno)

            # Só 200 JSON entra no cache. Erro (400, 404, 429, 500) passa
            # direto e sem guardar.
            if resposta is not None and status == 200:
                _dicionario()[chave] = (agora + ttl, resposta.get_data(), 200)

            return retorno

        # Preserva o nome, para o Flask não se perder ao montar as rotas.
        envolvida.__name__ = getattr(view, "__name__", "view")
        envolvida.__doc__ = view.__doc__
        return envolvida

    return deco