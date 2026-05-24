from __future__ import annotations

from datetime import date, datetime

from app.extensions import db


class StaffCashupCompletion(db.Model):
    __tablename__ = "staff_cashup_completions"
    __table_args__ = (db.UniqueConstraint("user_id", "staff_id", "business_date", name="uq_staff_cashup_day"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False, index=True)
    business_date = db.Column(db.Date, nullable=False, index=True)
    expected_cash = db.Column(db.Numeric(10, 2), nullable=True)
    expected_credit = db.Column(db.Numeric(10, 2), nullable=True)
    actual_cash = db.Column(db.Numeric(10, 2), nullable=True)
    actual_credit = db.Column(db.Numeric(10, 2), nullable=True)
    is_balanced = db.Column(db.Boolean, nullable=False, default=False)
    completed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    staff = db.relationship("Staff")
