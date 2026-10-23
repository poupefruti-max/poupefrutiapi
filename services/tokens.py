"""Operações compartilhadas de ciclo de vida dos JWTs."""

from datetime import datetime, timezone

from app.extensions import db
from app.models import RevokedToken


def revogar_token(claims: dict) -> None:
    """Registra o JTI atual até a expiração informada pelo próprio JWT."""
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        raise ValueError("JWT sem instante de expiração")
    registro = RevokedToken(
        jti=claims["jti"],
        token_type=claims.get("type", "access"),
        expires_at=datetime.fromtimestamp(exp, tz=timezone.utc),
    )
    db.session.add(registro)
    db.session.commit()

