"""
NOAA NOMADS GRIB filter client for NCEP models.

Downloads geographic-subset GRIB2 files via the NOMADS filter,
decodes with a built-in pure-Python GRIB2 reader (no C libraries),
and returns data in the same format as open_meteo.py.

No API key required. No rate limits.
Docs: https://nomads.ncep.noaa.gov/
"""

import time
import threading
import numpy as np
import requests
from datetime import datetime, timedelta, timezone

from forecast.grib2 import decode_grib2
from forecast.grid_utils import crop_and_thin_grid

_session = requests.Session()
_session.headers["User-Agent"] = "ModelForecastViewer/1.0"


# ─── Model configurations ─────────────────────────────────

NOMADS_MODELS = {
    "gfs": {
        "filter_url": "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl",
        "dir_pattern": "/gfs.{date}/{cycle:02d}/atmos",
        "file_pattern": "gfs.t{cycle:02d}z.pgrb2.0p25.f{fhour:03d}",
        "cycles": [0, 6, 12, 18],
        "publish_delay": 5,  # hours after cycle before data is reliably available
    },
    "nam": {
        "filter_url": "https://nomads.ncep.noaa.gov/cgi-bin/filter_nam.pl",
        "dir_pattern": "/nam.{date}",
        "file_pattern": "nam.t{cycle:02d}z.awphys{fhour:02d}.tm00.grib2",
        "cycles": [0, 6, 12, 18],
        "publish_delay": 2,
    },
    "hrrr": {
        "filter_url": "https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl",
        "dir_pattern": "/hrrr.{date}/conus",
        "file_pattern": "hrrr.t{cycle:02d}z.wrfsfcf{fhour:02d}.grib2",
        "cycles": list(range(24)),
        "publish_delay": 1,
    },
    "rap": {
        "filter_url": "https://nomads.ncep.noaa.gov/cgi-bin/filter_rap.pl",
        "dir_pattern": "/rap.{date}",
        "file_pattern": "rap.t{cycle:02d}z.awp130pgrbf{fhour:02d}.grib2",
        "cycles": list(range(24)),
        "publish_delay": 1,
    },
}


# ─── Variable mapping ─────────────────────────────────────
# grib_params: NOMADS GRIB filter field names set to "on"
# wind: True if U/V components need to be combined into speed
# convert: unit conversion function (None = no conversion)

