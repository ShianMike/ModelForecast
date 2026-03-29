"""
Meta routes: health check, available models and parameters.
"""
from flask import Blueprint, jsonify, request

bp = Blueprint("meta", __name__)

# Available forecast models
MODELS = {
    "gfs":   {"name": "GFS",   "source": "nomads",    "maxHour": 384, "step": 3,  "resolution": "0.25°"},
    "hrrr":  {"name": "HRRR",  "source": "nomads",    "maxHour": 48,  "step": 1,  "resolution": "3 km"},
    "nam":   {"name": "NAM",   "source": "nomads",    "maxHour": 84,  "step": 3,  "resolution": "12 km"},
    "rap":   {"name": "RAP",   "source": "nomads",    "maxHour": 51,  "step": 1,  "resolution": "13 km"},
}

# Parameter categories and definitions
PARAMETER_CATEGORIES = {
    "surface": {
        "label": "Surface & Near-Surface",
        "params": {
            "temperature_2m":     {"name": "2m Temperature",   "unit": "°F", "cmap": "temperature"},
            "dewpoint_2m":        {"name": "2m Dewpoint",      "unit": "°F", "cmap": "dewpoint"},
            "wind_speed_10m":     {"name": "10m Wind Speed",   "unit": "kt", "cmap": "wind"},
            "wind_gusts_10m":     {"name": "10m Wind Gusts",   "unit": "kt", "cmap": "wind"},
            "surface_pressure":   {"name": "MSLP",             "unit": "hPa", "cmap": "pressure"},
        },
    },
    "precip": {
        "label": "Precipitation & Moisture",
        "params": {
            "precipitation":      {"name": "Total QPF",        "unit": "in", "cmap": "precip"},
            "snowfall":           {"name": "Snowfall",         "unit": "in", "cmap": "snow"},
            "cape":               {"name": "CAPE",             "unit": "J/kg", "cmap": "cape"},
            "cloud_cover":        {"name": "Cloud Cover",      "unit": "%",    "cmap": "precip"},
        },
    },
    "upper": {
        "label": "Upper Air",
        "params": {
            "geopotential_height_500hPa": {"name": "500mb Heights", "unit": "dam", "cmap": "heights"},
            "temperature_850hPa":         {"name": "850mb Temp",    "unit": "°C",  "cmap": "temperature"},
            "wind_speed_250hPa":          {"name": "250mb Wind",    "unit": "kt",  "cmap": "jet"},
            "wind_speed_500hPa":          {"name": "500mb Wind",    "unit": "kt",  "cmap": "wind"},
            "wind_speed_850hPa":          {"name": "850mb Wind",    "unit": "kt",  "cmap": "wind"},
        },
    },
    "severe": {
        "label": "Severe Weather",
        "params": {
            "cape":                  {"name": "SB CAPE",          "unit": "J/kg", "cmap": "cape"},
            "convective_inhibition": {"name": "CIN",              "unit": "J/kg", "cmap": "cin"},
            "wind_gusts_10m":        {"name": "Wind Gusts",       "unit": "kt",   "cmap": "wind"},
            "visibility":            {"name": "Visibility",       "unit": "m",    "cmap": "wind"},
        },
    },
}


@bp.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@bp.route("/api/models", methods=["GET"])
def list_models():
    return jsonify(MODELS)


@bp.route("/api/parameters", methods=["GET"])
def list_parameters():
    model = request.args.get("model")
    if not model:
        return jsonify(PARAMETER_CATEGORIES)

    from forecast.nomads import is_nomads_model, get_supported_variables as nomads_vars
    from forecast.open_meteo import get_supported_variables as om_vars

    supported = nomads_vars(model) if is_nomads_model(model) else om_vars(model)

    filtered = {}
    for cat_key, cat in PARAMETER_CATEGORIES.items():
        params = {k: v for k, v in cat["params"].items() if k in supported}
        if params:
            filtered[cat_key] = {"label": cat["label"], "params": params}
    return jsonify(filtered)
