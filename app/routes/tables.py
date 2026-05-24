from __future__ import annotations

from collections import Counter
from decimal import Decimal
from datetime import datetime

from flask import Blueprint, jsonify, request
from sqlalchemy import and_, exists, select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Product, Sale, Staff, Table, TableOrder, TableOrderItem, TemperatureGroup
from app.utils.auth import login_required
from app.utils.manager_auth import closed_tables_access_allowed, verify_manager_pin
from app.utils.serialization import money_to_str


tables_bp = Blueprint("tables", __name__, url_prefix="/tables")


def _require_closed_tables_access():
    user = request.current_user  # type: ignore[attr-defined]
    staff = getattr(request, "current_staff", None)
    pin = request.headers.get("X-Manager-Pin", "")
    if closed_tables_access_allowed(user, staff, pin):
        return None
    return jsonify({"error": "manager_authorization_required"}), 403


def _table_json(t: Table) -> dict:
    return {
        "id": t.id,
        "table_number": t.table_number,
        "pax_count": t.pax_count,
        "created_at": t.created_at.isoformat(),
    }


@tables_bp.get("")
@login_required
def list_tables():
    staff = getattr(request, "current_staff", None)
    # Only show "open" tables by default:
    # a table is considered open if it has an open TableOrder.
    q = (
        Table.query.join(TableOrder, TableOrder.table_id == Table.id)
        .filter(TableOrder.status == "open")
    )
    # Non-managers: only see their own tables.
    if staff and getattr(staff, "role", "") != "manager":
        q = q.filter(TableOrder.opened_by_staff_id == staff.id)
    tables = q.order_by(Table.table_number.asc()).distinct().all()
    return jsonify([_table_json(t) for t in tables])


@tables_bp.get("/closed")
@login_required
def list_closed_tables():
    denied = _require_closed_tables_access()
    if denied:
        return denied
    staff = getattr(request, "current_staff", None)
    # Closed tables: at least one closed order AND no open orders.
    has_closed = exists(
        select(1).where(and_(TableOrder.table_id == Table.id, TableOrder.status == "closed"))
    )
    has_open = exists(select(1).where(and_(TableOrder.table_id == Table.id, TableOrder.status == "open")))

    q = Table.query.filter(has_closed).filter(~has_open)
    if staff and getattr(staff, "role", "") != "manager":
        # Latest closed table listing will be refined in /closed/details; here do a coarse filter.
        q = q.join(TableOrder, TableOrder.table_id == Table.id).filter(
            TableOrder.status == "closed", TableOrder.opened_by_staff_id == staff.id
        )
    tables = q.order_by(Table.table_number.asc()).distinct().all()

    return jsonify([_table_json(t) for t in tables])


@tables_bp.put("/<int:table_id>")
@login_required
def update_table(table_id: int):
    table = Table.query.get(table_id)
    if not table:
        return jsonify({"error": "table_not_found"}), 404

    data = request.get_json(silent=True) or {}
    if "pax_count" in data:
        pax_count = data.get("pax_count")
        if not isinstance(pax_count, int) or pax_count <= 0:
            return jsonify({"error": "pax_count_invalid"}), 400
        table.pax_count = pax_count

    db.session.commit()
    return jsonify(_table_json(table))


