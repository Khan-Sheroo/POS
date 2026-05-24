from __future__ import annotations

from decimal import Decimal

from flask import Blueprint, jsonify, request

from app.models import Sale, SaleItem
from app.services.sales_service import SalesServiceError, create_sale as create_sale_service
from app.utils.auth import login_required
from app.utils.serialization import money_to_str


sales_bp = Blueprint("sales", __name__, url_prefix="/sales")

_ERROR_MESSAGES: dict[str, str] = {
    "empty_items": "Items are required.",
    "invalid_items": "Items must be an array of {product_id, quantity}.",
    "invalid_product_id": "One or more product IDs are invalid.",
    "invalid_quantity": "Quantity must be greater than 0.",
    "invalid_payment_method": "Payment method must be cash, card, or account.",
    "product_not_found": "One or more products were not found.",
    "customer_account_id_required": "Select a customer account.",
    "account_not_found": "Customer account not found or inactive.",
    "manager_pin_required": "Manager PIN is required to charge to an account.",
    "invalid_pin": "Invalid manager PIN.",
}


def _sale_item_json(item: SaleItem) -> dict:
    line_total = (item.price_at_time or Decimal("0")) * (item.quantity or 0)
    return {
        "id": item.id,
        "product_id": item.product_id,
        "quantity": item.quantity,
        "price_at_time": money_to_str(item.price_at_time),
        "line_total": money_to_str(line_total),
        "temperature": getattr(item, "temperature", None),
        "product": (
            {
                "id": item.product.id,
                "name": item.product.name,
                "sku": item.product.sku,
            }
            if getattr(item, "product", None) is not None
            else None
        ),
    }


def _sale_json(sale: Sale) -> dict:
    account_payload = None
    if getattr(sale, "customer_account_id", None):
        from app.models import CustomerAccount

        acct = CustomerAccount.query.get(sale.customer_account_id)
        if acct:
            account_payload = {"id": acct.id, "name": acct.name}
    return {
        "id": sale.id,
        "total_amount": money_to_str(sale.total_amount),
        "payment_method": sale.payment_method,
        "customer_account": account_payload,
        "status": getattr(sale, "status", "active"),
        "voided_at": sale.voided_at.isoformat() if getattr(sale, "voided_at", None) else None,
        "created_at": sale.created_at.isoformat(),
        "items": [_sale_item_json(i) for i in sale.items],
    }


@sales_bp.post("")
@login_required
def create_sale():
    data = request.get_json(silent=True) or {}
    user = request.current_user  # type: ignore[attr-defined]
    staff = getattr(request, "current_staff", None)
    try:
        sale = create_sale_service(
            payment_method=data.get("payment_method"),
            items=data.get("items"),
            user_id=user.id,
            staff=staff,
            customer_account_id=data.get("customer_account_id"),
            manager_pin=data.get("manager_pin"),
        )
    except SalesServiceError as e:
        payload = {"error": e.code, "message": _ERROR_MESSAGES.get(e.code, "Request invalid.")}
        if e.details:
            payload.update(e.details)
        return jsonify(payload), 400

    return jsonify(_sale_json(sale)), 201


@sales_bp.get("")
@login_required
def list_sales():
    sales = Sale.query.order_by(Sale.id.desc()).limit(100).all()
    return jsonify([_sale_json(s) for s in sales])


@sales_bp.get("/<int:sale_id>")
@login_required
def get_sale(sale_id: int):
    sale = Sale.query.get(sale_id)
    if not sale:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_sale_json(sale))

