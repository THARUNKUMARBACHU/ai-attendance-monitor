"""JWT issuing (development login only) and verification.

In production an identity provider issues the tokens; only verify_token is used, with the
provider's keys configured instead of JWT_SECRET.
"""

from datetime import UTC, datetime, timedelta

import jwt
from pydantic import BaseModel, ValidationError

from attendance_ai.core.config import Settings
from attendance_ai.core.directory import User
from attendance_ai.core.errors import AuthenticationError

ALGORITHM = "HS256"


class TokenClaims(BaseModel):
    sub: str
    tenant_id: str
    product_id: str
    role: str


def issue_token(settings: Settings, user: User, product_id: str) -> tuple[str, int]:
    now = datetime.now(UTC)
    ttl = timedelta(minutes=settings.jwt_ttl_minutes)
    claims = {
        "sub": user.user_id,
        "tenant_id": user.tenant_id,
        "product_id": product_id,
        "role": user.role,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
    }
    token = jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)
    return token, int(ttl.total_seconds())


def verify_token(settings: Settings, token: str) -> TokenClaims:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            leeway=5,
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("The token has expired. Log in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("The token is invalid.") from exc
    try:
        return TokenClaims.model_validate(payload)
    except ValidationError as exc:
        raise AuthenticationError("The token is missing required claims.") from exc