NOMADS_VARIABLE_MAP = {
    "temperature_2m": {
        "grib_params": ["var_TMP", "lev_2_m_above_ground"],
        "wind": False,
        "convert": lambda k: k * 9 / 5 - 459.67,
        "unit": "°F",
    },
    "dewpoint_2m": {
        "grib_params": ["var_DPT", "lev_2_m_above_ground"],
        "wind": False,
        "convert": lambda k: k * 9 / 5 - 459.67,
        "unit": "°F",
    },
    "wind_speed_10m": {
        "grib_params": ["var_UGRD", "var_VGRD", "lev_10_m_above_ground"],
        "wind": True,
        "convert": lambda ms: ms * 1.94384,
        "unit": "kt",
    },
    "wind_gusts_10m": {
        "grib_params": ["var_GUST", "lev_surface"],
        "wind": False,
        "convert": lambda ms: ms * 1.94384,
        "unit": "kt",
    },
    "surface_pressure": {
        "grib_params": ["var_PRMSL", "lev_mean_sea_level"],
        "wind": False,
        "convert": lambda pa: pa / 100,
        "unit": "hPa",
        "model_overrides": {
            "hrrr": ["var_MSLMA", "lev_mean_sea_level"],
            "rap": ["var_MSLMA", "lev_mean_sea_level"],
        },
    },
    "precipitation": {
        "grib_params": ["var_APCP", "lev_surface"],
        "wind": False,
        "convert": lambda kgm2: kgm2 / 25.4,
        "unit": "in",
    },
    "snowfall": {
        "grib_params": ["var_SNOD", "lev_surface"],
        "wind": False,
        "convert": lambda m: m * 39.3701,
        "unit": "in",
    },
    "cape": {
        "grib_params": ["var_CAPE", "lev_surface"],
        "wind": False,
        "convert": None,
        "unit": "J/kg",
    },
    "convective_inhibition": {
        "grib_params": ["var_CIN", "lev_surface"],
        "wind": False,
        "convert": None,
        "unit": "J/kg",
    },
    "cloud_cover": {
        "grib_params": ["var_TCDC", "lev_entire_atmosphere"],
        "wind": False,
        "convert": None,
        "unit": "%",
        "model_overrides": {
            "nam": ["var_TCDC", "lev_entire_atmosphere_%28considered_as_a_single_layer%29"],
        },
    },
    "simulated_reflectivity": {
        "grib_params": ["var_REFC", "lev_entire_atmosphere"],
        "wind": False,
        "convert": None,
        "unit": "dBZ",
    },
    "visibility": {
        "grib_params": ["var_VIS", "lev_surface"],
        "wind": False,
        "convert": None,
        "unit": "m",
    },
    "relative_humidity_2m": {
        "grib_params": ["var_RH", "lev_2_m_above_ground"],
        "wind": False,
        "convert": None,
        "unit": "%",
    },
    "shortwave_radiation": {
        "grib_params": ["var_DSWRF", "lev_surface"],
        "wind": False,
        "convert": None,
        "unit": "W/m²",
    },
    "geopotential_height_500hPa": {
        "grib_params": ["var_HGT", "lev_500_mb"],
        "wind": False,
        "convert": lambda gpm: gpm / 10,
        "unit": "dam",
    },
    "temperature_850hPa": {
        "grib_params": ["var_TMP", "lev_850_mb"],
        "wind": False,
        "convert": lambda k: k - 273.15,
        "unit": "°C",
    },
    "wind_speed_250hPa": {
        "grib_params": ["var_UGRD", "var_VGRD", "lev_250_mb"],
        "wind": True,
        "convert": lambda ms: ms * 1.94384,
        "unit": "kt",
    },
    "wind_speed_500hPa": {
        "grib_params": ["var_UGRD", "var_VGRD", "lev_500_mb"],
        "wind": True,
        "convert": lambda ms: ms * 1.94384,
        "unit": "kt",
    },
    "wind_speed_850hPa": {
        "grib_params": ["var_UGRD", "var_VGRD", "lev_850_mb"],
        "wind": True,
        "convert": lambda ms: ms * 1.94384,
        "unit": "kt",
    },
}

# Per-model variable support
NOMADS_SUPPORTED_VARS = {
    "gfs": set(NOMADS_VARIABLE_MAP.keys()),
    "nam": set(NOMADS_VARIABLE_MAP.keys()) - {
        "simulated_reflectivity",  # Not consistently available on NAM endpoint
    },
    "rap": set(NOMADS_VARIABLE_MAP.keys()) - {
        "shortwave_radiation",  # DSWRF not available on RAP
    },
    "hrrr": set(NOMADS_VARIABLE_MAP.keys()) - {
        "wind_speed_250hPa",  # 2D product has no 250mb
    },
}

DEFAULT_BBOX = {
    "lat_min": 24.0, "lat_max": 50.0,
    "lon_min": -125.0, "lon_max": -66.0,
}


# ─── Cache ─────────────────────────────────────────────────

_cache = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 1800  # 30 min — model runs don't change

_latest_run_cache = {}
_latest_run_lock = threading.Lock()
_LATEST_RUN_TTL = 600


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
        _cache[key] = {"data": data, "ts": now}


# ─── Public helpers ────────────────────────────────────────

def is_nomads_model(model):
    """Check if a model should use NOMADS."""
    return model.lower() in NOMADS_MODELS


