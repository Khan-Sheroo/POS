import os
from pathlib import Path


class BaseConfig:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JSON_SORT_KEYS = False
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", SECRET_KEY)
    JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRES_SECONDS = int(os.getenv("JWT_EXPIRES_SECONDS", "3600"))
    # Legacy single-database file (used only by `flask migrate-legacy-tenant`).
    LEGACY_DATABASE_URI = os.getenv("LEGACY_DATABASE_URL", "")


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class ProductionConfig(BaseConfig):
    DEBUG = False


def apply_database_config(app) -> None:
    """Registry DB + per-tenant bind; tenant SQLite files live under instance/tenants/."""
    instance = Path(app.instance_path)
    instance.mkdir(parents=True, exist_ok=True)

    registry_path = instance / "registry.db"
    registry_uri = os.getenv("REGISTRY_DATABASE_URL") or f"sqlite:///{registry_path.resolve().as_posix()}"

    # Default bind (unused for queries; tenant/registry models use __bind_key__).
    placeholder = instance / "_placeholder.db"
    default_uri = os.getenv("DATABASE_URL") or f"sqlite:///{placeholder.resolve().as_posix()}"

    legacy = BaseConfig.LEGACY_DATABASE_URI
    if not legacy:
        legacy_candidate = instance / "pos_system.db"
        if legacy_candidate.is_file():
            legacy = f"sqlite:///{legacy_candidate.resolve().as_posix()}"

    app.config["SQLALCHEMY_DATABASE_URI"] = default_uri
    app.config["SQLALCHEMY_BINDS"] = {
        "registry": registry_uri,
        # Placeholder; replaced per request via use_tenant().
        "tenant": default_uri,
    }
    app.config["LEGACY_DATABASE_URI"] = legacy
