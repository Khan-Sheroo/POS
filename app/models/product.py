from __future__ import annotations

from datetime import datetime

from app.extensions import db
from app.models.base import TenantModel


class Product(TenantModel):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    cost_price = db.Column(db.Numeric(10, 2), nullable=True)
    sku = db.Column(db.String(64), unique=True, nullable=True, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True, index=True)
    temperature_group_id = db.Column(
        db.Integer, db.ForeignKey("temperature_groups.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    category = db.relationship("Category")
    temperature_group = db.relationship("TemperatureGroup", back_populates="products")