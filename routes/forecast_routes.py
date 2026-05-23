"""
Forecast API routes — gridded data and point forecasts.
Core forecast endpoints use NOAA GRIB sources (NOMADS first, AWS mirror second)
for GFS, NAM, RAP, and HRRR. Ensemble data remains on Open-Meteo.
"""
import base64
from io import BytesIO
import json
import math
import logging
import os
import re
import time
import threading
from collections import OrderedDict
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from flask import Blueprint, Response, jsonify, request
from PIL import Image, ImageDraw, ImageFont

from forecast import nomads
from forecast import aws_grib
from forecast import open_meteo
from forecast import ecmwf
from forecast import artifact_cache
from forecast import run_cache
from forecast.grid_utils import thin_grid_result
from forecast.open_meteo import RateLimitError
from forecast.parameters import get_color_scale
from routes.helpers import (
    QueryValidationError,
    _nan_safe,
    json_error,
    parse_bbox,
    parse_int_arg,
    parse_lat_lon,
)

log = logging.getLogger(__name__)

bp = Blueprint("forecast", __name__)

_COMPOSITE_MAX_MAP_CELLS = 18_000
_COMPOSITE_MAX_MAP_SIDE = 180
_PRODUCTION_ARTIFACT_ONLY_VARIABLES = {
    "effective_bulk_shear",
    "stp_approx",
    "scp",
    "ship",
    "critical_angle_composite",
}


# ─── Point endpoint cache (sounding) ───────────────────────
_POINT_CACHE = OrderedDict()
_POINT_CACHE_LOCK = threading.Lock()
_POINT_CACHE_TTL = 900  # seconds
_POINT_CACHE_MAX_SIZE = 500

_METEOGRAM_CACHE = OrderedDict()
_METEOGRAM_CACHE_LOCK = threading.Lock()
_METEOGRAM_CACHE_TTL = 1800  # seconds
_METEOGRAM_CACHE_MAX_SIZE = 500

_NOMADS_POINT_WORKERS = 8
_METEOGRAM_HOUR_CAP = 120

_POINT_MODEL_CONFIG = {
    "gfs": {"max_hour": 384, "step": 3},
    "hrrr": {"max_hour": 48, "step": 1},
    "nam": {"max_hour": 84, "step": 3},
    "rap": {"max_hour": 51, "step": 1},
    "ecmwf_ifs": {"max_hour": 240, "step": 3},
}

_MODEL_MAX_HOURS = {model: cfg["max_hour"] for model, cfg in _POINT_MODEL_CONFIG.items()}

_POINT_BBOX_HALF_SPAN = {
    "gfs": 0.40,
    "nam": 0.35,
    "rap": 0.25,
    "hrrr": 0.20,
    "ecmwf_ifs": 0.40,
}

_GRIB_SUPPORTED_MODELS = frozenset(_POINT_MODEL_CONFIG.keys())

# Sounding data step — HRRR has hourly upper-air data;
# GFS/NAM/RAP only have sounding data every 3 hours.
_SOUNDING_STEP = {
    "gfs": 3,
    "hrrr": 1,
    "nam": 3,
    "rap": 3,
    "ecmwf_ifs": 3,
}


def _snap_sounding_fhour(model, fhour):
    """Snap fhour to the nearest valid sounding hour for the model."""
    step = _SOUNDING_STEP.get(model.lower(), 3)
    if step <= 1:
        return fhour
    return round(fhour / step) * step

_METEOGRAM_GROUP_A_MAP = {
    (0, 0): "tmp_k",      # TMP
    (0, 6): "dpt_k",      # DPT
    (2, 2): "u10_ms",     # UGRD
    (2, 3): "v10_ms",     # VGRD
}

_METEOGRAM_GROUP_B_MAP = {
    (2, 22): "gust_ms",       # GUST
    (1, 8): "apcp_kgm2",      # APCP
    (7, 6): "cape",           # CAPE
    (6, 1): "cloud_cover",    # TCDC
}

_SOUNDING_MSG_MAP = {
    (0, 0): "tmp_k",      # TMP
    (1, 1): "rh",         # RH
    (2, 2): "u_ms",       # UGRD
    (2, 3): "v_ms",       # VGRD
    (3, 5): "hgt_m",      # HGT
}

_SOUNDING_LEVELS = [
    1000, 975, 950, 925, 900, 875, 850, 825, 800, 775, 750,
    700, 650, 600, 550, 500, 450, 400, 350, 300, 250, 200, 150,
]

_AWS_METEOGRAM_GROUP_A_PATTERNS = [
    r":TMP:2 m above ground:",
    r":DPT:2 m above ground:",
    r":UGRD:10 m above ground:",
    r":VGRD:10 m above ground:",
]

_AWS_METEOGRAM_GROUP_B_PATTERNS = [
    r":GUST:surface:",
    r":APCP:surface:",
    r":CAPE:surface:",
    r":TCDC:entire atmosphere:",
    r":TCDC:entire atmosphere \(considered as a single layer\):",
]

_ENSEMBLE_FALLBACK_ORDER = [
    "temperature_2m",
    "wind_speed_10m",
    "precipitation",
]

_CROSS_SECTION_VARIABLES = {
    "temperature": {
        "base": "temperature",
        "label": "Temperature",
        "unit": "°C",
        "request_params": {"temperature_unit": "celsius"},
    },
    "temperature_2m": {
        "base": "temperature",
        "label": "Temperature",
        "unit": "°C",
        "request_params": {"temperature_unit": "celsius"},
    },
    "temperature_850hPa": {
        "base": "temperature",
        "label": "Temperature",
        "unit": "°C",
        "request_params": {"temperature_unit": "celsius"},
    },
    "relative_humidity": {
        "base": "relative_humidity",
        "label": "Relative Humidity",
        "unit": "%",
        "request_params": {},
    },
    "relative_humidity_2m": {
        "base": "relative_humidity",
        "label": "Relative Humidity",
        "unit": "%",
        "request_params": {},
    },
    "wind_speed": {
        "base": "wind_speed",
        "label": "Wind Speed",
        "unit": "kt",
        "request_params": {"wind_speed_unit": "kn"},
    },
    "wind_speed_10m": {
        "base": "wind_speed",
        "label": "Wind Speed",
        "unit": "kt",
        "request_params": {"wind_speed_unit": "kn"},
    },
    "wind_speed_250hPa": {
        "base": "wind_speed",
        "label": "Wind Speed",
        "unit": "kt",
        "request_params": {"wind_speed_unit": "kn"},
    },
    "wind_speed_500hPa": {
        "base": "wind_speed",
        "label": "Wind Speed",
        "unit": "kt",
        "request_params": {"wind_speed_unit": "kn"},
    },
    "wind_speed_850hPa": {
        "base": "wind_speed",
        "label": "Wind Speed",
        "unit": "kt",
        "request_params": {"wind_speed_unit": "kn"},
    },
    "geopotential_height": {
        "base": "geopotential_height",
        "label": "Geopotential Height",
        "unit": "m",
        "request_params": {},
    },
    "geopotential_height_500hPa": {
        "base": "geopotential_height",
        "label": "Geopotential Height",
        "unit": "m",
        "request_params": {},
    },
}


def _aws_sounding_patterns(level):
    return [
        rf":TMP:{level} mb:",
        rf":RH:{level} mb:",
        rf":UGRD:{level} mb:",
        rf":VGRD:{level} mb:",
        rf":HGT:{level} mb:",
    ]


def _point_cache_get(key):
    return _ttl_lru_cache_get(_POINT_CACHE, _POINT_CACHE_LOCK, key, _POINT_CACHE_TTL)


def _point_cache_set(key, data):
    _ttl_lru_cache_set(
        _POINT_CACHE,
        _POINT_CACHE_LOCK,
        key,
        data,
        _POINT_CACHE_MAX_SIZE,
    )


def _meteogram_cache_get(key):
    return _ttl_lru_cache_get(
        _METEOGRAM_CACHE,
        _METEOGRAM_CACHE_LOCK,
        key,
        _METEOGRAM_CACHE_TTL,
    )


def _meteogram_cache_set(key, data):
    _ttl_lru_cache_set(
        _METEOGRAM_CACHE,
        _METEOGRAM_CACHE_LOCK,
        key,
        data,
        _METEOGRAM_CACHE_MAX_SIZE,
    )


def _ttl_lru_cache_get(cache, lock, key, ttl_seconds):
    """Return cache entry when fresh and mark as most-recently-used."""
    now = time.time()
    with lock:
        entry = cache.get(key)
        if not entry:
            return None
        if (now - entry["ts"]) >= ttl_seconds:
            del cache[key]
            return None
        cache.move_to_end(key)
        return entry["data"]


def _ttl_lru_cache_set(cache, lock, key, data, max_size):
    """Store cache entry and evict least-recently-used items above max_size."""
    with lock:
        cache[key] = {"data": data, "ts": time.time()}
        cache.move_to_end(key)
        while len(cache) > max_size:
            cache.popitem(last=False)


def _normalize_lon(lon):
    while lon > 180:
        lon -= 360
    while lon < -180:
        lon += 360
    return lon


def _point_bbox(model, lat, lon):
    half = _POINT_BBOX_HALF_SPAN.get(model.lower(), 0.35)
    return {
        "lat_min": max(-90.0, lat - half),
        "lat_max": min(90.0, lat + half),
        "lon_min": max(-180.0, lon - half),
        "lon_max": min(180.0, lon + half),
    }


def _parse_fhour_arg(model, arg_name="fhour", default=0):
    return parse_int_arg(
        request.args,
        arg_name,
        default=default,
        minimum=0,
        maximum=_MODEL_MAX_HOURS.get(model.lower()),
        clamp_max=True,
    )


