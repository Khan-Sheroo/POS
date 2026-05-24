from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models import AccountEntry, CustomerAccount, Sale, Staff, Table, TableOrder
from app.utils.manager_auth import staff_requires_manager_pin, verify_manager_pin
from app.utils.serialization import money_to_str


class AccountServiceError(Exception):
    def __init__(self, code: str, details: dict | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(code)


def account_balance(account_id: int) -> Decimal:
    charges = (
        db.session.query(func.coalesce(func.sum(AccountEntry.amount), 0))
        .filter_by(account_id=account_id, entry_type="charge")
        .scalar()
    )
    payments = (
        db.session.query(func.coalesce(func.sum(AccountEntry.amount), 0))
        .filter_by(account_id=account_id, entry_type="payment")
        .scalar()
    )
    return Decimal(str(charges or 0)) - Decimal(str(payments or 0))


def get_account_for_user(user_id: int, account_id: int) -> CustomerAccount | None:
    return CustomerAccount.query.filter_by(id=account_id, user_id=user_id, active=True).first()


def require_manager_for_account_charge(
    *,
    user_id: int,
    staff: Staff | None,
    manager_pin: str | None,
) -> None:
    if not staff_requires_manager_pin(staff):
        return
    pin = str(manager_pin or "").strip()
    if not (pin.isdigit() and len(pin) in {4, 5}):
        raise AccountServiceError("manager_pin_required")
    if not verify_manager_pin(user_id, pin):
        raise AccountServiceError("invalid_pin")


def record_account_charge(
    *,
    account: CustomerAccount,
    sale: Sale,
    staff_id: int | None,
) -> AccountEntry:
    entry = AccountEntry(
        account_id=account.id,
        sale_id=sale.id,
        entry_type="charge",
        amount=sale.total_amount,
        created_by_staff_id=staff_id,
    )
    db.session.add(entry)
    return entry


def get_account_owned(user_id: int, account_id: int) -> CustomerAccount | None:
    return CustomerAccount.query.filter_by(id=account_id, user_id=user_id).first()


def record_account_payment(
    *,
    account: CustomerAccount,
    amount: Decimal,
    staff_id: int | None,
    note: str | None = None,
) -> AccountEntry:
    if amount <= 0:
        raise AccountServiceError("amount_invalid")
    balance = account_balance(account.id)
    if amount > balance:
        raise AccountServiceError("amount_exceeds_balance")

    entry = AccountEntry(
        account_id=account.id,
        sale_id=None,
        entry_type="payment",
        amount=amount,
        note=(note or "").strip() or None,
        created_by_staff_id=staff_id,
    )
    db.session.add(entry)
    return entry


def _sale_items_json(sale: Sale) -> list[dict]:
    items: list[dict] = []
    for item in sale.items or []:
        line_total = (item.price_at_time or 0) * (item.quantity or 0)
        product = getattr(item, "product", None)
        items.append(
            {
                "product_id": item.product_id,
                "name": product.name if product else f"Product #{item.product_id}",
                "quantity": item.quantity,
                "price_at_time": money_to_str(item.price_at_time),
                "line_total": money_to_str(line_total),
            }
        )
    return items


def _charge_invoice_json(entry: AccountEntry) -> dict:
    sale = entry.sale if entry.sale_id else None
    if entry.sale_id and sale is None:
        sale = Sale.query.get(entry.sale_id)

    table_number = None
    items: list[dict] = []
    invoice_number = None
    sale_date = None

    if sale:
        invoice_number = sale.id
        sale_date = sale.created_at.isoformat() if sale.created_at else None
        items = _sale_items_json(sale)
        order = TableOrder.query.filter_by(closed_sale_id=sale.id).first()
        if order:
            table = Table.query.get(order.table_id)
            table_number = table.table_number if table else None

    return {
        "entry_id": entry.id,
        "invoice_number": invoice_number,
        "charged_at": entry.created_at.isoformat() if entry.created_at else None,
        "sale_date": sale_date,
        "amount": money_to_str(entry.amount),
        "table_number": table_number,
        "items": items,
    }


def _payment_json(entry: AccountEntry) -> dict:
    return {
        "entry_id": entry.id,
        "paid_at": entry.created_at.isoformat() if entry.created_at else None,
        "amount": money_to_str(entry.amount),
        "note": entry.note,
    }


def build_account_outstanding(user_id: int, account_id: int) -> dict | None:
    account = get_account_owned(user_id, account_id)
    if not account:
        return None

    charges = (
        AccountEntry.query.filter_by(account_id=account.id, entry_type="charge")
        .order_by(AccountEntry.created_at.desc())
        .all()
    )
    payments = (
        AccountEntry.query.filter_by(account_id=account.id, entry_type="payment")
        .order_by(AccountEntry.created_at.desc())
        .all()
    )

    total_charges = sum((c.amount or 0) for c in charges)
    total_payments = sum((p.amount or 0) for p in payments)

    return {
        "account": {
            "id": account.id,
            "name": account.name,
            "phone": account.phone,
            "email": account.email,
        },
        "balance": money_to_str(account_balance(account.id)),
        "total_charges": money_to_str(total_charges),
        "total_payments": money_to_str(total_payments),
        "invoices": [_charge_invoice_json(c) for c in charges],
        "payments": [_payment_json(p) for p in payments],
    }
