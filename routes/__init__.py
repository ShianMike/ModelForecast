"""
Routes package — registers all Flask blueprints.
"""
from .meta import bp as meta_bp
from .forecast_routes import bp as forecast_bp
from .artifacts import bp as artifacts_bp

ALL_BLUEPRINTS = [
    meta_bp,
    forecast_bp,
    artifacts_bp,
]


def register_all(app):
    """Register every blueprint on the Flask app."""
    for bp in ALL_BLUEPRINTS:
        app.register_blueprint(bp)
