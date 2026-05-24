from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from flask import Blueprint, jsonify, request
from sqlalchemy import case, func

from app.models import Sale
from app.services.trading_day_service import get_trading_date, trading_window
from app.utils.auth import login_required
from app.utils.serialization import money_to_str


reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


@reports_bp.get("/today")
@login_required
def today_report():
    """Daily summary for the active trading day based on Sale.created_at."""
    user = request.current_user  # type: ignore[attr-defined]
    start, end = trading_window(user.id)

    cash_sum = func.coalesce(
        func.sum(case((Sale.payment_method == "cash", Sale.total_amount), else_=0)),
        0,
    )
    card_sum = func.coalesce(
        func.sum(case((Sale.payment_method == "card", Sale.total_amount), else_=0)),
        0,
    )
    total_sum = func.coalesce(func.sum(Sale.total_amount), 0)
    txn_count = func.count(Sale.id)

    total_sales, transaction_count, cash_total, card_total = (
        Sale.query.with_entities(total_sum, txn_count, cash_sum, card_sum)
        .filter(Sale.created_at >= start, Sale.created_at < end)
        .filter(Sale.status == "active")
        .first()
        or (Decimal("0"), 0, Decimal("0"), Decimal("0"))
    )

    return jsonify(
        {
            "trading_date": get_trading_date(user.id).isoformat(),
            "total_sales": money_to_str(total_sales),
            "transaction_count": int(transaction_count or 0),
            "cash_total": money_to_str(cash_total),
            "card_total": money_to_str(card_total),
        }
    )

