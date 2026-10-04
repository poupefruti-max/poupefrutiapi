"""Pacote do painel admin."""

from .auth import admin_auth_bp
from .routes import admin_bp
from .marketplaces import admin_market_bp
from .decorators import admin_required

__all__ = ["admin_auth_bp", "admin_bp", "admin_market_bp", "admin_required"]
