from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from app.extensions import db
from app.models import (
    CashUp,
    Category,
    Product,
    Sale,
    SaleItem,
    Staff,
    StaffCashupCompletion,
    Table,
    TableOrder,
)
from app.services.staff_session_service import get_staff_ids_for_business_date, get_trading_day_staff_ids, is_staff_logged_in
from app.services.trading_day_service import get_trading_date, trading_window, trading_window_for_date
from app.utils.serialization import money_to_str


def today_window() -> tuple[datetime, datetime]:
    """UTC calendar day (legacy). Prefer trading_window(user_id)."""
    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start, end


def business_date_for(start: datetime) -> date:
    return start.date()


def _order_bill_total(order: TableOrder, sale: Sale | None) -> Decimal:
    if sale and sale.total_amount is not None:
        return sale.total_amount
    total = Decimal("0")
    for item in order.items or []:
        total += (item.price_at_time or Decimal("0")) * (item.quantity or 0)
    return total


def _order_tip(order: TableOrder, sale: Sale | None, bill: Decimal) -> Decimal:
    tip_amount = getattr(order, "tip_amount", None)
    if tip_amount is not None and tip_amount > 0:
        return tip_amount

    tip_mode = getattr(order, "tip_mode", None)
    tip_percent = getattr(order, "tip_percent", None)
    if tip_mode == "percent" and tip_percent is not None and bill > 0:
        return (bill * tip_percent / Decimal("100")).quantize(Decimal("0.01"))

    card_amount = getattr(order, "card_amount", None)
    if sale and card_amount is not None and sale.total_amount is not None:
        tip = card_amount - sale.total_amount
        if tip > 0:
            return tip
    return Decimal("0")


def open_tables_for_staff(staff_id: int) -> list[dict]:
    orders = TableOrder.query.filter_by(status="open", opened_by_staff_id=staff_id).all()
    out: list[dict] = []
    for order in orders:
        table = Table.query.get(order.table_id)
        out.append(
            {
                "table_id": order.table_id,
                "table_number": table.table_number if table else None,
                "order_id": order.id,
            }
        )
    out.sort(key=lambda row: (row["table_number"] is None, row["table_number"] or 0))
    return out


def _closed_orders_for_day(
    start: datetime, end: datetime, staff_id: int | None = None
) -> list[TableOrder]:
    q = TableOrder.query.filter(
        TableOrder.status == "closed",
        TableOrder.updated_at >= start,
        TableOrder.updated_at < end,
    )
    if staff_id is not None:
        q = q.filter(TableOrder.opened_by_staff_id == staff_id)
    return q.order_by(TableOrder.updated_at.asc()).all()


def _aggregate_closed_orders(orders: list[TableOrder]) -> tuple[dict, list[dict]]:
    turnover = Decimal("0")
    cash_payments = Decimal("0")
    credit_payments = Decimal("0")
    account_payments = Decimal("0")
    total_tips = Decimal("0")
    cash_due = Decimal("0")
    tables: list[dict] = []

    for order in orders:
        sale = Sale.query.get(order.closed_sale_id) if order.closed_sale_id else None
        if sale and getattr(sale, "status", "active") != "active":
            continue

        table = Table.query.get(order.table_id)
        bill = _order_bill_total(order, sale)
        tip = _order_tip(order, sale, bill)
        turnover += bill
        total_tips += tip

        cash_amount: Decimal | None = None
        card_amount: Decimal | None = None
        payment_method = sale.payment_method if sale else None

        if sale and payment_method == "cash":
            cash_payments += bill
            cash_due += bill
            cash_amount = bill
        elif sale and payment_method == "card":
            credit_payments += bill
            card_amount = bill
        elif sale and payment_method == "account":
            account_payments += bill

        closed_at = sale.created_at if sale else (order.updated_at or order.created_at)

        tables.append(
            {
                "closed_at": closed_at.isoformat() if closed_at else None,
                "invoice_number": sale.id if sale else None,
                "table_number": table.table_number if table else None,
                "bill_total": money_to_str(bill),
                "cash_amount": money_to_str(cash_amount) if cash_amount is not None else None,
                "card_amount": money_to_str(card_amount) if card_amount is not None else None,
                "tip": money_to_str(tip),
                "payment_method": payment_method,
            }
        )

    summary = {
        "turnover": money_to_str(turnover),
        "cash_payments": money_to_str(cash_payments),
        "credit_payments": money_to_str(credit_payments),
        "account_payments": money_to_str(account_payments),
        "total_tips": money_to_str(total_tips),
        "cash_due_to_restaurant": money_to_str(cash_due),
    }
    return summary, tables