@tables_bp.put("/<int:table_id>/owner")
@login_required
def update_table_owner(table_id: int):
    """
    Change the owner (opened_by_staff_id) of the current open table order.

    - Managers can change without extra authorization.
    - Non-managers require a valid manager PIN.
    """
    user = request.current_user  # type: ignore[attr-defined]
    staff = getattr(request, "current_staff", None)
    if not staff:
        return jsonify({"error": "staff_login_required"}), 403

    table = Table.query.get(table_id)
    if not table:
        return jsonify({"error": "table_not_found"}), 404

    order = TableOrder.query.filter_by(table_id=table_id, status="open").first()
    if not order:
        return jsonify({"error": "no_open_order"}), 400

    data = request.get_json(silent=True) or {}
    new_owner_id = data.get("opened_by_staff_id")
    manager_pin = str(data.get("manager_pin") or "").strip()

    if not isinstance(new_owner_id, int) or new_owner_id <= 0:
        return jsonify({"error": "opened_by_staff_id_invalid"}), 400

    new_owner = Staff.query.get(new_owner_id)
    if not new_owner or not new_owner.active or new_owner.user_id != user.id:
        return jsonify({"error": "staff_not_found"}), 404

    # Auth gate for non-managers.
    if getattr(staff, "role", "") != "manager":
        if not (manager_pin.isdigit() and len(manager_pin) in {4, 5}):
            return jsonify({"error": "manager_pin_required"}), 401
        managers = Staff.query.filter_by(user_id=user.id, role="manager", active=True).all()
        if not any(m.check_pin(manager_pin) for m in managers):
            return jsonify({"error": "invalid_pin"}), 401

    order.opened_by_staff_id = new_owner.id
    db.session.commit()
    return jsonify(_order_json(order))


@tables_bp.post("")
@login_required
def create_table():
    data = request.get_json(silent=True) or {}
    table_number = data.get("table_number")
    pax_count = data.get("pax_count")
    manager_pin = str(data.get("manager_pin") or "").strip()

    if not isinstance(table_number, int) or table_number <= 0:
        return jsonify({"error": "table_number_invalid"}), 400
    if not isinstance(pax_count, int) or pax_count <= 0:
        return jsonify({"error": "pax_count_invalid"}), 400

    staff = getattr(request, "current_staff", None)
    user = request.current_user  # type: ignore[attr-defined]

    existing = Table.query.filter_by(table_number=table_number).first()
    if existing:
        # If the table already exists, ensure it has an open order so it is visible in the UI.
        existing.pax_count = pax_count
        open_order = TableOrder.query.filter_by(table_id=existing.id, status="open").first()
        if open_order and staff and getattr(staff, "role", "") != "manager":
            # Table exists and belongs to someone else; require manager PIN override to view/open.
            if open_order.opened_by_staff_id != staff.id:
                if not (manager_pin.isdigit() and len(manager_pin) in {4, 5}):
                    owner = Staff.query.get(open_order.opened_by_staff_id) if open_order.opened_by_staff_id else None
                    return (
                        jsonify(
                            {
                                "error": "table_exists_owned_by_other",
                                "table": _table_json(existing),
                                "opened_by_staff": (
                                    {"id": owner.id, "name": owner.name, "role": owner.role} if owner else None
                                ),
                            }
                        ),
                        409,
                    )
                managers = Staff.query.filter_by(user_id=user.id, role="manager", active=True).all()
                if not any(m.check_pin(manager_pin) for m in managers):
                    return jsonify({"error": "manager_pin_required"}), 401
        if not open_order:
            order = TableOrder(table_id=existing.id, status="open")
            if staff:
                order.opened_by_staff_id = staff.id
            db.session.add(order)
        db.session.commit()
        return jsonify(_table_json(existing)), 200

    t = Table(table_number=table_number, pax_count=pax_count)
    db.session.add(t)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        # Race-condition safety: return existing if it was created concurrently.
        existing = Table.query.filter_by(table_number=table_number).first()
        if existing:
            return jsonify(_table_json(existing)), 200
        return jsonify({"error": "table_number_already_exists"}), 409

    # Create an open order so the table appears immediately.
    order = TableOrder(table_id=t.id, status="open")
    staff = getattr(request, "current_staff", None)
    if staff:
        order.opened_by_staff_id = staff.id
    db.session.add(order)
    db.session.commit()
    return jsonify(_table_json(t)), 201


def _temperature_labels_for_product(product: Product) -> set[str]:
    group = getattr(product, "temperature_group", None)
    if group is None and product.temperature_group_id:
        group = TemperatureGroup.query.get(product.temperature_group_id)
    if not group:
        return set()
    return {o.label for o in group.options or []}


