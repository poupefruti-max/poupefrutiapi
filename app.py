"""Aplicação Flask com integração PostgreSQL/Neon."""

import logging
from flask import Flask, jsonify, request
from dotenv import load_dotenv

load_dotenv()

from db import test_connection, close_pool
from services.users import (
    create_user,
    get_user_by_id,
    get_user_by_email,
    verify_password,
    ValidationError,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> Flask:
    app = Flask(__name__)

    @app.before_request
    def log_request():
        logger.info("%s %s", request.method, request.path)

    @app.teardown_appcontext
    def cleanup(_exc):
        pass

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok"}), 200

    @app.route("/users", methods=["POST"])
    def register_user():
        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"error": {"code": "invalid_json", "message": "Body JSON inválido"}}), 400

        username = data.get("username")
        email = data.get("email")
        phone = data.get("phone")
        password = data.get("password")
        neighborhood = data.get("neighborhood")

        try:
            user = create_user(username, email, phone, password, neighborhood)
            return jsonify(user), 201
        except ValidationError as e:
            return jsonify({"error": {"code": "validation_error", "message": str(e)}}), 400
        except Exception as e:
            logger.exception("Erro ao criar usuário: %s", e)
            return jsonify({"error": {"code": "internal_error", "message": "Erro interno no servidor"}}), 500

    @app.route("/login", methods=["POST"])
    def login():
        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"error": {"code": "invalid_json", "message": "Body JSON inválido"}}), 400

        email = data.get("email")
        password = data.get("password")

        if not email or not password:
            return jsonify({"error": {"code": "missing_credentials", "message": "Informe email e senha"}}), 400

        user = get_user_by_email(email)
        if not user or not verify_password(user["password_hash"], password):
            return jsonify({"error": {"code": "invalid_credentials", "message": "Email ou senha inválidos"}}), 401

        public_user = {k: v for k, v in user.items() if k != "password_hash"}
        return jsonify(public_user), 200

    @app.route("/users/<int:user_id>", methods=["GET"])
    def get_user(user_id: int):
        user = get_user_by_id(user_id)
        if not user:
            return jsonify({"error": {"code": "not_found", "message": "Usuário não encontrado"}}), 404
        return jsonify(user), 200

    with app.app_context():
        if not test_connection():
            logger.warning("Não foi possível conectar ao banco na inicialização")

    return app


app = create_app()

if __name__ == "__main__":
    try:
        app.run(host="0.0.0.0", port=5000, debug=True)
    finally:
        close_pool()