def get_supported_variables(model):
    """Return set of variable keys supported by a NOMADS model."""
    return NOMADS_SUPPORTED_VARS.get(model.lower(), set())


# ─── Latest run detection ─────────────────────────────────

def _find_latest_run(model="gfs"):
    """Determine the latest available model run → (date_str, cycle_int)."""
    with _latest_run_lock:
        cached = _latest_run_cache.get(model)
        if cached and (time.time() - cached["ts"]) < _LATEST_RUN_TTL:
            return cached["date"], cached["cycle"]

    config = NOMADS_MODELS[model]
    now = datetime.now(timezone.utc)
    available_time = now - timedelta(hours=config["publish_delay"])

    cycles = sorted(config["cycles"])
    run_cycle = cycles[0]
    run_date = available_time

    for c in cycles:
        if c <= available_time.hour:
            run_cycle = c
        else:
            break

    # If current hour - delay is before the first cycle, use yesterday's last
    if available_time.hour < cycles[0]:
        run_date = available_time - timedelta(days=1)
        run_cycle = cycles[-1]

    date_str = run_date.strftime("%Y%m%d")

    with _latest_run_lock:
        _latest_run_cache[model] = {
            "date": date_str, "cycle": run_cycle, "ts": time.time()
        }

    return date_str, run_cycle


def _iter_run_candidates(model, max_candidates=6):
    """Yield latest run followed by earlier valid cycles for backtracking."""
    latest_date, latest_cycle = _find_latest_run(model)
    cycles = set(NOMADS_MODELS[model]["cycles"])

    cursor = datetime.strptime(f"{latest_date}{latest_cycle:02d}", "%Y%m%d%H").replace(
        tzinfo=timezone.utc
    )
    seen = set()
    out = []

    while len(out) < max_candidates:
        key = (cursor.strftime("%Y%m%d"), cursor.hour)
        if cursor.hour in cycles and key not in seen:
            out.append(key)
            seen.add(key)
        cursor -= timedelta(hours=1)

    return out


# ─── GRIB filter URL builder ──────────────────────────────

def _build_filter_url(model, variable, forecast_hour, bbox, run_date=None, run_cycle=None):
    """Construct the full NOMADS GRIB filter URL."""
    config = NOMADS_MODELS[model]
    var_info = NOMADS_VARIABLE_MAP[variable]
    if run_date is None or run_cycle is None:
        run_date, run_cycle = _find_latest_run(model)

    parts = [
        config["filter_url"],
        "?dir=", config["dir_pattern"].format(date=run_date, cycle=run_cycle),
        "&file=", config["file_pattern"].format(cycle=run_cycle, fhour=forecast_hour),
    ]

    overrides = var_info.get("model_overrides", {})
    grib_params = overrides.get(model, var_info["grib_params"])
    for param in grib_params:
        parts.append(f"&{param}=on")

    parts.extend([
        "&subregion=",
        f"&toplat={bbox['lat_max']}",
        f"&bottomlat={bbox['lat_min']}",
        f"&leftlon={bbox['lon_min']}",
        f"&rightlon={bbox['lon_max']}",
    ])

    return "".join(parts), run_date, run_cycle


# ─── GRIB2 download + decode ──────────────────────────────

def _download_grib(url, retries=2):
    """Download a GRIB2 subset from NOMADS with retry on transient errors.

    Retries up to *retries* times on HTTP 429, 500, 502, 503, 504 before
    giving up.  HTTP 403 and 404 are raised immediately (no retry).
    """
    _TRANSIENT_CODES = {429, 500, 502, 503, 504}
    last_exc = None
    for attempt in range(1 + retries):
        try:
            resp = _session.get(url, timeout=30)
            if resp.status_code == 404:
                raise FileNotFoundError("NOMADS data not yet available for this run/hour")
            if resp.status_code == 403:
                raise PermissionError(f"NOMADS returned 403 Forbidden for {url}")
            if resp.status_code in _TRANSIENT_CODES and attempt < retries:
                continue  # retry
            resp.raise_for_status()
            if len(resp.content) < 50 or resp.content[:4] != b"GRIB":
                raise ValueError("Invalid GRIB response from NOMADS")
            return resp.content
        except (FileNotFoundError, PermissionError):
            raise
        except Exception as exc:
            last_exc = exc
            if attempt >= retries:
                raise
    raise last_exc  # pragma: no cover