def _parse_item_temperature(raw: dict, product: Product) -> tuple[str | None, tuple[dict, int] | None]:
    temp_raw = raw.get("temperature")
    temp = None
    if temp_raw is not None:
        temp = str(temp_raw).strip() or None
        if temp and len(temp) > 64:
            return None, ({"error": "temperature_invalid"}, 400)

    if product.temperature_group_id:
        if not temp:
            return None, ({"error": "temperature_required", "product_id": product.id}, 400)
        if temp not in _temperature_labels_for_product(product):
            return None, ({"error": "temperature_invalid", "product_id": product.id}, 400)
    elif temp:
        return None, ({"error": "temperature_not_allowed", "product_id": product.id}, 400)
    return temp, None


def _order_item_json(item: TableOrderItem) -> dict:
    line_total = (item.price_at_time or Decimal("0")) * (item.quantity or 0)
    return {
        "id": item.id,
        "product_id": item.product_id,
        "quantity": item.quantity,
        "price_at_time": money_to_str(item.price_at_time),
        "line_total": money_to_str(line_total),
        "temperature": getattr(item, "temperature", None),
        "product": (
            {"id": item.product.id, "name": item.product.name, "sku": item.product.sku}
            if getattr(item, "product", None) is not None
            else None
        ),
    }


def _order_json(order: TableOrder) -> dict:
    total = Decimal("0")
    for i in order.items:
        total += (i.price_at_time or Decimal("0")) * (i.quantity or 0)
    sale = Sale.query.get(order.closed_sale_id) if getattr(order, "closed_sale_id", None) else None
    sale_total = sale.total_amount if sale else None
    cash_received = getattr(order, "cash_received", None)
    card_amount = getattr(order, "card_amount", None)
    tip = (card_amount - sale_total) if (card_amount is not None and sale_total is not None) else None
    change = (cash_received - sale_total) if (cash_received is not None and sale_total is not None) else None
    sale_items = []
    if sale is not None:
        for si in sale.items:
            line_total = (si.price_at_time or Decimal("0")) * (si.quantity or 0)
            sale_items.append(
                {
                    "id": si.id,
                    "product_id": si.product_id,
                    "quantity": si.quantity,
                    "price_at_time": money_to_str(si.price_at_time),
                    "line_total": money_to_str(line_total),
                    "temperature": getattr(si, "temperature", None),
                    "product": (
                        {
                            "id": si.product.id,
                            "name": si.product.name,
                            "sku": si.product.sku,
                        }
                        if getattr(si, "product", None) is not None
                        else None
                    ),
                }
            )
    tip_mode = getattr(order, "tip_mode", None)
    tip_percent = getattr(order, "tip_percent", None)
    tip_amount = getattr(order, "tip_amount", None)

    has_closed_history = (
        TableOrder.query.filter(
            TableOrder.table_id == order.table_id,
            TableOrder.status == "closed",
            TableOrder.id != order.id,
        ).count()
        > 0
    )

    return {
        "id": order.id,
        "table_id": order.table_id,
        "status": order.status,
        "has_closed_history": has_closed_history,
        "created_at": order.created_at.isoformat(),
        "updated_at": order.updated_at.isoformat() if order.updated_at else order.created_at.isoformat(),
        "committed_at": order.committed_at.isoformat() if getattr(order, "committed_at", None) else None,
        "is_committed": bool(getattr(order, "committed_at", None)),
        "total_amount": money_to_str(total),
        "tip": {
            "mode": tip_mode,
            "percent": money_to_str(tip_percent) if tip_percent is not None else None,
            "amount": money_to_str(tip_amount) if tip_amount is not None else None,
        },
        "opened_by_staff": (
            {
                "id": order.opened_by_staff.id,
                "name": order.opened_by_staff.name,
                "role": order.opened_by_staff.role,
            }
            if getattr(order, "opened_by_staff", None) is not None
            else None
        ),
        "items": [_order_item_json(i) for i in order.items],
        "payment": (
            {
                "sale_id": sale.id,
                "payment_method": sale.payment_method,
                "sale_total_amount": money_to_str(sale.total_amount),
                "cash_received": money_to_str(cash_received),
                "card_amount": money_to_str(card_amount),
                "change": money_to_str(change),
                "tip": money_to_str(tip),
                "created_at": sale.created_at.isoformat(),
                "sale_items": sale_items,
            }
            if sale is not None
            else None
        ),
    }


