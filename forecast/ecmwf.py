"""
ECMWF IFS Open Data provider.

Fetches ECMWF IFS (Integrated Forecasting System) open forecast data from the
ECMWF public data store:  https://data.ecmwf.int/forecasts/

Uses the .index sidecar files for byte-range downloads, similar to the AWS
GRIB2 provider.  Only a subset of parameters is supported (those available in
the open data).

Reference:
  https://confluence.ecmwf.int/display/DAC/ECMWF+open+data
"""

import logging
import re
from datetime import datetime, timedelta

import numpy as np
import requests

from forecast.grib2 import decode_grib2
from forecast.grid_utils import crop_and_thin_grid

log = logging.getLogger(__name__)

_session = requests.Session()
_session.headers.update({"User-Agent": "ModelForecast/1.0"})

# ─── Model configuration ──────────────────────────────────

ECMWF_MODELS = {
    "ecmwf_ifs": {
        "name": "ECMWF IFS",
        "base_url": "https://data.ecmwf.int/forecasts",
        "resolution": "0.25°",
        "maxHour": 240,
        "step": 3,
        "cycles": [0, 12],
        "publish_delay_hours": 7,
    },
}

# Map internal variable names → ECMWF shortName / levtype / level search patterns.
# ECMWF open data uses GRIB shortName identifiers.
# Each entry specifies the param name, levtype, and optionally levelist
# used to match entries in the JSON .index sidecar file.
ECMWF_VARIABLE_MAP = {
    "temperature_2m": {
        "param": "2t", "levtype": "sfc",
        "wind": False,
        "convert": lambda v: v - 273.15,  # K → °C then to °F below
        "unit": "°F",
        "convert_final": lambda v: v * 9 / 5 + 32,
    },
    "dewpoint_2m": {
        "param": "2d", "levtype": "sfc",
        "wind": False,
        "convert": lambda v: v - 273.15,
        "unit": "°F",
        "convert_final": lambda v: v * 9 / 5 + 32,
    },
    "wind_speed_10m": {
        "wind": True,
        "param_u": "10u", "param_v": "10v", "levtype": "sfc",
        "convert": lambda v: v * 1.94384,  # m/s → kt
        "unit": "kt",
    },
    "surface_pressure": {
        "param": "msl", "levtype": "sfc",
        "wind": False,
        "convert": lambda v: v / 100.0,  # Pa → hPa
        "unit": "hPa",
    },
    "cape": {
        "param": "mucape", "levtype": "sfc",
        "wind": False,
        "convert": None,
        "unit": "J/kg",
    },
    "precipitation": {
        "param": "tp", "levtype": "sfc",
        "wind": False,
        "convert": lambda v: v * 1000.0 / 25.4,  # m → mm → inches
        "unit": "in",
    },
    "geopotential_height_500hPa": {
        "param": "gh", "levtype": "pl", "levelist": "500",
        "wind": False,
        "convert": lambda v: v / 10.0,  # gpm → dam
        "unit": "dam",
    },
    "temperature_850hPa": {
        "param": "t", "levtype": "pl", "levelist": "850",
        "wind": False,
        "convert": lambda v: v - 273.15,  # K → °C
        "unit": "°C",
    },
    "wind_speed_250hPa": {
        "wind": True,
        "param_u": "u", "param_v": "v", "levtype": "pl", "levelist": "250",
        "convert": lambda v: v * 1.94384,
        "unit": "kt",
    },
}

ECMWF_SUPPORTED_VARS = set(ECMWF_VARIABLE_MAP.keys())

DEFAULT_BBOX = {"lat_min": 20.0, "lat_max": 55.0, "lon_min": -130.0, "lon_max": -60.0}


# ─── Internal helpers ──────────────────────────────────────

def is_ecmwf_model(model):
    """Return True if *model* is served by this provider."""
    return model.lower() in ECMWF_MODELS


def get_supported_variables(model=None):
    return ECMWF_SUPPORTED_VARS


def _find_latest_run(model_key):
    """Determine the most recent ECMWF IFS run likely available.

    ECMWF publishes 00z and 12z runs with ~7 h delay.
    """
    cfg = ECMWF_MODELS[model_key]
    now = datetime.utcnow()
    for hours_back in range(0, 36, 12):
        candidate = now - timedelta(hours=hours_back)
        cycle = max(c for c in cfg["cycles"] if c <= candidate.hour) if candidate.hour >= min(cfg["cycles"]) else cfg["cycles"][-1]
        if cycle > candidate.hour:
            candidate -= timedelta(days=1)
        run_date = candidate.strftime("%Y%m%d")
        # Check publish delay
        run_dt = datetime.strptime(f"{run_date}{cycle:02d}", "%Y%m%d%H")
        if now >= run_dt + timedelta(hours=cfg["publish_delay_hours"]):
            return run_date, cycle
    # Fallback to yesterday 00z
    yesterday = (now - timedelta(days=1)).strftime("%Y%m%d")
    return yesterday, 0


