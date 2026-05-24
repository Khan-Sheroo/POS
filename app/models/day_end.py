from __future__ import annotations

from datetime import date, datetime

from app.extensions import db
from app.models.base import TenantModel


class DayEnd(TenantModel):
    __tablename__ = "day_ends"
    __table_args__ = (db.UniqueConstraint("user_id", "trading_date", name="uq_day_end_trading_date"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    trading_date = db.Column(db.Date, nullable=False, index=True)
    master_cashup_id = db.Column(db.Integer, db.ForeignKey("cash_ups.id"), nullable=True, index=True)
    closed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
