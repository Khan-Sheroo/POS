from __future__ import annotations

from datetime import date

from app.extensions import db
from app.models import Staff, StaffLoginSession, StaffTradingDayParticipation


def _prune_invalid_sessions(user_id: int) -> None:
    rows = StaffLoginSession.query.filter_by(user_id=user_id).all()
    changed = False
    for row in rows:
        staff = Staff.query.get(row.staff_id)
        if not staff or not staff.active or staff.user_id != user_id:
            db.session.delete(row)
            changed = True
    if changed:
        db.session.commit()


def get_logged_in_staff_ids(user_id: int) -> set[int]:
    _prune_invalid_sessions(user_id)
    rows = StaffLoginSession.query.filter_by(user_id=user_id).all()
    return {r.staff_id for r in rows}


def get_logged_in_staff(user_id: int) -> list[Staff]:
    ids = get_logged_in_staff_ids(user_id)
    if not ids:
        return []
    return (
        Staff.query.filter(Staff.id.in_(ids), Staff.active.is_(True))
        .order_by(Staff.name.asc())
        .all()
    )


def add_staff_login(user_id: int, staff: Staff) -> None:
    if staff.user_id != user_id or not staff.active:
        raise ValueError("invalid_staff")
    from app.services.trading_day_service import get_trading_date

    changed = False
    business_date = get_trading_date(user_id)
    participation = StaffTradingDayParticipation.query.filter_by(
        user_id=user_id,
        staff_id=staff.id,
        business_date=business_date,
    ).first()
    if not participation:
        db.session.add(
            StaffTradingDayParticipation(
                user_id=user_id,
                staff_id=staff.id,
                business_date=business_date,
            )
        )
        changed = True
    existing = StaffLoginSession.query.filter_by(user_id=user_id, staff_id=staff.id).first()
    if not existing:
        db.session.add(StaffLoginSession(user_id=user_id, staff_id=staff.id))
        changed = True
    if changed:
        db.session.commit()


def remove_staff_login(user_id: int, staff_id: int) -> Staff | None:
    staff = Staff.query.get(staff_id)
    row = StaffLoginSession.query.filter_by(user_id=user_id, staff_id=staff_id).first()
    if row:
        db.session.delete(row)
        db.session.commit()
    return staff if staff and staff.user_id == user_id else None


def is_staff_logged_in(user_id: int, staff_id: int) -> bool:
    return staff_id in get_logged_in_staff_ids(user_id)


def get_staff_ids_for_business_date(user_id: int, business_date: date) -> set[int]:
    rows = StaffTradingDayParticipation.query.filter_by(user_id=user_id, business_date=business_date).all()
    valid_ids: set[int] = set()
    changed = False
    for row in rows:
        staff = Staff.query.get(row.staff_id)
        if not staff or not staff.active or staff.user_id != user_id:
            db.session.delete(row)
            changed = True
            continue
        valid_ids.add(row.staff_id)
    if changed:
        db.session.commit()
    return valid_ids


def get_trading_day_staff_ids(user_id: int) -> set[int]:
    from app.services.trading_day_service import get_trading_date

    return get_staff_ids_for_business_date(user_id, get_trading_date(user_id))


def clear_all_staff_logins(user_id: int) -> None:
    StaffLoginSession.query.filter_by(user_id=user_id).delete()
    db.session.commit()