def _snap_fhour_to_step(model, fhour):
    """Snap fhour down to the nearest valid forecast step for the model.

    Models like ECMWF IFS (step=6) and ICON (step=3) only have data at
    specific intervals.  Requesting an in-between hour (e.g. 13) would 404
    on the upstream server, so we round down to the nearest valid step.
    """
    step = _POINT_MODEL_CONFIG.get(model.lower(), {}).get("step", 1)
    if step <= 1:
        return fhour
    return (fhour // step) * step


def _require_grib_supported_model(model):
    model_key = model.lower()
    if model_key not in _GRIB_SUPPORTED_MODELS:
        raise QueryValidationError(
            f"Model '{model}' is not supported for this endpoint."
        )
    return model_key


def _resolve_cross_section_variable(variable):
    config = _CROSS_SECTION_VARIABLES.get(variable)
    if not config:
        raise QueryValidationError(
            f"Variable '{variable}' is not supported for cross-sections."
        )
    return config


def _relative_humidity_from_temp_dewpoint(temp_c, dewpoint_c):
    if temp_c is None or dewpoint_c is None:
        return None
    try:
        rh = 100.0 * (_es_hpa(dewpoint_c) / max(_es_hpa(temp_c), 1e-6))
    except Exception:
        return None
    return max(0.0, min(100.0, rh))


def _extract_cross_section_profile_value(level_row, var_base):
    if var_base == "temperature":
        return level_row.get("temperature")
    if var_base == "wind_speed":
        return level_row.get("wind_speed")
    if var_base == "geopotential_height":
        return level_row.get("height")
    if var_base == "relative_humidity":
        return _relative_humidity_from_temp_dewpoint(
            level_row.get("temperature"),
            level_row.get("dewpoint"),
        )
    return None


def _load_persistent_forecast_cache(model, variable, fhour, bbox):
    artifact_payload = artifact_cache.load_latest(model, variable, fhour, bbox)
    if artifact_payload is not None:
        return artifact_payload, artifact_payload.get("run")

    if not run_cache.is_enabled() or not run_cache.supports_model(model):
        return None, None

    candidate_run = run_cache.resolve_candidate_run(model)
    entry = run_cache.load_entry(model, variable, fhour, bbox)
    if run_cache.should_serve_entry(entry, candidate_run):
        return entry["payload"], candidate_run
    return None, candidate_run


def _store_persistent_forecast_cache(model, variable, fhour, bbox, payload, requested_run):
    if not run_cache.is_enabled() or not run_cache.supports_model(model):
        return
    run_cache.store_latest(model, variable, fhour, bbox, payload, requested_run=requested_run)


def _artifact_only_variables():
    raw_value = os.environ.get("FORECAST_ARTIFACT_ONLY_VARIABLES")
    if raw_value is None:
        if os.environ.get("APP_ENV", "").strip().lower() == "production":
            return set(_PRODUCTION_ARTIFACT_ONLY_VARIABLES)
        return set()

    raw = raw_value.strip()
    if not raw:
        return set()
    if raw.lower() in {"0", "false", "no", "off", "none"}:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _requires_forecast_artifact(variable):
    return variable in _artifact_only_variables()


def _resolve_region_name(bbox):
    """Map a bounding box back to a named artifact region when possible.

    Only the named regions used by the artifact generator are recognized
    so the response stays informative without inventing labels for
    arbitrary user-drawn bounding boxes.
    """
    if not bbox:
        return None
    try:
        from forecast.artifact_status import REGIONS as _ARTIFACT_REGIONS
    except Exception:
        return None

    def _approx_equal(a, b):
        try:
            return abs(float(a) - float(b)) < 1e-6
        except (TypeError, ValueError):
            return False

    for name, candidate in _ARTIFACT_REGIONS.items():
        if all(
            _approx_equal(bbox.get(key), candidate[key])
            for key in ("lat_min", "lat_max", "lon_min", "lon_max")
        ):
            return name
    return None


def _nearest_message_value(msg, lat, lon):
    try:
        lats = np.asarray(msg.get("lats", []), dtype=float)
        lons = np.asarray(msg.get("lons", []), dtype=float)
        vals = np.asarray(msg.get("values", []), dtype=float)
        if lats.size == 0 or lons.size == 0 or vals.ndim != 2:
            return None

        if np.any(lons > 180):
            lons = np.where(lons > 180, lons - 360, lons)

        target_lon = _normalize_lon(float(lon))
        ilat = int(np.argmin(np.abs(lats - float(lat))))
        ilon = int(np.argmin(np.abs(lons - target_lon)))

        if ilat < 0 or ilat >= vals.shape[0] or ilon < 0 or ilon >= vals.shape[1]:
            return None
        v = float(vals[ilat, ilon])
        return v if np.isfinite(v) else None
    except Exception:
        return None


def _build_nomads_custom_url(model, forecast_hour, grib_params, level_params, bbox):
    model_key = model.lower()
    config = nomads.NOMADS_MODELS[model_key]
    run_date, run_cycle = nomads._find_latest_run(model_key)

    parts = [
        config["filter_url"],
        "?dir=", config["dir_pattern"].format(date=run_date, cycle=run_cycle),
        "&file=", config["file_pattern"].format(cycle=run_cycle, fhour=forecast_hour),
    ]
    for p in grib_params:
        parts.append(f"&{p}=on")
    for p in level_params:
        parts.append(f"&{p}=on")

    parts.extend([
        "&subregion=",
        f"&toplat={bbox['lat_max']}",
        f"&bottomlat={bbox['lat_min']}",
        f"&leftlon={bbox['lon_min']}",
        f"&rightlon={bbox['lon_max']}",
    ])
    return "".join(parts), run_date, run_cycle


def _fetch_nomads_point_fields(model, lat, lon, forecast_hour, grib_params, level_params, field_map):
    bbox = _point_bbox(model, lat, lon)
    url, run_date, run_cycle = _build_nomads_custom_url(
        model, forecast_hour, grib_params, level_params, bbox
    )
    grib_bytes = nomads._download_grib(url)
    messages = nomads.decode_grib2(grib_bytes)

    out = {}
    for msg in messages:
        key = (msg.get("category", -1), msg.get("parameter", -1))
        field_name = field_map.get(key)
        if not field_name:
            continue
        if field_name in out and out[field_name] is not None:
            continue
        out[field_name] = _nearest_message_value(msg, lat, lon)

    return out, run_date, run_cycle


def _model_hour_sequence(model, cap_hours=_METEOGRAM_HOUR_CAP):
    cfg = _POINT_MODEL_CONFIG.get(model.lower(), {"max_hour": 120, "step": 1})
    max_hour = min(cfg["max_hour"], cap_hours)
    return list(range(0, max_hour + 1, cfg["step"]))


def _valid_time_iso(run_date, run_cycle, forecast_hour):
    cycle_dt = datetime.strptime(f"{run_date}{run_cycle:02d}", "%Y%m%d%H")
    valid_dt = cycle_dt + timedelta(hours=forecast_hour)
    return valid_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _f_or_none(v, digits=2):
    if v is None:
        return None
    try:
        f = float(v)
    except Exception:
        return None
    if not np.isfinite(f):
        return None
    return round(f, digits)


def _dewpoint_from_temp_rh(temp_c, rh):
    if temp_c is None or rh is None or rh <= 0:
        return None
    try:
        a, b = 17.27, 237.7
        alpha = (a * temp_c) / (b + temp_c) + math.log(max(rh, 1e-6) / 100.0)
        td = (b * alpha) / (a - alpha)
        return round(td, 1)
    except Exception:
        return None


def _wind_dir_from_uv(u_ms, v_ms):
    if u_ms is None or v_ms is None:
        return None
    return round((270.0 - math.degrees(math.atan2(v_ms, u_ms))) % 360.0, 1)


def _pressure_to_height_m(pressure_hpa):
    if pressure_hpa is None or pressure_hpa <= 0:
        return None
    return 44330.0 * (1.0 - (pressure_hpa / 1013.25) ** 0.1903)


# ─── Sounding analysis (thermodynamic indices) ─────────────
def _es_hpa(t_c):
    """Saturation vapour pressure (Bolton 1980), hPa."""
    return 6.112 * math.exp((17.67 * t_c) / (t_c + 243.5))


def _mixing_ratio(t_c, p_hpa):
    e = _es_hpa(t_c)
    return 0.622 * e / max(p_hpa - e, 0.1)


def _theta(t_c, p_hpa):
    """Potential temperature (K)."""
    return (t_c + 273.15) * (1000.0 / p_hpa) ** 0.286


def _theta_e(t_c, td_c, p_hpa):
    """Equivalent potential temperature (K), Bolton 1980 approximation."""
    tK = t_c + 273.15
    r = _mixing_ratio(td_c, p_hpa)
    t_lcl = 56.0 + 1.0 / (1.0 / (td_c + 273.15 - 56.0) + math.log(tK / (td_c + 273.15)) / 800.0)
    return tK * (1000.0 / p_hpa) ** (0.2854 * (1.0 - 0.28 * r)) * math.exp(
        r * (1.0 + 0.81 * r) * (3376.0 / t_lcl - 2.54)
    )


def _t_from_theta_e(th_e, p_hpa):
    """Temperature on a moist adiabat at pressure p (Newton iteration)."""
    t = -40.0
    for _ in range(40):
        te = _theta_e(t, t, p_hpa)
        dt = (th_e - te) * 0.3
        t += max(-10, min(10, dt))
        if abs(dt) < 0.02:
            break
    return t


def _lcl_pressure(t_c, td_c, p_hpa):
    """Estimate LCL pressure by dry-adiabatic ascent until T == Td."""
    w = _mixing_ratio(td_c, p_hpa)
    for p in range(int(p_hpa), int(max(P_TOP_ANALYSIS, 100)) - 1, -5):
        t_dry = (t_c + 273.15) * (p / p_hpa) ** 0.286 - 273.15
        e = (w * p) / (0.622 + w)
        td_at_p = (243.5 * math.log(max(e, 1e-6) / 6.112)) / (17.67 - math.log(max(e, 1e-6) / 6.112))
        if t_dry <= td_at_p:
            return float(p)
    return None


P_TOP_ANALYSIS = 100  # hPa ceiling for integration


def _compute_sounding_analysis(profile):
    """Compute thermodynamic indices from a sounding profile list.

    Returns a dict with CAPE, CIN, LCL, LFC, EL, LI, K-Index, TT, PW, bulk shear.
    All values rounded; None if not computable.
    """
    if not profile or len(profile) < 3:
        return {}

    # Sort surface → top (descending pressure)
    prof = sorted(
        [lv for lv in profile if lv.get("pressure") is not None and lv.get("temperature") is not None],
        key=lambda lv: -lv["pressure"],
    )
    if len(prof) < 3:
        return {}

    # --- Surface parcel ---
    sfc = prof[0]
    t_sfc = sfc["temperature"]
    td_sfc = sfc.get("dewpoint")
    p_sfc = sfc["pressure"]
    if td_sfc is None:
        return {}

    # --- LCL ---
    lcl_p = _lcl_pressure(t_sfc, td_sfc, p_sfc)
    th_e_sfc = _theta_e(t_sfc, td_sfc, p_sfc)

    # --- Build virtual temperature profiles (env and parcel) ---
    cape = 0.0
    cin = 0.0
    lfc_p = None
    el_p = None
    prev_buoy = None

    for i in range(len(prof) - 1):
        p_lo = prof[i]["pressure"]
        p_hi = prof[i + 1]["pressure"]
        p_mid = (p_lo + p_hi) / 2.0

        # Environment temperature at mid-level (linear interp)
        t_env = (prof[i]["temperature"] + prof[i + 1]["temperature"]) / 2.0

        # Parcel temperature at mid-level
        if lcl_p is not None and p_mid > lcl_p:
            # Below LCL: dry adiabat
            t_parcel = (t_sfc + 273.15) * (p_mid / p_sfc) ** 0.286 - 273.15
        else:
            # Above LCL: moist adiabat
            t_parcel = _t_from_theta_e(th_e_sfc, p_mid)

        buoyancy = t_parcel - t_env

        # Integrate using Rd * dln(p)
        dp = math.log(p_lo) - math.log(p_hi)  # positive
        energy = 287.04 * buoyancy * dp / (t_env + 273.15) * (t_parcel + 273.15) / (t_env + 273.15)
        # Simplified: energy ≈ Rd * (Tv_parcel - Tv_env) * dln(p)
        energy = 287.04 * buoyancy * dp

        if buoyancy > 0:
            cape += energy
            if lfc_p is None and prev_buoy is not None and prev_buoy <= 0:
                lfc_p = p_mid
            el_p = p_mid
        else:
            if lfc_p is None:
                cin += energy  # energy is negative here

        prev_buoy = buoyancy

    # --- Lifted Index (T_env - T_parcel at 500 hPa) ---
    li = None
    t500_env = None
    for lv in prof:
        if lv["pressure"] == 500 and lv["temperature"] is not None:
            t500_env = lv["temperature"]
            break
    if t500_env is not None:
        t500_parcel = _t_from_theta_e(th_e_sfc, 500.0)
        li = round(t500_env - t500_parcel, 1)

    # --- K-Index = (T850 - T500) + Td850 - (T700 - Td700) ---
    ki = None
    t850 = td850 = t700 = td700 = t500 = None
    for lv in prof:
        p = lv["pressure"]
        if p == 850:
            t850 = lv["temperature"]
            td850 = lv.get("dewpoint")
        elif p == 700:
            t700 = lv["temperature"]
            td700 = lv.get("dewpoint")
        elif p == 500:
            t500 = lv["temperature"]
    if all(v is not None for v in (t850, td850, t700, td700, t500)):
        ki = round((t850 - t500) + td850 - (t700 - td700), 1)

    # --- Total Totals = VT + CT = (T850 - T500) + (Td850 - T500) ---
    tt = None
    if t850 is not None and td850 is not None and t500 is not None:
        tt = round((t850 - t500) + (td850 - t500), 1)

    # --- Precipitable Water (mm) by integrating mixing ratio ---
    pw = 0.0
    for i in range(len(prof) - 1):
        td_lo = prof[i].get("dewpoint")
        td_hi = prof[i + 1].get("dewpoint")
        p_lo = prof[i]["pressure"]
        p_hi = prof[i + 1]["pressure"]
        if td_lo is not None and td_hi is not None:
            w_lo = _mixing_ratio(td_lo, p_lo)
            w_hi = _mixing_ratio(td_hi, p_hi)
            w_avg = (w_lo + w_hi) / 2.0
            dp_pa = (p_lo - p_hi) * 100.0  # hPa → Pa
            pw += w_avg * dp_pa / 9.81  # kg/m²  ≈ mm
    pw = round(pw, 1)

    # --- 0-6 km bulk shear (kt) ---
    shear = None
    sfc_lv = prof[0]
    h_sfc = sfc_lv.get("height") or _pressure_to_height_m(sfc_lv["pressure"]) or 0
    ws_sfc = sfc_lv.get("wind_speed")
    wd_sfc = sfc_lv.get("wind_direction")
    # Find level closest to sfc + 6000 m
    target_h = h_sfc + 6000
    best_6km = None
    best_diff = 1e9
    for lv in prof:
        h = lv.get("height") or _pressure_to_height_m(lv["pressure"])
        if h is not None and lv.get("wind_speed") is not None and lv.get("wind_direction") is not None:
            diff = abs(h - target_h)
            if diff < best_diff:
                best_diff = diff
                best_6km = lv
    if ws_sfc is not None and wd_sfc is not None and best_6km is not None:
        u_sfc = -ws_sfc * math.sin(math.radians(wd_sfc))
        v_sfc = -ws_sfc * math.cos(math.radians(wd_sfc))
        ws6 = best_6km["wind_speed"]
        wd6 = best_6km["wind_direction"]
        u_6 = -ws6 * math.sin(math.radians(wd6))
        v_6 = -ws6 * math.cos(math.radians(wd6))
        shear = round(math.sqrt((u_6 - u_sfc) ** 2 + (v_6 - v_sfc) ** 2), 1)

    # --- LCL height AGL ---
    lcl_height = None
    if lcl_p is not None:
        lcl_h = _pressure_to_height_m(lcl_p)
        if lcl_h is not None:
            lcl_height = round(lcl_h - (h_sfc or 0), 0)

    return {
        "cape": round(max(cape, 0), 0) if cape is not None else None,
        "cin": round(min(cin, 0), 0) if cin is not None else None,
        "lcl_hpa": round(lcl_p, 0) if lcl_p else None,
        "lcl_agl_m": lcl_height,
        "lfc_hpa": round(lfc_p, 0) if lfc_p else None,
        "el_hpa": round(el_p, 0) if el_p else None,
        "lifted_index": li,
        "k_index": ki,
        "total_totals": tt,
        "pwat_mm": pw,
        "bulk_shear_0_6km_kt": shear,
    }


def _build_nomads_meteogram(model, lat, lon):
    hours = _model_hour_sequence(model)

    def fetch_hour(fhour):
        group_a, run_date, run_cycle = _fetch_nomads_point_fields(
            model,
            lat,
            lon,
            fhour,
            grib_params=["var_TMP", "var_DPT", "var_UGRD", "var_VGRD"],
            level_params=["lev_2_m_above_ground", "lev_10_m_above_ground"],
            field_map=_METEOGRAM_GROUP_A_MAP,
        )

        group_b_params = ["var_GUST", "var_APCP", "var_CAPE", "var_TCDC"]
        if fhour == 0:
            group_b_params = ["var_GUST", "var_CAPE", "var_TCDC"]

        group_b, _, _ = _fetch_nomads_point_fields(
            model,
            lat,
            lon,
            fhour,
            grib_params=group_b_params,
            level_params=[
                "lev_surface",
                "lev_entire_atmosphere",
                "lev_entire_atmosphere_%28considered_as_a_single_layer%29",
            ],
            field_map=_METEOGRAM_GROUP_B_MAP,
        )

        temp_f = None
        if group_a.get("tmp_k") is not None:
            temp_f = group_a["tmp_k"] * 9.0 / 5.0 - 459.67

        dew_f = None
        if group_a.get("dpt_k") is not None:
            dew_f = group_a["dpt_k"] * 9.0 / 5.0 - 459.67

        wind_kt = None
        if group_a.get("u10_ms") is not None and group_a.get("v10_ms") is not None:
            wind_kt = math.sqrt(group_a["u10_ms"] ** 2 + group_a["v10_ms"] ** 2) * 1.94384

        gust_kt = None
        if group_b.get("gust_ms") is not None:
            gust_kt = group_b["gust_ms"] * 1.94384

        apcp_in = 0.0 if fhour == 0 else None
        if group_b.get("apcp_kgm2") is not None:
            apcp_in = group_b["apcp_kgm2"] / 25.4

        return {
            "fhour": fhour,
            "time": _valid_time_iso(run_date, run_cycle, fhour),
            "temperature_2m": _f_or_none(temp_f, 2),
            "dew_point_2m": _f_or_none(dew_f, 2),
            "wind_speed_10m": _f_or_none(wind_kt, 2),
            "wind_gusts_10m": _f_or_none(gust_kt, 2),
            "cape": _f_or_none(group_b.get("cape"), 1),
            "cloud_cover": _f_or_none(group_b.get("cloud_cover"), 1),
            "apcp_accum_in": _f_or_none(apcp_in, 3),
        }

    rows = []
    workers = max(1, min(_NOMADS_POINT_WORKERS, len(hours)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fetch_hour, fh): fh for fh in hours}
        for fut in as_completed(futs):
            try:
                rows.append(fut.result())
            except Exception as e:
                log.debug("Skipping meteogram hour %s for %s: %s", futs[fut], model, e)

    if not rows:
        raise RuntimeError("No GRIB point data available for meteogram")

    rows.sort(key=lambda r: r["fhour"])

    precip_step = []
    prev_accum = None
    for row in rows:
        accum = row.get("apcp_accum_in")
        if accum is None:
            precip_step.append(None)
            continue
        if prev_accum is None:
            precip_step.append(max(0.0, accum))
        else:
            precip_step.append(max(0.0, accum - prev_accum))
        prev_accum = accum

    result = {
        "model": model,
        "lat": lat,
        "lon": lon,
        "times": [r["time"] for r in rows],
        "variables": {
            "temperature_2m": [r["temperature_2m"] for r in rows],
            "dew_point_2m": [r["dew_point_2m"] for r in rows],
            "wind_speed_10m": [r["wind_speed_10m"] for r in rows],
            "wind_gusts_10m": [r["wind_gusts_10m"] for r in rows],
            "precipitation": precip_step,
            "cape": [r["cape"] for r in rows],
            "cloud_cover": [r["cloud_cover"] for r in rows],
        },
        "units": {
            "temperature_2m": "degF",
            "dew_point_2m": "degF",
            "wind_speed_10m": "kt",
            "wind_gusts_10m": "kt",
            "precipitation": "in",
            "cape": "J/kg",
            "cloud_cover": "%",
        },
        "source_model": model,
        "source": "nomads_grib",
    }
    return _nan_safe(result)


def _build_nomads_sounding(model, lat, lon, fhour):
    def fetch_level(level):
        fields, run_date, run_cycle = _fetch_nomads_point_fields(
            model,
            lat,
            lon,
            fhour,
            grib_params=["var_TMP", "var_RH", "var_UGRD", "var_VGRD", "var_HGT"],
            level_params=[f"lev_{level}_mb"],
            field_map=_SOUNDING_MSG_MAP,
        )
        return level, fields, run_date, run_cycle

    rows = {}
    run_ref = None
    workers = max(1, min(_NOMADS_POINT_WORKERS, len(_SOUNDING_LEVELS)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fetch_level, lev): lev for lev in _SOUNDING_LEVELS}
        for fut in as_completed(futs):
            lev = futs[fut]
            try:
                level, fields, run_date, run_cycle = fut.result()
                rows[level] = fields
                if run_ref is None:
                    run_ref = (run_date, run_cycle)
            except Exception as e:
                log.debug("Skipping sounding level %s for %s: %s", lev, model, e)
                rows[lev] = {}

    if run_ref is None:
        raise RuntimeError("No GRIB sounding data available")

    profile = []
    for lev in sorted(_SOUNDING_LEVELS, reverse=True):
        f = rows.get(lev, {})

        t_c = None
        if f.get("tmp_k") is not None:
            t_c = f["tmp_k"] - 273.15

        rh = f.get("rh")
        if rh is not None:
            rh = max(0.0, min(100.0, float(rh)))

        dew_c = _dewpoint_from_temp_rh(t_c, rh)

        u_ms = f.get("u_ms")
        v_ms = f.get("v_ms")
        wspd_kt = None
        if u_ms is not None and v_ms is not None:
            wspd_kt = math.sqrt(u_ms ** 2 + v_ms ** 2) * 1.94384
        wdir = _wind_dir_from_uv(u_ms, v_ms)

        hgt = f.get("hgt_m")
        if hgt is None:
            hgt = _pressure_to_height_m(lev)

        profile.append({
            "pressure": lev,
            "temperature": _f_or_none(t_c, 1),
            "dewpoint": _f_or_none(dew_c, 1),
            "wind_speed": _f_or_none(wspd_kt, 1),
            "wind_direction": _f_or_none(wdir, 1),
            "height": _f_or_none(hgt, 1),
        })

    run_date, run_cycle = run_ref
    result = {
        "model": model,
        "source_model": model,
        "lat": lat,
        "lon": lon,
        "forecast_hour": fhour,
        "valid_time": _valid_time_iso(run_date, run_cycle, fhour),
        "profile": profile,
        "source": "nomads_grib",
    }
    return _nan_safe(result)


def _resolve_aws_point_model(model):
    model_key = model.lower()
    if aws_grib.is_aws_model(model_key):
        return model_key
    if model_key in ("nam", "rap"):
        return "gfs"
    return None


def _build_grib_meteogram(model, lat, lon):
    if nomads.is_nomads_model(model):
        try:
            return _build_nomads_meteogram(model, lat, lon)
        except Exception as exc:
            log.info("NOMADS GRIB meteogram failed for %s: %s - trying AWS", model, exc)

    aws_model = _resolve_aws_point_model(model)
    if aws_model is not None:
        try:
            result = _build_aws_meteogram(aws_model, lat, lon)
            result["model"] = model
            result["requested_model"] = model
            result["source_model"] = aws_model
            return result
        except Exception as exc:
            log.info("AWS GRIB meteogram failed for %s via %s: %s", model, aws_model, exc)

    raise RuntimeError(f"No GRIB source available for meteogram {model}")


def _build_grib_sounding(model, lat, lon, fhour):
    if nomads.is_nomads_model(model):
        try:
            result = _build_nomads_sounding(model, lat, lon, fhour)
            result["analysis"] = _compute_sounding_analysis(result.get("profile", []))
            return result
        except Exception as exc:
            log.info("NOMADS GRIB sounding failed for %s: %s - trying AWS", model, exc)

    aws_model = _resolve_aws_point_model(model)
    if aws_model is not None:
        try:
            result = _build_aws_sounding(aws_model, lat, lon, fhour)
            result["model"] = model
            result["source_model"] = aws_model
            result["analysis"] = _compute_sounding_analysis(result.get("profile", []))
            return result
        except Exception as exc:
            log.info("AWS GRIB sounding failed for %s via %s: %s", model, aws_model, exc)

    raise RuntimeError(f"No GRIB source available for sounding {model} at F{fhour:03d}")


def _build_grib_cross_section(model, variable, fhour, lat1, lon1, lat2, lon2):
    variable_config = _resolve_cross_section_variable(variable)
    sampled_fhour = _snap_sounding_fhour(model, fhour)
    levels = [1000, 925, 850, 700, 500, 300, 250, 200]
    points = []
    for idx in range(20):
        frac = idx / 19.0
        lat = lat1 + (lat2 - lat1) * frac
        lon = lon1 + (lon2 - lon1) * frac
        points.append((round(lat, 3), round(lon, 3)))

    def fetch_point_column(lat, lon):
        cache_key = f"snd:{model}:{sampled_fhour}:{round(lat, 3)}:{round(lon, 3)}"
        sounding = _point_cache_get(cache_key)
        if sounding is None:
            sounding = _build_grib_sounding(model, lat, lon, sampled_fhour)
            _point_cache_set(cache_key, sounding)
        profile_by_level = {
            int(row["pressure"]): row
            for row in sounding.get("profile", [])
            if row.get("pressure") is not None
        }
        column = []
        for level in levels:
            level_row = profile_by_level.get(level)
            value = (
                _extract_cross_section_profile_value(level_row, variable_config["base"])
                if level_row
                else None
            )
            column.append(_f_or_none(value, 1))
        return column, sounding.get("valid_time"), sounding.get("source"), sounding.get("source_model")

    payloads = [None] * len(points)
    failures = 0
    max_workers = min(4, len(points))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(fetch_point_column, lat, lon): idx
            for idx, (lat, lon) in enumerate(points)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                payloads[idx] = future.result()
            except Exception as exc:
                failures += 1
                log.debug(
                    "Skipping cross-section point %s for %s/%s: %s",
                    idx,
                    model,
                    variable,
                    exc,
                )
                payloads[idx] = ([None] * len(levels), None, None, None)

    if failures == len(points):
        raise RuntimeError("No GRIB cross-section data available")

    distances = []
    for idx in range(len(points)):
        if idx == 0:
            distances.append(0)
            continue
        dlat = points[idx][0] - points[idx - 1][0]
        dlon = points[idx][1] - points[idx - 1][1]
        distance_km = math.sqrt(dlat ** 2 + dlon ** 2) * 111.0
        distances.append(round(distances[-1] + distance_km, 1))

    valid_time = next((payload[1] for payload in payloads if payload and payload[1]), None)
    source = next((payload[2] for payload in payloads if payload and payload[2]), None)
    source_model = next((payload[3] for payload in payloads if payload and payload[3]), model)

    result = {
        "model": model,
        "source_model": source_model,
        "source": source,
        "variable": variable_config["base"],
        "requested_variable": variable,
        "requested_forecast_hour": fhour,
        "forecast_hour": sampled_fhour,
        "label": variable_config["label"],
        "unit": variable_config["unit"],
        "valid_time": valid_time,
        "points": [{"lat": lat, "lon": lon} for lat, lon in points],
        "levels": levels,
        "distances": distances,
        "values": [payload[0] for payload in payloads],
    }
    return _nan_safe(result)


def _fetch_aws_point_fields(model, lat, lon, forecast_hour, search_patterns, field_map):
    url, run_date, run_cycle = aws_grib._build_url(model, forecast_hour)
    idx_entries = aws_grib._fetch_idx(url)

    payload = b""
    for pattern in search_patterns:
        ranges = aws_grib._find_byte_ranges(idx_entries, pattern)
        if not ranges:
            continue
        start, end = ranges[0]
        payload += aws_grib._download_byte_range(url, start, end)

    if not payload:
        return {}, run_date, run_cycle

    messages = aws_grib.decode_grib2(payload)
    out = {}
    for msg in messages:
        key = (msg.get("category", -1), msg.get("parameter", -1))
        field_name = field_map.get(key)
        if not field_name:
            continue
        if field_name in out and out[field_name] is not None:
            continue
        out[field_name] = _nearest_message_value(msg, lat, lon)

    return out, run_date, run_cycle


def _build_aws_meteogram(model, lat, lon):
    hours = _model_hour_sequence(model)

    def fetch_hour(fhour):
        group_a, run_date, run_cycle = _fetch_aws_point_fields(
            model,
            lat,
            lon,
            fhour,
            search_patterns=_AWS_METEOGRAM_GROUP_A_PATTERNS,
            field_map=_METEOGRAM_GROUP_A_MAP,
        )

        group_b_patterns = list(_AWS_METEOGRAM_GROUP_B_PATTERNS)
        if fhour == 0:
            group_b_patterns = [p for p in group_b_patterns if "APCP" not in p]

        group_b, _, _ = _fetch_aws_point_fields(
            model,
            lat,
            lon,
            fhour,
            search_patterns=group_b_patterns,
            field_map=_METEOGRAM_GROUP_B_MAP,
        )

        temp_f = None
        if group_a.get("tmp_k") is not None:
            temp_f = group_a["tmp_k"] * 9.0 / 5.0 - 459.67

        dew_f = None
        if group_a.get("dpt_k") is not None:
            dew_f = group_a["dpt_k"] * 9.0 / 5.0 - 459.67

        wind_kt = None
        if group_a.get("u10_ms") is not None and group_a.get("v10_ms") is not None:
            wind_kt = math.sqrt(group_a["u10_ms"] ** 2 + group_a["v10_ms"] ** 2) * 1.94384

        gust_kt = None
        if group_b.get("gust_ms") is not None:
            gust_kt = group_b["gust_ms"] * 1.94384

        apcp_in = 0.0 if fhour == 0 else None
        if group_b.get("apcp_kgm2") is not None:
            apcp_in = group_b["apcp_kgm2"] / 25.4

        return {
            "fhour": fhour,
            "time": _valid_time_iso(run_date, run_cycle, fhour),
            "temperature_2m": _f_or_none(temp_f, 2),
            "dew_point_2m": _f_or_none(dew_f, 2),
            "wind_speed_10m": _f_or_none(wind_kt, 2),
            "wind_gusts_10m": _f_or_none(gust_kt, 2),
            "cape": _f_or_none(group_b.get("cape"), 1),
            "cloud_cover": _f_or_none(group_b.get("cloud_cover"), 1),
            "apcp_accum_in": _f_or_none(apcp_in, 3),
        }

    rows = []
    workers = max(1, min(_NOMADS_POINT_WORKERS, len(hours)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fetch_hour, fh): fh for fh in hours}
        for fut in as_completed(futs):
            try:
                rows.append(fut.result())
            except Exception as e:
                log.debug("Skipping AWS meteogram hour %s for %s: %s", futs[fut], model, e)

    if not rows:
        raise RuntimeError("No AWS GRIB point data available for meteogram")

    rows.sort(key=lambda r: r["fhour"])

    precip_step = []
    prev_accum = None
    for row in rows:
        accum = row.get("apcp_accum_in")
        if accum is None:
            precip_step.append(None)
            continue
        if prev_accum is None:
            precip_step.append(max(0.0, accum))
        else:
            precip_step.append(max(0.0, accum - prev_accum))
        prev_accum = accum

    result = {
        "model": model,
        "lat": lat,
        "lon": lon,
        "times": [r["time"] for r in rows],
        "variables": {
            "temperature_2m": [r["temperature_2m"] for r in rows],
            "dew_point_2m": [r["dew_point_2m"] for r in rows],
            "wind_speed_10m": [r["wind_speed_10m"] for r in rows],
            "wind_gusts_10m": [r["wind_gusts_10m"] for r in rows],
            "precipitation": precip_step,
            "cape": [r["cape"] for r in rows],
            "cloud_cover": [r["cloud_cover"] for r in rows],
        },
        "units": {
            "temperature_2m": "degF",
            "dew_point_2m": "degF",
            "wind_speed_10m": "kt",
            "wind_gusts_10m": "kt",
            "precipitation": "in",
            "cape": "J/kg",
            "cloud_cover": "%",
        },
        "source_model": model,
        "source": "aws_grib",
    }
    return _nan_safe(result)


def _build_aws_sounding(model, lat, lon, fhour):
    def fetch_level(level):
        fields, run_date, run_cycle = _fetch_aws_point_fields(
            model,
            lat,
            lon,
            fhour,
            search_patterns=_aws_sounding_patterns(level),
            field_map=_SOUNDING_MSG_MAP,
        )
        return level, fields, run_date, run_cycle

    rows = {}
    run_ref = None
    workers = max(1, min(_NOMADS_POINT_WORKERS, len(_SOUNDING_LEVELS)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fetch_level, lev): lev for lev in _SOUNDING_LEVELS}
        for fut in as_completed(futs):
            lev = futs[fut]
            try:
                level, fields, run_date, run_cycle = fut.result()
                rows[level] = fields
                if run_ref is None:
                    run_ref = (run_date, run_cycle)
            except Exception as e:
                log.debug("Skipping AWS sounding level %s for %s: %s", lev, model, e)
                rows[lev] = {}

    if run_ref is None:
        raise RuntimeError("No AWS GRIB sounding data available")

    profile = []
    for lev in sorted(_SOUNDING_LEVELS, reverse=True):
        f = rows.get(lev, {})

        t_c = None
        if f.get("tmp_k") is not None:
            t_c = f["tmp_k"] - 273.15

        rh = f.get("rh")
        if rh is not None:
            rh = max(0.0, min(100.0, float(rh)))

        dew_c = _dewpoint_from_temp_rh(t_c, rh)

        u_ms = f.get("u_ms")
        v_ms = f.get("v_ms")
        wspd_kt = None
        if u_ms is not None and v_ms is not None:
            wspd_kt = math.sqrt(u_ms ** 2 + v_ms ** 2) * 1.94384
        wdir = _wind_dir_from_uv(u_ms, v_ms)

        hgt = f.get("hgt_m")
        if hgt is None:
            hgt = _pressure_to_height_m(lev)

        profile.append({
            "pressure": lev,
            "temperature": _f_or_none(t_c, 1),
            "dewpoint": _f_or_none(dew_c, 1),
            "wind_speed": _f_or_none(wspd_kt, 1),
            "wind_direction": _f_or_none(wdir, 1),
            "height": _f_or_none(hgt, 1),
        })

    run_date, run_cycle = run_ref
    result = {
        "model": model,
        "source_model": model,
        "lat": lat,
        "lon": lon,
        "forecast_hour": fhour,
        "valid_time": _valid_time_iso(run_date, run_cycle, fhour),
        "profile": profile,
        "source": "aws_grib",
    }
    return _nan_safe(result)


def _get_supported(model):
    """Return supported variable set using the appropriate data source."""
    if nomads.is_nomads_model(model):
        return nomads.get_supported_variables(model)
    if aws_grib.is_aws_model(model):
        return aws_grib.get_supported_variables(model)
    if ecmwf.is_ecmwf_model(model):
        return ecmwf.get_supported_variables(model)
    return set()


# Derived composite parameter definitions
COMPOSITE_PARAMS = {
    "effective_bulk_shear": {
        "components": ["wind_speed_10m", "wind_speed_500hPa"],
        "compute": "_compute_bulk_shear",
    },
    "stp_approx": {
        "components": ["cape", "convective_inhibition", "wind_speed_10m", "wind_speed_500hPa"],
        "compute": "_compute_stp",
    },
    "thickness_1000_500": {
        "components": ["geopotential_height_500hPa", "surface_pressure"],
        "compute": "_compute_thickness",
    },
    "scp": {
        "components": ["cape", "wind_speed_10m", "wind_speed_500hPa", "wind_speed_850hPa"],
        "compute": "_compute_scp",
    },
    "ship": {
        "components": ["cape", "wind_speed_10m", "wind_speed_500hPa", "temperature_500hPa_raw"],
        "compute": "_compute_ship",
    },
    "critical_angle_composite": {
        "components": ["cape", "convective_inhibition", "wind_speed_10m", "wind_speed_500hPa", "wind_speed_850hPa"],
        "compute": "_compute_tornado_composite",
    },
}


def _fetch_grid(model, variable, fhour, bbox):
    """Fetch a single gridded field with robust multi-source fallback.

    Priority: NOMADS → AWS → ECMWF → ICON.
    Each source is tried only if the model is supported there.
    """
    errors = {}

    if nomads.is_nomads_model(model):
        try:
            return nomads.fetch_grid_forecast(model, variable, fhour, bbox)
        except Exception as exc:
            errors["NOMADS"] = exc
            log.warning(
                "NOMADS failed for %s/%s F%03d (%s: %s) — trying next source",
                model, variable, fhour, type(exc).__name__, exc,
            )

    if aws_grib.is_aws_model(model):
        try:
            return aws_grib.fetch_grid_forecast(model, variable, fhour, bbox)
        except Exception as exc:
            errors["AWS"] = exc
            log.warning(
                "AWS failed for %s/%s F%03d (%s: %s)",
                model, variable, fhour, type(exc).__name__, exc,
            )

    if ecmwf.is_ecmwf_model(model):
        try:
            return ecmwf.fetch_grid_forecast(model, variable, fhour, bbox)
        except Exception as exc:
            errors["ECMWF"] = exc
            log.warning(
                "ECMWF failed for %s/%s F%03d (%s: %s)",
                model, variable, fhour, type(exc).__name__, exc,
            )

    # All sources exhausted
    detail = " ".join(f"{src}: {exc}" for src, exc in errors.items())
    raise RuntimeError(
        f"No GRIB source available for {model}/{variable} at F{fhour:03d}. {detail}"
    )


def _fetch_composite_grid(model, variable, fhour, bbox):
    """Fetch a component field at a coarser working resolution for composites."""
    model_key = model.lower()
    if variable in open_meteo.get_supported_variables(model_key):
        grid = open_meteo.fetch_grid_forecast(model_key, variable, fhour, bbox)
    else:
        log.warning(
            "Open-Meteo does not support composite component %s/%s; falling back to GRIB",
            model_key,
            variable,
        )
        grid = _fetch_grid(model_key, variable, fhour, bbox)
    return thin_grid_result(
        grid,
        bbox=bbox,
        max_cells=_COMPOSITE_MAX_MAP_CELLS,
        max_side=_COMPOSITE_MAX_MAP_SIDE,
    )


def _compute_bulk_shear(grids, fhour):
    """Effective Bulk Shear ≈ |V500 − Vsfc| (scalar approx, kt)."""
    sfc = grids["wind_speed_10m"]
    u500 = grids["wind_speed_500hPa"]
    vals_sfc = sfc["values"]
    vals_500 = u500["values"]
    result = []
    for i in range(len(vals_sfc)):
        row = []
        for j in range(len(vals_sfc[i])):
            v1 = vals_sfc[i][j]
            v2 = vals_500[i][j] if i < len(vals_500) and j < len(vals_500[i]) else None
            if v1 is not None and v2 is not None:
                row.append(round(abs(v2 - v1), 1))
            else:
                row.append(None)
        result.append(row)
    return {
        "model": sfc["model"], "variable": "effective_bulk_shear",
        "forecast_hour": fhour, "lats": sfc["lats"], "lons": sfc["lons"],
        "values": result, "unit": "kt",
    }


def _compute_stp(grids, fhour):
    """Significant Tornado Parameter (enhanced).
    STP ≈ (CAPE/1500) × (shear/20) × CIN_term
    CIN_term: 1 if CIN > -50, ramps to 0 at CIN = -200.
    """
    cape_g = grids["cape"]
    sfc_g = grids["wind_speed_10m"]
    u500_g = grids["wind_speed_500hPa"]
    cin_g = grids.get("convective_inhibition")
    cape_v = cape_g["values"]
    sfc_v = sfc_g["values"]
    u500_v = u500_g["values"]
    cin_v = cin_g["values"] if cin_g else None
    result = []
    for i in range(len(cape_v)):
        row = []
        for j in range(len(cape_v[i])):
            c = cape_v[i][j]
            s1 = sfc_v[i][j] if i < len(sfc_v) and j < len(sfc_v[i]) else None
            s2 = u500_v[i][j] if i < len(u500_v) and j < len(u500_v[i]) else None
            if c is not None and s1 is not None and s2 is not None:
                shear = abs(s2 - s1)
                stp = (c / 1500.0) * (shear / 20.0)
                # Apply CIN damping
                if cin_v and i < len(cin_v) and j < len(cin_v[i]) and cin_v[i][j] is not None:
                    cin = cin_v[i][j]
                    if cin < -200:
                        stp = 0
                    elif cin < -50:
                        stp *= (cin + 200) / 150.0
                row.append(round(max(0, stp), 2))
            else:
                row.append(None)
        result.append(row)
    return {
        "model": cape_g["model"], "variable": "stp_approx",
        "forecast_hour": fhour, "lats": cape_g["lats"], "lons": cape_g["lons"],
        "values": result, "unit": "",
    }


def _compute_thickness(grids, fhour):
    """1000-500mb thickness (dam). Approx 1000mb height from surface pressure via hypsometric equation."""
    h500_g = grids["geopotential_height_500hPa"]
    sp_g = grids["surface_pressure"]
    h500_v = h500_g["values"]
    sp_v = sp_g["values"]
    result = []
    for i in range(len(h500_v)):
        row = []
        for j in range(len(h500_v[i])):
            h500 = h500_v[i][j]
            sp = sp_v[i][j] if i < len(sp_v) and j < len(sp_v[i]) else None
            if h500 is not None and sp is not None and sp > 0:
                # Approx 1000mb geopotential height from MSLP:
                # Z1000 ≈ (T_avg / g) * Rd * ln(sp / 1000)
                # Simplified: Z1000 ≈ (sp - 1000) * 0.083  (dam, rough approx)
                z1000 = (sp - 1000.0) * 0.083
                thickness = h500 - z1000
                row.append(round(thickness, 1))
            else:
                row.append(None)
        result.append(row)
    return {
        "model": h500_g["model"], "variable": "thickness_1000_500",
        "forecast_hour": fhour, "lats": h500_g["lats"], "lons": h500_g["lons"],
        "values": result, "unit": "dam",
    }


def _compute_scp(grids, fhour):
    """Supercell Composite Parameter.
    SCP ≈ (CAPE/1000) × (0-6km_shear/40) × (low_level_shear_term)
    Uses 850-sfc wind diff as a proxy for low-level storm-relative helicity.
    """
    cape_g = grids["cape"]
    sfc_g = grids["wind_speed_10m"]
    u500_g = grids["wind_speed_500hPa"]
    u850_g = grids["wind_speed_850hPa"]
    cape_v = cape_g["values"]
    sfc_v = sfc_g["values"]
    u500_v = u500_g["values"]
    u850_v = u850_g["values"]
    result = []
    for i in range(len(cape_v)):
        row = []
        for j in range(len(cape_v[i])):
            c = cape_v[i][j]
            s_sfc = sfc_v[i][j] if i < len(sfc_v) and j < len(sfc_v[i]) else None
            s_500 = u500_v[i][j] if i < len(u500_v) and j < len(u500_v[i]) else None
            s_850 = u850_v[i][j] if i < len(u850_v) and j < len(u850_v[i]) else None
            if c is not None and s_sfc is not None and s_500 is not None and s_850 is not None:
                deep_shear = abs(s_500 - s_sfc)
                # Proxy low-level shear (850-sfc) as SRH stand-in:
                # SRH_term ≈ (850-sfc shear / 10) capped at 3
                ll_shear = abs(s_850 - s_sfc)
                srh_term = min(ll_shear / 10.0, 3.0)
                scp = (c / 1000.0) * (deep_shear / 40.0) * srh_term
                row.append(round(max(0, scp), 2))
            else:
                row.append(None)
        result.append(row)
    return {
        "model": cape_g["model"], "variable": "scp",
        "forecast_hour": fhour, "lats": cape_g["lats"], "lons": cape_g["lons"],
        "values": result, "unit": "",
    }


def _compute_ship(grids, fhour):
    """Significant Hail Parameter (simplified).
    SHIP ≈ (CAPE × shear × T500_freezing_factor) / 44000
    T500_freezing_factor: max(0, (-T500 - 5.5) / 19.5) — penalizes warm 500mb.
    """
    cape_g = grids["cape"]
    sfc_g = grids["wind_speed_10m"]
    u500_g = grids["wind_speed_500hPa"]
    t500_g = grids["temperature_500hPa_raw"]  # °C (approximated)
    cape_v = cape_g["values"]
    sfc_v = sfc_g["values"]
    u500_v = u500_g["values"]
    t500_v = t500_g["values"]
    result = []
    for i in range(len(cape_v)):
        row = []
        for j in range(len(cape_v[i])):
            c = cape_v[i][j]
            s1 = sfc_v[i][j] if i < len(sfc_v) and j < len(sfc_v[i]) else None
            s2 = u500_v[i][j] if i < len(u500_v) and j < len(u500_v[i]) else None
            t5 = t500_v[i][j] if i < len(t500_v) and j < len(t500_v[i]) else None
            if c is not None and s1 is not None and s2 is not None and t5 is not None:
                shear = abs(s2 - s1)
                # Freezing-level factor: ramps from 0 at T500=-5.5°C to 1 at T500=-25°C
                freeze_factor = max(0, min(1, (-t5 - 5.5) / 19.5))
                ship = (c * shear * freeze_factor) / 44000.0
                row.append(round(max(0, ship), 2))
            else:
                row.append(None)
        result.append(row)
    return {
        "model": cape_g["model"], "variable": "ship",
        "forecast_hour": fhour, "lats": cape_g["lats"], "lons": cape_g["lons"],
        "values": result, "unit": "",
    }


def _compute_tornado_composite(grids, fhour):
    """Tornado Composite: combined metric weighting CAPE, shear, low-level shear, and CIN.
    TC ≈ (CAPE/2000) × (deep_shear/30) × (ll_shear/15) × CIN_factor
    CIN_factor: 1 if CIN > -25, tapers to 0 at CIN=-150.
    """
    cape_g = grids["cape"]
    sfc_g = grids["wind_speed_10m"]
    u500_g = grids["wind_speed_500hPa"]
    u850_g = grids["wind_speed_850hPa"]
    cin_g = grids.get("convective_inhibition")
    cape_v = cape_g["values"]
    sfc_v = sfc_g["values"]
    u500_v = u500_g["values"]
    u850_v = u850_g["values"]
    cin_v = cin_g["values"] if cin_g else None
    result = []
    for i in range(len(cape_v)):
        row = []
        for j in range(len(cape_v[i])):
            c = cape_v[i][j]
            s_sfc = sfc_v[i][j] if i < len(sfc_v) and j < len(sfc_v[i]) else None
            s_500 = u500_v[i][j] if i < len(u500_v) and j < len(u500_v[i]) else None
            s_850 = u850_v[i][j] if i < len(u850_v) and j < len(u850_v[i]) else None
            if c is not None and s_sfc is not None and s_500 is not None and s_850 is not None:
                deep_shear = abs(s_500 - s_sfc)
                ll_shear = abs(s_850 - s_sfc)
                tc = (c / 2000.0) * (deep_shear / 30.0) * (ll_shear / 15.0)
                # CIN damping
                if cin_v and i < len(cin_v) and j < len(cin_v[i]) and cin_v[i][j] is not None:
                    cin = cin_v[i][j]
                    if cin < -150:
                        tc = 0
                    elif cin < -25:
                        tc *= (cin + 150) / 125.0
                row.append(round(max(0, tc), 2))
            else:
                row.append(None)
        result.append(row)
    return {
        "model": cape_g["model"], "variable": "critical_angle_composite",
        "forecast_hour": fhour, "lats": cape_g["lats"], "lons": cape_g["lons"],
        "values": result, "unit": "",
    }


@bp.route("/api/forecast", methods=["GET"])
def get_forecast():
    """
    Fetch gridded forecast data for map rendering.

    Query params:
        model:    gfs, hrrr, nam, rap
        variable: temperature_2m, cape, etc.
        fhour:    forecast hour (int)
        lat_min, lat_max, lon_min, lon_max: bounding box (optional)
    """
    model = request.args.get("model", "gfs")
    variable = request.args.get("variable", "temperature_2m")
    try:
        model = _require_grib_supported_model(model)
        fhour = _parse_fhour_arg(model)
        fhour = _snap_fhour_to_step(model, fhour)
        bbox = parse_bbox(request.args)
    except QueryValidationError as exc:
        return json_error(str(exc), exc.status_code)

    supported = _get_supported(model)
    cached_payload, requested_run = _load_persistent_forecast_cache(model, variable, fhour, bbox)
    if cached_payload is not None:
        return jsonify(_nan_safe(cached_payload))

    # Handle composite/derived parameters
    if variable in COMPOSITE_PARAMS:
        if _requires_forecast_artifact(variable):
            return json_error(
                f"Precomputed '{variable}' forecast artifact is not available yet.",
                503,
                code="artifact_missing",
                artifact_required=True,
                model=model,
                variable=variable,
                forecast_hour=int(fhour),
                region=_resolve_region_name(bbox),
            )

        comp = COMPOSITE_PARAMS[variable]
        # Check that all component variables are supported by this model
        missing = [v for v in comp["components"]
                   if v not in supported and v != "temperature_500hPa_raw"]
        if missing:
            return jsonify({
                "error": f"Variable '{variable}' is not available for model '{model}' "
                         f"(missing: {', '.join(missing)})"
            }), 400
        try:
            grids = {}
            for comp_var in comp["components"]:
                # Special handling for raw 500mb temp (need °C for SHIP freezing-level calc)
                fetch_var = "temperature_850hPa" if comp_var == "temperature_500hPa_raw" else comp_var
                # For 500mb temp, override grib params to use 500mb level
                if comp_var == "temperature_500hPa_raw":
                    # Approximate 500mb temperature from 850mb: T500 ~= T850 - 25C.
                    t850 = _fetch_composite_grid(model, "temperature_850hPa", fhour, bbox)
                    # Approximate 500mb temperature (rough lapse rate)
                    approx_vals = []
                    for row in t850["values"]:
                        approx_vals.append([
                            (v - 25.0 if v is not None else None) for v in row
                        ])
                    grids[comp_var] = {**t850, "values": approx_vals}
                else:
                    grids[comp_var] = _fetch_composite_grid(model, fetch_var, fhour, bbox)
            compute_fn = globals()[comp["compute"]]
            result = compute_fn(grids, fhour)
            # Preserve timing metadata from source grids so frontend time labels stay synced.
            first_grid = next(iter(grids.values()), None)
            if isinstance(first_grid, dict):
                if "run" not in result and first_grid.get("run"):
                    result["run"] = first_grid["run"]
                if "valid_time" not in result and first_grid.get("valid_time"):
                    result["valid_time"] = first_grid["valid_time"]
            result = _nan_safe(result)
            _store_persistent_forecast_cache(model, variable, fhour, bbox, result, requested_run)
            return jsonify(result)
        except RateLimitError:
            return jsonify({"error": "Weather API rate limited. Please wait a moment and try again.", "retry_after": 60}), 429
        except Exception:
            log.exception("Composite forecast failed for %s/%s at fhour %s", model, variable, fhour)
            return json_error(f"Failed to compute '{variable}' forecast.", 502)

    if variable not in supported:
        return jsonify({"error": f"Variable '{variable}' is not available for model '{model}'"}), 400

    try:
        result = _fetch_grid(model, variable, fhour, bbox)
        result = _nan_safe(result)
        _store_persistent_forecast_cache(model, variable, fhour, bbox, result, requested_run)
        return jsonify(result)
    except RateLimitError:
        return jsonify({"error": "Weather API rate limited. Please wait a moment and try again.", "retry_after": 60}), 429
    except FileNotFoundError as e:
        return json_error(str(e), 404)
    except ValueError as e:
        return json_error(str(e), 400)
    except Exception:
        log.exception("Forecast fetch failed for %s/%s at fhour %s", model, variable, fhour)
        return json_error("Failed to fetch forecast data from upstream providers.", 502)


@bp.route("/api/color-scale", methods=["GET"])
def get_color_scale_route():
    """Return color scale definition for a given parameter."""
    cmap = request.args.get("cmap", "temperature")
    scale = get_color_scale(cmap)
    if scale is None:
        return jsonify({"error": f"Unknown color scale: {cmap}"}), 404
    return jsonify(scale)


@bp.route("/api/meteogram", methods=["GET"])
def get_meteogram():
    """
    Fetch full time-series at a single point for meteogram display.

    Query params:
        model:  gfs, hrrr, nam, rap
        lat:    latitude
        lon:    longitude
    """
    model = request.args.get("model", "gfs")
    try:
        model = _require_grib_supported_model(model)
        lat, lon = parse_lat_lon(request.args)
    except QueryValidationError as exc:
        return json_error(str(exc), exc.status_code)

    cache_key = f"met:{model}:{round(lat, 3)}:{round(lon, 3)}"
    cached = _meteogram_cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)

    try:
        result = _build_grib_meteogram(model, lat, lon)
        result = _nan_safe(result)
        _meteogram_cache_set(cache_key, result)
        return jsonify(result)
    except Exception:
        log.exception("Meteogram fetch failed for %s at (%.3f, %.3f)", model, lat, lon)
        return json_error("Failed to fetch meteogram data from upstream providers.", 502)


