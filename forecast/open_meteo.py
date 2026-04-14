"""
Open-Meteo API client for gridded forecast data.

Fetches model forecast grids via batched multi-coordinate requests,
caches all forecast hours so subsequent fhour lookups are instant.
Docs: https://open-meteo.com/en/docs
"""

import random
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import requests

_session = requests.Session()
_session.headers["User-Agent"] = "ModelForecastViewer/1.0"


class RateLimitError(Exception):
    """Raised when Open-Meteo returns 429."""
    pass


def _request_with_retry(url, params=None, timeout=15, max_retries=3):
    """GET with automatic retry + exponential backoff on transient errors.
    Raises RateLimitError immediately on 429 (no retry — it won't clear for ~1h).
    """
    last_exc = None
    resp = None
    for attempt in range(max_retries):
        try:
            resp = _session.get(url, params=params, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt == max_retries - 1:
                raise
            time.sleep((2 ** attempt) + random.uniform(0.0, 0.5))
            continue
        if resp.status_code == 429:
            raise RateLimitError("Weather API rate limited. Please wait a moment and try again.")
        if resp.status_code >= 500:
            if attempt == max_retries - 1:
                return resp
            time.sleep((2 ** attempt) + random.uniform(0.0, 0.5))
            continue
        return resp
    if last_exc is not None:
        raise last_exc
    return resp  # return last response even if still failing

# ─── Model → Open-Meteo API endpoint mapping ──────────────
MODEL_ENDPOINTS = {
    "gfs":   "https://api.open-meteo.com/v1/gfs",
    "hrrr":  "https://api.open-meteo.com/v1/gfs",
    "ecmwf": "https://api.open-meteo.com/v1/ecmwf",
    "icon":  "https://api.open-meteo.com/v1/dwd-icon",
    "nam":   "https://api.open-meteo.com/v1/gfs",
    "rap":   "https://api.open-meteo.com/v1/gfs",
    "jma":   "https://api.open-meteo.com/v1/jma",
    "gem":   "https://api.open-meteo.com/v1/gem",
}

# ─── Open-Meteo variable name mapping ─────────────────────
VARIABLE_MAP = {
    "temperature_2m":              "temperature_2m",
    "dewpoint_2m":                 "dew_point_2m",
    "wind_speed_10m":              "wind_speed_10m",
    "wind_gusts_10m":              "wind_gusts_10m",
    "surface_pressure":            "pressure_msl",
    "precipitation":               "precipitation",
    "snowfall":                    "snowfall",
    "cape":                        "cape",
    "convective_inhibition":       "convective_inhibition",
    "cloud_cover":                 "cloud_cover",
    "visibility":                  "visibility",
    "relative_humidity_2m":        "relative_humidity_2m",
    "shortwave_radiation":         "shortwave_radiation",
    "geopotential_height_500hPa":  "geopotential_height_500hPa",
    "temperature_850hPa":          "temperature_850hPa",
    "wind_speed_250hPa":           "wind_speed_250hPa",
    "wind_speed_500hPa":           "wind_speed_500hPa",
    "wind_speed_850hPa":           "wind_speed_850hPa",
}

# Pressure-level variables need special handling
PRESSURE_LEVEL_VARS = {
    "geopotential_height_500hPa":  {"var": "geopotential_height", "level": 500},
    "temperature_850hPa":          {"var": "temperature",         "level": 850},
    "wind_speed_250hPa":           {"var": "wind_speed",          "level": 250},
    "wind_speed_500hPa":           {"var": "wind_speed",          "level": 500},
    "wind_speed_850hPa":           {"var": "wind_speed",          "level": 850},
}

# ─── Per-model variable availability (from Open-Meteo docs) ───
# All models listed here share the GFS/HRRR endpoint use the same
# variable set as gfs for this mapping.
_COMMON_VARS = {
    "temperature_2m", "dewpoint_2m", "wind_speed_10m", "surface_pressure",
    "precipitation", "snowfall", "cloud_cover", "shortwave_radiation",
    "relative_humidity_2m",
    # pressure-level vars available on all models
    "geopotential_height_500hPa", "temperature_850hPa",
    "wind_speed_250hPa", "wind_speed_500hPa", "wind_speed_850hPa",
}

MODEL_VARIABLE_SUPPORT = {
    "gfs":   _COMMON_VARS | {"wind_gusts_10m", "cape", "convective_inhibition", "visibility"},
    "hrrr":  _COMMON_VARS | {"wind_gusts_10m", "cape", "convective_inhibition", "visibility"},
    "ecmwf": _COMMON_VARS | {"wind_gusts_10m", "cape", "convective_inhibition", "visibility"},
    "icon":  _COMMON_VARS | {"wind_gusts_10m", "cape", "visibility"},
    "nam":   _COMMON_VARS | {"wind_gusts_10m", "cape", "convective_inhibition", "visibility"},
    "rap":   _COMMON_VARS | {"wind_gusts_10m", "cape", "convective_inhibition", "visibility"},
    "jma":   _COMMON_VARS,  # no gusts, cape, cin, or visibility
    "gem":   _COMMON_VARS | {"wind_gusts_10m"},  # has gusts but no cape/cin/visibility
}


def get_supported_variables(model: str) -> set:
    """Return set of variable keys supported by a given model."""
    return MODEL_VARIABLE_SUPPORT.get(model, MODEL_VARIABLE_SUPPORT["gfs"])

# Default bounding box (CONUS)
DEFAULT_BBOX = {
    "lat_min": 24.0,
    "lat_max": 50.0,
    "lon_min": -125.0,
    "lon_max": -66.0,
}

# ─── Server-side cache ────────────────────────────────────
_cache = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 1800  # 30 minutes

MAX_TOTAL_POINTS = 300   # target grid size
BATCH_SIZE = 80          # coordinates per API call


def _cache_get(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
            return entry["data"]
        if entry:
            del _cache[key]
    return None


def _cache_set(key, data):
    with _cache_lock:
        now = time.time()
        if len(_cache) > 200:
            expired = [k for k, v in _cache.items() if (now - v["ts"]) > _CACHE_TTL]
            for k in expired:
                del _cache[k]
            # If still over limit, drop oldest entries
            if len(_cache) > 200:
                oldest = sorted(_cache, key=lambda k: _cache[k]["ts"])[:50]
                for k in oldest:
                    del _cache[k]
        _cache[key] = {"data": data, "ts": now}


def _compute_grid(bbox):
    """Build a lat/lon grid targeting ~MAX_TOTAL_POINTS total cells."""
    lat_range = bbox["lat_max"] - bbox["lat_min"]
    lon_range = abs(bbox["lon_max"] - bbox["lon_min"])

    if lat_range <= 0 or lon_range <= 0:
        return [round(bbox["lat_min"], 2)], [round(bbox["lon_min"], 2)]

    aspect = lon_range / lat_range
    n_lat = int(np.sqrt(MAX_TOTAL_POINTS / max(aspect, 0.1)))
    n_lon = int(n_lat * aspect)
    n_lat = max(3, min(30, n_lat))
    n_lon = max(3, min(50, n_lon))

    lats = np.linspace(bbox["lat_min"], bbox["lat_max"], n_lat)
    lons = np.linspace(bbox["lon_min"], bbox["lon_max"], n_lon)
    return [round(float(x), 2) for x in lats], [round(float(x), 2) for x in lons]


def _resolve_variable(variable):
    """Map our variable name to the Open-Meteo hourly parameter."""
    if variable in PRESSURE_LEVEL_VARS:
        pl = PRESSURE_LEVEL_VARS[variable]
        return f"{pl['var']}_{pl['level']}hPa"
    return VARIABLE_MAP.get(variable, variable)


# ─── Batch multi-coordinate fetch ─────────────────────────

def _fetch_batch(endpoint, lat_list, lon_list, hourly_var):
    """Fetch forecast for multiple coordinates in ONE API call."""
    lat_str = ",".join(str(v) for v in lat_list)
    lon_str = ",".join(str(v) for v in lon_list)
    url = (
        f"{endpoint}?latitude={lat_str}&longitude={lon_str}"
        f"&hourly={hourly_var}"
        f"&temperature_unit=fahrenheit&wind_speed_unit=kn&precipitation_unit=inch"
    )
    resp = _request_with_retry(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    # Multi-location → array; single location → object
    return data if isinstance(data, list) else [data]


def _fetch_parallel_fallback(endpoint, pairs, hourly_var):
    """Fallback: fetch individual points via thread pool."""
    results = {}
    unit = ""

    def _one(lat, lon):
        try:
            resp = _request_with_retry(endpoint, params={
                "latitude": lat, "longitude": lon,
                "hourly": hourly_var,
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "kn",
                "precipitation_unit": "inch",
            }, timeout=10)
            if resp.status_code == 200:
                d = resp.json()
                vals = d.get("hourly", {}).get(hourly_var, [])
                u = d.get("hourly_units", {}).get(hourly_var, "")
                return vals, u
        except Exception:
            pass
        return [], ""

    with ThreadPoolExecutor(max_workers=20) as pool:
        futs = {pool.submit(_one, lat, lon): (lat, lon) for lat, lon in pairs}
        for f in as_completed(futs):
            coord = futs[f]
            try:
                vals, u = f.result()
                results[coord] = vals
                if not unit and u:
                    unit = u
            except Exception:
                results[coord] = []

    return results, unit


# ─── Public API ───────────────────────────────────────────

def fetch_grid_forecast(model, variable, forecast_hour, bbox=None):
    """
    Fetch a 2-D gridded forecast.

    First call: batched multi-coordinate requests, caches ALL forecast hours.
    Subsequent calls (different fhour, same model/var/bbox): instant from cache.
    """
    if forecast_hour < 0:
        raise ValueError("Forecast hour must be greater than or equal to 0.")
    if bbox is None:
        bbox = DEFAULT_BBOX

    model_key = model.lower()
    endpoint = MODEL_ENDPOINTS.get(model_key)
    if not endpoint:
        raise ValueError(f"Unknown model: {model}")

    cache_key = (
        f"grid:{model_key}:{variable}"
        f":{bbox['lat_min']:.1f}:{bbox['lat_max']:.1f}"
        f":{bbox['lon_min']:.1f}:{bbox['lon_max']:.1f}"
    )

    cached = _cache_get(cache_key)
    if cached:
        return _extract_hour(cached, model_key, variable, forecast_hour)

    # ── Cache miss — fetch all hours ──────────────────────
    hourly_var = _resolve_variable(variable)
    lats, lons = _compute_grid(bbox)
    pairs = [(lat, lon) for lat in lats for lon in lons]

    results = {}   # (lat, lon) → list of hourly values
    unit = ""

    try:
        for i in range(0, len(pairs), BATCH_SIZE):
            batch = pairs[i:i + BATCH_SIZE]
            blats = [p[0] for p in batch]
            blons = [p[1] for p in batch]
            data_list = _fetch_batch(endpoint, blats, blons, hourly_var)
            for j, item in enumerate(data_list):
                if j < len(batch):
                    hourly = item.get("hourly", {})
                    results[batch[j]] = hourly.get(hourly_var, [])
                    if not unit:
                        unit = item.get("hourly_units", {}).get(hourly_var, "")
            # Small delay between batches to avoid rate limits
            if i + BATCH_SIZE < len(pairs):
                time.sleep(0.3)
    except RateLimitError:
        raise  # bubble up immediately — don't waste time on fallback
    except Exception:
        results, unit = _fetch_parallel_fallback(endpoint, pairs, hourly_var)

    # Assemble full hourly grid (all hours stored)
    hourly_grid = [
        [results.get((lat, lon), []) for lon in lons]
        for lat in lats
    ]

    payload = {"lats": lats, "lons": lons, "hourly_grid": hourly_grid, "unit": unit}
    _cache_set(cache_key, payload)

    return _extract_hour(payload, model_key, variable, forecast_hour)


def _extract_hour(payload, model_key, variable, forecast_hour):
    """Pull a single forecast hour from the cached all-hours grid."""
    if forecast_hour < 0:
        raise ValueError("Forecast hour must be greater than or equal to 0.")
    lats = payload["lats"]
    lons = payload["lons"]
    hourly_grid = payload["hourly_grid"]
    unit = payload["unit"]

    values_2d = []
    for lat_idx in range(len(lats)):
        row = []
        for lon_idx in range(len(lons)):
            h = hourly_grid[lat_idx][lon_idx]
            if h and forecast_hour < len(h) and h[forecast_hour] is not None:
                row.append(float(h[forecast_hour]))
            else:
                row.append(None)
        values_2d.append(row)

    return {
        "model": model_key,
        "variable": variable,
        "forecast_hour": forecast_hour,
        "lats": lats,
        "lons": lons,
        "values": values_2d,
        "unit": unit,
    }


def fetch_point_forecast(model, lat, lon, variables=None):
    """
    Fetch a full time-series forecast at a single point (for meteograms).
    """
    model_key = model.lower()
    endpoint = MODEL_ENDPOINTS.get(model_key)
    if not endpoint:
        raise ValueError(f"Unknown model: {model}")

    # Check cache
    pt_key = f"pt:{model_key}:{lat:.2f}:{lon:.2f}"
    cached = _cache_get(pt_key)
    if cached:
        return cached

    if variables is None:
        variables = [
            "temperature_2m", "dew_point_2m",
            "wind_speed_10m", "wind_gusts_10m",
            "precipitation", "cape",
            "cloud_cover",
        ]

    hourly_vars = ",".join(variables)

    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "hourly": hourly_vars,
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "kn",
        "precipitation_unit": "inch",
    }

    resp = _request_with_retry(endpoint, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    hourly = data.get("hourly", {})
    units = data.get("hourly_units", {})

    result = {
        "model": model_key,
        "lat": lat,
        "lon": lon,
        "times": hourly.get("time", []),
        "variables": {k: v for k, v in hourly.items() if k != "time"},
        "units": units,
    }
    _cache_set(pt_key, result)
    return result
