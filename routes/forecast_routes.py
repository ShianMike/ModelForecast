"""
Forecast API routes — gridded data and point forecasts.
Routes NOMADS models (GFS, NAM, RAP, HRRR) through NOAA NOMADS GRIB filter.
Remainder (ECMWF, ICON, JMA, GEM) falls through to Open-Meteo.
"""
from flask import Blueprint, jsonify, request

from forecast import nomads
from forecast import open_meteo
from forecast.open_meteo import RateLimitError
from forecast.parameters import get_color_scale
from routes.helpers import _nan_safe

bp = Blueprint("forecast", __name__)


def _get_supported(model):
    """Return supported variable set using the appropriate data source."""
    if nomads.is_nomads_model(model):
        return nomads.get_supported_variables(model)
    return open_meteo.get_supported_variables(model)


@bp.route("/api/forecast", methods=["GET"])
def get_forecast():
    """
    Fetch gridded forecast data for map rendering.

    Query params:
        model:    gfs, hrrr, ecmwf, icon, nam, rap, jma, gem
        variable: temperature_2m, cape, etc.
        fhour:    forecast hour (int)
        lat_min, lat_max, lon_min, lon_max: bounding box (optional)
    """
    model = request.args.get("model", "gfs")
    variable = request.args.get("variable", "temperature_2m")
    fhour = request.args.get("fhour", "0", type=int)

    bbox = None
    if all(k in request.args for k in ("lat_min", "lat_max", "lon_min", "lon_max")):
        bbox = {
            "lat_min": float(request.args["lat_min"]),
            "lat_max": float(request.args["lat_max"]),
            "lon_min": float(request.args["lon_min"]),
            "lon_max": float(request.args["lon_max"]),
        }

    supported = _get_supported(model)
    if variable not in supported:
        return jsonify({"error": f"Variable '{variable}' is not available for model '{model}'"}), 400

    try:
        if nomads.is_nomads_model(model):
            result = nomads.fetch_grid_forecast(model, variable, fhour, bbox)
        else:
            result = open_meteo.fetch_grid_forecast(model, variable, fhour, bbox)
        result = _nan_safe(result)
        return jsonify(result)
    except RateLimitError:
        return jsonify({"error": "Weather API rate limited. Please wait a moment and try again.", "retry_after": 60}), 429
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Failed to fetch forecast: {e}"}), 502


@bp.route("/api/color-scale", methods=["GET"])
def get_color_scale_route():
    """Return color scale definition for a given parameter."""
    cmap = request.args.get("cmap", "temperature")
    scale = get_color_scale(cmap)
    if scale is None:
        return jsonify({"error": f"Unknown color scale: {cmap}"}), 404
    return jsonify(scale)
