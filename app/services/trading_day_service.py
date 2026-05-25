from __future__ import annotations

from datetime import date, datetime, timedelta

from app.extensions import db
from app.models import CashUp, DayEnd, Setting, StaffLoginSession, TableOrder


def get_setting(user_id: int) -> Setting:
    s = Setting.query.filter_by(user_id=user_id).first()
    if not s:
        s = Setting(user_id=user_id, currency="ZAR")
        db.session.add(s)
        db.session.commit()
    return s


def get_trading_date(user_id: int) -> date:
    s = get_setting(user_id)
    if s.trading_date is not None:
        return s.trading_date
    return datetime.utcnow().date()


def trading_window_for_date(target_date: date, user_id: int | None = None) -> tuple[datetime, datetime]:
    start = datetime.combine(target_date, datetime.min.time())
    closed = day_end_record(user_id, target_date) if user_id is not None else None
    is_current_trading_day = user_id is not None and target_date == get_trading_date(user_id)
    if closed and closed.closed_at:
        end = closed.closed_at + timedelta(microseconds=1)
    elif is_current_trading_day:
        # An active trading day stays open until day end, even across calendar days.
        live_end = datetime.utcnow() + timedelta(seconds=1)
        end = live_end if live_end > start else start + timedelta(seconds=1)
    else:
        end = start + timedelta(days=1)
    return start, end


def trading_window(user_id: int) -> tuple[datetime, datetime]:
    d = get_trading_date(user_id)
    start, end = trading_window_for_date(d, user_id)
    return start, end


def day_end_record(user_id: int, trading_date: date) -> DayEnd | None:
    return DayEnd.query.filter_by(user_id=user_id, trading_date=trading_date).first()


def close_empty_open_table_orders() -> int:
    """Close open orders with no line items so empty tables do not block day end."""
    closed = 0
    orders = TableOrder.query.filter_by(status="open").all()
    for order in orders:
        qty = sum((it.quantity or 0) for it in (order.items or []))
        if qty > 0:
            continue
        order.status = "closed"
        closed += 1
    if closed:
        db.session.commit()
    return closed


def any_open_tables() -> list[dict]:
    orders = TableOrder.query.filter_by(status="open").all()
    out: list[dict] = []
    for order in orders:
        from app.models import Table

        table = Table.query.get(order.table_id)
        out.append(
            {
                "order_id": order.id,
                "table_id": order.table_id,
                "table_number": table.table_number if table else None,
            }
        )
    out.sort(key=lambda row: (row["table_number"] is None, row["table_number"] or 0))
    return out


def day_end_status(user_id: int) -> dict:
    from app.services.staff_cashup_service import existing_master_cashup, staff_completion_status

    trading_date = get_trading_date(user_id)
    start, end = trading_window(user_id)
    closed = day_end_record(user_id, trading_date)
    staff_status = staff_completion_status(user_id, start, end, for_master=True)
    master = existing_master_cashup(user_id, trading_date)
    empty_closed = close_empty_open_table_orders()
    open_tables = any_open_tables()

    blockers: list[str] = []
    if closed:
        blockers.append("This trading day has already been closed.")
    if not staff_status["all_complete"]:
        pending = staff_status.get("pending_staff") or []
        if pending:
            blockers.append(f"Staff cash ups pending: {', '.join(pending)}.")
        else:
            blockers.append("All staff must complete their cash ups.")
    if not master:
        blockers.append("Submit the master cash up before closing the day.")
    if open_tables:
        nums = [str(t["table_number"]) for t in open_tables if t.get("table_number") is not None]
        if nums:
            blockers.append(f"Open tables remain: {', '.join(f'#{n}' for n in nums)}.")
        else:
            blockers.append("Open tables remain.")

    return {
        "trading_date": trading_date.isoformat(),
        "day_closed": closed is not None,
        "day_closed_at": closed.closed_at.isoformat() if closed else None,
        "can_close_day": len(blockers) == 0,
        "blockers": blockers,
        "staff_all_complete": staff_status["all_complete"],
        "master_submitted": master is not None,
        "open_table_count": len(open_tables),
        "empty_open_orders_closed": empty_closed,
    }


def close_trading_day(user_id: int) -> dict:
    from app.services.staff_cashup_service import existing_master_cashup

    status = day_end_status(user_id)
    if not status["can_close_day"]:
        return {"ok": False, "error": "day_end_blocked", "status": status}

    trading_date = get_trading_date(user_id)
    master = existing_master_cashup(user_id, trading_date)
    if not master:
        status = day_end_status(user_id)
        return {"ok": False, "error": "master_cashup_required", "status": status}

    row = DayEnd(
        user_id=user_id,
        trading_date=trading_date,
        master_cashup_id=master.id,
    )
    db.session.add(row)

    s = get_setting(user_id)
    next_trading_date = trading_date + timedelta(days=1)
    today = datetime.utcnow().date()
    if next_trading_date < today:
        next_trading_date = today
    s.trading_date = next_trading_date
    cleared_logins = StaffLoginSession.query.filter_by(user_id=user_id).delete()
    db.session.commit()

    return {
        "ok": True,
        "closed_trading_date": trading_date.isoformat(),
        "new_trading_date": s.trading_date.isoformat(),
        "closed_at": row.closed_at.isoformat(),
        "staff_logins_cleared": cleared_logins,
    }
