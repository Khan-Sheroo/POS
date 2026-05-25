from __future__ import annotations

from flask import Blueprint, g, jsonify, request, current_app

from app.models import Staff
from app.services.staff_session_service import (
    add_staff_login,
    get_logged_in_staff,
    get_logged_in_staff_ids,
    is_staff_logged_in,
    remove_staff_login,
)
from app.utils.auth import login_required, role_required
from app.utils.jwt import create_staff_token
from app.utils.manager_auth import verify_manager_pin


staff_bp = Blueprint("staff", __name__, url_prefix="/staff")


def _staff_json(s: Staff) -> dict:
    return {"id": s.id, "name": s.name, "role": s.role, "active": s.active, "created_at": s.created_at.isoformat()}


def _issue_staff_token(staff: Staff, user_id: int) -> dict:
    tenant_id = getattr(request, "tenant_id", None) or getattr(g, "tenant_id", None)
    if tenant_id is None:
        raise RuntimeError("tenant_id missing for staff token")
    token = create_staff_token(
        staff_id=staff.id,
        company_user_id=user_id,
        tenant_id=int(tenant_id),
        role=staff.role,
        secret=current_app.config["JWT_SECRET_KEY"],
        algorithm=current_app.config["JWT_ALGORITHM"],
        expires_seconds=current_app.config["JWT_EXPIRES_SECONDS"],
    )
    return {"staff_token": token, "token_type": "Bearer", "staff": _staff_json(staff)}


@staff_bp.get("")
@login_required
def list_staff():
    user = request.current_user  # type: ignore[attr-defined]
    staff = Staff.query.filter_by(user_id=user.id, active=True).order_by(Staff.name.asc()).all()
    return jsonify([_staff_json(s) for s in staff])

@staff_bp.post("/bootstrap-manager")
@login_required
def bootstrap_manager():
    """
    Create the first manager staff account for this company user.
    Allowed only when no staff exist yet for the company.
    """
    user = request.current_user  # type: ignore[attr-defined]
    existing_any = Staff.query.filter_by(user_id=user.id).first()
    if existing_any:
        return jsonify({"error": "staff_already_configured"}), 400

    data = request.get_json(silent=True) or {}
    pin = str(data.get("pin") or "").strip()
    name = (data.get("name") or "Manager").strip() or "Manager"

    if not pin.isdigit() or len(pin) not in {4, 5}:
        return jsonify({"error": "pin_invalid"}), 400

    staff = Staff(user_id=user.id, name=name, role="manager", active=True, pin_hash="x")
    staff.set_pin(pin)
    from app.extensions import db

    db.session.add(staff)
    db.session.commit()

    return jsonify(_staff_json(staff)), 201


@staff_bp.post("")
@login_required
@role_required("manager")
def create_staff():
    user = request.current_user  # type: ignore[attr-defined]
    data = request.get_json(silent=True) or {}

    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()
    position = (data.get("position") or "").strip().lower()
    pin = str(data.get("pin") or "").strip()

    if not first_name or not last_name:
        return jsonify({"error": "name_required"}), 400
    if position not in {"manager", "waiter", "bartender", "clerk"}:
        return jsonify({"error": "position_invalid"}), 400
    if not pin.isdigit() or len(pin) not in {4, 5}:
        return jsonify({"error": "pin_invalid"}), 400

    staff = Staff(user_id=user.id, name=f"{first_name} {last_name}", role=position, active=True, pin_hash="x")
    staff.set_pin(pin)

    from app.extensions import db

    db.session.add(staff)
    db.session.commit()

    return jsonify(_staff_json(staff)), 201


@staff_bp.put("/<int:staff_id>")
@login_required
@role_required("manager")
def update_staff(staff_id: int):
    user = request.current_user  # type: ignore[attr-defined]
    staff = Staff.query.get(staff_id)
    if not staff or not staff.active or staff.user_id != user.id:
        return jsonify({"error": "invalid_staff"}), 404

    data = request.get_json(silent=True) or {}
    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()
    position = (data.get("position") or "").strip().lower()
    pin = str(data.get("pin") or "").strip()

    if not first_name or not last_name:
        return jsonify({"error": "name_required"}), 400
    if position not in {"manager", "waiter", "bartender", "clerk"}:
        return jsonify({"error": "position_invalid"}), 400
    if pin and (not pin.isdigit() or len(pin) not in {4, 5}):
        return jsonify({"error": "pin_invalid"}), 400

    staff.name = f"{first_name} {last_name}"
    staff.role = position
    if pin:
        staff.set_pin(pin)

    from app.extensions import db

    db.session.commit()
    return jsonify(_staff_json(staff))


