"""
AWS Open Data GRIB2 provider — downloads weather model data directly from
NOAA's S3 buckets using .idx index files and HTTP byte-range requests.

No API keys, no rate limits, no C library dependencies.
Uses our built-in pure-Python GRIB2 decoder.

Supported models:
  - GFS  (0.25° global, from noaa-gfs-bdp-pds)
  - HRRR (3km CONUS, from noaa-hrrr-bdp-pds)

Data sources:
  https://registry.opendata.aws/noaa-gfs-bdp-pds/
  https://registry.opendata.aws/noaa-hrrr-bdp-pds/
"""

import re
import time
import threading
import numpy as np
import requests
from datetime import datetime, timedelta, timezone

from forecast.grib2 import decode_grib2

_session = requests.Session()
_session.headers["User-Agent"] = "ModelForecastViewer/1.0"

# ─── AWS S3 bucket configurations ─────────────────────────

AWS_MODELS = {
    "gfs": {
        "bucket": "https://noaa-gfs-bdp-pds.s3.amazonaws.com",
        "path_pattern": "gfs.{date}/{cycle:02d}/atmos/gfs.t{cycle:02d}z.pgrb2.0p25.f{fhour:03d}",
        "cycles": [0, 6, 12, 18],
        "publish_delay": 5,
    },
    "hrrr": {
        "bucket": "https://noaa-hrrr-bdp-pds.s3.amazonaws.com",
        "path_pattern": "hrrr.{date}/conus/hrrr.t{cycle:02d}z.wrfsfcf{fhour:02d}.grib2",
        "cycles": list(range(24)),
        "publish_delay": 1,
    },
}

# ─── Variable → GRIB2 inventory search patterns ───────────
# These match against the .idx inventory lines to find byte ranges.
# Format is: "msg_num:byte_offset:date:VAR:level:fhour:..."

AWS_VARIABLE_MAP = {
    "temperature_2m": {
        "search": r":TMP:2 m above ground:",
        "convert": lambda k: k * 9 / 5 - 459.67,
        "unit": "°F",
        "wind": False,
    },
    "dewpoint_2m": {
        "search": r":DPT:2 m above ground:",
        "convert": lambda k: k * 9 / 5 - 459.67,
        "unit": "°F",
        "wind": False,
    },
    "wind_speed_10m": {
        "search_u": r":UGRD:10 m above ground:",
        "search_v": r":VGRD:10 m above ground:",
        "convert": lambda ms: ms * 1.94384,
        "unit": "kt",
        "wind": True,
    },
    "wind_gusts_10m": {
        "search": r":GUST:surface:",
        "convert": lambda ms: ms * 1.94384,
        "unit": "kt",
        "wind": False,
    },
    "surface_pressure": {
        "search": r":PRMSL:mean sea level:",
        "convert": lambda pa: pa / 100,
        "unit": "hPa",
        "wind": False,
    },
    "precipitation": {
        "search": r":APCP:surface:",
        "convert": lambda kgm2: kgm2 / 25.4,
        "unit": "in",
        "wind": False,
    },
    "snowfall": {
        "search": r":SNOD:surface:",
        "convert": lambda m: m * 39.3701,
        "unit": "in",
        "wind": False,
    },
    "cape": {
        "search": r":CAPE:surface:",
        "convert": None,
        "unit": "J/kg",
        "wind": False,
    },
    "convective_inhibition": {
        "search": r":CIN:surface:",
        "convert": None,
        "unit": "J/kg",
        "wind": False,
    },
    "cloud_cover": {
        "search": r":TCDC:entire atmosphere:",
        "convert": None,
        "unit": "%",
        "wind": False,
    },
    "visibility": {
        "search": r":VIS:surface:",
        "convert": None,
        "unit": "m",
        "wind": False,
    },
    "relative_humidity_2m": {
        "search": r":RH:2 m above ground:",
        "convert": None,
        "unit": "%",
        "wind": False,
    },
    "shortwave_radiation": {
        "search": r":DSWRF:surface:",
        "convert": None,
        "unit": "W/m²",
        "wind": False,
    },
    "geopotential_height_500hPa": {
        "search": r":HGT:500 mb:",
        "convert": lambda gpm: gpm / 10,
        "unit": "dam",
        "wind": False,
    },
    "temperature_850hPa": {
        "search": r":TMP:850 mb:",
        "convert": lambda k: k - 273.15,
        "unit": "°C",
        "wind": False,
    },
    "wind_speed_250hPa": {
        "search_u": r":UGRD:250 mb:",
        "search_v": r":VGRD:250 mb:",
        "convert": lambda ms: ms * 1.94384,
        "unit": "kt",
        "wind": True,
    },
    "wind_speed_500hPa": {
        "search_u": r":UGRD:500 mb:",
        "search_v": r":VGRD:500 mb:",
        "convert": lambda ms: ms * 1.94384,
        "unit": "kt",
        "wind": True,
    },
    "wind_speed_850hPa": {
        "search_u": r":UGRD:850 mb:",
        "search_v": r":VGRD:850 mb:",
        "convert": lambda ms: ms * 1.94384,
        "unit": "kt",
        "wind": True,
    },
}

