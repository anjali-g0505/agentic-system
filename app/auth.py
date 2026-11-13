"""JWT session. The principal (user id + role) comes from cardholders.csv at login and travels only in the token."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import data
from app.config import settings
from app.rbac import ALL_ROLES, Principal

bearer = HTTPBearer(auto_error=False)


async def issue_token(user_id: str) -> tuple[str, dict]:
    user = await data.get_user(user_id.strip().upper())
    if user is None or user["role"] not in ALL_ROLES:
        raise HTTPException(401, {"code": "unknown_user", "message": "Unknown user"})
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRY_MINUTES)
    token = jwt.encode({"sub": user["cardholder_id"], "role": user["role"], "name": user["name"], "exp": exp},
                       settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, {"user_id": user["cardholder_id"], "role": user["role"], "name": user["name"]}


async def current_principal(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
    if creds is None:
        raise HTTPException(401, {"code": "not_authenticated", "message": "Sign in first"})
    try:
        claims = jwt.decode(creds.credentials, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(401, {"code": "invalid_token", "message": "Session expired or invalid"})
    return Principal(user_id=claims["sub"], role=claims["role"])