def _find_or_create_open_order(table_id: int, staff: Staff | None) -> TableOrder:
    order = TableOrder.query.filter_by(table_id=table_id, status="open").first()
    if order:
        return order
    order = TableOrder(table_id=table_id, status="open")
    if staff:
        order.opened_by_staff_id = staff.id
    db.session.add(order)
    db.session.flush()
    return order


def _merge_order_items(order: TableOrder) -> None:
    """
    Merge duplicate lines (same product_id + price_at_time) by summing quantities.
    Keeps data tidy after transfers/splits.
    """
    by_key: dict[tuple[int, str, str], tuple[int, Decimal]] = {}
    for it in list(order.items or []):
        temp_key = (it.temperature or "") if getattr(it, "temperature", None) else ""
        key = (it.product_id, str(it.price_at_time), temp_key)
        prev_qty, prev_price = by_key.get(key, (0, it.price_at_time))
        by_key[key] = (prev_qty + (it.quantity or 0), prev_price)
    order.items = []
    for (pid, price_str, temp_key), (qty, price_dec) in by_key.items():
        if qty <= 0:
            continue
        order.items.append(
            TableOrderItem(
                product_id=pid,
                quantity=qty,
                price_at_time=Decimal(price_str) if price_str else price_dec,
                temperature=temp_key or None,
            )
        )


def _order_item_counter(items) -> Counter:
    counts: Counter = Counter()
    for it in items or []:
        temp = (getattr(it, "temperature", None) or "").strip() or ""
        counts[(it.product_id, temp)] += int(it.quantity or 0)
    return counts


def _order_cart_was_reduced(old_items, new_entries: list[dict], products_by_id: dict) -> bool:
    """True when new cart has fewer items than previously saved (remove or qty decrease)."""
    old_c = _order_item_counter(old_items)
    new_c: Counter = Counter()
    for entry in new_entries:
        product = products_by_id[entry["product_id"]]
        temp, err = _parse_item_temperature(entry["raw"], product)
        if err:
            continue
        temp_key = (temp or "").strip() or ""
        new_c[(entry["product_id"], temp_key)] += entry["quantity"]
    for key, old_qty in old_c.items():
        if new_c.get(key, 0) < old_qty:
            return True
    return False


def _require_manager_pin_for_reduction(staff, manager_pin: str) -> tuple[dict, int] | None:
    if staff and getattr(staff, "role", "") == "manager":
        return None
    user = request.current_user  # type: ignore[attr-defined]
    pin = str(manager_pin or "").strip()
    if not (pin.isdigit() and len(pin) in {4, 5}):
        return {"error": "manager_pin_required"}, 401
    if not verify_manager_pin(user.id, pin):
        return {"error": "invalid_pin"}, 401
    return None


