"""
Supabase Auth JWT verification.

The Next.js frontend attaches the signed-in user's Supabase session access
token as ``Authorization: Bearer <jwt>`` on every API call. Supabase issues
these as HS256 JWTs signed with the project's JWT secret
(``SUPABASE_JWT_SECRET`` — found in Project Settings > API). We verify the
signature here and extract the ``sub`` claim (the Supabase auth user id),
which is used as ``Novel.owner_id`` to scope all data per-user.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import settings

_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """
    FastAPI dependency that verifies the Supabase session JWT and returns the
    authenticated user's id (the token's ``sub`` claim).

    Raises
    ------
    HTTPException
        401 if the token is missing, malformed, or fails signature/expiry
        verification.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
        )

    if not settings.supabase_jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SUPABASE_JWT_SECRET is not configured on the server.",
        )

    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
        ) from exc

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is missing the 'sub' (user id) claim.",
        )

    return str(user_id)
