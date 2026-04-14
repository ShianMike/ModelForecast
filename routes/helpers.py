"""
Shared helpers used by multiple route modules.
"""
import math

from flask import jsonify


_MISSING = object()


class QueryValidationError(ValueError):
    """Raised when a client query parameter is missing or invalid."""

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def json_error(message, status_code):
    return jsonify({"error": message}), status_code


def _coerce_float(raw_value, name):
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise QueryValidationError(f"Query parameter '{name}' must be a valid number.") from exc
    if not math.isfinite(value):
        raise QueryValidationError(f"Query parameter '{name}' must be finite.")
    return value


def _coerce_int(raw_value, name):
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise QueryValidationError(f"Query parameter '{name}' must be a valid integer.") from exc
    return value


def parse_float_arg(args, name, *, default=_MISSING, minimum=None, maximum=None):
    raw_value = args.get(name)
    if raw_value in (None, ""):
        if default is _MISSING:
            raise QueryValidationError(f"Missing required query parameter '{name}'.")
        value = default
    else:
        value = _coerce_float(raw_value, name)

    if minimum is not None and value < minimum:
        raise QueryValidationError(f"Query parameter '{name}' must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise QueryValidationError(f"Query parameter '{name}' must be at most {maximum}.")
    return value


def parse_int_arg(args, name, *, default=_MISSING, minimum=None, maximum=None, clamp_max=False):
    raw_value = args.get(name)
    if raw_value in (None, ""):
        if default is _MISSING:
            raise QueryValidationError(f"Missing required query parameter '{name}'.")
        value = default
    else:
        value = _coerce_int(raw_value, name)

    if minimum is not None and value < minimum:
        raise QueryValidationError(f"Query parameter '{name}' must be at least {minimum}.")
    if maximum is not None and value > maximum:
        if clamp_max:
            value = maximum
        else:
            raise QueryValidationError(f"Query parameter '{name}' must be at most {maximum}.")
    return value


def parse_lat_lon(args, lat_name="lat", lon_name="lon"):
    lat = parse_float_arg(args, lat_name, minimum=-90.0, maximum=90.0)
    lon = parse_float_arg(args, lon_name, minimum=-180.0, maximum=180.0)
    return lat, lon


def parse_bbox(args):
    keys = ("lat_min", "lat_max", "lon_min", "lon_max")
    present = [key in args for key in keys]
    if any(present) and not all(present):
        raise QueryValidationError(
            "Bounding box requires lat_min, lat_max, lon_min, and lon_max."
        )
    if not any(present):
        return None

    lat_min = parse_float_arg(args, "lat_min", minimum=-90.0, maximum=90.0)
    lat_max = parse_float_arg(args, "lat_max", minimum=-90.0, maximum=90.0)
    lon_min = parse_float_arg(args, "lon_min", minimum=-180.0, maximum=180.0)
    lon_max = parse_float_arg(args, "lon_max", minimum=-180.0, maximum=180.0)

    if lat_min == lat_max or lon_min == lon_max:
        raise QueryValidationError(
            "Bounding box must span a non-zero latitude and longitude range."
        )

    return {
        "lat_min": min(lat_min, lat_max),
        "lat_max": max(lat_min, lat_max),
        "lon_min": min(lon_min, lon_max),
        "lon_max": max(lon_min, lon_max),
    }


def _nan_safe(obj):
    """Recursively replace NaN/Inf float values with None for JSON safety."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _nan_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_nan_safe(item) for item in obj]
    return obj
