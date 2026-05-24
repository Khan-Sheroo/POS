from __future__ import annotations

from app.extensions import db
from app.models.base import TenantModel


class TemperatureOption(TenantModel):
    __tablename__ = "temperature_options"

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey("temperature_groups.id"), nullable=False, index=True)
    label = db.Column(db.String(64), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    group = db.relationship("TemperatureGroup", back_populates="options")
