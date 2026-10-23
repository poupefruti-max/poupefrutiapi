"""Testes TDD para app/config.py — hardening para produção.

Cobre três problemas:
  A) ProductionConfig.__init__ nunca roda: get_config() retorna a classe, não
     uma instância, e from_object só copia atributos — nunca chama __init__.
  B) get_config() sem FLASK_ENV cai no DevelopmentConfig com segredos dev-*.
     Só funciona de verdade quando A é consertado (instância dispara __init__).
  C) DATABASE_URL não é normalizada para postgresql+psycopg:// + sslmode=require.
"""

import pytest
from unittest.mock import patch

from app.config import (
    Config,
    DevelopmentConfig,
    ProductionConfig,
    TestingConfig,
    get_config,
)


class TestNormalizeDatabaseUrl:
    """Problem C: _normalize_database_url garante psycopg + sslmode=require."""

    @pytest.mark.parametrize(
        "url, expected",
        [
            # postgresql:// → postgresql+psycopg:// + sslmode=require
            (
                "postgresql://user:pass@host:5432/db",
                "postgresql+psycopg://user:pass@host:5432/db?sslmode=require",
            ),
            # postgresql+psycopg:// sem sslmode → adiciona sslmode
            (
                "postgresql+psycopg://user:pass@host:5432/db",
                "postgresql+psycopg://user:pass@host:5432/db?sslmode=require",
            ),
            # sslmode=prefer → sslmode=require
            (
                "postgresql://user:pass@host:5432/db?sslmode=prefer",
                "postgresql+psycopg://user:pass@host:5432/db?sslmode=require",
            ),
            # sslmode=require já presente → mantém
            (
                "postgresql+psycopg://user:pass@host:5432/db?sslmode=require",
                "postgresql+psycopg://user:pass@host:5432/db?sslmode=require",
            ),
            # outros params → adiciona sslmode=require
            (
                "postgresql://user:pass@host:5432/db?application_name=myapp",
                "postgresql+psycopg://user:pass@host:5432/db?application_name=myapp&sslmode=require",
            ),
            # sqlite → não toca
            ("sqlite:///:memory:", "sqlite:///:memory:"),
            # None → None
            (None, None),
            # string vazia → string vazia
            ("", ""),
        ],
    )
    def test_normalizes_database_url(self, url, expected):
        from app.config import _normalize_database_url

        assert _normalize_database_url(url) == expected


class TestGetConfigReturnsInstances:
    """Problem A: get_config() deve retornar instâncias, não classes."""

    def test_production_returns_instance(self):
        with patch.object(ProductionConfig, "SECRET_KEY", "valid-secret-key"), \
             patch.object(ProductionConfig, "JWT_SECRET_KEY", "valid-jwt-secret-key"), \
             patch.object(ProductionConfig, "PROXY_FIX_X_FOR", 1):
            result = get_config("production")
        assert isinstance(result, ProductionConfig)

    def test_testing_returns_instance(self):
        result = get_config("testing")
        assert isinstance(result, TestingConfig)

    def test_development_returns_instance(self):
        result = get_config("development")
        assert isinstance(result, DevelopmentConfig)

    def test_default_returns_instance(self):
        result = get_config()
        assert isinstance(result, DevelopmentConfig)


class TestProductionConfigValidation:
    """Problem B: ProductionConfig.__init__ valida segredos antes de subir."""

    def test_rejects_dev_secret_key(self):
        with patch.object(ProductionConfig, "SECRET_KEY", "dev-secret-change-me"), \
             patch.object(ProductionConfig, "JWT_SECRET_KEY", "valid-jwt-secret"):
            with pytest.raises(RuntimeError, match="SECRET_KEY"):
                ProductionConfig()

    def test_rejects_dev_jwt_secret_key(self):
        with patch.object(ProductionConfig, "SECRET_KEY", "valid-secret-key"), \
             patch.object(ProductionConfig, "JWT_SECRET_KEY", "dev-jwt-secret-change-me"):
            with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
                ProductionConfig()

    def test_rejects_high_proxy_fix(self):
        with patch.object(ProductionConfig, "SECRET_KEY", "valid-secret-key"), \
             patch.object(ProductionConfig, "JWT_SECRET_KEY", "valid-jwt-secret-key"), \
             patch.object(ProductionConfig, "PROXY_FIX_X_FOR", 3):
            with pytest.raises(RuntimeError, match="PROXY_FIX_X_FOR"):
                ProductionConfig()

    def test_accepts_valid_secrets(self):
        with patch.object(ProductionConfig, "SECRET_KEY", "valid-secret-key"), \
             patch.object(ProductionConfig, "JWT_SECRET_KEY", "valid-jwt-secret-key"), \
             patch.object(ProductionConfig, "PROXY_FIX_X_FOR", 1):
            config = ProductionConfig()
            assert config.SECRET_KEY == "valid-secret-key"
            assert config.JWT_SECRET_KEY == "valid-jwt-secret-key"

    def test_normalizes_database_url_in_production(self):
        with patch.object(ProductionConfig, "SECRET_KEY", "valid-secret-key"), \
             patch.object(ProductionConfig, "JWT_SECRET_KEY", "valid-jwt-secret-key"), \
             patch.object(ProductionConfig, "PROXY_FIX_X_FOR", 1), \
             patch.object(
                 ProductionConfig,
                 "SQLALCHEMY_DATABASE_URI",
                 "postgresql://user:pass@host:5432/db",
             ):
            config = ProductionConfig()
            assert config.SQLALCHEMY_DATABASE_URI == (
                "postgresql+psycopg://user:pass@host:5432/db?sslmode=require"
            )


class TestGetConfigProductionIntegration:
    """get_config('production') deve levantar RuntimeError com segredos dev-*."""

    def test_get_config_production_rejects_dev_secrets(self):
        with patch.object(ProductionConfig, "SECRET_KEY", "dev-secret-change-me"), \
             patch.object(ProductionConfig, "JWT_SECRET_KEY", "dev-jwt-secret-change-me"):
            with pytest.raises(RuntimeError):
                get_config("production")

    def test_get_config_production_accepts_valid_secrets(self):
        with patch.object(ProductionConfig, "SECRET_KEY", "valid-secret-key"), \
             patch.object(ProductionConfig, "JWT_SECRET_KEY", "valid-jwt-secret-key"), \
             patch.object(ProductionConfig, "PROXY_FIX_X_FOR", 1):
            result = get_config("production")
            assert isinstance(result, ProductionConfig)
            assert result.SECRET_KEY == "valid-secret-key"