def completion_map(user_id: int, biz_date: date) -> dict[int, StaffCashupCompletion]:
    rows = StaffCashupCompletion.query.filter_by(user_id=user_id, business_date=biz_date).all()
    return {r.staff_id: r for r in rows}


def open_tables_for_venue() -> list[dict]:
    """All open table orders (any staff). Blocks master cash up until cleared."""
    orders = TableOrder.query.filter_by(status="open").all()
    out: list[dict] = []
    for order in orders:
        table = Table.query.get(order.table_id)
        out.append(
            {
                "table_id": order.table_id,
                "table_number": table.table_number if table else None,
                "order_id": order.id,
                "opened_by_staff_id": order.opened_by_staff_id,
            }
        )
    out.sort(key=lambda row: (row["table_number"] is None, row["table_number"] or 0))
    return out


def staff_completion_status(user_id: int, start: datetime, end: datetime, *, for_master: bool = False) -> dict:
    biz_date = business_date_for(start)
    done = completion_map(user_id, biz_date)
    participating_ids = get_staff_ids_for_business_date(user_id, biz_date)
    if not participating_ids:
        return {
            "staff": [],
            "all_complete": True,
            "pending_staff": [],
            "active_staff_count": 0,
        }
    active_staff = (
        Staff.query.filter(
            Staff.user_id == user_id,
            Staff.active.is_(True),
            Staff.id.in_(participating_ids),
        )
        .order_by(Staff.name.asc())
        .all()
    )
    staff_rows = []
    pending_names: list[str] = []
    for s in active_staff:
        exempt = for_master and getattr(s, "role", "") == "manager"
        row = done.get(s.id)
        completed = exempt or row is not None
        if not completed:
            pending_names.append(s.name)
        staff_rows.append(
            {
                "staff": {"id": s.id, "name": s.name, "role": s.role},
                "completed": completed,
                "completed_at": row.completed_at.isoformat() if row else None,
                "is_balanced": bool(row.is_balanced) if row else None,
                "exempt_from_master_requirement": exempt,
            }
        )
    return {
        "staff": staff_rows,
        "all_complete": len(pending_names) == 0,
        "pending_staff": pending_names,
        "active_staff_count": len(active_staff),
    }


def staff_cashup_is_balanced(
    expected_cash: Decimal,
    expected_credit: Decimal,
    actual_cash: Decimal,
    actual_credit: Decimal,
) -> bool:
    return actual_cash == expected_cash and actual_credit == expected_credit


def mark_staff_cashup_complete(
    user_id: int,
    staff: Staff,
    actual_cash: Decimal,
    actual_credit: Decimal,
) -> tuple[StaffCashupCompletion, bool]:
    if open_tables_for_staff(staff.id):
        raise ValueError("open_tables_remain")
    if is_staff_logged_in(user_id, staff.id):
        raise ValueError("staff_still_logged_in")

    start, end = trading_window(staff.user_id)
    biz_date = business_date_for(start)
    report = build_staff_cashup(staff, start, end)
    expected_cash = Decimal(report["summary"]["cash_payments"])
    expected_credit = Decimal(report["summary"]["credit_payments"])
    balanced = staff_cashup_is_balanced(expected_cash, expected_credit, actual_cash, actual_credit)

    existing = StaffCashupCompletion.query.filter_by(
        user_id=user_id, staff_id=staff.id, business_date=biz_date
    ).first()
    if existing:
        existing.expected_cash = expected_cash
        existing.expected_credit = expected_credit
        existing.actual_cash = actual_cash
        existing.actual_credit = actual_credit
        existing.is_balanced = balanced
        existing.completed_at = datetime.utcnow()
        db.session.commit()
        return existing, balanced

    row = StaffCashupCompletion(
        user_id=user_id,
        staff_id=staff.id,
        business_date=biz_date,
        expected_cash=expected_cash,
        expected_credit=expected_credit,
        actual_cash=actual_cash,
        actual_credit=actual_credit,
        is_balanced=balanced,
    )
    db.session.add(row)
    db.session.commit()
    return row, balanced


