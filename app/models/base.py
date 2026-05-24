from __future__ import annotations

from app.extensions import db


class TenantModel(db.Model):
    """POS data stored in the per-email tenant database."""

    __abstract__ = True
    __bind_key__ = "tenant"
