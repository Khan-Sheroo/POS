from __future__ import annotations

from datetime import datetime

from app.extensions import db


class CustomerAccount(db.Model):
    __tablename__ = "customer_accounts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(64), nullable=True)
    email = db.Column(db.String(200), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User")
    entries = db.relationship("AccountEntry", back_populates="account", cascade="all, delete-orphan")