@tables_bp.post("/<int:table_id>/order/transfer")
@login_required
def transfer_table_order(table_id: int):
    """
    Transfer items from one open table to another.

    Payload:
      - mode: "whole" | "split"
      - target_table_id: int
      - items: [{product_id:int, quantity:int}] (required for split)
    """
    staff = getattr(request, "current_staff", None)

    src_table = Table.query.get(table_id)
    if not src_table:
        return jsonify({"error": "table_not_found"}), 404

    src_order = TableOrder.query.filter_by(table_id=table_id, status="open").first()
    if not src_order:
        return jsonify({"error": "no_open_order"}), 400

    # Non-managers can only move from their own table.
    if staff and getattr(staff, "role", "") != "manager" and src_order.opened_by_staff_id != staff.id:
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode") or "").strip().lower()
    target_table_id = data.get("target_table_id")
    if mode not in {"whole", "split"}:
        return jsonify({"error": "mode_invalid"}), 400
    if not isinstance(target_table_id, int) or target_table_id <= 0:
        return jsonify({"error": "target_table_id_invalid"}), 400
    if target_table_id == table_id:
        return jsonify({"error": "target_table_same_as_source"}), 400

    target_table = Table.query.get(target_table_id)
    if not target_table:
        return jsonify({"error": "target_table_not_found"}), 404

    target_order = _find_or_create_open_order(target_table_id, staff)

    # Non-managers can only transfer into their own open table (or a table they can see).
    if staff and getattr(staff, "role", "") != "manager" and target_order.opened_by_staff_id != staff.id:
        return jsonify({"error": "forbidden"}), 403

    if mode == "whole":
        if not src_order.items:
            return jsonify({"error": "empty_items"}), 400
        # Move all items
        for it in list(src_order.items):
            target_order.items.append(
                TableOrderItem(
                    product_id=it.product_id,
                    quantity=it.quantity,
                    price_at_time=it.price_at_time,
                    temperature=getattr(it, "temperature", None),
                )
            )
        src_order.items = []
        # Move tip too (tip belongs to the bill)
        target_order.tip_mode = getattr(src_order, "tip_mode", None)
        target_order.tip_percent = getattr(src_order, "tip_percent", None)
        target_order.tip_amount = getattr(src_order, "tip_amount", None)
        src_order.tip_mode = None
        src_order.tip_percent = None
        src_order.tip_amount = None
        _merge_order_items(target_order)
        db.session.commit()
        return jsonify({"source": _order_json(src_order), "target": _order_json(target_order)})

    # split
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return jsonify({"error": "items_required"}), 400

    wanted: dict[tuple[int, str], int] = {}
    for idx, raw in enumerate(items):
        if not isinstance(raw, dict):
            return jsonify({"error": "item_invalid", "index": idx}), 400
        pid = raw.get("product_id")
        qty = raw.get("quantity")
        temp = str(raw.get("temperature") or "").strip() if raw.get("temperature") is not None else ""
        if not isinstance(pid, int) or pid <= 0:
            return jsonify({"error": "product_id_invalid", "index": idx}), 400
        if not isinstance(qty, int) or qty <= 0:
            return jsonify({"error": "quantity_invalid", "index": idx}), 400
        key = (pid, temp)
        wanted[key] = wanted.get(key, 0) + qty

    available: dict[tuple[int, str], list[TableOrderItem]] = {}
    for it in list(src_order.items):
        temp = (getattr(it, "temperature", None) or "") or ""
        available.setdefault((it.product_id, temp), []).append(it)

    for key, qty in wanted.items():
        have = sum((i.quantity or 0) for i in available.get(key, []))
        if have < qty:
            return jsonify(
                {
                    "error": "insufficient_quantity",
                    "product_id": key[0],
                    "temperature": key[1] or None,
                    "available": have,
                    "requested": qty,
                }
            ), 400

    for key, qty in wanted.items():
        remaining = qty
        for src_it in list(available.get(key, [])):
            if remaining <= 0:
                break
            take = min(remaining, src_it.quantity or 0)
            if take <= 0:
                continue
            target_order.items.append(
                TableOrderItem(
                    product_id=src_it.product_id,
                    quantity=take,
                    price_at_time=src_it.price_at_time,
                    temperature=getattr(src_it, "temperature", None),
                )
            )
            src_it.quantity = (src_it.quantity or 0) - take
            remaining -= take
        # Cleanup zero/negative lines
        src_order.items = [i for i in src_order.items if (i.quantity or 0) > 0]

    _merge_order_items(target_order)
    db.session.commit()
    return jsonify({"source": _order_json(src_order), "target": _order_json(target_order)})

@tables_bp.get("/<int:table_id>/order")
@login_required
def get_table_order(table_id: int):
    staff = getattr(request, "current_staff", None)
    table = Table.query.get(table_id)
    if not table:
        return jsonify({"error": "table_not_found"}), 404

    order = TableOrder.query.filter_by(table_id=table_id, status="open").first()
    if not order:
        # Do NOT auto-create an open order here.
        # Closed tables should stay closed unless explicitly reopened.
        return jsonify({"error": "no_open_order"}), 404

    if staff and getattr(staff, "role", "") != "manager" and order.opened_by_staff_id != staff.id:
        return jsonify({"error": "forbidden"}), 403
    return jsonify(_order_json(order))