@staff_bp.post("/login")
@login_required
def staff_login():
    user = request.current_user  # type: ignore[attr-defined]
    data = request.get_json(silent=True) or {}

    staff_id = data.get("staff_id")
    pin = str(data.get("pin") or "").strip()

    if not isinstance(staff_id, int) or staff_id <= 0:
        return jsonify({"error": "staff_id_invalid"}), 400
    if not pin.isdigit() or len(pin) not in {4, 5}:
        return jsonify({"error": "pin_invalid"}), 400

    staff = Staff.query.get(staff_id)
    if not staff or not staff.active or staff.user_id != user.id:
        return jsonify({"error": "invalid_staff"}), 401
    if not staff.check_pin(pin):
        return jsonify({"error": "invalid_pin"}), 401

    add_staff_login(user.id, staff)
    return jsonify(_issue_staff_token(staff, user.id))


@staff_bp.get("/session")
@login_required
def staff_session():
    """All staff currently logged in on this terminal."""
    user = request.current_user  # type: ignore[attr-defined]
    logged_in = get_logged_in_staff(user.id)
    return jsonify(
        {
            "logged_in": len(logged_in) > 0,
            "logged_in_staff": [_staff_json(s) for s in logged_in],
            "logged_in_count": len(logged_in),
        }
    )


@staff_bp.post("/enter")
@login_required
def staff_enter():
    """Continue as a staff member already logged in on this terminal (no PIN)."""
    user = request.current_user  # type: ignore[attr-defined]
    data = request.get_json(silent=True) or {}
    staff_id = data.get("staff_id")

    logged_in = get_logged_in_staff(user.id)
    if not logged_in:
        return (
            jsonify(
                {
                    "error": "no_staff_logged_in",
                    "message": "Log in with your PIN first.",
                }
            ),
            403,
        )

    if not isinstance(staff_id, int) or staff_id <= 0:
        if len(logged_in) == 1:
            staff_id = logged_in[0].id
        else:
            return jsonify({"error": "staff_id_required", "message": "Select who is entering."}), 400

    staff = Staff.query.get(staff_id)
    if not staff or not is_staff_logged_in(user.id, staff.id):
        return jsonify({"error": "staff_not_logged_in", "message": "That staff member is not logged in."}), 403

    return jsonify(_issue_staff_token(staff, user.id))


@staff_bp.post("/pin-login")
@login_required
def staff_pin_login():
    """
    PIN-only login: find a staff member (for this company user) matching the provided PIN.
    """
    user = request.current_user  # type: ignore[attr-defined]
    data = request.get_json(silent=True) or {}
    pin = str(data.get("pin") or "").strip()

    if not pin.isdigit() or len(pin) not in {4, 5}:
        return jsonify({"error": "pin_invalid"}), 400

    staff_list = Staff.query.filter_by(user_id=user.id, active=True).all()
    matches = [s for s in staff_list if s.check_pin(pin)]
    if len(matches) == 0:
        return jsonify({"error": "invalid_pin"}), 401
    if len(matches) > 1:
        return jsonify({"error": "pin_not_unique"}), 400

    staff = matches[0]
    add_staff_login(user.id, staff)
    return jsonify(_issue_staff_token(staff, user.id))


@staff_bp.post("/pin-logout")
@login_required
def staff_pin_logout():
    """Verify PIN and log out that staff member (others may stay logged in)."""
    user = request.current_user  # type: ignore[attr-defined]
    data = request.get_json(silent=True) or {}
    pin = str(data.get("pin") or "").strip()

    if not pin.isdigit() or len(pin) not in {4, 5}:
        return jsonify({"error": "pin_invalid"}), 400

    staff_list = Staff.query.filter_by(user_id=user.id, active=True).all()
    matches = [s for s in staff_list if s.check_pin(pin)]
    if len(matches) == 0:
        return jsonify({"error": "invalid_pin"}), 401
    if len(matches) > 1:
        return jsonify({"error": "pin_not_unique"}), 400

    staff = matches[0]
    if not is_staff_logged_in(user.id, staff.id):
        return (
            jsonify(
                {
                    "error": "staff_not_logged_in",
                    "message": f"{staff.name} is not logged in.",
                }
            ),
            400,
        )

    remove_staff_login(user.id, staff.id)
    return jsonify({"ok": True, "staff": _staff_json(staff)})


