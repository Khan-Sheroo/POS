from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

import click
from flask import Flask, current_app
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Category, Product, Setting, TenantAccount
from app.tenant import ensure_owner_user, provision_tenant_database, tenant_db_path, use_tenant


def _apply_sqlite_upgrades() -> int:
    """Incremental SQLite schema upgrades for the currently bound tenant database."""
    import app.models  # noqa: F401

    engine = db.engines.get("tenant")
    if engine is None:
        return 0
    if engine.dialect.name != "sqlite":
        return 0

    def column_exists(table: str, column: str) -> bool:
        if not table_exists(table):
            return True
        with engine.connect() as conn:
            rows = conn.execute(db.text(f"PRAGMA table_info({table})")).mappings().all()
        return any(r.get("name") == column for r in rows)

    def table_exists(table: str) -> bool:
        with engine.connect() as conn:
            row = conn.execute(
                db.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
                {"t": table},
            ).first()
        return row is not None

    def index_exists(name: str) -> bool:
        with engine.connect() as conn:
            row = conn.execute(
                db.text("SELECT name FROM sqlite_master WHERE type='index' AND name=:n"),
                {"n": name},
            ).first()
        return row is not None

    def exec_sql(statement: str, params: dict | None = None):
        with engine.begin() as conn:
            return conn.execute(db.text(statement), params or {})

    upgraded = 0

    if table_exists("table_orders") and not column_exists("table_orders", "opened_by_staff_id"):
        exec_sql("ALTER TABLE table_orders ADD COLUMN opened_by_staff_id INTEGER")
        upgraded += 1
    if not column_exists("table_orders", "tip_mode"):
        exec_sql("ALTER TABLE table_orders ADD COLUMN tip_mode VARCHAR(16)")
        upgraded += 1
    if not column_exists("table_orders", "tip_percent"):
        exec_sql("ALTER TABLE table_orders ADD COLUMN tip_percent NUMERIC(6,2)")
        upgraded += 1
    if not column_exists("table_orders", "tip_amount"):
        exec_sql("ALTER TABLE table_orders ADD COLUMN tip_amount NUMERIC(10,2)")
        upgraded += 1
    if not column_exists("table_orders", "committed_at"):
        exec_sql("ALTER TABLE table_orders ADD COLUMN committed_at DATETIME")
        upgraded += 1

    if not column_exists("staff_cashup_completions", "expected_cash"):
        exec_sql("ALTER TABLE staff_cashup_completions ADD COLUMN expected_cash NUMERIC(10,2)")
        upgraded += 1
    if not column_exists("staff_cashup_completions", "expected_credit"):
        exec_sql("ALTER TABLE staff_cashup_completions ADD COLUMN expected_credit NUMERIC(10,2)")
        upgraded += 1
    if not column_exists("staff_cashup_completions", "actual_cash"):
        exec_sql("ALTER TABLE staff_cashup_completions ADD COLUMN actual_cash NUMERIC(10,2)")
        upgraded += 1
    if not column_exists("staff_cashup_completions", "actual_credit"):
        exec_sql("ALTER TABLE staff_cashup_completions ADD COLUMN actual_credit NUMERIC(10,2)")
        upgraded += 1
    if not column_exists("staff_cashup_completions", "is_balanced"):
        exec_sql("ALTER TABLE staff_cashup_completions ADD COLUMN is_balanced BOOLEAN NOT NULL DEFAULT 0")
        upgraded += 1

    if not column_exists("settings", "trading_date"):
        exec_sql("ALTER TABLE settings ADD COLUMN trading_date DATE")
        upgraded += 1
    if not column_exists("settings", "active_staff_id"):
        exec_sql("ALTER TABLE settings ADD COLUMN active_staff_id INTEGER")
        upgraded += 1

    if not column_exists("sales", "customer_account_id"):
        exec_sql("ALTER TABLE sales ADD COLUMN customer_account_id INTEGER")
        upgraded += 1

    if not column_exists("products", "temperature_group_id"):
        exec_sql("ALTER TABLE products ADD COLUMN temperature_group_id INTEGER")
        upgraded += 1
    if not column_exists("table_order_items", "temperature"):
        exec_sql("ALTER TABLE table_order_items ADD COLUMN temperature VARCHAR(64)")
        upgraded += 1
    if not column_exists("sale_items", "temperature"):
        exec_sql("ALTER TABLE sale_items ADD COLUMN temperature VARCHAR(64)")
        upgraded += 1

    if not table_exists("temperature_groups") or not table_exists("temperature_options"):
        db.create_all(bind_key="tenant")
        upgraded += 1

    if not table_exists("day_ends"):
        db.create_all(bind_key="tenant")
        upgraded += 1

    if not column_exists("cash_ups", "user_id"):
        exec_sql("ALTER TABLE cash_ups ADD COLUMN user_id INTEGER")
        upgraded += 1
    if not column_exists("cash_ups", "trading_date"):
        exec_sql("ALTER TABLE cash_ups ADD COLUMN trading_date DATE")
        upgraded += 1

    if not table_exists("staff_login_sessions"):
        db.create_all(bind_key="tenant")
        upgraded += 1
    elif table_exists("settings") and column_exists("settings", "active_staff_id"):
        with engine.connect() as conn:
            rows = conn.execute(
                db.text("SELECT user_id, active_staff_id FROM settings WHERE active_staff_id IS NOT NULL")
            ).mappings().all()
        for row in rows:
            with engine.connect() as conn:
                exists = conn.execute(
                    db.text(
                        "SELECT 1 FROM staff_login_sessions "
                        "WHERE user_id = :uid AND staff_id = :sid LIMIT 1"
                    ),
                    {"uid": row["user_id"], "sid": row["active_staff_id"]},
                ).first()
            if not exists:
                exec_sql(
                    "INSERT INTO staff_login_sessions (user_id, staff_id, logged_in_at) "
                    "VALUES (:uid, :sid, datetime('now'))",
                    {"uid": row["user_id"], "sid": row["active_staff_id"]},
                )
                upgraded += 1

    if table_exists("cash_ups"):
        with engine.connect() as conn:
            orphan = conn.execute(
                db.text("SELECT id, created_at FROM cash_ups WHERE user_id IS NULL OR trading_date IS NULL")
            ).mappings().all()
        if orphan:
            with engine.connect() as conn:
                default_user = conn.execute(db.text("SELECT id FROM users ORDER BY id LIMIT 1")).scalar()
            if default_user is None:
                click.echo("Warning: cash_ups rows need user_id but no users exist.")
            else:
                for row in orphan:
                    created = row.get("created_at")
                    trading = created.date() if hasattr(created, "date") else None
                    exec_sql(
                        "UPDATE cash_ups SET user_id = :uid, trading_date = :td WHERE id = :id",
                        {"uid": default_user, "td": trading, "id": row["id"]},
                    )
                upgraded += 1

        if not index_exists("uq_cash_up_user_trading_date"):
            exec_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_up_user_trading_date "
                "ON cash_ups (user_id, trading_date)"
            )
            upgraded += 1

    return upgraded