@bp.route("/api/sounding", methods=["GET"])
def get_sounding():
    """
    Fetch vertical profile data at a point for a simple sounding display.

    Query params:
        model:  gfs, hrrr, nam, rap
        lat:    latitude
        lon:    longitude
        fhour:  forecast hour (default 0)
    """
    try:
        model = request.args.get("model", "gfs")
        model = _require_grib_supported_model(model)
        fhour_raw = _parse_fhour_arg(model)
        fhour = _snap_sounding_fhour(model, fhour_raw)
        lat, lon = parse_lat_lon(request.args)
    except QueryValidationError as exc:
        return json_error(str(exc), exc.status_code)

    cache_key = f"snd:{model}:{fhour}:{round(lat, 3)}:{round(lon, 3)}"
    cached = _point_cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)

    try:
        result = _build_grib_sounding(model, lat, lon, fhour)
        result = _nan_safe(result)
        _point_cache_set(cache_key, result)
        return jsonify(result)
    except Exception:
        log.exception("Sounding fetch failed for %s at fhour %s (%.3f, %.3f)", model, fhour, lat, lon)
        return json_error("Failed to fetch sounding data from upstream providers.", 502)


# ─── Local sounding plot renderer ─────────────────────────
_SOUNDING_PLOT_CACHE = {}
_SOUNDING_PLOT_CACHE_LOCK = threading.Lock()
_SOUNDING_PLOT_TTL = 1200  # 20 min


