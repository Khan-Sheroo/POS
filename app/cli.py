from __future__ import annotations

from decimal import Decimal

import click
from flask import Flask

from app.extensions import db
from app.models import Category, Product, User


def register_cli(app: Flask) -> None:
    @app.cli.command("create-user")
    @click.option("--email", prompt=True, help="User email (unique).")
    @click.option(
        "--password",
        prompt=True,
        hide_input=True,
        confirmation_prompt=True,
        help="User password.",
    )
    def create_user(email: str, password: str):
        """Create a user for login (dev/admin)."""
        db.create_all()
        e = (email or "").strip().lower()
        if not e or not password:
            raise click.ClickException("Email and password are required.")

        existing = User.query.filter_by(email=e).first()
        if existing:
            raise click.ClickException(f"User already exists: {e}")

        user = User(email=e)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Created user id={user.id} email={user.email}")

    @app.cli.command("init-db")
    def init_db():
        """Create database tables (no migrations)."""
        # Ensure all models are imported/registered before create_all().
        import app.models  # noqa: F401

        db.create_all()
        click.echo("Database initialized.")

    @app.cli.command("reset-db")
    def reset_db():
        """Drop and recreate all database tables (dev only)."""
        import app.models  # noqa: F401

        db.drop_all()
        db.create_all()
        click.echo("Database reset.")

    @app.cli.command("upgrade-db")
    def upgrade_db():
        """
        Apply lightweight, incremental schema upgrades for existing DBs.

        This keeps existing users/staff/data without requiring reset-db.
        Currently supports SQLite by adding newly introduced columns when missing.
        """
        import app.models  # noqa: F401

        db.create_all()

        dialect = db.engine.dialect.name
        if dialect != "sqlite":
            click.echo(f"No upgrade steps for dialect={dialect}.")
            return

        def column_exists(table: str, column: str) -> bool:
            rows = db.session.execute(db.text(f"PRAGMA table_info({table})")).mappings().all()
            return any(r.get("name") == column for r in rows)

        upgraded = 0

        # 2026-05: TableOrder.opened_by_staff_id
        if not column_exists("table_orders", "opened_by_staff_id"):
            db.session.execute(db.text("ALTER TABLE table_orders ADD COLUMN opened_by_staff_id INTEGER"))
            upgraded += 1

        # 2026-05: TableOrder tip persistence
        if not column_exists("table_orders", "tip_mode"):
            db.session.execute(db.text("ALTER TABLE table_orders ADD COLUMN tip_mode VARCHAR(16)"))
            upgraded += 1
        if not column_exists("table_orders", "tip_percent"):
            db.session.execute(db.text("ALTER TABLE table_orders ADD COLUMN tip_percent NUMERIC(6,2)"))
            upgraded += 1
        if not column_exists("table_orders", "tip_amount"):
            db.session.execute(db.text("ALTER TABLE table_orders ADD COLUMN tip_amount NUMERIC(10,2)"))
            upgraded += 1
        if not column_exists("table_orders", "committed_at"):
            db.session.execute(db.text("ALTER TABLE table_orders ADD COLUMN committed_at DATETIME"))
            upgraded += 1

        if not column_exists("staff_cashup_completions", "expected_cash"):
            db.session.execute(db.text("ALTER TABLE staff_cashup_completions ADD COLUMN expected_cash NUMERIC(10,2)"))
            upgraded += 1
        if not column_exists("staff_cashup_completions", "expected_credit"):
            db.session.execute(db.text("ALTER TABLE staff_cashup_completions ADD COLUMN expected_credit NUMERIC(10,2)"))
            upgraded += 1
        if not column_exists("staff_cashup_completions", "actual_cash"):
            db.session.execute(db.text("ALTER TABLE staff_cashup_completions ADD COLUMN actual_cash NUMERIC(10,2)"))
            upgraded += 1
        if not column_exists("staff_cashup_completions", "actual_credit"):
            db.session.execute(db.text("ALTER TABLE staff_cashup_completions ADD COLUMN actual_credit NUMERIC(10,2)"))
            upgraded += 1
        if not column_exists("staff_cashup_completions", "is_balanced"):
            db.session.execute(db.text("ALTER TABLE staff_cashup_completions ADD COLUMN is_balanced BOOLEAN NOT NULL DEFAULT 0"))
            upgraded += 1

        if not column_exists("settings", "trading_date"):
            db.session.execute(db.text("ALTER TABLE settings ADD COLUMN trading_date DATE"))
            upgraded += 1

        if not column_exists("settings", "active_staff_id"):
            db.session.execute(db.text("ALTER TABLE settings ADD COLUMN active_staff_id INTEGER"))
            upgraded += 1

        if not column_exists("sales", "customer_account_id"):
            db.session.execute(db.text("ALTER TABLE sales ADD COLUMN customer_account_id INTEGER"))
            upgraded += 1

        if not column_exists("products", "temperature_group_id"):
            db.session.execute(db.text("ALTER TABLE products ADD COLUMN temperature_group_id INTEGER"))
            upgraded += 1
        if not column_exists("table_order_items", "temperature"):
            db.session.execute(db.text("ALTER TABLE table_order_items ADD COLUMN temperature VARCHAR(64)"))
            upgraded += 1
        if not column_exists("sale_items", "temperature"):
            db.session.execute(db.text("ALTER TABLE sale_items ADD COLUMN temperature VARCHAR(64)"))
            upgraded += 1

        def table_exists(table: str) -> bool:
            row = db.session.execute(
                db.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
                {"t": table},
            ).first()
            return row is not None

        if not table_exists("temperature_groups") or not table_exists("temperature_options"):
            db.create_all()
            upgraded += 1

        if not table_exists("day_ends"):
            db.create_all()
            upgraded += 1

        # 2026-05: one master cash up per user per trading day
        if not column_exists("cash_ups", "user_id"):
            db.session.execute(db.text("ALTER TABLE cash_ups ADD COLUMN user_id INTEGER"))
            upgraded += 1
        if not column_exists("cash_ups", "trading_date"):
            db.session.execute(db.text("ALTER TABLE cash_ups ADD COLUMN trading_date DATE"))
            upgraded += 1

        def index_exists(name: str) -> bool:
            row = db.session.execute(
                db.text("SELECT name FROM sqlite_master WHERE type='index' AND name=:n"),
                {"n": name},
            ).first()
            return row is not None

        if not table_exists("staff_login_sessions"):
            db.create_all()
            upgraded += 1
        elif table_exists("settings") and column_exists("settings", "active_staff_id"):
            rows = db.session.execute(
                db.text("SELECT user_id, active_staff_id FROM settings WHERE active_staff_id IS NOT NULL")
            ).mappings().all()
            for row in rows:
                exists = db.session.execute(
                    db.text(
                        "SELECT 1 FROM staff_login_sessions "
                        "WHERE user_id = :uid AND staff_id = :sid LIMIT 1"
                    ),
                    {"uid": row["user_id"], "sid": row["active_staff_id"]},
                ).first()
                if not exists:
                    db.session.execute(
                        db.text(
                            "INSERT INTO staff_login_sessions (user_id, staff_id, logged_in_at) "
                            "VALUES (:uid, :sid, datetime('now'))"
                        ),
                        {"uid": row["user_id"], "sid": row["active_staff_id"]},
                    )
                    upgraded += 1

        if table_exists("cash_ups"):
            orphan = db.session.execute(
                db.text("SELECT id, created_at FROM cash_ups WHERE user_id IS NULL OR trading_date IS NULL")
            ).mappings().all()
            if orphan:
                default_user = db.session.execute(db.text("SELECT id FROM users ORDER BY id LIMIT 1")).scalar()
                if default_user is None:
                    click.echo("Warning: cash_ups rows need user_id but no users exist.")
                else:
                    for row in orphan:
                        created = row.get("created_at")
                        trading = created.date() if hasattr(created, "date") else None
                        db.session.execute(
                            db.text(
                                "UPDATE cash_ups SET user_id = :uid, trading_date = :td WHERE id = :id"
                            ),
                            {"uid": default_user, "td": trading, "id": row["id"]},
                        )
                    upgraded += 1

            if not index_exists("uq_cash_up_user_trading_date"):
                db.session.execute(
                    db.text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_up_user_trading_date "
                        "ON cash_ups (user_id, trading_date)"
                    )
                )
                upgraded += 1

        db.session.commit()
        click.echo(f"Database upgraded. steps_applied={upgraded}")

    @app.cli.command("seed-categories")
    def seed_categories():
        """Seed default product categories."""
        db.create_all()

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
            existing = Category.query.filter_by(name=name).first()
            if existing:
                continue
            db.session.add(Category(name=name))
            created += 1

        db.session.commit()
        click.echo(f"Seeded categories. created={created}")

    @app.cli.command("seed-products")
    def seed_products():
        """Seed a small starter product catalog."""
        # Ensure tables exist.
        db.create_all()

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
        click.echo(f"Seeded cold drinks. created={created} updated={updated}")