def _completion_json(row: StaffCashupCompletion | None) -> dict | None:
    if not row:
        return None
    return {
        "actual_cash": money_to_str(row.actual_cash) if row.actual_cash is not None else None,
        "actual_credit": money_to_str(row.actual_credit) if row.actual_credit is not None else None,
        "expected_cash": money_to_str(row.expected_cash) if row.expected_cash is not None else None,
        "expected_credit": money_to_str(row.expected_credit) if row.expected_credit is not None else None,
        "is_balanced": bool(row.is_balanced),
        "completed_at": row.completed_at.isoformat(),
    }


def build_staff_cashup(staff: Staff, start: datetime | None = None, end: datetime | None = None) -> dict:
    if start is None or end is None:
        start, end = trading_window(staff.user_id)

    orders = _closed_orders_for_day(start, end, staff_id=staff.id)
    summary, tables = _aggregate_closed_orders(orders)
    biz_date = business_date_for(start)
    done = completion_map(staff.user_id, biz_date).get(staff.id)

    completion = _completion_json(done)
    current_trading_date = get_trading_date(staff.user_id)
    is_current_trading_day = biz_date == current_trading_date
    open_tables = open_tables_for_staff(staff.id) if is_current_trading_day else []
    has_open_tables = len(open_tables) > 0
    logged_in = is_staff_logged_in(staff.user_id, staff.id) if is_current_trading_day else False
    participated = staff.id in get_staff_ids_for_business_date(staff.user_id, biz_date)
    return {
        "staff": {"id": staff.id, "name": staff.name, "role": staff.role},
        "date": biz_date.isoformat(),
        "is_current_trading_day": is_current_trading_day,
        "participated_in_trading_day": participated,
        "completed": done is not None,
        "completed_at": completion["completed_at"] if completion else None,
        "completion": completion,
        "summary": summary,
        "tables": tables,
        "open_tables": open_tables,
        "has_open_tables": has_open_tables,
        "is_logged_in": logged_in,
        "can_cash_up": is_current_trading_day and not has_open_tables and not logged_in and done is None,
    }


def build_sales_by_category(start: datetime, end: datetime) -> list[dict]:
    rows = (
        db.session.query(SaleItem, Product, Category)
        .join(Sale, SaleItem.sale_id == Sale.id)
        .join(Product, SaleItem.product_id == Product.id)
        .outerjoin(Category, Product.category_id == Category.id)
        .filter(Sale.created_at >= start, Sale.created_at < end)
        .filter(Sale.status == "active")
        .all()
    )

    by_category: dict[int | None, dict] = {}

    for sale_item, product, category in rows:
        cat_id = category.id if category else None
        cat_name = category.name if category else "Uncategorized"
        if cat_id not in by_category:
            by_category[cat_id] = {
                "category_id": cat_id,
                "category_name": cat_name,
                "category_total": Decimal("0"),
                "items": {},
            }
        bucket = by_category[cat_id]
        line_total = (sale_item.price_at_time or Decimal("0")) * (sale_item.quantity or 0)
        bucket["category_total"] += line_total
        pid = product.id
        if pid not in bucket["items"]:
            bucket["items"][pid] = {
                "product_id": pid,
                "name": product.name,
                "quantity": 0,
                "line_total": Decimal("0"),
            }
        bucket["items"][pid]["quantity"] += sale_item.quantity or 0
        bucket["items"][pid]["line_total"] += line_total

    out: list[dict] = []
    for cat in sorted(by_category.values(), key=lambda c: c["category_name"].lower()):
        items = sorted(cat["items"].values(), key=lambda i: i["name"].lower())
        out.append(
            {
                "category_id": cat["category_id"],
                "category_name": cat["category_name"],
                "category_total": money_to_str(cat["category_total"]),
                "items": [
                    {
                        "product_id": it["product_id"],
                        "name": it["name"],
                        "quantity": it["quantity"],
                        "line_total": money_to_str(it["line_total"]),
                    }
                    for it in items
                ],
            }
        )
    return out