def _plot_cache_get(key):
    now = time.time()
    with _SOUNDING_PLOT_CACHE_LOCK:
        entry = _SOUNDING_PLOT_CACHE.get(key)
        if entry and (now - entry["ts"]) < _SOUNDING_PLOT_TTL:
            return entry["data"]
        if entry:
            del _SOUNDING_PLOT_CACHE[key]
    return None


def _plot_cache_set(key, data):
    with _SOUNDING_PLOT_CACHE_LOCK:
        if len(_SOUNDING_PLOT_CACHE) > 200:
            oldest = sorted(_SOUNDING_PLOT_CACHE, key=lambda k: _SOUNDING_PLOT_CACHE[k]["ts"])[:50]
            for k in oldest:
                del _SOUNDING_PLOT_CACHE[k]
        _SOUNDING_PLOT_CACHE[key] = {"data": data, "ts": time.time()}


def _plot_font(size, bold=False):
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


def _safe_float(value):
    try:
        if value is None:
            return None
        value = float(value)
        if not math.isfinite(value):
            return None
        return value
    except (TypeError, ValueError):
        return None


def _draw_dashed_line(draw, start, end, fill, width=1, dash=8, gap=6):
    x1, y1 = start
    x2, y2 = end
    length = math.hypot(x2 - x1, y2 - y1)
    if length <= 0:
        return
    dx = (x2 - x1) / length
    dy = (y2 - y1) / length
    pos = 0.0
    while pos < length:
        seg_end = min(pos + dash, length)
        draw.line(
            (
                x1 + dx * pos,
                y1 + dy * pos,
                x1 + dx * seg_end,
                y1 + dy * seg_end,
            ),
            fill=fill,
            width=width,
        )
        pos += dash + gap


