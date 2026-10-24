"""Configuração da aplicação, lida de variáveis de ambiente."""

import os
from urllib.parse import parse_qs, urlencode, urlunparse, urlparse

from dotenv import load_dotenv

# Carrega o .env da raiz da API antes de ler qualquer variável.
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _normalize_database_url(url: str | None) -> str | None:
    """Garante driver psycopg e sslmode=require na URL de produção.

    Não toca em URLs que não sejam PostgreSQL (ex: sqlite), para que o
    TestingConfig continue funcionando com ``sqlite:///:memory:``.
    """
    if not url or not url.startswith("postgresql"):
        return url

    parsed = urlparse(url)
    scheme = parsed.scheme
    if scheme == "postgresql":
        scheme = "postgresql+psycopg"

    params = parse_qs(parsed.query, keep_blank_values=True)
    params["sslmode"] = ["require"]

    return urlunparse(
        parsed._replace(
            scheme=scheme,
            query=urlencode(params, doseq=True),
        )
    )


class Config:
    # --- Banco de dados ---------------------------------------------------
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/poupefruti"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 300}

    # --- Segredos ---------------------------------------------------------
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-jwt-secret-change-me")

    # --- CORS -------------------------------------------------------------
    # Lista separada por vírgulas, ex.: "http://localhost:3000,https://app.exemplo.com"
    CORS_ORIGINS = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "https://poupefruti.vercel.app").split(",")
        if origin.strip()
    ]

    # --- JWT --------------------------------------------------------------
    JWT_ACCESS_TOKEN_EXPIRES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRES", 60 * 60))  # 1h
    JWT_ERROR_MESSAGE_KEY = "message"

    # --- Regras de senha --------------------------------------------------
    MIN_PASSWORD_LENGTH = int(os.getenv("MIN_PASSWORD_LENGTH", 8))

    # --- Aplicação web (links de convite) ---------------------------------
    # Base pública da SPA, usada para montar APP_BASE_URL + "/convite#token=...".
    APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:3000")

    # --- Proxy (rate limit depende do IP real) ----------------------------
    # Quantos saltos de proxy existem NA FRENTE do app. 0 = desligado.
    #
    # Render -> app é 1 salto. Se ficar 0, `request.remote_addr` é o IP do
    # proxy e todos os usuários caem no MESMO bucket do rate limit.
    # Valores > 2 são recusados em produção (ver ProductionConfig).
    PROXY_FIX_X_FOR = _as_int(os.getenv("PROXY_FIX_X_FOR"), 0)

    # --- Rate limit -------------------------------------------------------
    # Login de usuário. Dois contadores independentes:
    #   - por IP: protege a API no geral
    #   - por e-mail: protege uma conta específica de ataque dirigido
    # Ficam separados porque têm CLIENTES diferentes: numa rede NAT (escritório,
    # escola) várias pessoas compartilham o IP e merecem folga maior nele,
    # enquanto o e-mail é sempre uma pessoa só e merece aperto maior.
    LOGIN_RATE_LIMIT = os.getenv("LOGIN_RATE_LIMIT", "10 per minute;50 per hour")
    LOGIN_EMAIL_RATE_LIMIT = os.getenv("LOGIN_EMAIL_RATE_LIMIT", "6 per minute;20 per hour")


class DevelopmentConfig(Config):
    DEBUG = _as_bool(os.getenv("FLASK_DEBUG"), True)


class ProductionConfig(Config):
    DEBUG = False

    def __init__(self) -> None:
        self.SQLALCHEMY_DATABASE_URI = _normalize_database_url(self.SQLALCHEMY_DATABASE_URI)
        if self.SECRET_KEY.startswith("dev-"):
            raise RuntimeError("Defina SECRET_KEY antes de rodar em produção.")
        if self.JWT_SECRET_KEY.startswith("dev-"):
            raise RuntimeError("Defina JWT_SECRET_KEY antes de rodar em produção.")
        # Mais de 2 saltos passaria a confiar em X-Forwarded-For injetado pelo
        # cliente, que poderia escapar do rate limit (ou mirar outra pessoa).
        if self.PROXY_FIX_X_FOR > 2:
            raise RuntimeError(
                "PROXY_FIX_X_FOR nao pode passar de 2. "
                "Contar os saltos de proxy errados quebra o rate limit."
            )


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False
    # Sem proxy nos testes: o IP é o do próprio cliente de teste.
    PROXY_FIX_X_FOR = 0
    # O limiter continua LIGADO (os testes de rate limit dependem disso), mas o
    # limite global some, para não trocar um problema por outro: todos os testes
    # compartilham o IP 127.0.0.1 e o padrão de 50/hora estouraria no meio da
    # suíte. Os limites por rota (login, admin, convites) seguem valendo.
    RATELIMIT_DEFAULT = ""


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}


def get_config(name: str | None = None):
    config_class = CONFIG_MAP.get(name or os.getenv("FLASK_ENV", "default"))
    return config_class()
