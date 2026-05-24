from __future__ import annotations

import os

from dotenv import load_dotenv
from flask import Flask, jsonify
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.tenant import clear_tenant_binding
from config import DevelopmentConfig, ProductionConfig, apply_database_config


def create_app(env: str | None = None) -> Flask:
    load_dotenv()

    flask_app = Flask(__name__, instance_relative_config=True)

    env = (env or os.getenv("APP_ENV") or "development").lower()
    if env in {"prod", "production"}:
        flask_app.config.from_object(ProductionConfig)
    else:
        flask_app.config.from_object(DevelopmentConfig)

    apply_database_config(flask_app)
    db.init_app(flask_app)

    with flask_app.app_context():
        import app.models  # noqa: F401

        db.create_all(bind_key="registry")

    from app.commands import register_cli
    from app.routes import api_bp
    from app.routes.web import web_bp

    register_cli(flask_app)
    flask_app.register_blueprint(api_bp, url_prefix="/api")
    flask_app.register_blueprint(web_bp)

    @flask_app.teardown_appcontext
    def _teardown_tenant_session(_exc=None):
        clear_tenant_binding()

    @flask_app.errorhandler(OperationalError)
    def handle_db_unavailable(err):
        if flask_app.config.get("DEBUG"):
            return jsonify({"error": "database_unavailable", "details": str(err)}), 503
        return jsonify({"error": "database_unavailable"}), 503

    @flask_app.get("/")
    def index():
        return jsonify(
            {
                "name": "POS_system API",
                "health": "/api/health",
                "multi_tenant": True,
            }
        )

    @flask_app.get("/favicon.ico")
    def favicon():
        return ("", 204)

    return flask_app
