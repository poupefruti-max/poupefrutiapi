"""Autorização dos endpoints de estabelecimento.

Duas decisões importantes:

* A permissão é lida do **banco a cada requisição** (não vai no token). Assim a
  remoção ou a rebaixada de alguém vale na hora, com o token que a pessoa já
  tinha — inclusive para ela mesma.
* Quem não é membro do mercado recebe **404**, não 403. Não confirmamos a
  existência de um recurso para quem não tem relação com ele.
"""

from functools import wraps

from flask import g, jsonify
from flask_jwt_extended import get_jwt, get_jwt_identity, verify_jwt_in_request

from ..extensions import db
from ..models import Establishment, EstablishmentMember
from ..rbac import pode_acao

#: Papéis que exigem que o mercado esteja ativo para escrita.
ESCRITAS = {
    "editar_perfil", "publicar", "convidar_staff", "convidar_manager",
    "convidar_owner", "alterar_papel", "remover_membro",
}


def _erro(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


def carregar_membership(user_id: int, establishment_id: int):
    """Vínculo do usuário com o mercado, lido do banco agora."""
    return EstablishmentMember.query.filter_by(
        user_id=user_id, establishment_id=establishment_id
    ).first()


def establishment_role_required(acao: str):
    """Exige membro do mercado com permissão para `acao`.

    Deixa em `g`:
        g.establishment  -> Establishment
        g.membership     -> EstablishmentMember
        g.meu_papel      -> str
    """

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            # --- token ---
            verify_jwt_in_request()

            claims = get_jwt()
            if claims.get("role") == "admin":
                # Token de admin não vale aqui; e não damos pista de que o
                # recurso existe para ele.
                return _erro("not_found", "Recurso não encontrado.", 404)

            # --- dados da URL ---
            establishment_id = kwargs.get("establishment_id") or kwargs.get("id")
            if establishment_id is None:
                return _erro("not_found", "Recurso não encontrado.", 404)

            establishment = db.session.get(Establishment, establishment_id)
            if establishment is None:
                return _erro("not_found", "Recurso não encontrado.", 404)

            # --- vínculo (banco, sempre) ---
            membership = carregar_membership(int(get_jwt_identity()), establishment.id)
            if membership is None:
                # Não é membro: 404, não 403.
                return _erro("not_found", "Recurso não encontrado.", 404)

            meu_papel = membership.role.value if membership.role else None

            # --- permissão ---
            if not pode_acao(acao, meu_papel):
                return _erro(
                    "insufficient_permissions",
                    "Você não tem permissão para esta ação.",
                    403,
                )

            # --- bloqueio por suspensão ---
            # Leitura continua; escrita para. O admin suspende e a página some.
            if acao in ESCRITAS and establishment.status is not None:
                if establishment.status.value == "suspended":
                    return _erro(
                        "establishment_suspended",
                        "Este estabelecimento está suspenso. As alterações estão "
                        "bloqueadas; fale com o PoupeFruti.",
                        403,
                    )

            g.establishment = establishment
            g.membership = membership
            g.meu_papel = meu_papel
            return fn(*args, **kwargs)

        return wrapper

    return deco
