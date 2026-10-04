"""Gestão do estabelecimento pelo time."""

from .routes import establishments_bp
from .team import team_bp
from .auth import establishment_role_required

__all__ = ["establishments_bp", "team_bp", "establishment_role_required"]
