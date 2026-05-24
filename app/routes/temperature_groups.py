from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Product, TemperatureGroup, TemperatureOption
from app.utils.auth import login_required, role_required


temperature_groups_bp = Blueprint("temperature_groups", __name__, url_prefix="/temperature-groups")


def _group_json(group: TemperatureGroup) -> dict:
    return {
        "id": group.id,
        "name": group.name,
        "options": [
            {"id": o.id, "label": o.label, "sort_order": o.sort_order}
            for o in sorted(group.options or [], key=lambda x: (x.sort_order, x.id))
        ],
        "created_at": group.created_at.isoformat(),
    }


def _parse_option_labels(raw) -> list[str] | None:
    if raw is None:
        return []
    if not isinstance(raw, list):
        return None
    labels: list[str] = []
    for item in raw:
        if isinstance(item, str):
            label = item.strip()
        elif isinstance(item, dict):
            label = str(item.get("label") or "").strip()
        else:
            return None
        if label:
            labels.append(label)
    return labels


@temperature_groups_bp.get("")
@login_required
def list_temperature_groups():
    groups = TemperatureGroup.query.order_by(TemperatureGroup.name.asc()).all()
    return jsonify([_group_json(g) for g in groups])


@temperature_groups_bp.post("")
@login_required
@role_required("manager")
def create_temperature_group():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name_required"}), 400
    labels = _parse_option_labels(data.get("options"))
    if labels is None:
        return jsonify({"error": "options_invalid"}), 400
    if not labels:
        return jsonify({"error": "options_required"}), 400

    group = TemperatureGroup(name=name)
    db.session.add(group)
    db.session.flush()
    for idx, label in enumerate(labels):
        db.session.add(TemperatureOption(group_id=group.id, label=label, sort_order=idx))

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "name_already_exists"}), 409

    return jsonify(_group_json(group)), 201


@temperature_groups_bp.put("/<int:group_id>")
@login_required
@role_required("manager")
def update_temperature_group(group_id: int):
    group = TemperatureGroup.query.get(group_id)
    if not group:
        return jsonify({"error": "not_found"}), 404

    data = request.get_json(silent=True) or {}
    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name_required"}), 400
        group.name = name

    if "options" in data:
        labels = _parse_option_labels(data.get("options"))
        if labels is None:
            return jsonify({"error": "options_invalid"}), 400
        if not labels:
            return jsonify({"error": "options_required"}), 400
        group.options = []
        db.session.flush()
        for idx, label in enumerate(labels):
            db.session.add(TemperatureOption(group_id=group.id, label=label, sort_order=idx))

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "name_already_exists"}), 409

    return jsonify(_group_json(group))


@temperature_groups_bp.delete("/<int:group_id>")
@login_required
@role_required("manager")
def delete_temperature_group(group_id: int):
    group = TemperatureGroup.query.get(group_id)
    if not group:
        return jsonify({"error": "not_found"}), 404

    Product.query.filter_by(temperature_group_id=group_id).update({"temperature_group_id": None})
    db.session.delete(group)
    db.session.commit()
    return ("", 204)