@tables_bp.put("/<int:table_id>/order")
@login_required
def save_table_order(table_id: int):
    staff = getattr(request, "current_staff", None)
    table = Table.query.get(table_id)
    if not table:
        return jsonify({"error": "table_not_found"}), 404

    data = request.get_json(silent=True) or {}
    items = data.get("items")
    if not isinstance(items, list):
        return jsonify({"error": "items_required"}), 400

    tip_data = data.get("tip") if isinstance(data.get("tip"), dict) else {}
    tip_mode = tip_data.get("mode")
    tip_percent = tip_data.get("percent")
    tip_amount = tip_data.get("amount")
    manager_pin = str(data.get("manager_pin") or "").strip()

    def _dec_or_none(v):
        if v is None:
            return None
        try:
            return Decimal(str(v))
        except Exception:
            return None

    tip_percent_dec = _dec_or_none(tip_percent)
    tip_amount_dec = _dec_or_none(tip_amount)

    if tip_mode is not None and str(tip_mode) not in {"none", "percent", "amount"}:
        return jsonify({"error": "tip_mode_invalid"}), 400
    if tip_percent_dec is not None and tip_percent_dec < 0:
        return jsonify({"error": "tip_percent_invalid"}), 400
    if tip_amount_dec is not None and tip_amount_dec < 0:
        return jsonify({"error": "tip_amount_invalid"}), 400

    # Tip authorization: only managers can set tip; non-managers require manager PIN.
    m = str(tip_mode) if tip_mode is not None else "none"
    is_setting_tip = m in {"percent", "amount"} and ((tip_percent_dec or Decimal("0")) > 0 or (tip_amount_dec or Decimal("0")) > 0)
    if is_setting_tip and (not staff or getattr(staff, "role", "") != "manager"):
        user = request.current_user  # type: ignore[attr-defined]
        if not (manager_pin.isdigit() and len(manager_pin) in {4, 5}):
            return jsonify({"error": "manager_pin_required"}), 401
        managers = Staff.query.filter_by(user_id=user.id, role="manager", active=True).all()
        if not any(mgr.check_pin(manager_pin) for mgr in managers):
            return jsonify({"error": "invalid_pin"}), 401

    normalized: list[dict] = []
    product_ids: set[int] = set()
    for idx, raw in enumerate(items):
        if not isinstance(raw, dict):
            return jsonify({"error": "item_invalid", "index": idx}), 400
        product_id = raw.get("product_id")
        quantity = raw.get("quantity")
        if not isinstance(product_id, int) or product_id <= 0:
            return jsonify({"error": "product_id_invalid", "index": idx}), 400
        if not isinstance(quantity, int) or quantity <= 0:
            return jsonify({"error": "quantity_invalid", "index": idx}), 400
        normalized.append({"product_id": product_id, "quantity": quantity, "raw": raw})
        product_ids.add(product_id)

    products = Product.query.filter(Product.id.in_(product_ids)).all()
    products_by_id = {p.id: p for p in products}
    missing = [pid for pid in product_ids if pid not in products_by_id]
    if missing:
        return jsonify({"error": "products_not_found", "product_ids": sorted(missing)}), 400

    order = TableOrder.query.filter_by(table_id=table_id, status="open").first()
    if not order:
        return jsonify({"error": "no_open_order"}), 400
    if staff and getattr(staff, "role", "") != "manager" and order.opened_by_staff_id != staff.id:
        return jsonify({"error": "forbidden"}), 403

    if getattr(order, "committed_at", None) and _order_cart_was_reduced(order.items, normalized, products_by_id):
        pin_err = _require_manager_pin_for_reduction(staff, manager_pin)
        if pin_err:
            return jsonify(pin_err[0]), pin_err[1]

    built_items: list[TableOrderItem] = []
    for entry in normalized:
        product = products_by_id[entry["product_id"]]
        temp, err = _parse_item_temperature(entry["raw"], product)
        if err:
            return jsonify(err[0]), err[1]
        built_items.append(
            TableOrderItem(
                product_id=entry["product_id"],
                quantity=entry["quantity"],
                price_at_time=product.price,
                temperature=temp,
            )
        )

    order.items = built_items

    # Persist tip on the open table order (survives reload/reopen).
    m = str(tip_mode) if tip_mode is not None else None
    if m in {None, "none", ""}:
        order.tip_mode = None
        order.tip_percent = None
        order.tip_amount = None
    elif m == "percent":
        order.tip_mode = "percent"
        order.tip_percent = tip_percent_dec
        order.tip_amount = None
    else:  # amount
        order.tip_mode = "amount"
        order.tip_amount = tip_amount_dec
        order.tip_percent = None

    db.session.commit()
    return jsonify(_order_json(order))


