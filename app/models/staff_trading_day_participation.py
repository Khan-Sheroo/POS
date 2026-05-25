from __future__ import annotations

from datetime import date, datetime

from app.extensions import db
from app.models.base import TenantModel


class StaffTradingDayParticipation(TenantModel):
    """Staff who logged into the current trading day at least once."""

    __tablename__ = "staff_trading_day_participations"
    __table_args__ = (db.UniqueConstraint("user_id", "staff_id", "business_date", name="uq_staff_trading_day"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False, index=True)
    business_date = db.Column(db.Date, nullable=False, index=True)
    first_logged_in_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    staff = db.relationship("Staff")
