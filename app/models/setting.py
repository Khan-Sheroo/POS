from __future__ import annotations

from datetime import date, datetime

from app.extensions import db


class Setting(db.Model):
    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True, index=True)
    currency = db.Column(db.String(8), nullable=False, default="ZAR")  # ZAR | USD | GBP
    trading_date = db.Column(db.Date, nullable=True, index=True)
    active_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=True, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User")

