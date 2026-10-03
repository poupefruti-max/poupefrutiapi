"""Decoradores de autenticação para admin."""

from functools import wraps
from flask import current_app, jsonify
from flask_jwt_extended import get_jwt, get_jwt_identity, verify_jwt_in_request

from ..extensions import db
from ..models import AdminUser


def erro(code: str, message: str, status: int):
    """Resposta de erro no formato padrão da API."""
    return jsonify({"error": {"code": code, "message": message}}), status


def admin_required(fn):
    """
    Decorator que exige JWT válido com claim role == 'admin'
    e que o AdminUser ainda exista no banco.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()

        claims = get_jwt()
        if claims.get("role") != "admin":
            return erro("forbidden", "Acesso restrito a administradores."), 403

        admin_id = get_jwt_identity()
        admin = db.session.get(AdminUser, int(admin_id))
        if admin is None:
            return erro("admin_not_found", "Administrador não encontrado."), 401

        return fn(*args, **kwargs)

    return wrapper