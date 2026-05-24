from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Category, Product
from app.utils.auth import login_required, role_required


categories_bp = Blueprint("categories", __name__, url_prefix="/categories")


def _category_json(c: Category) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "description": c.description,
        "created_at": c.created_at.isoformat(),
    }


@categories_bp.get("")
@login_required
def list_categories():
    categories = Category.query.order_by(Category.name.asc()).all()
    return jsonify([_category_json(c) for c in categories])


@categories_bp.post("")
@login_required
@role_required("manager")
def create_category():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    description = data.get("description")
    if not name:
        return jsonify({"error": "name_required"}), 400
    if description is not None and not isinstance(description, str):
        return jsonify({"error": "description_invalid"}), 400
    description = (description or "").strip() or None

    c = Category(name=name, description=description)
    db.session.add(c)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "category_already_exists"}), 409

    return jsonify(_category_json(c)), 201


@categories_bp.put("/<int:category_id>")
@login_required
@role_required("manager")
def update_category(category_id: int):
    c = Category.query.get(category_id)
    if not c:
        return jsonify({"error": "category_not_found"}), 404

    data = request.get_json(silent=True) or {}
    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name_required"}), 400
        c.name = name

    if "description" in data:
        description = data.get("description")
        if description is not None and not isinstance(description, str):
            return jsonify({"error": "description_invalid"}), 400
        c.description = (description or "").strip() or None

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "category_already_exists"}), 409

    return jsonify(_category_json(c))


@categories_bp.delete("/<int:category_id>")
@login_required
@role_required("manager")
def delete_category(category_id: int):
    c = Category.query.get(category_id)
    if not c:
        return jsonify({"error": "category_not_found"}), 404

    in_use = Product.query.filter_by(category_id=c.id).first() is not None
    if in_use:
        return jsonify({"error": "category_in_use"}), 400

    db.session.delete(c)
    db.session.commit()
    return jsonify({"ok": True})

