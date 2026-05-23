"""
Filesystem-backed forecast artifact cache.

This stores final JSON payloads that are already ready for /api/forecast. The
web service can read these files cheaply on free hosts instead of computing
multi-field derived products during user requests.
"""

import json
import logging
import os
from pathlib import Path
import threading
import time

log = logging.getLogger(__name__)

_DIR_ENV = "FORECAST_ARTIFACT_DIR"
_DIR_DEFAULT = "forecast_artifacts"
_SCHEMA_VERSION = "v1"
_LOCAL_TTL = 120

_local_cache = {}
_local_cache_lock = threading.Lock()


def _artifact_root():
    configured = os.environ.get(_DIR_ENV, _DIR_DEFAULT).strip()
    return Path(configured or _DIR_DEFAULT)


def is_enabled():
    return _artifact_root().exists()


def supports_model(model):
    return model.lower() in {"gfs", "hrrr", "nam", "rap", "ecmwf_ifs"}


def bbox_token(bbox):
    if not bbox:
        return "default"
    lat_min = min(float(bbox["lat_min"]), float(bbox["lat_max"]))
    lat_max = max(float(bbox["lat_min"]), float(bbox["lat_max"]))
    lon_min = min(float(bbox["lon_min"]), float(bbox["lon_max"]))
    lon_max = max(float(bbox["lon_min"]), float(bbox["lon_max"]))
    return f"{lat_min:.1f}_{lat_max:.1f}_{lon_min:.1f}_{lon_max:.1f}"


def payload_path(model, variable, fhour, bbox):
    return (
        _artifact_root()
        / _SCHEMA_VERSION
        / "latest"
        / model.lower()
        / variable
        / f"f{int(fhour):03d}"
        / f"{bbox_token(bbox)}.json"
    )


def _local_get(key):
    with _local_cache_lock:
        entry = _local_cache.get(key)
        if entry and (time.time() - entry["ts"]) < _LOCAL_TTL:
            return entry["data"]
        if entry:
            del _local_cache[key]
    return None


def _local_set(key, data):
    with _local_cache_lock:
        if len(_local_cache) > 500:
            expired = [
                k for k, v in _local_cache.items()
                if (time.time() - v["ts"]) >= _LOCAL_TTL
            ]
            for expired_key in expired[:200]:
                _local_cache.pop(expired_key, None)
        _local_cache[key] = {"data": data, "ts": time.time()}


def load_latest(model, variable, fhour, bbox):
    if not is_enabled() or not supports_model(model):
        return None

    path = payload_path(model, variable, fhour, bbox)
    cache_key = str(path)
    cached = _local_get(cache_key)
    if cached is not None:
        return cached

    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _local_set(cache_key, data)
        return data
    except Exception as exc:
        log.warning("Forecast artifact read failed for %s/%s f%03d: %s", model, variable, int(fhour), exc)
        return None


def store_latest(model, variable, fhour, bbox, payload):
    if not supports_model(model):
        return False

    path = payload_path(model, variable, fhour, bbox)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        path.write_text(body + "\n", encoding="utf-8")
        _local_set(str(path), payload)
        return True
    except Exception as exc:
        log.warning("Forecast artifact write failed for %s/%s f%03d: %s", model, variable, int(fhour), exc)
        return False
