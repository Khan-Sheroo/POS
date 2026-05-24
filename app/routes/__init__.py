from flask import Blueprint

api_bp = Blueprint("api", __name__)

from .auth import auth_bp  # noqa: E402
from .categories import categories_bp  # noqa: E402
from .health import health_bp  # noqa: E402
from .products import products_bp  # noqa: E402
from .temperature_groups import temperature_groups_bp  # noqa: E402
from .reports import reports_bp  # noqa: E402
from .sales import sales_bp  # noqa: E402
from .staff import staff_bp  # noqa: E402
from .settings import settings_bp  # noqa: E402
from .tables import tables_bp  # noqa: E402
from .cashups import cashups_bp  # noqa: E402
from .accounts import accounts_bp  # noqa: E402

api_bp.register_blueprint(auth_bp)
api_bp.register_blueprint(categories_bp)
api_bp.register_blueprint(health_bp)
api_bp.register_blueprint(products_bp)
api_bp.register_blueprint(temperature_groups_bp)
api_bp.register_blueprint(reports_bp)
api_bp.register_blueprint(sales_bp)
api_bp.register_blueprint(staff_bp)
api_bp.register_blueprint(settings_bp)
api_bp.register_blueprint(tables_bp)
api_bp.register_blueprint(cashups_bp)
api_bp.register_blueprint(accounts_bp)

