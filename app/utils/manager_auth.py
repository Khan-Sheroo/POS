from __future__ import annotations

from app.models import Staff


def verify_manager_pin(user_id: int, pin: str) -> bool:
    pin = str(pin or "").strip()
    if not (pin.isdigit() and len(pin) in {4, 5}):
        return False
    managers = Staff.query.filter_by(user_id=user_id, role="manager", active=True).all()
    return any(m.check_pin(pin) for m in managers)


def staff_requires_manager_pin(staff) -> bool:
    role = getattr(staff, "role", "") or ""
    return role not in {"manager"}


def closed_tables_access_allowed(user, staff, manager_pin: str | None = None) -> bool:
    """Managers, company owners (no staff session), or valid manager PIN."""
    if staff is None:
        return True
    if getattr(staff, "role", "") == "manager":
        return True
    return verify_manager_pin(user.id, manager_pin or "")
