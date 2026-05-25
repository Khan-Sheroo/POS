from __future__ import annotations



from datetime import date, datetime, timedelta

from decimal import Decimal, InvalidOperation



from flask import Blueprint, jsonify, request

from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError



from app.extensions import db

from app.models import CashUp, Sale, Staff

from app.services.staff_cashup_service import (
    build_master_cashup,
    build_staff_cashup,
    existing_master_cashup,
    mark_staff_cashup_complete,
    open_tables_for_staff,
    open_tables_for_venue,
    staff_completion_status,
)
from app.services.staff_session_service import get_staff_ids_for_business_date, is_staff_logged_in
from app.services.trading_day_service import (
    close_trading_day,
    day_end_status,
    get_trading_date,
    trading_window,
    trading_window_for_date,
)

from app.utils.auth import login_required

from app.utils.serialization import money_to_str





cashups_bp = Blueprint("cashups", __name__, url_prefix="/cashups")





def _parse_decimal(value) -> Decimal | None:

    if value is None:

        return None

    try:

        return Decimal(str(value))

    except (InvalidOperation, ValueError):

        return None





def _today_window():

    start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    end = start + timedelta(days=1)

    return start, end





def _today_sales_totals():

    start, end = _today_window()



    cash_sum = func.coalesce(

        func.sum(case((Sale.payment_method == "cash", Sale.total_amount), else_=0)),

        0,

    )

    card_sum = func.coalesce(

        func.sum(case((Sale.payment_method == "card", Sale.total_amount), else_=0)),

        0,

    )

    total_sum = func.coalesce(func.sum(Sale.total_amount), 0)



    q = Sale.query.with_entities(total_sum, cash_sum, card_sum).filter(

        Sale.created_at >= start, Sale.created_at < end

    )

    if hasattr(Sale, "status"):

        q = q.filter(Sale.status == "active")  # type: ignore[attr-defined]



    total_sales, cash_total, card_total = q.first() or (Decimal("0"), Decimal("0"), Decimal("0"))



    return (

        total_sales or Decimal("0"),

        cash_total or Decimal("0"),

        card_total or Decimal("0"),

    )





def _cashup_json(c: CashUp) -> dict:

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