def _render_local_sounding_png(sounding, theme="dark", colorblind=False):
    profile = [
        lv for lv in sounding.get("profile", [])
        if _safe_float(lv.get("pressure")) is not None
    ]
    profile = sorted(profile, key=lambda lv: -float(lv["pressure"]))
    if len(profile) < 3:
        raise ValueError("Not enough sounding levels to render")

    dark = str(theme).lower() != "light"
    bg = "#07111f" if dark else "#f8fafc"
    panel = "#0c1a2b" if dark else "#ffffff"
    border = "#29445f" if dark else "#cbd5e1"
    grid = "#20364d" if dark else "#d9e2ec"
    text = "#e6f1ff" if dark else "#0f172a"
    muted = "#8fb3d1" if dark else "#52677c"
    temp_color = "#ff756f" if not colorblind else "#d55e00"
    dew_color = "#65d98b" if not colorblind else "#009e73"
    wind_color = "#92c5ff" if dark else "#2563eb"

    width, height = 1200, 900
    image = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(image, "RGBA")

    plot_left, plot_top = 92, 118
    plot_right, plot_bottom = 875, 770
    plot_w = plot_right - plot_left
    plot_h = plot_bottom - plot_top
    side_left, side_right = 915, 1148
    p_bottom, p_top = 1000.0, 100.0
    t_min, t_max = -80.0, 50.0
    skew_px = 168.0

    title_font = _plot_font(32, bold=True)
    label_font = _plot_font(19, bold=True)
    small_font = _plot_font(16)
    tiny_font = _plot_font(13)

    draw.rectangle((0, 0, width, height), fill=bg)
    draw.rounded_rectangle((38, 34, width - 38, height - 36), radius=18, fill=panel, outline=border, width=2)

    model = str(sounding.get("model", "model")).upper()
    fhour = int(sounding.get("forecast_hour") or 0)
    valid = sounding.get("valid_time") or ""
    lat = _safe_float(sounding.get("lat"))
    lon = _safe_float(sounding.get("lon"))
    point = ""
    if lat is not None and lon is not None:
        point = f"  {lat:.2f}N, {lon:.2f}W"
    draw.text((72, 58), f"{model} Sounding - F{fhour:03d}", font=title_font, fill=text)
    draw.text((74, 94), f"{valid}{point}", font=small_font, fill=muted)

    draw.rectangle((plot_left, plot_top, plot_right, plot_bottom), fill=(255, 255, 255, 6), outline=border, width=1)

    log_span = math.log(p_bottom) - math.log(p_top)

    def y_for_p(pressure):
        pressure = max(p_top, min(p_bottom, float(pressure)))
        ratio = (math.log(p_bottom) - math.log(pressure)) / log_span
        return plot_bottom - ratio * plot_h

    def x_for_tp(temp_c, pressure):
        pressure = max(p_top, min(p_bottom, float(pressure)))
        ratio = (math.log(p_bottom) - math.log(pressure)) / log_span
        return plot_left + ((float(temp_c) - t_min) / (t_max - t_min)) * plot_w + ratio * skew_px

    pressure_ticks = [1000, 925, 850, 700, 500, 300, 250, 200, 150, 100]
    for pressure in pressure_ticks:
        y = y_for_p(pressure)
        draw.line((plot_left, y, plot_right, y), fill=grid, width=1)
        draw.text((48, y - 9), str(pressure), font=tiny_font, fill=muted)

    for temp in range(-80, 60, 10):
        x1 = x_for_tp(temp, p_bottom)
        x2 = x_for_tp(temp, p_top)
        _draw_dashed_line(draw, (x1, plot_bottom), (x2, plot_top), fill=(80, 112, 144, 115), width=1, dash=5, gap=8)
        if plot_left <= x1 <= plot_right:
            draw.text((x1 - 12, plot_bottom + 10), str(temp), font=tiny_font, fill=muted)

    draw.text((plot_left + plot_w / 2 - 80, height - 78), "Temperature (C)", font=small_font, fill=muted)
    draw.text((45, plot_top - 28), "hPa", font=small_font, fill=muted)

    def points_for(field):
        pts = []
        for lv in profile:
            pressure = _safe_float(lv.get("pressure"))
            value = _safe_float(lv.get(field))
            if pressure is not None and value is not None and p_top <= pressure <= p_bottom:
                pts.append((x_for_tp(value, pressure), y_for_p(pressure)))
        return pts

    for pts, color in ((points_for("dewpoint"), dew_color), (points_for("temperature"), temp_color)):
        if len(pts) >= 2:
            draw.line(pts, fill=color, width=4, joint="curve")
        for x, y in pts:
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=color)

    draw.line((plot_left + 22, plot_top + 28, plot_left + 72, plot_top + 28), fill=temp_color, width=4)
    draw.text((plot_left + 84, plot_top + 18), "Temp", font=tiny_font, fill=text)
    draw.line((plot_left + 152, plot_top + 28, plot_left + 202, plot_top + 28), fill=dew_color, width=4)
    draw.text((plot_left + 214, plot_top + 18), "Dewpoint", font=tiny_font, fill=text)

    # Wind column. Draw simple vectors scaled by speed; meteorological direction is "from".
    draw.text((side_left, plot_top), "Wind", font=label_font, fill=text)
    for pressure in pressure_ticks[:-1]:
        level = min(
            profile,
            key=lambda lv: abs((_safe_float(lv.get("pressure")) or 0) - pressure),
        )
        wspd = _safe_float(level.get("wind_speed"))
        wdir = _safe_float(level.get("wind_direction"))
        if wspd is None or wdir is None:
            continue
        y = y_for_p(_safe_float(level.get("pressure")) or pressure)
        x = side_left + 28
        length = max(18, min(74, wspd * 1.4))
        angle = math.radians(270.0 - wdir)
        x2 = x + math.cos(angle) * length
        y2 = y + math.sin(angle) * length
        draw.line((x, y, x2, y2), fill=wind_color, width=3)
        draw.ellipse((x2 - 3, y2 - 3, x2 + 3, y2 + 3), fill=wind_color)
        draw.text((side_left + 118, y - 9), f"{pressure:>4} {wspd:>4.0f} kt", font=tiny_font, fill=muted)

    analysis = sounding.get("analysis") or {}
    draw.rounded_rectangle((side_left, 382, side_right, 742), radius=12, fill=(255, 255, 255, 10), outline=border, width=1)
    draw.text((side_left + 18, 402), "Parcel / Severe Indices", font=label_font, fill=text)
    rows = [
        ("CAPE", analysis.get("cape"), "J/kg"),
        ("CIN", analysis.get("cin"), "J/kg"),
        ("LCL", analysis.get("lcl_hpa"), "hPa"),
        ("LFC", analysis.get("lfc_hpa"), "hPa"),
        ("EL", analysis.get("el_hpa"), "hPa"),
        ("LI", analysis.get("lifted_index"), "C"),
        ("K Index", analysis.get("k_index"), ""),
        ("Total Totals", analysis.get("total_totals"), ""),
        ("PWAT", analysis.get("pwat_mm"), "mm"),
        ("0-6 km Shear", analysis.get("bulk_shear_0_6km_kt"), "kt"),
    ]
    y = 442
    for label, value, unit in rows:
        rendered = "--" if value is None else f"{value:g} {unit}".rstrip()
        draw.text((side_left + 18, y), label, font=tiny_font, fill=muted)
        draw.text((side_right - 102, y), rendered, font=tiny_font, fill=text)
        y += 28

    for key, label, color in (
        ("lcl_hpa", "LCL", "#fbbf24"),
        ("lfc_hpa", "LFC", "#38bdf8"),
        ("el_hpa", "EL", "#c084fc"),
    ):
        pressure = _safe_float(analysis.get(key))
        if pressure is None or not (p_top <= pressure <= p_bottom):
            continue
        y = y_for_p(pressure)
        _draw_dashed_line(draw, (plot_left, y), (plot_right, y), fill=color, width=2, dash=10, gap=8)
        draw.text((plot_right - 44, y - 20), label, font=tiny_font, fill=color)

    draw.text((72, height - 44), f"Source: {sounding.get('source', 'grib')} / {sounding.get('source_model', model)}", font=tiny_font, fill=muted)
    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


