from __future__ import annotations

from datetime import datetime

from app.extensions import db
from app.models.base import TenantModel


class TableOrder(TenantModel):
    __tablename__ = "table_orders"

    id = db.Column(db.Integer, primary_key=True)
    table_id = db.Column(db.Integer, db.ForeignKey("tables.id"), nullable=False, index=True)
    status = db.Column(db.String(16), nullable=False, default="open")
    closed_sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=True, index=True)
    cash_received = db.Column(db.Numeric(10, 2), nullable=True)
    card_amount = db.Column(db.Numeric(10, 2), nullable=True)
    tip_mode = db.Column(db.String(16), nullable=True)  # "percent" | "amount" | null
    tip_percent = db.Column(db.Numeric(6, 2), nullable=True)
    tip_amount = db.Column(db.Numeric(10, 2), nullable=True)
    opened_by_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=True, index=True)
    committed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = db.relationship("TableOrderItem", back_populates="order", cascade="all, delete-orphan")
    opened_by_staff = db.relationship("Staff")