@staff_bp.get("/me")
@login_required
def staff_me():
    staff = getattr(request, "current_staff", None)
    if not staff:
        return jsonify({"error": "staff_login_required"}), 403
    return jsonify(_staff_json(staff))


@staff_bp.post("/manager-roster")
@login_required
def manager_roster():
    """Manager PIN required. Returns all active staff and who is logged in on this terminal."""
    user = request.current_user  # type: ignore[attr-defined]
    data = request.get_json(silent=True) or {}
    manager_pin = str(data.get("manager_pin") or "").strip()
    if not verify_manager_pin(user.id, manager_pin):
        return jsonify({"error": "invalid_pin", "message": "Access denied."}), 401

    logged_in_ids = get_logged_in_staff_ids(user.id)
    staff_list = Staff.query.filter_by(user_id=user.id, active=True).order_by(Staff.name.asc()).all()
    return jsonify(
        {
            "staff": [
                {
                    **_staff_json(s),
                    "logged_in": s.id in logged_in_ids,
                }
                for s in staff_list
            ],
            "logged_in_count": len(logged_in_ids),
        }
    )


@staff_bp.post("/manager-login-as")
@login_required
def manager_login_as():
    """Manager PIN required. Log in the selected staff member without their PIN."""
    user = request.current_user  # type: ignore[attr-defined]
    data = request.get_json(silent=True) or {}
    manager_pin = str(data.get("manager_pin") or "").strip()
    staff_id = data.get("staff_id")

    if not verify_manager_pin(user.id, manager_pin):
        return jsonify({"error": "invalid_pin", "message": "Access denied."}), 401
    if not isinstance(staff_id, int) or staff_id <= 0:
        return jsonify({"error": "staff_id_invalid"}), 400

    staff = Staff.query.get(staff_id)
    if not staff or not staff.active or staff.user_id != user.id:
        return jsonify({"error": "invalid_staff"}), 404

    add_staff_login(user.id, staff)
    return jsonify(_issue_staff_token(staff, user.id))


@staff_bp.post("/manager-logout-as")
@login_required
def manager_logout_as():
    """Manager PIN required. Log out the selected staff member."""
    user = request.current_user  # type: ignore[attr-defined]
    data = request.get_json(silent=True) or {}
    manager_pin = str(data.get("manager_pin") or "").strip()
    staff_id = data.get("staff_id")

    if not verify_manager_pin(user.id, manager_pin):
        return jsonify({"error": "invalid_pin", "message": "Access denied."}), 401
    if not isinstance(staff_id, int) or staff_id <= 0:
        return jsonify({"error": "staff_id_invalid"}), 400

    staff = Staff.query.get(staff_id)
    if not staff or not staff.active or staff.user_id != user.id:
        return jsonify({"error": "invalid_staff"}), 404
    if not is_staff_logged_in(user.id, staff.id):
        return jsonify({"error": "staff_not_logged_in", "message": f"{staff.name} is not logged in."}), 400

    remove_staff_login(user.id, staff.id)
    return jsonify({"ok": True, "staff": _staff_json(staff)})


@staff_bp.post("/authorize-manager")
@login_required
def authorize_manager():
    """
    Verify a manager PIN for the current company user.
    Used to authorize restricted UI actions (e.g. unlocking side nav).
    """
    user = request.current_user  # type: ignore[attr-defined]
    data = request.get_json(silent=True) or {}
    pin = str(data.get("pin") or "").strip()
    if not pin.isdigit() or len(pin) not in {4, 5}:
        return jsonify({"error": "pin_invalid"}), 400

    managers = Staff.query.filter_by(user_id=user.id, role="manager", active=True).all()
    ok = any(m.check_pin(pin) for m in managers)
    if not ok:
        return jsonify({"error": "invalid_pin"}), 401
    return jsonify({"ok": True})