def _build_url(model_key, forecast_hour, run_date=None, run_cycle=None):
    """Build the ECMWF open-data GRIB URL for a given run and forecast hour."""
    cfg = ECMWF_MODELS[model_key]
    if run_date is None or run_cycle is None:
        run_date, run_cycle = _find_latest_run(model_key)
    # URL pattern: {base}/{date}/{cycle}z/ifs/0p25/oper/{date}{cycle}0000-{fhour}h-oper-fc.grib2
    fhour_str = f"{forecast_hour}h"
    url = (
        f"{cfg['base_url']}/{run_date}/{run_cycle:02d}z/ifs/0p25/oper/"
        f"{run_date}{run_cycle:02d}0000-{fhour_str}-oper-fc.grib2"
    )
    return url, run_date, run_cycle


def _fetch_idx(url):
    """Fetch and parse the JSON .index sidecar for an ECMWF GRIB file.

    Each line is a JSON object with keys like:
      param, levtype, levelist, _offset, _length
    """
    idx_url = url.replace(".grib2", ".index")
    resp = _session.get(idx_url, timeout=20)
    if resp.status_code == 404:
        raise FileNotFoundError(f"ECMWF index not found: {idx_url}")
    resp.raise_for_status()
    import json
    entries = []
    for line in resp.text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            entries.append(entry)
        except json.JSONDecodeError:
            continue
    return entries


def _find_byte_ranges(entries, param, levtype, levelist=None):
    """Find byte offset ranges matching param/levtype/levelist in index entries."""
    ranges = []
    for entry in entries:
        if entry.get("param") != param:
            continue
        if entry.get("levtype") != levtype:
            continue
        if levelist is not None and str(entry.get("levelist", "")) != str(levelist):
            continue
        offset = entry["_offset"]
        length = entry["_length"]
        ranges.append((offset, offset + length - 1))
    return ranges


def _download_byte_range(url, start, end=None):
    """Download a byte range via HTTP Range header."""
    range_hdr = f"bytes={start}-{end}" if end is not None else f"bytes={start}-"
    resp = _session.get(url, headers={"Range": range_hdr}, timeout=30)
    if resp.status_code not in (200, 206):
        raise ValueError(f"HTTP {resp.status_code} fetching range from {url}")
    return resp.content


# ─── Public API ────────────────────────────────────────────

def fetch_grid_forecast(model, variable, forecast_hour, bbox=None):
    """Fetch a gridded forecast field from ECMWF open data.

    Returns the same dict shape used by the NOMADS and AWS providers.
    """
    if bbox is None:
        bbox = DEFAULT_BBOX

    model_key = model.lower()
    if model_key not in ECMWF_MODELS:
        raise ValueError(f"Model '{model}' is not available from ECMWF")
    if variable not in ECMWF_VARIABLE_MAP:
        raise ValueError(f"Variable '{variable}' not mapped for ECMWF")

    var_info = ECMWF_VARIABLE_MAP[variable]

    url, run_date, run_cycle = _build_url(model_key, forecast_hour)

    try:
        idx_entries = _fetch_idx(url)
    except FileNotFoundError:
        raise FileNotFoundError(f"ECMWF data not available for {run_date}/{run_cycle:02d}z F{forecast_hour:03d}")

    # Download relevant byte ranges
    if var_info["wind"]:
        levtype = var_info["levtype"]
        levelist = var_info.get("levelist")
        u_ranges = _find_byte_ranges(idx_entries, var_info["param_u"], levtype, levelist)
        v_ranges = _find_byte_ranges(idx_entries, var_info["param_v"], levtype, levelist)
        if not u_ranges or not v_ranges:
            raise FileNotFoundError(f"Wind U/V not found in ECMWF index for F{forecast_hour:03d}")
        data = b""
        for start, end in u_ranges + v_ranges:
            data += _download_byte_range(url, start, end)
    else:
        levelist = var_info.get("levelist")
        ranges = _find_byte_ranges(idx_entries, var_info["param"], var_info["levtype"], levelist)
        if not ranges:
            raise FileNotFoundError(f"Variable {variable} not found in ECMWF index")
        data = b""
        for start, end in ranges:
            data += _download_byte_range(url, start, end)

    messages = decode_grib2(data)
    if not messages:
        raise ValueError("No decodable GRIB2 messages from ECMWF")

    if var_info["wind"]:
        u_vals = v_vals = lats = lons = None
        for msg in messages:
            if lats is None:
                lats, lons = msg["lats"], msg["lons"]
            if msg["category"] == 2 and msg["parameter"] == 2:
                u_vals = msg["values"]
            elif msg["category"] == 2 and msg["parameter"] == 3:
                v_vals = msg["values"]
        if u_vals is None or v_vals is None:
            raise ValueError("Could not find U/V in ECMWF GRIB")
        raw_values = np.sqrt(u_vals ** 2 + v_vals ** 2)
        u_raw, v_raw = u_vals, v_vals
    else:
        msg = messages[0]
        lats, lons, raw_values = msg["lats"], msg["lons"], msg["values"]
        u_raw = v_raw = None

    # Longitude normalisation (0–360 → −180–180)
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
    convert_final = var_info.get("convert_final")
    if convert_final is not None:
        values = convert_final(values)

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