AWS_SUPPORTED_VARS = {
    "gfs": set(AWS_VARIABLE_MAP.keys()),
    "hrrr": set(AWS_VARIABLE_MAP.keys()) - {"wind_speed_250hPa"},
}

# ─── Cache ─────────────────────────────────────────────────

_cache = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 1800

_idx_cache = {}
_idx_cache_lock = threading.Lock()
_IDX_CACHE_TTL = 600

_latest_run_cache = {}
_latest_run_lock = threading.Lock()
_LATEST_RUN_TTL = 600


def _cache_get(store, lock, key, ttl):
    with lock:
        entry = store.get(key)
        if entry and (time.time() - entry["ts"]) < ttl:
            return entry["data"]
        if entry:
            del store[key]
    return None


def _cache_set(store, lock, key, data, max_size=200):
    with lock:
        now = time.time()
        if len(store) > max_size:
            expired = [k for k, v in store.items() if (now - v["ts"]) > _CACHE_TTL]
            for k in expired:
                del store[k]
            if len(store) > max_size:
                oldest = sorted(store, key=lambda k: store[k]["ts"])[:50]
                for k in oldest:
                    del store[k]
        store[key] = {"data": data, "ts": now}


# ─── Helpers ───────────────────────────────────────────────

def is_aws_model(model):
    return model.lower() in AWS_MODELS


def get_supported_variables(model):
    return AWS_SUPPORTED_VARS.get(model.lower(), set())


def _find_latest_run(model):
    """Determine latest available model run by probing S3 → (date_str, cycle)."""
    cached = _cache_get(_latest_run_cache, _latest_run_lock, model, _LATEST_RUN_TTL)
    if cached:
        return cached

    config = AWS_MODELS[model]
    now = datetime.now(timezone.utc)
    cycles = sorted(config["cycles"], reverse=True)

    # Try today and yesterday, most recent cycles first
    candidates = []
    for days_ago in range(2):
        day = now - timedelta(days=days_ago)
        date_str = day.strftime("%Y%m%d")
        for c in cycles:
            candidates.append((date_str, c))

    # Probe S3 for the first available .idx file (test f000)
    for date_str, cycle in candidates:
        path = config["path_pattern"].format(date=date_str, cycle=cycle, fhour=0)
        idx_url = f"{config['bucket']}/{path}.idx"
        try:
            resp = _session.head(idx_url, timeout=5)
            if resp.status_code == 200:
                result = (date_str, cycle)
                _cache_set(_latest_run_cache, _latest_run_lock, model, result, max_size=20)
                return result
        except Exception:
            continue

    # Fallback: best guess based on publish delay
    available_time = now - timedelta(hours=config["publish_delay"])
    run_cycle = cycles[-1]
    for c in sorted(cycles):
        if c <= available_time.hour:
            run_cycle = c
    date_str = available_time.strftime("%Y%m%d")
    result = (date_str, run_cycle)
    _cache_set(_latest_run_cache, _latest_run_lock, model, result, max_size=20)
    return result


def _build_url(model, forecast_hour):
    """Build the S3 URL for a GRIB2 file."""
    config = AWS_MODELS[model]
    date_str, cycle = _find_latest_run(model)
    path = config["path_pattern"].format(
        date=date_str, cycle=cycle, fhour=forecast_hour
    )
    return f"{config['bucket']}/{path}", date_str, cycle


def _fetch_idx(url):
    """Fetch and parse a .idx inventory file. Returns list of (msg_num, start_byte, metadata)."""
    idx_url = url + ".idx"
    cached = _cache_get(_idx_cache, _idx_cache_lock, idx_url, _IDX_CACHE_TTL)
    if cached:
        return cached

    resp = _session.get(idx_url, timeout=15)
    if resp.status_code == 404:
        raise FileNotFoundError(f"Index file not found: {idx_url}")
    resp.raise_for_status()

    entries = []
    for line in resp.text.strip().split("\n"):
        parts = line.split(":")
        if len(parts) >= 7:
            msg_num = int(parts[0])
            start_byte = int(parts[1])
            metadata = ":".join(parts[2:])
            entries.append((msg_num, start_byte, metadata))

    _cache_set(_idx_cache, _idx_cache_lock, idx_url, entries, max_size=100)
    return entries


def _find_byte_ranges(idx_entries, search_pattern):
    """Find byte range(s) for a variable in the .idx inventory.
    Returns list of (start, end) byte ranges.
    """
    ranges = []
    for i, (msg_num, start_byte, metadata) in enumerate(idx_entries):
        if re.search(search_pattern, ":" + metadata):
            # End byte is the start of the next message (or end of file)
            if i + 1 < len(idx_entries):
                end_byte = idx_entries[i + 1][1] - 1
            else:
                end_byte = None  # to end of file
            ranges.append((start_byte, end_byte))
    return ranges


