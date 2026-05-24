from __future__ import annotations

from flask import Blueprint, jsonify, request, current_app
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import User
from app.utils.auth import login_required
from app.utils.jwt import create_access_token


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "email_and_password_required"}), 400

    user = User(email=email)
    user.set_password(password)
    db.session.add(user)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "email_already_registered"}), 409

    return jsonify({"id": user.id, "email": user.email, "created_at": user.created_at.isoformat()}), 201


@auth_bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "email_and_password_required"}), 400

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({"error": "invalid_credentials"}), 401

    token = create_access_token(
        user_id=user.id,
        email=user.email,
        secret=current_app.config["JWT_SECRET_KEY"],
        algorithm=current_app.config["JWT_ALGORITHM"],
        expires_seconds=current_app.config["JWT_EXPIRES_SECONDS"],
    )

    return jsonify({"access_token": token, "token_type": "Bearer"})


@auth_bp.get("/me")
@login_required
def me():
    user = request.current_user  # type: ignore[attr-defined]
    return jsonify({"id": user.id, "email": user.email, "created_at": user.created_at.isoformat()})

