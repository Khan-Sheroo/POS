from __future__ import annotations

from datetime import datetime

from app.extensions import db


class TemperatureGroup(db.Model):
    __tablename__ = "temperature_groups"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    options = db.relationship(
        "TemperatureOption",
        back_populates="group",
        cascade="all, delete-orphan",
        order_by="TemperatureOption.sort_order",
    )
    products = db.relationship("Product", back_populates="temperature_group")