def _resolve_tenant_id(tenant_id: int | None) -> int:
    if tenant_id:
        account = TenantAccount.query.get(tenant_id)
        if not account:
            raise click.ClickException(f"Tenant id={tenant_id} not found in registry.")
        use_tenant(tenant_id)
        return tenant_id

    account = TenantAccount.query.order_by(TenantAccount.id.asc()).first()
    if not account:
        raise click.ClickException("No tenants registered yet. Use register or flask create-user.")
    use_tenant(account.id)
    return account.id


def register_cli(flask_app: Flask) -> None:
    @flask_app.cli.command("create-user")
    @click.option("--email", prompt=True, help="Company email (unique).")
    @click.option(
        "--password",
        prompt=True,
        hide_input=True,
        confirmation_prompt=True,
        help="Account password.",
    )
    def create_user(email: str, password: str):
        """Create a tenant account and empty tenant database."""
        import app.models  # noqa: F401

        db.create_all(bind_key="registry")
        e = (email or "").strip().lower()
        if not e or not password:
            raise click.ClickException("Email and password are required.")

        if TenantAccount.query.filter_by(email=e).first():
            raise click.ClickException(f"Account already exists: {e}")

        account = TenantAccount(email=e)
        account.set_password(password)
        db.session.add(account)
        db.session.commit()

        provision_tenant_database(account.id)
        use_tenant(account.id)
        owner = ensure_owner_user(email=e, password=password)
        if not Setting.query.filter_by(user_id=owner.id).first():
            db.session.add(Setting(user_id=owner.id, currency="ZAR"))
        db.session.commit()
        click.echo(f"Created tenant id={account.id} email={account.email} db={tenant_db_path(account.id)}")

    @flask_app.cli.command("init-db")
    def init_db():
        """Create the global account registry (tenant DBs are created on register)."""
        import app.models  # noqa: F401

        db.create_all(bind_key="registry")
        click.echo("Registry initialized. Tenant databases are created per email on register.")

    @flask_app.cli.command("migrate-legacy-tenant")
    @click.option("--email", prompt=True, help="Email for the existing single-database account.")
    def migrate_legacy_tenant(email: str):
        """
        Import instance/pos_system.db (or LEGACY_DATABASE_URL) as the first tenant database.
        """
        import app.models  # noqa: F401

        db.create_all(bind_key="registry")
        legacy_uri = current_app.config.get("LEGACY_DATABASE_URI") or ""
        if not legacy_uri:
            raise click.ClickException(
                "No legacy database found. Place data at instance/pos_system.db or set LEGACY_DATABASE_URL."
            )

        e = (email or "").strip().lower()
        if TenantAccount.query.filter_by(email=e).first():
            raise click.ClickException(f"Tenant already registered for {e}")

        legacy_engine = create_engine(legacy_uri)
        with legacy_engine.connect() as conn:
            row = conn.execute(
                text("SELECT email, password_hash FROM users ORDER BY id LIMIT 1")
            ).mappings().first()
        if not row:
            raise click.ClickException("Legacy database has no users row.")

        account = TenantAccount(email=e, password_hash=row["password_hash"])
        db.session.add(account)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise click.ClickException(f"Email already registered: {e}")

        dest = tenant_db_path(account.id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        legacy_path = Path(make_url(legacy_uri).database or "")
        if not legacy_path.is_file():
            raise click.ClickException(f"Legacy database file not found: {legacy_path}")
        shutil.copy2(legacy_path, dest)
        click.echo(f"Migrated legacy DB -> tenant id={account.id} ({dest})")

    @flask_app.cli.command("upgrade-db")
    @click.option("--tenant-id", type=int, default=None, help="Upgrade one tenant (default: all).")
    def upgrade_db(tenant_id: int | None):
        """Apply schema upgrades to tenant database(s)."""
        import app.models  # noqa: F401

        db.create_all(bind_key="registry")
        if tenant_id is not None:
            _resolve_tenant_id(tenant_id)
            steps = _apply_sqlite_upgrades()
            click.echo(f"Tenant {tenant_id} upgraded. steps_applied={steps}")
            return

        accounts = TenantAccount.query.order_by(TenantAccount.id.asc()).all()
        if not accounts:
            click.echo("No tenants in registry.")
            return

        total = 0
        for account in accounts:
            use_tenant(account.id)
            steps = _apply_sqlite_upgrades()
            total += steps
            click.echo(f"Tenant {account.id} ({account.email}): steps_applied={steps}")
        click.echo(f"Done. total_steps={total}")

    @flask_app.cli.command("seed-categories")
    @click.option("--tenant-id", type=int, default=None, help="Tenant to seed (default: first).")
    def seed_categories(tenant_id: int | None):
        """Seed default product categories for a tenant."""
        tid = _resolve_tenant_id(tenant_id)
        db.create_all(bind_key="tenant")

        names = [
            "Soft drinks",
            "Starters",
            "Mains",
            "Desserts",
            "Spirits",
            "Red wine",
            "White wine",
            "Liqueurs",
            "Extras",
            "Sides",
        ]

        created = 0
        for name in names:
            if Category.query.filter_by(name=name).first():
                continue
            db.session.add(Category(name=name))
            created += 1

        db.session.commit()
        click.echo(f"Tenant {tid}: seeded categories. created={created}")

    @flask_app.cli.command("seed-products")
    @click.option("--tenant-id", type=int, default=None, help="Tenant to seed (default: first).")
    def seed_products(tenant_id: int | None):
        """Seed a small starter product catalog for a tenant."""
        tid = _resolve_tenant_id(tenant_id)
        db.create_all(bind_key="tenant")

        soft_drinks = Category.query.filter_by(name="Soft drinks").first()
        if not soft_drinks:
            soft_drinks = Category(name="Soft drinks")
            db.session.add(soft_drinks)
            db.session.commit()

        cold_drinks = [
            {"name": "Coke", "sku": "DRINK-COKE", "price": Decimal("2.50"), "category": soft_drinks},
            {"name": "Coke Zero", "sku": "DRINK-COKE-ZERO", "price": Decimal("2.50"), "category": soft_drinks},
            {"name": "Fanta Orange", "sku": "DRINK-FANTA-ORANGE", "price": Decimal("2.50"), "category": soft_drinks},
            {"name": "Sprite", "sku": "DRINK-SPRITE", "price": Decimal("2.50"), "category": soft_drinks},
            {"name": "Water (Still)", "sku": "DRINK-WATER-STILL", "price": Decimal("1.50"), "category": soft_drinks},
        ]

        created = 0
        updated = 0
        for item in cold_drinks:
            existing = Product.query.filter_by(sku=item["sku"]).first()
            if existing:
                existing.name = item["name"]
                existing.price = item["price"]
                existing.category = item["category"]
                updated += 1
                continue
            db.session.add(
                Product(
                    name=item["name"],
                    sku=item["sku"],
                    price=item["price"],
                    category=item["category"],
                )
            )
            created += 1

        db.session.commit()
        click.echo(f"Tenant {tid}: seeded products. created={created} updated={updated}")
