"""Configuração global para pytest."""

import sys
from pathlib import Path

# Adiciona o diretório api ao path para imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from app import create_app
from app.extensions import db


@pytest.fixture(scope="session")
def app():
    """Cria app Flask para testes."""
    app = create_app("testing")
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SQLALCHEMY_ENGINE_OPTIONS": {},
        "WTF_CSRF_ENABLED": False,
    })

    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture(scope="function")
def session(app):
    """Provê sessão do banco para testes com rollback automático."""
    with app.app_context():
        yield db.session
        db.session.rollback()
