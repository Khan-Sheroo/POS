from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import Category, Product, TemperatureGroup
from app.utils.auth import login_required, role_required
from app.utils.serialization import money_to_str


products_bp = Blueprint("products", __name__, url_prefix="/products")


def _parse_decimal(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (int, float, str)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    return None


def _temperature_group_json(group: TemperatureGroup | None) -> dict | None:
    if group is None:
        return None
    return {
        "id": group.id,
        "name": group.name,
        "options": [
            {"id": o.id, "label": o.label, "sort_order": o.sort_order}
            for o in sorted(group.options or [], key=lambda x: (x.sort_order, x.id))
        ],
    }


def _resolve_temperature_group_id(raw) -> tuple[int | None, tuple[dict, int] | None]:
    if raw is None:
        return None, None
    if not isinstance(raw, int) or raw <= 0:
        return None, ({"error": "temperature_group_id_invalid"}, 400)
    group = TemperatureGroup.query.get(raw)
    if not group:
        return None, ({"error": "temperature_group_not_found"}, 400)
    return raw, None


def _product_json(product: Product) -> dict:
    return {
        "id": product.id,
        "name": product.name,
        "price": money_to_str(product.price),
        "cost_price": money_to_str(getattr(product, "cost_price", None)),
        "sku": product.sku,
        "category": (
            {"id": product.category.id, "name": product.category.name} if product.category is not None else None
        ),
        "temperature_group": _temperature_group_json(getattr(product, "temperature_group", None)),
        "temperature_group_id": getattr(product, "temperature_group_id", None),
        "created_at": product.created_at.isoformat(),
    }


@products_bp.get("")
@login_required
def list_products():
    products = (
        Product.query.options(
            joinedload(Product.temperature_group).joinedload(TemperatureGroup.options),
            joinedload(Product.category),
        )
        .order_by(Product.id.desc())
        .all()
    )
    return jsonify([_product_json(p) for p in products])


@products_bp.post("")
@login_required
@role_required("manager")
def create_product():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    price = _parse_decimal(data.get("price"))
    cost_price = _parse_decimal(data.get("cost_price"))
    sku = (data.get("sku") or "").strip() or None
    category_id = data.get("category_id")

    if not name:
        return jsonify({"error": "name_required"}), 400
    if price is None:
        return jsonify({"error": "price_required"}), 400
    if price <= 0:
        return jsonify({"error": "price_must_be_positive"}), 400
    if cost_price is not None and cost_price < 0:
        return jsonify({"error": "cost_price_must_be_non_negative"}), 400

    category = None
    if category_id is not None:
        if not isinstance(category_id, int) or category_id <= 0:
            return jsonify({"error": "category_id_invalid"}), 400
        category = Category.query.get(category_id)
        if not category:
            return jsonify({"error": "category_not_found"}), 400

    temp_group_id, err = _resolve_temperature_group_id(data.get("temperature_group_id"))
    if err:
        return jsonify(err[0]), err[1]

    product = Product(
        name=name,
        price=price,
        cost_price=cost_price,
        sku=sku,
        category=category,
        temperature_group_id=temp_group_id,
    )
    db.session.add(product)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "sku_already_exists"}), 409

    return jsonify(_product_json(product)), 201


@products_bp.put("/<int:product_id>")
@login_required
@role_required("manager")
def update_product(product_id: int):
    product = Product.query.get(product_id)
    if not product:
        return jsonify({"error": "not_found"}), 404

    data = request.get_json(silent=True) or {}

    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name_required"}), 400
        product.name = name

    if "price" in data:
        price = _parse_decimal(data.get("price"))
        if price is None:
            return jsonify({"error": "price_invalid"}), 400
        if price <= 0:
            return jsonify({"error": "price_must_be_positive"}), 400
        product.price = price

    if "cost_price" in data:
        cp = _parse_decimal(data.get("cost_price"))
        if cp is None and data.get("cost_price") is not None:
            return jsonify({"error": "cost_price_invalid"}), 400
        if cp is not None and cp < 0:
            return jsonify({"error": "cost_price_must_be_non_negative"}), 400
        product.cost_price = cp

    if "sku" in data:
        sku = (data.get("sku") or "").strip() or None
        product.sku = sku

    if "category_id" in data:
        category_id = data.get("category_id")
        if category_id is None:
            product.category = None
        else:
            if not isinstance(category_id, int) or category_id <= 0:
                return jsonify({"error": "category_id_invalid"}), 400
            category = Category.query.get(category_id)
            if not category:
                return jsonify({"error": "category_not_found"}), 400
            product.category = category

    if "temperature_group_id" in data:
        temp_group_id, err = _resolve_temperature_group_id(data.get("temperature_group_id"))
        if err:
            return jsonify(err[0]), err[1]
        product.temperature_group_id = temp_group_id

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "sku_already_exists"}), 409

    return jsonify(_product_json(product))


@products_bp.delete("/<int:product_id>")
@login_required
@role_required("manager")
def delete_product(product_id: int):
    product = Product.query.get(product_id)
    if not product:
        return jsonify({"error": "not_found"}), 404

    db.session.delete(product)
    db.session.commit()
    return ("", 204)