@tables_bp.post("/<int:table_id>/order/commit")
@login_required
def commit_table_order(table_id: int):
    staff = getattr(request, "current_staff", None)
    table = Table.query.get(table_id)
    if not table:
        return jsonify({"error": "table_not_found"}), 404

    order = TableOrder.query.filter_by(table_id=table_id, status="open").first()
    if not order:
        return jsonify({"error": "no_open_order"}), 400
    if staff and getattr(staff, "role", "") != "manager" and order.opened_by_staff_id != staff.id:
        return jsonify({"error": "forbidden"}), 403

    # Commit sends order to kitchen; table stays open until checkout.
    if not getattr(order, "committed_at", None):
        order.committed_at = datetime.utcnow()

    db.session.commit()
    db.session.refresh(order)
    return jsonify(_order_json(order))


@tables_bp.post("/<int:table_id>/order/close")
@login_required
def close_table_order(table_id: int):
    table = Table.query.get(table_id)
    if not table:
        return jsonify({"error": "table_not_found"}), 404

    open_orders = TableOrder.query.filter_by(table_id=table_id, status="open").all()
    if not open_orders:
        return jsonify({"error": "no_open_order"}), 400

    data = request.get_json(silent=True) or {}
    sale_id = data.get("sale_id")
    cash_received = data.get("cash_received")
    card_amount = data.get("card_amount")

    sale = None
    if sale_id is not None:
        if not isinstance(sale_id, int) or sale_id <= 0:
            return jsonify({"error": "sale_id_invalid"}), 400
        sale = Sale.query.get(sale_id)
        if not sale:
            return jsonify({"error": "sale_not_found"}), 400

    def _num_or_none(v):
        if v is None:
            return None
        try:
            return Decimal(str(v))
        except Exception:
            return None

    cash_dec = _num_or_none(cash_received)
    card_dec = _num_or_none(card_amount)
    if cash_dec is not None and cash_dec < 0:
        return jsonify({"error": "cash_received_invalid"}), 400
    if card_dec is not None and card_dec < 0:
        return jsonify({"error": "card_amount_invalid"}), 400

    # Close the table: close ALL open orders (older data may have duplicates).
    for o in open_orders:
        o.status = "closed"
        if sale is not None:
            o.closed_sale_id = sale.id
            o.cash_received = cash_dec
            o.card_amount = card_dec
    db.session.commit()

    closed = sorted(open_orders, key=lambda o: o.updated_at or o.created_at, reverse=True)[0]
    return jsonify(_order_json(closed))


