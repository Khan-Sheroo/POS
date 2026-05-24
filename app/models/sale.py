from __future__ import annotations

from datetime import datetime

from app.extensions import db


class Sale(db.Model):
    __tablename__ = "sales"

    id = db.Column(db.Integer, primary_key=True)
    total_amount = db.Column(db.Numeric(10, 2), nullable=False)
    payment_method = db.Column(db.String(16), nullable=False)
    customer_account_id = db.Column(db.Integer, db.ForeignKey("customer_accounts.id"), nullable=True, index=True)
    status = db.Column(db.String(16), nullable=False, default="active", index=True)
    voided_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    items = db.relationship("SaleItem", back_populates="sale", cascade="all, delete-orphan")

