from __future__ import annotations

from datetime import datetime

from app.extensions import db


class AccountEntry(db.Model):
    __tablename__ = "account_entries"

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("customer_accounts.id"), nullable=False, index=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=True, index=True)
    entry_type = db.Column(db.String(16), nullable=False)  # charge | payment
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    note = db.Column(db.String(255), nullable=True)
    created_by_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    account = db.relationship("CustomerAccount", back_populates="entries")
    sale = db.relationship("Sale")
