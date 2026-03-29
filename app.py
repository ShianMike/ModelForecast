"""
Flask API for the Model Forecast Viewer.
Slim entry point — all route logic lives in the routes/ package.
"""

import os
import re

from flask import Flask, send_from_directory, request, abort
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman

import routes

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend", "dist")

# ─── Allowed origins ───────────────────────────────────────
ALLOWED_ORIGINS = [
    "https://shianmike.github.io",
]
if os.environ.get("FLASK_DEBUG") or os.environ.get("FLASK_ENV") == "development" or not os.environ.get("K_SERVICE"):
    ALLOWED_ORIGINS += [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://localhost:5001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:3002",
        "http://127.0.0.1:5001",
    ]

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")

# ─── CORS ──────────────────────────────────────────────────
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}}, supports_credentials=False)

# ─── Rate limiting ─────────────────────────────────────────
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per minute", "30 per second"],
    storage_uri="memory://",
)

# ─── Security headers via Talisman ─────────────────────────
_is_production = bool(os.environ.get("K_SERVICE"))

csp = {
    "default-src": "'self'",
    "script-src":  "'self' 'unsafe-inline'",
    "style-src":   "'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com",
    "font-src":    "'self' https://fonts.gstatic.com data:",
    "img-src":     "'self' data: blob: https://*.basemaps.cartocdn.com https://*.tile.openstreetmap.org "
                   "https://server.arcgisonline.com https://tilecache.rainviewer.com",
    "connect-src": "'self' https://api.open-meteo.com https://archive-api.open-meteo.com "
                   "https://api.rainviewer.com https://*.run.app",
    "media-src":   "'self' blob:",
    "frame-ancestors": "'none'",
    "base-uri":    "'self'",
    "form-action": "'self'",
    "object-src":  "'none'",
}

Talisman(
    app,
    force_https=_is_production,
    force_https_permanent=False,
    strict_transport_security=True,
    strict_transport_security_max_age=63072000,
    strict_transport_security_include_subdomains=True,
    strict_transport_security_preload=True,
    content_security_policy=csp,
    content_security_policy_nonce_in=["script-src"],
    referrer_policy="strict-origin-when-cross-origin",
    frame_options="DENY",
    permissions_policy={
        "geolocation":     "()",
        "camera":          "()",
        "microphone":      "()",
        "payment":         "()",
    },
    session_cookie_secure=_is_production,
    session_cookie_http_only=True,
    session_cookie_samesite="Lax",
)


@app.after_request
def add_extra_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024


@app.before_request
def block_path_traversal():
    if ".." in request.path or re.search(r"[<>\"';\x00]", request.path):
        abort(400)


# Register all API blueprints
routes.register_all(app)


# ─── SPA catch-all ─────────────────────────────────────────
@app.route("/ModelForecast/", defaults={"path": ""})
@app.route("/ModelForecast/<path:path>")
def serve_spa_prefixed(path):
    file_path = os.path.join(FRONTEND_DIR, path)
    if path and os.path.isfile(file_path):
        return send_from_directory(FRONTEND_DIR, path)
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_spa(path):
    file_path = os.path.join(FRONTEND_DIR, path)
    if path and os.path.isfile(file_path):
        return send_from_directory(FRONTEND_DIR, path)
    return send_from_directory(FRONTEND_DIR, "index.html")


if __name__ == "__main__":
    print("Starting Model Forecast API on http://localhost:5001")
    app.run(debug=True, port=5001)