def _decode_and_combine(grib_bytes, is_wind=False):
    """Decode GRIB2 bytes → (lats_1d, lons_1d, values_2d).
    For wind: combines u/v messages into speed.
    """
    messages = decode_grib2(grib_bytes)
    if not messages:
        raise ValueError("No decodable messages in GRIB data")

    if is_wind:
        # category=2, param=2 → UGRD; param=3 → VGRD
        u_vals = v_vals = lats = lons = None
        for msg in messages:
            if lats is None:
                lats, lons = msg["lats"], msg["lons"]
            if msg["category"] == 2 and msg["parameter"] == 2:
                u_vals = msg["values"]
            elif msg["category"] == 2 and msg["parameter"] == 3:
                v_vals = msg["values"]
        if u_vals is not None and v_vals is not None:
            speed = np.sqrt(u_vals**2 + v_vals**2)
            return lats, lons, speed, u_vals, v_vals
        raise ValueError("Could not find wind U/V components in GRIB")
    else:
        msg = messages[0]
        return msg["lats"], msg["lons"], msg["values"], None, None


# ─── Public API ────────────────────────────────────────────

def fetch_grid_forecast(model, variable, forecast_hour, bbox=None):
    """
    Fetch a 2D gridded forecast from NOMADS.
    Returns the same dict shape as open_meteo.fetch_grid_forecast():
        {model, variable, forecast_hour, lats, lons, values (2D), unit, ...}
    """
    if bbox is None:
        bbox = DEFAULT_BBOX

    model_key = model.lower()
    if model_key not in NOMADS_MODELS:
        raise ValueError(f"Model '{model}' is not available on NOMADS")
    if variable not in NOMADS_VARIABLE_MAP:
        raise ValueError(f"Variable '{variable}' is not available on NOMADS")

    cache_key = (
        f"nomads:{model_key}:{variable}:{forecast_hour}"
        f":{bbox['lat_min']:.1f}:{bbox['lat_max']:.1f}"
        f":{bbox['lon_min']:.1f}:{bbox['lon_max']:.1f}"
    )
    cached = _cache_get(cache_key)
    if cached:
        return cached

    var_info = NOMADS_VARIABLE_MAP[variable]

    # Accumulated fields (APCP, SNOD) don't exist at fhour=0 — return zeros
    _ACCUM_VARS = {"precipitation", "snowfall"}
    if variable in _ACCUM_VARS and forecast_hour == 0:
        run_date, run_cycle = _find_latest_run(model_key)
        # Use a neighbouring variable to get grid dimensions
        ref_url, _, _ = _build_filter_url(
            model_key, "temperature_2m", 0, bbox
        )
        ref_bytes = _download_grib(ref_url)
        ref_lats, ref_lons, ref_vals, _, _ = _decode_and_combine(ref_bytes)
        assert ref_lats is not None and ref_lons is not None
        ref_lats, ref_lons, ref_vals, _, _ = crop_and_thin_grid(
            ref_lats, ref_lons, ref_vals, bbox=bbox
        )
        cycle_dt = datetime.strptime(f"{run_date}{run_cycle:02d}", "%Y%m%d%H")
        result = {
            "model": model_key,
            "variable": variable,
            "forecast_hour": 0,
            "lats": [round(float(x), 4) for x in ref_lats],
            "lons": [round(float(x), 4) for x in ref_lons],
            "values": [[0.0] * len(ref_lons) for _ in range(len(ref_lats))],
            "unit": var_info["unit"],
            "valid_time": cycle_dt.strftime("%Y-%m-%d %H:%M UTC"),
            "run": f"{run_date}/{run_cycle:02d}z",
        }
        _cache_set(cache_key, result)
        return result

    lats = lons = raw_values = u_raw = v_raw = None
    run_date = run_cycle = None
    last_missing = None

    for cand_date, cand_cycle in _iter_run_candidates(model_key, max_candidates=6):
        try:
            url, run_date, run_cycle = _build_filter_url(
                model_key,
                variable,
                forecast_hour,
                bbox,
                run_date=cand_date,
                run_cycle=cand_cycle,
            )
            grib_bytes = _download_grib(url)
            lats, lons, raw_values, u_raw, v_raw = _decode_and_combine(
                grib_bytes, is_wind=var_info["wind"]
            )
            break
        except FileNotFoundError as exc:
            last_missing = exc
            continue

    if lats is None or lons is None or raw_values is None or run_date is None or run_cycle is None:
        if last_missing is not None:
            raise last_missing
        raise RuntimeError(
            f"NOMADS data unavailable for {model_key}/{variable} at F{forecast_hour:03d}"
        )

    assert lats is not None and lons is not None

    # Convert longitude 0-360 → -180..180
    if np.any(lons > 180):
        lons = lons.copy()
        lons[lons > 180] -= 360
        sort_idx = np.argsort(lons)
        lons = lons[sort_idx]
        raw_values = raw_values[:, sort_idx]
        if u_raw is not None and v_raw is not None:
            u_raw = u_raw[:, sort_idx]
            v_raw = v_raw[:, sort_idx]

    # Ensure latitudes ascending
    if len(lats) > 1 and lats[0] > lats[-1]:
        lats = lats[::-1]
        raw_values = raw_values[::-1, :]
        if u_raw is not None and v_raw is not None:
            u_raw = u_raw[::-1, :]
            v_raw = v_raw[::-1, :]

    lats, lons, raw_values, u_raw, v_raw = crop_and_thin_grid(
        lats, lons, raw_values, bbox=bbox, u_component=u_raw, v_component=v_raw
    )

    # Unit conversion (speed only — keep u/v in m/s for direction)
    convert = var_info["convert"]
    if convert is not None:
        values = convert(raw_values.astype(np.float64))
    else:
        values = raw_values.astype(np.float64)

    # Valid time label
    cycle_dt = datetime.strptime(f"{run_date}{run_cycle:02d}", "%Y%m%d%H")
    valid_dt = cycle_dt + timedelta(hours=forecast_hour)
    valid_time = valid_dt.strftime("%Y-%m-%d %H:%M UTC")

    result = {
        "model": model_key,
        "variable": variable,
        "forecast_hour": forecast_hour,
        "lats": [round(float(x), 4) for x in lats],
        "lons": [round(float(x), 4) for x in lons],
        "values": [
            [float(v) if np.isfinite(v) else None for v in row]
            for row in values
        ],
        "unit": var_info["unit"],
        "valid_time": valid_time,
        "run": f"{run_date}/{run_cycle:02d}z",
    }

    # Include U/V components for wind direction arrows
    if u_raw is not None and v_raw is not None:
        result["u_component"] = [
            [float(v) if np.isfinite(v) else None for v in row]
            for row in u_raw
        ]
        result["v_component"] = [
            [float(v) if np.isfinite(v) else None for v in row]
            for row in v_raw
        ]

    _cache_set(cache_key, result)
    return result


def fetch_point_forecast(model, lat, lon, variables=None):
    """
    Fetch time-series at a point.  Delegates to Open-Meteo for efficiency
    (NOMADS would need one GRIB file per forecast hour — too many requests).
    """
    from forecast.open_meteo import fetch_point_forecast as om_point
    return om_point(model, lat, lon, variables)
