from __future__ import annotations

import re
from pathlib import Path

from flask import current_app, g
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.extensions import db

_ENGINE_CACHE: dict[int, Engine] = {}


def tenants_dir() -> Path:
    root = Path(current_app.instance_path) / "tenants"
    root.mkdir(parents=True, exist_ok=True)
    return root


def tenant_db_path(tenant_id: int) -> Path:
    return tenants_dir() / f"tenant_{tenant_id}.db"


def tenant_database_uri(tenant_id: int) -> str:
    path = tenant_db_path(tenant_id).resolve()
    return f"sqlite:///{path.as_posix()}"


def get_tenant_engine(tenant_id: int) -> Engine:
    engine = _ENGINE_CACHE.get(tenant_id)
    if engine is None:
        engine = create_engine(
            tenant_database_uri(tenant_id),
            connect_args={"check_same_thread": False},
        )
        _ENGINE_CACHE[tenant_id] = engine
    return engine


def provision_tenant_database(tenant_id: int) -> None:
    """Create tenant SQLite file and all POS tables."""
    import app.models  # noqa: F401

    path = tenant_db_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    uri = tenant_database_uri(tenant_id)
    binds = dict(current_app.config.get("SQLALCHEMY_BINDS") or {})
    binds["tenant"] = uri
    current_app.config["SQLALCHEMY_BINDS"] = binds
    engine = get_tenant_engine(tenant_id)
    db.engines["tenant"] = engine
    db.create_all(bind_key="tenant")


def tenant_database_exists(tenant_id: int) -> bool:
    return tenant_db_path(tenant_id).is_file()


def use_tenant(tenant_id: int) -> None:
    """Point the 'tenant' SQLAlchemy bind at this company's database for the current request."""
    if not tenant_database_exists(tenant_id):
        provision_tenant_database(tenant_id)
    g.tenant_id = int(tenant_id)
    uri = tenant_database_uri(tenant_id)
    binds = dict(current_app.config.get("SQLALCHEMY_BINDS") or {})
    binds["tenant"] = uri
    current_app.config["SQLALCHEMY_BINDS"] = binds
    engine = get_tenant_engine(tenant_id)
    db.engines["tenant"] = engine


def clear_tenant_binding() -> None:
    db.session.remove()
    g.pop("tenant_id", None)


def ensure_owner_user(*, email: str, password: str):
    """Ensure the tenant DB has a company User row (for staff/settings FKs)."""
    from app.models import User

    user = User.query.filter_by(email=email).first()
    if user:
        return user
    user = User(email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    return user


def slug_email(email: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (email or "").strip().lower()).strip("-") or "tenant"