def existing_master_cashup(
    user_id: int,
    trading_date: date,
    start: datetime | None = None,
    end: datetime | None = None,
) -> CashUp | None:
    """At most one master cash up per user and trading day (enforced in DB)."""
    row = CashUp.query.filter_by(user_id=user_id, trading_date=trading_date).first()
    if row:
        return row
    if start is None or end is None:
        if trading_date == get_trading_date(user_id):
            start, end = trading_window(user_id)
        else:
            start, end = trading_window_for_date(trading_date, user_id)
    return (
        CashUp.query.filter(
            CashUp.user_id == user_id,
            CashUp.created_at >= start,
            CashUp.created_at < end,
        )
        .order_by(CashUp.id.desc())
        .first()
    )


def _master_cashup_json(c: CashUp) -> dict:
    return {
        "id": c.id,
        "total_sales": money_to_str(c.total_sales),
        "cash_total": money_to_str(c.cash_total),
        "card_total": money_to_str(c.card_total),
        "expected_cash": money_to_str(c.expected_cash),
        "actual_cash": money_to_str(c.actual_cash),
        "difference": money_to_str(c.difference),
        "created_at": c.created_at.isoformat(),
    }


def build_master_cashup(
    user_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
    trading_date: date | None = None,
) -> dict:
    current_trading_date = get_trading_date(user_id)
    selected_trading_date = trading_date or current_trading_date
    if start is None or end is None:
        if selected_trading_date == current_trading_date:
            start, end = trading_window(user_id)
        else:
            start, end = trading_window_for_date(selected_trading_date, user_id)

    orders = _closed_orders_for_day(start, end)
    summary, _ = _aggregate_closed_orders(orders)
    status = staff_completion_status(user_id, start, end, for_master=True)
    categories = build_sales_by_category(start, end)
    existing = existing_master_cashup(user_id, selected_trading_date, start, end)
    is_current_trading_day = selected_trading_date == current_trading_date
    open_tables = open_tables_for_venue() if is_current_trading_day else []
    has_open_tables = len(open_tables) > 0

    from app.services.trading_day_service import day_end_status

    if is_current_trading_day:
        day_status = day_end_status(user_id)
    else:
        closed = existing is not None
        day_status = {
            "trading_date": selected_trading_date.isoformat(),
            "day_closed": closed,
            "day_closed_at": None,
            "can_close_day": False,
            "blockers": ["Viewing a filtered date. Switch back to the current trading day to submit day end."],
            "staff_all_complete": status["all_complete"],
            "master_submitted": existing is not None,
            "open_table_count": 0,
            "empty_open_orders_closed": 0,
        }

    return {
        "date": business_date_for(start).isoformat(),
        "trading_date": selected_trading_date.isoformat(),
        "current_trading_date": current_trading_date.isoformat(),
        "is_current_trading_day": is_current_trading_day,
        "summary": summary,
        "categories": categories,
        "staff_status": status,
        "open_tables": open_tables,
        "has_open_tables": has_open_tables,
        "master_already_submitted": existing is not None,
        "master_cashup": _master_cashup_json(existing) if existing else None,
        "can_submit_master": (
            is_current_trading_day
            and status["all_complete"]
            and existing is None
            and not day_status["day_closed"]
            and not has_open_tables
        ),
        "day_end": day_status,
    }
