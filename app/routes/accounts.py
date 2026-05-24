from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request

from app.extensions import db
from app.models import CustomerAccount
from app.services.account_service import (
    AccountServiceError,
    account_balance,
    build_account_outstanding,
    get_account_owned,
    record_account_payment,
)
from app.utils.auth import login_required, role_required
from app.utils.serialization import money_to_str


accounts_bp = Blueprint("accounts", __name__, url_prefix="/accounts")


def _account_json(account: CustomerAccount, *, include_balance: bool = True) -> dict:
    payload = {
        "id": account.id,
        "name": account.name,
        "phone": account.phone,
        "email": account.email,
        "notes": account.notes,
        "active": bool(account.active),
        "created_at": account.created_at.isoformat(),
    }
    if include_balance:
        payload["balance"] = money_to_str(account_balance(account.id))
    return payload


@accounts_bp.get("")
@login_required
def list_accounts():
    user = request.current_user  # type: ignore[attr-defined]
    active_only = request.args.get("active", "1") != "0"
    q = CustomerAccount.query.filter_by(user_id=user.id)
    if active_only:
        q = q.filter_by(active=True)
    rows = q.order_by(CustomerAccount.name.asc()).all()
    return jsonify([_account_json(a) for a in rows])


@accounts_bp.get("/<int:account_id>")
@login_required
def get_account(account_id: int):
    user = request.current_user  # type: ignore[attr-defined]
    account = CustomerAccount.query.filter_by(id=account_id, user_id=user.id).first()
    if not account:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_account_json(account))


@accounts_bp.post("")
@login_required
@role_required("manager")
def create_account():
    user = request.current_user  # type: ignore[attr-defined]
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400

    account = CustomerAccount(
        user_id=user.id,
        name=name,
        phone=(data.get("phone") or "").strip() or None,
        email=(data.get("email") or "").strip() or None,
        notes=(data.get("notes") or "").strip() or None,
        active=bool(data.get("active", True)),
    )
    db.session.add(account)
    db.session.commit()
    return jsonify(_account_json(account)), 201


@accounts_bp.put("/<int:account_id>")
@login_required
@role_required("manager")
def update_account(account_id: int):
    user = request.current_user  # type: ignore[attr-defined]
    account = CustomerAccount.query.filter_by(id=account_id, user_id=user.id).first()
    if not account:
        return jsonify({"error": "not_found"}), 404

    data = request.get_json(silent=True) or {}
    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name_required"}), 400
        account.name = name
    if "phone" in data:
        account.phone = (data.get("phone") or "").strip() or None
    if "email" in data:
        account.email = (data.get("email") or "").strip() or None
    if "notes" in data:
        account.notes = (data.get("notes") or "").strip() or None
    if "active" in data:
        account.active = bool(data.get("active"))

    db.session.commit()
    return jsonify(_account_json(account))


def _parse_amount(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


@accounts_bp.get("/<int:account_id>/outstanding")
@login_required
def account_outstanding(account_id: int):
    user = request.current_user  # type: ignore[attr-defined]
    payload = build_account_outstanding(user.id, account_id)
    if not payload:
        return jsonify({"error": "not_found", "message": "Account not found."}), 404
    return jsonify(payload)


@accounts_bp.post("/<int:account_id>/settle")
@login_required
def settle_account(account_id: int):
    user = request.current_user  # type: ignore[attr-defined]
    staff = getattr(request, "current_staff", None)
    account = get_account_owned(user.id, account_id)
    if not account:
        return jsonify({"error": "not_found", "message": "Account not found."}), 404

    data = request.get_json(silent=True) or {}
    amount = _parse_amount(data.get("amount"))
    if amount is None or amount <= 0:
        return jsonify({"error": "amount_invalid", "message": "Enter a valid payment amount."}), 400

    note = (data.get("note") or "").strip() or None

    try:
        entry = record_account_payment(
            account=account,
            amount=amount,
            staff_id=staff.id if staff else None,
            note=note,
        )
        db.session.commit()
    except AccountServiceError as e:
        db.session.rollback()
        if e.code == "amount_exceeds_balance":
            bal = money_to_str(account_balance(account.id))
            return (
                jsonify(
                    {
                        "error": e.code,
                        "message": f"Payment cannot exceed balance owing ({bal}).",
                        "balance": bal,
                    }
                ),
                400,
            )
        return jsonify({"error": e.code, "message": "Invalid payment amount."}), 400

    return (
        jsonify(
            {
                "ok": True,
                "entry_id": entry.id,
                "amount": money_to_str(amount),
                "balance": money_to_str(account_balance(account.id)),
                "account": _account_json(account),
            }
        ),
        201,
    )
