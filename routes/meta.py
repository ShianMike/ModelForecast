"""
Meta routes: health check, available models and parameters.
"""
from functools import lru_cache
from io import BytesIO

from flask import Blueprint, Response, jsonify, request
from PIL import Image, ImageDraw, ImageFont

bp = Blueprint("meta", __name__)

# Available forecast models
MODELS = {
    "gfs":   {"name": "GFS",   "source": "nomads",    "maxHour": 384, "step": 3,  "resolution": "0.25°"},
    "hrrr":  {"name": "HRRR",  "source": "nomads",    "maxHour": 48,  "step": 1,  "resolution": "3 km"},
    "nam":   {"name": "NAM",   "source": "nomads",    "maxHour": 84,  "step": 3,  "resolution": "12 km"},
    "rap":   {"name": "RAP",   "source": "nomads",    "maxHour": 51,  "step": 1,  "resolution": "13 km"},
    "ecmwf_ifs":    {"name": "ECMWF IFS",    "source": "ecmwf",  "maxHour": 240, "step": 3, "resolution": "0.25°"},
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
            "thickness_1000_500":         {"name": "1000‑500mb Thickness", "unit": "dam", "cmap": "thickness", "derived": True},
        },
    },
    "severe": {
        "label": "Severe Weather",
        "params": {
            "cape":                  {"name": "SB CAPE",          "unit": "J/kg", "cmap": "cape"},
            "convective_inhibition": {"name": "CIN",              "unit": "J/kg", "cmap": "cin"},
            "wind_gusts_10m":        {"name": "Wind Gusts",       "unit": "kt",   "cmap": "wind"},
            "simulated_reflectivity": {"name": "Simulated Reflectivity (SimRef)", "unit": "dBZ", "cmap": "reflectivity"},
            "visibility":            {"name": "Visibility",       "unit": "m",    "cmap": "wind"},
            "effective_bulk_shear":  {"name": "Eff. Bulk Shear",  "unit": "kt",   "cmap": "shear", "derived": True},
        },
    },
    "severe_combo": {
        "label": "Severe Composites",
        "params": {
            "stp_approx":              {"name": "STP (Sig Tornado)",        "unit": "",  "cmap": "stp",               "derived": True},
            "scp":                     {"name": "SCP (Supercell)",          "unit": "",  "cmap": "scp",               "derived": True},
            "ship":                    {"name": "SHIP (Sig Hail)",          "unit": "",  "cmap": "ship",              "derived": True},
            "critical_angle_composite": {"name": "Tornado Composite",      "unit": "",  "cmap": "tornado_composite", "derived": True},
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
    from forecast.ecmwf import is_ecmwf_model, get_supported_variables as ecmwf_vars
    from routes.forecast_routes import COMPOSITE_PARAMS

    if is_nomads_model(model):
        supported = nomads_vars(model)
    elif is_ecmwf_model(model):
        supported = ecmwf_vars(model)
    else:
        supported = om_vars(model)

    def _is_available(key, meta):
        """Check if a parameter is available for the current model."""
        if key in supported:
            return True
        if meta.get("derived") and key in COMPOSITE_PARAMS:
            # Only include composites whose components are all supported
            comp = COMPOSITE_PARAMS[key]
            return all(
                c in supported or c == "temperature_500hPa_raw"
                for c in comp["components"]
            )
        return False

    filtered = {}
    for cat_key, cat in PARAMETER_CATEGORIES.items():
        params = {k: v for k, v in cat["params"].items() if _is_available(k, v)}
        if params:
            filtered[cat_key] = {"label": cat["label"], "params": params}
    return jsonify(filtered)


def _load_font(size, bold=False):
    """Load a cross-platform truetype font with a safe default fallback."""
    candidates = [
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "Arial Bold.ttf" if bold else "Arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


@lru_cache(maxsize=1)
def _render_social_preview_png():
    """Render a static social preview card used by Open Graph and Twitter tags."""
    width, height = 1200, 630
    image = Image.new("RGB", (width, height), "#06121e")
    draw = ImageDraw.Draw(image, "RGBA")

    # Background glow and panels to mimic meteorological overlays.
    draw.ellipse((-220, -200, 720, 620), fill=(16, 53, 89, 180))
    draw.ellipse((700, -150, 1450, 540), fill=(12, 70, 74, 160))
    draw.rectangle((0, 510, width, 630), fill=(4, 12, 20, 220))

    card_left, card_top = 60, 56
    card_right, card_bottom = width - 60, height - 72
    draw.rounded_rectangle(
        (card_left, card_top, card_right, card_bottom),
        radius=28,
        fill=(8, 18, 30, 210),
        outline=(66, 146, 203, 180),
        width=2,
    )

    # Stylized "forecast swath" shapes.
    draw.polygon(
        [(620, 170), (980, 140), (1080, 260), (860, 380), (640, 340), (560, 230)],
        fill=(157, 214, 113, 145),
    )
    draw.polygon(
        [(735, 230), (925, 205), (970, 280), (845, 335), (720, 310), (688, 260)],
        fill=(236, 230, 127, 165),
    )

    title_font = _load_font(64, bold=True)
    subtitle_font = _load_font(33, bold=False)
    chip_font = _load_font(28, bold=True)

    draw.text((110, 120), "Model Forecast", font=title_font, fill=(224, 242, 255, 255))
    draw.text(
        (110, 208),
        "Interactive Weather Model Viewer",
        font=subtitle_font,
        fill=(134, 199, 255, 255),
    )
    draw.text(
        (110, 266),
        "Soundings, cross-sections, meteograms, composites, and animation",
        font=subtitle_font,
        fill=(180, 218, 248, 255),
    )

    chip_specs = [
        ("GFS", "#2f73bf"),
        ("HRRR", "#0f7f72"),
        ("NAM", "#8d6339"),
        ("RAP", "#5648a2"),
    ]
    x = 112
    y = 360
    for label, color in chip_specs:
        text_w = draw.textlength(label, font=chip_font)
        chip_w = int(text_w + 52)
        draw.rounded_rectangle((x, y, x + chip_w, y + 58), radius=22, fill=color)
        draw.text((x + 26, y + 14), label, font=chip_font, fill=(240, 247, 255, 255))
        x += chip_w + 16

    draw.text((110, 535), "modelforecastpy.app", font=_load_font(30, bold=False), fill=(121, 205, 240, 255))

    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


@bp.route("/og-image.png", methods=["GET"])
def social_preview_image():
    """Serve a generated OG image used by social embeds for shared links."""
    response = Response(_render_social_preview_png(), mimetype="image/png")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response
