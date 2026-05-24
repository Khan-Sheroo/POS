from __future__ import annotations

from datetime import date, datetime

from app.extensions import db


class CashUp(db.Model):
    __tablename__ = "cash_ups"
    __table_args__ = (
        db.UniqueConstraint("user_id", "trading_date", name="uq_cash_up_user_trading_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    trading_date = db.Column(db.Date, nullable=False, index=True)
    total_sales = db.Column(db.Numeric(10, 2), nullable=False)
    cash_total = db.Column(db.Numeric(10, 2), nullable=False)
    card_total = db.Column(db.Numeric(10, 2), nullable=False)
    expected_cash = db.Column(db.Numeric(10, 2), nullable=False)
    actual_cash = db.Column(db.Numeric(10, 2), nullable=False)
    difference = db.Column(db.Numeric(10, 2), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