@tables_bp.get("/closed/details")
@login_required
def list_closed_tables_details():
    denied = _require_closed_tables_access()
    if denied:
        return denied
    staff = getattr(request, "current_staff", None)
    # Tables that have at least one closed order and no open orders.
    has_closed = exists(
        select(1).where(and_(TableOrder.table_id == Table.id, TableOrder.status == "closed"))
    )
    has_open = exists(select(1).where(and_(TableOrder.table_id == Table.id, TableOrder.status == "open")))
    tables = Table.query.filter(has_closed).filter(~has_open).order_by(Table.table_number.asc()).all()

    out: list[dict] = []
    for t in tables:
        order = (
            TableOrder.query.filter_by(table_id=t.id, status="closed")
            .order_by(TableOrder.updated_at.desc())
            .first()
        )
        if not order:
            continue
        if staff and getattr(staff, "role", "") != "manager" and order.opened_by_staff_id != staff.id:
            continue
        out.append({"table": _table_json(t), "order": _order_json(order)})
    return jsonify(out)


@tables_bp.get("/<int:table_id>/order/closed/latest")
@login_required
def get_latest_closed_order(table_id: int):
    denied = _require_closed_tables_access()
    if denied:
        return denied
    staff = getattr(request, "current_staff", None)
    table = Table.query.get(table_id)
    if not table:
        return jsonify({"error": "table_not_found"}), 404

    order = (
        TableOrder.query.filter_by(table_id=table_id, status="closed")
        .order_by(TableOrder.updated_at.desc())
        .first()
    )
    if not order:
        return jsonify({"error": "no_closed_order"}), 404
    if staff and getattr(staff, "role", "") != "manager" and order.opened_by_staff_id != staff.id:
        return jsonify({"error": "forbidden"}), 403
    return jsonify({"table": _table_json(table), "order": _order_json(order)})


@tables_bp.post("/<int:table_id>/reopen")
@login_required
def reopen_table(table_id: int):
    """
    Reopen a closed table by creating a new open TableOrder seeded from the latest closed order.
    """
    denied = _require_closed_tables_access()
    if denied:
        return denied
    table = Table.query.get(table_id)
    if not table:
        return jsonify({"error": "table_not_found"}), 404

    staff = getattr(request, "current_staff", None)
    existing_open = TableOrder.query.filter_by(table_id=table_id, status="open").first()
    if existing_open:
        if staff and not existing_open.opened_by_staff_id:
            existing_open.opened_by_staff_id = staff.id
            db.session.commit()
        return jsonify(_order_json(existing_open)), 200

    closed = (
        TableOrder.query.filter_by(table_id=table_id, status="closed")
        .order_by(TableOrder.updated_at.desc())
        .first()
    )
    if not closed:
        return jsonify({"error": "no_closed_order"}), 404

    # Keep reporting consistent: reopening voids the previous sale (if any).
    if getattr(closed, "closed_sale_id", None):
        sale = Sale.query.get(closed.closed_sale_id)
        if sale and getattr(sale, "status", "active") == "active":
            sale.status = "voided"
            sale.voided_at = datetime.utcnow()

    # Build items from closed order items; if empty, fall back to linked sale items.
    seed_items: list[dict] = []
    if closed.items:
        for it in closed.items:
            seed_items.append(
                {
                    "product_id": it.product_id,
                    "quantity": it.quantity,
                    "price_at_time": it.price_at_time,
                    "temperature": getattr(it, "temperature", None),
                }
            )
    elif getattr(closed, "closed_sale_id", None):
        sale = Sale.query.get(closed.closed_sale_id)
        if sale:
            for si in sale.items:
                seed_items.append(
                    {
                        "product_id": si.product_id,
                        "quantity": si.quantity,
                        "price_at_time": si.price_at_time,
                        "temperature": getattr(si, "temperature", None),
                    }
                )

    order = TableOrder(table_id=table_id, status="open")
    if staff:
        order.opened_by_staff_id = staff.id
    # Carry tip forward when reopening from latest closed order.
    order.tip_mode = getattr(closed, "tip_mode", None)
    order.tip_percent = getattr(closed, "tip_percent", None)
    order.tip_amount = getattr(closed, "tip_amount", None)
    order.items = [
        TableOrderItem(
            product_id=it["product_id"],
            quantity=it["quantity"],
            price_at_time=it["price_at_time"],
            temperature=it.get("temperature"),
        )
        for it in seed_items
    ]
    db.session.add(order)
    db.session.commit()
    return jsonify(_order_json(order)), 201

