from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt


def create_access_token(
    *,
    user_id: int,
    email: str,
    secret: str,
    algorithm: str,
    expires_seconds: int,
    extra_claims: dict | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "typ": "user",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_seconds)).timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)
    token = jwt.encode(payload, secret, algorithm=algorithm)
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


def create_staff_token(
    *,
    staff_id: int,
    company_user_id: int,
    role: str,
    secret: str,
    algorithm: str,
    expires_seconds: int,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(staff_id),
        "company_user_id": int(company_user_id),
        "role": role,
        "typ": "staff",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_seconds)).timestamp()),
    }
    token = jwt.encode(payload, secret, algorithm=algorithm)
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


def decode_token(*, token: str, secret: str, algorithm: str) -> dict:
    return jwt.decode(token, secret, algorithms=[algorithm])