@bp.route("/api/sounding-plot", methods=["GET"])
def get_sounding_plot():
    """
    Render a local model sounding image from the same GRIB profile data used by
    /api/sounding. This keeps Model Forecast independent from the retired
    Sounding Analysis service.

    Query params:
        model, lat, lon, fhour, theme, colorblind
    Returns:
        { image (base64 PNG), params, profile, meta }
    """
    try:
        model = request.args.get("model", "gfs")
        fhour_raw = _parse_fhour_arg(model)
        fhour = _snap_sounding_fhour(model, fhour_raw)
        theme = request.args.get("theme", "dark")
        colorblind = request.args.get("colorblind", "false").lower() == "true"
        run_token = request.args.get("run")
        lat, lon = parse_lat_lon(request.args)
    except QueryValidationError as exc:
        return json_error(str(exc), exc.status_code)

    run_date_token = None
    if run_token:
        m = re.match(r"^(\d{8})\/(\d{2})z$", str(run_token).strip(), flags=re.IGNORECASE)
        if m:
            run_date_token = f"{m.group(1)}{m.group(2)}"

    cache_key = (
        f"sndplot:{model}:{fhour}:{round(lat, 3)}:{round(lon, 3)}:{theme}:{run_date_token or 'auto'}"
    )
    cached = _plot_cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)

    try:
        sounding = _build_grib_sounding(model, lat, lon, fhour)
        image_png = _render_local_sounding_png(sounding, theme=theme, colorblind=colorblind)
        result = {
            "image": base64.b64encode(image_png).decode("ascii"),
            "params": sounding.get("analysis", {}),
            "profile": sounding.get("profile", []),
            "meta": {
                "model": sounding.get("model", model),
                "source_model": sounding.get("source_model"),
                "forecast_hour": sounding.get("forecast_hour", fhour),
                "valid_time": sounding.get("valid_time"),
                "source": sounding.get("source"),
            },
        }
        _plot_cache_set(cache_key, result)
        return jsonify(result)
    except Exception:
        log.exception("Local sounding plot failed for %s at fhour %s (%.3f, %.3f)", model, fhour, lat, lon)
        return json_error("Failed to render sounding plot from model profile data.", 502)


