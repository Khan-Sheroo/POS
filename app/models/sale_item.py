from __future__ import annotations

from app.extensions import db
from app.models.base import TenantModel


class SaleItem(TenantModel):
    __tablename__ = "sale_items"

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False)
    price_at_time = db.Column(db.Numeric(10, 2), nullable=False)
    temperature = db.Column(db.String(64), nullable=True)

    sale = db.relationship("Sale", back_populates="items")
    product = db.relationship("Product")