def _download_byte_range(url, start, end=None):
    """Download a byte range from a URL using HTTP Range header."""
    if end is not None:
        range_header = f"bytes={start}-{end}"
    else:
        range_header = f"bytes={start}-"
    resp = _session.get(url, headers={"Range": range_header}, timeout=30)
    if resp.status_code not in (200, 206):
        raise ValueError(f"HTTP {resp.status_code} fetching byte range from {url}")
    return resp.content


def _download_variable(url, idx_entries, var_info):
    """Download GRIB2 bytes for a specific variable using byte-range requests."""
    if var_info["wind"]:
        # Wind: need both U and V components
        u_ranges = _find_byte_ranges(idx_entries, var_info["search_u"])
        v_ranges = _find_byte_ranges(idx_entries, var_info["search_v"])
        if not u_ranges or not v_ranges:
            raise FileNotFoundError("Wind U/V components not found in index")
        data = b""
        for start, end in u_ranges + v_ranges:
            data += _download_byte_range(url, start, end)
        return data
    else:
        ranges = _find_byte_ranges(idx_entries, var_info["search"])
        if not ranges:
            raise FileNotFoundError(f"Variable not found in index: {var_info['search']}")
        data = b""
        for start, end in ranges:
            data += _download_byte_range(url, start, end)
        return data


# ─── Public API ────────────────────────────────────────────

def fetch_grid_forecast(model, variable, forecast_hour, bbox=None):
    """Fetch a 2D gridded forecast from AWS S3.
    Returns same dict shape as nomads.fetch_grid_forecast().
    """
    from forecast.nomads import DEFAULT_BBOX
    if bbox is None:
        bbox = DEFAULT_BBOX

    model_key = model.lower()
    if model_key not in AWS_MODELS:
        raise ValueError(f"Model '{model}' not available on AWS")
    if variable not in AWS_VARIABLE_MAP:
        raise ValueError(f"Variable '{variable}' not mapped for AWS")

    cache_key = (
        f"aws:{model_key}:{variable}:{forecast_hour}"
        f":{bbox['lat_min']:.1f}:{bbox['lat_max']:.1f}"
        f":{bbox['lon_min']:.1f}:{bbox['lon_max']:.1f}"
    )
    cached = _cache_get(_cache, _cache_lock, cache_key, _CACHE_TTL)
    if cached:
        return cached

    var_info = AWS_VARIABLE_MAP[variable]

    # Accumulated fields at fhour=0 don't exist — return zeros
    if variable in ("precipitation", "snowfall") and forecast_hour == 0:
        # Fall through to caller — they'll handle zeros
        raise FileNotFoundError("Accumulated fields not available at f000")

    url, run_date, run_cycle = _build_url(model_key, forecast_hour)

    # Fetch index and download just the bytes we need
    idx_entries = _fetch_idx(url)
    grib_bytes = _download_variable(url, idx_entries, var_info)

    # Decode GRIB2
    messages = decode_grib2(grib_bytes)
    if not messages:
        raise ValueError("No decodable GRIB2 messages from AWS data")

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
            raise ValueError("Could not find U/V wind components")
        raw_values = np.sqrt(u_vals**2 + v_vals**2)
        u_raw, v_raw = u_vals, v_vals
    else:
        msg = messages[0]
        lats, lons = msg["lats"], msg["lons"]
        raw_values = msg["values"]
        u_raw = v_raw = None

    # Convert 0-360 longitudes to -180..180
    if np.any(lons > 180):
        lons = lons.copy()
        lons[lons > 180] -= 360
        sort_idx = np.argsort(lons)
        lons = lons[sort_idx]
        raw_values = raw_values[:, sort_idx]
        if u_raw is not None:
            u_raw = u_raw[:, sort_idx]
            v_raw = v_raw[:, sort_idx]

    # Ensure latitudes ascending
    if len(lats) > 1 and lats[0] > lats[-1]:
        lats = lats[::-1]
        raw_values = raw_values[::-1, :]
        if u_raw is not None:
            u_raw = u_raw[::-1, :]
            v_raw = v_raw[::-1, :]

    # Subset to bounding box
    lat_mask = (lats >= bbox["lat_min"]) & (lats <= bbox["lat_max"])
    lon_mask = (lons >= bbox["lon_min"]) & (lons <= bbox["lon_max"])
    if np.any(lat_mask) and np.any(lon_mask):
        lats = lats[lat_mask]
        lons = lons[lon_mask]
        raw_values = raw_values[np.ix_(lat_mask, lon_mask)]
        if u_raw is not None:
            u_raw = u_raw[np.ix_(lat_mask, lon_mask)]
            v_raw = v_raw[np.ix_(lat_mask, lon_mask)]

    # Unit conversion
    convert = var_info["convert"]
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
            [float(v) if np.isfinite(v) else None for v in row]
            for row in u_raw
        ]
        result["v_component"] = [
            [float(v) if np.isfinite(v) else None for v in row]
            for row in v_raw
        ]

    _cache_set(_cache, _cache_lock, cache_key, result)
    return result