@bp.route("/api/cross-section", methods=["GET"])
def get_cross_section():
    """
    Fetch a vertical cross-section along a line using GRIB soundings.

    Query params:
        model, variable, fhour,
        lat1, lon1, lat2, lon2  — endpoints of the cross-section line
    """
    try:
        model = request.args.get("model", "gfs")
        model = _require_grib_supported_model(model)
        variable = request.args.get("variable", "temperature")
        fhour = _parse_fhour_arg(model)
        lat1, lon1 = parse_lat_lon(request.args, "lat1", "lon1")
        lat2, lon2 = parse_lat_lon(request.args, "lat2", "lon2")
        _resolve_cross_section_variable(variable)
    except QueryValidationError as exc:
        return json_error(str(exc), exc.status_code)

    try:
        result = _build_grib_cross_section(model, variable, fhour, lat1, lon1, lat2, lon2)
        return jsonify(result)
    except Exception:
        log.exception(
            "Cross-section fetch failed for %s/%s at fhour %s from (%.3f, %.3f) to (%.3f, %.3f)",
            model,
            variable,
            fhour,
            lat1,
            lon1,
            lat2,
            lon2,
        )
        return json_error("Failed to fetch cross-section data from upstream providers.", 502)


@bp.route("/api/cross-section/stream", methods=["GET"])
def get_cross_section_stream():
    """Stream cross-section progress via Server-Sent Events.

    Emits events:
      - ``progress`` with ``{completed, total}`` as each column finishes.
      - ``result``   with the full cross-section JSON (same shape as the
        non-streaming endpoint).
      - ``error``    on failure.

    The client can render a progress bar and receive the final payload in a
    single connection without polling.
    """
    try:
        model = request.args.get("model", "gfs")
        model = _require_grib_supported_model(model)
        variable = request.args.get("variable", "temperature")
        fhour = _parse_fhour_arg(model)
        lat1, lon1 = parse_lat_lon(request.args, "lat1", "lon1")
        lat2, lon2 = parse_lat_lon(request.args, "lat2", "lon2")
        _resolve_cross_section_variable(variable)
    except QueryValidationError as exc:
        return json_error(str(exc), exc.status_code)

    def generate():
        """Yield SSE frames as each sounding column completes."""
        try:
            variable_config = _resolve_cross_section_variable(variable)
            sampled_fhour = _snap_sounding_fhour(model, fhour)
            levels = [1000, 925, 850, 700, 500, 300, 250, 200]
            points = []
            for idx in range(20):
                frac = idx / 19.0
                lat = lat1 + (lat2 - lat1) * frac
                lon = lon1 + (lon2 - lon1) * frac
                points.append((round(lat, 3), round(lon, 3)))

            total = len(points)
            completed = [0]
            payloads = [None] * total

            def fetch_point_column(idx, lat, lon):
                cache_key = f"snd:{model}:{sampled_fhour}:{round(lat, 3)}:{round(lon, 3)}"
                sounding = _point_cache_get(cache_key)
                if sounding is None:
                    sounding = _build_grib_sounding(model, lat, lon, sampled_fhour)
                    _point_cache_set(cache_key, sounding)
                profile_by_level = {
                    int(row["pressure"]): row
                    for row in sounding.get("profile", [])
                    if row.get("pressure") is not None
                }
                column = []
                for level in levels:
                    level_row = profile_by_level.get(level)
                    value = (
                        _extract_cross_section_profile_value(level_row, variable_config["base"])
                        if level_row
                        else None
                    )
                    column.append(_f_or_none(value, 1))
                return column, sounding.get("valid_time"), sounding.get("source"), sounding.get("source_model")

            max_workers = min(4, total)
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(fetch_point_column, idx, lat, lon): idx
                    for idx, (lat, lon) in enumerate(points)
                }
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        payloads[idx] = future.result()
                    except Exception as exc:
                        log.debug("Cross-section point %s failed: %s", idx, exc)
                        payloads[idx] = ([None] * len(levels), None, None, None)
                    completed[0] += 1
                    yield f"event: progress\ndata: {json.dumps({'completed': completed[0], 'total': total})}\n\n"

            # Compute distances
            distances = [0]
            for i in range(1, len(points)):
                dlat = points[i][0] - points[i - 1][0]
                dlon = points[i][1] - points[i - 1][1]
                km = math.sqrt(dlat ** 2 + dlon ** 2) * 111.0
                distances.append(round(distances[-1] + km, 1))

            valid_time = next((p[1] for p in payloads if p and p[1]), None)
            source = next((p[2] for p in payloads if p and p[2]), None)
            source_model = next((p[3] for p in payloads if p and p[3]), model)

            result = _nan_safe({
                "model": model,
                "source_model": source_model,
                "source": source,
                "variable": variable_config["base"],
                "requested_variable": variable,
                "requested_forecast_hour": fhour,
                "forecast_hour": sampled_fhour,
                "label": variable_config["label"],
                "unit": variable_config["unit"],
                "valid_time": valid_time,
                "points": [{"lat": lat, "lon": lon} for lat, lon in points],
                "levels": levels,
                "distances": distances,
                "values": [p[0] for p in payloads],
            })
            yield f"event: result\ndata: {json.dumps(result)}\n\n"
        except Exception as exc:
            log.exception("SSE cross-section failed")
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@bp.route("/api/ensemble", methods=["GET"])
def get_ensemble():
    """
    Fetch ensemble plume data (all members) at a single point for a variable.

    Query params:
        lat, lon: point
        variable: temperature_2m, precipitation, cape, wind_speed_10m
    Returns:
        { times, members: [ [val,...], ... ], percentiles: { p10, p25, p50, p75, p90 } }
    """
    try:
        lat, lon = parse_lat_lon(request.args)
    except QueryValidationError as exc:
        return json_error(str(exc), exc.status_code)

    requested_variable = request.args.get("variable", "temperature_2m")
    candidate_variables = [requested_variable]
    for fallback_var in _ENSEMBLE_FALLBACK_ORDER:
        if fallback_var not in candidate_variables:
            candidate_variables.append(fallback_var)

    try:
        selected = None

        for candidate_var in candidate_variables:
            try:
                om_var = open_meteo.VARIABLE_MAP.get(candidate_var, candidate_var)
                params = {
                    "latitude": round(lat, 4),
                    "longitude": round(lon, 4),
                    "hourly": om_var,
                    "temperature_unit": "fahrenheit",
                    "wind_speed_unit": "kn",
                    "precipitation_unit": "inch",
                }
                resp = open_meteo._request_with_retry(
                    "https://ensemble-api.open-meteo.com/v1/ensemble",
                    params={**params, "models": "gfs_seamless"},
                    timeout=20,
                )
                resp.raise_for_status()

                data = resp.json()
                hourly = data.get("hourly", {})
                times = hourly.get("time", [])

                members = []
                for key, vals in hourly.items():
                    if key.startswith(om_var + "_member"):
                        members.append(vals)

                if not members:
                    continue

                arr = np.asarray(members, dtype=float)
                if arr.size == 0:
                    continue

                valid_ratio = float(np.isfinite(arr).mean())
                is_last_candidate = candidate_var == candidate_variables[-1]
                if valid_ratio < 0.12 and not is_last_candidate:
                    continue

                percentiles = {
                    "p10": np.nanpercentile(arr, 10, axis=0).tolist(),
                    "p25": np.nanpercentile(arr, 25, axis=0).tolist(),
                    "p50": np.nanpercentile(arr, 50, axis=0).tolist(),
                    "p75": np.nanpercentile(arr, 75, axis=0).tolist(),
                    "p90": np.nanpercentile(arr, 90, axis=0).tolist(),
                }

                selected = {
                    "variable": requested_variable,
                    "source_variable": candidate_var,
                    "lat": lat,
                    "lon": lon,
                    "times": times,
                    "n_members": len(members),
                    "members": members,
                    "percentiles": percentiles,
                    "valid_ratio": round(valid_ratio, 4),
                }
                break
            except RateLimitError:
                raise
            except Exception as exc:
                log.info(
                    "Ensemble candidate failed for %s via %s at (%.3f, %.3f): %s",
                    requested_variable,
                    candidate_var,
                    lat,
                    lon,
                    exc,
                )
                continue

        if selected is None:
            return jsonify({"error": "No ensemble members found"}), 404

        selected = _nan_safe(selected)
        return jsonify(selected)
    except RateLimitError:
        return jsonify({"error": "Rate limited. Try again shortly.", "retry_after": 60}), 429
    except Exception:
        log.exception("Ensemble fetch failed for %s at (%.3f, %.3f)", requested_variable, lat, lon)
        return json_error("Failed to fetch ensemble data from the upstream provider.", 502)
