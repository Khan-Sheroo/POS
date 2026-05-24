from __future__ import annotations

import os

from dotenv import load_dotenv
from flask import Flask, jsonify
from sqlalchemy.exc import OperationalError

from app.extensions import db
from config import DevelopmentConfig, ProductionConfig


def create_app(env: str | None = None) -> Flask:
    load_dotenv()

    app = Flask(__name__)

    env = (env or os.getenv("APP_ENV") or "development").lower()
    if env in {"prod", "production"}:
        app.config.from_object(ProductionConfig)
    else:
        app.config.from_object(DevelopmentConfig)

    db.init_app(app)

    from app.cli import register_cli
    from app.routes import api_bp
    from app.routes.web import web_bp

    register_cli(app)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(web_bp)

    @app.errorhandler(OperationalError)
    def handle_db_unavailable(err):
        # Helpful details in development; keep production response minimal.
        if app.config.get("DEBUG"):
            return jsonify({"error": "database_unavailable", "details": str(err)}), 503
        return jsonify({"error": "database_unavailable"}), 503

    @app.get("/")
    def index():
        return jsonify(
            {
                "name": "POS_system API",
                "health": "/api/health",
            }
        )

    @app.get("/favicon.ico")
    def favicon():
        return ("", 204)

    return app

