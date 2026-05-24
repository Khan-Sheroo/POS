from __future__ import annotations

from functools import wraps

import jwt
from flask import current_app, jsonify, request

from app.models import Staff, User
from app.tenant import use_tenant
from app.utils.jwt import decode_token


def _get_bearer_token() -> str | None:
    auth = request.headers.get("Authorization", "")
    if not auth:
        return None
    parts = auth.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1]


def _tenant_id_from_payload(payload: dict) -> int | None:
    raw = payload.get("tid")
    if isinstance(raw, int):
        return raw
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _get_bearer_token()
        if not token:
            return jsonify({"error": "missing_bearer_token"}), 401

        try:
            payload = decode_token(
                token=token,
                secret=current_app.config["JWT_SECRET_KEY"],
                algorithm=current_app.config["JWT_ALGORITHM"],
            )
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "token_expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "invalid_token"}), 401

        tenant_id = _tenant_id_from_payload(payload)
        if tenant_id is None:
            return jsonify({"error": "invalid_token", "message": "Missing tenant. Please log in again."}), 401

        use_tenant(tenant_id)
        request.tenant_id = tenant_id  # type: ignore[attr-defined]

        typ = payload.get("typ") or "user"
        staff = None

        if typ == "staff":
            company_user_id = payload.get("company_user_id")
            if not isinstance(company_user_id, int):
                try:
                    company_user_id = int(company_user_id)
                except (TypeError, ValueError):
                    return jsonify({"error": "invalid_token"}), 401
            user = User.query.get(int(company_user_id))
            if not user:
                return jsonify({"error": "user_not_found"}), 401
            staff = Staff.query.get(int(payload["sub"]))
            if not staff or not staff.active or staff.user_id != user.id:
                return jsonify({"error": "staff_not_found"}), 401
            from app.services.staff_session_service import is_staff_logged_in

            if not is_staff_logged_in(user.id, staff.id):
                return jsonify(
                    {"error": "staff_login_required", "message": "Please log in with your PIN first."}
                ), 403
        else:
            user = User.query.get(int(payload["sub"]))
            if not user:
                return jsonify({"error": "user_not_found"}), 401

        request.current_user = user  # type: ignore[attr-defined]
        request.current_staff = staff  # type: ignore[attr-defined]
        return fn(*args, **kwargs)

    return wrapper


def role_required(*allowed_roles: str):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            staff = getattr(request, "current_staff", None)
            if staff:
                if staff.role not in set(allowed_roles):
                    return jsonify({"error": "forbidden"}), 403
                return fn(*args, **kwargs)
            if "manager" in allowed_roles:
                return fn(*args, **kwargs)
            return jsonify({"error": "staff_login_required"}), 403

        return wrapper

    return decorator