def _requested_business_date() -> date | None:
    raw = (request.args.get("date") or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _staff_for_cashup(staff_id: int, business_date: date | None = None) -> Staff | tuple[dict, int]:

    user = request.current_user  # type: ignore[attr-defined]

    current_staff = getattr(request, "current_staff", None)



    staff = Staff.query.get(staff_id)

    if not staff or not staff.active or staff.user_id != user.id:

        return {"error": "staff_not_found"}, 404

    target_date = business_date or get_trading_date(user.id)
    if staff.id not in get_staff_ids_for_business_date(user.id, target_date):
        return {"error": "staff_not_in_trading_day"}, 404



    if current_staff and getattr(current_staff, "role", "") != "manager" and current_staff.id != staff.id:

        return {"error": "forbidden"}, 403



    return staff





@cashups_bp.get("/master")

@login_required

def get_master_cashup():

    user = request.current_user  # type: ignore[attr-defined]
    selected_date = _requested_business_date()
    if selected_date:
        start, end = trading_window_for_date(selected_date, user.id)
        return jsonify(build_master_cashup(user.id, start, end, selected_date))
    return jsonify(build_master_cashup(user.id))





@cashups_bp.get("/staff")

@login_required

def list_staff_cashups():

    """Summaries for each active staff member (today, UTC)."""

    user = request.current_user  # type: ignore[attr-defined]

    current_staff = getattr(request, "current_staff", None)

    selected_date = _requested_business_date() or get_trading_date(user.id)
    start, end = trading_window_for_date(selected_date, user.id)

    participating_ids = get_staff_ids_for_business_date(user.id, selected_date)
    if not participating_ids:
        return jsonify([])

    q = (
        Staff.query.filter(
            Staff.user_id == user.id,
            Staff.active.is_(True),
            Staff.id.in_(participating_ids),
        )
        .order_by(Staff.name.asc())
    )

    if current_staff and getattr(current_staff, "role", "") != "manager":

        q = q.filter(Staff.id == current_staff.id)



    out = []

    for s in q.all():

        report = build_staff_cashup(s, start, end)

        completion = report.get("completion") or {}
        out.append(
            {
                "staff": report["staff"],
                "date": report["date"],
                "summary": report["summary"],
                "table_count": len(report["tables"]),
                "completed": report["completed"],
                "completed_at": report.get("completed_at") or completion.get("completed_at"),
                "is_balanced": completion.get("is_balanced"),
                "has_open_tables": report.get("has_open_tables", False),
                "open_tables": report.get("open_tables", []),
                "can_cash_up": report.get("can_cash_up", False),
            }
        )

    return jsonify(out)





@cashups_bp.get("/staff/<int:staff_id>")

@login_required

def get_staff_cashup(staff_id: int):

    """Full staff cash up with per-table breakdown (printable)."""

    user = request.current_user  # type: ignore[attr-defined]
    selected_date = _requested_business_date()
    result = _staff_for_cashup(staff_id, selected_date)

    if not isinstance(result, Staff):

        return jsonify(result[0]), result[1]

    if selected_date:
        start, end = trading_window_for_date(selected_date, user.id)
        return jsonify(build_staff_cashup(result, start, end))
    return jsonify(build_staff_cashup(result))





@cashups_bp.post("/staff/<int:staff_id>/complete")

@login_required

def complete_staff_cashup(staff_id: int):

    user = request.current_user  # type: ignore[attr-defined]
    selected_date = _requested_business_date()
    current_trading_date = get_trading_date(user.id)
    if selected_date and selected_date != current_trading_date:
        return jsonify({"error": "historical_view_read_only"}), 400

    result = _staff_for_cashup(staff_id, current_trading_date)

    if not isinstance(result, Staff):

        return jsonify(result[0]), result[1]



    data = request.get_json(silent=True) or {}
    actual_cash = _parse_decimal(data.get("actual_cash"))
    actual_credit = _parse_decimal(data.get("actual_credit"))
    if actual_cash is None:
        return jsonify({"error": "actual_cash_required"}), 400
    if actual_credit is None:
        return jsonify({"error": "actual_credit_required"}), 400
    if actual_cash < 0 or actual_credit < 0:
        return jsonify({"error": "amounts_must_be_non_negative"}), 400

    open_tables = open_tables_for_staff(result.id)
    if open_tables:
        table_numbers = [t["table_number"] for t in open_tables if t.get("table_number") is not None]
        return (
            jsonify(
                {
                    "error": "open_tables_remain",
                    "message": "This staff member still has open tables. Close all tables before cashing up.",
                    "open_tables": open_tables,
                    "table_numbers": table_numbers,
                }
            ),
            400,
        )

    if is_staff_logged_in(user.id, result.id):
        return (
            jsonify(
                {
                    "error": "staff_still_logged_in",
                    "message": f"{result.name} must log out before cashing up.",
                }
            ),
            400,
        )

    try:
        row, balanced = mark_staff_cashup_complete(user.id, result, actual_cash, actual_credit)
    except ValueError as e:
        if str(e) == "open_tables_remain":
            open_tables = open_tables_for_staff(result.id)
            table_numbers = [t["table_number"] for t in open_tables if t.get("table_number") is not None]
            return (
                jsonify(
                    {
                        "error": "open_tables_remain",
                        "message": "This staff member still has open tables. Close all tables before cashing up.",
                        "open_tables": open_tables,
                        "table_numbers": table_numbers,
                    }
                ),
                400,
            )
        if str(e) == "staff_still_logged_in":
            return (
                jsonify(
                    {
                        "error": "staff_still_logged_in",
                        "message": f"{result.name} must log out before cashing up.",
                    }
                ),
                400,
            )
        raise

    start, end = trading_window(user.id)

    status = staff_completion_status(user.id, start, end)

    return (
        jsonify(
            {
                "ok": True,
                "staff_id": result.id,
                "completed_at": row.completed_at.isoformat(),
                "is_balanced": balanced,
                "all_staff_complete": status["all_complete"],
                "pending_staff": status["pending_staff"],
            }
        ),
        201,
    )





@cashups_bp.get("")

@login_required

def list_cashups():

    user = request.current_user  # type: ignore[attr-defined]
    selected_date = _requested_business_date()
    q = CashUp.query.filter_by(user_id=user.id)
    if selected_date:
        q = q.filter_by(trading_date=selected_date)
    rows = q.order_by(CashUp.id.desc()).limit(200).all()

    return jsonify([_cashup_json(r) for r in rows])





@cashups_bp.post("")

@login_required

def create_cashup():

    user = request.current_user  # type: ignore[attr-defined]

    start, end = trading_window(user.id)
    trading_date = get_trading_date(user.id)

    status = staff_completion_status(user.id, start, end, for_master=True)

    if not status["all_complete"]:

        return (

            jsonify(

                {

                    "error": "staff_cashups_incomplete",

                    "pending_staff": status["pending_staff"],

                }

            ),

            400,

        )

    open_tables = open_tables_for_venue()
    if open_tables:
        table_numbers = [t["table_number"] for t in open_tables if t.get("table_number") is not None]
        return (
            jsonify(
                {
                    "error": "open_tables_remain",
                    "message": "Close all open tables before submitting the master cash up.",
                    "open_tables": open_tables,
                    "table_numbers": table_numbers,
                }
            ),
            400,
        )

    data = request.get_json(silent=True) or {}

    actual_cash = _parse_decimal(data.get("actual_cash"))

    if actual_cash is None:

        return jsonify({"error": "actual_cash_required"}), 400

    if actual_cash < 0:

        return jsonify({"error": "actual_cash_must_be_non_negative"}), 400

    if existing_master_cashup(user.id, trading_date):
        return (
            jsonify(
                {
                    "error": "master_cashup_already_submitted",
                    "message": "A master cash up has already been submitted for this trading day.",
                }
            ),
            400,
        )

    day_status = day_end_status(user.id)
    if day_status["day_closed"]:
        return (
            jsonify(
                {
                    "error": "trading_day_closed",
                    "message": "This trading day is already closed.",
                }
            ),
            400,
        )

    master = build_master_cashup(user.id, start, end)

    summary = master["summary"]

    turnover = Decimal(summary["turnover"])

    cash_total = Decimal(summary["cash_payments"])

    card_total = Decimal(summary["credit_payments"])

    expected_cash = Decimal(summary["cash_due_to_restaurant"])

    difference = actual_cash - expected_cash



    row = CashUp(
        user_id=user.id,
        trading_date=trading_date,
        total_sales=turnover,
        cash_total=cash_total,
        card_total=card_total,
        expected_cash=expected_cash,
        actual_cash=actual_cash,
        difference=difference,
    )
    db.session.add(row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return (
            jsonify(
                {
                    "error": "master_cashup_already_submitted",
                    "message": "A master cash up has already been submitted for this trading day.",
                }
            ),
            400,
        )

    return jsonify(_cashup_json(row)), 201


@cashups_bp.get("/day-end")
@login_required
def get_day_end_status():
    user = request.current_user  # type: ignore[attr-defined]
    return jsonify(day_end_status(user.id))


@cashups_bp.post("/day-end")
@login_required
def post_day_end():
    user = request.current_user  # type: ignore[attr-defined]
    result = close_trading_day(user.id)
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result), 201


