"""Configuração da aplicação, lida de variáveis de ambiente."""

import os

from dotenv import load_dotenv

# Carrega o .env da raiz da API antes de ler qualquer variável.
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    # --- Banco de dados ---------------------------------------------------
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/poupefruti"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

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


class DevelopmentConfig(Config):
    DEBUG = _as_bool(os.getenv("FLASK_DEBUG"), True)


class ProductionConfig(Config):
    DEBUG = False

    def __init__(self) -> None:
        if self.SECRET_KEY.startswith("dev-"):
            raise RuntimeError("Defina SECRET_KEY antes de rodar em produção.")
        if self.JWT_SECRET_KEY.startswith("dev-"):
            raise RuntimeError("Defina JWT_SECRET_KEY antes de rodar em produção.")


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}


def get_config(name: str | None = None):
    return CONFIG_MAP.get(name or os.getenv("FLASK_ENV", "default"))