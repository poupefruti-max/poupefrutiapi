"""Fábrica da aplicação Flask."""

import logging

from flask import Flask, jsonify

from .config import get_config
from .extensions import cors, db, jwt, migrate


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(get_config(config_name))

    _init_extensions(app)
    _register_jwt_handlers(app)
    _register_blueprints(app)
    _register_error_handlers(app)

    app.logger.setLevel(logging.INFO)
    return app


def _init_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    cors.init_app(
        app,
        resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}},
        supports_credentials=True,
    )


def _register_blueprints(app: Flask) -> None:
    from .auth import auth_bp
    from .leads import leads_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(leads_bp)


def _register_jwt_handlers(app: Flask) -> None:
    """Deixa os erros do Flask-JWT-Extended no mesmo formato JSON da API."""

    @jwt.unauthorized_loader
    def missing_token(reason: str):
        return jsonify({"error": {"code": "authorization_required", "message": reason}}), 401

    @jwt.invalid_token_loader
    def invalid_token(reason: str):
        return jsonify({"error": {"code": "invalid_token", "message": reason}}), 401

    @jwt.expired_token_loader
    def expired_token(_header: dict, _payload: dict):
        return (
            jsonify(
                {
                    "error": {
                        "code": "token_expired",
                        "message": "Token expirado. Faça login novamente.",
                    }
                }
            ),
            401,
        )


def _register_error_handlers(app: Flask) -> None:
    """Garante que toda resposta de erro saia no mesmo formato JSON."""

    @app.errorhandler(404)
    def not_found(_err):
        return jsonify({"error": {"code": "not_found", "message": "Recurso não encontrado."}}), 404

    @app.errorhandler(405)
    def method_not_allowed(_err):
        return (
            jsonify(
                {"error": {"code": "method_not_allowed", "message": "Método não permitido."}}
            ),
            405,
        )

    @app.errorhandler(500)
    def internal_error(err):
        db.session.rollback()
        app.logger.exception("Erro interno: %s", err)
        return (
            jsonify(
                {
                    "error": {
                        "code": "internal_error",
                        "message": "Erro interno no servidor.",
                    }
                }
            ),
            500,
        )