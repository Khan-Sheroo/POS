from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.extensions import db
from app.models import Product, Sale, SaleItem, Staff
from app.services.account_service import (
    AccountServiceError,
    get_account_for_user,
    record_account_charge,
    require_manager_for_account_charge,
)


@dataclass(frozen=True)
class SalesServiceError(Exception):
    code: str
    details: dict | None = None


def _validate_payment_method(payment_method: str) -> str:
    pm = (payment_method or "").strip().lower()
    if pm not in {"cash", "card", "account"}:
        # Stable API error code used by frontend.
        raise SalesServiceError(code="invalid_payment_method")
    return pm


def _validate_items(items) -> list[dict]:
    if not isinstance(items, list) or not items:
        raise SalesServiceError(code="empty_items")

    normalized: list[dict] = []
    for idx, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise SalesServiceError(code="invalid_items", details={"index": idx})
        product_id = raw.get("product_id")
        quantity = raw.get("quantity")
        if not isinstance(product_id, int) or product_id <= 0:
            raise SalesServiceError(code="invalid_product_id", details={"index": idx})
        if not isinstance(quantity, int) or quantity <= 0:
            raise SalesServiceError(code="invalid_quantity", details={"index": idx})
        temp = None
        if raw.get("temperature") is not None:
            temp = str(raw.get("temperature") or "").strip() or None
            if temp and len(temp) > 64:
                raise SalesServiceError(code="invalid_temperature", details={"index": idx})
        normalized.append({"product_id": product_id, "quantity": quantity, "temperature": temp})

    return normalized


def _fetch_products_by_id(product_ids: set[int]) -> dict[int, Product]:
    products = Product.query.filter(Product.id.in_(product_ids)).all()
    products_by_id = {p.id: p for p in products}
    missing = [pid for pid in product_ids if pid not in products_by_id]
    if missing:
        raise SalesServiceError(code="product_not_found", details={"product_ids": sorted(missing)})
    return products_by_id


def _calculate_total(items: list[dict], products_by_id: dict[int, Product]) -> Decimal:
    total = Decimal("0")
    for it in items:
        product = products_by_id[it["product_id"]]
        price = product.price or Decimal("0")
        total += price * it["quantity"]
    return total


def create_sale(
    *,
    payment_method: str,
    items,
    user_id: int | None = None,
    staff: Staff | None = None,
    customer_account_id: int | None = None,
    manager_pin: str | None = None,
) -> Sale:
    pm = _validate_payment_method(payment_method)
    normalized_items = _validate_items(items)

    product_ids = {it["product_id"] for it in normalized_items}
    products_by_id = _fetch_products_by_id(product_ids)

    total = _calculate_total(normalized_items, products_by_id)

    account = None
    if pm == "account":
        if user_id is None:
            raise SalesServiceError(code="account_user_required")
        if not isinstance(customer_account_id, int) or customer_account_id <= 0:
            raise SalesServiceError(code="customer_account_id_required")
        account = get_account_for_user(user_id, customer_account_id)
        if not account:
            raise SalesServiceError(code="account_not_found")
        try:
            require_manager_for_account_charge(
                user_id=user_id,
                staff=staff,
                manager_pin=manager_pin,
            )
        except AccountServiceError as e:
            if e.code in {"manager_pin_required", "invalid_pin"}:
                raise SalesServiceError(code=e.code) from e
            raise

    sale = Sale(
        total_amount=total,
        payment_method=pm,
        customer_account_id=account.id if account else None,
    )
    sale.items = [
        SaleItem(
            product_id=products_by_id[it["product_id"]].id,
            quantity=it["quantity"],
            price_at_time=products_by_id[it["product_id"]].price,
            temperature=it.get("temperature"),
        )
        for it in normalized_items
    ]

    db.session.add(sale)
    db.session.flush()

    if account:
        record_account_charge(account=account, sale=sale, staff_id=staff.id if staff else None)

    db.session.commit()
    return sale

