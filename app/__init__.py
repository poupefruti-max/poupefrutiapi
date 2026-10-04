"""Fábrica da aplicação Flask."""

import logging
import os
import sys

from flask import Flask, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import get_config
from .extensions import cors, db, jwt, migrate, limiter


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(get_config(config_name))

    _impedir_banco_real_em_teste(app)

    _init_extensions(app)
    _register_jwt_handlers(app)
    _register_blueprints(app)
    _register_error_handlers(app)
    _register_cli_commands(app)

    app.logger.setLevel(logging.INFO)
    return app


def _impedir_banco_real_em_teste(app: Flask) -> None:
    """Trava: sob pytest, só pode abrir banco de teste.

    Existe porque já aconteceu: um teste chamou `create_app("development")`,
    que lê o DATABASE_URL do .env (Neon de produção), e o `db.drop_all()` do
    teardown apagou todas as tabelas. Um erro desse tipo é irreversível.

    Aqui a falha acontece na criação do app, com mensagem clara, antes de
    qualquer conexão — em vez de no meio de um `drop_all()`.
    """
    if "PYTEST_CURRENT_TEST" not in os.environ:
        return

    uri = str(app.config.get("SQLALCHEMY_DATABASE_URI") or "")
    if uri.startswith("sqlite"):
        return

    # Não mostra a URL: ela carrega usuário e senha.
    try:
        do_ppo = uri.split("://", 1)[-1].split("@", 1)[-1].split("/", 1)[0]
    except Exception:
        do_ppo = "?"

    raise RuntimeError(
        "Trava de segurança: um teste tentou abrir o banco "
        f"'{do_ppo}' que não é de teste. Sob pytest só é permitido SQLite. "
        "Use create_app('testing') — nunca 'development'/'production' — "
        "e confira o SQLALCHEMY_DATABASE_URI antes de db.create_all()/drop_all()."
    )


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
    _aplicar_proxy_fix(app)


def _aplicar_proxy_fix(app: Flask) -> None:
    """Confia nos cabeçalhos X-Forwarded-* quando há proxy na frente.

    Sem isso, `request.remote_addr` é o IP do proxy — e o rate limit, que usa
    esse endereço como chave, vira UM limite só para todos os usuários. Bastaria
    uma pessoa esbarrando no limite para travar o login de todo mundo.

    Por isso ProxyFix é aplicado **fora da caixa**. Confiar cegamente em
    X-Forwarded-For permitiria um cliente falsificar o IP e escapar do limite
    (ou mirar outra pessoa), então só ligamos com configureção explícita e com
    a contagem de saltos exata do proxy:

    * Render -> app é **um** salto (o Render termina TLS e repassa).
      PROXY_FIX_X_FOR=1
    * Render com CDN/proxy extra -> PROXY_FIX_X_FOR=2

    `X-Proto` e `X-Host` têm um salto só; valores maiores fariam o app confiar em
    cabeçalhos injetados pelo cliente.
    """
    x_for = app.config.get("PROXY_FIX_X_FOR", 0)
    if not x_for:
        return

    ProxyFix(
        app.wsgi_app,
        x_for=int(x_for),
        x_proto=1,
        x_host=1,
        x_port=1,
        x_prefix=0,
    )
    app.logger.info("ProxyFix ativo (x_for=%s)", x_for)


def _register_blueprints(app: Flask) -> None:
    from .auth import auth_bp
    from .leads import leads_bp
    from .admin import admin_auth_bp, admin_bp, admin_market_bp
    from .invitations import invitations_bp
    from .establishments import establishments_bp, team_bp
    from .public import public_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(leads_bp)
    app.register_blueprint(admin_auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(admin_market_bp)
    app.register_blueprint(invitations_bp)
    app.register_blueprint(establishments_bp)
    app.register_blueprint(team_bp)
    app.register_blueprint(public_bp)


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

    @app.errorhandler(429)
    def rate_limited(_err):
        # O Flask-Limiter responderia HTML cru; a API fala JSON.
        return (
            jsonify(
                {
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": "Muitas tentativas. Aguarde um pouco e tente de novo.",
                    }
                }
            ),
            429,
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
