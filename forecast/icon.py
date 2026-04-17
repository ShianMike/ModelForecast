"""
DWD ICON Open Data provider.

Fetches DWD ICON (Icosahedral Nonhydrostatic) global model forecasts from:
  https://opendata.dwd.de/weather/nwp/icon/grib/

ICON data is published in single-level GRIB2 files per variable/level/hour,
compressed with bzip2.  This provider downloads individual variable files and
decodes them.

Reference:
  https://www.dwd.de/EN/ourservices/opendata/opendata.html
"""

import bz2
import logging
from datetime import datetime, timedelta

import numpy as np
import requests

from forecast.grib2 import decode_grib2
from forecast.grid_utils import crop_and_thin_grid

log = logging.getLogger(__name__)

_session = requests.Session()
_session.headers.update({"User-Agent": "ModelForecast/1.0"})

# ─── Model configuration ──────────────────────────────────

ICON_MODELS = {
    "icon_global": {
        "name": "ICON Global",
        "base_url": "https://opendata.dwd.de/weather/nwp/icon/grib",
        "resolution": "13 km",
        "maxHour": 180,
        "step": 3,
        "cycles": [0, 6, 12, 18],
        "publish_delay_hours": 4,
    },
}

# Map internal variable names → DWD file path components.
# DWD uses a specific naming convention:
#   {base}/{cycle}/{varname}/icon_global_icosahedral_single-level_{date}{cycle}_{fhour}_{VARNAME}.grib2.bz2
ICON_VARIABLE_MAP = {
    "temperature_2m": {
        "dwd_name": "t_2m",
        "level_type": "single-level",
        "wind": False,
        "convert": lambda v: (v - 273.15) * 9 / 5 + 32,  # K → °F
        "unit": "°F",
    },
    "dewpoint_2m": {
        "dwd_name": "td_2m",
        "level_type": "single-level",
        "wind": False,
        "convert": lambda v: (v - 273.15) * 9 / 5 + 32,
        "unit": "°F",
    },
    "wind_speed_10m": {
        "wind": True,
        "dwd_name_u": "u_10m",
        "dwd_name_v": "v_10m",
        "level_type": "single-level",
        "convert": lambda v: v * 1.94384,  # m/s → kt
        "unit": "kt",
    },
    "surface_pressure": {
        "dwd_name": "pmsl",
        "level_type": "single-level",
        "wind": False,
        "convert": lambda v: v / 100.0,  # Pa → hPa
        "unit": "hPa",
    },
    "cape": {
        "dwd_name": "cape_ml",
        "level_type": "single-level",
        "wind": False,
        "convert": None,
        "unit": "J/kg",
    },
    "precipitation": {
        "dwd_name": "tot_prec",
        "level_type": "single-level",
        "wind": False,
        "convert": lambda v: v / 25.4,  # mm → inches
        "unit": "in",
    },
    "cloud_cover": {
        "dwd_name": "clct",
        "level_type": "single-level",
        "wind": False,
        "convert": None,
        "unit": "%",
    },
    "geopotential_height_500hPa": {
        "dwd_name": "fi",
        "level_type": "pressure-level",
        "level": 500,
        "wind": False,
        "convert": lambda v: v / (9.80665 * 10),  # m²/s² → dam
        "unit": "dam",
    },
    "temperature_850hPa": {
        "dwd_name": "t",
        "level_type": "pressure-level",
        "level": 850,
        "wind": False,
        "convert": lambda v: v - 273.15,  # K → °C
        "unit": "°C",
    },
}

ICON_SUPPORTED_VARS = set(ICON_VARIABLE_MAP.keys())

DEFAULT_BBOX = {"lat_min": 20.0, "lat_max": 55.0, "lon_min": -130.0, "lon_max": -60.0}


# ─── Internal helpers ──────────────────────────────────────

def is_icon_model(model):
    """Return True if *model* is served by this provider."""
    return model.lower() in ICON_MODELS


def get_supported_variables(model=None):
    return ICON_SUPPORTED_VARS


def _find_latest_run(model_key):
    """Determine the most recent ICON run likely available."""
    cfg = ICON_MODELS[model_key]
    now = datetime.utcnow()
    for hours_back in range(0, 48, 6):
        candidate = now - timedelta(hours=hours_back)
        cycle = max(c for c in cfg["cycles"] if c <= candidate.hour)
        run_date = candidate.strftime("%Y%m%d")
        run_dt = datetime.strptime(f"{run_date}{cycle:02d}", "%Y%m%d%H")
        if now >= run_dt + timedelta(hours=cfg["publish_delay_hours"]):
            return run_date, cycle
    yesterday = (now - timedelta(days=1)).strftime("%Y%m%d")
    return yesterday, 0


