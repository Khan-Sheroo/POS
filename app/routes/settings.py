from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.extensions import db
from app.models import Setting
from app.utils.auth import login_required, role_required


settings_bp = Blueprint("settings", __name__, url_prefix="/settings")


def _settings_json(s: Setting) -> dict:
    return {"currency": s.currency, "updated_at": s.updated_at.isoformat()}


@settings_bp.get("")
@login_required
def get_settings():
    user = request.current_user  # type: ignore[attr-defined]
    s = Setting.query.filter_by(user_id=user.id).first()
    if not s:
        s = Setting(user_id=user.id, currency="ZAR")
        db.session.add(s)
        db.session.commit()
    return jsonify(_settings_json(s))


@settings_bp.put("")
@login_required
@role_required("manager")
def update_settings():
    user = request.current_user  # type: ignore[attr-defined]
    data = request.get_json(silent=True) or {}
    currency = (data.get("currency") or "").strip().upper()
    if currency not in {"ZAR", "USD", "GBP"}:
        return jsonify({"error": "currency_invalid"}), 400

    s = Setting.query.filter_by(user_id=user.id).first()
    if not s:
        s = Setting(user_id=user.id, currency=currency)
        db.session.add(s)
    else:
        s.currency = currency

    db.session.commit()
    return jsonify(_settings_json(s))

