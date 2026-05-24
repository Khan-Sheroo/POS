from __future__ import annotations

from flask import Blueprint, render_template, redirect


web_bp = Blueprint("web", __name__)


@web_bp.get("/login")
def login_page():
    return render_template("login.html")


@web_bp.get("/register")
def register_page():
    return render_template("register.html")


@web_bp.get("/staff/login")
def staff_login_page():
    return render_template("staff_login.html")

@web_bp.get("/staff/new")
def staff_new_page():
    return render_template("staff_new.html")


@web_bp.get("/pos")
def pos_page():
    """Legacy URL — staff ordering uses table order (commit, pay, prep instructions)."""
    return redirect("/tables/list", code=302)

@web_bp.get("/products")
def products_page():
    return render_template("products.html")


@web_bp.get("/categories")
def categories_page():
    return render_template("categories.html")


@web_bp.get("/accounts")
def accounts_page():
    return render_template("accounts.html")


@web_bp.get("/cashups")
def cashups_page():
    return render_template("cash_ups.html")


# Backwards compatible alias.
@web_bp.get("/cash-ups")
def cash_ups_alias():
    return redirect("/cashups", code=302)


@web_bp.get("/tables")
def tables_home_page():
    return render_template("tables_home.html")


@web_bp.get("/tables/new")
def tables_new_page():
    return render_template("tables_new.html")


@web_bp.get("/tables/list")
def tables_list_page():
    return render_template("tables_list.html")

@web_bp.get("/tables/closed")
def tables_closed_page():
    return render_template("tables_closed.html")


@web_bp.get("/tables/<int:table_id>/order")
def table_order_page(table_id: int):
    return render_template("table_order.html", table_id=table_id)


@web_bp.get("/tables/<int:table_id>/order/closed")
def table_order_closed_page(table_id: int):
    return render_template("table_order_closed.html", table_id=table_id)


@web_bp.get("/reports/today")
def reports_today_page():
    return render_template("reports_today.html")


@web_bp.get("/settings")
def settings_page():
    return render_template("settings.html")

