"""
Artifact inventory routes.

These endpoints expose a read-only view of the precomputed forecast artifacts
stored under ``forecast_artifacts/``. They are intended for dashboards and
production smoke tests so missing or stale coverage is obvious without
manually inspecting the filesystem.
"""

from flask import Blueprint, jsonify

from forecast import artifact_status

bp = Blueprint("artifacts", __name__)


@bp.route("/api/artifacts/status", methods=["GET"])
def artifacts_status():
    """Return a structured status payload for all tracked artifact sets.

    The default coverage focuses on HRRR CONUS severe composites because they
    are the first artifacts wired into the production Render deployment.
    """
    payload = artifact_status.build_status()
    return jsonify(payload)
