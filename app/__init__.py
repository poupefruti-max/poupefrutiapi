"""Fábrica da aplicação Flask."""

import logging
import sys

from flask import Flask, jsonify

from .config import get_config
from .extensions import cors, db, jwt, migrate, limiter


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(get_config(config_name))

    _init_extensions(app)
    _register_jwt_handlers(app)
    _register_blueprints(app)
    _register_error_handlers(app)
    _register_cli_commands(app)

    app.logger.setLevel(logging.INFO)
    return app


def _init_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    cors.init_app(
        app,
        resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}},
        supports_credentials=False,
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    limiter.init_app(app)


def _register_blueprints(app: Flask) -> None:
    from .auth import auth_bp
    from .leads import leads_bp
    from .admin import admin_auth_bp, admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(leads_bp)
    app.register_blueprint(admin_auth_bp)
    app.register_blueprint(admin_bp)


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


def _register_cli_commands(app: Flask) -> None:
    """Registra comandos CLI personalizados."""

    @app.cli.command("create-admin")
    def create_admin():
        """Cria um usuário administrador (pede e-mail e senha no terminal)."""
        import bcrypt

        from .models import AdminUser

        email = input("E-mail do admin: ").strip().lower()
        if not email:
            print("E-mail não pode ser vazio.", file=sys.stderr)
            sys.exit(1)

        password = input("Senha: ").strip()
        if not password:
            print("Senha não pode ser vazia.", file=sys.stderr)
            sys.exit(1)

        min_len = app.config["MIN_PASSWORD_LENGTH"]
        if len(password) < min_len:
            print(f"A senha deve ter ao menos {min_len} caracteres.", file=sys.stderr)
            sys.exit(1)

        name = input("Nome (opcional): ").strip() or None

        with app.app_context():
            # Verifica se já existe
            existing = AdminUser.query.filter_by(email=email).first()
            if existing:
                print(f"Admin com e-mail {email} já existe.", file=sys.stderr)
                sys.exit(1)

            admin = AdminUser(email=email, name=name)
            admin.set_password(password)

            db.session.add(admin)
            try:
                db.session.commit()
                print(f"Admin criado com sucesso! ID: {admin.id}")
            except Exception as e:
                db.session.rollback()
                print(f"Erro ao criar admin: {e}", file=sys.stderr)
                sys.exit(1)