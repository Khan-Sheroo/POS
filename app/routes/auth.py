from __future__ import annotations

from flask import Blueprint, jsonify, request, current_app
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Setting, TenantAccount, User
from app.tenant import ensure_owner_user, provision_tenant_database, use_tenant
from app.utils.auth import login_required
from app.utils.jwt import create_access_token


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _parse_tenant_id(payload: dict) -> int | None:
    raw = payload.get("tid")
    if isinstance(raw, int):
        return raw
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


@auth_bp.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "email_and_password_required"}), 400

    account = TenantAccount(email=email)
    account.set_password(password)
    db.session.add(account)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "email_already_registered"}), 409

    provision_tenant_database(account.id)
    use_tenant(account.id)
    owner = ensure_owner_user(email=email, password=password)
    if not Setting.query.filter_by(user_id=owner.id).first():
        db.session.add(Setting(user_id=owner.id, currency="ZAR"))
    db.session.commit()

    return (
        jsonify(
            {
                "tenant_id": account.id,
                "email": account.email,
                "created_at": account.created_at.isoformat(),
            }
        ),
        201,
    )


@auth_bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "email_and_password_required"}), 400

    account = TenantAccount.query.filter_by(email=email).first()
    if not account or not account.check_password(password):
        return jsonify({"error": "invalid_credentials"}), 401

    use_tenant(account.id)
    owner = ensure_owner_user(email=email, password=password)
    db.session.commit()

    token = create_access_token(
        user_id=owner.id,
        email=account.email,
        tenant_id=account.id,
        secret=current_app.config["JWT_SECRET_KEY"],
        algorithm=current_app.config["JWT_ALGORITHM"],
        expires_seconds=current_app.config["JWT_EXPIRES_SECONDS"],
    )

    return jsonify(
        {
            "access_token": token,
            "token_type": "Bearer",
            "tenant_id": account.id,
        }
    )


@auth_bp.get("/me")
@login_required
def me():
    user = request.current_user  # type: ignore[attr-defined]
    tenant_id = getattr(request, "tenant_id", None)  # type: ignore[attr-defined]
    return jsonify(
        {
            "id": user.id,
            "email": user.email,
            "tenant_id": tenant_id,
            "created_at": user.created_at.isoformat(),
        }
    )