def _build_url(model_key, var_info, dwd_name, forecast_hour, run_date, run_cycle):
    """Build the DWD open-data URL for a single ICON GRIB file."""
    cfg = ICON_MODELS[model_key]
    level_type = var_info["level_type"]
    fhour_str = f"{forecast_hour:03d}"

    if level_type == "pressure-level":
        level = var_info["level"]
        filename = (
            f"icon_global_icosahedral_{level_type}_{run_date}{run_cycle:02d}"
            f"_{fhour_str}_{level}_{dwd_name.upper()}.grib2.bz2"
        )
        url = f"{cfg['base_url']}/{run_cycle:02d}/{dwd_name}/{filename}"
    else:
        filename = (
            f"icon_global_icosahedral_{level_type}_{run_date}{run_cycle:02d}"
            f"_{fhour_str}_{dwd_name.upper()}.grib2.bz2"
        )
        url = f"{cfg['base_url']}/{run_cycle:02d}/{dwd_name}/{filename}"

    return url


def _download_and_decompress(url):
    """Download a bzip2-compressed GRIB file and decompress it."""
    resp = _session.get(url, timeout=30)
    if resp.status_code == 404:
        raise FileNotFoundError(f"ICON file not found: {url}")
    resp.raise_for_status()
    return bz2.decompress(resp.content)


# ─── Public API ────────────────────────────────────────────

def fetch_grid_forecast(model, variable, forecast_hour, bbox=None):
    """Fetch a gridded forecast field from DWD ICON open data.

    Returns the same dict shape used by the NOMADS, AWS, and ECMWF providers.
    """
    if bbox is None:
        bbox = DEFAULT_BBOX

    model_key = model.lower()
    if model_key not in ICON_MODELS:
        raise ValueError(f"Model '{model}' is not available from DWD")
    if variable not in ICON_VARIABLE_MAP:
        raise ValueError(f"Variable '{variable}' not mapped for ICON")

    var_info = ICON_VARIABLE_MAP[variable]
    run_date, run_cycle = _find_latest_run(model_key)

    if var_info["wind"]:
        # Download U and V separately
        url_u = _build_url(model_key, var_info, var_info["dwd_name_u"], forecast_hour, run_date, run_cycle)
        url_v = _build_url(model_key, var_info, var_info["dwd_name_v"], forecast_hour, run_date, run_cycle)
        data_u = _download_and_decompress(url_u)
        data_v = _download_and_decompress(url_v)
        msgs_u = decode_grib2(data_u)
        msgs_v = decode_grib2(data_v)
        if not msgs_u or not msgs_v:
            raise ValueError("Could not decode ICON wind GRIB data")
        u_msg, v_msg = msgs_u[0], msgs_v[0]
        lats, lons = u_msg["lats"], u_msg["lons"]
        u_raw = u_msg["values"]
        v_raw = v_msg["values"]
        raw_values = np.sqrt(u_raw ** 2 + v_raw ** 2)
    else:
        dwd_name = var_info["dwd_name"]
        url = _build_url(model_key, var_info, dwd_name, forecast_hour, run_date, run_cycle)
        data = _download_and_decompress(url)
        messages = decode_grib2(data)
        if not messages:
            raise ValueError("No decodable GRIB2 messages from ICON")
        msg = messages[0]
        lats, lons, raw_values = msg["lats"], msg["lons"], msg["values"]
        u_raw = v_raw = None

    # Longitude normalisation
    if np.any(lons > 180):
        lons = lons.copy()
        lons[lons > 180] -= 360
        idx = np.argsort(lons)
        lons = lons[idx]
        raw_values = raw_values[:, idx]
        if u_raw is not None:
            u_raw = u_raw[:, idx]
            v_raw = v_raw[:, idx]

    # Latitude ascending
    if len(lats) > 1 and lats[0] > lats[-1]:
        lats = lats[::-1]
        raw_values = raw_values[::-1, :]
        if u_raw is not None:
            u_raw = u_raw[::-1, :]
            v_raw = v_raw[::-1, :]

    lats, lons, raw_values, u_raw, v_raw = crop_and_thin_grid(
        lats, lons, raw_values, bbox=bbox, u_component=u_raw, v_component=v_raw,
    )

    # Unit conversion
    convert = var_info.get("convert")
    if convert is not None:
        values = convert(raw_values.astype(np.float64))
    else:
        values = raw_values.astype(np.float64)

    cycle_dt = datetime.strptime(f"{run_date}{run_cycle:02d}", "%Y%m%d%H")
    valid_dt = cycle_dt + timedelta(hours=forecast_hour)

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
        "valid_time": valid_dt.strftime("%Y-%m-%d %H:%M UTC"),
        "run": f"{run_date}/{run_cycle:02d}z",
    }

    if u_raw is not None and v_raw is not None:
        result["u_component"] = [
            [float(v) if np.isfinite(v) else None for v in row] for row in u_raw
        ]
        result["v_component"] = [
            [float(v) if np.isfinite(v) else None for v in row] for row in v_raw
        ]

    return result
