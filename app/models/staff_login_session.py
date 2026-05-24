from __future__ import annotations

from datetime import datetime

from app.extensions import db
from app.models.base import TenantModel


class StaffLoginSession(TenantModel):
    """Terminal staff logins — multiple staff may be logged in at once."""

    __tablename__ = "staff_login_sessions"
    __table_args__ = (db.UniqueConstraint("user_id", "staff_id", name="uq_staff_login_session"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False, index=True)
    logged_in_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    staff = db.relationship("Staff")
